# Dynamic Momentum-Trend Holding-Period Cap — Research Report (2026-08-05)

**Location of artifacts:** `research/dynamic_momentum_cap_2026-08-05/` — `step1_descriptive.py` (main script, source of truth), `step1_run.log` (full run log), `step1b_robustness.py` + `step1b_run.log` (follow-up robustness checks), `max_holding_trend_annotated.csv` (all 1,796 `MAX_HOLDING` trades, one row each, every field described below), `trend_class_summary.csv`, `trend_quintile_summary.csv`, `era_distribution_counts.csv`/`era_distribution_pct.csv`, `ols_summary.txt`, `ols_joint_trend_vs_level.txt`, `step1_stats_summary.json`.

## 1. Environment and methodology confirmation

- Local DB (`database/investment_research.db`) confirmed current before running anything: `price_data` spans 2007-01-01 → 2026-08-03 across 540 tickers (2,454,903 rows). `database/` confirmed gitignored (`.gitignore:66: database/`, `.gitignore:35: database/*.db`). `git status --short` at task start showed only `?? database/` and `?? research/momentum_horizon_2026-08-05/` (the sibling investigation's own committed-since-then output, already merged at `7261e54`) — no local modifications to any tracked production file.
- Checked for concurrent-session evidence: a second worktree (`C:/GitHub/sp500invest-deploy-systemd-parity`, branch `fix/deploy-systemd-parity`) exists but is clean and on an already-merged commit (`d4ca08f`, merged into `main` at `145d052`) — not an active concurrent session. Worked directly in the main checkout, per CLAUDE.md's standing practice (same pattern all three sibling investigations tonight used).
- **Backtest convention (stated explicitly, per CLAUDE.md's "Two backtest conventions" standing requirement): Step 1 is a CONTINUOUS WALK.** Not a variant comparison — purely "pull one real, already-fidelity-established trade set and look at what happened next."
- **Trade source: reused directly** from the sibling momentum-strength investigation's own real run (`research/momentum_horizon_2026-08-05/all_trades_full_run.json`) rather than re-run from scratch. Same engine (`simulate_portfolio_compounding_mtm` via `run_full_validation(config, mark_to_market=True, live_universe=True)`), same `config.yaml`, same 2007-01-01→2026-08-03 window, same code (confirmed via the clean `git status` above — no production file changed since that commit). Reproduction verified programmatically before computing anything new (`step1_descriptive.py` asserts on load): `total_trades=3161`, `MAX_HOLDING=1796` (56.8%) — **matches the task brief's ~3,161-trade/56.8% baseline exactly.**

Exit-reason breakdown (reproduced): MAX_HOLDING 1796 (56.8%), STOP_LOSS 1169, SIGNAL_OFF 131, CRISIS_EXIT 35, END_OF_DATA 30.

## 2. Exact definitions used (code, not paraphrase)

**Momentum-trend-since-entry** — the one signal this investigation tests, per the task's deliberate narrowing:

```python
# momentum series: analysis/signals.py:compute_momentum_series() — unmodified,
# close / close.shift(252) - 1.0, read directly from precompute_ticker() frames.

trend_since_entry(t) = momentum[t] - momentum[entry_date]

strengthening_at_cap = momentum[exit_date] - momentum[entry_date] > 0
decaying_at_cap      = momentum[exit_date] - momentum[entry_date] <= 0
```

This is deliberately **not** the sibling report's `momentum_decaying_at_exit` (which compares momentum at exit to momentum 20 trading days *before* exit — a recent-slope measure). The task's own phrasing ("strengthening or weakening **since entry**") calls for a cumulative-since-entry measure instead, so a new, distinct definition was built and is stated here as literal code.

**Population:** all 1,796 real `MAX_HOLDING` trades — these are exactly the trades a dynamic-cap mechanism could change the outcome of; every other exit reason (`STOP_LOSS`/`SIGNAL_OFF`/`CRISIS_EXIT`/`END_OF_DATA`) already fired before any cap-extension logic would get a chance to run. 0 trades skipped (all 1796 had usable frame/momentum data at both entry and exit).

**Post-cap measures** (purely retrospective, from real stored `price_data`): `fwd_ret_{10,20,30}d = close[exit_loc+N]/exit_price - 1`; `era` = coarse regime bucket (`2007-2009_GFC` / `2010-2019_bull` / `2020_covid` / `2021-2026_recent`, by entry-date year) — a finer 4-way split than the sibling's binary `era_pre2010`, chosen because this task's population spans meaningfully distinct regimes beyond just "pre/post-2010."

**Fitness-for-recalculated-daily-cadence check** (validation of the one signal, not a second candidate): for every trade, `trend_since_entry(t)` is walked across the full daily hold, recording `n_sign_flips_during_hold` (how often the sign changes across the whole hold) and `pct_last_5d_agree_with_cap_classification` (whether the classification is stable in the days immediately preceding the cap, vs. a single noisy snapshot).

## 3. Step 1 results — trend-class summary

| Class | n | % of MAX_HOLDING | mean trend_since_entry | mean pnl% at cap | mean fwd_ret_10d | mean fwd_ret_20d | mean fwd_ret_30d | median fwd_ret_30d | % fwd_30d > 0 |
|---|---|---|---|---|---|---|---|---|---|
| Strengthening | 988 | 55.0% | +33.4% | +11.22% | 0.98% | 1.60% | **2.64%** | 1.77% | 56.5% |
| Decaying | 808 | 45.0% | −24.8% | +3.82% | 0.47% | 1.02% | **1.78%** | 1.52% | 53.6% |

At face value: Strengthening beats Decaying by ~0.86pp mean forward return over 30 days. This is the level at which the sibling report's Weak-band finding also looked promising before deeper checks. The same deeper checks are applied here.

## 4. Regime confounding check — explicit, per this project's standard

| Class | n | 2007-09 GFC | 2010-19 bull | 2020 covid | 2021-26 recent |
|---|---|---|---|---|---|
| Decaying | 808 | 53 (6.6%) | 429 (53.1%) | 57 (7.1%) | 269 (33.3%) |
| Strengthening | 988 | 105 (10.6%) | 515 (52.1%) | 53 (5.4%) | 315 (31.9%) |

Unlike the sibling report's 82.4%-vs-4.5% regime skew, this split is **not** grossly imbalanced at face value — both classes are majority 2010–2019-bull, and neither class over-concentrates in one regime by more than ~4pp. This looked, initially, like a case where regime confounding is *not* the dominant issue. Section 6's within-era breakdown shows this initial read was incomplete — regime concentration by count is not the only way an effect can be regime-driven, and the within-era decomposition is what actually surfaces the problem.

## 5. Statistical tests

```
Spearman(trend_since_entry, fwd_ret_10d): rho=0.0578, p=0.0143, n=1796
Spearman(trend_since_entry, fwd_ret_20d): rho=0.0378, p=0.1102, n=1788
Spearman(trend_since_entry, fwd_ret_30d): rho=0.0552, p=0.0199, n=1782

Mann-Whitney U (Strengthening vs Decaying, fwd_ret_30d):
  U=407333.0, p=0.1703, n_strengthening=985, n_decaying=797   -- NOT significant

OLS: fwd_ret_30d = -0.0078 + 0.0242*trend_since_entry + era dummies
     trend_since_entry: coef=0.0242, p=1.64e-08 (HC3-robust p<0.001 too)
     R² = 0.023
```

**Already a mixed signal at this point**: the continuous relationship (Spearman rho≈0.055, OLS p<0.001 even era-controlled) is statistically significant, but the binary group comparison that actually mirrors "would a dynamic cap have picked a materially different outcome" (Mann-Whitney) is not (p=0.17). This discrepancy is exactly why the follow-up robustness checks (Step 1b) were run before treating this as a real finding, matching the "check for regime confounding explicitly, don't stop at one comparison" bar the sibling reports set.

### 5a. Robustness follow-up (`step1b_robustness.py`)

**Quintile gradient — not monotonic:**

| Quintile | n | mean trend | mean fwd_ret_30d | % positive |
|---|---|---|---|---|
| Q1 (weakest) | 357 | −0.444 | 1.46% | 52.4% |
| Q2 | 356 | −0.088 | 2.09% | 54.5% |
| Q3 | 356 | +0.025 | **1.51%** | 55.1% |
| Q4 | 356 | +0.143 | 3.15% | 61.5% |
| Q5 (strongest) | 357 | +0.752 | 3.06% | 54.6% |

Q3 dips *below* Q1 and Q2 — the exact "bounces instead of rising" pattern the sibling report flagged as a red flag for its own Weak-band finding. Q5 vs Q1 direct comparison: mean 3.06% vs 1.46%, but **Mann-Whitney p=0.2222 — not significant.**

**Outlier sensitivity:**
```
Winsorized (clip at 1st/99th pct): rho=0.0551, p=0.0201, n=1782   -- still "significant"
Trimmed (DROP top/bottom 1% entirely): rho=0.0382, p=0.1107, n=1746  -- NOT significant
```
Simply clipping extreme values leaves the result intact, but genuinely *removing* the most extreme ~36 trades kills the significance. This is the same fragility shape the sibling report's Weak-band finding showed (driven by 2 outlier trades in 17) — here diluted across a much larger sample, but present.

**Trend vs. level — trend does carry distinct information (a positive check):**
```
Spearman(momentum_at_cap, trend_since_entry): rho=0.3121, p<0.0001   (correlated by construction)
Spearman(momentum_at_cap, fwd_ret_30d) [level alone]: rho=0.0224, p=0.3449  -- NOT significant
Joint OLS: trend_since_entry coef=0.0248, p<0.001; momentum_at_cap coef=-0.0004, p=0.889
```
Momentum *level* at the cap point has no predictive power on its own — trend is doing the actual work in the OLS, not serving as a level proxy. This part of the hypothesis holds up.

**Within-era decomposition — the decisive check:**

| Era | n | Spearman rho | p | mean fwd30 Strengthening | mean fwd30 Decaying |
|---|---|---|---|---|---|
| 2007-2009 GFC | 158 | **0.2705** | **0.0006** | 1.42% (n=105) | −3.95% (n=53) |
| 2010-2019 bull | 944 | **0.0185** | **0.5694** | 1.96% (n=515) | 1.89% (n=429) |
| 2020 covid | 110 | −0.0242 | 0.8022 | 5.07% (n=53) | 2.25% (n=57) |
| 2021-2026 recent | 570 | 0.0859 | 0.0404 | 3.74% (n=312) | 2.68% (n=258) |

**This is the finding that decides the gate.** The effect is real and strong *only* in the 2007-2009 GFC era (rho=0.27, p<0.001, n=158, 8.8% of the population) and weakly marginal in the most recent era. In the **dominant era — 2010-2019 bull market, 944 trades, 52.6% of the entire MAX_HOLDING population** — there is essentially **zero relationship** (rho=0.0185, p=0.57): Strengthening and Decaying trades produced statistically indistinguishable 30-day forward returns (1.96% vs 1.89%). The aggregate era-controlled OLS coefficient being significant does not mean the effect is universal — a dummy-variable control shifts the *intercept* per era, it does not verify the *slope* is consistent across eras, and here it plainly is not. The population-wide correlation is being pulled almost entirely by one small, atypical crisis-recovery regime, exactly the "naive co-occurrence looks like a real effect but is actually a regime artifact" trap CLAUDE.md's culture is built to catch — the same shape as the sibling investigation's Weak-band finding, just surfaced one layer deeper (via within-era slope decomposition rather than raw era-count concentration).

## 6. Fitness-for-recalculated-daily-cadence diagnostic

```
n_sign_flips_during_hold: mean=2.37, median=2, 25th pct=1, 75th pct=4, max=11
  (370/1796 = 20.6% of trades never flip sign across the whole hold)
pct_last_5_trading_days_agreeing_with_cap-day_classification: mean=90.4%, median=100.0%
  (77.5% of trades have full 5/5-day agreement in the window right before cap)
```

The trend measure is genuinely noisy earlier in the hold (median 2 sign flips over ~30 days is not a clean, one-directional trend) but stabilizes materially by the time it nears the actual 30-day cap boundary (90.4% mean/100% median agreement over the last 5 days). **Conclusion: this signal is reasonably fit for evaluation right at the existing cap boundary, but would need an explicit smoothing/hysteresis rule (not built or tested here, out of scope per the task's narrowing) if a mechanism tried to act on it materially earlier in the hold** — a caveat worth recording even though it did not end up mattering, since the Step 1 gate failed on a different, more fundamental basis (Section 5a) before this diagnostic's implication needed to be acted on.

## 7. Step 1 gate decision: NOT WARRANTED — no Step 2 run

Per the task's own explicit gating rule, stated honestly per this project's "no meaningful difference found is a fully valid outcome" culture:

**Step 1 does not show a real, promising, reasonably-sized pattern**, specifically:

1. The binary group comparison that most directly answers "would extending the cap for Strengthening-trend positions have helped" is **not statistically significant** (Mann-Whitney p=0.17 for the trend-class split, p=0.22 for the top-vs-bottom quintile comparison) despite a nominally significant continuous OLS/Spearman relationship — the two tests disagree, and the discrepancy itself is a warning sign, not a wash that favors adoption.
2. The quintile gradient is **not monotonic** (Q3 mean return dips below Q1 and Q2) — the same non-monotonicity red flag the sibling report used to reject its own aggregate finding.
3. The apparent significance is **not robust to removing (not merely clipping) the most extreme ~2% of trades** — Spearman rho drops from 0.055 (p=0.02) to 0.038 (p=0.11, not significant) once the top/bottom 1% of `trend_since_entry` values are dropped entirely, indicating the aggregate result leans on a relatively small number of extreme trades, structurally similar to the sibling report's own outlier-driven Weak-band collapse.
4. **The effect is essentially absent (rho=0.019, p=0.57) in the single largest era subgroup — 2010-2019 bull market, 944 trades, 52.6% of the whole MAX_HOLDING population** — and is concentrated almost entirely in the small 2007-2009 GFC era (158 trades, 8.8%), with only a weak, marginal echo in the most recent era. An era-dummy-controlled aggregate regression does not detect this because it only shifts the intercept per era, not the slope — the within-era decomposition is what actually exposes it.
5. R² of the era-controlled OLS is 0.023 — trend_since_entry explains roughly 2% of the variance in what happens after the cap, even where the relationship is nominally significant.

The one genuinely positive sub-finding — trend carries real, distinct information beyond momentum *level* at the cap point (Section 5a) — does not rescue the overall gate decision, since the trend signal itself fails the monotonicity, outlier-robustness, and within-era-consistency checks independently of whether it beats a level proxy.

**Because Step 1 does not clear its own gate, Step 2 was not built or run.** No engine modification was made, no reset-at-boundary sweep was run, no 16-block validation was performed. This is consistent with, not a shortcut around, the task's own instruction: "Do not build a backtest engine modification or run block validation on a hypothesis Step 1 itself doesn't support."

## 8. The sizing-coupling design question — stated explicitly per the task brief

Even though Step 2 was never exercised, the task requires this decision stated explicitly regardless of outcome: **had Step 2 been warranted, the default scope would have been sizing fixed at entry — only exit timing adjusts, VaR sizing is not re-run mid-trade.** Reasoning: the VaR sizing investigation already found `max_hold` directly drives `holding_var = daily_var * sqrt(holding_days)` in `analysis/volatility.py:_var_position_size_core()`; simultaneously re-sizing a position *and* extending its cap would confound whatever effect the cap change alone produces with a second, independent lever (sizing), making any Step 2 result impossible to attribute cleanly. This decision was never actually exercised in code, since Step 1 did not clear its gate.

## 9. Recommendation

**Researched, not adopted.** A dynamically-adjusted holding-period cap driven by the position's own momentum trend since entry does not show a real, robust, population-wide effect on post-cap continuation. What signal exists in the aggregate statistics is concentrated in a single small, atypical regime (the 2007-2009 GFC recovery), is not robust to reasonable outlier trimming, does not survive as a monotonic gradient, and is undetectable in the dominant 2010-2019 regime that produced over half of all `MAX_HOLDING` trades ever generated by this system. The existing fixed 30-day `stock_max_holding_days` cap, independent of any momentum-trend adjustment, remains the best-supported choice given this evidence. `config.yaml`, `analysis/decision.py`, `analysis/backtest.py`, and `brokers/trading212.py` are all unchanged.

**Caveat, stated plainly:** this only tests the one specific "cumulative momentum change since entry" trend definition the task specified (not the sibling script's own 20-day-recency `momentum_decaying_at_exit` measure, not any smoothed/regression-slope variant, not ADX/VIX/RSI or any combination — all deliberately out of scope per the task's own narrowing). It does not rule out every conceivable dynamic-cap framing, only this one.

## 10. Confirmation

- No production files were modified: `git status --short` shows only the new `research/dynamic_momentum_cap_2026-08-05/` directory, the pre-existing untracked `database/` directory, and the pre-existing `research/momentum_horizon_2026-08-05/` sibling directory — none created or altered by this task beyond reading `all_trades_full_run.json`.
- `config.yaml` has zero diff against the working tree.
- No deploy/CI infrastructure was touched (`deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py`, `CLAUDE.md`, `CLAUDE_HISTORY.md` — none read for editing, none written to).
- No production trading-decision code was modified (`analysis/decision.py`, `analysis/backtest.py`, `brokers/trading212.py` — all read-only, imported and called exactly as they exist, never patched or monkeypatched; no custom candidate pool or `is_member()` override was built, so the "Research scripts must verify decision-engine parity" standing requirement does not additionally apply here — Step 1 used the real, unmodified `run_full_validation()` output directly, and its own supplementary momentum-trend computation is a purely retrospective, display-only reconstruction with no bearing on any trading decision, live or backtested).
- Nothing was committed and nothing was pushed — `git status` shows no staged changes.
- No production database was touched — `database/investment_research.db` was read-only for every query in this investigation.
- No `git worktree` was created — worked directly in the main checkout, since no concurrent-session evidence was found (see Section 1).
