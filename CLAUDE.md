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

First-run sequence: **Run Analysis** → **Generate Signals**. On the very first run with no data in the database, click Run Analysis on the Signals page; subsequent runs are fully automatic. Refresh pulls up to 10 years of OHLCV history per asset on the first load for each ticker; subsequent runs are incremental (only days since the last stored date). Run Analysis computes all technical indicators and writes them to the `indicators` table. Generate Signals detects the market regime then scores every asset and writes to the `signals` table.

After the first run, analysis and signals are automated. On every Streamlit startup the app re-runs analysis if price data exists. Six APScheduler jobs fire automatically:

| Schedule | Type | What it does |
|---|---|---|
| **Mon–Fri 07:00** | Asian EOD | `pull_incremental()` for exchanges closed before 07:00 UTC (e.g. JPX/Tokyo ~06:30), then full analysis pipeline; opens virtual trades for those tickers only |
| **Mon–Fri 13:30** | Analysis only | `run_analysis()` → `generate_signals()` → `compute_all_pairs()` — no data pull, no virtual trade opens (EU mid-session, US about to open) |
| **Mon–Fri 19:05** | FOMC reaction | Analysis-only pipeline on FOMC decision days only (`analysis/fomc.py:is_fomc_decision_day()` gates execution); silently exits on non-FOMC days |
| **Mon–Fri 21:30** | Full refresh | `pull_incremental()` for all open exchanges, FRED macro fetch, then full analysis pipeline; opens virtual trades for all eligible tickers |
| **Mon–Fri 00:00–21:00 + 23:00 (hourly)** | Trade alerts | Fetches live prices for open trades; emails alert on STOP LOSS / EXIT SIGNAL — filtered per-trade by home exchange session (only alerts when the asset's market is open) |
| **Sunday 10:00** | Weekly summary | Sends the weekly performance summary email (regime, signals, open trades, P&L, accuracy, macro, diverging pairs) |

All three analysis runs execute `run_analysis()` → `generate_signals()` → `compute_all_pairs()` → `_process_virtual_trades()` and stamp `last_analysis_run` + `last_run_type`. The EOD run additionally stamps `last_full_refresh`. Manual clicks are only needed on the very first run.

The FRED API key lives in `config.yaml` under `macro.fred_api_key`. Without it, all FRED-sourced scoring returns 0; VIX scoring (sourced from the local `^VIX` price data) still works. FRED series tracked: T10Y2Y, T10Y3M, DFII10 (real yields), T10YIE (breakeven inflation), BAMLH0A0HYM2 (HY OAS), BAMLC0A0CM (IG OAS), M2SL, DPCREDIT (discount window), BOGMBASE, CFNAI (Chicago Fed National Activity Index), PERMIT (housing permits), DTWEXBGS (trade-weighted dollar), DFEDTARU (Fed funds rate upper bound). Tickers that are themselves macro instruments (^VIX, TLT, IEF, HYG) receive a neutral macro score of 0 to avoid self-referential scoring.

Key config sections added since initial release:
- `notifications.weekly_summary.enabled` — controls the Sunday 10:00 UTC weekly summary email independently of `notifications.email.enabled`.
- `backtest.max_holding_days`, `backtest.atr_stop_multiplier`, `backtest.walk_forward_months`, `backtest.walk_forward_step` — configure the backtesting engine.
- `virtual_trading.enabled`, `portfolio_size_gbp`, `position_pct`, `max_open_positions`, `entry_signal` — control automated virtual trading. `entry_signal: "STRONG_BUY"` limits entries to STRONG_BUY/STRONG_SELL; `"BUY"` also accepts BUY/SELL.
- `trading212_invest.enabled`, `live`, `max_position_gbp`, `max_total_deployed`, `entry_signal`, `exit_signal`, `ucits_mapping` — control T212 Invest API integration. `entry_signal: "BUY"` enters on BUY or STRONG_BUY; `"STRONG_BUY"` restricts to STRONG_BUY only (same pattern for `exit_signal`). `ucits_mapping` maps internal research tickers to T212 UCITS instrument IDs — only mapped tickers receive automated orders. Requires `T212_API_KEY` and `T212_API_SECRET` env vars. `live: false` is the hard default and is enforced in code.

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
                              ├── analysis/correlations.py:compute_all_pairs()
                              └── analysis/fomc.py  (FOMC calendar; gates 19:05 scheduler job)
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

The app has eight pages toggled via a sidebar radio button (default: **Daily Briefing**). The sidebar shows Last Refresh and Last Analysis timestamps. The watchlist has approximately **72 assets across 19 groups**.
- **Daily Briefing** — single compact status bar (regime pill + ^GSPC stats + VIX/yield/USD + active overlay pills in one bordered row), regime description + threshold captions below, overlay detail strips when active, FOMC key events, signal distribution counts; open trade monitor combining CFD/virtual trades and T212 Invest positions (all sorted by urgency), with live GBP P&L, health status (ON TRACK / WEAKENING / EXIT SIGNAL / STOP LOSS), and ATR-based stop loss; top opportunities filtered to tradeable assets (T212 Invest tickers excluded from opportunities while a position is held)
- **Signals** — compact header block (overlays + catch-up strips + regime badge + signal distribution pills + timestamp, all in one bordered card); market intelligence plain-English summary; ranked table of all assets by confluence score with module symbols; Generate Signals button
- **Charts** — grouped asset cards landing; selecting one shows the candlestick chart, volume, and indicator panel; a "← All Assets" button returns to the landing
- **Relationships** — compact single-line header (pair count + last-computed timestamp, read dynamically from `RELATIONSHIP_MAP`); 30-day correlation matrix heatmap across all price-data tickers (FRED series excluded); pair-level table (30d corr, 90d corr, historical norm, z-score, CONFIRMING/DIVERGING/NEUTRAL status); plain-English divergence narratives (correlations recompute automatically — no manual button)
- **Trade Log** — log new CFD trades (auto-populates current signal; captures leverage, FX rate, investment amount, SL/TP, R:R, swap rate, spread cost, T212 instrument name); open-position tracker with live GBP P&L and per-trade edit expanders; close-trade form; position sizing guidance; **T212 Invest Positions** table below the CFD tracker (ticker, UCITS ID, qty, avg/current price, current value £, P&L £/%, days held, signal, status — auto-managed, read-only)
- **Performance** — equity curve, win rate by signal type and regime, indicator hit-rate table, closed trades list; all sections respect an optional exit-date filter
- **Backtest** — 6-tab page: Run & Overview (date range presets, ticker scope — tradeable-only / T212 CFD Only / all assets; equity curve, metrics, trade table; collapsible T212 CFD comparison panel that filters any existing run to `trading212_instrument` tickers and shows side-by-side metrics + excluded-ticker trade counts without re-running), Period Analysis (COVID Crash / 2022 Bear / 2023–24 Recovery + per-regime breakdown), Module Attribution (win rate when each module agreed vs disagreed with trade direction), Signal Decay (holding period histogram, exit reason distribution, avg hold by regime), Walk-Forward (rolling 6-month windows), Virtual Trading (open/closed virtual positions)
- **Status** — system status grid (deployment, uptime, DB size, last refresh/analysis, next scheduled run); Manual Controls section with two buttons: "Run Analysis Now" (analysis + signals only, no data pull) and "Refresh Data + Analysis" (price pull + FRED macro fetch + analysis + signals, matching the full EOD scheduler job); Send Test Email button and Send Weekly Summary Now button side by side; **T212 Invest Account** section (auto-loads on page open, auto-refreshes every 60 seconds — split into two tabs: **Account** tab shows live account balance metrics, deployed-capital progress bar, order log from `t212_invest_orders`, retry/delete buttons for FAILED orders; **Mapping** tab shows the UCITS instrument table; open positions removed — shown on Daily Briefing and Trade Log instead); Scheduler Health grid

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
5. else → `MIXED`

Each `Regime` carries a `REGIME_WEIGHTS` dict that sums to 1.0 across the six analysis modules. Weights by regime:

| Module | TRENDING_BULL | TRENDING_BEAR | RANGING_LOW_VOL | HIGH_VOLATILITY | MIXED |
|--------|:---:|:---:|:---:|:---:|:---:|
| trend | 0.30 | 0.25 | 0.10 | 0.10 | 0.18 |
| mean_reversion | 0.10 | 0.15 | 0.35 | 0.15 | 0.18 |
| volatility | 0.15 | 0.20 | 0.15 | 0.35 | 0.18 |
| volume | 0.20 | 0.15 | 0.15 | 0.15 | 0.18 |
| macro | 0.15 | 0.15 | 0.15 | 0.15 | 0.18 |
| correlation | 0.10 | 0.10 | 0.10 | 0.10 | 0.10 |

### Regime overlays

`analysis/regime.py:detect_overlays()` runs after each `compute_all_pairs()` call. Results are written to `system_metadata` under the key `active_overlays` (comma-separated key names, or `"none"`):

| Overlay key | Trigger | Confidence impact |
|-------------|---------|-------------------|
| `MACRO_STRESS` | TLT/^GSPC correlation DIVERGING (z ≥ 1.5) | −20% on equity/bond BUY signals |
| `VOL_REGIME_SHIFT` | ^VIX/^GSPC correlation DIVERGING (z ≥ 1.5) | Informational warning only |
| `CREDIT_STRESS` | HYG/TLT return-spread z-score < −1.0 | −15% on equity BUY signals |
| `RECESSION_RISK` | CFNAI < −0.70 for 2 consecutive months | −25% on all equity BUY signals |
| `EM_STRESS` | DTWEXBGS 30-day change > +3% (dollar surging) | −15% on EM asset BUY signals |

Active overlays are displayed as compact inline strips on the Signals and Daily Briefing pages. Confidence badges appear on opportunity cards in the Daily Briefing for affected tickers when overlays are active. `OVERLAY_DESCRIPTIONS` in `main.py` maps each key to its UI label, colour, and descriptive text.

### Confluence scoring

`analysis/confluence.py:generate_signals()` calls `score_ticker()` for each asset, which:
1. Calls all seven `compute_*` functions fresh from stored price data
2. Computes `weighted_score = Σ(module_score × regime_weight)` ∈ [-1, 1]
3. Classifies as `STRONG_BUY` (≥`buy_threshold`), `BUY` (≥0.3), `NEUTRAL`, `SELL` (≤-0.3), `STRONG_SELL` (≤`-sell_threshold`) — STRONG boundaries are regime-aware: `get_regime_thresholds(regime_name, config)` reads `confluence.regime_thresholds.<REGIME>` from config (all five regimes have explicit entries; no global fallback). TRENDING_BULL raises sell_threshold to 0.70 (harder to short in a bull), TRENDING_BEAR raises buy_threshold to 0.70, MIXED tightens to 0.60/0.60, HIGH_VOLATILITY to 0.70/0.70, RANGING_LOW_VOL stays at 0.55/0.55. The backtest engine applies the same per-date regime thresholds via vectorised Series. All downstream consumers (email alerts, virtual trading, trade health status) read the already-classified `signal` string from the DB — no re-classification from raw scores. The Daily Briefing shows the current effective thresholds and approximate module agreement required (e.g. "Threshold: 0.60 — requires ~4/7 modules to agree")
4. Upserts to the `signals` table keyed on `(ticker, today's date)`

The seventh module is `compute_correlations(ticker, config)` from `analysis/correlations.py`. It finds all predefined pairs involving the ticker, checks if the current rolling correlation matches the expected direction (CONFIRMING/DIVERGING/NEUTRAL), and returns a score of -1/0/1. Most watchlist tickers appear in zero or one pair and receive score=0; tickers like `^GSPC`, `GLD`, and `^VIX` appear in multiple pairs and receive a blended score.

### Feedback learning

`confluence.py:recalculate_learned_weights()` reads all closed trades and computes per-module win rates (direction matched profitable outcome). Win rates are normalised and clamped between `min_single_weight` (0.05) and `max_single_weight` (0.30) from config, then stored in `system_metadata` under `learned_weights` (JSON). When learned weights exist they override the static `REGIME_WEIGHTS` in `score_ticker()`. The correlation module is held at a neutral 0.5 win rate since its score is not stored on trades. A history of the last 20 snapshots is kept under `weight_history`. `maybe_update_weights()` is called at the end of each `generate_signals()` run and only triggers recalculation when the closed-trade count hits a new multiple of the configured boundary.

### Tradeable vs analysis-only assets

Each watchlist entry in `config.yaml` has a `tradeable: true/false` flag. `get_all_tickers()` returns all tickers regardless of this flag; the tradeable subset is derived at startup in `main.py` as `tradeable_set`. Assets with `tradeable: false` (e.g. ^GSPC, ^IXIC, CPER, CORN) are excluded from opportunity filtering on the Daily Briefing and from the Trade Log ticker list. The Signals page shows all assets but displays a "Tradeable" sub-count on every signal distribution type. The `brief_include_analysis` checkbox on the Daily Briefing allows temporarily including analysis-only assets in the opportunities list.

### Trading212 instrument mapping

Watchlist entries may carry `trading212_instrument: "XAG/USD"` when the T212 instrument name differs from the yfinance ticker. At startup, `main.py` builds a `t212_names: dict[str, str]` mapping `ticker → t212_name` from all watchlist entries that have this field. The Trade Log form uses `t212_names.get(ticker, "")` to pre-fill the Platform Instrument field.

### Cross-asset correlation module

`analysis/correlations.py` defines a `RELATIONSHIP_MAP` of **28 predefined pairs** with expected correlation direction (+1 positive, -1 inverse). The original 10 pairs cover core macro relationships (GLD/UUP, USO/XLE, CPER/^GSPC, HYG/^GSPC, ^VIX/^GSPC, TLT/^GSPC, GBPUSD=X/^FTSE, EURUSD=X/^GSPC, BTC-USD/^GSPC, SLV/GLD as a risk-appetite ratio). The expanded set adds: TLT/GLD, USO/UNG, XLF/TLT, XLK/^VIX, XLU/TLT, GLD/^GSPC, EEM/UUP, HYG/TLT, BTC-USD/GLD, XLE/USO, SOXX/^GSPC, KRE/TLT, VNQ/TLT, FXI/UUP, EMB/UUP, KBE/T10Y2Y, GLD/DFII10, INDA/UUP.

**FRED series as pair legs:** Two pairs use FRED macro series (not yfinance price tickers) as one leg — `KBE/T10Y2Y` (bank stocks vs yield curve steepness) and `GLD/DFII10` (gold vs real yields). `_FRED_PAIR_SERIES: frozenset[str] = frozenset({"T10Y2Y", "DFII10"})` gates which legs use macro data. `_get_macro_changes(series_id, days)` reads from the `macro_indicators` table and returns a `pd.Series` of day-over-day changes using `diff()` not `pct_change()` — yield series can cross zero and `pct_change()` is undefined there. FRED legs do not generate catch-up opportunity flags (`return_30d = None`). `_ALL_PAIR_TICKERS` is derived dynamically by filtering out `_FRED_PAIR_SERIES` members, so only price-data tickers appear in the correlation matrix heatmap.

Per pair, the module computes: 30-day rolling correlation, 90-day rolling correlation, historical mean/std of the 30d rolling series, z-score, and a CONFIRMING/DIVERGING/NEUTRAL status. DIVERGING requires both a sign mismatch (current vs expected) AND z-score ≥ 1.5. CONFIRMING requires a sign match AND z-score ≤ 1.0.

`compute_all_pairs(config)` is called once at the end of `run_analysis()` (not per-ticker) and upserts results to the `correlations` table (`ticker_a, ticker_b, date` unique constraint).

`build_correlation_matrix(window=30)` returns a square pandas DataFrame of pairwise correlations across all price-data tickers referenced in any pair (FRED series excluded) — used to render the Plotly heatmap on the Relationships page.

**Proxy inheritance (`CATEGORY_PROXY`):** Tickers that do not appear directly in any `RELATIONSHIP_MAP` pair can still receive a correlation score via `CATEGORY_PROXY: dict[str, str]` — a mapping from ticker to a related pair member whose pair score is blended at 50% into the ticker's confluence score. This allows analysis-only assets like COPX (→ GLD), XOP (→ USO), ITB (→ TLT), USDCNY=X (→ FXI), USDINR=X (→ INDA), and USDBRL=X (→ EWZ) to participate in correlation scoring without requiring bespoke pairs. A proxy inheritance table is rendered at the bottom of the Relationships page. Proxies are applied inside `compute_correlations(ticker, config)` — the direct-pair path runs first; proxy only fires when no direct pair exists.

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

**`data/scheduler.py`** registers six APScheduler jobs. All three standard analysis runs share `_run_analysis_pipeline(config, tickers, run_type)` which calls `run_analysis()` → `generate_signals()` → `_process_virtual_trades()` → stamps `last_run_type`. `generate_signals()` itself stamps `last_analysis_run`. The EOD job additionally calls `pull_incremental()` for open exchanges (per-exchange calendar check) and stamps `last_full_refresh`. Each step is in its own `try/except` so one failure never blocks the rest. A sixth job, `_fomc_reaction_job()`, fires at 19:05 UTC every weekday but calls `analysis/fomc.py:is_fomc_decision_day()` and immediately returns on non-FOMC days; on decision days it runs `_run_analysis_pipeline()` to capture the post-announcement market reaction before the regular 21:30 EOD run. The FOMC calendar uses a hardcoded 2025–2026 schedule plus a Fed press-release API check for real-time confirmation.

`_trade_alert_job()` runs hourly 00:00–21:00 + 23:00 UTC on weekdays (covering all major market sessions). For each open trade it first checks whether the trade's home exchange is currently in session via `_is_exchange_in_session(exchange, now_utc)` — trades on closed exchanges are skipped so alerts only fire when the market is actually open. For in-session trades it fetches a live price, calls `enrich_open_trade()` to get the current health status, and emails an alert when a trade reaches STOP LOSS or EXIT SIGNAL. Deduplication: the last-alerted status per trade is stored in `system_metadata` under `trade_alert_status:{trade_id}` — the same status only triggers one email until it changes. The global `alert_cooldown_hours` setting prevents email storms.

`_weekly_summary_job()` runs every Sunday at 10:00 UTC. It calls `output/briefing.py:send_weekly_summary_email(config)` which gathers all data from the DB (no external API calls) and sends an HTML email with plain-text fallback covering regime/overlays, signal snapshot, 7-day signal performance, open trades, closed trades P&L, accuracy progress, macro pulse, diverging pairs, and virtual portfolio status. Can be triggered on demand via the "Send Weekly Summary Now" button on the Status page. Controlled independently via `notifications.weekly_summary.enabled`.

`_process_virtual_trades(config, run_type, eligible_exchanges=None)` is called at the end of every `_run_analysis_pipeline()` call. It reads open virtual positions and latest signals from the DB, then:
- **Close phase**: closes any virtual position where the current price has crossed the stored stop_loss_price, or the signal has reversed direction. Runs on every call regardless of run_type.
- **Open phase**: opens new virtual positions for STRONG_BUY/STRONG_SELL signals on tradeable tickers, subject to `max_open_positions`. New position eligibility: when `eligible_exchanges` is provided (pre-market Asian run), only tickers in those exchanges can open; when `None` and `run_type == RUN_FULL` (EOD run), all tickers on exchanges that had a session today are eligible; otherwise (analysis-only runs) no new positions are opened. All virtual trades use `account_type = 'VIRTUAL'` in the existing `trades` table — no separate table.

### Trade lifecycle

`output/performance.py` handles the full trade lifecycle:
- `get_current_signal(ticker)` — reads the most recent signals row (uses `notes AS regime` since the `regime` column is written by the legacy path)
- `log_trade(...)` — inserts an open trade via ORM session, captures signal context columns (signal_type, confluence_score, regime, five module scores) at entry time, plus the full CFD context: leverage, investment_amount, position_value, margin_required, currency, account_currency, fx_rate_at_entry, fx_fee_pct, overnight_rate, swap_basis, spread_cost, platform_instrument, stop_loss_price, take_profit_price, risk_reward_ratio, planned_risk_gbp
- `close_trade(trade_id, exit_price)` — raw SQL UPDATE; P&L is computed in quote currency then converted to GBP via fx_rate_at_entry, minus fx_fee_pct on position_value. For JPY-quoted pairs (USDJPY=X) a two-step conversion is used (pips → JPY → USD → GBP). Also records `pnl_pct` and `holding_days`
- Live prices via `get_latest_price(ticker)` use a 5-day yfinance window; the UI wraps this with `@st.cache_data(ttl=300)` to limit API calls
- `enrich_open_trade(trade, live_price, max_holding_days=30)` in `output/briefing.py` augments each open trade with current signal, ATR-based stop loss (entry ± 2×ATR when no stored SL exists), unrealised GBP P&L, days held, `days_remaining`, `near_max_hold` flag, live R:R, and a health status (ON TRACK / WEAKENING / EXIT SIGNAL / STOP LOSS). Status priority: STOP LOSS > EXIT SIGNAL > WEAKENING > ON TRACK. Trades are rendered on the Daily Briefing as compact HTML status cards sorted by urgency. When `near_max_hold` is True (≤ 7 days remaining), a "⏱ Xd left" amber badge appears in the card detail row

`FOREX_QUOTE_CCY` in `performance.py` maps forex CFD tickers to their quote currency (USD for GBP/USD, EUR/USD, AUD/USD; JPY for USD/JPY; etc.) so the P&L conversion chain is always correct.

`output/signals.py` is pure-pandas analysis over a list[dict] of closed trades — no DB calls. Functions: `accuracy_summary`, `accuracy_by_signal_type`, `accuracy_by_regime`, `accuracy_by_indicator`, `equity_curve`.

### Schema migrations and startup fixes

New columns are added to existing tables via `_apply_migrations()` in `data/database.py`. Each `ALTER TABLE` runs in its own `engine.begin()` block so a duplicate-column error never aborts subsequent statements. `init_db()` calls both `Base.metadata.create_all()` and `_apply_migrations()`. It is called **at module level in `main.py`** (immediately after logging setup, before any function definitions) to guarantee tables exist on fresh deployments, and again inside `_initialise()` on every page render (idempotent — fast when schema is current). Never wrap multiple `ALTER TABLE` calls in a single `engine.begin()`.

`_apply_data_fixes(engine)` runs after migrations on every startup. Each fix is guarded by a value-based condition so it is safe to call repeatedly — it only fires when the target row still has the pre-fix value. Use this pattern for one-time corrections to existing trade rows.

`_purge_unlisted_tickers(engine)` runs on every startup. It reads the current watchlist from config.yaml, then deletes rows for any ticker no longer present from `price_data`, `indicators`, `signals`, and `correlations`. For `correlations` it removes pairs where either `ticker_a` or `ticker_b` has been removed. This keeps the DB in sync when assets are removed from the watchlist without manual SQL.

### Additional database tables

Beyond the tables described above, `data/database.py` defines:

- **`MacroIndicator`** (`series_id, date` unique) — stores FRED series values with `pct_change_30d` (≈ month-over-month), `z_score_2y` (value vs 2-year rolling mean/std), and a −1/0/+1 `score`. `compute_macro(ticker, config)` reads from this table (no FRED API calls at analysis time) and computes a per-ticker composite score. After averaging all stored series scores + VIX, it applies ticker-specific adjustments: CFNAI boosts/penalises equity-group tickers (±0.10/−0.20) based on economic cycle; DFEDTARU trend routes ±0.10 to rate-sensitive assets (VNQ/XLRE/ITB bearish when rising, KBE/KRE/XLF bullish); M2SL growth routes ±0.10/+0.05 to crypto/gold; PERMIT falling >5% MoM penalises real estate tickers −0.10. `SIGNALS_ACTIVE` (frozenset exported from `analysis/macro.py`) lists which series have ticker-specific routing or overlay triggers — used by the dashboard to show "✓ Signals" vs "Composite" tags.
- **`OpportunityFlag`** (`ticker, flag_type, date` unique) — records catch-up opportunities detected when a correlation pair is DIVERGING with z-score ≥ 1.5. Fields include `z_score`, `pair_corr_30d`, `price_at_flag`, and `return_5d`/`return_20d` (populated on later runs). `get_active_opportunity_flags()` returns flags from the most recent analysis date; they are displayed as compact strips in the Signals and Daily Briefing header blocks.
- **`Performance`** (`ticker, date` unique) — rolling performance metrics: daily return, cumulative return, drawdown, max drawdown, Sharpe, Sortino, Calmar, win rate, avg win/loss, profit factor. Currently written by the analysis pipeline but not yet surfaced in a dedicated UI view beyond the closed-trade Performance page.
- **`BacktestRun`** (`id` PK) — metadata for each backtest run: name, created_at, start_date, end_date, tickers_json, params_json, status (running/complete/failed), error_message, and summary metrics (total_trades, win_rate, avg_return_pct, sharpe_ratio, max_drawdown_pct, total_return_pct, avg_holding_days).
- **`BacktestSignal`** (`run_id, ticker, date` unique) — per-ticker per-date signal history generated during a backtest run: regime, confluence_score, signal classification, all five module scores, close_price, atr.
- **`BacktestTrade`** (`id` PK, FK to `run_id`) — simulated trades from a backtest run: ticker, direction, entry/exit date and price, entry_signal, exit_reason (STOP_LOSS / SIGNAL_REVERSAL / MAX_HOLDING / END_OF_DATA), pnl_pct, regime_at_entry, all five module scores at entry, holding_days, is_win.

The `correlations` table uses column names `divergence` (CONFIRMING/DIVERGING/NEUTRAL), `z_score`, and `pair_score` — not `status_30d`, `z_score_30d`, or `expected_direction`. Always use the ORM model or check `data/database.py:CorrelationData` before writing raw SQL against this table.

### Backtesting engine

`analysis/backtest.py` vectorizes the full signal logic over historical price data. It does **not** call the live analysis modules — instead it re-implements the scoring logic using `rolling()` and `ewm()` which are inherently look-ahead free. Key functions:

- `compute_signal_history(ticker, start_date, end_date, config)` → DataFrame of daily regime/scores/signals for a ticker. The correlation module is excluded; its 0.10 weight is re-normalised across the other five modules per date.
- `simulate_trades(signal_df, config)` → list of trade dicts. Entry on STRONG_BUY (LONG) or STRONG_SELL (SHORT). Exit priority: stop loss (entry ± `atr_stop_multiplier` × ATR) → signal reversal → `max_holding_days` → end of data.
- `run_backtest(tickers, start_date, end_date, config, name)` → creates a `BacktestRun` record, iterates tickers, stores signals and trades in bulk, updates run with summary metrics, returns `run_id`.
- Query helpers: `get_all_runs()`, `get_run(run_id)`, `get_run_trades(run_id)` (returns DataFrame), `get_run_signals(run_id)`, `delete_run(run_id)`.

`analysis/backtest_metrics.py` is pure-pandas, no DB calls. It accepts a trades DataFrame from `get_run_trades()` and provides:
- `compute_metrics(df)` → full dict: win_rate, avg_return, Sharpe, Calmar, max_drawdown, profit_factor, max_loss_streak.
- `equity_curve(df)` → dated DataFrame (date, equity, drawdown_pct).
- `period_breakdown(df)` → metrics per predefined period (COVID Crash / 2022 Bear / 2023-24 Recovery).
- `module_attribution(df)` → per-module win rate when module agreed vs disagreed with trade direction.
- `signal_decay(df)` → hold distribution histogram, exit reason percentages, avg hold by regime.
- `walk_forward(df, window_months, step_months)` → list of rolling-window metric dicts.
- `regime_performance(df)` → win rate and return broken down by regime at entry.

Backtest config lives under `backtest:` in `config.yaml` (`max_holding_days`, `atr_stop_multiplier`, `walk_forward_months`, `walk_forward_step`). The `_render_backtest_page(config, watchlist)` function in `main.py` implements all six UI tabs.

### Broker integration — Trading212 Invest

`brokers/trading212.py` contains two concerns:

**`Trading212Client`** — thin HTTP wrapper around the T212 Invest REST API (base URL switches between `demo.trading212.com` and `live.trading212.com` based on `live` flag). Authentication is HTTP Basic Auth over `T212_API_KEY:T212_API_SECRET`. All requests use `_request()` which retries up to 3 times with exponential back-off; HTTP 429 (rate-limited) gets a longer wait. On 4xx errors `_request()` reads the response body and includes it in the raised `HTTPError` message, so the T212 rejection reason is visible in error emails and the `error_message` column of `t212_invest_orders`. Methods: `get_account_summary()`, `get_positions()`, `get_instruments()` (24-hour module-level cache), `place_market_order(ticker, quantity)` (raises `RuntimeError` if `live=True` — live trading is hard-blocked), `cancel_order(order_id)`.

**`maybe_place_t212_orders(config, run_type)`** — called from `_run_analysis_pipeline()` in `data/scheduler.py` after `_process_virtual_trades()`. Runs two phases on every EOD (`run_type == "full"`) call when `trading212_invest.enabled: true` and credentials are present:

*EXIT phase* — for each open T212 position, reverse-maps the UCITS ticker to the research ticker via `ucits_mapping`, reads the current signal, and places a SELL market order if the signal is in `exit_signals` (default: SELL or STRONG_SELL).

*BUY phase* — for each ticker in `ucits_mapping`, checks the current signal; if it is in `entry_signals` (default: BUY or STRONG_BUY), checks that no position exists and no recent order is recorded in the DB (30-day window), then places a BUY market order sized via `get_quantity_for_gbp()`. Before placing, fetches `get_instruments()` metadata and applies two guards: (1) quantity is floored to an integer with `math.floor()` — all `ucits_mapping` instruments are EU/UK-listed ETFs that require whole-share quantities; T212's `maxQuantityPrecision` metadata field is not used because it is unreliable for these instruments (returns values that T212's own order API subsequently rejects); (2) the floored quantity must meet `minTradeQuantity` from the instruments metadata — if not, the order is skipped with a warning. Stops if total deployed ≥ `max_total_deployed` × account value, or free cash < £10.

