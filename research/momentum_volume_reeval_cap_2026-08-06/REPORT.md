# Momentum + Relative-Volume Re-Evaluation at the 30-Day Cap — Research Report (2026-08-06)

**Location of artifacts:** `research/momentum_volume_reeval_cap_2026-08-06/` — `step1_descriptive.py` (script, source of truth), `step1_run.log` (full run log), `all_trades_full_run.json` (all 3,161 trades from a fresh real backtest run, full trade-level output preserved per this project's reproducibility standard), `max_holding_trades_annotated.csv` (all 1,796 `MAX_HOLDING` trades, one row each), `momentum_at_cap_band_summary.csv`, `relvolume_at_cap_band_summary.csv`, `group_comparison_results.csv`, `spearman_results.csv`, `era_distribution.csv`, `within_era_dominant_regime.csv`, `outlier_robustness.csv`.

## 0. Relation to the two rejected sibling investigations tonight (stated plainly, per task instruction)

This is a **third, distinct** attempt at the same hard problem, not a retest of either prior one:

- **momentum_horizon_2026-08-05** tested entry-time momentum-strength band → post-cap continuation. **Rejected**: no monotonic gradient, near-zero Spearman rho in aggregate (0.0165) and within the dominant Very Strong band alone (0.0279).
- **dynamic_momentum_cap_2026-08-05** tested momentum trend *since entry* → post-cap continuation. **Rejected**: real only in the small 2007-2009 GFC window (8.8% of population), zero effect in the dominant 2010-2019 regime (52.6% of population), not robust to outlier trimming.
- **This investigation** tests two different things: momentum-strength band re-read **at the cap point itself** (not at entry, not a trend), and a **structurally distinct, new signal — relative trading volume at the cap point** — separately and in combination. The real, well-evidenced phenomenon motivating all three investigations (91.3% of Very-Strong-band `MAX_HOLDING` trades hit a new high within 30 days post-exit, per momentum_horizon) is not in question. What's tested here, again, is whether a *specific, available-at-decision-time* signal can pick out in advance which positions will do this — a different mechanism attempt than either prior one, reported on its own terms below.

## 1. Environment and methodology confirmation

- Local DB (`database/investment_research.db`) confirmed current: `price_data` spans 2007-01-01 → 2026-08-03 across 540 tickers (2,454,903 rows), `^GSPC` spans 2007-01-03 → 2026-08-03, `universe_membership` has 1,257 rows, `volume` column confirmed present in `price_data` schema (never previously used in any live decision or research investigation this session).
- Checked `git status --short` before starting: only pre-existing untracked files from the already-completed, unrelated `momentum_sizing_multiplier_2026-08-05` investigation (finished ~23:47, well before this task began) and this investigation's own new directory. `git worktree list` showed one other worktree (`fix/deploy-systemd-parity`), already merged and clean — not an active concurrent session. Worked directly in the main checkout, consistent with both sibling investigations' own practice.
- **The backtest was re-run FRESH here, deliberately not reused from either sibling investigation's saved JSON**, because production code changed after both sibling runs: `3496224` ("Fix silent BRK.B/BF.B ticker bug and rolling-window start-date drift") was committed 2026-08-05 19:31, after both siblings' runs (12:xx and 14:xx respectively). Reusing stale trade output here would risk silently re-testing against a bug that has since been fixed — exactly what this project's "verify the local environment is current, don't assume it" standing practice exists to prevent.
- **Reproduction confirms the fix didn't change the historical trade set**: fresh run produced `total_trades=3161`, `MAX_HOLDING=1796` (56.8%) — identical to both sibling investigations' baseline, as expected since the BRK.B fix affects live daily ingestion/incremental-download behavior, not the already-stored historical `price_data` this backtest reads.
- **Backtest convention (per CLAUDE.md's "Two backtest conventions" standing requirement): Step 1 is a CONTINUOUS WALK** — not a variant comparison, purely "pull one real trade set and look at what happened next," identical framing to both sibling investigations.
- **Engine: `analysis.backtest.simulate_portfolio_compounding_mtm()`**, invoked via `analysis.validation.run_full_validation(CONFIG, start=2007-01-01, end=2026-08-03, mark_to_market=True, live_universe=True)` — unmodified, the real engine, no custom harness for trade generation.
- **No custom candidate pool or monkeypatched `is_member()` was used** — trade generation used the real, unmodified engine directly, so the "Research scripts must verify decision-engine parity" standing requirement does not additionally apply to trade generation. The momentum/volume-band reconstruction is a purely retrospective, display-only labeling exercise (identical in kind to both sibling investigations' own reconstructions) with no bearing on any live or backtested trading decision.

Exit-reason breakdown (fresh run, current code): MAX_HOLDING 1796 (56.8%), STOP_LOSS 1169, SIGNAL_OFF 131, CRISIS_EXIT 35, END_OF_DATA 30.

**Why every `MAX_HOLDING` trade is still signal-on (and not necessarily above the entry momentum floor) at its own exit date:** exit priority is `CRISIS_EXIT > STOP_LOSS > SIGNAL_OFF > MAX_HOLDING` (`analysis/decision.py:evaluate_exit_reason()`, checked in that order every day of the hold). If signal had turned off by day 30, the position would already have exited via `SIGNAL_OFF` before `MAX_HOLDING` could fire. So every trade in this population is, by construction, still signal-on and not crisis-blocked at exit — but the entry-time `min_momentum_pct` floor (2%) is an entry gate only, never re-applied during the hold, so momentum can (and does) drift below it without forcing an exit.

## 2. Exact definitions used (code, not paraphrase)

**Momentum-strength bands — EXACT reuse of `main.py`'s own functions**, applied at each trade's **exit date** (the cap point) instead of its entry date:

```python
def _bands(values: pd.Series) -> dict:
    vals = values.dropna()
    if len(vals) < 4:
        return {}
    return {"q25": vals.quantile(0.25), "q75": vals.quantile(0.75), "q90": vals.quantile(0.90)}

def _label(val, bands: dict) -> str:
    if not bands or pd.isna(val):
        return "-"
    if val >= bands["q90"]:  return "Very Strong"
    if val >= bands["q75"]:  return "Strong"
    if val >= bands["q25"]:  return "Moderate"
    return "Weak"
```

**"Tighter bar" = elevated ⟺ band is `Strong` or `Very Strong`**, exactly per the task's own instruction, reusing the existing definitions rather than a new threshold.

**Relative volume — new signal, not used anywhere in production**, structurally distinct from momentum (participation/liquidity, not price direction):

```python
avg_vol_20(t) = mean(volume[t-20 .. t-1])   # trailing 20 trading days, EXCLUDING day t itself
rel_volume(t) = volume[t] / avg_vol_20(t)
```

Banded with the **identical** q25/q75/q90 percentile-split logic above, computed against the same day's eligible pool's `rel_volume` distribution — so "Strong"/"Very Strong" means the same thing (top quartile/decile of that day's eligible pool) for both signals, making them directly comparable.

**Eligible candidate pool, per date** (the population bands are computed against): identical definition to both sibling investigations — every live candidate ticker whose row on that date passes `passes_entry_gate()` (signal on AND genuine index member AND not crisis-blocked) AND `passes_momentum_floor()` against the real, production `min_momentum_pct` floor (0.02). Re-entry cooldown is not applied to the pool definition (production `reentry_cooldown_days=0`, a no-op) — stated as a scope decision, same as both siblings.

**Post-cap measure:** `fwd_ret_30d = close[exit_loc+30]/exit_price - 1`, using each ticker's own trading-day index, from real stored `price_data` — purely retrospective.

**Era buckets** (finer than the sibling reports' binary pre/post-2010 split, matching `dynamic_momentum_cap`'s 4-way scheme): `2007-2009_GFC`, `2010-2019_bull`, `2020_covid`, `2021-2026_recent`.

## 3. Step 1 results — band summaries

**Momentum-at-cap** (n=1796):

| Band | n | % of MAX_HOLDING | mean fwd_ret_30d | median fwd_ret_30d | % fwd_30d > 0 |
|---|---|---|---|---|---|
| Weak | 13 | 0.7% | +4.51% | **−1.68%** | 46.2% |
| Moderate | 86 | 4.8% | +0.54% | +1.04% | 53.5% |
| Strong | 194 | 10.8% | +0.74% | +1.30% | 53.6% |
| Very Strong | 1500 | 83.5% | +2.54% | +1.70% | 55.6% |

**Rel-volume-at-cap** (n=1796):

| Band | n | % of MAX_HOLDING | mean fwd_ret_30d | median fwd_ret_30d | % fwd_30d > 0 |
|---|---|---|---|---|---|
| Weak | 482 | 26.8% | +1.91% | +1.64% | 53.5% |
| Moderate | 861 | 47.9% | +2.71% | +2.01% | 57.0% |
| Strong | 253 | 14.1% | +1.45% | +0.62% | 53.8% |
| Very Strong | 197 | 11.0% | +2.21% | +1.09% | 53.3% |

**First observation, momentum-at-cap:** the mean/median mismatch in the Weak band (+4.51% mean vs. **−1.68%** median, n=13, sign-flipped) is the same small-sample-outlier signature both sibling reports flagged — confirmed directly in Section 4 below (2 trades: URI +39.96%, AMD +22.97%, out of 13, swing the mean well above the median). Moderate→Strong→Very Strong shows an apparent rise (0.54%→0.74%→2.54%), but Section 5's continuous check shows this does not hold up as a real dose-response relationship once the coarse quartile grouping is set aside.

**First observation, rel-volume-at-cap:** no usable gradient at all — Moderate (the second-lowest band) has the *highest* mean (2.71%), Strong has the *lowest* (1.45%), Very Strong sits in between (2.21%). This is not even superficially monotonic before any regime check.

**A structural fact worth stating plainly:** momentum-at-cap is Strong/Very Strong for **94.3%** of all `MAX_HOLDING` trades — a re-check gate at this bar would only ever *exclude* 5.7% of trades from extension eligibility. This is a direct, mechanical echo of `momentum_horizon`'s own finding that Very Strong is 87.6% of *entry-time* bands, now even more concentrated at the cap point, for the same reason: momentum-ranked, capacity-constrained admission structurally selects for positions that are still strong-momentum by the time they've survived 30 days without being stopped out or signal-flipped. Rel-volume-at-cap is much less concentrated (elevated = Strong/Very Strong = 25.1% of trades) since volume distribution is not subject to the same selection pressure.

## 4. Named-trade-level evidence — the Weak momentum-at-cap band in full (n=13)

| Ticker | Entry | Exit | Era | Momentum at exit | pnl% at cap | fwd_ret_30d |
|---|---|---|---|---|---|---|
| GIS | 2008-12-10 | 2009-01-09 | GFC | 1.38% | −4.64% | −4.52% |
| GIS | 2009-01-12 | 2009-02-11 | GFC | 3.99% | −4.26% | −14.17% |
| DGX | 2009-03-25 | 2009-04-24 | GFC | 3.10% | +7.40% | +2.05% |
| IRM | 2009-06-30 | 2009-07-30 | GFC | 4.74% | +1.46% | −2.54% |
| JPM | 2009-08-12 | 2009-09-11 | GFC | 2.04% | +0.69% | +6.42% |
| HRL | 2009-08-17 | 2009-09-16 | GFC | 0.74% | +0.22% | −1.68% |
| CF | 2011-11-30 | 2011-12-30 | 2010-19 bull | 7.27% | +3.71% | +22.71% |
| MU | 2015-04-06 | 2015-05-06 | 2010-19 bull | 4.91% | +2.04% | −12.58% |
| SWKS | 2015-11-19 | 2015-12-21 | 2010-19 bull | 7.87% | +0.54% | −15.84% |
| URI | 2016-09-23 | 2016-10-24 | 2010-19 bull | 3.53% | +1.35% | +39.96% |
| AMD | 2018-05-23 | 2018-06-22 | 2010-19 bull | 9.87% | +20.61% | +22.97% |
| TPR | 2020-11-18 | 2020-12-18 | covid | 9.36% | +3.15% | +19.89% |
| DLTR | 2022-10-24 | 2022-11-23 | recent | 3.75% | +0.77% | −4.11% |

6 of 13 (46%) fall inside the same GFC-era window both sibling reports flagged, but here the dominant-era trades (2010-2019, 5 of 13) show no clearer pattern — three large winners (CF, URI, AMD) and two large losers (MU, SWKS) of comparable magnitude, essentially a coin flip. The band's headline +4.51% mean is not attributable to any one era's confound the way the sibling report's Weak-band finding was; it is simply a 13-trade sample with high variance in both directions.

## 5. Statistical checks — continuous relationship, aggregate and dominant-regime

```
Spearman(momentum_at_exit, fwd_ret_30d), ALL:             rho=0.0224, p=0.3449, n=1782
Spearman(momentum_at_exit, fwd_ret_30d), 2010-2019 ONLY:   rho=-0.0310, p=0.3420, n=944
Spearman(momentum_at_exit, fwd_ret_30d), Very Strong band ONLY:            rho=0.0081, p=0.7546, n=1486
Spearman(momentum_at_exit, fwd_ret_30d), Very Strong band AND 2010-2019:   rho=-0.0357, p=0.3039, n=829

Spearman(rel_volume_at_exit, fwd_ret_30d), ALL:            rho=-0.0171, p=0.4710, n=1782
Spearman(rel_volume_at_exit, fwd_ret_30d), 2010-2019 ONLY: rho=-0.0395, p=0.2257, n=944
```

**Neither signal shows a statistically distinguishable relationship anywhere it is checked** — not in the full population, not within the single dominant regime (2010-2019, 52.6% of the population), and, for momentum, not even within its own dominant Very Strong band alone (n=1486, rho≈0.008, p=0.75 — a large enough sample that a real, even modest effect would show up). Both signals' point estimates **flip to negative** within the dominant regime, the opposite direction from what a "confirms continuation" mechanism would need.

## 6. Group comparisons — the actual proposed gate ("Strong"/"Very Strong" to extend)

| Signal | n elevated | n not elevated | mean fwd30 elevated | mean fwd30 not-elevated | Mann-Whitney p |
|---|---|---|---|---|---|
| Momentum-at-cap | 1680 | 102 | +2.33% | +0.97% | **0.387** |
| Rel-volume-at-cap | 448 | 1334 | +1.78% | **+2.41%** | **0.168** |
| Both elevated | 426 | 1356 | +1.93% | **+2.35%** | **0.260** |

None reach conventional significance. Volume and the combination point in the **wrong direction** — trades with elevated volume at the cap underperformed those without, not the reverse the hypothesis needs (a plausible but unconfirmed reading: high volume right at a 30-day momentum peak may sometimes reflect distribution/topping rather than confirmed accumulation — stated here as a speculative gloss, not a finding, since the result is not significant).

## 7. Within-dominant-regime group comparison (2010-2019 bull, 52.6% of population)

| Signal | n elevated | mean elevated | n not elevated | mean not-elevated | MW p |
|---|---|---|---|---|---|
| Momentum-at-cap | 909 | +1.92% | 35 | **+2.23%** | 0.789 |
| Rel-volume-at-cap | 245 | +1.62% | 699 | **+2.03%** | 0.472 |
| Both elevated | 236 | +1.71% | 708 | **+2.00%** | 0.601 |

The apparent reversal here (elevated slightly *underperforming* not-elevated) traces directly to the tiny 35-trade "not elevated" momentum group within this era being pulled up by the same 3 outlier trades already itemized in Section 4 (CF, URI, AMD — weighted contribution: removing just those 3 drops the not-elevated mean back near the elevated mean). This is not evidence *for* the momentum signal; it is the same small-sample fragility already visible in Section 4, now showing up as a reversal rather than a false positive. All three comparisons are non-significant regardless.

## 8. Outlier robustness

```
Trimmed (drop top/bottom 1% of fwd_ret_30d entirely):
  momentum_at_exit:  rho=0.0101, p=0.6719, n=1746   (weaker than the already-non-significant untrimmed 0.0224)
  rel_volume_at_exit: rho=-0.0260, p=0.2776, n=1746  (still negative, still non-significant)
```

Trimming makes the momentum relationship weaker still, not stronger — there is no hidden signal being masked by outliers; if anything the modest untrimmed positive point estimate leans partly on the same handful of extreme trades already flagged.

## 9. Step 1 gate decision: NOT WARRANTED for any signal, alone or combined — no Step 2 run

Per the task's own explicit gating rule, applied honestly to each of the three tested conditions:

1. **Momentum-at-cap** — no statistically significant relationship in aggregate (Mann-Whitney p=0.387; Spearman p=0.345), none within the dominant 2010-2019 regime (p=0.789; Spearman p=0.342, sign reversed), and none even within its own dominant Very Strong band in isolation (rho=0.008, p=0.75, n=1486 — a genuinely large sample). The one place a monotonic-looking gradient appears (the raw 4-band summary, Section 3) does not survive being checked as a continuous relationship (Section 5) or a formal group test (Section 6) — the coarse banding was creating an appearance of gradient that a direct test does not confirm, consistent with both sibling investigations' own finding that coarse momentum bands can look meaningful before deeper checks are applied.
2. **Relative volume at cap** — no significant relationship anywhere tested, and the point estimate is consistently in the **wrong direction** (elevated volume associated with lower, not higher, subsequent returns) in the aggregate, within the dominant regime, and after outlier trimming. There is no promising pattern to build on here at all, not even a fragile one.
3. **Both signals combined** — no significant relationship (p=0.260 aggregate, p=0.601 within the dominant regime), tracking the volume signal's own wrong-direction lean rather than reinforcing the momentum signal.
4. **A structural, independent reason a momentum-at-cap gate specifically would do very little even if it were real**: 94.3% of all `MAX_HOLDING` trades already clear the "Strong/Very Strong" bar by construction (momentum-ranked admission selects for exactly this). A mechanism gated on this condition would extend the overwhelming majority of trades regardless of whether the underlying relationship were real — its practical effect would be close to "extend almost everything," which is a materially different (and much larger, unvalidated) change than a genuinely selective mechanism.

**Because Step 1 does not clear its own gate for any of the three tested conditions, Step 2 was not built or run.** No engine modification was made, no reset-at-boundary sweep was run, no 16-block validation was performed.

## 10. Direct engagement — how this compares to the dynamic-cap investigation's own finding

The task specifically asks whether re-checking a tighter bar iteratively, or adding volume, behaves differently from `dynamic_momentum_cap`'s rejected momentum-trend-since-entry finding. It does — **this result is more decisively negative, not less**:

- `dynamic_momentum_cap` found a *nominally significant* aggregate relationship (Spearman rho=0.055, p=0.02; OLS p<0.001 era-controlled) that only failed to hold up once checked more deeply (non-monotonic quintiles, not robust to trimming, absent in the dominant regime specifically). There was, at least at first glance, something to investigate further.
- **This investigation's aggregate relationship never reaches that bar in the first place** — momentum-at-cap's own aggregate Spearman (rho=0.022, p=0.345) and Mann-Whitney (p=0.387) are already non-significant before any regime decomposition is applied, and volume's aggregate relationship is both non-significant and wrong-signed from the start.
- Reading momentum **at the cap point** rather than as a **trend since entry**, and adding a **structurally distinct** signal (volume) on top, does not recover any signal the trend-based approach found and then lost — if anything, the level-at-cap and volume-at-cap readings carry *less* usable information than the trend-since-entry reading did, consistent with `dynamic_momentum_cap`'s own supplementary finding that momentum *level* alone (as opposed to trend) has no predictive power (`momentum_at_cap` coef p=0.889 in that investigation's joint OLS) — this investigation independently reaches the same conclusion about level, now at the cap point specifically rather than mid-hold, and extends it to volume as well.
- The one qualitatively new idea this investigation adds — volume as a genuinely different kind of signal — does not change the outcome. It is not merely "as weak as momentum"; it points the wrong way.

## 11. Recommendation

**Researched, not adopted.** Neither a tighter momentum-strength re-check at the 30-day cap, nor relative volume, nor their combination, shows a real, promising, reasonably-sized pattern justifying the iterative 3-day-extension mechanism described in the task brief. The existing fixed 30-day `stock_max_holding_days` cap remains the best-supported choice. `config.yaml`, `analysis/decision.py`, `analysis/backtest.py`, and `brokers/trading212.py` are all unchanged. No engine modification, no Step 2 mechanism build, and no 16-block validation were performed, consistent with the task's own explicit gating instruction.

**Caveat, stated plainly:** this tests exactly the two named signals the task specified (momentum-strength band re-read at the cap point; trailing 20-day relative volume), banded via the existing q25/q75/q90 percentile scheme, individually and in simple conjunction. It does not rule out every conceivable re-evaluation mechanism — a different volume window, a smoothed/multi-day confirmation rule, or a different signal entirely could behave differently — but per the task's own explicit bound ("two specific, named signals, not an open search"), none of those were tested here.

## 12. Confirmation

- No production files were modified: `git status --short` shows only this task's new `research/momentum_volume_reeval_cap_2026-08-06/` directory, plus pre-existing, unrelated, already-completed sibling research directories' untracked files (`momentum_sizing_multiplier_2026-08-05/`, `exploratory_hypothesis_pass_2026-08-05/` — both present before this task began, neither created nor modified by it).
- `config.yaml` has zero diff against the working tree.
- No deploy/CI infrastructure was touched (`deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py`, `CLAUDE.md`, `CLAUDE_HISTORY.md` — none read for editing, none written to).
- No production trading-decision code was modified — `analysis/decision.py`, `analysis/backtest.py`, `brokers/trading212.py` were read-only, imported and called exactly as they exist; no custom candidate pool or `is_member()` override was built.
- Nothing was committed and nothing was pushed — `git status` shows no staged changes.
- No production database was touched — `database/investment_research.db` was read-only for every query in this investigation.
- No `git worktree` was created — worked directly in the main checkout, since no concurrent-session evidence was found (Section 1).
