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

First-run sequence: **Refresh Data** → **Run Analysis** → **Generate Signals**. Refresh pulls up to 10 years of OHLCV history per asset on the first load for each ticker; subsequent runs are incremental (only days since the last stored date). Run Analysis computes all technical indicators and writes them to the `indicators` table. Generate Signals detects the market regime then scores every asset and writes to the `signals` table.

After the first run, analysis and signals are automated. On every Streamlit startup the app re-runs analysis if price data exists. Three APScheduler jobs fire on weekdays:

| Time (UTC) | Type | What it does |
|---|---|---|
| **07:00** | Analysis only | Runs full analysis pipeline on stored data (Asian closed, European opening) |
| **13:30** | Analysis only | Runs full analysis pipeline on stored data (before US open) |
| **21:30** | Full refresh | Pulls incremental prices for open exchanges, then runs full analysis |

All three runs execute `run_analysis()` → `generate_signals()` → `compute_all_pairs()` and stamp `last_analysis_run` + `last_run_type`. The EOD run additionally stamps `last_full_refresh`. Manual clicks are only needed on the very first run.

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
                              ├── analysis/macro.py  (FRED + VIX from DB)
                              └── analysis/correlations.py:compute_all_pairs()
                                          │
                                          ▼
                              indicators table + correlations table
                                          │
                                          ▼
                              analysis/regime.py:detect_regime()
                              (reads ^GSPC indicators → Regime enum)
                                          │
                                          ▼
                              analysis/confluence.py:generate_signals()
                              (regime-weighted dot product per ticker, 7 modules)
                                          │
                                          ▼
                                    signals table
