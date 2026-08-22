"""STAGE 0 (§3) — REALISED EFFECT SIZE, measured BEFORE any cell is run.

READ-ONLY. Operates entirely on the PRESERVED baseline trade sets plus stored
close prices. No arm is simulated, no engine is invoked, no database is written.

Per §3.1: peak_gain = max(close[t]) / entry_price - 1 over t in [entry, exit],
using CLOSE (not high) so the measured population is exactly the population the
arm's own HWM convention could act on.

Per §3.4 this is an UPPER BOUND on the affected set, never a prediction of
n_fired -- once a trail fires, capital returns early and the downstream
admission sequence changes.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
SWEEP = ROOT / "research" / "exit_mechanics_prereg_2026-08-11"

from data.database import get_db_path                              # noqa: E402
from data.ingestion import get_price_df                            # noqa: E402

DB = ROOT / "database" / "investment_research.db"
assert get_db_path().resolve() == DB.resolve(), "wrong database"

THRESHOLDS = [0.30, 0.50, 1.00, 2.00]
POOLS = [("Pool 2 CLEAN", "trades_pool2_clean_s3.0.csv", 348548.74, 565),
         ("Pool 1", "trades_pool1_s3.0.csv", None, None)]

_price_cache: dict[str, pd.Series] = {}


def close_series(ticker: str) -> pd.Series:
    if ticker not in _price_cache:
        df = get_price_df(ticker, days=9000)
        s = df.set_index("date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        _price_cache[ticker] = s.sort_index()
    return _price_cache[ticker]


def analyse(label: str, fname: str, expect_pnl, expect_n):
    tr = pd.read_csv(SWEEP / fname)
    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
    tr["exit_date"] = pd.to_datetime(tr["exit_date"])

    print("=" * 78)
    print(f"{label}  --  {fname}")
    print("=" * 78)
    print(f"positions          : {len(tr)}")
    print(f"total pnl_gbp      : £{tr['pnl_gbp'].sum():,.2f}")
    if expect_n is not None:
        ok_n = len(tr) == expect_n
        ok_p = abs(tr["pnl_gbp"].sum() - expect_pnl) < 0.01
        print(f"SANITY GATE        : n={ok_n} ({expect_n}), pnl={ok_p} (£{expect_pnl:,.2f})")
        if not (ok_n and ok_p):
            raise SystemExit("SANITY GATE FAILED -- stop")

    rows = []
    for r in tr.itertuples(index=False):
        s = close_series(r.ticker)
        seg = s[(s.index >= r.entry_date) & (s.index <= r.exit_date)]
        if seg.empty:
            rows.append({"peak_gain": np.nan, "peak_date": pd.NaT})
            continue
        gains = seg / float(r.entry_price) - 1.0
        rows.append({"peak_gain": float(gains.max()), "peak_date": gains.idxmax(),
                     "n_days_in_seg": len(seg),
                     **{f"first_cross_{int(t*100)}":
                        (gains[gains >= t].index[0] if (gains >= t).any() else pd.NaT)
                        for t in THRESHOLDS}})
    px = pd.DataFrame(rows)
    tr = pd.concat([tr.reset_index(drop=True), px], axis=1)
    assert tr["peak_gain"].notna().all(), "missing price coverage for some position"

    # peak_gain can never be below the realised return, by construction.
    viol = (tr["peak_gain"] < tr["pnl_pct"] - 1e-9).sum()
    print(f"consistency check  : peak_gain >= pnl_pct for all -> {viol == 0} (violations={viol})")

    q = tr["peak_gain"].quantile([.10, .25, .50, .75, .90, .95, .99]).round(4)
    print("\npeak_gain distribution (fraction, e.g. 0.30 = +30%):")
    print("  " + "  ".join(f"p{int(k*100)}={v:+.4f}" for k, v in q.items()))
    print(f"  max={tr['peak_gain'].max():+.4f}   mean={tr['peak_gain'].mean():+.4f}")

    total_pnl = tr["pnl_gbp"].sum()
    out = []
    print("\n" + "-" * 78)
    print("§3.1 PER-THRESHOLD EFFECT SIZE")
    print("-" * 78)
    for t in THRESHOLDS:
        sub = tr[tr["peak_gain"] >= t]
        n = len(sub)
        col = f"first_cross_{int(t*100)}"
        days = (sub[col] - sub["entry_date"]).dt.days if n else pd.Series(dtype=float)
        # giveback: how much of the peak was handed back by the actual exit
        giveback_pp = (sub["peak_gain"] - sub["pnl_pct"]) * 100 if n else pd.Series(dtype=float)
        frac_kept = (sub["pnl_pct"] / sub["peak_gain"]) if n else pd.Series(dtype=float)
        rec = {
            "threshold": t,
            "n_reached": int(n),
            "pct_of_positions": round(100 * n / len(tr), 2),
            "pnl_gbp": round(float(sub["pnl_gbp"].sum()), 2),
            "share_of_total_pnl_pct": round(100 * float(sub["pnl_gbp"].sum()) / total_pnl, 2),
            "median_days_to_first_cross": (float(days.median()) if n else None),
            "median_hold_days": (float(sub["holding_days"].median()) if n else None),
            "median_giveback_pp": (round(float(giveback_pp.median()), 2) if n else None),
            "mean_giveback_pp": (round(float(giveback_pp.mean()), 2) if n else None),
            "median_frac_of_peak_kept": (round(float(frac_kept.median()), 4) if n else None),
            "exit_reasons": (sub["exit_reason"].value_counts().to_dict() if n else {}),
            "MATERIALITY_n_reached_ge_30": bool(n >= 30),
        }
        out.append(rec)
        print(f"\nT = +{int(t*100)}%")
        print(f"  n_reached                 : {n}  ({rec['pct_of_positions']}% of {len(tr)})")
        print(f"  their P&L                 : £{rec['pnl_gbp']:,.2f}  "
              f"({rec['share_of_total_pnl_pct']}% of total)")
        print(f"  median days to 1st cross  : {rec['median_days_to_first_cross']}")
        print(f"  median total holding days : {rec['median_hold_days']}")
        print(f"  median giveback peak->exit: {rec['median_giveback_pp']} pp   "
              f"(mean {rec['mean_giveback_pp']} pp)")
        print(f"  median frac of peak kept  : {rec['median_frac_of_peak_kept']}")
        print(f"  exit reasons              : {rec['exit_reasons']}")
        print(f"  §3.2 n_reached >= 30      : {'PASS' if n >= 30 else 'FAIL -> NOT TESTED'}")

    tag = label.lower().replace(" ", "_")
    tr.to_csv(HERE / f"stage0_peaks_{tag}.csv", index=False)
    return out


if __name__ == "__main__":
    results = {}
    for label, fname, ep, en in POOLS:
        results[label] = analyse(label, fname, ep, en)
        print()
    with open(HERE / "stage0_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    print("wrote stage0_results.json")
