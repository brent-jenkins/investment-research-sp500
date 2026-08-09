# Admission-ranking promotion — production-code reproduction of Arm A/B (2026-08-09)

**Requirement 4 of the promotion task**: re-run `research/relative_ranking_prereg_2026-08-09`'s own
Arm A / Arm B comparison via the PRODUCTION code path (`analysis/backtest.py`'s real, unmodified
`simulate_portfolio_compounding_mtm()`, driven only by the new `trading212_invest.admission_ranking_mode`
config key) — never the disposable research engine (`relative_ranking_engine.py`) — on Pool 2 CLEAN, and
confirm it reproduces the study's own trade files before anything else ships.

## Environment

Local DB copied fresh from the main checkout (`c:\GitHub\investment-app\database\investment_research.db`,
743,059,456 bytes) into this worktree immediately before running. Confirmed directly, not assumed:
`ticker_data_quality` = 612 CLEAN / 188 QUARANTINED / 35 NEEDS_REVIEW, `price_data` MAX(date) =
2026-08-07 — exactly matching both CLAUDE.md's last-confirmed production state and the original study's
own stated environment ("612 CLEAN, price data through 2026-08-07"). Pool 2 CLEAN
(`get_research_stock_candidates() ∩ CLEAN`) = 612 tickers, matching the study's own pool.

## Method

`verify.py`: builds the Pool 2 CLEAN cache fresh via `analysis.backtest.precompute_ticker()`/
`slice_ticker()` (identical to the study's own `step0_build_caches.py`), then calls the real
`simulate_portfolio_compounding_mtm(caches, config, gspc_close, per_trade_pct=0.05, max_deployed_pct=0.70)`
twice — once with `admission_ranking_mode` absent (Arm A, production default) and once with it set to
`"relative_rank"` (Arm B, the promoted mode) — nothing else changed. Compared against the study's own
`trades_pool2_clean_A.csv`/`trades_pool2_clean_B.csv`.

**First comparison pass showed 843/809 "mismatches" despite trade count and total P&L matching to the
penny for both arms — investigated before being trusted, not accepted or dismissed.** The initial
`_compare()` diffed rows in raw insertion order; `simulate_portfolio_compounding_mtm()`'s trade list comes
from a day-by-day event walk, not a stable per-ticker sort, so two independent runs (this one and the
study's own) are not guaranteed to emit the same trades in the same row order even when the trade sets
are identical. Re-compared after sorting both frames onto a canonical `(ticker, entry_date, exit_date)`
order: **0 real differences, both arms.**

## Result

| | Trades | Total P&L (£) | Row-level match after canonical sort |
|---|---|---|---|
| Arm A (absolute_momentum) | 4,919 | 103,926.50 | **0 mismatches / 4,919** |
| Arm B (relative_rank) | 4,876 | 166,830.11 | **0 mismatches / 4,876** |

Both trade count and total P&L match the study's own files exactly. Arm A's own figures (4,919 trades,
£103,926.50) additionally match the study's own separately-reported degenerate-case parity check
(`relative_ranking_engine.py` with `_sort_key="momentum"` vs. raw production, byte-identical, same two
numbers) — three independent runs (the study's original production run, the study's research-engine
degenerate check, and this promotion's production-code run) now agree exactly.

**Conclusion: the promoted production code (`analysis/signals.py:compute_relative_rank_series()`,
`analysis/decision.py:resolve_admission_rank_value()`/`resolve_admission_ranking_mode()`,
`analysis/backtest.py`'s `_admit_group()` wiring) reproduces Arm B's study result exactly, on the same
pool, via the real code path — not the disposable research engine.** Nothing in the promotion changed
backtest behaviour relative to the research finding it promotes. Requirement 4 is satisfied; the
promotion proceeds.

## Files

`verify.py`, `trades_pool2_clean_A_reproduced.csv`, `trades_pool2_clean_B_reproduced.csv`. No
`_cache_pool2_clean.pkl` committed (large, reproducible from the DB per this project's own convention).
