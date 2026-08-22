"""§6 — per-position PATH CHECK, to estimate n_fired.

READ-ONLY. Per-position path computation ONLY: no portfolio, no admission, no
capacity, no cascade, no arm. For each preserved baseline position that reached
threshold T, walk its own daily bars from entry to its ACTUAL exit and ask
whether a trail would have been touched first.

Uses the RULINGS exactly:
  * §4.3 ATR recomputed as of the current date -- trail = hwm - 3.0 * ATR(t)
  * §4.4 effective stop = max(entry_stop, trail)

And the §4.2 anti-lookahead ordering exactly:
  1. CHECK today's LOW against the trail as it stood at YESTERDAY's close
  2. only if it survived, check activation against today's CLOSE
  3. update HWM from today's CLOSE
  4. ratchet UP only

Fill convention matches the production realistic-stop model: min(stop, open).

STATED LIMIT: this estimates the DIRECT effect on the baseline population. In a
real arm run, an early exit frees capital and the downstream admission sequence
changes, so the arm's own realised population will differ. This is an estimate of
n_fired for the positions that exist in baseline, not a prediction of the arm's
own trade count.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
SWEEP = ROOT / "research" / "exit_mechanics_prereg_2026-08-11"

from analysis.backtest import precompute_ticker                    # noqa: E402
from data.database import get_db_path                              # noqa: E402

DB = ROOT / "database" / "investment_research.db"
assert get_db_path().resolve() == DB.resolve(), "wrong database"
CONFIG = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
K_TRAIL = float(CONFIG["backtest"]["atr_stop_multiplier"])          # 3.0, live
assert K_TRAIL == 3.0, f"expected live 3.0x, got {K_TRAIL}"

THRESHOLDS = [0.30, 0.50, 1.00, 2.00]
POOLS = [("Pool 2 CLEAN", "pool_2_clean"), ("Pool 1", "pool_1")]

_cache: dict[str, pd.DataFrame] = {}


def frame(ticker: str):
    if ticker not in _cache:
        c = precompute_ticker(ticker, CONFIG)
        _cache[ticker] = None if c is None else c["frame"] if isinstance(c, dict) and "frame" in c else c
    return _cache[ticker]


def walk(tk, entry_date, exit_date, entry_price, initial_stop, activation):
    """Replay one position's own path. Returns dict or None if it never fires."""
    f = frame(tk)
    if f is None:
        return None
    seg = f[(f.index >= entry_date) & (f.index <= exit_date)]
    if seg.empty:
        return None

    hwm = float(entry_price)
    trail_active = False
    stop = float(initial_stop)          # effective stop; §4.4 max(entry_stop, trail)
    first = True
    for dt, row in seg.iterrows():
        close = float(row["close"])
        low = float(row["low"]) if pd.notna(row.get("low")) else close
        opn = float(row["open"]) if pd.notna(row.get("open")) else close

        # STEP 1 -- check today's LOW against YESTERDAY's trail level.
        # Skipped on the entry day itself: the position is opened at that
        # day's close, so the day's own low precedes the entry.
        if not first and trail_active and stop > float(initial_stop) + 1e-12:
            if low <= stop:
                fill = min(stop, opn)
                return {"fire_date": dt, "fire_price": fill,
                        "fire_pnl_pct": fill / float(entry_price) - 1.0,
                        "hwm_at_fire": hwm}
        first = False

        # STEP 2 -- activation on today's CLOSE.
        if not trail_active and close / float(entry_price) - 1.0 >= activation:
            trail_active = True
        # STEP 3 -- HWM from today's CLOSE.
        hwm = max(hwm, close)
        # STEP 4 -- ratchet UP only, current-date ATR (§4.3 ruling).
        if trail_active:
            atr_t = row.get("atr")
            if pd.notna(atr_t):
                stop = max(stop, hwm - K_TRAIL * float(atr_t))
    return None


def run(label, tag):
    tr = pd.read_csv(HERE / f"stage0_peaks_{tag}.csv")
    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
    tr["exit_date"] = pd.to_datetime(tr["exit_date"])
    print("=" * 78)
    print(f"{label}  ({len(tr)} baseline positions)")
    print("=" * 78)

    out = []
    for t in THRESHOLDS:
        sub = tr[tr["peak_gain"] >= t].copy()
        fired, recs = 0, []
        for r in sub.itertuples(index=False):
            res = walk(r.ticker, r.entry_date, r.exit_date,
                       r.entry_price, r.initial_stop, t)
            if res is not None:
                fired += 1
                recs.append({
                    "ticker": r.ticker,
                    "entry_date": str(r.entry_date.date()),
                    "actual_exit": str(r.exit_date.date()),
                    "actual_reason": r.exit_reason,
                    "actual_pnl_pct": round(float(r.pnl_pct), 4),
                    "fire_date": str(res["fire_date"].date()),
                    "fire_pnl_pct": round(res["fire_pnl_pct"], 4),
                    "delta_pp": round((res["fire_pnl_pct"] - float(r.pnl_pct)) * 100, 2),
                    "peak_gain": round(float(r.peak_gain), 4),
                    "days_saved": int((r.exit_date - res["fire_date"]).days),
                })
        n = len(sub)
        d = pd.DataFrame(recs)
        better = int((d["delta_pp"] > 0).sum()) if len(d) else 0
        worse = int((d["delta_pp"] < 0).sum()) if len(d) else 0
        rec = {
            "threshold": t, "n_reached": n, "n_fired_est": fired,
            "fire_rate_pct": round(100 * fired / n, 1) if n else None,
            "n_better": better, "n_worse": worse,
            "median_delta_pp": round(float(d["delta_pp"].median()), 2) if len(d) else None,
            "mean_delta_pp": round(float(d["delta_pp"].mean()), 2) if len(d) else None,
            "sum_delta_pp": round(float(d["delta_pp"].sum()), 2) if len(d) else None,
            "median_days_saved": int(d["days_saved"].median()) if len(d) else None,
            "MATERIALITY_n_reached_ge_30": bool(n >= 30),
            "MATERIALITY_n_fired_ge_10": bool(fired >= 10),
        }
        out.append(rec)
        gate = ("TESTED" if (n >= 30 and fired >= 10)
                else f"NOT TESTED (n_reached>=30:{n >= 30}, n_fired>=10:{fired >= 10})")
        print(f"\nT = +{int(t*100)}%")
        print(f"  n_reached        : {n}")
        print(f"  n_fired (est)    : {fired}   ({rec['fire_rate_pct']}% of reached)")
        print(f"  better : worse   : {better} : {worse}")
        print(f"  delta pp  median : {rec['median_delta_pp']}   mean {rec['mean_delta_pp']}"
              f"   sum {rec['sum_delta_pp']}")
        print(f"  median days saved: {rec['median_days_saved']}")
        print(f"  §3.2 GATE        : {gate}")
        if len(d):
            d.sort_values("delta_pp").to_csv(HERE / f"pathcheck_{tag}_T{int(t*100)}.csv", index=False)
    return out


if __name__ == "__main__":
    res = {}
    for label, tag in POOLS:
        res[label] = run(label, tag)
        print()
    with open(HERE / "pathcheck_results.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2)
    print("wrote pathcheck_results.json")