`entry_signal` and `exit_signal` in config control the threshold: `"BUY"` accepts BUY or STRONG_BUY; `"STRONG_BUY"` restricts to STRONG_BUY only (same pattern for exit). All order attempts (success and failure) are written to `t212_invest_orders`. API errors set `t212_invest_error` in `system_metadata`, halt the current run, and send an alert email.

**`poll_placed_orders(config)`** — called from the hourly `_trade_alert_job()` and on every "Refresh Account" click. Reads all rows with `status = 'PLACED'` from `t212_invest_orders` (including `quantity` and `currency_code`), calls `GET /equity/orders/{id}` for each, and updates `status` / `fill_price` / `filled_quantity` / `estimated_value_gbp` / `filled_at` / `cancelled_at` when a terminal state is reached (FILLED / CANCELLED / REJECTED). Tries multiple field names for fill price (`fillPrice`, `executionDetails.price`, `fillResult.price`) to be robust across T212 API versions. **404 handling:** T212 removes filled market orders from the orders endpoint once they become positions — a 404 does not mean cancelled. When `GET /equity/orders/{id}` returns 404, the poller calls `get_position(t212_ticker)` to check for an active position: if one exists the order is marked FILLED; only if no position exists is it marked CANCELLED. **Fill price extraction on 404:** tries `averagePrice`, `avgPrice`, `avg_price` in order (skipping zero values); falls back to deriving average price as `currentPrice − ppl/quantity`; last resort uses `currentPrice`. The position's `quantity` is included in the synthetic resp so `filled_quantity` is always stored. **`estimated_value_gbp` on fill:** after confirming FILLED, computes `fill_price × effective_quantity` converted to GBP using live FX rates read from `price_data` (`GBPUSD=X`, `EURUSD=X`). T212 returns `averagePrice` in the instrument's native currency (USD for most UCITS ETFs, EUR for European ones, GBP/GBX for UK-listed), so this conversion is essential to store the true GBP cost basis rather than a native-currency amount or the original intended position size.

