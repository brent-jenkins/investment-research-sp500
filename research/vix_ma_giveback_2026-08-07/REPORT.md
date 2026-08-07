# VIX sizing / S&P-200d regime gate / 8% peak-DD giveback exit — joint test (2026-08-07)

**Origin.** User proposal, developed over conversation into three mechanisms tested together rather than separately, per the user's own "closing AND locking the door" framing: (1) VIX-scaled position sizing as a pure risk control, (2) an S&P 500 trend filter to gauge market conditions before entry, (3) a peak-drawdown "giveback" exit to lock in profit on a reversing winner without cutting it before it's truly peaked.

**Backtest convention: CONTINUOUS WALK, not reset-at-boundary**, per CLAUDE.md's "Two backtest conventions" standing requirement — one single 2007-01-01 → 2026-08-06 compounding walk per variant, exactly the shape a real single account would show, since the question here ("is this mechanism worth adding to the one real strategy") is not a train/test variant-selection question. The 16-block validation applied on top uses `compute_metrics_fixed()`'s fixed-starting-capital-per-block convention (the same reset-at-boundary-equivalent fairness every other block-validated comparison in this project's history uses), so block-level comparisons between variants are still fair even though the underlying walk is continuous.

**Exact pass/fail formula** (`analysis/validation.py:_block_passes()`, unchanged): a block **passes** iff `profit_factor > 1.0` AND `abs(strategy_max_drawdown_pct) <= abs(sp500_max_drawdown_pct) * 1.5`.

**Environment**: local DB was empty at the start of this session — pulled a fresh, verified copy from production (871 tickers, 3,461,961 rows, through 2026-08-06) before anything else, per this project's standing local-research-environment requirement. `^VIX` history (2006–present) fetched via yfinance directly for this research; not added to production ingestion.

## 1. Parameter selection — diagnostics, not tuning

Per the user's request to "work out the appropriate peak DD % and MA windows," each parameter was set from a real, descriptive diagnostic against the actual historical trade/price data, then tested **once** at that value — not swept to find a winner. Full detail and numbers in `step1_vix_diagnostic.py` / `step2_baseline_and_diagnostics.py` / `step2b_long_ma_diagnostic.py` and their logs.

- **VIX dampener, `min(1, 20/VIX)`, continuous, no threshold** — used exactly as proposed. Mean VIX 2007-2026 = 19.79, confirming 20 is a sensible, non-arbitrary centering constant: the dampener is a no-op on 64.3% of days, only meaningfully bites (halves size or more) on 3.6% of days.
- **Market regime filter — pivoted from the originally-proposed 7/14/30-day S&P MA to a 200-day MA.** The short windows showed a mean-reversion pattern in the real data — days below the MA had systematically *better* forward S&P returns than days above it (e.g. MA30: 20-day-forward return 1.12% below vs. 0.63% above) — the opposite of what a "only enter when the market looks healthy" gate wants. Widening to 100/150/200-day windows flipped this to the theoretically-correct direction (200d: 60-day-forward 2.55% above vs. 2.01% below; 120-day-forward 5.22% above vs. 4.21% below), consistent across all three long windows tested. **200-day chosen** as the standard, most-stable (fewest regime flips: 115 vs. 221 for 100-day) of the three, not because it scored best in this data — an off-the-shelf choice, not a fitted one.
- **Peak-DD giveback exit — widened from the originally-proposed 1%/3%/5% to 8%.** Real diagnostic on 2,872 historical trades that exited via MAX_HOLDING/SIGNAL_OFF and were ever in profit: a 1% cap would have fired on 83.5% of them, 3% on 54.5%, 5% on 33.8% — these are ordinary-volatility trigger rates, not reversal detection, and directly risk "closing winners before they've truly peaked" (peaks land 75-83% of the way through the average hold — early giveback is usually just normal noise on a trade still climbing). 8% fires on a much more selective 15.6% of eligible trades, sitting close to the real discrimination point: of trades whose giveback reached 8%, 50.4% went on to end as losers anyway (vs. only 17.5% for the population as a whole) — a genuine signal, not noise, at that level.

## 2. Five variants, one continuous walk each

Today's real config (`per_trade_pct=0.05`, `max_total_deployed=0.8`, `starting_capital_gbp=50,000`), live universe (503 candidates), mark-to-market engine (`simulate_portfolio_compounding_mtm()`), with three new, additive, marked, temporary keyword parameters added directly to that function (reverted after this investigation — see §5) rather than a hand-duplicated research clone, per this project's "Research scripts must verify decision-engine parity" standing requirement.

**Baseline reproduced exactly at default (no-op) parameters** before trusting any variant: 5,397 trades, £276,421.84, byte-identical across two independent runs (`step2_baseline_and_diagnostics.py` and `step3b_run_variant.py baseline`) — confirms the temporary additions are behaviourally transparent when disabled.

| Variant | n_trades | distinct tickers | win rate | PF | Sharpe | max DD (fixed-denom) | CAGR | total return | **16-block pass** |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline** | 5,397 | 388 | 44.27% | 1.29 | 0.329 | −128.9% | 10.62% | 552.84% | **5/16** |
| VIX sizing only | 6,173 | 412 | 45.00% | 1.33 | 0.365 | −87.64% | 9.40% | 430.88% | **4/16** |
| S&P-200d gate only | 4,376 | 376 | 45.00% | 1.28 | 0.315 | −82.67% | 8.65% | 315.70% | **4/16** |
| Peak-DD 8% giveback only | 5,785 | 392 | 47.21% | 1.27 | 0.318 | −104.22% | 9.59% | 448.55% | **4/16** |
| **All three combined** | 4,964 | 384 | 47.72% | 1.29 | 0.338 | −87.51% | 8.27% | 291.64% | **2/16** |

**Every mechanism improves win rate and reduces (fixed-denominator) drawdown vs. baseline — none improves the block-pass rate, and combining all three is the worst result of any variant tested, not the best.** This directly contradicts the "closing AND locking the door" hypothesis this investigation was built to test: rather than three individually-weak mechanisms reinforcing each other, they interfered — the combined variant's block-pass rate (2/16) is worse than any single mechanism alone (4/16 each).

*(Baseline's own 5/16 is itself lower than figures elsewhere in this project's history — a known, separate effect of the `max_total_deployed` cap having been raised from 0.5 to 0.8 on 2026-08-06, a deliberate user decision the deployment-cap sweep found reduces cost-adjusted block-pass performance; not a new finding of this investigation, just the honest baseline these five variants were compared against.)*

## 3. Why — block-level detail, not just the aggregate

Full block-by-block table in `step4_run.log`. The dominant, mechanistic explanation is the **S&P-200d gate's lag at V-shaped recoveries**, not a diffuse or unexplained effect:

- **Block 0 (2007-01 → 2008-03) and Block 1 (2008-03 GFC crash → 2009-06):** the 200-day MA needs 200 days of history to exist at all, and the market spent most of this window below whatever MA had formed — `ma_only`/`combined` traded almost nothing (n=0 and n=3 respectively, vs. 75-345 for the other variants). This is the gate doing exactly what a trend filter should during a crash, but it also means these two blocks are near-total no-strategy periods for the gated variants.
- **Block 2 (2009-06 → 2010-09, the recovery):** baseline **passes** (CAGR 9.15%, PF 1.52) — this is exactly the sharp-recovery period a momentum strategy should capture. `ma_only` and `combined` **fail here specifically** (CAGR −0.84% and 2.85%) — the 200-day average was still catching up from the crash, so the gate stayed shut through the most profitable leg of the recovery. **Block 10 (2019-04 → 2020-06, the COVID crash+recovery)** shows the same mechanism but more marginally than originally stated here — corrected after the pairwise-combination round below surfaced the error: `ma_only` alone actually still *passes* block 10 (PF 1.01, just over the bar, despite a negative −3.64% CAGR — a warning sign, not yet a fail); it's `vix_ma` and `combined` that push it over into an outright fail. The lag mechanism is the same; `ma_only` in isolation is right at the edge of it rather than already over.
- This is the textbook, well-known weakness of any long-window SMA trend filter — slow to re-engage after a sharp reversal — and it is costing real blocks here, not a marginal effect.

The other two mechanisms are more evenly matched to baseline block-by-block (sometimes better, e.g. Block 4, sometimes worse, e.g. Block 12), but none tips the aggregate pass count upward on its own, and the **combined variant inherits the S&P-gate's worst blocks (0, 1, 2, 10) while gaining no offsetting wins from the other two mechanisms in those same blocks** — explaining why 2/16 is lower than any individual mechanism's 4/16 rather than a blend of them.

## 4. Capacity-turnover — reported separately, per CLAUDE.md's standing requirement

None of these mechanisms just "removes the bad trades and leaves the rest" — every one of them, alone or combined, cascades through this system's capacity-constrained, compounding, momentum-priority admission into a substantially different trade sequence for the rest of the 19-year walk:

| Variant | removed vs. baseline | newly admitted | unchanged | same-day contested-slot swaps |
|---|---|---|---|---|
| VIX sizing only | 3,409 | 4,185 | 1,988 (36.8%) | 1,418 |
| S&P-200d gate only | 4,208 | 3,187 | 1,189 (22.0%) | 1,056 |
| Peak-DD giveback only | 3,714 | 4,102 | 1,683 (31.2%) | 1,451 |
| **All three combined** | 4,283 | 3,850 | **1,114 (20.6%)** | 1,060 |

**Roughly 63-79% of trades are genuinely different trades under every variant, not the same trades with a tweak.** This is the same admission-order-cascading effect this project's `ma_extension_ceiling_2026-08-06` investigation found for a related (single) mechanism — confirmed again here across three different mechanisms and their combination. It's the direct mechanical explanation for why individually-sound risk-management logic (every mechanism genuinely reduces drawdown and improves win rate) doesn't translate cleanly into a genuinely different, not just risk-adjusted, set of underlying bets — and why combining mechanisms doesn't simply add their individual effects.

## 5. Recommendation

**Researched, not adopted — for all three mechanisms, individually and combined.** Per this project's standing bar ("a real descriptive finding is not itself grounds to keep tuning parameters until something clears the bar," `ma_extension_ceiling_2026-08-06`): none of the three, at their data-derived (not swept) parameter settings, improves the 16-block pass rate, and the combined variant is the worst of the five. The user's "closing AND locking the door" hypothesis was tested directly and did not hold in this implementation — worth stating plainly rather than reframed as a partial win, since every whole-run risk metric (drawdown, win rate) did improve, which could otherwise read as success if the block-pass rate weren't checked.

**What would be worth a follow-up, if pursued:** the S&P-200d gate's lag at recovery inflection points is a specific, mechanistic, fixable-in-principle weakness (not a fundamental flaw in "gauge the market" as an idea) — a faster-reacting regime signal (e.g. a shorter confirmation window paired with the 200-day baseline, or a rate-of-change condition rather than a level condition) could plausibly avoid missing blocks 2 and 10 specifically. This was not tested here, per this project's own discipline against parameter-tuning past a first reasonable choice within one investigation.

**Caveat, stated plainly:** this tests exactly the parameter values derived above (VIX/20; S&P close vs. its 200-day MA as a hard gate; 8% peak-DD giveback), each chosen once from real data, not swept. It does not rule out every conceivable variant of these three ideas — a softer regime signal, a proportional (rather than hard-gate) market filter, a different giveback percentage, or sizing the giveback threshold per-ticker by volatility could behave differently — but none of those were tested here.

## 6. Reproducibility

- Full trade-level output for all 5 variants preserved: `trades_baseline.csv`, `trades_vix_only.csv`, `trades_ma_only.csv`, `trades_dd_only.csv`, `trades_combined.csv` (per CLAUDE.md's "preserve full trade-level output" standing requirement — this is a citable headline finding).
- Diagnostics preserved: `vix_history.csv`, `giveback_diagnostic.csv`, `sp500_ma_regime_diagnostic.csv` (7/14/30d), `sp500_long_ma_regime_diagnostic.csv` (100/150/200d).
- `block_validation_results.json` (all 16 blocks × 5 variants), `whole_run_summary.csv`, `capacity_turnover.csv`.
- `analysis/backtest.py` was temporarily modified (three new, additive, marked, keyword-only parameters on `simulate_portfolio_compounding_mtm()`) — **reverted immediately after this investigation**, confirmed via `git diff analysis/backtest.py` clean. No other production file was touched. Nothing was committed or pushed. No production database was modified (this session's local DB copy is a read-only pull from production, gitignored, used only for this research).

## 7. Follow-up round (2026-08-07): faster regime signal + pairwise combinations

Two follow-up questions from the user: (a) chase the "faster-reacting regime signal" idea flagged in §5, since the S&P-200d gate's specific weakness was its lag at recovery inflections; (b) since the full 3-way combination made things worse, test each pairwise combination of 2 mechanisms as well, not just the singles and the full triple.

### 7a. Rate-of-change re-entry signal — researched, dropped before full-engine testing

Diagnosed, not built. The natural "faster" candidate is the S&P's own N-day rate of change (ROC) rather than a level-vs-MA test. **First check (does it actually react faster at real crash bottoms) confirmed yes**: at both the 2009-03-09 and 2020-03-23 troughs, ROC(20-90) turned positive 3-43 days after the bottom vs. the 200-day MA's 65-84 days — a large, genuine speed advantage.

**Second check (does trading on it actually capture better forward returns) reversed the conclusion, at two levels:**
- **Full-sample**: negative-ROC days had *better* forward returns than positive-ROC days, across ROC windows 20/30/40/60 and forward horizons 20/60/120 days — the identical mean-reversion pattern already found for the short (7/14/30d) MA windows in §1, for the same underlying reason (ordinary small dips dominate the sample, not rare crash bottoms).
- **Scoped to the actual target population** (days already below the 200-day MA, i.e. exactly where a re-entry override would apply): still reversed. E.g. ROC30, 120-day-forward return: **2.09%** once ROC had turned positive vs. **5.11%** while still negative. Consistent in sign across all four windows and all three horizons.

**Interpretation**: the trough-timing check only measures when the signal flips, not whether trading on the flip is profitable. The market's best forward returns from a bear-regime state come from buying while things still look weak on trailing return (near the true bottom), not waiting for ROC to confirm a recovery — the classic trade-off any trend-confirmation filter accepts (fewer whipsaws, but later, less profitable re-entries). Per the user's decision, this idea was dropped before spending the ~10-13 minutes of full-engine compute per variant it would have cost — the diagnostic evidence already argued against it at both the level it needed to hold.

### 7b. Pairwise combinations — same parameters as §2 (VIX `min(1,20/VIX)`, S&P-200d gate, 8% peak-DD giveback), no re-tuning

| Variant | n_trades | win rate | PF | Sharpe | max DD | CAGR | **16-block pass** |
|---|---|---|---|---|---|---|---|
| Baseline | 5,397 | 44.27% | 1.29 | 0.329 | −128.9% | 10.62% | **5/16** |
| VIX only | 6,173 | 45.00% | 1.33 | 0.365 | −87.64% | 9.40% | 4/16 |
| MA-200d only | 4,376 | 45.00% | 1.28 | 0.315 | −82.67% | 8.65% | 4/16 |
| DD-giveback only | 5,785 | 47.21% | 1.27 | 0.318 | −104.22% | 9.59% | 4/16 |
| **VIX + MA** | 4,701 | 45.12% | 1.31 | 0.336 | −87.13% | 8.88% | **3/16** |
| **VIX + DD** | 6,550 | 47.62% | 1.29 | 0.344 | −102.86% | 8.74% | **4/16** |
| **MA + DD** | 4,645 | 48.33% | 1.30 | 0.342 | −106.83% | 9.24% | **3/16** |
| All three combined | 4,964 | 47.72% | 1.29 | 0.338 | −87.51% | 8.27% | **2/16** |

**A clean, specific pattern, not an undifferentiated "everything gets worse the more you combine":** every combination that includes the S&P-200d gate scores worse than any combination without it. `VIX + DD` (no MA) ties the best single-mechanism result (4/16, no worse than either alone) — VIX sizing and the peak-DD exit compose without actively harming each other. `VIX + MA` and `MA + DD` both drop to 3/16 — worse than either constituent mechanism alone in every case. The full triple (which includes MA) is worst of all at 2/16.

**Which blocks, precisely** (baseline passes 2, 3, 4, 10, 12 — blocks 3 and 4 are robust, passing under every single variant tested):

| Variant | Passing blocks | Lost vs. baseline |
|---|---|---|
| Baseline | 2, 3, 4, 10, 12 | — |
| VIX only | 2, 3, 4, 10 | 12 |
| MA only | 3, 4, 10, 12 | 2 |
| DD only | 2, 3, 4, 10 | 12 |
| VIX + MA | 3, 4, 12 | 2, 10 |
| VIX + DD | 2, 3, 4, 10 | 12 |
| MA + DD | 3, 4, 10 | 2, 12 |
| Combined | 3, 4 | 2, 10, 12 |

The MA gate alone only costs block 2 outright (block 10 survives on its own, barely — PF 1.01). But layering VIX sizing on top of the MA gate tips block 10 into an outright fail too (`VIX + MA` loses both 2 and 10): VIX dampening shrinks position sizes hardest exactly when volatility spikes, which is exactly when the COVID recovery (block 10) needed full-size positions to turn its already-marginal PF back above 1.0 — the two mechanisms' individually-reasonable risk reductions compound against the same block rather than cancelling out.

### 7c. Capacity-turnover (pairwise combos)

| Variant | removed vs. baseline | newly admitted | unchanged | same-day contested-slot swaps |
|---|---|---|---|---|
| VIX + MA | 4,219 | 3,523 | 1,178 (25.1%) | 1,052 |
| VIX + DD | 3,744 | 4,897 | 1,653 (25.2%) | 1,492 |
| MA + DD | 4,323 | 3,571 | 1,074 (23.1%) | 1,071 |

Same order of magnitude as every single-mechanism variant in §4 — roughly three-quarters of trades are genuinely different under any pairwise combination too, confirming the capacity-cascading effect isn't specific to the mechanisms tested individually or combined three-way.

### 7d. Revised recommendation

**Still researched, not adopted — including every pairwise combination.** But the finding is now more specific and more useful than "combining doesn't help": **the S&P-200d regime gate is the mechanism causing the degradation, in every combination it appears in** (`MA only`, `VIX+MA`, `MA+DD`, and the full triple are the four worst-performing variants of the eight tested, all at 2-4/16, with the two gate+something-else pairings actually below the gate alone). VIX sizing and the peak-DD giveback exit, on their own or paired together, are consistently the least damaging — `VIX + DD` matches the best score any mechanism achieves (4/16) without needing the gate at all. If either of these two ever warrants a second look, it would be as a `VIX + DD` pairing without the regime gate — not tested to actually improve on baseline here, but the closest any combination came to not making things worse.

**Correction to §3 acknowledged above**: the original write-up overstated `ma_only`'s block-10 result as an outright fail; the pairwise round's own data (re-checked directly, not re-asserted) shows it as a marginal pass. Recorded here per this project's own reproducibility standard — a wrong claim about a pass/fail outcome doesn't get quietly dropped.

`analysis/backtest.py` was re-modified identically to §2's original addition for this round, and reverted again immediately after — confirmed via `git diff analysis/backtest.py` clean. Trade-level CSVs for all three pairwise variants preserved (`trades_vix_ma.csv`, `trades_vix_dd.csv`, `trades_ma_dd.csv`).
