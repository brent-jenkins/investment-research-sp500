# Entry-Time MA-Extension Ceiling — Research Report (2026-08-06)

**Location of artifacts:** `research/ma_extension_ceiling_2026-08-06/` — `step1_descriptive.py`, `step1_run.log`, `trades_with_ma_extension.csv` (all 3,161 trades, independently re-derived MA14/30/50 at entry), `group_table_whole.csv`, `group_table_maxhold.csv`, `two_group_results.csv`, `spearman_results.csv`, `partial_correlation_results.csv`, `outlier_robustness.csv`, `regime_decomposition.csv`, `extension_distribution_by_era.csv` (Step 1); `step2_mechanism_validate.py`, `step2_run.log`, `trades_baseline.csv`/`trades_gate.csv`/`trades_rerank.csv`, `whole_run_summary.csv`, `block_validation_results.json`, `admission_interaction.csv` (Step 2).

## 1. Environment re-verification

`git status`/latest commit/`price_data` coverage all confirmed unchanged since the enriched export ran earlier tonight (same session). Trade population (ticker, entry_date, exit_date, entry_price, pnl_pct, exit_reason, momentum_at_entry_pct, holding_days) reused from that export's own direct engine fields — **its `ma_14/30/50_at_entry` columns were explicitly NOT reused** for Step 1's core computation, per the task's instruction.

## 2. Step 1 — independent re-derivation, confirmed

