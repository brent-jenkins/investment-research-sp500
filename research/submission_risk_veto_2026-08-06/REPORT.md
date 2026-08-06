# Submission-Time "Excessive Risk" Veto — Research Report (2026-08-06)

**Location of artifacts:** `research/submission_risk_veto_2026-08-06/` — `step1_descriptive.py` (script, source of truth), `step1_run.log` (full run log), `trades_with_next_day_stop_distance.csv` (all 3,424 trades with recomputed next-day stop distance), `divergence_summary.csv`, `vetoed_vs_rest_comparison.csv`, `regime_decomposition.csv`.

## 0. Framing — a due-diligence veto at submission, not a change to candidate selection

Architecturally the same category as the existing price-sanity check (`analysis/decision.py:is_submission_price_move_blocked()`): a final, pre-trade gate applied ~17 hours after the EOD decision, at the moment an order is actually about to be placed, not a change to the entry gate or admission ranking. Price-sanity re-checks price; this investigates re-checking **volatility** — and therefore stop distance (2.0 × ATR(14)), which can shift overnight in a way a same-day decision never sees.

## 1. The backtest-gap limitation — confirmed, and how it was approximated

Confirmed directly, not assumed: `analysis/decision.py:492`'s own comment on `is_submission_price_move_blocked()` states the backtest engine "has no decide-then-submit time gap at all... every engine enters a position at the SAME moment the decision is made." This is structurally identical for any volatility-based submission-time check.

**Approximation used, stated explicitly per the task's own instruction**: for each of the corrected engine's real, already-admitted trades, stop distance was recomputed using that ticker's own **next trading day's** close and ATR(14) — the closest available daily-bar proxy for "what the risk snapshot would look like by the time submission actually happens." This is an approximation, not a reconstruction of the real ~1-hour-into-next-session submission moment (true intraday data doesn't exist for this history and can't be reconstructed now — the same daily-vs-intraday limitation noted throughout tonight). Faithfulness verified directly, not assumed: recomputing each trade's own **decision-day** stop distance from its own decision-day ATR/entry-price reproduced the trade's own already-recorded `stop_distance_pct` exactly for **all 3,424/3,424 trades checked** (0 mismatches) — confirming the ATR reconstruction genuinely matches what the real engine computed, before trusting the next-day recomputation built on top of it.

**Population**: reused directly from the corrected engine's own real 3,436-trade output (`research/realistic_stop_fill_permanent_2026-08-06/new_default_trades_full.json`, environment confirmed unchanged since) — no re-run needed. 3,424/3,436 trades had a usable next trading day (12 skipped, end-of-data edge cases).

## 2. How much does overnight divergence actually happen? Real, but small on average.

```
delta_pct (next-day stop% minus decision-day stop%), n=3,424:
  mean=+0.046pp   std=0.428pp   min=-1.818pp   25%=-0.154pp   50%=-0.039pp   75%=+0.152pp   max=+5.649pp

% widened overnight: 42.6%      % narrowed overnight: 57.4%
Mean |delta_pct|: 0.256pp        Median |delta_pct|: 0.154pp

delta_ratio (next-day / decision-day) percentiles:
  50th: 0.993   75th: 1.025   90th: 1.067   95th: 1.104   99th: 1.192
```

**The typical overnight shift is genuinely small** — a median absolute change of 0.15 percentage points, and even at the 99th percentile the stop distance only grows by ~19% relative to its decision-day value. Slightly more trades narrow overnight (57.4%) than widen (42.6%). This is itself a real, previously-unknown answer to the task's own first question — and it tempers expectations before looking at anything else: the underlying premise (meaningful overnight volatility shift) is real but modest for the large majority of candidates.

## 3. Data-driven veto threshold

**90th percentile of the next-day-recomputed `stop_distance_pct`, across the whole 3,436-trade historical population = 10.95%.** Captures 343 trades (10.0%), as designed.

**Interpretation stated explicitly, per this project's "state the convention, don't leave it implicit" discipline**: this is a single, fixed historical threshold — the same design convention the existing price-sanity threshold itself uses (a fixed 3%, not recomputed per day) — rather than a fresh, per-day, eligible-pool-relative percentile (the convention main.py's momentum-strength bands use). The task's own phrasing ("top decile of that day's distribution") could support either reading; the fixed-population version was chosen as the more defensible, computationally tractable, and directly analogous-to-price-sanity design. Stated as a scope decision, not an oversight — a per-day pool-relative version was not built or tested.

## 4. Performance of would-be-vetoed candidates — the decisive finding, and it points the wrong way

| Group | n | mean pnl% | median pnl% | win rate |
|---|---|---|---|---|
| **Would-be-vetoed** | 343 | **+1.63%** | −3.18% | 43.1% |
| Rest of population | 3,081 | **+0.95%** | −2.45% | 45.0% |