```

`main.py` is the only entry point. It inserts the project root into `sys.path` so that `from data.X import ...` and `from analysis.X import ...` resolve correctly regardless of how Streamlit invokes the file.

The app has six pages toggled via a sidebar radio button (default: **Signals**):
- **Signals** — regime card, ranked table of all assets by confluence score, Generate Signals button
- **Charts** — grouped asset cards landing; selecting one shows the candlestick chart, volume, and indicator panel; a "← All Assets" button returns to the landing
- **Relationships** — 30-day correlation matrix heatmap for all 14 tracked cross-asset tickers, pair-level table (30d corr, 90d corr, historical norm, z-score, CONFIRMING/DIVERGING/NEUTRAL status), plain-English divergence narratives; "↻ Recompute Now" triggers `compute_all_pairs()` on demand
- **Trade Log** — log new eToro trades (auto-populates current signal), open-position tracker with live prices and unrealised P&L, close-trade form
- **Performance** — equity curve, win rate by signal type and regime, indicator hit-rate table, closed trades list; all sections respect an optional exit-date filter
- **Status** — system status grid (deployment, uptime, DB size, last refresh/analysis, next scheduled run)

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
1. Calls all seven `compute_*` functions fresh from stored price data
2. Computes `weighted_score = Σ(module_score × regime_weight)` ∈ [-1, 1]
3. Classifies as `STRONG_BUY` (≥0.6), `BUY` (≥0.3), `NEUTRAL`, `SELL` (≤-0.3), `STRONG_SELL` (≤-0.6)
4. Upserts to the `signals` table keyed on `(ticker, today's date)`

The seventh module is `compute_correlations(ticker, config)` from `analysis/correlations.py`. It finds all predefined pairs involving the ticker, checks if the current rolling correlation matches the expected direction (CONFIRMING/DIVERGING/NEUTRAL), and returns a score of -1/0/1. Most watchlist tickers appear in zero or one pair and receive score=0; tickers like `^GSPC`, `GLD`, and `^VIX` appear in multiple pairs and receive a blended score.

### Cross-asset correlation module

`analysis/correlations.py` defines a `RELATIONSHIP_MAP` of 10 predefined pairs with expected correlation direction (+1 positive, -1 inverse):

| Pair | Expected | Rationale |
|------|----------|-----------|
| GLD vs UUP | inverse | Gold rises when dollar falls |
| USO vs XLE | positive | Oil price leads energy stocks |
| CPER vs ^GSPC | positive | Copper leads economic growth |
| HYG vs ^GSPC | positive | Credit stress precedes equity stress |
| ^VIX vs ^GSPC | inverse | Volatility spikes when market falls |
| TLT vs ^GSPC | inverse | Flight to safety in risk-off |
| GBPUSD=X vs ^FTSE | inverse | Strong pound hurts FTSE exporters |
| EURUSD=X vs ^GSPC | positive | Dollar strength affects multinationals |
| BTC-USD vs ^GSPC | positive | Risk-on correlation |
| SLV vs GLD | positive | Silver follows gold with higher beta |

Per pair, the module computes: 30-day rolling correlation, 90-day rolling correlation, historical mean/std of the 30d rolling series, z-score, and a CONFIRMING/DIVERGING/NEUTRAL status. DIVERGING requires both a sign mismatch (current vs expected) AND z-score ≥ 1.5. CONFIRMING requires a sign match AND z-score ≤ 1.0.

`compute_all_pairs(config)` is called once at the end of `run_analysis()` (not per-ticker) and upserts results to the `correlations` table (`ticker_a, ticker_b, date` unique constraint).

`build_correlation_matrix(window=30)` returns a square pandas DataFrame of pairwise correlations across all 14 tickers involved in any pair — used to render the Plotly heatmap on the Relationships page.

The regime string is stored in both the `regime` column and the legacy `notes` column of the signals table. `get_latest_signals()` returns rows sorted by `confluence_score` descending.

### TA-Lib fallback pattern

Every analysis module tries `import talib` and sets `_TALIB_AVAILABLE`. All computation paths have a pure-pandas/numpy fallback so the app runs without TA-Lib. Always maintain both paths when editing or adding indicator logic.

### Database writes

All writes use `sqlite_insert(...).on_conflict_do_update(...)` (upsert) so every operation is idempotent. The unique constraint is `(ticker, date)` on every table. `get_session()` returns a new session each call — callers must close it (use `try/finally`). The engine is created fresh each call; SQLite WAL mode and `check_same_thread=False` handle Streamlit's threading.

For **reading** indicator data in the UI, use raw SQL via `get_engine().connect()` rather than ORM queries — the ORM mapper can silently return `None` when the live schema has columns the mapper hasn't seen. See `_render_indicator_panel()` in `main.py` for the pattern.

`data/database.py` also defines a `SystemMetadata` key-value table (`key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME`). Two helpers manage it: `set_metadata(key, value)` (upserts via `INSERT OR REPLACE`) and `get_metadata(key) -> datetime | None` (parses the stored ISO string). `pull_incremental()` stamps `last_refresh_run` and `generate_signals()` stamps `last_analysis_run` so the Status page can display actual wall-clock times rather than midnight trade dates.

The Signals page ranked table uses `st.dataframe()` with a Pandas `Styler` (`.map()` per column subset) rather than a custom HTML table. This lets Streamlit's theme control fonts, row backgrounds, and borders while only the meaningful cells carry explicit colours (signal badge, score, module symbols). Set `height=header_px + row_height_px * len(rows)` to expand the frame to its full content and suppress the scrollbar.

### Status page and startup automation

`main.py:_initialise()` is called on every Streamlit session start. On the **first** request of a session it checks `_has_price_data()` (a lightweight `SELECT 1 FROM price_data LIMIT 1`). If price data exists it synchronously runs `run_analysis()` then `generate_signals()` for the full watchlist and stores a completion message in `st.session_state["_startup_msg"]`. The main render loop pops that key and calls `st.toast()` so the user sees a non-intrusive notification.

`_render_home_page()` powers both the **Status** page and the Charts landing (shown when no asset is selected). It draws a 2×3 status grid (Last Deployment, Service Uptime, Database Size, Last Data Refresh, Last Analysis Run, Next Scheduled Run) using `st.cache_data.clear()` at entry to flush stale values, then `@st.cache_data(ttl=60)` wrappers for the two metadata reads. Cards are colour-coded green/amber/red via `_age_level()`. All dynamic values passed to `unsafe_allow_html` are sanitised with `html.escape()`. Service uptime is read from `psutil.Process().create_time()`.

**`data/scheduler.py`** registers three APScheduler jobs on weekdays. All three share `_run_analysis_pipeline(config, tickers, run_type)` which calls `run_analysis()` → `generate_signals()` → stamps `last_run_type`. `generate_signals()` itself stamps `last_analysis_run`. The EOD job additionally calls `pull_incremental()` for open exchanges (per-exchange calendar check) and stamps `last_full_refresh` after the data pull succeeds. Each step is in its own `try/except` so one failure never blocks the rest.

### Trade lifecycle

`output/performance.py` handles the full trade lifecycle:
- `get_current_signal(ticker)` — reads the most recent signals row (uses `notes AS regime` since the `regime` column is written by the legacy path)
- `log_trade(...)` — inserts an open trade via ORM session, captures signal context columns (signal_type, confluence_score, regime, five module scores) at entry time
- `close_trade(trade_id, exit_price)` — raw SQL UPDATE; computes `pnl = (exit - entry) × multiplier × qty`, `pnl_pct`, and `holding_days`
- Live prices via `get_latest_price(ticker)` use a 5-day yfinance window; the UI wraps this with `@st.cache_data(ttl=300)` to limit API calls

`output/signals.py` is pure-pandas analysis over a list[dict] of closed trades — no DB calls. Functions: `accuracy_summary`, `accuracy_by_signal_type`, `accuracy_by_regime`, `accuracy_by_indicator`, `equity_curve`.

### Schema migrations

New columns are added to existing tables via `_apply_migrations()` in `data/database.py`. Each `ALTER TABLE` runs in its own `engine.begin()` block so a duplicate-column error never aborts subsequent statements. `init_db()` calls both `Base.metadata.create_all()` and `_apply_migrations()`. It is called **at module level in `main.py`** (immediately after logging setup, before any function definitions) to guarantee tables exist on fresh deployments, and again inside `_initialise()` on every page render (idempotent — fast when schema is current). Never wrap multiple `ALTER TABLE` calls in a single `engine.begin()`.

### yfinance MultiIndex handling

`yf.download()` for a single ticker returns a MultiIndex column DataFrame in yfinance ≥ 0.2.x. `ingestion.py` flattens this with `df.columns = [col[0] for col in df.columns]` before processing. Any new code that calls `yf.download` for a single ticker must do the same.

## Deployment (Ubuntu 22.04 / cloud VM)

The app runs as a systemd service behind a Caddy HTTPS reverse proxy.

### First-time setup on a fresh VM

```bash
# 1. Clone the repo
git clone https://github.com/YOUR/investment-research.git /root/investment-research
cd /root/investment-research

# 2. Run the one-time setup script (takes ~5 min)
bash setup.sh research.yourdomain.com
```

`setup.sh` installs Python 3.11, compiles the TA-Lib C library from source, installs Caddy, installs all packages system-wide via `python3.11 -m pip`, writes `.streamlit/config.toml`, and registers `investment-research.service` with systemd. There is no virtualenv — packages are installed at the system level.

### Password-protecting the dashboard

Caddy's `basicauth` directive guards the app. After `setup.sh` runs:

```bash
caddy hash-password          # enter your password, copy the hash output
nano /etc/caddy/Caddyfile    # replace REPLACE_WITH_HASH (and 'admin' if desired)
systemctl reload caddy
```

### Ongoing deploys (from Windows)

The easiest path is a plain `git push origin main` — the pre-push hook calls `deploy.ps1` automatically (see [Git hooks](#git-hooks) below). To deploy without that hook, or from a machine where it isn't installed:

```bash
bash deploy.sh          # Git Bash — VM_IP is hardcoded in the script
```

```powershell
.\deploy.ps1            # PowerShell — gitignored, local-only; SSH key at ssh-keys\
```

Both scripts push to GitHub first, then SSH into the VM to `git pull`, reinstall any new packages via `python3.11 -m pip install -r requirements.txt --quiet`, and `systemctl restart` the service.

### Git hooks

`setup-hooks.sh` installs a pre-push hook that auto-deploys whenever you push to `main`. Run once after cloning:

```bash
bash setup-hooks.sh
```

The hook SSHes directly into the server after a 15-second background delay, so git finishes pushing to GitHub before the server runs `git pull`. Output is appended to `/tmp/investment-research-deploy.log` on the local machine. To push without triggering deployment: skip the hook by temporarily removing it or not installing it on that machine.

### Key files

| File | Purpose |
|---|---|
| `setup.sh` | One-time server bootstrap (no virtualenv; installs packages system-wide) |
| `investment-research.service` | systemd unit; ExecStart uses `/usr/local/bin/streamlit` directly |
| `Caddyfile` | Caddy reverse-proxy template (gitignored — contains domain/hash) |
| `.streamlit/config.toml.example` | Reference for the server config created by `setup.sh` (actual file is gitignored) |
| `deploy.sh` | Git Bash deploy script (VM_IP hardcoded; SSH key at `ssh-keys/`) |
| `deploy.ps1` | PowerShell deploy script — gitignored (local-only, contains SSH key path) |
| `setup-hooks.sh` | Installs the pre-push git hook; run once after cloning |
| `config.yaml` | Runtime config — **gitignored** (contains FRED API key; manage on server directly) |

### TA-Lib: Windows vs Linux

`requirements.txt` has both package names commented out with explanations. `setup.sh` installs `TA-Lib` (Linux wrapper) after compiling the C library. Windows dev installs `TA-Lib-Precompiled` manually if needed; all analysis modules have pure-pandas fallbacks so it is optional on both platforms.

### Useful server commands

```bash
journalctl -u investment-research -f   # live app logs
systemctl status investment-research   # service health
systemctl reload caddy                 # apply Caddyfile changes without downtime
```

## Adding a new analysis module

1. Create `analysis/your_module.py` with a `compute_your_module(df, config) -> dict` returning `{"score": int, ...raw_values}`.
2. Call it in `analysis/engine.py:analyse_ticker()` and merge its raw values into `indicator_values`.
3. Call it in `analysis/confluence.py:score_ticker()` and add its score to `module_scores`.
4. Add `"your_module"` with a weight to every regime dict in `analysis/regime.py:REGIME_WEIGHTS` — weights must still sum to 1.0 per regime.
5. Add `your_module_score` to the `Signal` model in `data/database.py`, to `_apply_migrations()`, and to the upsert in `confluence.py:_persist_signal()`.
6. Add any new raw-value columns to the `Indicator` model and to `_apply_migrations()`.
7. Update `total_signal_modules` in `config.yaml`.

Note: `analysis/correlations.py` is an exception to the pattern — it operates on pairs of assets rather than a single ticker's DataFrame, so `compute_all_pairs()` is called once at the end of `run_analysis()` rather than inside `analyse_ticker()`, and `compute_correlations(ticker, config)` fetches its own data internally rather than accepting a pre-fetched `df`.

## Adding assets to the watchlist

Edit `config.yaml` under `watchlist:` — add a dict with `ticker` and `name` to the appropriate group. No code changes needed; `get_all_tickers()` iterates all groups dynamically. The current watchlist has 69 assets across 18 groups: `indices`, `sector_etfs`, `energy`, `commodities`, `bonds`, `volatility`, `international`, `forex`, `crypto`, `volatility_complex`, `tech_subsectors`, `financial_subsectors`, `healthcare_subsectors`, `international_expanded`, `real_assets`, `fixed_income_expanded`, `commodities_expanded`, `crypto_expanded`. When adding a new group, also add its label to the `_GROUP_LABELS` dict in `main.py` so it appears on the Charts landing page. Forex pairs (e.g. `EURUSD=X`) return zero volume from yfinance — the volume module scores neutral for these, which is expected.
