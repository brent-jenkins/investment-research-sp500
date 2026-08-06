"""Step 1 -- rigorous, INDEPENDENT confirmation of the "entry-time
extension ceiling" hypothesis: does entering a position while its price
sits far above its own 14/30/50-day moving averages predict a worse
outcome than entering while price sits below them?

Motivating manual finding (from a review of tonight's enriched CSV
export): trades entered below all three MAs averaged 3.05% return vs.
1.28% for trades entered above all three -- monotonic, survived being
checked within MAX_HOLDING trades alone (no exit-reason contamination),
and survived a partial-Spearman check controlling for entry momentum
(rho=-0.055, p=0.002, i.e. not just momentum in disguise). NOT yet
checked: outlier robustness, or a rigorous multi-test regime
decomposition -- that is what this script does.

CRITICAL METHODOLOGICAL POINT, per the task's own explicit instruction:
the earlier enriched CSV export's own ma_14_at_entry/ma_30_at_entry/
ma_50_at_entry columns are NOT reused here. That export was a data-
export task, not a fidelity-checked research engine. This script
independently, freshly recomputes all three moving averages directly
from stored price_data, using its own rolling-mean computation -- a
genuinely separate code path from the export script's own MA-building
function. A cross-check against the export's own MA values is run
afterward as a bonus consistency check, not a substitute for the
independent computation.

Trade population: REUSED from research/full_trade_export_enriched_2026-
08-06/full_trade_export_enriched_2007-2026.csv -- specifically only its
DIRECT, already-fidelity-verified engine fields (ticker, entry_date,
exit_date, entry_price, pnl_pct, exit_reason, momentum_at_entry_pct,
holding_days), never its MA columns. Reused rather than re-running the
whole backtest because the environment is confirmed unchanged since
that export ran earlier tonight (same session: git status clean of any
code change, price_data DB coverage identical 2007-01-01->2026-08-03/
540 tickers) -- re-running simulate_portfolio_compounding_mtm() again
would reproduce byte-identical trades at real, non-trivial cost for zero
new information.

Backtest convention (per CLAUDE.md's "Two backtest conventions" standing
requirement): CONTINUOUS WALK -- purely retrospective, not a variant
comparison.

Era buckets: 3-way split, matching the convention used throughout
tonight's session (seasonal_stopout, exploratory_hypothesis_pass):
2007-2009 GFC / 2010-2019 bull / 2020-2026 recent.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sp_stats

from data.ingestion import get_price_df

TRADES_CSV = r'c:\GitHub\investment-research-sp500\research\full_trade_export_enriched_2026-08-06\full_trade_export_enriched_2007-2026.csv'
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\ma_extension_ceiling_2026-08-06'
MA_WINDOWS = (14, 30, 50)


def _era(entry_date: pd.Timestamp) -> str:
    y = entry_date.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def _partial_spearman(x: pd.Series, y: pd.Series, z: pd.Series):
    """Partial Spearman correlation of x,y controlling for z, via the
    standard rank-residual method: rank-transform all three series, OLS-
    regress ranked-x and ranked-y each on ranked-z, then Pearson-correlate
    the two residual series. Returns (rho, p, n)."""
    df = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
    n = len(df)
    rx = sp_stats.rankdata(df["x"])
    ry = sp_stats.rankdata(df["y"])
    rz = sp_stats.rankdata(df["z"])
    Xz = sm.add_constant(rz.astype(float))
    resid_x = sm.OLS(rx.astype(float), Xz).fit().resid
    resid_y = sm.OLS(ry.astype(float), Xz).fit().resid
    rho, p = sp_stats.pearsonr(resid_x, resid_y)
    return rho, p, n


def main():
    print("=== Step 1: entry-time MA-extension ceiling -- independent confirmation ===", flush=True)

    df = pd.read_csv(TRADES_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    print(f"Loaded {len(df)} trades (direct engine fields only: ticker, entry_date, exit_date, "
          f"entry_price, pnl_pct, exit_reason, momentum_at_entry_pct, holding_days -- "
          f"MA columns from this file explicitly NOT used).", flush=True)

    exit_counts = df["exit_reason"].value_counts()
    print("Exit-reason breakdown (cross-check against established baseline):")
    print(exit_counts.to_string())
    assert len(df) == 3161, f"Expected 3161 trades, got {len(df)}"
    assert exit_counts.get("MAX_HOLDING", 0) == 1796, f"Expected 1796 MAX_HOLDING, got {exit_counts.get('MAX_HOLDING')}"
    print("Baseline reproduction confirmed exactly.\n", flush=True)

    df["era"] = df["entry_date"].apply(_era)

    # ------------------------------------------------------------------
    # 1. INDEPENDENT, fresh MA14/30/50 recomputation directly from stored
    #    price_data -- a separate code path from the export script's own.
    # ------------------------------------------------------------------
    unique_tickers = sorted(df["ticker"].unique())
    print(f"Independently recomputing MA{MA_WINDOWS} for {len(unique_tickers)} unique traded tickers...", flush=True)
    ma_frames = {}
    for i, ticker in enumerate(unique_tickers):
        pdf = get_price_df(ticker, days=8000)
        if pdf.empty:
            continue
        pdf = pdf.set_index("date")
        pdf.index = pd.to_datetime(pdf.index)
        close = pdf["close"].astype(float).sort_index()
        mas = pd.DataFrame({"close": close})
        for w in MA_WINDOWS:
            mas[f"ma_{w}"] = close.rolling(w).mean()
        ma_frames[ticker] = mas
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(unique_tickers)} tickers done", flush=True)
    print(f"  {len(ma_frames)}/{len(unique_tickers)} tickers have usable price history\n", flush=True)

    records = []
    n_missing = 0
    for row in df.itertuples():
        ticker = row.ticker
        entry_date = row.entry_date
        frame = ma_frames.get(ticker)
        if frame is None or entry_date not in frame.index:
            n_missing += 1
            continue
        entry_price = float(row.entry_price)
        ma_vals = {}
        pos_vs = {}
        ok = True
        for w in MA_WINDOWS:
            v = frame.loc[entry_date, f"ma_{w}"]
            if pd.isna(v):
                ok = False
                break
            ma_vals[w] = float(v)
            pos_vs[w] = entry_price / float(v) - 1.0
        if not ok:
            n_missing += 1
            continue
        n_above = sum(1 for w in MA_WINDOWS if pos_vs[w] > 0)
        records.append({
            "ticker": ticker, "entry_date": entry_date, "exit_date": row.exit_date,
            "era": row.era, "exit_reason": row.exit_reason, "pnl_pct": row.pnl_pct,
            "momentum_at_entry_pct": row.momentum_at_entry_pct, "holding_days": row.holding_days,
            "entry_price": entry_price,
            "ma_14": ma_vals[14], "ma_30": ma_vals[30], "ma_50": ma_vals[50],
            "pos_vs_ma14_pct": pos_vs[14] * 100, "pos_vs_ma30_pct": pos_vs[30] * 100,
            "pos_vs_ma50_pct": pos_vs[50] * 100,
            "n_mas_above": n_above,
            "below_all_three": n_above == 0, "above_all_three": n_above == 3,
        })
    print(f"MA recomputation missing/unusable for {n_missing}/{len(df)} trades "
          f"(insufficient price history before {MA_WINDOWS[-1]}-day window)", flush=True)

    out_df = pd.DataFrame(records)
    out_df.to_csv(f"{OUT_DIR}/trades_with_ma_extension.csv", index=False)
    print(f"Wrote {len(out_df)} annotated trades -> trades_with_ma_extension.csv\n", flush=True)

    # ------------------------------------------------------------------
    # 1b. Bonus consistency check against the export's own MA columns
    #     (not a substitute for the independent computation above).
    # ------------------------------------------------------------------
    export_df = pd.read_csv(TRADES_CSV)[["ticker", "entry_date", "ma_14_at_entry", "ma_30_at_entry", "ma_50_at_entry"]]
    export_df["entry_date"] = pd.to_datetime(export_df["entry_date"])
    merged = out_df.merge(export_df, on=["ticker", "entry_date"], how="inner")
    for w in MA_WINDOWS:
        diff = (merged[f"ma_{w}"] - merged[f"ma_{w}_at_entry"]).abs()
        print(f"Consistency check MA{w}: max abs diff vs. export's own column = {diff.max():.6f} "
              f"(n={len(merged)})")

    # ------------------------------------------------------------------
    # 2. Group comparisons -- 4-level ordinal (n_mas_above 0/1/2/3),
    #    whole population and MAX_HOLDING-only.
    # ------------------------------------------------------------------
    def group_table(data: pd.DataFrame, label: str):
        print(f"\n=== {label} (n={len(data)}) ===")
        rows = []
        for k in (0, 1, 2, 3):
            sub = data[data["n_mas_above"] == k]
            if sub.empty:
                continue
            rows.append({
                "n_mas_above": k, "n": len(sub),
                # NOTE: pnl_pct is already stored as a percentage (e.g. -6.0791
                # means -6.08%), not a fraction -- no further *100 needed here.
                "mean_pnl_pct": sub["pnl_pct"].mean(),
                "median_pnl_pct": sub["pnl_pct"].median(),
                "pct_positive": (sub["pnl_pct"] > 0).mean() * 100,
            })
        t = pd.DataFrame(rows)
        print(t.to_string(index=False))
        return t

    whole_group = group_table(out_df, "Whole population, by n_mas_above")
    maxhold_group = group_table(out_df[out_df["exit_reason"] == "MAX_HOLDING"], "MAX_HOLDING only, by n_mas_above")
    whole_group.to_csv(f"{OUT_DIR}/group_table_whole.csv", index=False)
    maxhold_group.to_csv(f"{OUT_DIR}/group_table_maxhold.csv", index=False)

    # ------------------------------------------------------------------
    # 3. Two-group comparison: below-all-three vs above-all-three.
    # ------------------------------------------------------------------
    def two_group_test(data: pd.DataFrame, label: str):
        below = data[data["below_all_three"]]["pnl_pct"].dropna()
        above = data[data["above_all_three"]]["pnl_pct"].dropna()
        u, p = sp_stats.mannwhitneyu(below, above, alternative="two-sided")
        print(f"\n--- {label}: below-all-three (n={len(below)}, mean={below.mean()*100:.2f}%) vs "
              f"above-all-three (n={len(above)}, mean={above.mean()*100:.2f}%) ---")
        print(f"  median: below={below.median()*100:.2f}%  above={above.median()*100:.2f}%")
        print(f"  Mann-Whitney U={u:.1f} p={p:.6f}")
        return {"label": label, "n_below": len(below), "mean_below": below.mean(),
                "n_above": len(above), "mean_above": above.mean(), "mw_p": p}

    tg_whole = two_group_test(out_df, "Whole population")
    tg_maxhold = two_group_test(out_df[out_df["exit_reason"] == "MAX_HOLDING"], "MAX_HOLDING only")
    pd.DataFrame([tg_whole, tg_maxhold]).to_csv(f"{OUT_DIR}/two_group_results.csv", index=False)

    # ------------------------------------------------------------------
    # 4. Continuous correlation (Spearman) -- all 3 windows, whole pop +
    #    MAX_HOLDING only.
    # ------------------------------------------------------------------
    print("\n=== Spearman correlations (pos_vs_maN_pct vs pnl_pct) ===")
    corr_rows = []
    maxhold_df = out_df[out_df["exit_reason"] == "MAX_HOLDING"]
    for w in MA_WINDOWS:
        col = f"pos_vs_ma{w}_pct"
        for scope_name, scope_df in [("ALL", out_df), ("MAX_HOLDING_only", maxhold_df)]:
            sub = scope_df[[col, "pnl_pct"]].dropna()
            rho, p = sp_stats.spearmanr(sub[col], sub["pnl_pct"])
            print(f"  MA{w}, {scope_name}: rho={rho:.4f} p={p:.6f} n={len(sub)}")
            corr_rows.append({"window": w, "scope": scope_name, "rho": rho, "p": p, "n": len(sub)})
    pd.DataFrame(corr_rows).to_csv(f"{OUT_DIR}/spearman_results.csv", index=False)

    # ------------------------------------------------------------------
    # 5. Partial correlation controlling for entry momentum.
    # ------------------------------------------------------------------
    print("\n=== Partial Spearman (pos_vs_ma50_pct, pnl_pct | momentum_at_entry_pct) ===")
    partial_rows = []
    for scope_name, scope_df in [("ALL", out_df), ("MAX_HOLDING_only", maxhold_df)]:
        rho, p, n = _partial_spearman(scope_df["pos_vs_ma50_pct"], scope_df["pnl_pct"], scope_df["momentum_at_entry_pct"])
        print(f"  {scope_name}: partial rho={rho:.4f} p={p:.6f} n={n}")
        partial_rows.append({"scope": scope_name, "window": 50, "partial_rho": rho, "p": p, "n": n})
    pd.DataFrame(partial_rows).to_csv(f"{OUT_DIR}/partial_correlation_results.csv", index=False)

    # ------------------------------------------------------------------
    # 6. Outlier robustness -- trim top/bottom 1% of pnl_pct.
    # ------------------------------------------------------------------
    print("\n=== Outlier robustness (trim top/bottom 1% of pnl_pct) ===")
    outlier_rows = []
    for scope_name, scope_df in [("ALL", out_df), ("MAX_HOLDING_only", maxhold_df)]:
        valid = scope_df.dropna(subset=["pnl_pct", "pos_vs_ma50_pct"])
        lo, hi = valid["pnl_pct"].quantile(0.01), valid["pnl_pct"].quantile(0.99)
        trimmed = valid[(valid["pnl_pct"] >= lo) & (valid["pnl_pct"] <= hi)]
        rho, p = sp_stats.spearmanr(trimmed["pos_vs_ma50_pct"], trimmed["pnl_pct"])
        print(f"  {scope_name} TRIMMED: rho={rho:.4f} p={p:.6f} n={len(trimmed)} "
              f"(untrimmed n={len(valid)})")
        outlier_rows.append({"scope": scope_name, "rho_trimmed": rho, "p_trimmed": p, "n_trimmed": len(trimmed)})

        below = trimmed[trimmed["below_all_three"]]["pnl_pct"]
        above = trimmed[trimmed["above_all_three"]]["pnl_pct"]
        if len(below) >= 2 and len(above) >= 2:
            u, p2 = sp_stats.mannwhitneyu(below, above, alternative="two-sided")
            print(f"    below-vs-above (trimmed): below mean={below.mean()*100:.2f}% (n={len(below)}) "
                  f"above mean={above.mean()*100:.2f}% (n={len(above)}) MW p={p2:.6f}")
    pd.DataFrame(outlier_rows).to_csv(f"{OUT_DIR}/outlier_robustness.csv", index=False)

    # ------------------------------------------------------------------
    # 7. Regime decomposition -- within each of the 3 eras, MAX_HOLDING-only
    #    (matches how the motivating manual finding itself was checked).
    # ------------------------------------------------------------------
    print("\n=== Regime decomposition (MAX_HOLDING only, within each era) ===")
    era_rows = []
    for era_name in ["2007-2009_GFC", "2010-2019_bull", "2020-2026_recent"]:
        sub = maxhold_df[maxhold_df["era"] == era_name]
        rho, p = sp_stats.spearmanr(sub["pos_vs_ma50_pct"], sub["pnl_pct"])
        below = sub[sub["below_all_three"]]["pnl_pct"]
        above = sub[sub["above_all_three"]]["pnl_pct"]
        mw_p = np.nan
        if len(below) >= 2 and len(above) >= 2:
            _, mw_p = sp_stats.mannwhitneyu(below, above, alternative="two-sided")
        print(f"  {era_name}: n={len(sub)}  Spearman rho={rho:.4f} p={p:.6f}  "
              f"below-mean={below.mean() if len(below) else np.nan:.2f}% (n={len(below)})  "
              f"above-mean={above.mean() if len(above) else np.nan:.2f}% (n={len(above)})  MW p={mw_p}")
        era_rows.append({
            "era": era_name, "n": len(sub), "spearman_rho": rho, "spearman_p": p,
            "n_below": len(below), "mean_below_pct": below.mean() if len(below) else None,
            "n_above": len(above), "mean_above_pct": above.mean() if len(above) else None,
            "mw_p": mw_p,
        })
    pd.DataFrame(era_rows).to_csv(f"{OUT_DIR}/regime_decomposition.csv", index=False)

    # ------------------------------------------------------------------
    # 8. Distribution of extension (pos_vs_ma50_pct) by era -- to help
    #    explain WHY the effect might be regime-inconsistent.
    # ------------------------------------------------------------------
    print("\n=== Distribution of pos_vs_ma50_pct at entry, by era (all trades) ===")
    dist_rows = []
    for era_name in ["2007-2009_GFC", "2010-2019_bull", "2020-2026_recent"]:
        sub = out_df[out_df["era"] == era_name]["pos_vs_ma50_pct"]
        row = {
            "era": era_name, "n": len(sub), "mean": sub.mean(), "median": sub.median(),
            "q25": sub.quantile(0.25), "q75": sub.quantile(0.75),
            "pct_above_all_three": 100 * (out_df[out_df["era"] == era_name]["above_all_three"]).mean(),
            "pct_below_all_three": 100 * (out_df[out_df["era"] == era_name]["below_all_three"]).mean(),
        }
        print(f"  {era_name}: n={row['n']} mean={row['mean']:.2f}% median={row['median']:.2f}% "
              f"q25={row['q25']:.2f}% q75={row['q75']:.2f}% "
              f"%above_all_three={row['pct_above_all_three']:.1f}% %below_all_three={row['pct_below_all_three']:.1f}%")
        dist_rows.append(row)
    pd.DataFrame(dist_rows).to_csv(f"{OUT_DIR}/extension_distribution_by_era.csv", index=False)

    print("\n=== Step 1 done ===", flush=True)


if __name__ == "__main__":
    main()
