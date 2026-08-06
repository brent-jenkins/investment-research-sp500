# Does Having a STOP_LOSS Mechanism At All Improve Outcomes? (2026-08-06)

**A genuinely different, more fundamental question than any prior stop-related investigation tonight.** Every earlier one assumed a stop-loss exists and tested variations around it (timing, distance, coordination). This tests removing it entirely — `CRISIS_EXIT → SIGNAL_OFF → MAX_HOLDING` only — run fresh against the just-corrected (realistic intraday-touch fill) engine specifically, since any prior intuition about whether stops help was very likely shaped by the old, overstated convention just fixed and is not trusted here.

**Headline finding, stated plainly before the detail: the aggregate numbers genuinely favor removing the stop, and that is exactly why this needed the elevated bar the task specified, not a lower one.** No-stop wins on every test-period headline metric and 2 more of 16 blocks. But real, identified tail risk exists (a confirmed −45% single-position loss and four more in the −18% to −35% range), and the one block spanning the 2007–2008 crisis — the exact regime a stop-loss exists to protect against — is measurably worse without it. **Recommendation: researched, not adopted.**

**Location of artifacts:** `research/no_stop_loss_2026-08-06/` — `no_stop_loss_investigation.py` (script, source of truth), `run.log` (full run log), `trades_with_stop_reset.csv`/`trades_no_stop_reset.csv` (+ full JSON), `rejections_with_stop_reset.csv`/`rejections_no_stop_reset.csv`, `test_period_headline_comparison.json`, `block_validation_results.json`, `direct_downside_matched_trades.csv`, `foregone_candidate_forward_returns.csv`.

## 1. Environment confirmation — running the corrected engine

Re-verified directly before building anything: a fresh parity run (`disable_stop_loss=False`, `reset_at_boundary_date=None`) reproduced the established corrected baseline exactly — 3,436 trades, £127,167.80 total P&L — asserted programmatically, not eyeballed. `git log`/DB coverage confirmed unchanged since the permanent stop-fill fix was deployed. This investigation runs entirely on top of the corrected engine, not the old, overstated one.

## 2. Mechanism — two isolated, marked, TEMPORARY additions, already reverted

Both added directly to the real `simulate_portfolio_compounding_mtm()` (not a hand-duplicated clone — same parity-risk reasoning as every other engine-level investigation tonight), both verified inert at their defaults (the parity check above), both **reverted immediately after capturing all results below** (`git checkout -- analysis/backtest.py`, confirmed clean).

- **`disable_stop_loss=True`** passes `stop_price=None` to `evaluate_exit_reason()` for the exit check — that function's own `STOP_LOSS` branch requires a non-`None` stop price, so this falls through cleanly to `SIGNAL_OFF`/`MAX_HOLDING`. `evaluate_exit_reason()` itself — still shared with live and every other caller — was never modified.
- **`reset_at_boundary_date=2020-01-01`** (`VALIDATION_TRAIN_TEST_SPLIT`) — the exact reset-at-boundary technique already established and validated in this project's own "30-day re-entry cooldown" Part B methodology: `cash` is force-adjusted exactly once, the first event day on/after the boundary, so `current_balance` reads exactly `starting_capital_gbp` from there on. Open positions carried across the boundary are left untouched — only the account's total capital base resets. Required per CLAUDE.md's "Two backtest conventions" standing requirement: comparing two variants is a reset-at-boundary question, not a continuous-walk one.

Position sizing is unaffected by `disable_stop_loss` — confirmed by a separate investigation earlier tonight (`research/underwater_time_cutoff_2026-08-06`) that stop distance never feeds `_var_position_size_core()` at all.

## 3. Capacity-turnover reporting — per the same-day standing requirement, kept separate throughout

This investigation's own context is what motivated that requirement, so it is applied rigorously here: Sections 5 and 6 below report the direct effect and the capacity-turnover effect **separately**, never blended into one number.

## 4. TEST-period (entry ≥ 2020-01-01) headline comparison

| Metric | With-stop | No-stop | Change |
|---|---|---|---|
| Trades | 1,279 | 931 | **−348 (−27.2%)** |
| Distinct tickers | 198 | 202 | +4 |
| **Total P&L** | **£52,607.99** | **£74,387.60** | **+£21,779.61 (+41.4%)** |
| Win rate | 44.25% | 56.07% | +11.82pp |
| Profit factor | 1.38 | 1.65 | +0.27 |
| Sharpe | 0.414 | 0.524 | +0.110 |
| Max DD (fixed) | −29.18% | −22.34% | improved |
| Max DD (peak-relative) | −15.57% | −11.48% | improved |
| CAGR | 11.54% | 14.88% | +3.34pp |
| Total return | 105.22% | 148.78% | +43.56pp |

