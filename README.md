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

### 📅 Daily Briefing (default page)
The operational home page. Compact header block shows the active market regime, all key stats (^GSPC vs SMA200, ADX, VIX, yield spread, USD trend), any active regime overlays or catch-up opportunity flags, signal distribution counts, and the last-updated timestamp — all in one dense card.

Below the header:
- **Open Trade Monitor** — CFD/virtual trades and T212 Invest positions combined into a single list, sorted by urgency. Each card shows live GBP P&L, current signal, score delta since entry, ATR-based stop loss, live R:R, and a health status (ON TRACK / WEAKENING / EXIT SIGNAL / STOP LOSS). Tickers with an open T212 Invest position are excluded from Today's Opportunities to avoid duplicate signals.
- **Top Opportunities** — STRONG BUY / STRONG SELL signals filtered to tradeable assets by default. Each card shows the rationale, live price, and overlay confidence warning badges (MACRO_STRESS −20%, CREDIT_STRESS −15%, RECESSION_RISK −25%, EM_STRESS −15%) when relevant overlays are active for that ticker.

### 📊 Signals
Compact header block (regime badge, overlays, catch-up flags, signal distribution pills, timestamp — all in one bordered card). Market intelligence plain-English summary of signal breadth, VIX, ADX, and regime context. Full ranked signal table for all assets with module score symbols (▲ / ● / ▼) per column and signal-badge colour coding.

### 📈 Charts
Asset-group landing page with group cards. Clicking through shows a candlestick + volume chart, indicator panel (RSI, Bollinger Bands, MACD, ADX, SMA20/50/200), and raw indicator values for the most recent date.

### 🔗 Relationships
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

### 📋 Trade Log
Full CFD trade lifecycle: log new trades (auto-populates current signal; captures leverage, FX rate, investment amount, SL/TP, R:R, swap rate, spread cost, T212 instrument name), open-position tracker with live GBP P&L and per-trade edit expanders, close-trade form, and position sizing guidance based on account balance and per-trade risk settings. A **T212 Invest Positions** table below the CFD tracker shows all open automated ETF positions (ticker, UCITS ID, qty, avg/current price, current value £, P&L £/%, days held, current signal, status) — read-only, auto-managed by the EOD pipeline.

### 🏆 Performance
Historical analysis of all closed trades: equity curve (cumulative GBP P&L), win rate by signal type and by regime, indicator hit-rate table, closed trades list. All sections filter to a selected date range.

### 🧪 Backtest
Replay the current signal logic over up to 10 years of stored historical price data — no look-ahead bias (vectorized pandas rolling operations).

- **Run & Overview** — select date range (presets: Full History / COVID Era / Bear Market / Recovery / Custom), ticker scope (tradeable-only / **T212 CFD Only** / all assets), ATR stop multiplier, max holding days. Runs asynchronously and stores results in the database. Equity curve, full metrics strip (win rate, Sharpe, max drawdown, profit factor), and complete simulated trade table. A collapsible **T212 CFD comparison** panel instantly filters any existing run's trades to only tickers with a `trading212_instrument` mapping and shows a side-by-side metrics table plus a breakdown of excluded tickers and how many trades each contributed — no re-run required.
- **Period Analysis** — performance breakdown for COVID Crash (Feb–May 2020), 2022 Bear Market, and 2023–24 Recovery, plus per-regime win rate/return table.
- **Module Attribution** — for each analysis module, win rate when it agreed vs disagreed with the trade direction. Shows which modules have genuine predictive edge.
- **Signal Decay** — holding period distribution histogram, exit reason breakdown (STOP_LOSS / SIGNAL_REVERSAL / MAX_HOLDING / END_OF_DATA), average hold by regime.
- **Walk-Forward** — rolling 6-month performance windows in 1-month steps. Consistent win rates across windows indicate the signal logic has stable edge.
- **Virtual Trading** — view open/closed virtual positions and weekly virtual P&L (requires virtual trading enabled in config).

