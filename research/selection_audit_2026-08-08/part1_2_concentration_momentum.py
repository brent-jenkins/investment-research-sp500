"""Parts 1 & 2 of the descriptive selection audit (2026-08-08).

Read-only. Uses the existing preserved trade-level export
research/full_trade_export_enriched_corrected_2026-08-06/full_trade_export_enriched_corrected_2007-2026.csv
(3,437 trades) -- generated via run_full_validation(..., live_universe=True,
mark_to_market=True), the same engine/universe methodology
main.py's Backtest page uses and the one confirmed to match live execution.
2.0x ATR stop (current production default), realistic intraday-touch
STOP_LOSS fill (permanent default), continuous 2007-2026 walk (single-
strategy realistic-track-record convention, not a variant comparison --
see CLAUDE.md's "Two backtest conventions").

No re-run. No new backtest. Momentum-at-entry, sector, exit_reason,
holding_days, pnl_gbp/pnl_pct all already present on this export.
"""
import json

import numpy as np
import pandas as pd

SRC = r"c:\GitHub\investment-app\research\full_trade_export_enriched_corrected_2026-08-06\full_trade_export_enriched_corrected_2007-2026.csv"
OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"

df = pd.read_csv(SRC, parse_dates=["entry_date", "exit_date"])
n = len(df)
total_pnl = df["pnl_gbp"].sum()
print(f"Loaded {n} trades. Total P&L = £{total_pnl:,.2f}")

results = {"n_trades": n, "total_pnl_gbp": round(float(total_pnl), 2)}

# ---------------------------------------------------------------------------
# PART 1: Concentration and luck
# ---------------------------------------------------------------------------
df_sorted = df.sort_values("pnl_gbp", ascending=False).reset_index(drop=True)

def top_n_share(n_top):
    return float(df_sorted["pnl_gbp"].head(n_top).sum() / total_pnl)

def top_pct_share(pct):
    k = max(1, int(round(n * pct)))
    return k, float(df_sorted["pnl_gbp"].head(k).sum() / total_pnl)

top10_share = top_n_share(10)
top1pct_n, top1pct_share = top_pct_share(0.01)
top_decile_n, top_decile_share = top_pct_share(0.10)

# Winners only, for "share of gross profit" framing (total P&L nets losers,
# which can make a share look larger/smaller than the winners' own story)
winners = df[df["pnl_gbp"] > 0]
losers = df[df["pnl_gbp"] <= 0]
gross_profit = winners["pnl_gbp"].sum()
gross_loss = losers["pnl_gbp"].sum()  # negative
top10_of_gross = float(df_sorted["pnl_gbp"].clip(lower=0).head(10).sum() / gross_profit)

pnl = df["pnl_gbp"]
pnl_pct = df["pnl_pct"]

def skewness(x):
    x = np.asarray(x, dtype=float)
    mu = x.mean()
    sigma = x.std(ddof=0)
    if sigma == 0:
        return 0.0
    return float(np.mean(((x - mu) / sigma) ** 3))

dist = {
    "pnl_gbp": {
        "mean": float(pnl.mean()), "median": float(pnl.median()),
        "std": float(pnl.std()), "skew": skewness(pnl),
        "min": float(pnl.min()), "max": float(pnl.max()),
        "p5": float(pnl.quantile(0.05)), "p25": float(pnl.quantile(0.25)),
        "p75": float(pnl.quantile(0.75)), "p95": float(pnl.quantile(0.95)),
    },
    "pnl_pct": {
        "mean": float(pnl_pct.mean()), "median": float(pnl_pct.median()),
        "std": float(pnl_pct.std()), "skew": skewness(pnl_pct),
        "min": float(pnl_pct.min()), "max": float(pnl_pct.max()),
    },
    "win_rate": float((df["pnl_gbp"] > 0).mean()),
    "n_winners": int(len(winners)), "n_losers": int(len(losers)),
    "gross_profit_gbp": float(gross_profit), "gross_loss_gbp": float(gross_loss),
    "profit_factor": float(gross_profit / abs(gross_loss)) if gross_loss != 0 else None,
}

# Full decile breakdown of P&L share (not just top decile) -- shows the whole
# concentration curve, not just the headline numbers.
df_sorted["rank_pct"] = (df_sorted.index + 1) / n
decile_shares = []
for d in range(1, 11):
    lo = (d - 1) / 10
    hi = d / 10
    mask = (df_sorted["rank_pct"] > lo) & (df_sorted["rank_pct"] <= hi)
    decile_shares.append({
        "decile_by_pnl_rank": d,  # 1 = biggest winners
        "n": int(mask.sum()),
        "pnl_gbp_sum": float(df_sorted.loc[mask, "pnl_gbp"].sum()),
        "share_of_total_pnl": float(df_sorted.loc[mask, "pnl_gbp"].sum() / total_pnl),
    })

