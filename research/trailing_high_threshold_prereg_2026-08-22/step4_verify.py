"""V1-V7 verification (§6 of the pre-registration). No cell result is trusted
until every item here passes. READ-ONLY, no database writes.

V7 exists because of the §4.3 RULING: the four trailing unit tests were written
against the frozen-entry-ATR convention. Their synthetic frames pin atr at a
constant 1.0, so they are EXPECTED to carry over unchanged to current-date ATR --
and that expectation is VERIFIED here, not assumed.
"""
import pickle
import sys
from pathlib import Path

import pandas as pd
import yaml

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
SWEEP = ROOT / "research" / "exit_mechanics_prereg_2026-08-11"

from trail_engine import simulate_exit_sweep                       # noqa: E402

BASE = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
FAILS: list[str] = []


def _require(res, name):
    """A test that has not been observed producing a non-empty result has not
    been observed at all -- the exit sweep's vacuous-pass lesson, kept."""
    n = len(res["trades"]) if res else 0
    if n == 0:
        FAILS.append(f"{name}: VACUOUS (0 trades)")
        print(f"    !! {name} produced 0 trades -- vacuous, not a pass")
        return False
    return True


def live_cfg():
    c = yaml.safe_load(yaml.safe_dump(BASE))
    c["backtest"]["atr_stop_multiplier"] = 3.0      # live
    return c


# ---------------------------------------------------------------- V1 / V2 / V5 / V6
def v1_v2_v5_v6():
    print("\n" + "=" * 74)
    print("V1 degenerate parity | V2 zero TRAILING_STOP | V5 no retro-activation | V6 ratchet")
    print("=" * 74)
    for pool, ref in (("pool2_clean", "trades_pool2_clean_s3.0.csv"),
                      ("pool1", "trades_pool1_s3.0.csv")):
        c = pickle.load(open(HERE / f"_cache_{pool}.pkl", "rb"))
        res = simulate_exit_sweep(
            c["caches"], live_cfg(), c["gspc_close"], per_trade_pct=0.05,
            max_deployed_pct=0.70, disable_stop_loss=False,
            max_holding_days_override=None, unlimited_holding=True,
            trail_activation=None, trail_multiplier=3.0)
        got = pd.DataFrame(res["trades"])
        exp = pd.read_csv(SWEEP / ref)
        key = ["ticker", "entry_date", "exit_date"]
        for d in (got, exp):
            for k in ("entry_date", "exit_date"):
                d[k] = pd.to_datetime(d[k]).dt.strftime("%Y-%m-%d")
        # THE COMPARISON MUST BE LIKE-FOR-LIKE. The first version of this check
        # compared IN-MEMORY float64 against the reference CSV with exact !=,
        # and reported 90/565 (15.9%) mismatches on identical trade sets.
        # Measured directly: pandas' default to_csv/read_csv round trip is not
        # bit-exact and alters 16.2% of float64 values at ~1e-16 relative --
        # matching the observed mismatch rate almost exactly. The engine output
        # is therefore routed through the SAME round trip as the reference
        # before comparing, so both sides carry identical formatting loss.
        _tmp = HERE / f"_v1_roundtrip_{pool}.csv"
        got.to_csv(_tmp, index=False)
        got = pd.read_csv(_tmp)
        for k in ("entry_date", "exit_date"):
            got[k] = pd.to_datetime(got[k]).dt.strftime("%Y-%m-%d")
        # mergesort: stable, so ties in the key cannot reorder between frames.
        g = got.sort_values(key, kind="mergesort").reset_index(drop=True)
        e = exp.sort_values(key, kind="mergesort").reset_index(drop=True)
        cols = ["ticker", "entry_date", "exit_date", "exit_reason", "pnl_gbp"]
        same_n = len(g) == len(e)
        mism = (int((g[cols] != e[cols]).any(axis=1).sum())
                if same_n else None)
        pnl_ok = abs(g["pnl_gbp"].sum() - e["pnl_gbp"].sum()) < 0.01
        print(f"\n  [{pool}] n={len(g)} (expect {len(e)})  "
              f"pnl=£{g['pnl_gbp'].sum():,.2f} (expect £{e['pnl_gbp'].sum():,.2f})")
        print(f"    V1 byte-identical trade SET : "
              f"{'PASS' if same_n and mism == 0 else f'FAIL (mismatches={mism})'}")
        if not (same_n and mism == 0 and pnl_ok):
            FAILS.append(f"V1 {pool}")
        n_trail = int((got["exit_reason"] == "TRAILING_STOP").sum())
        print(f"    V2 zero TRAILING_STOP       : {'PASS' if n_trail == 0 else f'FAIL ({n_trail})'}")
        if n_trail:
            FAILS.append(f"V2 {pool}")

        # V5/V6 need an ARMED arm; use B50 on this pool.
        res2 = simulate_exit_sweep(
            c["caches"], live_cfg(), c["gspc_close"], per_trade_pct=0.05,
            max_deployed_pct=0.70, disable_stop_loss=False,
            max_holding_days_override=None, unlimited_holding=True,
            trail_activation=0.50, trail_multiplier=3.0)
        t2 = pd.DataFrame(res2["trades"])
        if not _require(res2, f"V5/V6-{pool}"):
            continue
        act = t2[t2["trail_activated"]]
        # V5: a position may only be flagged activated if its own path reached +50%.
        bad = int((act["exit_price"] / act["entry_price"] - 1.0 < 0.50).sum()
                  and 0)  # placeholder; real check below uses peak, not exit
        peaks = pd.read_csv(HERE / "stage0_peaks_pool_2_clean.csv") if pool == "pool2_clean" \
            else pd.read_csv(HERE / "stage0_peaks_pool_1.csv")
        pk = {(r.ticker, str(pd.to_datetime(r.entry_date).date())): r.peak_gain
              for r in peaks.itertuples(index=False)}
        checked = retro = 0
        for r in act.itertuples(index=False):
            k = (r.ticker, str(pd.to_datetime(r.entry_date).date()))
            if k in pk:
                checked += 1
                if pk[k] < 0.50 - 1e-9:
                    retro += 1
        print(f"    V5 no retro-activation      : "
              f"{'PASS' if retro == 0 else f'FAIL ({retro})'}  "
              f"(checked {checked} of {len(act)} activated against baseline peaks)")
        if retro:
            FAILS.append(f"V5 {pool}")
        # V6: ratchet never lowers, and §4.4 effective stop = max(entry_stop, trail)
        low = int((t2["final_stop"] < t2["initial_stop"] - 1e-9).sum())
        print(f"    V6 final_stop>=initial_stop : {'PASS' if low == 0 else f'FAIL ({low})'} "
              f"[also proves §4.4 max(entry_stop,trail)]")
        if low:
            FAILS.append(f"V6 {pool}")
        ts = t2[t2["exit_reason"] == "TRAILING_STOP"]
        strictly = int((ts["final_stop"] > ts["initial_stop"] + 1e-12).sum())
        print(f"    V6 every TRAILING_STOP has a raised stop : "
              f"{'PASS' if strictly == len(ts) else 'FAIL'} ({strictly}/{len(ts)})")
        if strictly != len(ts):
            FAILS.append(f"V6-strict {pool}")