The Status page renders a **T212 Invest Account** section. When disabled: info card. When enabled + keys present: account data **auto-loads on page open and auto-refreshes every 60 seconds** — on each render the cached `st.session_state["t212_account_data"]` is checked; if absent or older than 60 seconds (and not in an error state), `get_account_summary()` + `get_positions()` + `poll_placed_orders()` are called silently before rendering. A "🔄 Refresh Account" button is still available for an immediate on-demand refresh. `_render_home_page()` ends with `time.sleep(60); st.rerun()` (guarded by `_t212_enabled and _t212_has_key` and no current error) so the Status page keeps cycling while open; if the log auto-refresh toggle is active it takes precedence (30 s interval). Displays: 4 account metrics, a deployed-capital progress bar, a redirect caption pointing to the Daily Briefing and Trade Log for open positions, recent orders (last 10) with colour-coded status (FILLED=green, PLACED=amber, FAILED/CANCELLED/REJECTED=red), a UCITS mapping table, and — when any FAILED orders are present — a **Retry failed orders** list. Each FAILED row shows the ticker, UCITS instrument ID, direction, quantity, timestamp, and truncated error message alongside a **↻ Retry** button and a **🗑 Delete** button. Clicking Retry re-fetches instrument metadata, applies the same precision floor and minimum-quantity guard, and places a new market order; success logs a new PLACED row in `t212_invest_orders` and shows an inline confirmation. Clicking Delete removes the row from `t212_invest_orders` permanently. The `t212_invest_error` banner (shown when a prior API error was stored in `system_metadata`) has a **✕ Clear** button that wipes the stored error key. Credentials (`T212_API_KEY`, `T212_API_SECRET`) are set in the systemd service file alongside `FRED_API_KEY`.

