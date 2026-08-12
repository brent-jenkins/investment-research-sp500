"""Sec 7 verification + Sec 8 sanity gate. Runs before any sweep cell.

  7.1 Degenerate parity  -- trail off, holding 30, stop 2.0x must reproduce raw
                            production simulate_portfolio_compounding_mtm()
                            byte-identical on a canonical sort.
  7.3 Trailing unit tests -- on synthetic series with hand-computed expectations,
                            INCLUDING the anti-lookahead demonstration: a bar
                            whose LOW would trigger a same-day exit under the
                            wrong ordering and does not under the correct one.
  7.5 No TRAILING_STOP may appear when activation is None.
  8   Sanity gate         -- trade SETS vs the preserved live-config figures.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pickle
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis.backtest import simulate_portfolio_compounding_mtm      # noqa: E402
from sweep_engine import simulate_exit_sweep                          # noqa: E402

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    BASE = yaml.safe_load(f)

SRC = ROOT / "research" / "relative_ranking_prereg_2026-08-09"
REFERENCE = {"pool2_clean": (4876, 166830.11, SRC / "trades_pool2_clean_B.csv"),
             "pool1": (4757, None, SRC / "trades_pool1_B.csv")}
KEY = ["ticker", "entry_date", "exit_date"]
CMP = ["ticker", "entry_date", "exit_date", "exit_reason", "position_gbp", "pnl_gbp"]


def canon(df):
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values(KEY).reset_index(drop=True)


# ---------------------------------------------------------------- 7.3
def unit_tests():
    """Synthetic single-ticker frames, hand-computed expectations.

    is_member() is patched to True for the synthetic tickers: without it the
    entry gate rejects every one of them and EVERY test passes vacuously on an
    empty trade list. That is exactly the degenerate case the standing
    "reports clean for an unrelated reason" entry describes, and it was caught
    here only because test (b) indexed [0] and raised. _require() below asserts
    each test actually produced a trade, so a vacuous pass cannot recur.
    """
    print("\n### 7.3 Trailing-stop unit tests (synthetic, hand-computed)")
    fails = []
    from analysis import universe as _u
    _orig_is_member = _u.is_member
    _u.is_member = lambda ticker, d: True

    def _require(res, name):
        if not res["trades"]:
            fails.append(f"{name}: NO TRADES -- test would have passed vacuously")
            return False
        return True

    def frame(closes, lows=None, opens=None, highs=None):
        n = len(closes)
        idx = pd.bdate_range("2015-01-01", periods=n)
        lows = lows or closes
        opens = opens or closes
        return pd.DataFrame({"close": closes, "low": lows, "open": opens,
                             "atr": [1.0] * n, "signal": [True] * n,
                             "momentum": [1.0] * n, "relative_rank": [1.0] * n}, index=idx)

    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["backtest"]["atr_stop_multiplier"] = 2.0
    cfg["backtest"]["starting_capital_gbp"] = 100000
    gspc = pd.Series(100.0, index=pd.bdate_range("2015-01-01", periods=400))

    # (a) never reaches activation -> identical to trail-off
    f = frame([100.0] * 5 + [101.0] * 25)
    a = simulate_exit_sweep([{"ticker": "AAA", "frame": f}], cfg, gspc, 0.05, 0.70,
                            trail_activation=None, max_holding_days_override=30)
    b = simulate_exit_sweep([{"ticker": "AAA", "frame": f}], cfg, gspc, 0.05, 0.70,
                            trail_activation=0.10, max_holding_days_override=30)
    ok_a = _require(a, "(a)-trailoff") and _require(b, "(a)-trailon")
    same = ok_a and ([t["exit_reason"] for t in a["trades"]] == [t["exit_reason"] for t in b["trades"]])
    print(f"  (a) below activation behaves identically to trail-off: {same} "
          f"(n={len(a['trades'])} trades, non-vacuous)")
    if not same:
        fails.append("(a) sub-activation divergence")

    # (b) activates then retraces exactly to the trail -> exits at the trail
    #     entry 100, ATR 1, k=2 -> trail = HWM - 2. Rise to 115 (activates at
    #     +10% = 110), HWM 115, trail 113. Then a low of 113 must trigger.
    closes = [100.0, 105.0, 110.0, 115.0, 114.0]
    lows = [100.0, 105.0, 110.0, 115.0, 113.0]
    f = frame(closes + [114.0] * 25, lows + [114.0] * 25)
    r = simulate_exit_sweep([{"ticker": "BBB", "frame": f}], cfg, gspc, 0.05, 0.70,
                            trail_activation=0.10, max_holding_days_override=30)
    if not _require(r, "(b)"):
        print(f"  (b) SKIPPED -- no trades"); return fails
    t0 = r["trades"][0]
    ok = t0["exit_reason"] == "TRAILING_STOP" and abs(t0["final_stop"] - 113.0) < 1e-9
    print(f"  (b) activates and exits at the trail: reason={t0['exit_reason']} "
          f"stop={t0['final_stop']:.4f} (expect TRAILING_STOP @ 113.0) -> {ok}")
    if not ok:
        fails.append("(b) trail exit level wrong")

    # (c) ratchet never lowers: HWM falls back, stop must hold
    closes = [100.0, 115.0, 111.0, 111.0]
    f = frame(closes + [111.0] * 26)
    r = simulate_exit_sweep([{"ticker": "CCC", "frame": f}], cfg, gspc, 0.05, 0.70,
                            trail_activation=0.10, max_holding_days_override=30)
    if not _require(r, "(c)"):
        print(f"  (c) SKIPPED -- no trades"); return fails
    t0 = r["trades"][0]
    ok = t0["final_stop"] >= 113.0 - 1e-9
    print(f"  (c) ratchet never lowers: final_stop={t0['final_stop']:.4f} (>=113.0) -> {ok}")
    if not ok:
        fails.append("(c) ratchet lowered")

    # (d) THE ANTI-LOOKAHEAD DEMONSTRATION.
    #     Day 2 closes at 115 (activates, HWM 115, trail 113 set at END of day 2).
    #     Day 3 has low 112.5 and close 120. Under the CORRECT ordering the day-3
    #     low is checked against the trail as of day 2's close (113) -> exit.
    #     Under the WRONG ordering, day 3's close (120) would first raise the
    #     trail to 118, and the low 112.5 would still exit -- so to isolate the
    #     bug we need the opposite construction: a day whose low is ABOVE
    #     yesterday's trail but BELOW the trail today's own high/close implies.
    #     Day 3: low 114.0, close 121.0. Correct: 114.0 > 113 -> NO exit.
    #     Wrong ordering: trail would become 121-2=119, and 114 < 119 -> exit.
    closes = [100.0, 115.0, 121.0]
    lows = [100.0, 115.0, 114.0]
    f = frame(closes + [121.0] * 27, lows + [121.0] * 27)
    r = simulate_exit_sweep([{"ticker": "DDD", "frame": f}], cfg, gspc, 0.05, 0.70,
                            trail_activation=0.10, max_holding_days_override=30)
    if not _require(r, "(d)"):
        print(f"  (d) SKIPPED -- no trades"); return fails
    t0 = r["trades"][0]
    ok = t0["exit_reason"] != "TRAILING_STOP"
    print(f"  (d) ANTI-LOOKAHEAD: day-3 low 114 vs yesterday's trail 113 -> "
          f"reason={t0['exit_reason']} (must NOT be TRAILING_STOP) -> {ok}")
    print(f"      (under the wrong ordering the trail would have been 119 and this WOULD exit)")
    if not ok:
        fails.append("(d) same-day lookahead present")

    _u.is_member = _orig_is_member
    return fails


def main() -> None:
    fails = unit_tests()
    ok_all = not fails

    for label, (n_ref, pnl_ref, ref_path) in REFERENCE.items():
        with open(HERE / f"_cache_{label}.pkl", "rb") as fh:
            c = pickle.load(fh)
        caches, gspc = c["caches"], c["gspc_close"]
        cfg = yaml.safe_load(yaml.safe_dump(BASE))

        print(f"\n{'='*84}\n### 7.1 Degenerate parity + Sec 8 sanity gate -- {label}")
        t0 = time.time()
        prod = pd.DataFrame(simulate_portfolio_compounding_mtm(
            caches, cfg, gspc, per_trade_pct=0.05, max_deployed_pct=0.70))
        print(f"  production: {len(prod):,} trades, £{prod['pnl_gbp'].sum():,.2f} "
              f"({time.time()-t0:.0f}s)", flush=True)
        t0 = time.time()
        ours = pd.DataFrame(simulate_exit_sweep(
            caches, cfg, gspc, 0.05, 0.70, trail_activation=None,
            max_holding_days_override=30)["trades"])
        print(f"  sweep_engine (baseline cell): {len(ours):,} trades, "
              f"£{ours['pnl_gbp'].sum():,.2f} ({time.time()-t0:.0f}s)", flush=True)

        a, b = canon(prod), canon(ours)
        if len(a) != len(b):
            print(f"  *** COUNT DIFFERS {len(a)} vs {len(b)}")
            ok_all = False
        else:
            mism = 0
            for col in CMP:
                x, y = a[col], b[col]
                bad = ~((x - y).abs() < 1e-6) if x.dtype.kind == "f" else (x != y)
                if int(bad.sum()):
                    mism += int(bad.sum())
                    print(f"  *** {col}: {int(bad.sum())} mismatches")
            print(f"  7.1 degenerate parity: {'PASS -- byte-identical, 0 mismatches' if not mism else 'FAIL'}")
            ok_all &= (mism == 0)

        # 7.5 -- no TRAILING_STOP when activation is off
        n_trail = int((ours["exit_reason"] == "TRAILING_STOP").sum())
        print(f"  7.5 TRAILING_STOP count with activation=None: {n_trail} (must be 0)")
        ok_all &= (n_trail == 0)

        # Sec 8 -- sanity gate against the preserved reference, as trade SETS
        ref = canon(pd.read_csv(ref_path))
        print(f"  Sec 8 sanity gate vs {ref_path.name}: reference {len(ref):,} trades"
              + (f", £{pnl_ref:,.2f}" if pnl_ref else ""))
        if len(ref) == len(b):
            mism = sum(int((~((ref[c] - b[c]).abs() < 1e-6)).sum()) if ref[c].dtype.kind == "f"
                       else int((ref[c] != b[c]).sum()) for c in CMP)
            print(f"  Sec 8: {'PASS -- trade SETS identical' if not mism else f'FAIL ({mism} mismatches)'}")
            ok_all &= (mism == 0)
        else:
            print(f"  *** Sec 8 FAIL: {len(ref)} vs {len(b)}")
            ok_all = False

    print("\n" + "=" * 84)
    if fails:
        print("UNIT TEST FAILURES:", fails)
    print("OVERALL: PASS -- engine certified, sweep may proceed" if ok_all
          else "OVERALL: FAIL -- do not run any sweep cell")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