### 🖥️ Status
System status grid (Last Deployment, Service Uptime, Database Size, Last Data Refresh, Last Analysis Run, Next Scheduled Run). Manual Controls: **⚡ Run Analysis Now** (analysis + signals only) and **🔄 Refresh Data + Analysis** (price pull + FRED macro fetch + full analysis pipeline — equivalent to the EOD scheduler job). Send Test Email and Send Weekly Summary Now buttons for verifying email delivery.

A **T212 Invest Account** panel shows the live connection state when `trading212_invest.enabled: true` in config.yaml: account value, available cash, deployed-capital progress bar, and the last 10 automated orders with colour-coded status (FILLED green / PLACED amber / FAILED·CANCELLED·REJECTED red). Account data loads automatically when the Status page opens and **auto-refreshes every 60 seconds** — no manual click needed. The "🔄 Refresh Account" button is still available for an immediate on-demand refresh; it also polls `GET /equity/orders/{id}` for any orders still showing PLACED, updating status, fill price, filled quantity, and the actual GBP cost basis in the database. An hourly background poll runs the same order-fill check as part of the trade alert job. **Note:** T212 removes filled market orders from the orders endpoint once they become open positions — a 404 response does not mean cancelled. The poller checks for an active position on 404; if one exists the order is marked FILLED with `averagePrice` from the position (derived as `currentPrice − ppl/quantity` if `averagePrice` is null), and `estimated_value_gbp` is updated with `fill_price × quantity` converted to GBP using live FX rates — T212 returns prices in the instrument's native currency (USD, EUR, or GBP) so conversion is required for correct cost basis. Only if no position is found is the order marked CANCELLED. Open positions are displayed on the **Daily Briefing** (as trade cards alongside CFD positions) and the **Trade Log** (as a dedicated table) rather than on the Status page. Position data shown on the Daily Briefing is cached for 90 seconds so closed positions clear within two Status page refresh cycles. A collapsible UCITS mapping table shows which research tickers map to T212 instrument IDs. When FAILED orders are present, a **Retry failed orders** list appears below the order table — each row shows the ticker, direction, quantity, and error, with a **↻ Retry** button that applies the instrument precision/minimum-quantity guard and re-submits the order.

> **Forex pairs excluded by design:** Tickers ending in `=X` (e.g. `GBPUSD=X`, `EURUSD=X`) are never automated through T212 Invest. T212 Invest is a cash/equity account that supports stocks and ETFs only — forex is CFD-only, and the T212 CFD account has no public API. All forex trades remain manual CFD positions logged in the Trade Log.
>
> **Ticker substitutions in `ucits_mapping`:** Some research tickers are mapped to a different T212 instrument where T212 doesn't carry the exact fund. For example, `VIXY` maps to `VXX` — VXX (iPath S&P 500 VIX Short-Term Futures ETN) is the T212-available equivalent, tracking the same VIX short-term futures index. Each substitution is documented as an inline comment on the relevant `ucits_mapping` entry in `config.yaml`.

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

Six APScheduler jobs run automatically:

| Schedule | Type | What it does |
|----------|------|--------------|
| **Mon–Fri 07:00 UTC** | Asian EOD | `pull_incremental()` for exchanges closed before 07:00 UTC (e.g. JPX/Tokyo), then full analysis pipeline; opens virtual trades for those tickers only |
| **Mon–Fri 13:30 UTC** | Analysis only | `run_analysis()` → `generate_signals()` → `compute_all_pairs()` — before US open, no data pull |
| **Mon–Fri 19:05 UTC** | FOMC reaction | Analysis-only pipeline on FOMC decision days only (Fed statement released ~18:00–19:00 UTC); silently skips on non-FOMC days |
| **Mon–Fri 21:30 UTC** | Full refresh | `pull_incremental()` for open exchanges, then full analysis pipeline + FRED macro fetch |
| **Mon–Fri 00:00–21:00 + 23:00 UTC (hourly)** | Trade alerts | Fetches live prices for open trades; emails alert on STOP LOSS or EXIT SIGNAL — filtered per-trade by home exchange session |
| **Sunday 10:00 UTC** | Weekly summary | Sends the weekly summary email covering signals, open trades, closed P&L, accuracy progress, macro pulse, diverging pairs |

