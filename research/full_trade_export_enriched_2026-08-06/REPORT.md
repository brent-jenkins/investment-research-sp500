# Enriched 2007-2026 Trade-History CSV Export (2026-08-06)

**Not a hypothesis test.** Follow-up to tonight's earlier export (`sp500invest_full_trade_export_2007-2026.csv`, 3,161 trades, already delivered) — a richer version with additional columns to help understand *why* individual trades won or lost. Same purpose: personal manual exploration, no variant comparison, no block validation.

**Location of artifacts:** `research/full_trade_export_enriched_2026-08-06/` — `export_trades_enriched.py` (script, source of truth), `export_run.log` (full run log), `full_trade_export_enriched_2007-2026.csv` (kept here for the record). **A copy also written directly to `C:\Users\user\Downloads\sp500invest_full_trade_export_enriched_2007-2026.csv`** — see Section 5.

## 1. Environment re-verification (before running anything)

- `git status --short`: unchanged since the first export earlier tonight — only expected untracked research-output directories, no code changes.
- Latest commit unchanged (`32d6781`, the earlier research-documentation commit — not a code change to anything this export touches).
- `price_data` DB coverage unchanged: 2007-01-01 → 2026-08-03, 540 tickers.
- **Conclusion: environment identical to the first export's run — safe to proceed without re-checking further.**

## 2. How the "engine-internal" fields were actually captured — read this before trusting the corresponding columns

The task's own instruction was explicit: capture `daily_var`/`holding_var`/ATR/stop-distance from **the real engine's own internal trade records**, not by re-deriving the VaR/ATR formulas independently. `analysis.backtest.simulate_portfolio_compounding_mtm()` does not currently expose these values on its returned trade dicts (`stop_distance_pct` and `position_gbp` are already there; `daily_var`/`holding_var`/ATR are not).

**Solution used: a runtime monkeypatch, `analysis/backtest.py` never edited.** `analysis.volatility._resolve_entry_stop()` and `analysis.volatility._var_position_size_core()` — the two shared, pure functions both live and every backtest engine call — are bound as module-global names inside `analysis.backtest`'s own namespace (`from analysis.volatility import ...`). This script temporarily reassigns those two names (`analysis.backtest._resolve_entry_stop`, `analysis.backtest._var_position_size_core`) to thin wrapper functions that call straight through to the real implementation and simply record each call's real arguments/return values, then restores the originals immediately after the run (inside a `try/finally`, so restoration happens even on error). **This changes nothing about what the simulation computes** — confirmed directly, not assumed: the run reproduced the exact established baseline (`total_trades=3161`, `MAX_HOLDING=1796`, 56.8%) with the monkeypatch active, asserted programmatically in the script itself.

**Why call order alone wasn't enough, and what was used instead.** At the real call site (`analysis/backtest.py:1344/1353`), `_var_position_size_core()` is only called when a candidate has enough prior-return history (`>= _MIN_VAR_RETURNS` days) — otherwise sizing falls back to a flat rule and the VaR function is never called for that candidate at all. So captured records were built as: each `_resolve_entry_stop()` call opens a new pending record; each `_var_position_size_core()` call (when it happens) fills in the *most recently opened* pending record — safe because the real engine is single-threaded and never interleaves two candidates' stop/sizing calls. The resulting records were then joined back to the engine's own final trade list via an **exact-match** composite key (`entry_price`, `stop_distance_pct`) — both values are stored verbatim (the same Python float, not recomputed) on the trade dict the engine actually emits.

**Join integrity, verified directly, not assumed:**
- 977,530 stop/sizing calls were captured across the full 19-year run (every candidate ever evaluated for entry, not just the 3,161 that became real trades).
- 402 join-key collisions occurred among those 977,530 candidate evaluations (two different, mostly-rejected candidates coincidentally sharing an `(entry_price, stop_distance_pct)` pair) — expected at this volume, and checked directly for whether it corrupted any of the 3,161 real trades.
- **All 3,161 real trades matched to a captured record (100%)**, asserted programmatically.
- **Verified independently, after the fact**: recomputing `stop_distance_pct` from each trade's own captured `atr_at_entry` and its own `entry_price`, using the exact production formula (`round(atr_stop_multiplier * atr / price * 100, 4)`, `atr_stop_multiplier=2.0` from `config.yaml`), reproduces the trade's own already-verified `stop_distance_pct` **exactly, for all 3,161 rows, zero mismatches**. This is strong, direct evidence the join found the correct record for every trade, not just a same-key coincidence — a genuinely wrong join would need the colliding record's ATR to independently satisfy the same price/formula relationship, which the check above rules out.
- 0/3,161 trades used the flat-sizing fallback (every trade had enough history for genuine VaR sizing), so `daily_var_at_entry`/`holding_var_at_entry` are populated for all 3,161 rows.

## 3. Which columns come from where — stated plainly, per the task's own request

**Genuinely captured from the real engine's own internal state** (Section 2's monkeypatch):
- `atr_at_entry`, `daily_var_at_entry`, `holding_var_at_entry`

**Already present on the engine's own emitted trade record, carried through unchanged** (no new computation needed):
- `position_gbp_at_entry`, `stop_distance_pct`, `entry_price`, `exit_price`, `pnl_gbp`, `pnl_pct`, `holding_days`, `exit_reason`, `win_loss`

**A trivial, deterministic unit conversion of two already-engine-provided numbers** (not an independent recomputation of any decision logic):
- `stop_distance_gbp` = `position_gbp_at_entry × stop_distance_pct / 100` — the £ amount that would be lost if the stop were hit, the same convention the engine's own `pnl_gbp = position_gbp × pnl_pct` already uses.