results["part1_concentration"] = {
    "top_10_trades_share_of_total_pnl": top10_share,
    "top_1pct_n": top1pct_n, "top_1pct_share_of_total_pnl": top1pct_share,
    "top_decile_n": top_decile_n, "top_decile_share_of_total_pnl": top_decile_share,
    "top_10_trades_share_of_GROSS_PROFIT": top10_of_gross,
    "distribution": dist,
    "decile_breakdown_by_pnl_rank": decile_shares,
    "top_10_trades": df_sorted.head(10)[
        ["ticker", "entry_date", "exit_date", "pnl_gbp", "pnl_pct", "exit_reason", "holding_days"]
    ].assign(entry_date=lambda d: d["entry_date"].dt.date.astype(str),
             exit_date=lambda d: d["exit_date"].dt.date.astype(str)).to_dict("records"),
}

print(f"\n--- PART 1: Concentration ---")
print(f"Top 10 trades / total P&L: {top10_share:.1%}")
print(f"Top 1% ({top1pct_n} trades) / total P&L: {top1pct_share:.1%}")
print(f"Top decile ({top_decile_n} trades) / total P&L: {top_decile_share:.1%}")
print(f"Top 10 trades / GROSS profit (winners only): {top10_of_gross:.1%}")
print(f"Win rate: {dist['win_rate']:.1%}  PF: {dist['profit_factor']:.3f}")
print(f"pnl_gbp: mean={dist['pnl_gbp']['mean']:.2f} median={dist['pnl_gbp']['median']:.2f} skew={dist['pnl_gbp']['skew']:.2f}")

# ---------------------------------------------------------------------------
# PART 2: Does entry momentum predict anything?
# ---------------------------------------------------------------------------
mdf = df.dropna(subset=["momentum_at_entry_pct"]).copy()
mdf["decile"] = pd.qcut(mdf["momentum_at_entry_pct"], 10, labels=False, duplicates="drop") + 1

bucket_rows = []
for d, g in mdf.groupby("decile"):
    bucket_rows.append({
        "decile": int(d),
        "n": int(len(g)),
        "momentum_min": float(g["momentum_at_entry_pct"].min()),
        "momentum_max": float(g["momentum_at_entry_pct"].max()),
        "momentum_mean": float(g["momentum_at_entry_pct"].mean()),
        "win_rate": float((g["pnl_gbp"] > 0).mean()),
        "mean_pnl_pct": float(g["pnl_pct"].mean()),
        "median_pnl_pct": float(g["pnl_pct"].median()),
        "mean_holding_days": float(g["holding_days"].mean()),
        "mean_pnl_gbp": float(g["pnl_gbp"].mean()),
    })
bucket_rows.sort(key=lambda r: r["decile"])

spearman = float(mdf["momentum_at_entry_pct"].corr(mdf["pnl_pct"], method="spearman"))
pearson = float(mdf["momentum_at_entry_pct"].corr(mdf["pnl_pct"], method="pearson"))

# Monotonicity check: is win_rate / mean_pnl_pct monotonically non-decreasing
# across deciles? Report the actual sequence and a simple rank-correlation
# of decile-number vs decile-mean-return as the monotonicity statistic.
decile_nums = [r["decile"] for r in bucket_rows]
decile_means = [r["mean_pnl_pct"] for r in bucket_rows]
decile_winrates = [r["win_rate"] for r in bucket_rows]
mono_corr_mean = float(pd.Series(decile_nums).corr(pd.Series(decile_means), method="spearman"))
mono_corr_winrate = float(pd.Series(decile_nums).corr(pd.Series(decile_winrates), method="spearman"))

results["part2_momentum_predictiveness"] = {
    "n_with_momentum": int(len(mdf)),
    "spearman_corr_momentum_vs_pnl_pct": spearman,
    "pearson_corr_momentum_vs_pnl_pct": pearson,
    "decile_rank_vs_decile_mean_return_spearman": mono_corr_mean,
    "decile_rank_vs_decile_winrate_spearman": mono_corr_winrate,
    "deciles": bucket_rows,
}

print(f"\n--- PART 2: Momentum predictiveness ---")
print(f"Spearman(momentum, pnl_pct) = {spearman:.4f}   Pearson = {pearson:.4f}")
print(f"decile-rank vs decile-mean-return Spearman = {mono_corr_mean:.4f}")
print(f"decile-rank vs decile-winrate Spearman = {mono_corr_winrate:.4f}")
for r in bucket_rows:
    print(f"  D{r['decile']:2d}  n={r['n']:4d}  mom=[{r['momentum_min']:8.2f},{r['momentum_max']:8.2f}]  "
          f"win={r['win_rate']:.1%}  mean_pnl%={r['mean_pnl_pct']*100:6.2f}  "
          f"median_pnl%={r['median_pnl_pct']*100:6.2f}  hold={r['mean_holding_days']:5.1f}d")

with open(f"{OUT_DIR}/part1_2_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nWrote {OUT_DIR}/part1_2_results.json")
