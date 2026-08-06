"""Step 1 -- rigorous, PRE-SPECIFIED confirmation of the seasonal
stop-out-rate hypothesis raised in the exploratory pass
(research/exploratory_hypothesis_pass_2026-08-05/REPORT.md, candidate #1).

Question: does entry SEASON predict exit-reason MIX (survival to the
30-day MAX_HOLDING cap vs. getting STOP_LOSS'd first) -- a composition
effect, not a magnitude effect? Structural rationale: Santa Claus
rally/January effect (Nov-Jan) vs. "sell in May"/thin summer liquidity
(Jun-Aug), independent, well-documented equity-market seasonality.

CRITICAL METHODOLOGICAL POINT, stated per the task's own explicit
instruction: the exploratory pass's own observation compared the two
MOST EXTREME months out of twelve (December vs. July) -- a real
multiple-comparisons risk distinct from every other investigation
tonight, each of which tested one pre-specified signal. With twelve
groups, some pairwise gap will look striking under pure noise even with
no real effect. THIS SCRIPT DOES NOT RE-CONFIRM DECEMBER VS. JULY AS THE
TEST. The confirmatory test is the PRE-SPECIFIED, literature-grounded
grouping the structural rationale actually points to: entry month in
{Nov, Dec, Jan} vs. entry month in {Jun, Jul, Aug}. The full 12-month
breakdown is reported separately, for transparency only -- it is not
itself the confirmatory test.

Backtest convention (per CLAUDE.md's "Two backtest conventions" standing
requirement): CONTINUOUS WALK -- this is a purely retrospective read of
one already-computed, real trade population, not a variant comparison.

Trade population: reused directly from this SAME session's own fresh
run at research/momentum_volume_reeval_cap_2026-08-06/all_trades_full_run.json
(simulate_portfolio_compounding_mtm(), live_universe=True,
mark_to_market=True, current code, run earlier tonight -- confirmed
total_trades=3161, exit_reason breakdown MAX_HOLDING=1796/STOP_LOSS=1169/
SIGNAL_OFF=131/CRISIS_EXIT=35/END_OF_DATA=30, matching the exact figures
FOUR separate investigations tonight (concentration_risk, momentum_horizon,
dynamic_momentum_cap, stop_cluster_coordination) independently reproduced,
AND confirmed identical after the d1a067a BRK.B/rolling-window fix landed
(that fix affects live ingestion, not stored historical price_data). Not
re-run from scratch here since nothing about the engine, config, or
database changed between that run (earlier tonight, same session) and now.

No custom candidate pool or monkeypatched is_member() is used anywhere in
this script -- it is a pure read/transform of the real engine's own
already-generated trade list (entry_date, exit_date, exit_reason, pnl_pct,
holding_days, stop_distance_pct -- all fields the real engine itself
produced), so the "Research scripts must verify decision-engine parity"
standing requirement does not apply to trade generation here.
"""
import json

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sp_stats

TRADES_PATH = r'c:\GitHub\investment-research-sp500\research\momentum_volume_reeval_cap_2026-08-06\all_trades_full_run.json'
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\seasonal_stopout_2026-08-06'

SEASON_A_MONTHS = {11, 12, 1}   # Nov-Jan: Santa Claus rally / January effect
SEASON_B_MONTHS = {6, 7, 8}     # Jun-Aug: "sell in May" aftermath / summer thin liquidity


