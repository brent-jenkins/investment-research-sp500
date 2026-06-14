# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```powershell
# Install dependencies (Windows — TA-Lib-Precompiled provides the .whl, no C build needed)
pip install -r requirements.txt

# Launch the Streamlit dashboard
streamlit run main.py
```

On first launch, click **Refresh Data** in the sidebar to pull ~1 year of OHLCV history for all 20 watchlist assets. Subsequent refreshes are incremental (only missing dates are fetched). The background APScheduler job then fires automatically on weekdays at 18:30 ET while the process is running.

## Architecture

### Data flow

```
yfinance → data/ingestion.py → SQLite (database/investment_research.db)
                                      ↓
                             analysis/* modules (read via get_price_df)
                                      ↓
                             analysis/confluence.py → output/signals.py → signals table
```

`main.py` is the only entry point. It adds the project root to `sys.path` at the top so that `from data.X import ...` and `from analysis.X import ...` resolve correctly regardless of how Streamlit invokes the file.

### Configuration

Everything flows from `config.yaml`. All indicator periods, confluence thresholds, scheduler timing, rate limits, and the watchlist are read at runtime — nothing is hardcoded. Every module that needs config calls its own `_load_config()` helper (resolves relative to `Path(__file__).parent.parent`).

### Signal pipeline

Each analysis module (`regime`, `trend`, `mean_reversion`, `volatility`, `volume`, `macro`) exposes a single scoring function that returns **-1** (bearish), **0** (neutral), or **+1** (bullish). `analysis/confluence.py:generate_signal` calls all six, counts agreeing votes, and emits **BUY / SELL / NEUTRAL** based on the thresholds in `config.yaml` under `confluence:`.

- `min_signals_required` (default 3) — minimum agreeing votes.
- `buy_threshold` / `sell_threshold` (default 0.60) — fraction of total modules that must agree.
- `confluence_score` returned is `(bullish - bearish) / total_modules` ∈ [-1, 1].

### TA-Lib fallback pattern

Every analysis module tries `import talib` and sets `_TALIB_AVAILABLE`. All computation paths have a pure-pandas/numpy fallback so the app runs even if TA-Lib fails to import. Always maintain both paths when editing or adding indicator logic.

### Database writes

All writes use SQLAlchemy's `sqlite_insert(...).on_conflict_do_update(...)` (upsert) so every operation is idempotent. The unique constraints are `(ticker, date)` on every table. `get_session()` returns a **new** session each call — callers are responsible for closing it (use `try/finally`). The engine is created fresh each call too; SQLite's WAL mode and `check_same_thread=False` handle Streamlit's threading.

### yfinance MultiIndex handling

`yf.download()` for a single ticker returns a MultiIndex column DataFrame in yfinance ≥ 0.2.x. `ingestion.py` flattens this with `df.columns = [col[0] for col in df.columns]` before processing. Any new code that calls `yf.download` for a single ticker must do the same.

## Adding a new analysis module

1. Create `analysis/your_module.py` with a `score_your_module(df, ...) -> int` function returning -1/0/1.
2. Import and call it in `analysis/confluence.py:generate_signal`.
3. Add the score column to the `Signal` model in `data/database.py` and update `total_signal_modules` in `config.yaml`.
4. Persist the new score in `output/signals.py:run_all_signals`.

## Adding assets to the watchlist

Edit `config.yaml` under `watchlist:` — add a dict with `ticker` and `name` to the appropriate group. No code changes needed; `get_all_tickers()` iterates all groups dynamically.