**`_get_t212_invest_brief_cards(config)`** (in `main.py`) is the shared helper that builds enriched trade-card dicts for open T212 Invest positions. It checks `st.session_state["t212_account_data"]` first (populated by the Status page auto-refresh); if absent or older than **90 seconds** it makes a fresh `get_positions()` call and updates session state. The 90-second TTL matches the Status page auto-refresh cycle so closed positions disappear from the Open Trade Monitor within two cycles rather than persisting for up to 5 minutes. For each UCITS ticker in `ucits_mapping` that has an open T212 position it: reverse-maps to the research ticker, reads the first FILLED BUY entry from `t212_invest_orders` for signal/score/date context, reads the current signal from the `signals` table, applies simplified status rules (STRONG_SELL → EXIT SIGNAL, SELL → WEAKENING, else ON TRACK), and sets `unreal_pnl_gbp` directly from T212's `ppl` field (already GBP). `max_holding_days` is set to `None` — `_render_trade_card()` renders these as "Day X" without a /max denominator. The Daily Briefing merges these cards with enriched CFD trades and sorts all by `status_priority`. The Trade Log renders them as a separate read-only table below the CFD position tracker. T212 Invest tickers are added to `open_tickers` so they are excluded from Today's Opportunities while a position is held.

**Forex exclusion:** Tickers with a `=X` suffix (e.g. `GBPUSD=X`, `EURUSD=X`) are never candidates for T212 Invest automation. T212 Invest is a cash/equity account that only supports stocks and ETFs — forex instruments are available only through T212's CFD account, which has no public API. The `ucits_mapping` in config.yaml should never include `=X` tickers; the BUY phase skips them implicitly because they will never appear in that mapping.

