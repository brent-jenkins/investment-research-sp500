# Investment Research Dashboard

A self-hosted Streamlit application for daily market analysis, multi-module confluence signal generation, cross-asset correlation tracking, CFD trade management, historical backtesting, and automated virtual trading.

Live at [research.example.com](https://research.example.com) (password protected).

---

## What it does

The app tracks ~72 assets across 19 groups (indices, ETFs, commodities, forex, crypto, bonds, etc.), runs a seven-module technical and macro analysis pipeline, detects the current market regime, and scores every asset on a weighted confluence scale from −1 to +1. Signals are regenerated automatically on six scheduler jobs. Open CFD trades are monitored live with P&L in GBP, ATR-based stop losses, and automated health status. Automated email alerts fire when trades hit critical thresholds. A backtesting engine replays signal logic over up to 10 years of stored history.

---

## Running locally

```powershell
pip install -r requirements.txt
python -m streamlit run main.py
```

On the very first run, no data exists yet — click **Run Analysis** then **Generate Signals** on the Signals page, or wait for the next scheduled run. After that, analysis is fully automatic.

Add your FRED API key to `config.yaml` under `macro.fred_api_key` (free at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)). Without it, yield-curve and FRED macro scoring return 0; VIX scoring still works.

---

## Pages

### Daily Briefing (default page)
The operational home page. A single compact status bar shows the active market regime, key stats (^GSPC vs SMA200, ADX, VIX, yield spread, USD trend), and any active regime overlay pills — all in one bordered row. The regime description and signal-threshold caption appear below it. Active overlay detail strips follow with their full descriptions. FOMC key-event pills and signal distribution counts (all assets / tradeable-only) complete the header block.

Below the header:
- **Open Trade Monitor** — CFD/virtual trades and T212 Invest positions combined into a single list, sorted by urgency. Each card shows live GBP P&L, current signal, score delta since entry, ATR-based stop loss, live R:R, and a health status (ON TRACK / WEAKENING / EXIT SIGNAL / STOP LOSS). Tickers with an open T212 Invest position are excluded from Today's Opportunities to avoid duplicate signals.
- **Top Opportunities** — STRONG BUY / STRONG SELL signals filtered to tradeable assets by default. Each card shows the rationale, live price, and overlay confidence warning badges (MACRO_STRESS −20%, CREDIT_STRESS −15%, RECESSION_RISK −25%, EM_STRESS −15%) when relevant overlays are active for that ticker.

### Signals
Compact header block (regime badge, overlays, catch-up flags, signal distribution pills, timestamp — all in one bordered card). Market intelligence plain-English summary of signal breadth, VIX, ADX, and regime context. Full ranked signal table for all assets with module score symbols (▲ / ● / ▼) per column and signal-badge colour coding.

### Charts
Asset-group landing page with group cards. Clicking through shows a candlestick + volume chart, indicator panel (RSI, Bollinger Bands, MACD, ADX, SMA20/50/200), and raw indicator values for the most recent date.

### Relationships
Cross-asset correlation analysis across **28 predefined pairs**. Compact header shows the pair count and last-computed timestamp (read dynamically from `RELATIONSHIP_MAP`). 30-day correlation matrix heatmap across all price-data tickers (FRED series such as T10Y2Y and DFII10 are excluded from the heatmap). Pair-level table (30d corr, 90d corr, historical norm, z-score, CONFIRMING/DIVERGING/NEUTRAL status), and plain-English divergence narratives. Correlations are recomputed automatically on every analysis run.

| Pair | Expected direction |
|------|--------------------|
| GLD vs UUP | Inverse — gold rises when dollar falls |
| USO vs XLE | Positive — oil leads energy stocks |
| CPER vs ^GSPC | Positive — copper leads growth |
| HYG vs ^GSPC | Positive — credit stress precedes equity stress |
| ^VIX vs ^GSPC | Inverse — volatility spikes on market falls |
| TLT vs ^GSPC | Inverse — flight to safety in risk-off |
| GBPUSD=X vs ^FTSE | Inverse — strong pound hurts FTSE exporters |
| EURUSD=X vs ^GSPC | Positive — dollar strength affects multinationals |
| BTC-USD vs ^GSPC | Positive — risk-on correlation |
| SLV vs GLD | Positive — silver/gold ratio as risk-appetite indicator |
| TLT vs GLD | Positive — both benefit from falling real yields |
| USO vs UNG | Positive — oil and gas move on broad energy demand |
| XLF vs TLT | Inverse — banks benefit from rising rates (falling TLT) |
| XLK vs ^VIX | Inverse — tech most sensitive to risk appetite |
| XLU vs TLT | Positive — utilities act as bond proxies |
| GLD vs ^GSPC | Inverse — gold/equity ratio is the classic risk-off indicator |
| EEM vs UUP | Inverse — strong dollar weighs on emerging markets |
| HYG vs TLT | Inverse — credit spread proxy |
| BTC-USD vs GLD | Positive — both treated as inflation hedges |
| XLE vs USO | Positive — energy stocks vs oil price relative value |
| SOXX vs ^GSPC | Positive — semiconductors lead the broader market |
| KRE vs TLT | Inverse — regional banks benefit from rising rates |
| VNQ vs TLT | Positive — REITs act as bond proxies |
| FXI vs UUP | Inverse — strong dollar weighs on China equities |
| EMB vs UUP | Inverse — strong dollar raises EM sovereign debt costs |
| KBE vs T10Y2Y ¹ | Positive — banks expand NIMs when yield curve steepens |
| GLD vs DFII10 ¹ | Inverse — gold rallies when real yields fall |
| INDA vs UUP | Inverse — strong dollar weakens rupee and weighs on India equities |