All three analysis runs also call `_process_virtual_trades()` to auto-manage virtual positions.

---

## Backtesting

`analysis/backtest.py` replays the current signal logic over stored historical price data. The correlation module is excluded (it requires pair-level data and is not stored per-date in isolation); its weight is re-normalised across the remaining five modules.

Exit conditions (checked in priority order):
1. **Stop Loss** — price crosses entry ± N × ATR (configurable `atr_stop_multiplier`, default 2.0)
2. **Signal Reversal** — signal moves opposite to trade direction (SELL/STRONG_SELL for LONG)
3. **Max Holding Days** — position closed after a configurable cap (default 60 days)

Results are stored in three tables (`backtest_runs`, `backtest_signals`, `backtest_trades`) and analysed by `analysis/backtest_metrics.py` which provides: full metrics (Sharpe, Calmar, profit factor, max drawdown), equity curve, period breakdown, module attribution, signal decay histogram, and rolling walk-forward validation.

---

## Virtual trading

When `virtual_trading.enabled: true`, the system automatically manages a simulated portfolio in the `trades` table (identified by `account_type = 'VIRTUAL'`). After every analysis pipeline run:

- **Opens** a new virtual position on STRONG_BUY / STRONG_SELL signals for tradeable tickers (subject to `max_open_positions` cap).
- **Closes** existing virtual positions when the signal reverses direction or the current price crosses the ATR-based stop loss.

Virtual positions are sized at `position_pct` of `portfolio_size_gbp` (default 5% of £2,000 = £100 per trade). P&L is recorded as a price-change percentage (no FX or leverage applied). Virtual trades are visible on the Backtest page → Virtual Trading tab and in the weekly summary email.

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

SQLite via SQLAlchemy, WAL mode. All writes are upserts keyed on `(ticker, date)`.

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

Schema migrations (`_apply_migrations()`) run on every startup. One-time data fixes (`_apply_data_fixes()`) are guarded by value-based conditions. Stale tickers are purged from all tables on every startup via `_purge_unlisted_tickers()`.

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
  max_holding_days_bonds: 45     # bond ETFs (TLT, IEF, etc.) — slow-moving, longer trend duration
  atr_stop_multiplier: 2.0       # stop loss = entry ± N × ATR
  walk_forward_months: 6
  walk_forward_step: 1

virtual_trading:
  enabled: true
  portfolio_size_gbp: 2000
  position_pct: 0.05             # 5% per trade
  max_open_positions: 10
  entry_signal: "STRONG_BUY"    # STRONG_BUY = strict; BUY = looser

learning:
  enabled: true
  min_trades_before_adjustment: 20
  max_single_weight: 0.30
  min_single_weight: 0.05
```

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
| Process manager | systemd (`investment-research.service`) |

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

After creating or editing the file: `systemctl daemon-reload && systemctl restart investment-research`

The `investment-research.service` template in the repo has blank placeholders for these variables. Never fill in real values there — the drop-in file takes precedence and survives all future deploys.

### Deploying updates

```bash
bash setup-hooks.sh   # install pre-push deploy hook (run once)
git push origin main  # triggers automatic deploy
```

Manual deploy: `bash deploy.sh` (Git Bash) or `.\deploy.ps1` (PowerShell, gitignored). `deploy.sh` also copies `investment-research.service` to `/etc/systemd/system/` and runs `systemctl daemon-reload` so the installed service file stays in sync with the repo.

### Useful server commands

```bash
journalctl -u investment-research -f   # live logs
systemctl status investment-research
systemctl restart investment-research
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
