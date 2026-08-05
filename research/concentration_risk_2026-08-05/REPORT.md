# Sector/Momentum Concentration Risk Investigation — Final Report (2026-08-05)

**Bottom line: researched, not adopted.** Simultaneously-open, correlated positions are real, large, and structurally present in this system almost every trading day — the underlying VaR sizing scheme's independence assumption is genuinely and substantially wrong on a typical day (realized joint risk runs ~2x what independent sizing implies). But the *average-day* cost of this, isolated from general market-volatility regime, is not robust — it is small, inconsistent across robustness checks, and reverses sign on the more conservative test. A real, but only partial and non-dominant, effect does survive in the extreme *tail* (the worst single days are meaningfully enriched for high joint-risk episodes). A concrete correlation-aware admission constraint was built and tested: it does **not** dominate the current baseline on every metric in a single reset-at-boundary comparison (better drawdown, but worse CAGR/PF/Sharpe) — it does not clear this project's own bar for even proceeding to the expensive 16-block validation, so it was **not** block-validated and is **not recommended for adoption**. `config.yaml` was not touched; nothing was deployed or committed.

## 0. Environment verification

`database/investment_research.db`: `price_data` covers **540 distinct tickers**, **2007-01-01 to 2026-08-03** — confirmed directly. `analysis.universe.get_live_stock_candidates()` returns **503 live candidates** as of 2026-08-03. `database/` is untracked and gitignored (`database/*.db` in `.gitignore`).

## 1. No decision-engine parity concern — stated explicitly

Part 1/2 called `analysis.backtest.simulate_portfolio_compounding_mtm()` **directly, unmodified** — no custom candidate pool, no monkeypatched `is_member()`, so CLAUDE.md's parity-verification rule (scoped to scripts that build a custom universe or monkeypatch decision logic) doesn't apply. Part 3's `concentration_constraint_engine.py` is a verbatim copy of that same function with one isolated, marked change (a haircut inserted after the real `_var_position_size_core()`); fidelity was **confirmed directly**: with the haircut disabled it reproduces the real function byte-for-byte identical on a 60-ticker real-data subset (`self_check.py`: `2856 == 2856 trades, BYTE-IDENTICAL: True`).

## 2. Part 1 — Quantified historical concentration

**Backtest convention: CONTINUOUS WALK** (stated explicitly — a track-record/frequency question, not a variant comparison). Real, unmodified engine, live universe, full history: **3,161 trades**, 2007-01-01 to 2026-08-03, CAGR 13.6%, PF 1.65, peak-relative max DD -28.53%. Full trades preserved at `results/full_trades_continuous.json`.

"Open" convention: `entry_date <= t < exit_date` (half-open — the exit day is already realized into `pnl_gbp`, not double-counted). All correlation/volatility windows use data strictly before day *t*.

**How often** (4,673-day master calendar, 2008-01-03 to 2026-08-03):

| | % of days |
|---|---:|
| n_open >= 1 | 99.53% |
| n_open >= 2 | 99.27% |
| n_open >= 3 | 99.08% |
| n_open >= 4 | 98.82% |
| n_open >= 5 | 98.35% |

**Holding 2+, even 5+, simultaneously-open positions is the near-permanent normal state, not an occasional episode.** With `per_trade_pct=0.05`/`max_total_deployed=0.5`, this system is essentially always running a basket of ~5-10 concurrent positions. The SNDK/WDC/MU/LITE/STX incident was an extreme cluster, not a different regime from every other day.

**How correlated** — trailing pairwise correlation of open positions' daily log returns:

| Window | n | mean | p5 | p25 | median | p75 | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20d | 4,626 | 0.366 | 0.137 | 0.255 | 0.351 | 0.459 | 0.648 |
| 60d | 4,603 | 0.359 | 0.184 | 0.272 | 0.342 | 0.422 | 0.613 |
| 120d | 4,568 | 0.360 | 0.201 | 0.277 | 0.339 | 0.430 | 0.589 |

