# Full 2007-2026 Trade-History CSV Export (2026-08-06)

**Not a hypothesis test.** This is a data export for manual, personal exploration outside the usual experiment pipeline — a single, rich CSV covering every trade the current, unmodified production logic would generate across the full 2007-2026 history. No variant comparison, no block validation; the standard for those does not apply here.

**Location of artifacts:** `research/full_trade_export_2026-08-06/` — `export_trades.py` (script, source of truth), `export_run.log` (full run log), `full_trade_export_2007-2026.csv` (the export itself, kept here for the record). **A copy also written directly to `C:\Users\user\Downloads\sp500invest_full_trade_export_2007-2026.csv`** — see Section 4 for how to open it.

## 1. What the two FX rates are actually used for — confirmed by code trace, not assumed

Grepped every module under `analysis/` (`decision.py`, `backtest.py`, `signals.py`, `universe.py`, `crisis.py`, `volatility.py`, `validation.py`) for `GBPUSD`, `EURUSD`, `_fx_rates`, `_to_gbp`: **zero matches.** Neither FX rate is read by, or has any path into, entry logic, exit logic, position sizing, momentum, the signal calculation, or the crisis check, anywhere.

Every real usage of the two rates lives in exactly two places, both currency-conversion/reporting only:

- **`brokers/trading212.py`** — `Trading212Client._fx_rates()` (reads the latest `GBPUSD=X`/`EURUSD=X` close from `price_data`) and `_to_gbp()` (converts a USD- or EUR-denominated price/P&L into GBP). Called from `get_instrument_price_gbp()` (converting a live position's native-currency price to GBP for sizing display), the fill-price-to-GBP conversion inside order polling (`actual_value = ... _to_gbp(fill_price * effective_qty, ...)`), and the account-summary P&L conversion.
- **`output/performance.py`** — `_t212_ccy_to_gbp()` (a thin wrapper around the same two functions), used to display a position's current value and P&L in GBP on the Dashboard.

**Conclusion: both FX rates are reporting/currency-conversion inputs only in the current system — confirmed, not a trading-decision input anywhere.** Per the task's instruction, both are included in the CSV unconditionally regardless of this answer, and labeled `entry_gbpusd_reporting_only`/`exit_gbpusd_reporting_only`/`entry_eurusd_reporting_only`/`exit_eurusd_reporting_only` to state this plainly in the column names themselves. GBPUSD=X and EURUSD=X are both quoted USD-per-1-unit (e.g. GBPUSD=X ≈ 1.27 means £1 = $1.27), per `_fx_rates()`'s own docstring.

**S&P 500 level, for the same reason stated plainly:** `analysis/crisis.py`'s `is_crisis_day()`/`is_entry_blocked()`/`compute_raw_trigger()` all take a `gspc_close` parameter — the real crisis check literally reads `^GSPC`'s daily close. Exported as `entry_sp500_level`/`exit_sp500_level` for exactly that reason — this is a genuine decision input (crisis-check), unlike the two FX rates.

## 2. Confirmation the backtest was run fresh, current code

- **Re-run from scratch, not reused from any of tonight's saved trade JSONs** — necessary because `d1a067a` ("Fix silent BRK.B/BF.B ticker bug and rolling-window start-date drift") landed partway through tonight's session, after several earlier investigations' own trade-generation runs.
- **Engine: `analysis.backtest.simulate_portfolio_compounding_mtm()`**, invoked via `analysis.validation.run_full_validation(CONFIG, start=2007-01-01, end=2026-08-03, mark_to_market=True, live_universe=True)` — unmodified, the real engine, no custom harness, no monkeypatching, no custom candidate pool.
- **Result, matching tonight's established baseline exactly**: `total_trades=3161`, exit-reason breakdown `MAX_HOLDING=1796 (56.8%)`, `STOP_LOSS=1169`, `SIGNAL_OFF=131`, `CRISIS_EXIT=35`, `END_OF_DATA=30` — identical to the figures independently reproduced by every investigation tonight, and (as established in a companion investigation earlier this session) confirmed unaffected by the BRK.B fix, which changes live daily ingestion behavior, not already-stored historical `price_data`.
- Reference series confirmed to span the full window: `^GSPC` 2007-01-03→2026-08-03 (4,925 rows), `GBPUSD=X` 2007-01-01→2026-08-03 (5,092 rows), `EURUSD=X` 2007-01-01→2026-08-03 (5,078 rows).
- Momentum-at-entry recovered for **all 3,161 trades** (0 missing) by rebuilding `analysis.backtest.precompute_ticker()`'s own frames (the real engine's own function, unmodified) for all 503 live candidate tickers and reading each trade's own entry-date momentum value directly.

