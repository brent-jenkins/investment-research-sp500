"""END-DATE SENSITIVITY -- not pre-registered, added on discovering that the
long-hold arms' advantage is dominated by UNREALISED mark-to-market on positions
still open when the walk ends.

Measured first (step: P&L by exit reason):
    baseline h30 : 7.6% of P&L is END_OF_DATA (still-open) marks
    hunlimited   : 62.1%
    s3.0         : 59.9%
    snone        : 56.3%

An arm that rarely closes a position accumulates unrealised gains that the
equity curve counts as portfolio value. That is not wrong -- a real account
holding NVDA up 722% would genuinely be worth that -- but it makes the headline
highly dependent on WHERE the window happens to stop, and on a handful of names
being near highs on that specific day.

This truncates each arm's own equity curve at successively earlier end dates and
recomputes the comparison. No new simulation: the panel already carries daily
marked-to-market equity, and FX is recomputed from trades entered on or before
each cutoff.

If s3.0's advantage over baseline survives at every cutoff, the result is about
exit mechanics. If it appears only when the window ends on 2026-08-07, it is
about the window.
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
FX = 0.0030
CUTOFFS = ["2022-08-05", "2023-08-04", "2024-08-02", "2025-02-07", "2025-08-01",
           "2026-02-06", "2026-08-07"]
CELLS = ["h30", "h60", "h90", "hunlimited", "s3.0", "snone"]


def cagr(s):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100.0


def main() -> None:
    pool = sys.argv[1] if len(sys.argv) > 1 else "pool2_clean"
    with open(HERE / "_reference_series.pkl", "rb") as fh:
        ref = pickle.load(fh)
    tr_full = ref["^SP500TR"]
    irx_full = ref["^IRX"]

    data = {}
    for c in CELLS:
        p = HERE / f"panel_{pool}_{c}.csv"
        t = HERE / f"trades_{pool}_{c}.csv"
        if not p.exists():
            continue
        pn = pd.read_csv(p)
        pn["date"] = pd.to_datetime(pn["date"])
        td = pd.read_csv(t)
        td["entry_date"] = pd.to_datetime(td["entry_date"])
        data[c] = (pn.set_index("date").sort_index(), td)

    print(f"\nCost-adjusted EXCESS vs ^SP500TR (pp), by window end date -- {pool}")
    print(f"{'end date':>12} " + " ".join(f"{c:>11}" for c in data) + f" {'best':>11}")
    rows = []
    for cut in CUTOFFS:
        cutd = pd.Timestamp(cut)
        line, vals = {}, {}
        for c, (pn, td) in data.items():
            eq = pn["current_balance"].astype(float)
            eq = eq[eq.index <= cutd]
            if len(eq) < 50:
                continue
            tr = tr_full.reindex(eq.index, method="ffill").dropna()
            irx = irx_full.reindex(eq.index, method="ffill")
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            sub = td[td["entry_date"] <= cutd]
            fx_pp = (float(sub["position_gbp"].sum()) * FX / yrs) / float(eq.mean()) * 100.0
            idle_pp = float((1 - pn.loc[eq.index, "current_deployed_pct"] / 100.0)
                            .clip(lower=0).mean()) * float(irx.mean())
            adj = cagr(eq) - fx_pp + idle_pp
            vals[c] = round(adj - cagr(tr), 3)
        if "h30" not in vals:
            continue
        base = vals["h30"]
        line = {c: round(v - base, 3) for c, v in vals.items()}
        best = max(line, key=line.get)
        rows.append({"cutoff": cut, "excess_vs_bench": vals, "delta_vs_baseline": line,
                     "best_cell": best})
        print(f"{cut:>12} " + " ".join(f"{line[c]:>+11.3f}" for c in data if c in line)
              + f" {best:>11}")

    print("\n(values are delta vs the h30 baseline at that same cutoff; h30 is 0 by construction)")
    print("\nAbsolute cost-adjusted excess vs ^SP500TR (pp) -- negative = trails the index:")
    print(f"{'end date':>12} " + " ".join(f"{c:>11}" for c in data))
    for r in rows:
        print(f"{r['cutoff']:>12} " + " ".join(
            f"{r['excess_vs_bench'].get(c, float('nan')):>+11.3f}" for c in data))

    (HERE / f"enddate_sensitivity_{pool}.json").write_text(json.dumps(rows, indent=2))
    print(f"\nWrote enddate_sensitivity_{pool}.json")


if __name__ == "__main__":
    main()