Roughly unchanged for n_open>=3/4 — in the range typical of any diversified US-equity basket (market-beta correlation is commonly ~0.3-0.4), not dramatically elevated on average.

**How much this matters for aggregate risk** — weighted by entry-time `position_gbp`:

Diversification ratio (`DR = sum(w_i*sigma_i) / sqrt(w'Cov w)`; DR=1 = no benefit): n=4,603, mean=**1.589**, p5=1.233, median=1.578, p95=1.983 — real but incomplete diversification.

```
indep_gap_60d = P_real/P_ind - 1
P_real = sqrt(w' Cov w)                 # realized 60d covariance
P_ind  = sqrt(sum(w_i^2 * sigma_i^2))   # zero-cross-correlation assumption
```
n=4,603, **mean=1.067**, p5=0.542, median=1.032, p95=1.717.

**On a typical day, realized joint portfolio risk runs ~2.07x what independent VaR sizing implies — ~2.72x at the 95th percentile.** This is a real, structural, previously-undocumented gap between the sizing formula's implicit assumption and reality — worth recording regardless of whether it costs performance.

**Worst episodes**: the 25 worst single days in the 19-year history are dominated by a **mid-2026 semiconductor/storage/tech cluster** — SNDK, WDC, MU, LITE, STX, LRCX, TER, AMD, INTC, DELL, AMAT, COHR, GLW, CIEN, MRVL, KLAC, FLEX, ECHO, FIX — 9 of the top 25 (2026-06-05 to 2026-07-28), each holding 14-18 open positions with `indep_gap_60d` of 1.4-2.1, well above the ~1.07 population mean. Concretely the same failure shape as the motivating incident. (`results/worst_episodes.csv`)

## 3. Part 2 — Isolating the cost

Approach: regression-based isolation controlling for contemporaneous S&P 500 realized vol (`mkt_vol20`) and drawdown-from-high (`mkt_dd`), cross-checked with matched-tercile comparison. Sample: n_open>=2, complete regressors, n=4,603.

**Average-day magnitude — NOT robust:**

| | raw r vs avg_corr_60d | residual Pearson (control mkt_vol20) | residual Spearman | multivariate OLS coef |
|---|---|---|---|---|
| `abs(daily return)` | r=0.147, p=1.1e-23 | r=0.055, p=1.9e-4 | r=0.014, **p=0.33 n.s.** | coef=+0.00216, p=0.0078 (tiny, R2=0.067) |
| `downside-only return` (the real cost) | r=0.057, p=1.0e-4 | r=0.006, **p=0.70 n.s.** | r=**-0.184**, p=1.6e-36 (wrong sign) | coef=**-0.00055, p=0.45 n.s., wrong sign** |

Matched-tercile t-test on downside return: not significant in any of 3 vol terciles (p=0.67, 0.72, 0.18).

**For the measure that actually matters — downside risk — there is no significant positive relationship once market volatility is controlled for.** This is exactly the naive co-occurrence failure the task warned against, caught directly.

**Tail-specific — real but partial, non-dominant:** Given Section 2's worst-episode pattern, a dedicated logistic-regression follow-up was run (`tail_risk_isolation.py`, 3 bad-day thresholds).
- Raw enrichment: 40% of the worst 25 days had `indep_gap_60d` above the sample's own 90th percentile (vs 10% null), 32% above the 95th (vs 5% null). Binomial p=7.9e-05 / p=2.0e-05.
- Within-vol-regime matched comparison (Mann-Whitney): significant for `indep_gap_60d` (not raw correlation) in 2 of 3 vol terciles at 2 of 3 thresholds (e.g. mid-vol p5 threshold p=0.00062; mid-vol -2% threshold p=0.0090; high-vol -2% threshold p=0.032).
- Full multivariate logistic regression (the strictest test): coefficient positive at all 3 thresholds but **never reaches p<0.05** (p=0.13-0.23) — plausibly underpowered (13-231 tail events).

**Conclusion: a real, non-trivial tail pattern survives within-regime matching but not the strictest multivariate test — genuinely mixed, not a clean "yes."**