¹ T10Y2Y and DFII10 are FRED macro series, not price tickers — these pairs read from the `macro_indicators` table via `_get_macro_changes()`.

Assets not directly in any pair inherit a correlation score via `CATEGORY_PROXY` in `analysis/correlations.py` — each proxy maps a ticker to a related pair member and blends 50% of that member's pair score into the ticker's confluence score. For example, COPX → GLD, XOP → USO, ITB → TLT, USDCNY=X → FXI. A proxy inheritance table is shown at the bottom of the Relationships page.

### Trade Log
Full CFD trade lifecycle: log new trades (auto-populates current signal; captures leverage, FX rate, investment amount, SL/TP, R:R, swap rate, spread cost, T212 instrument name), open-position tracker with live GBP P&L and per-trade edit expanders, close-trade form, and position sizing guidance based on account balance and per-trade risk settings. A **T212 Invest Positions** table below the CFD tracker shows all open automated ETF positions (ticker, UCITS ID, qty, avg/current price, current value £, P&L £/%, days held, current signal, status) — read-only, auto-managed by the EOD pipeline.

### Performance
Historical analysis of all closed trades: equity curve (cumulative GBP P&L), win rate by signal type and by regime, indicator hit-rate table, closed trades list. All sections filter to a selected date range.

### Backtest
Replay the current signal logic over up to 10 years of stored historical price data — no look-ahead bias (vectorized pandas rolling operations).

- **Run & Overview** — select date range (presets: Full History / COVID Era / Bear Market / Recovery / Custom), ticker scope (tradeable-only / **T212 CFD Only** / all assets), ATR stop multiplier, max holding days. Runs asynchronously and stores results in the database. Equity curve, full metrics strip (win rate, Sharpe, max drawdown, profit factor), and complete simulated trade table. A collapsible **T212 CFD comparison** panel instantly filters any existing run's trades to only tickers with a `trading212_instrument` mapping and shows a side-by-side metrics table plus a breakdown of excluded tickers and how many trades each contributed — no re-run required.
- **Period Analysis** — performance breakdown for COVID Crash (Feb–May 2020), 2022 Bear Market, and 2023–24 Recovery, plus per-regime win rate/return table.
- **Module Attribution** — for each analysis module, win rate when it agreed vs disagreed with the trade direction. Shows which modules have genuine predictive edge.
- **Signal Decay** — holding period distribution histogram, exit reason breakdown (STOP_LOSS / SIGNAL_REVERSAL / MAX_HOLDING / END_OF_DATA), average hold by regime.
- **Walk-Forward** — rolling 6-month performance windows in 1-month steps. Consistent win rates across windows indicate the signal logic has stable edge.
- **Virtual Trading** — view open/closed virtual positions and weekly virtual P&L (requires virtual trading enabled in config).

### Status
System status grid (Last Deployment, Service Uptime, Database Size, Last Data Refresh, Last Analysis Run, Next Scheduled Run). Manual Controls: **Run Analysis Now** (analysis + signals only) and **Refresh Data + Analysis** (price pull + FRED macro fetch + full analysis pipeline — equivalent to the EOD scheduler job). Send Test Email and Send Weekly Summary Now buttons for verifying email delivery.