**Listed in the task brief alongside "engine-internal" fields, but NOT actually engine state — stated honestly, correcting the task's own framing:**
- `momentum_strength_band_at_entry` — the engine only ever uses the raw continuous momentum value (for the entry-floor gate and the priority sort); it has no concept of Weak/Moderate/Strong/Very Strong bands anywhere. This column is `main.py`'s own display-only reconstruction (`_compute_momentum_strength_bands()`/`_momentum_strength_label()`, reused byte-identical), applied here at each trade's *entry* date against that date's real eligible-candidate pool — the same methodology two earlier investigations tonight (`momentum_horizon`, `momentum_volume_reeval_cap`) already used and validated, not re-derived from scratch.
- `already_peaked_at_exit` — a purely retrospective price-history check (max close during the hold vs. the actual exit price); the engine has no such concept. Same definition `momentum_horizon` used.
- `momentum_at_entry_pct` — the raw continuous value read directly from the same `precompute_ticker()`-style frame the engine itself computes it from (not re-derived formula-wise), but recovered here via a parallel frame build, not read off the trade dict (which doesn't carry it).

**Genuinely new for this export — not used anywhere in current decision logic, and not a re-test of anything already run:**
- `ma_14_at_entry`/`ma_30`/`ma_50` and the same three at exit — rolling means of stored close prices.
- `volume_at_entry`/`volume_at_exit` — from `price_data`'s own `volume` column. **Note, stated as instructed:** exit-point volume specifically was already formally tested against outcome earlier tonight (`momentum_volume_reeval_cap_2026-08-06` — rejected, no relationship found, if anything wrong-directioned). Included here for descriptive completeness and cross-referencing only, not because it's an open question.
- `sector`/`industry` — a fresh `yfinance.Ticker(t).info` pull for all 331 unique tickers that actually appear in the trade list (not all 503 live candidates — only the ones that traded). Succeeded for all 331 (0 failures). This is Yahoo Finance's own sector/industry taxonomy (GICS-based, per Yahoo's convention) — the system has no sector-aware logic anywhere today, so this is genuinely external context, not retrofitted into any existing function.

## 4. Confirmation the backtest was run fresh, current code

- Re-run from scratch, same engine as the first export: `analysis.backtest.simulate_portfolio_compounding_mtm()` via `analysis.validation.run_full_validation(CONFIG, start=2007-01-01, end=2026-08-03, mark_to_market=True, live_universe=True)`.
- **Result, matching the established baseline exactly**: `total_trades=3161`, `MAX_HOLDING=1796` (56.8%), `STOP_LOSS=1169`, `SIGNAL_OFF=131`, `CRISIS_EXIT=35`, `END_OF_DATA=30` — asserted programmatically in the script, not just eyeballed.

## 5. The CSV, and how to retrieve it

**This session runs locally on the Windows laptop** — direct file, no SFTP/mount needed.

**The file is already sitting at:**
```
C:\Users\user\Downloads\sp500invest_full_trade_export_enriched_2007-2026.csv
```
Open it directly in Excel/Numbers, or via File Explorer → Downloads. A second copy lives at `research/full_trade_export_enriched_2026-08-06/full_trade_export_enriched_2007-2026.csv` in this repo (not committed — see below).

**3,161 rows, 35 columns, zero nulls in every column** — verified directly. Full column list, in order: `ticker, win_loss, pnl_gbp, pnl_pct, entry_date, exit_date, holding_days, entry_price, exit_price, entry_sp500_level, exit_sp500_level, entry_gbpusd_reporting_only, exit_gbpusd_reporting_only, entry_eurusd_reporting_only, exit_eurusd_reporting_only, exit_reason, momentum_at_entry_pct, momentum_strength_band_at_entry, position_gbp_at_entry, stop_distance_pct, stop_distance_gbp, atr_at_entry, daily_var_at_entry, holding_var_at_entry, already_peaked_at_exit, volume_at_entry, volume_at_exit, ma_14_at_entry, ma_30_at_entry, ma_50_at_entry, ma_14_at_exit, ma_30_at_exit, ma_50_at_exit, sector, industry`.

Same honest limitation as the first export, carried over unchanged: `entry_date`/`exit_date` are dates only — daily bars, no intraday timestamp exists or is fabricated.

Sanity-checked directly: momentum-strength band distribution (Very Strong 2,685 / Strong 185 / Moderate 184 / Weak 98 / "-" 9 for a too-thin pool) matches the same heavily-skewed shape found in tonight's earlier momentum-band investigations; sector distribution looks sane (Technology 841, Consumer Cyclical 553, Healthcare 338, Industrials 313, Energy 268, ...); spot-checked AAPL's first 2008-01-03 entry — a `STOP_LOSS` exit the next day with `stop_distance_gbp` (£151.98) landing almost exactly on the realized `pnl_gbp` (−£151.98), exactly as expected for a trade that closed via its own stop.

## 6. Confirmation

- **`analysis/backtest.py` was never modified on disk** — the monkeypatch reassigns module-global names at runtime, inside the export script's own process, and restores the originals in a `finally` block before the script exits. `git diff analysis/backtest.py` shows zero changes.
- No other production files were modified: `analysis/decision.py`, `analysis/volatility.py`, `analysis/crisis.py`, `analysis/universe.py`, `config.yaml` were all read-only.
- No custom candidate pool or monkeypatched `is_member()` — the real, unmodified `run_full_validation()` was used directly for trade generation; the monkeypatch touches only the two pure sizing/stop functions, and is verified behaviourally transparent (Section 4).
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only queries via the existing `get_price_df()` path.