**MA14/30/50 independently recomputed** directly from stored `price_data` (fresh rolling-mean code, a separate code path from the export's own). Bonus consistency check against the export's own MA columns: **max abs diff = 0.000000 across all 3,161 trades, all three windows** — the export's MA values were already correct, now independently confirmed rather than assumed.

**Group comparison, corrected scale** (an initial print/CSV bug that double-scaled percentages by 100× was caught and fixed before use here — confirmed it never affected any statistical test, since Spearman/Mann-Whitney/partial-correlation are all scale-invariant and were computed on the correctly-scaled raw values throughout; only descriptive mean/median display was affected, now corrected):

| n_mas_above | n (whole pop) | mean pnl% | median pnl% | n (MAX_HOLDING) | mean pnl% | median pnl% |
|---|---|---|---|---|---|---|
| 0 (below all 3) | 759 | **3.05%** | 0.74% | 468 | **9.00%** | 7.07% |
| 1 | 365 | 2.32% | 0.00% | 201 | 9.83% | 7.99% |
| 2 | 412 | 1.82% | 0.42% | 232 | 8.03% | 5.85% |
| 3 (above all 3) | 1,625 | **1.28%** | −0.97% | 895 | **6.84%** | 5.17% |

Whole-population mean is **cleanly monotonic** (3.05→2.32→1.82→1.28%) — reproduces the motivating manual finding exactly. MAX_HOLDING-only is not perfectly monotonic (group 1 slightly exceeds group 0), but the overall decline from least- to most-extended is clear and the endpoints move the same direction (9.00%→6.84% mean, 7.07%→5.17% median).

**Two-group test (below-all-three vs. above-all-three):**
- Whole population: 3.05% vs. 1.28%, Mann-Whitney p=0.0456 (significant, though not by a wide margin).
- **MAX_HOLDING only: 9.00% vs. 6.84%, Mann-Whitney p=0.0008** — highly significant, and this is the cleanest test (no exit-reason-composition contamination).

**Spearman, all three MA windows, both scopes** — consistent sign and magnitude throughout:
```
MA14: ALL rho=-0.0506 p=0.0044 | MAX_HOLDING rho=-0.0851 p=0.0003
MA30: ALL rho=-0.0587 p=0.0010 | MAX_HOLDING rho=-0.0964 p=0.00004
MA50: ALL rho=-0.0505 p=0.0045 | MAX_HOLDING rho=-0.0925 p=0.00009
```
Not a coincidence tied to one arbitrary window choice — all three independently significant, same direction.

**Partial Spearman controlling for entry momentum** (rank-residual method): ALL rho=-0.0460, p=0.0097, n=3,161; MAX_HOLDING rho=-0.1053, p=0.000008, n=1,796 — reproduces the motivating finding's own claim (rho=-0.055, p=0.002) closely, independently. **Confirmed: not just momentum in disguise.**

**Outlier robustness — the one check the motivating manual finding hadn't done yet, and the most reassuring result in this investigation.** Trimming the top/bottom 1% of `pnl_pct`:
```
ALL trimmed:         rho=-0.0639 p=0.0004  (untrimmed: rho=-0.0505 p=0.0045)  -- STRONGER, not weaker
MAX_HOLDING trimmed: rho=-0.1103 p=0.000004 (untrimmed: rho=-0.0925 p=0.00009) -- STRONGER, not weaker
```
**This effect gets stronger after removing extreme trades, the opposite of what killed several other investigations tonight** (dynamic_momentum_cap's trend finding collapsed from p=0.02 to p=0.11 under the same test; momentum_horizon's Weak-band finding was traced to two outlier trades). This is a genuinely more robust signal than anything else tested tonight.

## 3. Direct engagement with the regime-inconsistency caveat

**Confirmed exactly as described — this is real, not glossed over:**

| Era | n (MAX_HOLDING) | Spearman rho | p | below-mean | above-mean | MW p |
|---|---|---|---|---|---|---|
| 2007-2009 GFC | 158 | **+0.030** | 0.705 | 7.53% (n=59) | 7.90% (n=65) | 0.496 |
| 2010-2019 bull (dominant) | 944 | **−0.186** | **<0.0001** | 8.58% (n=228) | 5.61% (n=486) | **0.00003** |
| 2020-2026 recent | 694 | −0.049 | 0.196 | 10.01% (n=181) | 8.38% (n=344) | 0.223 |

Strong and highly significant in the dominant 2010-2019 regime (52.6% of the MAX_HOLDING population). Direction-consistent (below still beats above) but not significant in 2020-2026. Genuinely reversed, though not significant, in the small GFC window. **This is a meaningfully different shape from what killed other investigations tonight** — there, the effect lived only in a small, atypical window and vanished in the dominant regime; here the dominant regime is where it's strongest, and the weaker eras don't contradict it so much as fail to confirm it at conventional significance with smaller samples.

**A partial, data-grounded explanation for the regime gap, offered honestly as suggestive rather than proven:**

| Era | mean pos_vs_ma50 | median | q25–q75 | %above-all-3 | %below-all-3 |
|---|---|---|---|---|---|
| 2007-2009 GFC | 3.76% | 3.25% | −3.51% to 9.98% | 50.0% | 27.5% |
| 2010-2019 bull | 4.44% | 4.50% | −0.49% to 9.00% | 53.1% | 22.5% |
| 2020-2026 recent | 6.29% | 5.88% | −1.48% to 13.39% | 49.7% | 24.8% |

The 2020s population sits further above its own MAs on average (mean 6.29% vs. 3.76–4.44% in earlier eras) with a wider spread — consistent with a market where structurally different, high-momentum leaders (the same semiconductor/storage cluster already flagged independently in three other investigations tonight) routinely trade well above their own moving averages while still performing well, diluting the "stretched = risky" signal in that specific era. This is a plausible read of the distributional data, not a proven causal explanation.

## 4. Step 1 gate decision: CLEARED

Monotonic (whole population, cleanly; MAX_HOLDING, broadly), significant in the cleanest test (MAX_HOLDING only, p=0.0008), consistent across all three MA windows, survives (and strengthens under) outlier trimming, survives momentum-controlling partial correlation, and the regime caveat — while real — points the SAME direction as the headline finding in every era except a small, non-significant reversal in the smallest, most atypical window. **This is the most robust signal found in any investigation tonight.** Proceeded to Step 2.

## 5. Step 2 — two mechanisms, both tested, neither adopted

**Implementation, stated precisely per the task's own instruction on fidelity.** A genuinely additive, clearly marked, temporary addition to the REAL `analysis.backtest.simulate_portfolio_compounding_mtm()` — three new keyword parameters (`extension_mode`, `extension_threshold_pct`, `extension_discount_coef`), default `extension_mode="none"` producing byte-identical behaviour to before. **Verified, not assumed**: re-running with the addition in place and `extension_mode="none"` reproduced the established baseline exactly (3,161 trades, 1,796 MAX_HOLDING, 56.8%). This was chosen over a hand-duplicated research clone specifically because the admission sort key lives inline inside a nested closure (`_admit_group()`) with no exposed seam to monkeypatch — a duplicate would have re-introduced exactly the kind of drift risk this project's "Research scripts must verify decision-engine parity" standing requirement warns about. **The addition was reverted immediately after capturing all results below** (`git checkout -- analysis/backtest.py`, confirmed zero diff) — no production code is left changed by this investigation.

**Mechanism 1 — GATE**: block a candidate outright if its price sits more than **15%** above its own 50-day MA at entry. Threshold chosen from the real historical admitted-trade population's own extension distribution (15% sits between its 85th [14.6%] and 90th [17.2%] percentile) — a defensible "clearly stretched" cutoff, stated explicitly as a first, reasonable choice, not a carefully-derived optimum.

**Mechanism 2 — RERANK**: `adjusted_score = momentum × (1 − min(1, max(0, ext_pct/100) × 1.0))` — a proportional (not fixed-subtraction) discount to the momentum-priority admission sort, scaling with the candidate's own momentum magnitude so it doesn't over- or under-penalize across this system's huge momentum range (a few percent to 1000%+, per tonight's other investigations). Lets a less-extended, lower-raw-momentum candidate win a capacity-contested slot from a more-extended one — something the gate structurally cannot do.