## 3. Honest limitation, stated upfront

The underlying `price_data` is **daily bars only**. `entry_date`/`exit_date` in the CSV are genuinely dates, not timestamps — there is no intraday time-of-open/time-of-close anywhere in this system's historical data, so none is fabricated here. `holding_days` is the actual calendar-day gap between the two dates (matches the real engine's own holding-period accounting, used for the `MAX_HOLDING` cap and VaR sizing).

## 4. The CSV, and how to retrieve it

**This session is running locally on the Windows laptop** (not the Mac Mini or the cloud server), so this is a direct file, not something needing SFTP or a network mount.

**The file is already sitting at:**
```
C:\Users\user\Downloads\sp500invest_full_trade_export_2007-2026.csv
```
Open it directly in Excel, Numbers, or any spreadsheet tool, or open the Downloads folder in File Explorer and double-click it. A second copy lives at `research/full_trade_export_2026-08-06/full_trade_export_2007-2026.csv` in this repo, kept for the record (not committed — see below).

**3,161 rows, 15 columns, zero nulls in any column** — verified directly, not assumed. Columns, in order:

| Column | Meaning |
|---|---|
| `ticker` | Stock ticker |
| `win_loss` | `WIN` / `LOSS` |
| `pnl_gbp` | Realized P&L in GBP |
| `pnl_pct` | Realized P&L as a percentage |
| `entry_date` / `exit_date` | Dates only (Section 3) |
| `holding_days` | Calendar days held |
| `entry_sp500_level` / `exit_sp500_level` | `^GSPC` close, the actual crisis-check input, at entry/exit |
| `entry_gbpusd_reporting_only` / `exit_gbpusd_reporting_only` | `GBPUSD=X` close at entry/exit — currency-conversion/reporting only, confirmed never a decision input (Section 1) |
| `entry_eurusd_reporting_only` / `exit_eurusd_reporting_only` | `EURUSD=X` close at entry/exit — same confirmation |
| `exit_reason` | `CRISIS_EXIT` / `STOP_LOSS` / `SIGNAL_OFF` / `MAX_HOLDING` / `END_OF_DATA` |
| `momentum_at_entry_pct` | Trailing 12-month momentum (%) at entry, the same field used throughout tonight's research |

Sanity-checked directly: `win_loss` counts (1,555 WIN / 1,606 LOSS) sum to 3,161; `exit_reason` counts match the baseline exactly; spot-checked first/last rows (e.g. the first trades, AAPL/AMZN entering 2008-01-03 and getting `STOP_LOSS`'d within days, land right in the early-2008 selloff — consistent with real market history).

## 5. Confirmation

- No production files were modified: `analysis/backtest.py`, `analysis/validation.py`, `analysis/crisis.py`, `brokers/trading212.py`, `output/performance.py`, and `config.yaml` were all read-only — read/traced for Section 1's FX-usage question and called exactly as they exist for the export itself, never edited.
- No custom candidate pool or monkeypatched `is_member()` — the real, unmodified `run_full_validation()` and `precompute_ticker()` were used directly.
- Nothing was committed and nothing was pushed.
- No production database was touched beyond read-only `SELECT`-equivalent queries via the existing `get_price_df()`/backtest data-loading path.