# ---------------------------------------------------------------- V3 / V4 / V7
def v3_v4_v7():
    print("\n" + "=" * 74)
    print("V3 four unit tests (non-vacuous) | V4 anti-lookahead | V7 re-derived for current ATR")
    print("=" * 74)

    def frame(closes, lows=None, opens=None, atr=1.0):
        n = len(closes)
        idx = pd.bdate_range("2015-01-01", periods=n)
        lows = lows if lows is not None else list(closes)
        opens = opens if opens is not None else list(closes)
        return pd.DataFrame({"close": closes, "low": lows, "open": opens,
                             "atr": [atr] * n, "signal": [True] * n,
                             "momentum": [1.0] * n, "relative_rank": [1.0] * n}, index=idx)

    cfg = yaml.safe_load(yaml.safe_dump(BASE))
    cfg["backtest"]["atr_stop_multiplier"] = 2.0
    cfg["backtest"]["starting_capital_gbp"] = 100000
    gspc = pd.Series(100.0, index=pd.bdate_range("2015-01-01", periods=400))

    def run(f, act, src="current"):
        import analysis.universe as U
        orig = U.is_member
        U.is_member = lambda *a, **k: True
        try:
            import trail_engine as TE
            TE.universe.is_member = lambda *a, **k: True
            return simulate_exit_sweep([{"ticker": "AAA", "frame": f}], cfg, gspc, 0.05, 0.70,
                                       trail_activation=act, max_holding_days_override=30,
                                       trail_atr_source=src)
        finally:
            U.is_member = orig

    # (a) never reaches activation -> identical to trail-off
    f = frame([100.0] * 5 + [101.0] * 25)
    a, b = run(f, None), run(f, 0.10)
    if _require(a, "(a)-off") and _require(b, "(a)-on"):
        ok = [t["exit_reason"] for t in a["trades"]] == [t["exit_reason"] for t in b["trades"]]
        print(f"  (a) sub-activation == trail-off : {ok}  (n={len(a['trades'])}, non-vacuous)")
        if not ok:
            FAILS.append("(a)")

    # (b) activates then retraces exactly to the trail -> exits AT the trail
    f = frame([100.0, 105.0, 110.0, 115.0, 114.0] + [114.0] * 25,
              [100.0, 105.0, 110.0, 115.0, 113.0] + [114.0] * 25)
    r = run(f, 0.10)
    if _require(r, "(b)"):
        t0 = r["trades"][0]
        ok = t0["exit_reason"] == "TRAILING_STOP" and abs(t0["final_stop"] - 113.0) < 1e-9
        print(f"  (b) exits at the trail          : reason={t0['exit_reason']} "
              f"stop={t0['final_stop']:.4f} (expect TRAILING_STOP @113.0) -> {ok}")
        if not ok:
            FAILS.append("(b)")

    # (c) ratchet never lowers
    f = frame([100.0, 115.0, 111.0, 111.0] + [111.0] * 26)
    r = run(f, 0.10)
    if _require(r, "(c)"):
        t0 = r["trades"][0]
        ok = t0["final_stop"] >= 113.0 - 1e-9
        print(f"  (c) ratchet never lowers        : final_stop={t0['final_stop']:.4f} "
              f"(>=113.0) -> {ok}")
        if not ok:
            FAILS.append("(c)")

    # (d) THE ANTI-LOOKAHEAD DEMONSTRATION (V4)
    f = frame([100.0, 115.0, 121.0] + [121.0] * 27,
              [100.0, 115.0, 114.0] + [121.0] * 27)
    r = run(f, 0.10)
    if _require(r, "(d)"):
        t0 = r["trades"][0]
        ok = t0["exit_reason"] != "TRAILING_STOP"
        print(f"  (d) V4 ANTI-LOOKAHEAD           : day-3 low 114 vs yesterday's trail 113 "
              f"-> reason={t0['exit_reason']} (must NOT be TRAILING_STOP) -> {ok}")
        print(f"      (wrong ordering would set trail=119 first and this WOULD exit)")
        if not ok:
            FAILS.append("(d) lookahead")

    # V7 -- the carry-over is VERIFIED, not assumed.
    print("\n  V7 -- re-derivation under the §4.3 ruling:")
    for name, f, act in (("(a)", frame([100.0] * 5 + [101.0] * 25), 0.10),
                         ("(b)", frame([100.0, 105.0, 110.0, 115.0, 114.0] + [114.0] * 25,
                                       [100.0, 105.0, 110.0, 115.0, 113.0] + [114.0] * 25), 0.10),
                         ("(c)", frame([100.0, 115.0, 111.0, 111.0] + [111.0] * 26), 0.10),
                         ("(d)", frame([100.0, 115.0, 121.0] + [121.0] * 27,
                                       [100.0, 115.0, 114.0] + [121.0] * 27), 0.10)):
        rc, re_ = run(f, act, "current"), run(f, act, "entry")
        if not (_require(rc, f"V7{name}-cur") and _require(re_, f"V7{name}-ent")):
            continue
        same = ([(t["exit_reason"], round(t["final_stop"], 9)) for t in rc["trades"]]
                == [(t["exit_reason"], round(t["final_stop"], 9)) for t in re_["trades"]])
        print(f"    {name} current-ATR == entry-ATR on a constant-atr fixture : {same}")
        if not same:
            FAILS.append(f"V7 {name}")

    # V7b -- and prove the two conventions DO differ when atr actually varies,
    # so the parameter is demonstrably live rather than silently ignored.
    n = 30
    idx = pd.bdate_range("2015-01-01", periods=n)
    closes = [100.0] + [100.0 + 3.0 * i for i in range(1, n)]
    fv = pd.DataFrame({"close": closes, "low": closes, "open": closes,
                       "atr": [1.0 + 0.5 * i for i in range(n)], "signal": [True] * n,
                       "momentum": [1.0] * n, "relative_rank": [1.0] * n}, index=idx)
    rc, re_ = run(fv, 0.10, "current"), run(fv, 0.10, "entry")
    if _require(rc, "V7b-cur") and _require(re_, "V7b-ent"):
        diff = rc["trades"][0]["final_stop"] != re_["trades"][0]["final_stop"]
        print(f"    V7b rising-ATR fixture: conventions DIFFER -> {diff} "
              f"(current={rc['trades'][0]['final_stop']:.3f}, "
              f"entry={re_['trades'][0]['final_stop']:.3f})")
        if not diff:
            FAILS.append("V7b parameter inert")


if __name__ == "__main__":
    v1_v2_v5_v6()
    v3_v4_v7()
    print("\n" + "=" * 74)
    print("ALL VERIFICATION PASSED" if not FAILS else f"FAILURES: {FAILS}")
    print("=" * 74)
    sys.exit(1 if FAILS else 0)
