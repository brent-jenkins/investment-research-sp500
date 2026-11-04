# Seasonal Entry Timing vs. Exit-Reason Mix — Research Report (2026-08-06)

**Location of artifacts:** `research/seasonal_stopout_2026-08-06/` — `step1_descriptive.py` (script, source of truth), `step1_run.log` (full run log), `full_12month_breakdown.csv` (transparency table), `season_ab_trades.csv` (the 1,611 trades entering Nov/Dec/Jan or Jun/Jul/Aug, one row each), `within_era_check.csv`, `magnitude_check.csv`, `logit_summary.txt`.

## 0. Framing — following up the exploratory pass's strongest-ranked candidate, correcting a real methodological risk

`research/exploratory_hypothesis_pass_2026-08-05/REPORT.md` (candidate #1, ranked "strongest") observed that December-entry trades survive to the 30-day `MAX_HOLDING` cap at 66.1% vs. 45.8% for July-entry trades — a ~20pp gap — and flagged a structural rationale (Santa Claus rally/January effect vs. "sell in May"/summer thin liquidity). **That report explicitly labeled itself "not a finding"** — a read-only exploratory pass, no test run. This task's own instruction identifies the real risk directly: December and July are the two *most extreme* months out of twelve, and testing that specific pair is a genuine multiple-comparisons risk distinct from every other investigation tonight (each of which pre-specified one signal, not the best-looking pair out of twelve). **This report does not re-confirm December vs. July.** The confirmatory test below is the literature-grounded, pre-specified grouping the structural rationale actually implies: entry month ∈ {Nov, Dec, Jan} vs. entry month ∈ {Jun, Jul, Aug}.

## 1. Environment and methodology confirmation

- **Backtest convention (per CLAUDE.md's "Two backtest conventions" standing requirement): CONTINUOUS WALK** — a purely retrospective read of one already-computed, real trade population, not a variant comparison.
- **Trade population reused directly**, not re-run, from this same Claude Code session's own fresh backtest earlier tonight (`research/momentum_volume_reeval_cap_2026-08-06/all_trades_full_run.json`) — `simulate_portfolio_compounding_mtm()`, `live_universe=True`, `mark_to_market=True`, current code (run *after* `3496224`'s BRK.B/rolling-window-drift fix, unlike the exploratory pass's own source file at `concentration_risk_2026-08-05/results/full_trades_continuous.json`, which predates that fix). Not re-run again here because nothing about the engine, config, or database changed between that run and this one, in the same session.
- **Reproduction cross-checked and asserted programmatically** (`step1_descriptive.py` asserts on load): `total_trades=3161`, `MAX_HOLDING=1796`, `STOP_LOSS=1169`, `SIGNAL_OFF=131`, `CRISIS_EXIT=35`, `END_OF_DATA=30` — identical to the figures the exploratory pass reports as independently reproduced across four separate investigations tonight, and confirmed unaffected by the BRK.B fix (which changes live daily ingestion behavior, not already-stored historical `price_data`).
- **No custom candidate pool or monkeypatched `is_member()`** — this script is a pure read/transform of the real engine's own already-generated trade list (`entry_date`, `exit_date`, `exit_reason`, `pnl_pct`, all fields the real engine itself produced). The "Research scripts must verify decision-engine parity" standing requirement does not apply to trade generation here, since no custom pipeline interception was built.

## 2. Exact definitions used (code, not paraphrase)

```python
SEASON_A_MONTHS = {11, 12, 1}   # Nov-Jan: Santa Claus rally / January effect
SEASON_B_MONTHS = {6, 7, 8}     # Jun-Aug: "sell in May" aftermath / summer thin liquidity

def _era(entry_date) -> str:
    y = entry_date.year
    if y <= 2009:  return "2007-2009_GFC"
    if y <= 2019:  return "2010-2019_bull"
    return "2020-2026_recent"
```

3-way era split, matching the exploratory pass's own scheme exactly (not the unrelated 4-way split used in a separate investigation earlier tonight) — deliberately consistent with the candidate this task follows up on.

## 3. Full 12-month breakdown (transparency only — NOT the confirmatory test)

| Month | n | MAX_HOLDING% | STOP_LOSS% | Other% |
|---|---:|---:|---:|---:|
| Jan | 303 | 51.8% | 43.2% | 5.0% |
| Feb | 230 | 59.1% | 33.0% | 7.8% |
| Mar | 265 | 63.8% | 29.8% | 6.4% |
| Apr | 263 | 55.9% | 37.6% | 6.5% |
| May | 239 | 60.7% | 36.4% | 2.9% |
| Jun | 279 | **62.4%** | 33.3% | 4.3% |
| Jul | 277 | **45.8%** | 44.4% | 9.7% |
| Aug | 267 | 54.7% | 39.0% | 6.4% |
| Sep | 259 | 51.4% | 43.2% | 5.4% |
| Oct | 294 | 55.1% | 36.7% | 8.2% |
| Nov | 246 | 57.7% | 35.4% | 6.9% |
| Dec | 239 | **66.1%** | 29.3% | 4.6% |

**This table itself is exactly why the pre-specified grouping matters, not the extreme pair.** December (66.1%) and July (45.8%) are genuinely the highest and lowest months — but January (51.8%, the *lowest* value inside the "NovJan" group) and June (62.4%, one of the *highest* values, inside the "JunAug" group) both work against the hypothesized direction. A grouping built honestly from the literature-grounded window, rather than from the two best-looking months, necessarily absorbs both of these — which is exactly what Section 4 shows happening to the headline gap.

## 4. Pre-specified confirmatory test: Nov-Jan vs. Jun-Aug

n(NovJan) = 788, n(JunAug) = 823 (1,550 trades in other months excluded from this comparison, shown in Section 3 only).

**MAX_HOLDING (survival) rate: NovJan = 58.0%, JunAug = 54.3%, gap = 3.7pp** — an order of magnitude smaller than the 20pp Dec-vs-Jul gap the exploratory pass observed, confirming the multiple-comparisons risk the task flagged was real.

```
Chi-squared (season x MAX_HOLDING/STOP_LOSS/OTHER, 3-bucket): chi2=2.743  p=0.2538  dof=2
Chi-squared (season x MAX_HOLDING/STOP_LOSS only, 2x2):       chi2=1.350  p=0.2452  dof=1
Fisher's exact test (2x2, exact rather than asymptotic):      odds ratio=0.880  p=0.2281
```

**None of these reach conventional significance.** The pre-specified, honestly-constructed test does not confirm the exploratory pass's headline gap.

**Era-controlled logistic regression** (`survived_to_cap ~ season_NovJan + era_dummies`, n=1611):

```
                           coef    std err      z      P>|z|
season_NovJan            0.1747    0.102     1.719     0.086
era_2010-2019_bull       0.8290    0.156     5.328     0.000
era_2020-2026_recent     0.7082    0.161     4.402     0.000
Pseudo R² = 0.0143
```

Controlling for era, the season term's direction matches the hypothesis (positive = NovJan more likely to survive to cap) and is closer to significance than the raw chi-squared, but **still does not cross p<0.05** (p=0.086). Era itself is highly significant — most of what the naive test explains is which market regime a trade fell in, not which season.

## 5. Regime confounding check — within each of the three eras separately

| Era | n NovJan | n JunAug | MAX_HOLDING% NovJan | MAX_HOLDING% JunAug | gap | chi² p |
|---|---|---|---|---|---|---|
| 2007-2009 GFC | 120 | 101 | 35.0% | 45.5% | **−10.5pp** | 0.258 |
| 2010-2019 bull | 387 | 410 | 63.8% | 56.3% | +7.5pp | **0.026** |
| 2020-2026 recent | 281 | 312 | 59.8% | 54.5% | +5.3pp | **<0.001** |

**A genuinely mixed, and structurally different, picture from every other investigation tonight.** Here the *atypical* GFC era is the one that **reverses** the hypothesized direction (not merely fails to show it), while the two larger, more "normal" eras — together 86% of the season-eligible population — both show the hypothesized direction, one of them strongly. This is a materially better regime-robustness profile than either rejected sibling investigation (both of which found their effect concentrated *only* in the small, atypical GFC window). It is also, on its own, not sufficient to overturn Section 4's result: **the pre-specified confirmatory test is the pooled test across all eras, and that test is the one that must clear the gate** — a pattern that only shows up once eras are sliced apart, after the fact, is a supplementary observation, not confirmation of the pre-registered hypothesis. Stated as a supplementary check, not used to override Section 4: pooling just the two non-GFC eras (an *ad hoc* cut decided only after seeing the GFC reversal, explicitly **not** part of the pre-specified test) narrows the gap to 6.6pp with chi² p=0.088 — still short of 0.05.

## 6. Composition-not-magnitude check

| Exit type | NovJan mean pnl% (n) | JunAug mean pnl% (n) | Mann-Whitney p |
|---|---|---|---|
| MAX_HOLDING | +7.73% (457) | +6.58% (447) | 0.203 |
| STOP_LOSS | −6.57% (288) | −6.72% (320) | 0.590 |

Confirmed, as the exploratory pass found: whatever seasonal signal might exist here is specifically in *which* exit fires, not in the magnitude of P&L once it does — mean P&L within each exit type is statistically indistinguishable by season (both p > 0.05, comfortably).

## 7. Step 1 gate decision: NOT WARRANTED — no Step 2 run

**This is the closest call of any investigation tonight — not a clean rejection like the momentum-strength/volume-at-cap signals tested in a companion investigation earlier this session, but still short of the bar.** Applying the same significance standard this project has held to consistently (both rejected sibling investigations were held to p<0.05 on their primary test, regardless of how suggestive a secondary metric looked):

1. **The pre-specified confirmatory test — the actual test this task designates, not the full 12-month table — is not statistically significant**: chi-squared p=0.245–0.254, Fisher's exact p=0.228. The 20pp headline gap the exploratory pass observed shrinks to 3.7pp once decembers's and July's neighbor months (January, June) are honestly included per the literature-grounded window — direct, quantified confirmation that the multiple-comparisons risk this task flagged was real, not merely a theoretical concern.
2. **The era-controlled logistic regression gets closer (p=0.086) but does not cross p<0.05.** Directionally consistent with the hypothesis, but "directionally consistent and marginal" was explicitly not enough to save `dynamic_momentum_cap`'s aggregate finding earlier tonight (which was more strongly significant in aggregate, p<0.001, before regime decomposition undermined it) — the same standard applies here in reverse: a marginal aggregate result is not promoted to a finding by a favorable regime breakdown found after the fact.
3. **Regime robustness is genuinely better than either rejected sibling finding** (positive direction in 2 of 3 eras covering 86% of the population, one era strongly significant) — this is recorded honestly as the most encouraging aspect of this candidate, and as the reason this call is closer than the other two. But per the task's own explicit instruction, the pooled pre-specified test is the confirmatory one, and it does not clear its own gate.

**Because Step 1 does not clear its own gate, Step 2 (a specific seasonal stop-distance mechanism, block-validated across 16 windows) was not built or run.**

## 8. Step 2 prerequisite question, answered regardless of gate outcome (per task's explicit instruction)

**Does stop distance feed into VaR sizing anywhere? Traced directly, not assumed: no.**

`_resolve_entry_stop()` and `_var_position_size_core()` (both in `analysis/volatility.py`, the single shared module both live and backtest call) are invoked sequentially at every entry point in `analysis/backtest.py` (lines 298/303, 655/661, 953/962, 1344/1353) and `brokers/trading212.py` (lines 3851/3883) — but **independently**, with no data flow between them:

```python
stop, stop_distance_pct = _resolve_entry_stop(direction, price, atr, config)   # ATR-based only
...
position_gbp, _dv, _hv = _var_position_size_core(
    log_returns, starting_capital, max_position_gbp, max_hold,   # max_hold = CONFIGURED cap (e.g. 30), not stop_distance_pct
    var_confidence, max_risk_pct, min_position_gbp,
)
```

`_var_position_size_core()`'s full parameter list is `log_returns, account_value_gbp, max_position_gbp, holding_days, var_confidence, max_risk_pct, min_position_gbp` — `stop_distance_pct` never appears in it. The `holding_days` argument passed at entry time is the **configured** `stock_max_holding_days` (e.g. 30), used only to scale VaR via the square-root-of-time rule — not the ATR-derived stop distance. **A seasonal stop-distance adjustment, had Step 1 warranted one, would therefore be an isolated change to `_resolve_entry_stop()`'s stop placement only — it would not need to touch, and would not automatically propagate into, position sizing.** This confirms the isolation Step 2's design would have needed to assume, had it run.

## 9. Recommendation

**Researched, not adopted.** The pre-specified, literature-grounded seasonal grouping (Nov-Jan vs. Jun-Aug) does not show a statistically significant difference in exit-reason mix in the one test this task designates as confirmatory (chi-squared p≈0.25, Fisher's exact p≈0.23), and the era-controlled logistic regression, while directionally consistent and closer (p=0.086), does not cross the conventional significance threshold either. `config.yaml`, `analysis/decision.py`, `analysis/backtest.py`, and `brokers/trading212.py` are all unchanged. No engine modification, no Step 2 mechanism build, and no 16-block validation were performed, consistent with the task's own explicit gating instruction.

**Caveat, stated plainly, and more so than usual given how close this call is:** this result does not prove there is no genuine seasonal effect on stop-out rate — it shows that *this specific, honestly pre-specified test, on this trade sample* does not clear a conventional significance bar. The regime breakdown (Section 5) is the most encouraging evidence for the underlying idea of anything tested tonight, and a larger sample (more years of data, as they accumulate) or a marginally different pre-specified window could plausibly cross the threshold where this one does not. That is exactly why it is reported as "researched, not adopted" rather than "no effect" — the honest distinction this project's culture requires between "we tested this specific, rigorous formulation and it didn't clear the bar" and "this idea is definitively wrong."

## 10. Confirmation

- No production files were modified: `git status --short` shows only this task's new `research/seasonal_stopout_2026-08-06/` directory, plus pre-existing, unrelated, already-completed sibling research directories' untracked files (`momentum_sizing_multiplier_2026-08-05/`, `exploratory_hypothesis_pass_2026-08-05/`, `momentum_volume_reeval_cap_2026-08-06/` — all present or created by separate, already-reported tasks this session, none modified by this one).
- `config.yaml` has zero diff against the working tree.
- No deploy/CI infrastructure was touched (`deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py`, `CLAUDE.md`, `CLAUDE_HISTORY.md` — none read for editing, none written to).
- No production trading-decision code was modified — `analysis/decision.py`, `analysis/backtest.py`, `brokers/trading212.py`, `analysis/volatility.py` were read-only, read/traced for the Section 8 question, never edited.
- Nothing was committed and nothing was pushed — `git status` shows no staged changes.
- No production database was touched — this investigation used only an already-materialized trade-list JSON from earlier in this same session; no new database query was made.
- No `git worktree` was created — worked directly in the main checkout, consistent with the rest of tonight's session.
