"""Force-close comparison at the five robustness-test cutoffs, both arms, both
pools, on fully-realised P&L.

Force-closing means: every position still open at the cutoff is closed at that
date's close. The engine already does this at the walk's end (END_OF_DATA), and
`equity(D) - starting_capital` is exactly the force-closed total P&L at D --
verified to the penny at the full window and validated against genuine
truncated-cache re-runs at two intermediate cutoffs (step1).

Reports the same headline metrics as the sweep so the comparison is like-for-like
against the published table, plus a decomposition of each arm's total into:
  strategy-closed  -- positions the strategy itself chose to exit by rule
  force-closed     -- positions still open at the cutoff, closed by the device

FORCE-CLOSING IS ARTIFICIAL. A real system would not liquidate on a date. It is a
comparison device to isolate realisation, not a proposed behaviour.
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
SWEEP = HERE.parent / "exit_mechanics_prereg_2026-08-11"
FX = 0.0030
START_CAP = 50000.0
CUTOFFS = ["2022-08-05", "2023-08-04", "2024-08-02", "2025-08-01", "2026-08-07"]
ARMS = {"h30": "baseline (2.0x, 30d)", "s3.0": "winner (3.0x, unlimited)"}


def cagr(s):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100.0


def maxdd(s):
    return float((s / s.cummax() - 1.0).min() * 100.0)


def main() -> None:
    with open(SWEEP / "_reference_series.pkl", "rb") as fh:
        ref = pickle.load(fh)

    results = {}
    for pool in ("pool2_clean", "pool1"):
        data = {}
        for a in ARMS:
            td = pd.read_csv(SWEEP / f"trades_{pool}_{a}.csv")
            pn = pd.read_csv(SWEEP / f"panel_{pool}_{a}.csv")
            td["entry_date"] = pd.to_datetime(td["entry_date"])
            td["exit_date"] = pd.to_datetime(td["exit_date"])
            pn["date"] = pd.to_datetime(pn["date"])
            data[a] = (td, pn.set_index("date").sort_index())

        print(f"\n{'='*104}\n{pool}  -- FULLY-REALISED (force-closed at each cutoff)")
        print(f"{'cutoff':>11} {'arm':>6} {'closed n':>9} {'total P&L':>12} {'strat-closed':>13} "
              f"{'force-closed':>13} {'costCAGR':>9} {'excess':>8} {'maxDD':>8} {'fx pp':>7}")
        rows = []
        for cut in CUTOFFS:
            D = pd.Timestamp(cut)
            per = {}
            for a, (td, pn) in data.items():
                eq = pn["current_balance"].astype(float)
                eq = eq[eq.index <= D]
                if len(eq) < 50:
                    continue
                tr = ref["^SP500TR"].reindex(eq.index, method="ffill").dropna()
                irx = ref["^IRX"].reindex(eq.index, method="ffill")
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25

                total = float(eq.iloc[-1]) - START_CAP          # force-closed total, exact
                closed = td[td["exit_date"] <= D]               # exited by rule before the cutoff
                strat = float(closed["pnl_gbp"].sum())
                forced = total - strat                          # still-open block, crystallised at D
                n_open = int(((td["entry_date"] <= D) & (td["exit_date"] > D)).sum())

                sub = td[td["entry_date"] <= D]
                fx_pp = (float(sub["position_gbp"].sum()) * FX / yrs) / float(eq.mean()) * 100.0
                idle_pp = float((1 - pn.loc[eq.index, "current_deployed_pct"] / 100.0)
                                .clip(lower=0).mean()) * float(irx.mean())
                adj = cagr(eq) - fx_pp + idle_pp
                per[a] = {
                    "n_closed_by_rule": int(len(closed)), "n_force_closed": n_open,
                    "total_pnl": round(total, 2), "strategy_closed_pnl": round(strat, 2),
                    "force_closed_pnl": round(forced, 2),
                    "force_closed_share_pct": round(100.0 * forced / total, 2) if total else None,
                    "cost_adj_cagr": round(adj, 3),
                    "excess": round(adj - cagr(tr), 3),
                    "maxdd": round(maxdd(eq), 2), "fx_pp": round(fx_pp, 3),
                }
                p = per[a]
                print(f"{cut:>11} {a:>6} {p['n_closed_by_rule']:>9,} {p['total_pnl']:>12,.0f} "
                      f"{p['strategy_closed_pnl']:>13,.0f} {p['force_closed_pnl']:>13,.0f} "
                      f"{p['cost_adj_cagr']:>9.3f} {p['excess']:>+8.3f} {p['maxdd']:>8.2f} "
                      f"{p['fx_pp']:>7.3f}")
            if len(per) == 2:
                d = round(per["s3.0"]["excess"] - per["h30"]["excess"], 3)
                pnl_ratio = per["s3.0"]["total_pnl"] / per["h30"]["total_pnl"]
                rows.append({"cutoff": cut, "delta_excess_pp": d,
                             "total_pnl_ratio": round(pnl_ratio, 3), "arms": per})
                print(f"{'':>11} {'-> ':>6} DELTA excess {d:+.3f}pp   "
                      f"winner total P&L {pnl_ratio:.2f}x baseline's")
        results[pool] = rows

        ds = [r["delta_excess_pp"] for r in rows]
        print(f"\n  {pool}: delta across cutoffs min {min(ds):+.3f}  median "
              f"{np.median(ds):+.3f}  max {max(ds):+.3f}  positive {sum(1 for x in ds if x>0)}/{len(ds)}")

    (HERE / "forceclose_comparison.json").write_text(json.dumps(results, indent=2, default=str))
    print("\nWrote forceclose_comparison.json")


if __name__ == "__main__":
    main()