**Ticker substitution:** Some entries in `ucits_mapping` intentionally map a research ticker to a different T212 instrument. For example, `VIXY` maps to `trading212_instrument: "VXX"` because T212 does not list VIXY; VXX (iPath S&P 500 VIX Short-Term Futures ETN) tracks the same VIX short-term futures index and is the practical equivalent. The rationale for each substitution is documented as an inline comment on the relevant `ucits_mapping` entry in `config.yaml`.

**Quantity sizing note:** `get_quantity_for_gbp(ticker, gbp_amount)` uses the research ticker's USD close price from `price_data` to estimate share count. This is accurate for USD-denominated UCITS ETFs (e.g. S&P 500, treasury bond ETFs) whose per-share price tracks the US counterpart closely. It is approximate for GBP- or EUR-denominated UCITS ETFs (e.g. LSE-listed European equity ETFs) where the per-share price differs from the US equivalent — the instrument precision guard prevents the resulting quantity from causing a T212 rejection, but the notional order size may differ from `max_position_gbp`.

`T212InvestOrder` in `data/database.py` persists every order attempt: `order_id`, `ticker`, `t212_ticker`, `direction`, `quantity`, `estimated_price`, `estimated_value_gbp`, `fill_price`, `filled_quantity`, `status` (PLACED/FILLED/FAILED/CANCELLED/REJECTED), `signal_type`, `confluence_score`, `regime`, `account_type` (DEMO/LIVE), `error_message`, timestamps. `estimated_value_gbp` is always written at order time (even for FAILED orders) using the intended `position_gbp`; it is overwritten on fill confirmation with the true GBP cost basis (`fill_price × filled_quantity` converted from native currency). `regime` is read via `COALESCE(s.regime, s.notes)` from the signals table to handle both the current and legacy regime storage paths.