### 5a. Whole-run summary (fixed-capital metrics, full 2007-2026 continuous walk)

| Variant | n_trades | distinct tickers | win rate | PF | Sharpe | max DD | CAGR | total return |
|---|---|---|---|---|---|---|---|---|
| Baseline | 3,161 | 331 | 49.19% | 1.65 | 0.607 | −88.77% | 13.60% | 968.95% |
| Gate | 3,150 | 339 | 48.00% | 1.62 | 0.577 | **−81.45%** | 13.01% | 871.26% |
| Rerank | 3,171 | 327 | 48.41% | **1.67** | **0.619** | −109.49% | **14.09%** | **1,058.96%** |

**Gate**: worse on every return-based metric than baseline (lower PF, Sharpe, CAGR, total return), better on drawdown. **Rerank**: modestly better on PF/Sharpe/CAGR/total-return, but meaningfully worse drawdown.

### 5b. 16-block validation — the deciding evidence

```
Baseline: 10/16 blocks passed
Gate:     10/16 blocks passed  -- TIES baseline, no improvement
Rerank:    8/16 blocks passed  -- WORSE than baseline
```

**Neither mechanism improves the block-pass rate.** The gate exactly ties baseline despite its whole-run metrics being uniformly worse on return; re-ranking's modestly better headline return numbers do NOT translate into more blocks clearing this project's own `PF > 1.0` / drawdown-within-1.5×-S&P bar — if anything, re-ranking is worse by this measure.

### 5c. Admission-order interaction — why a real correlational signal didn't turn into a validated improvement

```
Gate:   2,334 trades removed vs. baseline, 2,323 newly admitted, 827 unchanged (74% of baseline trades displaced)
        925 distinct dates where a removal AND a different, new addition happened same-day (genuine contested-slot swaps)
Rerank: 2,217 trades removed vs. baseline, 2,227 newly admitted, 944 unchanged (70% of baseline trades displaced)
        905 distinct dates with genuine contested-slot swaps
```

**This is the key finding explaining Section 5b.** Changing the admission rule at all — gate or rerank — doesn't just filter out the worst ~5-15% of trades and leave the rest untouched. Because admission is capacity-constrained and the account compounds, blocking or deprioritizing even a modest share of candidates cascades: roughly **70-74% of all trades ever generated are DIFFERENT trades**, not the same trade sequence minus the extended ones. Both mechanisms are doing exactly what they were designed to do (verified directly: hundreds of same-day slot swaps where a less-extended candidate won a slot a more-extended one held under baseline) — but a real, modest-sized correlational effect on individual trade quality (rho in the −0.05 to −0.19 range) gets diluted, once propagated through this much downstream path-dependency, to a net effect that is flat-to-negative at the block level, not a clean improvement.

## 6. Recommendation

**Researched, not adopted — for both mechanisms.** Step 1's underlying observation is real and the most robust finding of any investigation tonight — but Step 2 shows directly, not just theoretically, that a well-evidenced descriptive correlation does not automatically translate into a validated improvement once admission-order cascading effects are accounted for. Per the task's own explicit bar ("do not implement anything on the VM unless the evidence shows a clearly, exceptionally strong improvement"): the gate ties baseline on block-pass rate while being worse on every return metric; re-ranking is worse on block-pass rate despite modestly better headline returns. Neither clears the bar.

`analysis/backtest.py` is confirmed reverted to its pre-investigation state (`git diff` clean). `config.yaml`, `analysis/decision.py`, `brokers/trading212.py` were never touched. No production changes made.

**Caveat, stated plainly:** this tests exactly the two mechanisms and parameter choices specified (15% hard gate; proportional 1.0-coefficient re-rank discount, both on the 50-day MA specifically). It does not rule out every conceivable extension-aware admission rule — a different threshold, a softer gate, a different discount function, or combining extension with the momentum-strength bands from earlier investigations could behave differently — but none of those were tested here, and per this project's own standing discipline, a real descriptive finding is not itself grounds to keep tuning parameters until something clears the bar.

## 7. Confirmation

- **`analysis/backtest.py` was temporarily modified (an additive, marked, isolated change), then reverted** — `git diff analysis/backtest.py` confirmed clean after `git checkout -- analysis/backtest.py`, verified directly, not assumed.
- No other production files were modified: `config.yaml`, `analysis/decision.py`, `analysis/volatility.py`, `brokers/trading212.py` were all read-only throughout.
- No custom candidate pool or monkeypatched `is_member()` — trade generation for both Step 1 (reused, already-verified fields) and Step 2 (the real, temporarily-extended engine function, parity-verified at defaults) used genuine production logic throughout.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries.
