"""Concentration-risk investigation, Step 2b (follow-up): the worst_episodes.csv
table produced by analyze_concentration.py shows the 19-year backtest's worst
single days heavily dominated by a mid-2026 semiconductor/storage cluster
(SNDK/WDC/MU/LITE/STX/LRCX/TER/AMD/INTC/DELL/...) with indep_gap_60d far
above the ~1.07 population mean -- suggestive of a genuine TAIL-specific
concentration effect that the average-magnitude regression in
analyze_concentration.py (run across ALL n_open>=2 days, most of which are
ordinary days) could dilute or miss entirely. This script tests that
directly: does elevated correlation/joint-risk among open positions predict
landing in the strategy's own bad-day tail, after controlling for
contemporaneous market vol/drawdown regime?

CONVENTIONS (stated explicitly, per CLAUDE.md):
- Reads results/daily_panel.csv, built from the CONTINUOUS WALK trade list
  (see analyze_concentration.py's own header for why that's the right
  convention for this track-record/frequency question).
- All regressors (mkt_vol20, mkt_dd, avg_corr_60d, indep_gap_60d, n_open) are
  computed using data strictly before the day being evaluated (no look-ahead)
  -- inherited unchanged from analyze_concentration.py's own panel build.
- "Bad day" defined three ways for robustness: (1) r_t below the sample's own
  5th percentile (relative/quantile), (2) r_t < -2%, (3) r_t < -3%
  (fixed, economically meaningful thresholds) -- all three reported, not
  just whichever looks best.
"""
import json
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

RESULTS_DIR = r'c:\GitHub\investment-research-sp500\research\concentration_risk_2026-08-05\results'


def run_logit(y, X_df, label):
    X = sm.add_constant(X_df.values)
    try:
        model = sm.Logit(y, X).fit(disp=0, maxiter=200)
        coefs = [float(c) for c in model.params]
        pvals = [float(p) for p in model.pvalues]
        odds = [float(np.exp(c)) for c in model.params]
        converged = bool(model.mle_retvals.get("converged", True))
        pseudo_r2 = float(model.prsquared)
    except Exception as e:
        return {"label": label, "error": str(e)}
    return {
        "label": label,
        "predictors": ["const"] + list(X_df.columns),
        "coefs": coefs, "pvalues": pvals, "odds_ratios": odds,
        "converged": converged, "pseudo_r_squared": pseudo_r2,
        "n_obs": int(len(y)), "n_bad": int(y.sum()),
    }


