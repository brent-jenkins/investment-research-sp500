# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```powershell
# Install dependencies (TA-Lib-Precompiled has no wheel for Python 3.14+, so it is
# commented out in requirements.txt — all analysis modules have pure-pandas fallbacks)
pip install -r requirements.txt

# Launch the Streamlit dashboard
python -m streamlit run main.py
```

First-run sequence: **Refresh Data** → **Run Analysis** → **Generate Signals**. Refresh pulls ~1 year of OHLCV history for all 20 watchlist assets (incremental on subsequent runs). Run Analysis computes all technical indicators and writes them to the `indicators` table. Generate Signals (on the Signals page) detects the market regime then scores every asset and writes to the `signals` table. The background APScheduler job fires automatically on weekdays at 18:30 ET while the process is running.

The FRED API key for yield-curve data lives in `config.yaml` under `macro.fred_api_key`. Without it, VIX scoring still works; yield-curve scoring returns 0.

## Architecture

### Data flow

```
yfinance ──► data/ingestion.py ──► SQLite (price_data table)
                                          │
                                          ▼
                              analysis/engine.py:run_analysis()
                              ├── analysis/trend.py
                              ├── analysis/mean_reversion.py
                              ├── analysis/volatility.py
                              ├── analysis/volume.py
                              └── analysis/macro.py  (FRED + VIX from DB)
                                          │
                                          ▼
                                   indicators table
                                          │
                                          ▼
                              analysis/regime.py:detect_regime()
                              (reads ^GSPC indicators → Regime enum)
                                          │
                                          ▼
                              analysis/confluence.py:generate_signals()
                              (regime-weighted dot product per ticker)
                                          │
                                          ▼
                                    signals table
```

`main.py` is the only entry point. It inserts the project root into `sys.path` so that `from data.X import ...` and `from analysis.X import ...` resolve correctly regardless of how Streamlit invokes the file.

The app has two pages toggled via a sidebar radio button:
- **Charts** — candlestick chart, volume, and indicator panel for the selected asset
- **Signals** — regime card, ranked table of all assets by confluence score, Generate Signals button

### Configuration

Everything flows from `config.yaml`. All indicator periods, confluence thresholds, scheduler timing, rate limits, and the watchlist are read at runtime — nothing is hardcoded. Every module that needs config calls its own `_load_config()` helper (resolves via `Path(__file__).parent.parent / "config.yaml"`).

### Analysis engine

`analysis/engine.py:run_analysis()` is the Phase 2 orchestrator. For each ticker it:
1. Loads price data via `get_price_df()`
2. Calls each `compute_*()` function, which returns a dict of `{"score": int, ...raw values}`
3. Merges all raw values and upserts them into the `indicators` table for the latest date

Each `compute_*` function in the analysis modules returns the **latest** scalar values only (not a full time series). The score from each module feeds into `confluence.py:generate_signal()`.

### Regime detection

`analysis/regime.py:detect_regime()` reads the most recent stored ^GSPC indicators (SMA 200, ADX, VIX) and applies priority-ordered rules:
1. VIX > 30 → `HIGH_VOLATILITY`
2. price > SMA200, ADX > 25, VIX < 20 → `TRENDING_BULL`
3. price < SMA200, ADX > 25, VIX > 20 → `TRENDING_BEAR`
4. ADX < 20, VIX < 15 → `RANGING_LOW_VOL`
5. else → `UNKNOWN`

Each `Regime` carries a `REGIME_WEIGHTS` dict that sums to 1.0 across the five analysis modules.

### Confluence scoring

`analysis/confluence.py:generate_signals()` calls `score_ticker()` for each asset, which:
1. Calls all five `compute_*` functions fresh from stored price data
2. Computes `weighted_score = Σ(module_score × regime_weight)` ∈ [-1, 1]
3. Classifies as `STRONG_BUY` (≥0.6), `BUY` (≥0.3), `NEUTRAL`, `SELL` (≤-0.3), `STRONG_SELL` (≤-0.6)
4. Upserts to the `signals` table keyed on `(ticker, today's date)`

The regime string is stored in both the `regime` column and the legacy `notes` column of the signals table. `get_latest_signals()` returns rows sorted by `confluence_score` descending.

### TA-Lib fallback pattern

Every analysis module tries `import talib` and sets `_TALIB_AVAILABLE`. All computation paths have a pure-pandas/numpy fallback so the app runs without TA-Lib. Always maintain both paths when editing or adding indicator logic.

### Database writes

All writes use `sqlite_insert(...).on_conflict_do_update(...)` (upsert) so every operation is idempotent. The unique constraint is `(ticker, date)` on every table. `get_session()` returns a new session each call — callers must close it (use `try/finally`). The engine is created fresh each call; SQLite WAL mode and `check_same_thread=False` handle Streamlit's threading.

For **reading** indicator data in the UI, use raw SQL via `get_engine().connect()` rather than ORM queries — the ORM mapper can silently return `None` when the live schema has columns the mapper hasn't seen. See `_render_indicator_panel()` in `main.py` for the pattern.

### Schema migrations

New columns are added to existing tables via `_apply_migrations()` in `data/database.py`. Each `ALTER TABLE` runs in its own `engine.begin()` block so a duplicate-column error never aborts subsequent statements. `init_db()` calls both `Base.metadata.create_all()` and `_apply_migrations()`, and is called on **every page render** in `_initialise()` (idempotent — fast when schema is current). Never wrap multiple `ALTER TABLE` calls in a single `engine.begin()`.

### yfinance MultiIndex handling

`yf.download()` for a single ticker returns a MultiIndex column DataFrame in yfinance ≥ 0.2.x. `ingestion.py` flattens this with `df.columns = [col[0] for col in df.columns]` before processing. Any new code that calls `yf.download` for a single ticker must do the same.

## Adding a new analysis module

1. Create `analysis/your_module.py` with a `compute_your_module(df, config) -> dict` returning `{"score": int, ...raw_values}`.
2. Call it in `analysis/engine.py:analyse_ticker()` and merge its raw values into `indicator_values`.
3. Call it in `analysis/confluence.py:generate_signal()` and include its score in the `scores` list.
4. Add any new raw-value columns to the `Indicator` model in `data/database.py` and to the `new_columns` list in `_apply_migrations()`.
5. Update `total_signal_modules` in `config.yaml`.

## Adding assets to the watchlist

Edit `config.yaml` under `watchlist:` — add a dict with `ticker` and `name` to the appropriate group. No code changes needed; `get_all_tickers()` iterates all groups dynamically.