### Email alerts

`output/briefing.py` provides three email functions that all send **HTML emails with plain-text fallback** via a shared `_smtp_send(subject, text_body, html_body, config)` helper. `_smtp_send` builds a `MIMEMultipart("alternative")` message, attaches both text and HTML parts, reads `smtp_pass` from config or `os.environ.get("SMTP_PASSWORD", "")`, and handles the SMTP connection/login/sendmail in one place. All HTML uses inline CSS only (no flex/grid, `bgcolor` attribute on TD elements for Outlook compatibility).

- `send_alert_email(alerts, config)` — trade alert email. `_build_alert_html(alerts)` renders per-alert bordered table cards with a coloured status header. Called by `_trade_alert_job()`. `should_send_alert(config)` checks `alert_cooldown_hours` before sending.
- `send_weekly_summary_email(config)` — gathers data via `_gather_weekly_data(config)` (DB-only, no external API calls). `_build_weekly_email_html(data, config)` produces HTML sections: Market Regime & Overlays, Signal Snapshot, Last Week's Performance, Open Trades, Closed This Week, Signal Accuracy Progress (HTML progress bar), Macro Pulse, Watch List, Virtual Portfolio. Plain text built separately by `_build_weekly_email(data, config)`. Sends stamped to `system_metadata` under `last_weekly_summary_sent`. Respects `notifications.weekly_summary.enabled` independently of `notifications.email.enabled`.
- `send_test_email(config, ...)` — `_build_test_html(config, regime, last_analysis, open_trade_count)` produces a status table + confirmation strip. Bypasses the enabled flag; always sends. Returns a success string with recipient addresses.