**No-stop wins on every metric in this table.** Stated plainly because it's genuinely the headline result — this is not a marginal, ambiguous comparison at the aggregate level.

## 5. 16-block validation — where the picture gets more nuanced

```
With-stop: 11/16 blocks passed
No-stop:   13/16 blocks passed
```

| Block | Dates | With-stop | No-stop | With-stop CAGR | No-stop CAGR |
|---|---|---|---|---|---|
| **0** | **2007-01-01 → 2008-03-23** | ✗ | ✗ | **−32.03%** | **−42.34%** |
| 1 | 2008-03-23 → 2009-06-13 | ✗ | ✗ | −21.00% | −20.86% |
| 7 | 2015-07-27 → 2016-10-17 | ✗ | ✓ | −1.52% | +1.76% |
| 8 | 2016-10-17 → 2018-01-07 | ✗ | ✓ | 14.40% | 14.40% |
| 15 | (final block) | ✗ | ✗ | 44.97% | 55.16% |
| *(other 11 blocks)* | | ✓ | ✓ | mostly higher under no-stop | |

**No block ever flips from pass (with-stop) to fail (no-stop)** — the only two blocks that differ (7, 8) both flip fail→pass under no-stop. This is genuinely reassuring on its own terms. **But Block 0 — the one block spanning the leading edge of the 2007–2008 financial crisis — is measurably worse without the stop**: CAGR −32.03% → **−42.34%**, a real, concrete instance of the exact failure mode a stop-loss exists to guard against, showing up in the one part of 19 years of history that most resembles a genuine crisis regime. Block 1 (the crisis's continuation, through mid-2009) is roughly a wash (−21.00% vs. −20.86%). The blocks where no-stop wins most clearly (7, 8, and most of the middle blocks) are calmer, trending years — consistent with a stop-loss costing more in "chop you back into a winner" opportunity cost than it earns in crash protection during benign regimes, and earning its keep specifically when a genuine crisis hits.

## 6. Direct-downside effect (per the capacity-turnover reporting requirement — kept separate from Section 7)

Restricted to the 394 `(ticker, entry_date)` pairs admitted under **both** variants (a genuine like-for-like comparison, unconfounded by which candidates got a slot at all) — of these, 138 were `STOP_LOSS` under with-stop. What actually happened to those same 138 positions under no-stop:

```
Mean with-stop pnl_pct (at the stop):        −7.30%
Mean no-stop pnl_pct (eventual real outcome): −6.92%
Median no-stop pnl_pct:                       −6.35%
Worst no-stop pnl_pct:                       −45.04%

Eventually recover to a real win:  26/138 (18.8%)
Genuinely worse off without the stop (delta_pnl_gbp < 0): 61/138 (44.2%)
Better off without the stop (delta_pnl_gbp > 0):           77/138 (55.8%)
Total delta_pnl_gbp across these 138 matched trades:      +£1,400.96 (net positive)
```

**On average, even this specific stopped-out population is a wash-to-slightly-positive without the stop.** But the average hides the real question the task asked for explicitly — "how much worse do the genuine large losers get, not just an average":

| Ticker | Entry date | With-stop pnl% | No-stop pnl% | No-stop exit reason | Δ (£) |
|---|---|---|---|---|---|
| TMUS | 2011-07-25 | −4.90% | **−45.04%** | SIGNAL_OFF | **−£940.12** |
| AAPL | 2008-01-03 | −6.08% | **−32.46%** | MAX_HOLDING | **−£659.60** |
| NVDA | 2022-03-28 | −11.64% | **−34.74%** | MAX_HOLDING | **−£644.46** |
| WAT | 2008-01-03 | −3.64% | **−27.96%** | SIGNAL_OFF | **−£608.07** |
| CEG | 2024-10-03 | −7.97% | **−18.54%** | MAX_HOLDING | **−£485.34** |

**Five real, identified, individually large single-position losses that a stop-loss would have prevented**, ranging −18.5% to −45.0%, on real companies across three different eras (2008 GFC-adjacent, 2011, 2022 tech selloff, 2024) — not a single-era artifact. This is the concrete, quantified form of "unbounded per-position downside within a hold" that removing a stop actually means in practice, and it is real even though the *average* effect across the matched population is roughly neutral.

## 7. Capacity-turnover effect (kept separate from Section 6)

```
Total trades, full 2007-2026 run: with_stop=3,436  no_stop=2,553  (−883, −25.7%)
Total trades, TEST period only:   with_stop=1,279   no_stop=931   (−348, −27.2%)

Admitted under with-stop but NOT under no-stop: 3,042
Admitted under no-stop but NOT under with-stop: 2,159
```

**Removing the stop reduces total trade count by roughly a quarter — a large, real effect, not a rounding difference.** Without early stop-outs, positions occupy a capacity slot for materially longer on average (closable only via `CRISIS_EXIT`/`SIGNAL_OFF`/`MAX_HOLDING`), leaving less room for new admissions — confirmed directly, not inferred, via the no-stop run's own `REJECTED_CAPACITY` log:

```
Confirmed cases: a with-stop-admitted candidate was explicitly REJECTED_CAPACITY under no-stop
(its slot stayed occupied by a position that would have stopped out): 1,311

Mean 30-trading-day forward return of these foregone candidates:  +2.43%
Median: +2.04%   % positive: 58.5%
```

**A real, quantified, positive-expectancy opportunity cost** — the specific candidates displaced by no-stop's longer-held positions had, on average, a modestly positive 30-day forward return. This is not double-counted against Section 4's headline P&L (that figure already reflects the real, actually-simulated trade sequence under each variant) — it is the diagnostic answer to *why* no-stop trades less, and *what quality* of opportunity is being missed by trading less, exactly as the task's own "next-best-candidate forward returns" framing asked for.

## 8. Recommendation: researched, not adopted — the elevated bar is not met

**The aggregate, block-validated numbers genuinely favor no-stop, and that is stated honestly, not minimized.** Every test-period headline metric improves, 2 more blocks pass with zero blocks flipping pass→fail, and even the direct-downside effect on matched stopped-out trades is roughly neutral-to-positive on average. A lower bar — "is this technically better on the numbers" — would say yes.

**But the task set an explicitly elevated bar for touching a core safety mechanism, and three independent pieces of evidence say this doesn't clear it:**

1. **Real, identified, severe tail risk**: five confirmed single-position losses from −18.5% to −45.0% that a stop-loss specifically would have prevented, spanning three different market eras — not a single-era artifact, and not visible in any average-based metric.
2. **The one crisis-regime block in 19 years of history is measurably worse without the stop** (Block 0, 2007–2008: CAGR −32.03% → −42.34%) — the exact failure mode a stop-loss exists to guard against, showing up precisely where it should matter most. The strategy's good aggregate performance without a stop is concentrated in the calmer, trending years that dominate this 19-year sample by count — a future regime more like 2007–2008 than the sample's own average year is not something a backtest average can rule out.
3. **A real, quantified capacity-turnover cost**: 27% fewer trades in the test period, with the specifically-identified foregone candidates showing positive expected value (+2.43% mean 30-day forward return) — genuine opportunities being missed, not merely "fewer trades, doesn't matter."

Removing a bounded-loss mechanism entirely is a structural, qualitative change to the strategy's risk character, not a parameter tweak — VaR-based position sizing is calibrated against an assumed downside that the stop-loss is the actual enforcement mechanism for; removing it decouples the two. Good backtested performance in one specific 19-year sample, dominated by trending conditions, is not the same claim as "this is safe to remove," and per the task's own framing, "technically better on paper" is explicitly not sufficient here.

`config.yaml`, `analysis/decision.py`, `brokers/trading212.py` are all unchanged. `analysis/backtest.py` was temporarily modified for this investigation and confirmed reverted (`git diff` clean).

**Caveat, stated plainly:** this tests exactly the proposal (`STOP_LOSS` removed entirely from the priority chain) against exactly this comparison (reset-at-boundary, 2020-01-01 split, corrected engine). It does not rule out every conceivable refinement — a wider stop, a stop that only fires on confirmed multi-day declines, or a regime-conditional stop could behave differently — but none of those were tested here, and per this project's standing discipline, a rejected proposal is not grounds to keep tuning parameters without a fresh, explicitly-scoped reason to do so.

## 9. Confirmation

- `analysis/backtest.py` was temporarily modified (an additive, marked, isolated change), then reverted — `git diff analysis/backtest.py` confirmed clean, function signature confirmed restored.
- No other production files were modified: `config.yaml`, `analysis/decision.py`, `analysis/volatility.py`, `brokers/trading212.py` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — both variants used the real, unmodified admission/entry-gate/sizing logic; only the `STOP_LOSS` trigger input and the account's capital-reset point were changed, and only for the two research runs.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