Mann-Whitney U=478,247.0, **p=0.0039 — statistically significant, but in the WRONG direction for a risk veto to make sense.** The candidates this veto would block have a *higher* mean realized return than the rest of the population, not a lower one. (Median is slightly worse for the vetoed group, −3.18% vs. −2.45%, and win rate slightly lower, 43.1% vs. 45.0% — a genuinely mixed picture even before regime decomposition, not a clean "vetoed = bad" story at any level.)

**A plausible read, stated honestly as interpretation, not proof**: stop distance is driven by ATR relative to price — a high next-day-recomputed stop distance is largely a marker of *high volatility*, and high-volatility, high-momentum names are exactly the kind of stock this strategy is designed to chase hardest. Filtering them out on volatility alone risks cutting into the strategy's own best-performing trades, similar in shape to the price-sanity threshold's own documented momentum-skew problem (CLAUDE.md's "Price-sanity threshold — momentum-skew investigation," a symmetric price check disproportionately filtering the highest-momentum candidates before that was fixed).

## 5. Regime confounding — checked explicitly, and this is where the real signal actually lives

| Era | n vetoed | mean pnl% (vetoed) | n rest | mean pnl% (rest) | MW p |
|---|---|---|---|---|---|
| **2007-2009 GFC** | 71 | **−3.43%** | 376 | −0.64% | **0.0005** |
| 2010-2019 bull | 44 | +2.79% | 1,666 | +1.12% | 0.8393 |
| 2020-2026 recent | 228 | +2.99% | 1,039 | +1.25% | 0.2727 |

**Vetoed-population era concentration**: 66.5% from 2020-2026, 20.7% from 2007-2009 GFC, only 12.8% from 2010-2019 bull.

**The only regime where "excessive next-day stop distance" is genuinely, significantly associated with worse outcomes is the small 2007-2009 GFC window** (n=71, 20.7% of the vetoed population) — and there, it's a real, coherent, sensible pattern: the worst GFC-vetoed trades cluster tightly around September–December 2008 (NTRS −18.8%, CF −18.3% and −14.5%, CHRW −14.0%, EOG −14.0%, MA −13.7%, all `CRISIS_EXIT`/`STOP_LOSS`) — genuine, extreme volatility spikes during the literal Lehman collapse predicting bad outcomes, exactly the intuition motivating this proposal. **But in the two much larger eras — 79.3% of the vetoed population combined — there is no significant effect, and the point estimate trends in the opposite (better-performing) direction in both.** This is the same "real effect confined to a small, atypical crisis window, absent from the dominant regime" shape that killed two other momentum-adjacent investigations earlier tonight, now showing up here for a volatility-based signal instead.

## 6. Step 1 gate decision: NOT WARRANTED — no Step 2 run

Per the task's own explicit gating rule:

1. **The underlying premise is only weakly supported to begin with** — overnight stop-distance divergence is real but modest for the large majority of candidates (median |Δ| 0.15pp; even the 99th percentile is only ~19% relative growth).
2. **The aggregate performance comparison points the wrong way for a risk veto** — would-be-vetoed candidates have a *higher* mean realized return than the rest (+1.63% vs. +0.95%, p=0.004), not lower.
3. **The one place a genuine, significant "vetoed = worse" pattern exists is a small, atypical crisis window** (2007-2009 GFC, 20.7% of the vetoed population) — completely absent, and directionally reversed, in the two eras making up the other 79.3%.

**Because Step 1 does not clear its own gate, Step 2 was not built or run.** No submission-time code was added to `brokers/trading212.py`, no reset-at-boundary sweep, no 16-block validation, and the direct/capacity-turnover effect split required by the standing requirement was not exercised on a mechanism that Step 1 itself doesn't support building.

## 7. Recommendation

**Researched, not adopted.** A submission-time volatility veto, using a data-driven top-decile threshold on next-day-recomputed stop distance, does not show a real, promising, regime-robust pattern — the aggregate signal is real but points the wrong direction, and the one regime where it works as intuited (genuine crisis conditions) is too small and atypical to generalize from. `config.yaml`, `analysis/decision.py`, and `brokers/trading212.py` are all unchanged.

**Caveat, stated plainly:** this tests exactly the proposed mechanism (a fixed, whole-population 90th-percentile threshold on next-day-recomputed 2.0×ATR(14) stop distance). It does not rule out every conceivable refinement — a per-day pool-relative threshold, a narrower crisis-conditional trigger (which the GFC finding above might actually support on its own, narrower terms), or a different volatility measure entirely could behave differently — but none of those were tested here, and per this project's standing discipline, a rejected proposal is not grounds to keep tuning parameters without a fresh, explicitly-scoped reason to do so.

## 8. Confirmation

- No production files were modified: `analysis/backtest.py`, `analysis/decision.py`, `brokers/trading212.py`, `config.yaml` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — the population is the corrected engine's own real, already-generated trade list; the next-day recomputation is a purely retrospective, read-only price/ATR lookup with no bearing on any trading decision.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