A **T212 Invest Account** panel shows the live connection state when `trading212_invest.enabled: true` in config.yaml, including the DEMO/LIVE badge, position/deployment limits, and the effective entry threshold for the current regime (flagged "(override)" when `entry_signal_overrides` applies one, or a `⛔ Entry blocked` caption in place of the threshold when the current regime isn't in `allowed_entry_regimes` — see "Virtual trading" below). It is split into two tabs:
- **Account tab** — account value, available cash, deployed-capital progress bar, and the last 10 automated orders with colour-coded status (FILLED green / PLACED amber / FAILED·CANCELLED·REJECTED red). Data is read from a scheduler-maintained DB snapshot; the **Refresh Account** button queues an out-of-band fetch (handled by the 60-second job dispatcher, "updating within 60s"). When FAILED orders are present a **Retry failed orders** list appears — each row shows the ticker, direction, quantity, and error with a **Retry** and **Delete** button. The Retry button applies the instrument precision/minimum-quantity guard and re-submits.
- **Mapping tab** — UCITS instrument mapping table (which research tickers map to T212 instrument IDs).

**Note:** T212 removes filled market orders from the orders endpoint once they become open positions — a 404 response does not mean cancelled. The poller checks for an active position on 404; if one exists the order is marked FILLED with `averagePrice` from the position (derived as `currentPrice − ppl/quantity` if `averagePrice` is null), and `estimated_value_gbp` is updated with `fill_price × quantity` converted to GBP using live FX rates — T212 returns prices in the instrument's native currency (USD, EUR, or GBP) so conversion is required for correct cost basis. Only if no position is found is the order marked CANCELLED. Open positions are displayed on the **Daily Briefing** and **Trade Log** pages, not here.

> **Forex pairs excluded by design:** Tickers ending in `=X` (e.g. `GBPUSD=X`, `EURUSD=X`) are never automated through T212 Invest. T212 Invest is a cash/equity account that supports stocks and ETFs only — forex is CFD-only, and the T212 CFD account has no public API. All forex trades remain manual CFD positions logged in the Trade Log.
>
> **Ticker substitutions in `ucits_mapping`:** Some research tickers are mapped to a different T212 instrument where T212 doesn't carry the exact fund. For example, `VIXY` maps to `VXX` — VXX (iPath S&P 500 VIX Short-Term Futures ETN) is the T212-available equivalent, tracking the same VIX short-term futures index. Each substitution is documented as an inline comment on the relevant `ucits_mapping` entry in `config.yaml`.
>
> **Tradeable assets without a `ucits_mapping` entry:** `SHY`, `JNK`, and `XLRE` are `tradeable: true` in the watchlist so they generate signals, appear in opportunities, and can be logged as manual CFD trades — but they have no `ucits_mapping` entry and therefore cannot receive automated T212 Invest orders. `JNK`'s omission is intentional: `HYG` is already mapped and covers essentially the same US high-yield bond exposure (adding JNK would open a duplicate position in the same index). `SHY` and `XLRE` are likely gaps — UCITS equivalents exist for both (iShares $ Treasury Bond 1-3yr ETF; SPDR S&P U.S. Real Estate Select Sector UCITS ETF), and their absence is inconsistent with the pattern of TLT/IEF/TIP being mapped and all other major SPDR sector ETFs being mapped. Consider adding their T212 instrument IDs to `ucits_mapping` in `config.yaml`.

---

## Analysis pipeline

### Seven scoring modules

Each module returns a score of −1 (bearish), 0 (neutral), or +1 (bullish):

| Module | What it measures |
|--------|-----------------|
| **Trend** | SMA20/50/200 alignment, MACD histogram, ADX direction, golden/death cross |
| **Mean Reversion** | RSI (oversold/overbought), Bollinger Band %B, 52-week z-score |
| **Volatility** | ATR%, historical volatility, Bollinger Band width regime |
| **Volume** | Volume vs 20-day SMA, OBV trend direction |
| **Macro** | VIX regime, yield curve, plus 12 additional FRED series with per-ticker routing (see below) |
| **Correlation** | Cross-asset pair status for pairs involving the ticker (CONFIRMING = +1, DIVERGING = −1, NEUTRAL = 0) |
| **Regime** | Not directly scored per ticker; determines the weights applied to the other six modules |

> **Trend module's golden/death cross vote is direction-aware, not purely positional.** A golden cross (SMA50 > SMA200) only votes bullish while SMA50 is rising — a declining SMA50 that's still above SMA200 from an earlier uptrend votes neutral (stale cross), or flips bearish if the two lines have converged within `signals.ma_cross_near_pct` (default 2%) of each other, since that's a warning of an imminent death cross rather than confirmation of bullish momentum. Death crosses are scored with the same logic mirrored. The slope lookback is `signals.ma_cross_slope_window` (default 10 trading days). The positional `golden_cross` flag shown on the Charts page indicator panel ("Golden ✓" / "Death ✗") is unchanged and reflects the raw SMA50-vs-SMA200 position; a "⚠ SMA50 declining/rising" caption appears underneath when the direction-aware vote disagrees with it.

### FRED macro series

The macro module fetches and scores these series (stored in `macro_indicators` table). Beyond the composite average score, several series apply ticker-specific adjustments inside `compute_macro()`:

| Series | What it is | Scoring / ticker-specific routing |
|--------|-----------|----------------------------------|
| T10Y2Y | 10Y−2Y yield spread | Bearish when inverted; drives regime detection |
| T10Y3M | 10Y−3M yield spread | Bearish when inverted; drives regime detection |
| DFII10 | 10-year real yield | Bearish when > 2% or z-score > 1 |
| T10YIE | 10-year breakeven inflation | Bearish when < 1% or > 3.5% |
| BAMLH0A0HYM2 | HY credit OAS | Bearish on rapid widening or > 5%; CREDIT_STRESS overlay trigger |
| BAMLC0A0CM | IG credit OAS | Bearish when > 1.5% |
| CFNAI | Chicago Fed National Activity Index | Equity-group boost +0.10 (> 0.20) / penalty −0.10 (< −0.20) / −0.20 (< −0.70); RECESSION_RISK overlay trigger |
| DFEDTARU | Fed funds rate (upper bound) | Rising rates: −0.10 VNQ/XLRE/ITB/rate-bearish, +0.10 KBE/KRE/XLF; falling rates: opposite |
| M2SL | M2 money supply | Growing > 0.5% MoM: +0.10 crypto, +0.05 gold; shrinking < −0.5%: −0.10 crypto |
| PERMIT | Housing permits (SAAR) | Falling > 5% MoM: −0.10 penalty for VNQ, XLRE, ITB |
| DTWEXBGS | Trade-weighted dollar | Bearish when rising (z-score > 0.5); EM_STRESS overlay trigger when 30d change > +3% |
| DPCREDIT | Fed discount window rate | Composite score contribution; shown as rate % vs Fed Funds spread |
| BOGMBASE | Monetary base | Composite score contribution |

VIX is sourced from the local `price_data` table (^VIX ticker), not FRED.

Self-referential macro tickers (^VIX, TLT, IEF, HYG) receive a neutral macro score of 0.

The Macro Dashboard tags each card: **✓ Signals** for series with ticker-specific routing or overlay triggers; **Composite** for series that contribute to the average score only.

### Regime detection

Reads the most recent stored ^GSPC indicators. Priority order:

1. VIX > 30 → `HIGH_VOLATILITY`
2. price > SMA200, ADX > 25, VIX < 20 → `TRENDING_BULL`
3. price < SMA200, ADX > 25, VIX > 20 → `TRENDING_BEAR`
4. ADX < 20, VIX < 15 → `RANGING_LOW_VOL`
5. else → `MIXED`

The ADX thresholds (> 25 for trending, < 20 for ranging) are intentionally strict. ADX in the 20–25 range correctly produces `MIXED`, which signals that regime certainty is low and keeps position sizing conservative.

### Regime weights (sum to 1.0 per regime)

| Module | TRENDING_BULL | TRENDING_BEAR | RANGING_LOW_VOL | HIGH_VOLATILITY | MIXED |
|--------|:---:|:---:|:---:|:---:|:---:|
| Trend | 0.30 | 0.25 | 0.10 | 0.10 | 0.18 |
| Mean Reversion | 0.10 | 0.15 | 0.35 | 0.15 | 0.18 |
| Volatility | 0.15 | 0.20 | 0.15 | 0.35 | 0.18 |
| Volume | 0.20 | 0.15 | 0.15 | 0.15 | 0.18 |
| Macro | 0.15 | 0.15 | 0.15 | 0.15 | 0.18 |
| Correlation | 0.10 | 0.10 | 0.10 | 0.10 | 0.10 |

### Regime overlays

Detected by `detect_overlays()` after each correlation run. Five overlays are defined:

| Overlay | Trigger | Confidence impact |
|---------|---------|------------------|
| `MACRO_STRESS` | TLT/^GSPC correlation DIVERGING (z ≥ 1.5) | −20% on equity/bond BUY signals |
| `VOL_REGIME_SHIFT` | ^VIX/^GSPC correlation DIVERGING (z ≥ 1.5) | Informational warning only |
| `CREDIT_STRESS` | HYG/TLT return-spread z-score < −1.0 | −15% on equity BUY signals |
| `RECESSION_RISK` | CFNAI < −0.70 for 2 consecutive months | −25% on all equity BUY signals |
| `EM_STRESS` | DTWEXBGS 30-day change > +3% (dollar surging) | −15% on EM asset BUY signals |

### Confluence scoring

`weighted_score = Σ(module_score × regime_weight)` ∈ [−1, +1]

- ≥ `buy_threshold` → `STRONG_BUY`
- ≥ 0.30 → `BUY`
- (−0.30, +0.30) → `NEUTRAL`
- ≤ −0.30 → `SELL`
- ≤ `−sell_threshold` → `STRONG_SELL`

STRONG BUY/SELL boundaries are **regime-aware**. All five regimes have explicit entries under `confluence.regime_thresholds` in `config.yaml` — there is no separate global default:

| Regime | Buy threshold | Sell threshold | Rationale |
|--------|:---:|:---:|---|
| `TRENDING_BULL` | 0.55 | 0.70 | Standard buy; harder to short in a bull |
| `TRENDING_BEAR` | 0.70 | 0.55 | Harder to go long in a bear; standard sell |
| `MIXED` | 0.60 | 0.60 | Require moderate conviction in uncertain conditions |
| `HIGH_VOLATILITY` | 0.70 | 0.70 | Very strong signal required when volatility is elevated |
| `RANGING_LOW_VOL` | 0.55 | 0.55 | Mean-reversion regime — standard threshold |

The backtest engine applies the same per-date regime thresholds. All downstream consumers (email alerts, virtual trading, trade health status) read the stored `signal` classification from the database — no re-classification from raw scores occurs outside `generate_signals()`.

The Daily Briefing shows the current effective thresholds and the approximate number of signal modules that must agree (e.g. "Threshold: 0.60 (MIXED) — requires ~4/7 modules to agree").

### Feedback learning

After 20+ closed trades, `recalculate_learned_weights()` computes per-module accuracy and adjusts confluence weights proportionally, clamped between 0.05 and 0.30. Learned weights override static regime weights and are stored in `system_metadata` under `learned_weights`.

### Automatic scheduling

Seven APScheduler jobs run automatically from a **standalone scheduler daemon** (`scheduler_daemon.py`) managed by `investment-research-scheduler.service`. The daemon starts at boot and fires jobs on their UTC schedule regardless of whether anyone has visited the dashboard — no browser connection required.

| Schedule | Type | What it does |
|----------|------|--------------|
| **Every 60 seconds** | Job dispatcher | Writes `scheduler_heartbeat` to `system_metadata`; checks `pending_t212_fetch` flag and, when set, calls `_fetch_t212_snapshot()` to refresh the T212 account/positions snapshot |
| **Mon–Fri 07:00 UTC** | Asian EOD | `pull_incremental()` for exchanges closed before 07:00 UTC (e.g. JPX/Tokyo), then full analysis pipeline; opens virtual trades for those tickers only |
| **Mon–Fri 13:30 UTC** | Analysis only | `run_analysis()` → `generate_signals()` → `compute_all_pairs()` — before US open, no data pull |
| **Mon–Fri 19:05 UTC** | FOMC reaction | Analysis-only pipeline on FOMC decision days only (Fed statement released ~18:00–19:00 UTC); silently skips on non-FOMC days |
| **Mon–Fri 21:30 UTC** | Full refresh | `pull_incremental()` for open exchanges, then full analysis pipeline + FRED macro fetch |
| **Mon–Fri 00:00–21:00 + 23:00 UTC (hourly)** | Trade alerts | Fetches live prices for open trades; emails alert on STOP LOSS or EXIT SIGNAL — filtered per-trade by home exchange session; refreshes the T212 account/positions snapshot unconditionally on each run |
| **Sunday 10:00 UTC** | Weekly summary | Sends the weekly summary email covering signals, open trades, closed P&L, accuracy progress, macro pulse, diverging pairs |

All three analysis runs also call `_process_virtual_trades()` to auto-manage virtual positions.

The Streamlit web UI (`investment-research.service`) also starts an in-process scheduler as a fallback, but checks the `scheduler_heartbeat` key in `system_metadata` first — if the heartbeat is less than 90 seconds old the daemon is active and the in-process scheduler is skipped, preventing duplicate job execution.

---

## Backtesting

`analysis/backtest.py` replays the current signal logic over stored historical price data. The correlation module is excluded (it requires pair-level data and is not stored per-date in isolation); its weight is re-normalised across the remaining five modules.

Entry is gated per date by the same two-step regime logic used by live automation (see "Per-regime entry signal overrides" and "Regime entry gate" in Configuration): `simulate_trades()` first checks `analysis/regime.py:is_entry_allowed()` against `virtual_trading.allowed_entry_regimes` — a regime not in that list (MIXED, RANGING_LOW_VOL) blocks entry outright for that date, before any signal is even considered — then, for an allowed regime, resolves the effective threshold via `analysis/regime.py:resolve_entry_signal()` against `virtual_trading.entry_signal_overrides`, entering only on STRONG_BUY/STRONG_SELL by default, or also on plain BUY/SELL where that regime's override loosens the threshold. This keeps simulated results consistent with what the live system would actually have traded, rather than a fixed STRONG_BUY-only bar (or worse, no regime gate at all) across every regime.

Exit conditions (checked in priority order, unaffected by either entry gate):
1. **Stop Loss** — price crosses entry ± N × ATR (configurable `atr_stop_multiplier`, default 2.0)
2. **Signal Reversal** — signal moves opposite to trade direction (SELL/STRONG_SELL for LONG)
3. **Max Holding Days** — position closed after a configurable cap (default 60 days)

Results are stored in three tables (`backtest_runs`, `backtest_signals`, `backtest_trades`) and analysed by `analysis/backtest_metrics.py` which provides: full metrics (Sharpe, Calmar, profit factor, max drawdown), equity curve, period breakdown, module attribution, signal decay histogram, and rolling walk-forward validation.

---

## Virtual trading

When `virtual_trading.enabled: true`, the system automatically manages a simulated portfolio in the `trades` table (identified by `account_type = 'VIRTUAL'`). After every analysis pipeline run:

- **Opens** a new virtual position on STRONG_BUY / STRONG_SELL signals for tradeable tickers (subject to `max_open_positions` cap).
- **Closes** existing virtual positions when the signal reverses direction or the current price crosses the ATR-based stop loss.

Virtual positions are sized at `position_pct` of `portfolio_size_gbp` (default 5% of £2,000 = £100 per trade). P&L is recorded as a price-change percentage (no FX or leverage applied). Virtual trades are visible on the Backtest page → Virtual Trading tab (which shows the effective entry threshold for the current regime, flagged when overridden) and in the weekly summary email.

**Per-regime entry threshold.** `virtual_trading.entry_signal` (and `trading212_invest.entry_signal`) is a global default that can be overridden per detected regime via `entry_signal_overrides` — see "Configuration" below for the full rationale and structure. `analysis/regime.py:resolve_entry_signal()` resolves the effective threshold each run using the regime stamped on the most recent `^GSPC` row in the `signals` table, so it always matches the regime that produced the signal being acted on — not a value that could drift from a freshly recomputed regime.

---

## Email alerts

All three email functions send **HTML emails with plain-text fallback** via a shared `_smtp_send()` helper in `output/briefing.py`. Emails are built as `MIMEMultipart("alternative")` with inline CSS only (Outlook-compatible — no external stylesheets, no flex/grid, `bgcolor` on TD elements). SMTP password is read from config or the `SMTP_PASSWORD` environment variable.

| Type | When | Controlled by |
|------|------|--------------|
| **Trade alert** | Hourly (14:00–21:00 UTC weekdays) when any open trade hits STOP LOSS or EXIT SIGNAL | `notifications.email.enabled` + per-trade deduplication via `system_metadata` |
| **Weekly summary** | Every Sunday at 10:00 UTC | `notifications.weekly_summary.enabled` |
| **Test email** | On demand via Status page button | Always sends (ignores enabled flag) |

The weekly summary covers: regime + overlays, signal snapshot with tradeable counts, 7-day signal performance (directional hit rate), open trade status, trades closed that week, signal accuracy progress toward the 20-trade learning threshold (HTML progress bar), FRED macro pulse (VIX + key series with week-over-week change), diverging pairs and catch-up flags, and virtual portfolio status.

The trade alert cooldown (`alert_cooldown_hours: 8`) prevents email storms. Each trade's last-alerted status is stored in `system_metadata` under `trade_alert_status:{trade_id}` to prevent duplicate alerts for the same condition.

Default relay: Brevo (smtp-relay.brevo.com:587). Any STARTTLS-capable relay works. Set the SMTP password via the `SMTP_PASSWORD` environment variable rather than the config file.

---

## Trade model

Trades capture the full CFD context at the time of entry:

| Field | Description |
|-------|-------------|
| `direction` | LONG or SHORT |
| `account_type` | PRACTICE, LIVE, or VIRTUAL |
| `account_name` | Named account (e.g. "Trading212 Practice") |
| `currency` | Instrument quote currency (e.g. USD for US ETFs) |
| `account_currency` | Account currency (GBP) |
| `fx_rate_at_entry` | Spot rate used to convert P&L to GBP |
| `leverage` | CFD leverage multiplier |
| `investment_amount` | Cash deployed in GBP |
| `position_value` | investment_amount × leverage |
| `overnight_rate` | Daily swap as % of basis amount |
| `spread_cost` | Opening spread cost in GBP |
| `fx_fee_pct` | Currency conversion fee % |
| `stop_loss_price` | Planned stop loss (preferred over ATR-computed SL in the UI) |
| `take_profit_price` | Planned take profit |
| `planned_risk_gbp` | Estimated GBP loss if stop loss is hit |

P&L on close: `(exit − entry) × qty × leverage` converted to GBP via `fx_rate_at_entry`, minus `fx_fee_pct` on `position_value`. JPY-quoted forex CFDs (USDJPY=X) use a two-step conversion: pips → JPY → USD → GBP.

---

## Database schema

SQLite via SQLAlchemy, WAL mode, `NullPool` (no connection pooling — each `with conn:` block gets a fresh connection, safe for Streamlit's multi-threaded reruns). All writes are upserts keyed on `(ticker, date)`.

| Table | Purpose |
|-------|---------|
| `price_data` | Daily OHLCV per ticker |
| `indicators` | Computed technical + macro indicators per ticker per date |
| `signals` | Confluence scores and signal classifications per ticker per date |
| `correlations` | Cross-asset pair stats (30d corr, 90d corr, hist mean/std, z-score, status) |
| `macro_indicators` | FRED series values with 30d pct change, 2yr z-score, and −1/0/+1 score |
| `opportunity_flags` | Catch-up opportunity flags with 5d/20d return tracking |
| `trades` | Full CFD trade ledger (open and closed, including VIRTUAL account type) |
| `performance` | Rolling performance metrics per ticker (Sharpe, Sortino, Calmar, drawdown) |
| `backtest_runs` | Metadata for each backtest run (params, status, summary metrics) |
| `backtest_signals` | Per-ticker per-date signal history generated during a backtest run |
| `backtest_trades` | Simulated trades from each backtest run with full entry/exit/P&L detail |
| `system_metadata` | Key-value store for operational state (timestamps, learned weights, alert status) |

Schema migrations (`_apply_migrations()`) run on every startup. One-time data fixes (`_apply_data_fixes()`) are guarded by value-based conditions. Stale tickers are purged from all tables on every startup via `_purge_unlisted_tickers()`. Rows with a null or zero close price are also purged from `price_data` on every startup — these are incomplete intraday bars that yfinance occasionally returns for sessions that hadn't closed at fetch time, and a single such row breaks the rolling-mean SMA line on the chart for the following 20–50 trading days. The ingestion layer (`_upsert_price_rows`) now also skips these rows before they reach the database.

---

## Configuration (`config.yaml`)

Key sections (file is gitignored — manage on server directly):

```yaml
macro:
  fred_api_key: "YOUR_KEY_HERE"

notifications:
  email:
    enabled: true
    smtp_host: "smtp-relay.brevo.com"
    smtp_port: 587
    smtp_user: ""
    smtp_password: ""     # or SMTP_PASSWORD env var
    from_address: ""
    to_addresses: []
    alert_cooldown_hours: 8
  weekly_summary:
    enabled: true         # set false to disable without touching email.enabled

backtest:
  max_holding_days: 30           # default max hold — equities, ETFs, commodities
  max_holding_days_forex: 5      # FX pairs (ticker ends with =X) — swap costs make long holds expensive
  max_holding_days_crypto: 14    # crypto (ticker ends with -USD) — high volatility, shorter cycles
  max_holding_days_bonds: 45     # bond ETFs (TLT, IEF, SHY, TIP) — slow-moving, longer trend duration
  atr_stop_multiplier: 2.0       # stop loss = entry ± N × ATR
  walk_forward_months: 6
  walk_forward_step: 1

virtual_trading:
  enabled: true
  portfolio_size_gbp: 2000
  position_pct: 0.05             # 5% per trade
  max_open_positions: 10
  entry_signal: "STRONG_BUY"    # global default — STRONG_BUY = strict; BUY = looser
  entry_signal_overrides:       # per-regime override, keyed by Regime enum name — falls
    MIXED: STRONG_BUY           # back to entry_signal above for regimes not listed here.
    RANGING_LOW_VOL: STRONG_BUY # trading212_invest.entry_signal_overrides uses the same
    HIGH_VOLATILITY: STRONG_BUY # shape — see the Status page's T212 Invest Account section.
    TRENDING_BULL: STRONG_BUY
    TRENDING_BEAR: STRONG_BUY
  allowed_entry_regimes:        # checked BEFORE entry_signal_overrides — a regime not
    - TRENDING_BULL             # listed here blocks new entries outright, regardless of
    - TRENDING_BEAR             # signal strength. MIXED and RANGING_LOW_VOL are
    - HIGH_VOLATILITY           # deliberately absent (both blocked) — see below.
  max_theme_positions: 3        # max simultaneous positions per macro theme (e.g. USD-short forex)

learning:
  enabled: true
  min_trades_before_adjustment: 20
  max_single_weight: 0.30
  min_single_weight: 0.05
```

### Per-regime entry signal overrides

Backtesting showed uneven edge by regime: MIXED had 416 trades at a 37.5% win rate, profit factor 0.123, and −91.26% max drawdown, while a short-sample backtest of TRENDING_BULL (PF 0.631) and TRENDING_BEAR (PF 0.202) initially looked like it had genuine edge. `entry_signal_overrides` (shown on `virtual_trading` above; `trading212_invest` supports the identical key) is the mechanism for raising the conviction threshold per regime — currently all five regimes are set to `STRONG_BUY`. A regime with no entry in the overrides dict falls back to the global `entry_signal`. Both live automation paths (`brokers/trading212.py:maybe_place_t212_orders()` and `data/scheduler.py:_process_virtual_trades()`) resolve this once per run via `analysis/regime.py:resolve_entry_signal()`, keyed on the regime most recently stamped on `^GSPC`'s row in the `signals` table — so the threshold applied always matches the regime that scored the signal being acted on. Only entries are affected; `exit_signal` has no regime override. The resolved regime is what gets logged to the `regime` column on `t212_invest_orders` and virtual `trades` rows, keeping the applied threshold auditable.

**Why TRENDING_BULL/TRENDING_BEAR aren't looser.** They were briefly set to `BUY` based on the short-sample numbers above, then reverted after a full 10-year backtest showed that was a mistake. `simulate_trades()`'s signal-reversal exit has always accepted plain SELL/BUY, not just STRONG_SELL/STRONG_BUY; pairing that with a loosened BUY *entry* created high-churn whipsaw trading in trending regimes — TRENDING_BULL and TRENDING_BEAR were only ~25% of all regime-days across 2016–2026 but generated 90% of all simulated trades, and win rate/profit factor/drawdown all got materially worse. Confirmed as a real strategy effect (not a code bug) via an independent from-scratch reimplementation of the entry/exit walk-forward that produced identical trade lists to the production function on real data. After reverting to uniform `STRONG_BUY`, a full-history production backtest exactly reproduced the original pre-feature baseline (757 trades / 41.2% win rate / PF 1.35 / −85.4% max drawdown), confirming the current config is a functional no-op relative to the hardcoded behaviour that existed before this feature.

**The backtest engine applies the same `entry_signal_overrides` logic** (against `virtual_trading`'s config specifically — see "Backtesting" above), so simulated results reflect the live system's current per-regime entry gating rather than a single fixed threshold across every regime.

### Regime entry gate

`entry_signal_overrides` alone can only tighten a regime down to `STRONG_BUY` — the 10-year backtest showed that isn't enough. MIXED is 56.8% of all regime-days but has no demonstrable edge even at STRONG_BUY-only (416 trades, 37.5% win rate, −91.26% max drawdown), and RANGING_LOW_VOL has only 1 trade of backtest data — too little to trust either way. `allowed_entry_regimes` (shown on `virtual_trading` above; `trading212_invest` supports the identical key) is a stricter gate, checked *before* `entry_signal_overrides`: a regime not in the list blocks new entries entirely, no matter how strong the signal. `HIGH_VOLATILITY` is kept allowed for now purely for lack of data to exclude it.

`analysis/regime.py:is_entry_allowed(config, regime_name, section)` implements the check — `section` is `"trading212_invest"` or `"virtual_trading"`. If `allowed_entry_regimes` is absent from a config section entirely, every regime is allowed (the backwards-compatible default for configs written before this gate existed).

**The exit phase is always active, regardless of regime.** The gate only ever suppresses *new* entries:
- `maybe_place_t212_orders()` checks it at the very start of the BUY phase (before the deployed-capital check) and returns immediately if blocked, logging `"Skipping T212 Invest BUY phase — regime is MIXED, entries blocked"`. The EXIT phase runs first, unconditionally — existing T212 positions keep exiting normally on signal reversal, confirmed NEUTRAL, or the backstop time limit in any regime.
- `_process_virtual_trades()` checks it at the start of the open phase only; the close phase (stop loss, signal reversal) is unaffected.
- `simulate_trades()` checks it per date, only when no position is open; a trade that opened in an allowed regime still exits normally even if the regime later rolls into a blocked one.

The Daily Briefing and the Status page's T212 Invest Account section both show a `⛔ Entries blocked — MIXED regime` caption in place of the entry-threshold caption whenever the current regime isn't in `allowed_entry_regimes`, so it's immediately visible that no new positions will open.

### Tradeable vs analysis-only

Each watchlist entry has a `tradeable: true/false` flag. Assets with `tradeable: false` are included in analysis but excluded from opportunity filtering, Trade Log, and virtual trading. The Signals page shows all assets with a tradeable sub-count on each signal distribution.

---

## Server deployment

| Item | Value |
|------|-------|
| Provider | Cloud VPS |
| OS | Ubuntu 22.04 LTS |
| Python | 3.11 system-wide (no virtualenv) |
| Proxy | Caddy (automatic TLS + basic auth) |
| Process manager | systemd (`investment-research.service` + `investment-research-scheduler.service`) |

### First-time setup

```bash
git clone https://github.com/YOUR/investment-research.git /root/investment-research
cd /root/investment-research
bash setup.sh your.domain.com
```

Set the dashboard password after setup:

```bash
caddy hash-password && nano /etc/caddy/Caddyfile   # replace REPLACE_WITH_HASH
systemctl reload caddy
```

### Managing server credentials

API keys and passwords are kept in a systemd drop-in override file that is never touched by deploys:

```bash
mkdir -p /etc/systemd/system/investment-research.service.d/
nano /etc/systemd/system/investment-research.service.d/secrets.conf
```

```ini
[Service]
Environment=FRED_API_KEY=your_fred_key
Environment=SMTP_PASSWORD=your_smtp_password
Environment=T212_API_KEY=your_t212_key
Environment=T212_API_SECRET=your_t212_secret
```

After creating or editing the file: `systemctl daemon-reload && systemctl restart investment-research && systemctl restart investment-research-scheduler`

The `investment-research.service` template in the repo has blank placeholders for these variables. Never fill in real values there — the drop-in file takes precedence and survives all future deploys.

The scheduler daemon automatically inherits the same credentials: `setup.sh` and both deploy scripts create a symlink at `/etc/systemd/system/investment-research-scheduler.service.d/secrets.conf` pointing to the same drop-in file, so you only need to manage one secrets file.

### Deploying updates

```bash
bash setup-hooks.sh   # install pre-push deploy hook (run once)
git push origin main  # triggers automatic deploy
```

Manual deploy: `bash deploy.sh` (Git Bash) or `.\deploy.ps1` (PowerShell, gitignored). Both scripts copy both service files to `/etc/systemd/system/`, ensure the secrets symlink exists, run `systemctl daemon-reload`, and restart both `investment-research` and `investment-research-scheduler`.

### Useful server commands

```bash
journalctl -u investment-research -f              # web UI live logs
journalctl -u investment-research-scheduler -f   # scheduler daemon live logs
systemctl status investment-research
systemctl status investment-research-scheduler
systemctl restart investment-research
systemctl restart investment-research-scheduler
```

---

## Extending the app

### Adding assets

Edit `config.yaml` under `watchlist:` — add a dict with `ticker`, `name`, `exchange`, and `tradeable`. No code changes needed. If adding a new group, also add its label to `_GROUP_LABELS` in `main.py`.

### Adding a new analysis module

1. Create `analysis/your_module.py` with `compute_your_module(df, config) -> dict` returning `{"score": int, ...raw_values}`.
2. Call it in `analysis/engine.py:analyse_ticker()` and merge raw values.
3. Call it in `analysis/confluence.py:score_ticker()` and include its score.
4. Add weights to every regime dict in `REGIME_WEIGHTS` — weights must sum to 1.0.
5. Add `your_module_score` to the `Signal` model, `_apply_migrations()`, and the upsert in `_persist_signal()`.
6. Add raw-value columns to the `Indicator` model and `_apply_migrations()`.
7. Update `total_signal_modules` in `config.yaml`.

Always maintain both TA-Lib and pure-pandas fallback paths (`_TALIB_AVAILABLE` flag pattern).