def main():
    panel = pd.read_csv(RESULTS_DIR + r"\daily_panel.csv", parse_dates=["date"])
    sub = panel[panel["n_open"] >= 2].dropna(
        subset=["r_t", "avg_corr_60d", "indep_gap_60d", "mkt_vol20", "mkt_dd", "n_open"]
    ).copy()
    print(f"Sample: {len(sub)} days with n_open>=2 and complete regressors")

    thresholds = {
        "p5_relative": sub["r_t"].quantile(0.05),
        "fixed_-2pct": -0.02,
        "fixed_-3pct": -0.03,
    }
    print("Bad-day thresholds:", thresholds)

    out = {"n_obs_total": int(len(sub)), "thresholds": {k: float(v) for k, v in thresholds.items()},
           "results": {}}

    for tname, thresh in thresholds.items():
        bad = (sub["r_t"] < thresh).astype(int)
        n_bad = int(bad.sum())
        print(f"\n=== {tname} (threshold={thresh:.4f}): {n_bad} bad days / {len(sub)} ===")

        res_group = {}

        # Logistic regression with avg_corr_60d
        r1 = run_logit(bad.values, sub[["mkt_vol20", "mkt_dd", "n_open", "avg_corr_60d"]], f"{tname}_avgcorr")
        res_group["logit_avg_corr_60d"] = r1
        print("  logit avg_corr_60d coef/p:", r1.get("coefs", [None])[-1], r1.get("pvalues", [None])[-1])

        # Logistic regression with indep_gap_60d (the more direct joint-risk-vs-independence measure)
        r2 = run_logit(bad.values, sub[["mkt_vol20", "mkt_dd", "n_open", "indep_gap_60d"]], f"{tname}_indepgap")
        res_group["logit_indep_gap_60d"] = r2
        print("  logit indep_gap_60d coef/p:", r2.get("coefs", [None])[-1], r2.get("pvalues", [None])[-1])

        # Simple matched (Mann-Whitney, robust to outliers) comparison within vol tercile
        sub["vol_tercile"] = pd.qcut(sub["mkt_vol20"], 3, labels=["low_vol", "mid_vol", "high_vol"])
        bucket_rows = []
        for tercile, g in sub.groupby("vol_tercile", observed=True):
            gb = bad.loc[g.index]
            bad_g = g[gb == 1]
            good_g = g[gb == 0]
            if len(bad_g) < 5 or len(good_g) < 5:
                bucket_rows.append({"tercile": str(tercile), "skipped": True, "n_bad": int(len(bad_g))})
                continue
            u_corr, p_corr = stats.mannwhitneyu(bad_g["avg_corr_60d"], good_g["avg_corr_60d"], alternative="greater")
            u_gap, p_gap = stats.mannwhitneyu(bad_g["indep_gap_60d"], good_g["indep_gap_60d"], alternative="greater")
            bucket_rows.append({
                "tercile": str(tercile), "n_bad": int(len(bad_g)), "n_good": int(len(good_g)),
                "mean_avg_corr_60d_bad": float(bad_g["avg_corr_60d"].mean()),
                "mean_avg_corr_60d_good": float(good_g["avg_corr_60d"].mean()),
                "mannwhitney_p_avg_corr_bad_greater": float(p_corr),
                "mean_indep_gap_60d_bad": float(bad_g["indep_gap_60d"].mean()),
                "mean_indep_gap_60d_good": float(good_g["indep_gap_60d"].mean()),
                "mannwhitney_p_indep_gap_bad_greater": float(p_gap),
            })
        res_group["matched_tercile_mannwhitney"] = bucket_rows
        for row in bucket_rows:
            print("   ", row)

        out["results"][tname] = res_group

    # Direct headline stat: of the worst 25 single days overall (already
    # saved to worst_episodes.csv), what fraction have indep_gap_60d above
    # the FULL SAMPLE's own 90th / 95th percentile?
    worst25 = sub.sort_values("r_t").head(25)
    p90 = sub["indep_gap_60d"].quantile(0.90)
    p95 = sub["indep_gap_60d"].quantile(0.95)
    frac_above_90 = float((worst25["indep_gap_60d"] > p90).mean())
    frac_above_95 = float((worst25["indep_gap_60d"] > p95).mean())
    out["worst_25_days_headline"] = {
        "full_sample_indep_gap_60d_p90": float(p90),
        "full_sample_indep_gap_60d_p95": float(p95),
        "fraction_of_worst25_days_with_indep_gap_above_p90": frac_above_90,
        "fraction_of_worst25_days_with_indep_gap_above_p95": frac_above_95,
        "expected_fraction_under_null_p90": 0.10,
        "expected_fraction_under_null_p95": 0.05,
        "worst25_mean_indep_gap_60d": float(worst25["indep_gap_60d"].mean()),
        "full_sample_mean_indep_gap_60d": float(sub["indep_gap_60d"].mean()),
    }
    print("\nWorst-25-days headline:", json.dumps(out["worst_25_days_headline"], indent=2))

    # Binomial test: is the worst-25 hit rate above p90/p95 significantly
    # higher than the null (10%/5%) rate?
    n90 = int((worst25["indep_gap_60d"] > p90).sum())
    n95 = int((worst25["indep_gap_60d"] > p95).sum())
    bt90 = stats.binomtest(n90, 25, 0.10, alternative="greater")
    bt95 = stats.binomtest(n95, 25, 0.05, alternative="greater")
    out["worst_25_days_headline"]["binomial_test_p90_p_value"] = float(bt90.pvalue)
    out["worst_25_days_headline"]["binomial_test_p95_p_value"] = float(bt95.pvalue)
    print("binomial p90 p-value:", bt90.pvalue, " binomial p95 p-value:", bt95.pvalue)

    with open(RESULTS_DIR + r"\tail_risk_isolation.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\nSaved tail_risk_isolation.json")


if __name__ == "__main__":
    main()