When writing raw SQL against the `correlations` table in email or scheduler code, use the actual column names: `divergence` (not `status_30d`), `z_score` (not `z_score_30d`), `pair_score` (not `expected_direction`).

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

### Managing server credentials

API keys and passwords live in a systemd drop-in override file that is never touched by deploys:

```
/etc/systemd/system/investment-research.service.d/secrets.conf
```

```ini
[Service]
Environment=FRED_API_KEY=...
Environment=SMTP_PASSWORD=...
Environment=T212_API_KEY=...
Environment=T212_API_SECRET=...
```

After editing: `systemctl daemon-reload && systemctl restart investment-research`. The `investment-research.service` template in the repo has blank placeholders — never put real values there. The drop-in takes precedence and is unaffected by `deploy.sh` or `git pull`.

### Ongoing deploys (from Windows)

The easiest path is a plain `git push origin main` — the pre-push hook calls `deploy.ps1` automatically (see [Git hooks](#git-hooks) below). To deploy without that hook, or from a machine where it isn't installed:

```bash
bash deploy.sh          # Git Bash — VM_IP is hardcoded in the script
```

```powershell
.\deploy.ps1            # PowerShell — gitignored, local-only; SSH key at ssh-keys\
```

Both scripts push to GitHub first, then SSH into the VM to `git pull`, reinstall any new packages via `python3.11 -m pip install -r requirements.txt --quiet`, and `systemctl restart` the service. `deploy.sh` additionally copies `investment-research.service` from the repo to `/etc/systemd/system/` and runs `systemctl daemon-reload` before the restart, keeping the installed service file in sync with the repo template.

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

Edit `config.yaml` under `watchlist:` — add a dict with `ticker`, `name`, `exchange`, and `tradeable` to the appropriate group. No code changes needed; `get_all_tickers()` iterates all groups dynamically. The current watchlist has approximately 72 assets across 19 groups: `indices`, `sector_etfs`, `energy`, `commodities`, `bonds`, `volatility`, `international`, `forex`, `crypto`, `volatility_complex`, `tech_subsectors`, `financial_subsectors`, `healthcare_subsectors`, `international_expanded`, `real_assets`, `fixed_income_expanded`, `commodities_expanded`, `crypto_expanded`, `uk_etfs`, and any new groups added since. When adding a new group, also add its label to the `_GROUP_LABELS` dict in `main.py` so it appears on the Charts landing page. Forex pairs (e.g. `EURUSD=X`) return zero volume from yfinance — the volume module scores neutral for these, which is expected.

Optionally add `trading212_instrument: "XAG/USD"` when the T212 instrument name differs from the yfinance ticker — this pre-fills the Platform Instrument field in the Trade Log form.
