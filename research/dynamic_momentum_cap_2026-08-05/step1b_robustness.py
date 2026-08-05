"""Step 1b -- robustness follow-up on the Step 1 finding.

Step 1 found: Spearman(trend_since_entry, fwd_ret_30d) rho=0.055, p=0.020,
n=1782; OLS coef on trend_since_entry = 0.0242, p<0.001 controlling for
era dummies, R^2=0.023; but the binary Strengthening-vs-Decaying group
comparison (Mann-Whitney) was NOT significant (p=0.17), and the raw mean
gap was only ~0.86pp over 30 days. This script checks whether the
significant continuous relationship is (a) monotonic across quantiles
(real gradient, not a couple of outliers), (b) robust to winsorizing
extreme trend_since_entry / fwd_ret values, (c) distinct from a simple
"momentum LEVEL at the cap point" effect (trend and level are correlated
by construction; need to check trend adds anything beyond level), and
(d) consistent within each era separately (not just era-controlled).
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\dynamic_momentum_cap_2026-08-05'

df = pd.read_csv(f"{OUT_DIR}/max_holding_trend_annotated.csv")
print(f"Loaded {len(df)} annotated MAX_HOLDING trades", flush=True)

valid = df.dropna(subset=["trend_since_entry", "fwd_ret_30d"]).copy()
print(f"Valid for analysis (non-NaN trend/fwd30): {len(valid)}", flush=True)

# ------------------------------------------------------------------
# (a) Quintile gradient check
# ------------------------------------------------------------------
print("\n=== (a) Quintile gradient: trend_since_entry vs fwd_ret_30d ===", flush=True)
valid["trend_quintile"] = pd.qcut(valid["trend_since_entry"], 5, labels=[f"Q{i+1}" for i in range(5)])
q_summary = valid.groupby("trend_quintile", observed=True).agg(
    n=("fwd_ret_30d", "size"),
    mean_trend=("trend_since_entry", "mean"),
    mean_fwd_ret_30d=("fwd_ret_30d", "mean"),
    median_fwd_ret_30d=("fwd_ret_30d", "median"),
    pct_positive=("fwd_ret_30d", lambda x: (x > 0).mean()),
)
print(q_summary.to_string(), flush=True)
q_summary.to_csv(f"{OUT_DIR}/trend_quintile_summary.csv")

# ------------------------------------------------------------------
# (b) Outlier robustness -- winsorize at 1st/99th percentile
# ------------------------------------------------------------------
print("\n=== (b) Outlier robustness (winsorized at 1/99 pct) ===", flush=True)
def winsorize(s, lo=0.01, hi=0.99):
    lo_v, hi_v = s.quantile(lo), s.quantile(hi)
    return s.clip(lo_v, hi_v)

wins = valid.copy()
wins["trend_w"] = winsorize(wins["trend_since_entry"])
wins["fwd30_w"] = winsorize(wins["fwd_ret_30d"])
rho_w, p_w = stats.spearmanr(wins["trend_w"], wins["fwd30_w"])
print(f"Spearman (winsorized): rho={rho_w:.4f}, p={p_w:.4f}, n={len(wins)}", flush=True)

# Drop top/bottom 1% of trend_since_entry entirely (not just clip) and re-test
lo_t, hi_t = valid["trend_since_entry"].quantile([0.01, 0.99])
trimmed = valid[(valid["trend_since_entry"] >= lo_t) & (valid["trend_since_entry"] <= hi_t)]
rho_t, p_t = stats.spearmanr(trimmed["trend_since_entry"], trimmed["fwd_ret_30d"])
print(f"Spearman (trimmed 1/99 pct, n={len(trimmed)}): rho={rho_t:.4f}, p={p_t:.4f}", flush=True)

# ------------------------------------------------------------------
# (c) Trend vs LEVEL -- does trend add anything beyond momentum_at_cap?
# ------------------------------------------------------------------
print("\n=== (c) Trend vs level: correlation and joint OLS ===", flush=True)
rho_lvl, p_lvl = stats.spearmanr(valid["momentum_at_cap"], valid["trend_since_entry"])
print(f"Spearman(momentum_at_cap, trend_since_entry): rho={rho_lvl:.4f}, p={p_lvl:.4f}", flush=True)
rho_lvl_fwd, p_lvl_fwd = stats.spearmanr(valid["momentum_at_cap"], valid["fwd_ret_30d"])
print(f"Spearman(momentum_at_cap, fwd_ret_30d) [level alone]: rho={rho_lvl_fwd:.4f}, p={p_lvl_fwd:.4f}", flush=True)

era_dummies = pd.get_dummies(valid["era"], drop_first=True).astype(float)
X_joint = pd.concat([
    valid[["trend_since_entry", "momentum_at_cap"]].reset_index(drop=True),
    era_dummies.reset_index(drop=True),
], axis=1)
X_joint = sm.add_constant(X_joint)
y = valid["fwd_ret_30d"].reset_index(drop=True).astype(float)
model_joint = sm.OLS(y, X_joint.astype(float)).fit()
print("\nOLS: fwd_ret_30d ~ trend_since_entry + momentum_at_cap + era dummies")
print(model_joint.summary().as_text(), flush=True)
with open(f"{OUT_DIR}/ols_joint_trend_vs_level.txt", "w") as fh:
    fh.write(model_joint.summary().as_text())

# ------------------------------------------------------------------
# (d) Within-era consistency
# ------------------------------------------------------------------
print("\n=== (d) Within-era Spearman(trend_since_entry, fwd_ret_30d) ===", flush=True)
for era, sub in valid.groupby("era"):
    if len(sub) < 20:
        print(f"{era}: n={len(sub)} (too small, skipped)", flush=True)
        continue
    r, p = stats.spearmanr(sub["trend_since_entry"], sub["fwd_ret_30d"])
    print(f"{era}: n={len(sub)}, rho={r:.4f}, p={p:.4f}, "
          f"mean_fwd30_strengthening={sub[sub.trend_class=='Strengthening']['fwd_ret_30d'].mean():.4f} "
          f"(n={ (sub.trend_class=='Strengthening').sum() }), "
          f"mean_fwd30_decaying={sub[sub.trend_class=='Decaying']['fwd_ret_30d'].mean():.4f} "
          f"(n={ (sub.trend_class=='Decaying').sum() })", flush=True)

# ------------------------------------------------------------------
# (e) Economic magnitude sanity check: top vs bottom quintile
# ------------------------------------------------------------------
print("\n=== (e) Top vs bottom quintile direct comparison ===", flush=True)
top = valid[valid["trend_quintile"] == "Q5"]
bot = valid[valid["trend_quintile"] == "Q1"]
u, p_u = stats.mannwhitneyu(top["fwd_ret_30d"], bot["fwd_ret_30d"], alternative="two-sided")
print(f"Q5 (n={len(top)}) mean fwd_ret_30d={top['fwd_ret_30d'].mean():.4f}, "
      f"Q1 (n={len(bot)}) mean fwd_ret_30d={bot['fwd_ret_30d'].mean():.4f}, "
      f"Mann-Whitney p={p_u:.4f}", flush=True)

# Era composition of Q5 vs Q1 -- confound check
print("\nEra composition, Q5 (strongest trend) vs Q1 (weakest trend):")
print(pd.crosstab(valid[valid.trend_quintile.isin(["Q1","Q5"])]["trend_quintile"],
                   valid[valid.trend_quintile.isin(["Q1","Q5"])]["era"], normalize="index").round(3).to_string(),
      flush=True)
