"""Stale-peak diagnostic, step 2 -- distributions first, then the four questions.

Deliberately reports the DISTRIBUTION of both metrics before any threshold is
chosen, and states percentile cut points as a choice rather than presenting them
as a discovered structure.

Usage: python step2_analyse.py <B_relative_rank|A_absolute_momentum>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
ROOT = HERE.parent.parent

SECTOR_SRC = (ROOT / "research" / "full_trade_export_enriched_corrected_2026-08-06"
              / "full_trade_export_enriched_corrected_2007-2026.csv")


def dist(s: pd.Series, name: str, unit: str = "") -> dict:
    q = [0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100]
    vals = {f"p{p}": round(float(s.quantile(p / 100)), 3) for p in q}
    print(f"\n{name} (n={s.notna().sum():,}){unit}")
    print("  " + "  ".join(f"p{p}={vals[f'p{p}']:g}" for p in q))
    print(f"  mean={s.mean():.3f}  sd={s.std():.3f}  skew={s.skew():.3f}")
    return {"n": int(s.notna().sum()), "mean": round(float(s.mean()), 3),
            "sd": round(float(s.std()), 3), "skew": round(float(s.skew()), 3), **vals}


def bucket_table(td: pd.DataFrame, col: str, edges, labels, title: str) -> list:
    """Per-bucket outcome table. Reports the full shape, not only aggregates:
    win rate, mean, median, p10/p25/p75/p90 of pnl_pct, plus the share of total
    gains and total losses -- and a mean recomputed with each bucket's single
    largest winner removed, since Part 1 established the top decile carries 257%
    of P&L and a difference in means here can be one trade."""
    tot_gain = td.loc[td["pnl_gbp"] > 0, "pnl_gbp"].sum()
    tot_loss = td.loc[td["pnl_gbp"] < 0, "pnl_gbp"].sum()
    print(f"\n{title}")
    hdr = (f"  {'bucket':>16} {'n':>6} {'%n':>6} {'win%':>7} {'meanP%':>8} {'medP%':>8} "
           f"{'p10':>7} {'p90':>7} {'PnL £':>11} {'%gains':>7} {'%loss':>7} {'mean-x1':>8}")
    print(hdr)
    rows = []
    td = td.copy()
    td["_b"] = pd.cut(td[col], bins=edges, labels=labels, include_lowest=True, right=False)
    for lab in labels:
        g = td[td["_b"] == lab]
        if g.empty:
            continue
        gains = g.loc[g["pnl_gbp"] > 0, "pnl_gbp"].sum()
        losses = g.loc[g["pnl_gbp"] < 0, "pnl_gbp"].sum()
        # drop the single biggest winner in this bucket
        gx = g.drop(g["pnl_pct"].idxmax()) if len(g) > 1 else g
        r = {
            "bucket": str(lab), "n": int(len(g)),
            "pct_n": round(100.0 * len(g) / len(td), 2),
            "win_pct": round(100.0 * float((g["pnl_gbp"] > 0).mean()), 2),
            "mean_pnl_pct": round(100.0 * float(g["pnl_pct"].mean()), 3),
            "median_pnl_pct": round(100.0 * float(g["pnl_pct"].median()), 3),
            "p10_pnl_pct": round(100.0 * float(g["pnl_pct"].quantile(0.10)), 2),
            "p25_pnl_pct": round(100.0 * float(g["pnl_pct"].quantile(0.25)), 2),
            "p75_pnl_pct": round(100.0 * float(g["pnl_pct"].quantile(0.75)), 2),
            "p90_pnl_pct": round(100.0 * float(g["pnl_pct"].quantile(0.90)), 2),
            "total_pnl_gbp": round(float(g["pnl_gbp"].sum()), 2),
            "share_of_gains_pct": round(100.0 * gains / tot_gain, 2) if tot_gain else 0.0,
            "share_of_losses_pct": round(100.0 * losses / tot_loss, 2) if tot_loss else 0.0,
            "mean_pnl_pct_ex_top1": round(100.0 * float(gx["pnl_pct"].mean()), 3),
            "mean_hold_days": round(float(g["holding_days"].mean()), 1),
            "mean_rank_pct_within_day": round(float(g["rank_pct_within_day"].mean()), 2),
        }
        rows.append(r)
        print(f"  {r['bucket']:>16} {r['n']:>6} {r['pct_n']:>6.1f} {r['win_pct']:>7.2f} "
              f"{r['mean_pnl_pct']:>8.3f} {r['median_pnl_pct']:>8.3f} {r['p10_pnl_pct']:>7.1f} "
              f"{r['p90_pnl_pct']:>7.1f} {r['total_pnl_gbp']:>11,.0f} "
              f"{r['share_of_gains_pct']:>7.1f} {r['share_of_losses_pct']:>7.1f} "
              f"{r['mean_pnl_pct_ex_top1']:>8.3f}")
    return rows


def main() -> None:
    label = sys.argv[1]
    td = pd.read_csv(HERE / f"metrics_{label}.csv")
    td["entry_date"] = pd.to_datetime(td["entry_date"])
    td = td[td["below_peak_pct"].notna()].reset_index(drop=True)
    out = {"label": label, "n_trades": int(len(td))}

    print("=" * 100)
    print(f"STALE-PEAK DIAGNOSTIC -- {label}  ({len(td):,} admitted positions)")
    print(f"total P&L £{td['pnl_gbp'].sum():,.2f}  win rate {100*(td['pnl_gbp']>0).mean():.2f}%")
    print("=" * 100)

    # ---------- distributions FIRST, before any threshold ----------
    print("\n### DISTRIBUTIONS (reported before any cut point is chosen)")
    out["dist_below_peak"] = dist(td["below_peak_pct"], "below_peak_pct", "  [0 = entered at window peak]")
    out["dist_days_since_peak"] = dist(td["days_since_peak"], "days_since_peak", "  [trading days]")
    out["dist_momentum"] = dist(td["momentum_at_entry_pct"], "momentum_at_entry_pct")
    out["dist_rank_within_day"] = dist(td["rank_pct_within_day"], "rank_pct_within_day",
                                       "  [0 = best candidate that day]")

    # shape check: is there a natural break, or is it continuous?
    h = np.histogram(td["below_peak_pct"], bins=np.arange(0, 105, 5))[0]
    print("\n  below_peak_pct histogram, 5pp bins 0..100:")
    print("   " + " ".join(f"{c:>5}" for c in h))
    out["below_peak_hist_5pp_bins"] = [int(x) for x in h]

    # ---------- Q1: how common ----------
    print("\n\n### Q1 -- fraction of admitted positions materially below their own trailing peak")
    cuts = [0.001, 5, 10, 15, 20, 25, 30, 40, 50]
    q1 = {}
    for c in cuts:
        n = int((td["below_peak_pct"] >= c).sum())
        q1[f">={c}%"] = {"n": n, "pct": round(100.0 * n / len(td), 2)}
        print(f"  below peak by >= {c:>5}% : {n:>5,}  ({100.0*n/len(td):>5.2f}%)")
    out["q1_cut_points"] = q1
    pcts = {f"p{p}": round(float(td["below_peak_pct"].quantile(p / 100)), 2)
            for p in (50, 60, 70, 75, 80, 90, 95)}
    print(f"  percentile cut points (a CHOICE, not a discovered break): {pcts}")
    out["q1_percentiles"] = pcts

    # ---------- Q2: outcomes by bucket ----------
    print("\n\n### Q2 -- do those positions perform worse?")
    edges = [0, 5, 10, 20, 30, 101]
    labels = ["0-5%", "5-10%", "10-20%", "20-30%", ">=30%"]
    out["q2_below_peak_buckets"] = bucket_table(td, "below_peak_pct", edges, labels,
                                                "By below_peak_pct at entry:")
    dedges = [0, 5, 21, 63, 126, 253]
    dlabels = ["0-4d", "5-20d", "21-62d", "63-125d", ">=126d"]
    out["q2_days_since_peak_buckets"] = bucket_table(td, "days_since_peak", dedges, dlabels,
                                                     "By days_since_peak at entry:")

    # correlations -- the Part 2 guardrail
    for c in ("below_peak_pct", "days_since_peak"):
        sp = stats.spearmanr(td[c], td["pnl_pct"])
        pe = stats.pearsonr(td[c], td["pnl_pct"])
        print(f"\n  Spearman({c}, pnl_pct) = {sp.statistic:+.4f}  (p={sp.pvalue:.3f})")
        print(f"  Pearson ({c}, pnl_pct) = {pe.statistic:+.4f}  (p={pe.pvalue:.3f})")
        out[f"corr_{c}"] = {"spearman": round(float(sp.statistic), 4),
                           "spearman_p": round(float(sp.pvalue), 4),
                           "pearson": round(float(pe.statistic), 4),
                           "pearson_p": round(float(pe.pvalue), 4)}

    # ---------- Q3: share of total losses and gains ----------
    print("\n\n### Q3 -- share of TOTAL losses and TOTAL gains (the actual question)")
    tot_gain = td.loc[td["pnl_gbp"] > 0, "pnl_gbp"].sum()
    tot_loss = td.loc[td["pnl_gbp"] < 0, "pnl_gbp"].sum()
    print(f"  gross gains £{tot_gain:,.2f}   gross losses £{tot_loss:,.2f}   "
          f"net £{tot_gain+tot_loss:,.2f}")
    q3 = {}
    for c in (10, 20, 30):
        g = td[td["below_peak_pct"] >= c]
        gg = g.loc[g["pnl_gbp"] > 0, "pnl_gbp"].sum()
        gl = g.loc[g["pnl_gbp"] < 0, "pnl_gbp"].sum()
        q3[f">={c}%"] = {
            "n": int(len(g)), "pct_of_trades": round(100.0 * len(g) / len(td), 2),
            "share_of_gains_pct": round(100.0 * gg / tot_gain, 2),
            "share_of_losses_pct": round(100.0 * gl / tot_loss, 2),
            "net_pnl_gbp": round(float(g["pnl_gbp"].sum()), 2),
            "net_pnl_share_pct": round(100.0 * float(g["pnl_gbp"].sum()) / td["pnl_gbp"].sum(), 2),
        }
        r = q3[f">={c}%"]
        print(f"  below peak >= {c}%: {r['n']:,} trades ({r['pct_of_trades']}% of all) -> "
              f"{r['share_of_gains_pct']}% of gains, {r['share_of_losses_pct']}% of losses, "
              f"net £{r['net_pnl_gbp']:,.0f} ({r['net_pnl_share_pct']}% of total P&L)")
    out["q3"] = q3

    # ---------- Q4: era and sector ----------
    print("\n\n### Q4 -- does the pattern concentrate by era or sector?")
    td["year"] = td["entry_date"].dt.year
    yr = td.groupby("year").agg(
        n=("pnl_gbp", "size"), mean_below_peak=("below_peak_pct", "mean"),
        pct_ge20=("below_peak_pct", lambda s: 100.0 * (s >= 20).mean()),
        win_pct=("pnl_gbp", lambda s: 100.0 * (s > 0).mean()),
        pnl=("pnl_gbp", "sum")).round(2)
    print("\n  by entry year:")
    print(yr.to_string())
    out["q4_by_year"] = yr.reset_index().to_dict("records")

    sec = pd.read_csv(SECTOR_SRC, usecols=["ticker", "sector"]).dropna().drop_duplicates("ticker")
    smap = dict(zip(sec["ticker"], sec["sector"]))
    td["sector"] = td["ticker"].map(smap)
    cov = 100.0 * td["sector"].notna().mean()
    print(f"\n  sector coverage from preserved enriched export: {cov:.1f}% of trades")
    sc = td[td["sector"].notna()].groupby("sector").agg(
        n=("pnl_gbp", "size"), mean_below_peak=("below_peak_pct", "mean"),
        pct_ge20=("below_peak_pct", lambda s: 100.0 * (s >= 20).mean()),
        win_pct=("pnl_gbp", lambda s: 100.0 * (s > 0).mean()),
        pnl=("pnl_gbp", "sum")).round(2).sort_values("n", ascending=False)
    print(sc.to_string())
    out["q4_sector_coverage_pct"] = round(cov, 1)
    out["q4_by_sector"] = sc.reset_index().to_dict("records")

    # ---------- the capacity wall (smoothness §6 / Part 4) ----------
    print("\n\n### Would a filter on this pattern reach admissions, or hit the capacity wall?")
    for c in (10, 20, 30):
        g = td[td["below_peak_pct"] >= c]
        print(f"  below peak >= {c}%: mean rank-within-day "
              f"{g['rank_pct_within_day'].mean():.2f}%  median "
              f"{g['rank_pct_within_day'].median():.2f}%  "
              f"(all trades: mean {td['rank_pct_within_day'].mean():.2f}%)")
    out["q_capacity_wall"] = {
        f">={c}%": {
            "mean_rank_pct": round(float(td[td['below_peak_pct'] >= c]['rank_pct_within_day'].mean()), 2),
            "median_rank_pct": round(float(td[td['below_peak_pct'] >= c]['rank_pct_within_day'].median()), 2),
        } for c in (10, 20, 30)}
    out["all_trades_mean_rank_pct"] = round(float(td["rank_pct_within_day"].mean()), 2)

    (HERE / f"analysis_{label}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote analysis_{label}.json")


if __name__ == "__main__":
    main()
