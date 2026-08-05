# VaR Position-Sizing Investigation — Final Report (2026-08-05)

**Bottom line: researched, not adopted.** No tested `var_confidence` value or time-scaling exponent robustly, defensibly beats the current production settings (`var_confidence=0.95`, sqrt scaling) once checked against genuine block-independent validation. One candidate (`var_confidence=0.925`) looked like a clean win in a single reset-at-boundary comparison — exactly the failure pattern this task warned about — and did not survive the 16-block check. `config.yaml` was not touched; nothing was deployed.

## 1. Environment verification (done first)

`database/investment_research.db` (~527MB): `price_data` covers **540 distinct tickers**, **2007-01-01 to 2026-08-03**, 2,454,903 rows — current, not stale. `analysis.universe.get_live_stock_candidates()` returns **503 live candidates** as of 2026-08-03, the same dynamic S&P 500 universe live trading uses. The DB file is untracked/gitignored from any commit path (confirmed via `git status`).

## 2. A structural finding that reframes the whole investigation

Before running anything, checked what `holding_days` value actually reaches `_var_position_size_core()` in production:

```
distinct max_hold values across 503 live candidates: {30}
```

**Every live stock trade is sized against the same constant, `holding_days=30`** — `_var_position_size_core()` is called with `st["max_hold"]` (the ticker's *maximum allowed* holding period), never the trade's own eventual *realized* holding period, which can't be known at entry time. This means the scaling-exponent question isn't "does this system size a 3-day hold differently from a 25-day hold" (it structurally can't, regardless of exponent) — it's "is 30 days the right fixed horizon to size risk against." Changing the exponent is mathematically equivalent to uniformly rescaling `max_risk_pct` by `30^k` for every trade.

## 3. Real holding-period distribution (from a full 2007–2026 continuous-walk baseline, 3,161 trades — continuous-walk convention, used here only to characterize the distribution, not to compare variants)

| | |
|---|---|
| Median holding_days | **30** (the cap) |
| Mean | 23.0 |
| p25 / p75 | 14 / 30 |
| MAX_HOLDING exits | **56.8%** of all trades |
| STOP_LOSS | 37.0% |
| SIGNAL_OFF / CRISIS_EXIT / END_OF_DATA | 4.1% / 1.1% / 0.9% |

Over half of all trades run the full 30 days. This is heavily right-skewed, not evenly spread.

## 4. Empirical fit of the scaling exponent (independent of any backtest)

For 120 randomly-sampled live tickers, fit `log(|VaR_N|) = intercept + k*log(N)` across N in {1,2,3,5,7,10,15,20,25,30,33} using real rolling N-day historical-VaR percentiles (`var_confidence=0.95`, matching config):

```
k (percentile-based): mean=0.4721  median=0.4748  std=0.0404  (96.7% of tickers in [0.4, 0.6])
k (std-dev-based, cross-check): mean=0.4722  median=0.4693
|VaR_30|/|VaR_1| ratio: median=5.100  (sqrt(30)=5.477, linear(30)=30, no-scale=1)
```

**Real multi-day dispersion tracks sqrt(time) almost exactly** — the current hardcoded 0.5 sits within 5% of the empirically best-fit value. This is a genuine, reassuring finding: the sqrt design choice is empirically well-grounded on this system's own data, not an arbitrary convention.

## 5. Backtest convention — stated explicitly

**Reset-at-boundary** throughout (per CLAUDE.md's "Two backtest conventions" — this is a variant-comparison question, not a track-record report), at two levels:
- **Primary sweep**: one reset at 2020-01-01, walking only the test period to 2026-08-03.
- **Block validation**: 16 genuinely independent resets (fresh £50,000, zero positions, each truncated to `[block_start, block_end]` with a corrected `END_OF_DATA`) — not a bucketing of one continuous walk (which would still let earlier-block compounding contaminate later blocks).

## 6. var_confidence sweep — full results (reset-at-boundary, test period, live universe, sqrt scaling fixed)

| var_confidence | Trades | CAGR | DD (fixed) | DD (peak-rel.) | PF | Sharpe | Win rate | Ending £ |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.85 | 1,016 | 21.44% | -33.54% | -10.43% | 1.82 | 0.70 | 49.0% | £179,698 |
| 0.90 | 1,060 | 21.17% | -35.19% | -11.29% | 1.79 | 0.66 | 48.4% | £177,035 |
| **0.925** | 1,120 | **22.31%** | -29.43% | -9.84% | **1.89** | **0.76** | **51.1%** | **£188,334** |
| **0.95 (current)** | 1,270 | 19.29% | -27.56% | -12.47% | 1.74 | 0.67 | 48.4% | £159,699 |
| 0.975 | 1,521 | 20.03% | -23.87% | -10.06% | 1.85 | 0.72 | 50.0% | £166,369 |
| 0.99 | 1,871 | 17.36% | -21.44% | -7.12% | 1.72 | 0.63 | 48.7% | £143,479 |
| 0.995 | 2,103 | 16.78% | -17.80% | -8.25% | 1.74 | 0.64 | 49.3% | £138,827 |

0.925 and 0.975 dominated baseline on every metric in this single comparison -> flagged **promising**, taken to block validation.

## 7. Scaling-approach sweep — full results (var_confidence=0.95 fixed)

| Scaling | k | Trades | CAGR | DD (fixed) | DD (peak-rel.) | PF | Sharpe | Ending £ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No scaling | 0.0 | 999 | 19.25% | -34.56% | -9.93% | 1.77 | 0.67 | £159,393 |
| **Sqrt (current)** | **0.5** | **1,270** | **19.29%** | **-27.56%** | -12.47% | 1.74 | 0.67 | £159,699 |
| Fitted (empirical) | 0.475 | 1,204 | 20.35% | -34.33% | -10.64% | 1.74 | 0.65 | £169,290 |
| Linear | 1.0 | 4,865 | 14.15% | -10.84% | -5.89% | 1.79 | 0.67 | £119,499 |

**No scaling alternative beats sqrt.** No-scaling is a CAGR wash with worse drawdown; fitted-k trades better CAGR for worse fixed-denominator drawdown; linear is a fundamentally different, lower-return profile, not a Pareto win. None was promising enough to warrant block validation — this agrees with Section 4's independent evidence.

## 8. Fidelity/parity verification (checked before trusting anything above)

- **Engine fidelity**: the sweep harness with inert defaults reproduces the real, unmodified `simulate_portfolio_compounding_mtm()` byte-for-byte (306/306 trades identical, 60-ticker/1.5yr subset).
- **Scaled-core fidelity**: `_var_position_size_core_scaled(k=0.5)` matched the real `_var_position_size_core()` across 500 random-input trials, all_match=True.
- **Block-truncation mechanism**: smoke-tested — zero trades outside window, `END_OF_DATA` correctly keyed to block end, not each ticker's own last row.
- **Downstream parity, from real sweep trades**: 30 sampled `MAX_HOLDING` trades all respected `get_max_holding_days()`; 30 sampled `STOP_LOSS` trades' `exit_price` matched the real stop formula to the cent; 12 sample entries' `holding_var` exactly equaled `daily_var * sqrt(30)` via the real `_var_position_size_core()` called directly with real historical log returns.

## 9. 16-block independent validation — the decisive check

```python
def _block_passes(strat_metrics, sp_dd):  # imported directly from analysis.validation, not re-derived
    dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
    pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
    return bool(dd_ok and pf_ok)
```

**Pass rate: all three variants tied at 14/16** (baseline, 0.925, 0.975) — the same two blocks fail for every variant (block 0: pre-GFC, block 1: GFC-acute), matching this project's own well-established "blocks 0/1 always fail" pattern (a sanity check on methodology, not a new finding).

**But the win-count and mean-CAGR breakdown tell a different story than Section 6's single comparison:**

| | Win count (16 blocks) | Mean CAGR (16 blocks) | Median CAGR (16 blocks) |
|---|---:|---:|---:|
| **0.95 (current production)** | **9/16** | **12.94%** | 15.25% |
| 0.925 | 4/16 | 12.74% | 15.98% |
| 0.975 | 3/16 | 11.44% | 15.64% |

**`var_confidence=0.925`'s apparent win in Section 6 does not survive.** The current production baseline (0.95) wins the plurality of independent blocks outright (9/16, vs. 4/16 and 3/16) and has the highest mean CAGR across blocks — the exact opposite of what the single reset-at-boundary comparison suggested. `0.925`'s only real edge is a slightly higher *median* CAGR (15.98% vs. 15.25%), which is far weaker evidence than the clean, simultaneous dominance on every metric it appeared to show in Section 6. `0.975` is unambiguously the weakest of the three under block-independent testing (fewest wins, lowest mean CAGR) despite also looking good in Section 6. This is the identical failure shape CLAUDE.md documents for the retracted 5-day re-entry-cooldown finding and the ADX<20 gate: a favorable result in one continuous/single-reset comparison that is a coincidence of one historical path, not a robust property.

## 10. Recommendation

**(b) Researched, not adopted.** Neither a different `var_confidence` nor a different time-scaling exponent is genuinely, robustly better than the current `var_confidence=0.95` / sqrt-scaling production settings:
- The scaling exponent is independently, strongly confirmed correct by real price-dispersion data (Section 4) — no sweep variant beat it, consistent with that independent evidence.
- The `var_confidence=0.925` candidate that looked like a clean win in the primary sweep failed to survive block-independent validation — production's current 0.95 wins more blocks and has a higher mean CAGR across the 16 genuinely independent windows.
- No config change was made. **`config.yaml` was not touched, nothing was committed, nothing was deployed.**

## Reproducibility

Every formula used is stated as literal code above (Section 9), not paraphrased. Full trade-level output (not just aggregates) for all 10 sweep variants and all 3 block-validation runs is preserved on disk under `research/var_sizing_sweep_2026-08-05/results/` (`reset_*.json`/`block_*.json` — complete trade lists and per-block metrics), together with the engine (`var_sizing_engine.py`), fidelity checks (`self_check.py`, `smoke_block_trunc.py`), parity cross-check (`parity_cross_check.py`), and the empirical exponent fit (`fit_scaling_exponent.py`, `fitted_exponent_result.json`) — nothing was deleted.

**Files touched, all under `research/var_sizing_sweep_2026-08-05/`:** `var_sizing_engine.py`, `reset_sweep_worker.py`, `block_validate_worker.py`, `self_check.py`, `smoke_block_trunc.py`, `fit_scaling_exponent.py`, `parity_cross_check.py`, `fitted_exponent_result.json`, `results/*.json`/`results/*.log`, and this `REPORT.md`. No file outside this directory was modified.