## 4. Part 3 — Constraint design and testing

**Design**: correlation-aware sizing haircut layered on the real shared decision functions (not a fork of entry/exit logic). After the real `_var_position_size_core()` sizes a candidate, compute its trailing-60d correlation against every open position, take the max, and if it exceeds `corr_haircut_threshold`, scale position size by `corr_haircut_factor`. Fidelity-confirmed byte-identical when disabled.

**Convention: RESET-AT-BOUNDARY** (stated explicitly — variant comparison). Standard test-period reset at 2020-01-01 to 2026-08-03.

| Variant | Trades | CAGR | DD (fixed) | DD (peak-rel) | PF | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| **Baseline** (real, unmodified) | 1,270 | **19.29%** | -27.56% | -12.47% | **1.74** | **0.67** |
| Block entries >0.7 corr. with any open position | 1,235 | 16.27% | **-18.90%** | **-9.78%** | 1.67 | 0.574 |

**Does not dominate the baseline** — better drawdown, worse CAGR/PF/Sharpe. Does not clear this project's own bar ("dominated baseline on every metric" — the standard the earlier `var_confidence` investigation used before proceeding to block validation) for even attempting the expensive 16-block check.

Two softer 0.5x-haircut variants were launched but killed after >1h45m with zero output (vs 348s baseline / 5,601s full-block) — a partial haircut keeps more positions open than an outright block, snowballing the per-day correlation cost. Since the strongest version of the intervention already failed to dominate, the weaker variants were not pursued further.

**No block validation was run** — per this project's established practice, a non-dominant single-comparison result isn't carried forward. `analysis.backtest.make_blocks()` / `analysis.validation._block_passes()` were imported directly (never re-derived) into `block_validate_constraint.py`, ready for future use, but unused here.

## 5. Recommendation

**Researched, not adopted.**
- The structural fact (near-universal, correlated concurrent positions; VaR sizing's independence assumption wrong by ~2x on a typical day) is real and worth recording, independent of whether a fix is warranted.
- The average-day isolated cost does not survive controlling for market volatility.
- The tail-specific pattern is real but mixed — survives matched comparison, not the strictest multivariate test.
- The one constraint actually built and tested does not dominate baseline in a single reset-at-boundary comparison, so per this project's own practice it was not carried to block validation.
- **`config.yaml` was not touched. Nothing was committed, pushed, or deployed anywhere.**

## 6. Caveats

- The semiconductor sanity check (`results/semiconductor_sanity_check.json`) is weaker than intended — it measured the *general* basket's correlation on days when 2+ semi names co-occurred, not the semi names' own pairwise correlation. Flagged as a limitation; doesn't affect the primary findings (Sections 2-3), which never depended on it.
- `indep_gap`/DR weighting uses entry-time cost-basis `position_gbp`, not mark-to-market — a stated simplification.
- The tail logistic regression is likely underpowered (13-231 events) — reported honestly as inconclusive rather than as proof of either "effect" or "no effect."

## Reproducibility

All formulas stated as literal code (Sections 2, 4); `_block_passes()` quoted directly from `analysis/validation.py`. Full trade-level and day-level output preserved under `research/concentration_risk_2026-08-05/results/`: `full_trades_continuous.json` (3,161 trades), `daily_panel.csv` (4,673-row day-by-day panel), `concentration_summary.json`, `isolation_analysis.json`, `tail_risk_isolation.json`, `worst_episodes.csv`, `semiconductor_sanity_check.json`, `reset_baseline.json`, `reset_h70_skip.json`. Engines: `run_full_backtest.py`, `analyze_concentration.py`, `tail_risk_isolation.py`, `concentration_constraint_engine.py`, `self_check.py`, `reset_sweep_constraint.py`, `block_validate_constraint.py`.

**Confirmed: no file outside `research/concentration_risk_2026-08-05/` was modified.** `config.yaml`, `deploy.sh`, `webhook_listener.py`, `.github/workflows/`, `tests/test_deploy_*.py` were not touched. Nothing was committed or pushed. Nothing was deployed anywhere.