def _era(entry_date: pd.Timestamp) -> str:
    """3-way era split -- matching the exploratory pass's own scheme
    (2007-2009 GFC / 2010-2019 bull / 2020-2026 recent), not the 4-way
    split used in a separate, unrelated investigation earlier tonight --
    deliberately consistent with the task this script follows up on."""
    y = entry_date.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def main():
    print("=== Step 1: seasonal stop-out-rate pre-specified confirmation ===", flush=True)

    with open(TRADES_PATH) as f:
        trades = json.load(f)
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    print(f"Loaded {len(df)} trades from {TRADES_PATH}", flush=True)

    exit_counts = df["exit_reason"].value_counts()
    print("Exit-reason breakdown (cross-check against established baseline):")
    print(exit_counts.to_string())
    assert len(df) == 3161, f"Expected 3161 trades, got {len(df)}"
    assert exit_counts.get("MAX_HOLDING", 0) == 1796, f"Expected 1796 MAX_HOLDING, got {exit_counts.get('MAX_HOLDING')}"
    print("Baseline reproduction confirmed exactly.\n", flush=True)

    df["entry_month"] = df["entry_date"].dt.month
    df["era"] = df["entry_date"].apply(_era)
    df["survived_to_cap"] = (df["exit_reason"] == "MAX_HOLDING").astype(int)
    df["exit_bucket"] = df["exit_reason"].apply(
        lambda r: r if r in ("MAX_HOLDING", "STOP_LOSS") else "OTHER"
    )

    # ------------------------------------------------------------------
    # 1. Full 12-month breakdown -- transparency only, NOT the confirmatory test.
    # ------------------------------------------------------------------
    print("=== Full 12-month exit-reason breakdown (transparency, not the confirmatory test) ===")
    month_rows = []
    for m in range(1, 13):
        sub = df[df["entry_month"] == m]
        if sub.empty:
            continue
        vc = sub["exit_reason"].value_counts(normalize=True) * 100
        month_rows.append({
            "month": m, "n": len(sub),
            "MAX_HOLDING_pct": vc.get("MAX_HOLDING", 0.0),
            "STOP_LOSS_pct": vc.get("STOP_LOSS", 0.0),
            "SIGNAL_OFF_pct": vc.get("SIGNAL_OFF", 0.0),
            "CRISIS_EXIT_pct": vc.get("CRISIS_EXIT", 0.0),
            "END_OF_DATA_pct": vc.get("END_OF_DATA", 0.0),
        })
    month_df = pd.DataFrame(month_rows)
    print(month_df.to_string(index=False))
    month_df.to_csv(f"{OUT_DIR}/full_12month_breakdown.csv", index=False)

    # ------------------------------------------------------------------
    # 2. PRE-SPECIFIED confirmatory test: Nov-Jan vs Jun-Aug.
    # ------------------------------------------------------------------
    print("\n=== PRE-SPECIFIED confirmatory test: Nov-Jan vs Jun-Aug ===")
    season_df = df[df["entry_month"].isin(SEASON_A_MONTHS | SEASON_B_MONTHS)].copy()
    season_df["season"] = season_df["entry_month"].apply(
        lambda m: "NovJan" if m in SEASON_A_MONTHS else "JunAug"
    )
    n_a = (season_df["season"] == "NovJan").sum()
    n_b = (season_df["season"] == "JunAug").sum()
    print(f"n NovJan={n_a}  n JunAug={n_b}  (excluded {len(df) - len(season_df)} trades in other months)")

    # Chi-squared on exit-reason composition (3-bucket: MAX_HOLDING/STOP_LOSS/OTHER)
    ct = pd.crosstab(season_df["season"], season_df["exit_bucket"])
    print("\nContingency table (season x exit_bucket):")
    print(ct.to_string())
    chi2, p_chi2, dof, expected = sp_stats.chi2_contingency(ct)
    print(f"\nChi-squared: chi2={chi2:.4f}  p={p_chi2:.6f}  dof={dof}")

    # Same, but MAX_HOLDING vs STOP_LOSS only (the two dominant, seasonally-relevant reasons)
    ct2 = pd.crosstab(season_df["season"], season_df["exit_reason"].where(
        season_df["exit_reason"].isin(["MAX_HOLDING", "STOP_LOSS"])
    ).dropna())
    ct2_sub = season_df[season_df["exit_reason"].isin(["MAX_HOLDING", "STOP_LOSS"])]
    ct2 = pd.crosstab(ct2_sub["season"], ct2_sub["exit_reason"])
    print("\nContingency table (season x MAX_HOLDING/STOP_LOSS only):")
    print(ct2.to_string())
    chi2b, p_chi2b, dofb, _ = sp_stats.chi2_contingency(ct2)
    print(f"Chi-squared (MAX_HOLDING/STOP_LOSS only): chi2={chi2b:.4f}  p={p_chi2b:.6f}  dof={dofb}")

    pct_a = 100 * season_df[season_df["season"] == "NovJan"]["survived_to_cap"].mean()
    pct_b = 100 * season_df[season_df["season"] == "JunAug"]["survived_to_cap"].mean()
    print(f"\nMAX_HOLDING (survival) rate: NovJan={pct_a:.1f}%  JunAug={pct_b:.1f}%  gap={pct_a-pct_b:.1f}pp")

    # Logistic regression: survived_to_cap ~ season + era dummies (era-controlled)
    print("\n--- Logistic regression: survived_to_cap ~ season + era (era-controlled) ---")
    reg_df = season_df.copy()
    reg_df["season_NovJan"] = (reg_df["season"] == "NovJan").astype(int)
    era_dummies = pd.get_dummies(reg_df["era"], prefix="era", drop_first=True).astype(float)
    X = pd.concat([reg_df[["season_NovJan"]].astype(float), era_dummies], axis=1)
    X = sm.add_constant(X)
    y = reg_df["survived_to_cap"].astype(float)
    logit_model = sm.Logit(y, X).fit(disp=0)
    print(logit_model.summary())
    with open(f"{OUT_DIR}/logit_summary.txt", "w") as fh:
        fh.write(str(logit_model.summary()))

    season_df.to_csv(f"{OUT_DIR}/season_ab_trades.csv", index=False)

    # ------------------------------------------------------------------
    # 3. Regime confounding check -- within each of the 3 eras separately.
    # ------------------------------------------------------------------
    print("\n=== Regime confounding check -- within each era separately ===")
    era_rows = []
    for era_name in ["2007-2009_GFC", "2010-2019_bull", "2020-2026_recent"]:
        sub = season_df[season_df["era"] == era_name]
        sub_a = sub[sub["season"] == "NovJan"]
        sub_b = sub[sub["season"] == "JunAug"]
        if len(sub_a) < 2 or len(sub_b) < 2:
            print(f"  {era_name}: insufficient n (NovJan={len(sub_a)}, JunAug={len(sub_b)}) -- skipped")
            continue
        ct_era = pd.crosstab(sub["season"], sub["exit_bucket"])
        try:
            chi2_e, p_e, dof_e, _ = sp_stats.chi2_contingency(ct_era)
        except ValueError as e:
            chi2_e, p_e, dof_e = np.nan, np.nan, np.nan
        pct_a_e = 100 * sub_a["survived_to_cap"].mean()
        pct_b_e = 100 * sub_b["survived_to_cap"].mean()
        print(f"  {era_name}: n_NovJan={len(sub_a)} n_JunAug={len(sub_b)} "
              f"MAX_HOLDING%: NovJan={pct_a_e:.1f}% JunAug={pct_b_e:.1f}% gap={pct_a_e-pct_b_e:.1f}pp "
              f"chi2 p={p_e}")
        era_rows.append({
            "era": era_name, "n_NovJan": len(sub_a), "n_JunAug": len(sub_b),
            "MAX_HOLDING_pct_NovJan": pct_a_e, "MAX_HOLDING_pct_JunAug": pct_b_e,
            "gap_pp": pct_a_e - pct_b_e, "chi2": chi2_e, "chi2_p": p_e,
        })
    pd.DataFrame(era_rows).to_csv(f"{OUT_DIR}/within_era_check.csv", index=False)

    # ------------------------------------------------------------------
    # 4. Composition-not-magnitude check: mean pnl_pct within each exit
    #    type, by season -- should be FLAT if this is purely a composition
    #    effect (which exit fires), not a magnitude effect (how good/bad
    #    that exit is once it happens).
    # ------------------------------------------------------------------
    print("\n=== Composition-vs-magnitude check ===")
    mag_rows = []
    for exit_type in ["MAX_HOLDING", "STOP_LOSS"]:
        sub = season_df[season_df["exit_reason"] == exit_type]
        sub_a = sub[sub["season"] == "NovJan"]["pnl_pct"]
        sub_b = sub[sub["season"] == "JunAug"]["pnl_pct"]
        u, p = sp_stats.mannwhitneyu(sub_a, sub_b, alternative="two-sided") if len(sub_a) >= 2 and len(sub_b) >= 2 else (np.nan, np.nan)
        print(f"  {exit_type}: NovJan mean pnl_pct={sub_a.mean()*100:.2f}% (n={len(sub_a)})  "
              f"JunAug mean pnl_pct={sub_b.mean()*100:.2f}% (n={len(sub_b)})  MW p={p}")
        mag_rows.append({
            "exit_type": exit_type, "n_NovJan": len(sub_a), "mean_pnl_pct_NovJan": sub_a.mean(),
            "n_JunAug": len(sub_b), "mean_pnl_pct_JunAug": sub_b.mean(), "mw_p": p,
        })
    pd.DataFrame(mag_rows).to_csv(f"{OUT_DIR}/magnitude_check.csv", index=False)

    print("\n=== Step 1 done ===", flush=True)


if __name__ == "__main__":
    main()
