"""Robustness for any Diagnostic A proxy clearing |Spearman| >= 0.05.

The pre-committed rule: a proxy at |rho| < 0.05 is reported as NULL and NOT
sliced. A proxy that clears gets the stale-peak diagnostic's four checks:

  1. drop each bucket's single largest winner (a difference in means can be one
     trade -- Part 1: the top decile carries 257% of P&L);
  2. exclude 2008;
  3. 2015 onwards only;
  4. confirm it is not a momentum proxy (Spearman against momentum-at-entry,
     plus the effect conditioned within momentum quartiles).

ONE CHECK IS ADDED HERE, and it is stated as an addition rather than smuggled in
with the four: membership_tenure_td is mechanically bounded by how long the
dataset has existed, so it is a candidate CALENDAR-TIME variable rather than a
company-characteristic one. Checks 2 and 3 partially address that; the direct
test -- Spearman(proxy, entry date) and the effect within entry-year cohorts --
does not exist in the stale-peak set because nothing there had this exposure.

Momentum-at-entry is recomputed with the production function the engine itself
calls (analysis.signals.compute_momentum_series), not a local re-derivation.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from data.database import get_engine  # noqa: E402
from analysis.signals import compute_momentum_series  # noqa: E402

POOLS = ("pool2_clean", "pool1")
# proxy -> sign convention: +1 means "higher value = more distress hypothesised"
CLEARING = {"below_peak_pct": +1, "down_up_vol_ratio": +1, "membership_tenure_td": -1}


def sp(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 30:
        return None, int(m.sum())
    return round(float(pd.Series(a[m]).corr(pd.Series(b[m]), method="spearman")), 4), int(m.sum())


def bucket_table(df, col, edges, labels):
    v = pd.to_numeric(df[col], errors="coerce")
    b = pd.cut(v, bins=edges, labels=labels, include_lowest=True)
    rows = {}
    for lab in labels:
        s = df[b == lab]
        if not len(s):
            continue
        p = s["pnl_pct"]
        ex = p.drop(p.idxmax()) if len(p) > 1 else p
        rows[str(lab)] = {
            "n": int(len(s)),
            "win_pct": round(100.0 * float((p > 0).mean()), 2),
            "mean": round(float(p.mean()), 3),
            "median": round(float(p.median()), 3),
            "mean_ex_top1": round(float(ex.mean()), 3),
        }
    return rows


def momentum_at_entry(df):
    """Production compute_momentum_series() per ticker, read at each entry row."""
    eng = get_engine()
    out = {}
    tickers = sorted(df["ticker"].unique())
    with eng.connect() as c:
        for i in range(0, len(tickers), 400):
            chunk = tickers[i:i + 400]
            q = text("SELECT ticker, date, close FROM price_data "
                     f"WHERE ticker IN ({','.join(f':t{j}' for j in range(len(chunk)))}) "
                     "ORDER BY ticker, date")
            raw = pd.read_sql(q, c, params={f"t{j}": t for j, t in enumerate(chunk)})
            raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
            for tk, g in raw.groupby("ticker"):
                g = g.set_index("date").sort_index()
                out[tk] = compute_momentum_series(g["close"])
    vals = []
    for _, r in df.iterrows():
        s = out.get(r["ticker"])
        if s is None or s.empty:
            vals.append(np.nan)
            continue
        e = pd.Timestamp(r["entry_date"]).normalize()
        s2 = s.loc[:e]
        vals.append(float(s2.iloc[-1]) if len(s2) and np.isfinite(s2.iloc[-1]) else np.nan)
    return np.array(vals, dtype=float)


def main():
    res = {}
    for pool in POOLS:
        df = pd.read_csv(HERE / f"proxies_{pool}.csv")
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        df["year"] = df["entry_date"].dt.year
        df["mom_at_entry"] = momentum_at_entry(df) * 100.0
        df.to_csv(HERE / f"proxies_{pool}_with_momentum.csv", index=False)

        pr = {}
        for col, sign in CLEARING.items():
            v = pd.to_numeric(df[col], errors="coerce")
            base_rho, base_n = sp(v, df["pnl_pct"])
            qs = np.array(v.quantile([0, .25, .5, .75, 1.0]).to_numpy(), dtype=float, copy=True)
            qs[0] -= 1e-9
            labels = ["Q1", "Q2", "Q3", "Q4"]
            e = {"spearman_full": base_rho, "n_full": base_n,
                 "distress_direction": "higher = more distress" if sign > 0 else "lower = more distress",
                 "quartile_edges": [round(float(x), 4) for x in qs],
                 "buckets_full": bucket_table(df, col, qs, labels)}

            # check 2 / 3
            for name, sub in (("excl_2008", df[df["year"] != 2008]),
                              ("from_2015", df[df["year"] >= 2015])):
                r, n = sp(pd.to_numeric(sub[col], errors="coerce"), sub["pnl_pct"])
                e[name] = {"spearman": r, "n": n,
                           "buckets": bucket_table(sub, col, qs, labels)}

            # check 4 -- momentum proxy?
            r, n = sp(v, df["mom_at_entry"])
            e["spearman_vs_momentum_at_entry"] = r
            e["n_vs_momentum"] = n
            mq = pd.qcut(df["mom_at_entry"], 4, labels=["M1", "M2", "M3", "M4"], duplicates="drop")
            pq = pd.qcut(v, 4, labels=labels, duplicates="drop")
            grid = {}
            for m in ["M1", "M2", "M3", "M4"]:
                row = {}
                for q in labels:
                    s = df[(mq == m) & (pq == q)]
                    row[q] = {"n": int(len(s)),
                              "mean": round(float(s["pnl_pct"].mean()), 3) if len(s) else None}
                grid[m] = row
            e["momentum_quartile_grid"] = grid

            # ADDED CHECK -- calendar-time confound
            r, n = sp(v, df["entry_date"].map(pd.Timestamp.toordinal))
            e["spearman_vs_entry_date"] = r
            by_year = {}
            for y, s in df.groupby("year"):
                if len(s) < 30:
                    continue
                rr, nn = sp(pd.to_numeric(s[col], errors="coerce"), s["pnl_pct"])
                by_year[int(y)] = {"n": nn, "spearman": rr}
            e["within_year_spearman"] = by_year
            pr[col] = e
        res[pool] = pr

    (HERE / "step2_robustness.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    for pool, pr in res.items():
        print(f"\n{'='*100}\n{pool}\n{'='*100}")
        for col, e in pr.items():
            print(f"\n-- {col}  ({e['distress_direction']})")
            print(f"   full rho {e['spearman_full']}  |  excl-2008 {e['excl_2008']['spearman']} "
                  f"(n={e['excl_2008']['n']})  |  2015+ {e['from_2015']['spearman']} "
                  f"(n={e['from_2015']['n']})")
            print(f"   vs momentum-at-entry {e['spearman_vs_momentum_at_entry']}  |  "
                  f"vs entry date {e['spearman_vs_entry_date']}")
            print(f"   {'bucket':<8}{'n':>5}{'win%':>8}{'mean':>9}{'median':>9}{'ex-top1':>10}")
            for lab, b in e["buckets_full"].items():
                print(f"   {lab:<8}{b['n']:>5}{b['win_pct']:>8.2f}{b['mean']:>9.3f}"
                      f"{b['median']:>9.3f}{b['mean_ex_top1']:>10.3f}")
            print("   momentum-quartile grid (mean pnl%):")
            for m, row in e["momentum_quartile_grid"].items():
                parts = []
                for q in ["Q1", "Q2", "Q3", "Q4"]:
                    mv = row[q]["mean"]
                    parts.append(f"{q}:" + ("      -" if mv is None else f"{mv:+7.2f}")
                                 + f"(n={row[q]['n']:>3})")
                cells = "  ".join(parts)
                print(f"     {m}  {cells}")
            print(f"   within-year rho: {e['within_year_spearman']}")


if __name__ == "__main__":
    main()
