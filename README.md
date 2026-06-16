# Investment Research Dashboard

A self-hosted Streamlit application for daily market analysis, multi-module confluence signal generation, cross-asset correlation tracking, and CFD trade management across multiple accounts.

Live at [research.example.com](https://research.example.com) (password protected).

---

## What it does

The app tracks ~67 assets across 19 groups (indices, ETFs, commodities, forex, crypto, bonds, etc.), runs a seven-module technical and macro analysis pipeline, detects the current market regime, and scores every asset on a weighted confluence scale from −1 to +1. Signals are regenerated automatically three times per weekday. Open CFD trades are monitored live with P&L in GBP, ATR-based stop losses, and automated health status.

---

## Running locally

```powershell
pip install -r requirements.txt
python -m streamlit run main.py
```

First-run sequence: **Refresh Data** → **Run Analysis** → **Generate Signals**. After that, analysis runs automatically on every Streamlit startup (if price data exists) and via three APScheduler jobs.

Add your FRED API key to `config.yaml` under `macro.fred_api_key` (free at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)). Without it, yield-curve and FRED macro scoring return 0; VIX scoring still works.

---

## Pages

### 📅 Daily Briefing (default page)
The operational home page. Compact header block shows the active market regime, all key stats (^GSPC vs SMA200, ADX, VIX, yield spread, USD trend), any active regime overlays or catch-up opportunity flags, signal distribution counts, and the last-updated timestamp — all in one dense card.

Below the header:
- **Open Trade Monitor** — every open position as a status card with live GBP P&L, current signal, score delta since entry, ATR-based stop loss, live R:R, and a health status (ON TRACK / WEAKENING / EXIT SIGNAL / STOP LOSS). Sorted by urgency.
- **Top Opportunities** — STRONG BUY / STRONG SELL signals with multi-module agreement, filtered to tradeable assets by default. Each card shows the rationale, live price (greys out when market is closed), and a MACRO_STRESS confidence warning for equity/bond longs when that overlay is active.

### 📊 Signals
Compact header block: regime overlays and catch-up flags as inline strips, regime badge + stats, regime description, signal distribution pills (count + tradeable sub-count for each of 5 signal types), and the signals timestamp — all inside one bordered card.

Below: the market intelligence summary (plain-English read of signal breadth, VIX, ADX, and regime context), then the full ranked signal table for all assets with module score symbols (▲ / ● / ▼) per column and signal-badge colour coding. Includes a Generate Signals button.

### 📈 Charts
Asset-group landing page with group cards. Clicking through to an asset shows:
- Candlestick + volume chart (Plotly, full date range stored)
- Indicator panel: RSI, Bollinger Bands, MACD, ADX, SMA20/50/200 overlaid on price
- Raw indicator values for the most recent date

### 🔗 Relationships
Cross-asset correlation analysis across 10 predefined pairs:

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
| SLV vs GLD | Positive — silver follows gold with higher beta |

Shows: 30-day correlation heatmap across all 14 involved tickers, pair-level table (30d corr, 90d corr, historical norm, z-score, CONFIRMING/DIVERGING/NEUTRAL status), plain-English divergence narratives, and a "↻ Recompute Now" button.

### 📋 Trade Log
Full CFD trade lifecycle:
- **Log new trade** form: auto-populates the current signal; captures entry price, quantity, direction, account (PRACTICE/LIVE with named accounts), leverage, investment amount, currency, T212 instrument name, stop loss / take profit prices, preferred R:R, planned GBP risk, overnight swap rate and basis, spread cost, FX conversion fee.
- **Open positions table** with live GBP P&L, entry signal, current signal with score delta.
- **Edit expanders** on each open trade to update SL, TP, R:R, and planned risk without closing.
- **Close trade form** — records exit price, computes P&L in GBP (handling FX conversion and FX fee for cross-currency CFDs).
- **Position sizing guidance** — shows suggested investment and margin at the selected leverage based on account balance and risk-per-trade settings in config.yaml.

### 🏆 Performance
Historical analysis of all closed trades:
- Equity curve (cumulative GBP P&L)
- Win rate by signal type and by regime
- Indicator hit-rate table (per module: how often each module's direction matched the trade outcome)
- Closed trades list with optional exit-date filter
- All sections filter to trades closed within the selected date range

### 🖥️ Status
System status grid (2×3 cards): Last Deployment, Service Uptime, Database Size, Last Data Refresh, Last Analysis Run, Next Scheduled Run. Colour-coded green/amber/red by age. Manual Controls section for triggering Refresh Data and Run Analysis on demand, with a Send Test Email button for verifying SMTP delivery.

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
| **Macro** | VIX regime, 10Y-2Y yield curve spread from FRED, plus 9 additional FRED series (see below) |
| **Correlation** | Cross-asset pair status for pairs involving the ticker (CONFIRMING = +1, DIVERGING = −1, NEUTRAL = 0) |
| **Regime** | Not directly scored per ticker; determines the weights applied to the other six modules |

### FRED macro series

The macro module fetches and scores these series (stored in `macro_indicators` table):

| Series | What it is | Bearish signal |
|--------|-----------|---------------|
| T10Y2Y | 10Y−2Y yield spread | Inverted (< −0.1%) |
| T10Y3M | 10Y−3M yield spread | Inverted (< 0%) |
| DFII10 | 10-year real yield | > 2% or z-score > 1 |
| T10YIE | 10-year breakeven inflation | < 1% or > 3.5% |
| BAMLH0A0HYM2 | HY credit OAS | Rapid widening or > 5% |
| BAMLC0A0CM | IG credit OAS | > 1.5% |
| M2SL | M2 money supply | Contracting month-over-month |
| DPCREDIT | Fed discount window borrowing | z-score > 2 (banking stress) |
| BOGMBASE | Monetary base | Contracting |
| USSLIND | Philly Fed Leading Index | Falling 3+ consecutive months |
| PERMIT | Housing permits (SAAR) | Falling > 1% month-over-month |
| DTWEXBGS | Trade-weighted dollar | Rising (z-score > 0.5) |

VIX is sourced from the local `price_data` table (^VIX ticker), not FRED.

Self-referential macro tickers (^VIX, TLT, IEF, HYG) receive a neutral macro score of 0.

### Regime detection

Reads the most recent stored ^GSPC indicators. Priority order:

1. VIX > 30 → `HIGH_VOLATILITY`
2. price > SMA200, ADX > 25, VIX < 20 → `TRENDING_BULL`
3. price < SMA200, ADX > 25, VIX > 20 → `TRENDING_BEAR`
4. ADX < 20, VIX < 15 → `RANGING_LOW_VOL`
5. else → `UNKNOWN`

### Regime weights (sum to 1.0 per regime)

| Module | TRENDING_BULL | TRENDING_BEAR | RANGING_LOW_VOL | HIGH_VOLATILITY | UNKNOWN |
|--------|:---:|:---:|:---:|:---:|:---:|
| Trend | 0.30 | 0.25 | 0.10 | 0.10 | 0.18 |
| Mean Reversion | 0.10 | 0.15 | 0.35 | 0.15 | 0.18 |
| Volatility | 0.15 | 0.20 | 0.15 | 0.35 | 0.18 |
| Volume | 0.20 | 0.15 | 0.15 | 0.15 | 0.18 |
| Macro | 0.15 | 0.15 | 0.15 | 0.15 | 0.18 |
| Correlation | 0.10 | 0.10 | 0.10 | 0.10 | 0.10 |

### Regime overlays

Detected by `detect_overlays()` after each correlation run. Stored as a comma-separated list in `system_metadata` under the key `active_overlays`. Three overlays are defined:

| Overlay | Trigger | Effect |
|---------|---------|--------|
| `MACRO_STRESS` | TLT/^GSPC DIVERGING (bonds and equities moving together) | Confidence warning on equity/bond STRONG_BUY signals |
| `VOL_REGIME_SHIFT` | ^VIX/^GSPC DIVERGING (VIX moving with market, not against) | Informational warning shown on Signals and Daily Briefing |
| `CREDIT_STRESS` | HYG/^GSPC DIVERGING | Confidence warning on equity long signals |

### Confluence scoring

`weighted_score = Σ(module_score × regime_weight)` ∈ [−1, +1]

Signal classification:
- ≥ 0.60 → `STRONG_BUY`
- ≥ 0.30 → `BUY`
- (−0.30, +0.30) → `NEUTRAL`
- ≤ −0.30 → `SELL`
- ≤ −0.60 → `STRONG_SELL`

### Feedback learning

After 20+ closed trades, `recalculate_learned_weights()` computes per-module accuracy (win rate = direction matched trade outcome) and adjusts confluence weights proportionally, clamped between `min_single_weight` (0.05) and `max_single_weight` (0.30). Learned weights are stored in `system_metadata` under `learned_weights` and override the static regime weights. A history of the last 20 weight snapshots is kept under `weight_history`. Correlation is not trackable and is always held at a neutral 50% win rate for learning purposes.

### Catch-up opportunity flags

When a correlation pair is DIVERGING and the z-score is ≥ 1.5, `detect_opportunity_flags()` in `correlations.py` writes an `OpportunityFlag` row. These appear as CATCH-UP strips in the Signals and Daily Briefing compact header blocks. Return tracking (5-day, 20-day) is populated on subsequent runs to measure flag accuracy.

### Automatic scheduling (APScheduler, weekdays only)

| Time (UTC) | Type | What it does |
|---|---|---|
| **07:00** | Analysis only | `run_analysis()` → `generate_signals()` → `compute_all_pairs()` |
| **13:30** | Analysis only | Same — before US open |
| **21:30** | Full refresh | `pull_incremental()` for open exchanges, then full analysis pipeline |

---

## Trade model

Trades capture the full CFD context at the time of entry:

| Field | Description |
|-------|-------------|
| `direction` | LONG or SHORT |
| `account_type` | PRACTICE or LIVE |
| `account_name` | Named account (e.g. "Trading212 Practice") |
| `currency` | Instrument quote currency (e.g. USD for US ETFs) |
| `account_currency` | Account currency (GBP) |
| `fx_rate_at_entry` | Spot rate used to convert P&L to GBP |
| `leverage` | CFD leverage multiplier |
| `investment_amount` | Cash deployed in GBP |
| `position_value` | investment_amount × leverage |
| `margin_required` | Cash actually margin-locked (= investment_amount / leverage for T212) |
| `overnight_rate` | Daily swap as % of basis amount |
| `swap_basis` | Basis for swap calculation: `investment`, `margin`, or `position` |
| `spread_cost` | Opening spread cost in GBP |
| `fx_fee_pct` | Currency conversion fee % (0.50% for T212 forex pairs) |
| `platform_instrument` | T212 instrument name when it differs from the yfinance ticker |
| `stop_loss_price` | Planned stop loss (preferred over ATR-computed SL in the UI) |
| `take_profit_price` | Planned take profit |
| `risk_reward_ratio` | Preferred R:R at entry |
| `planned_risk_gbp` | Estimated GBP loss if stop loss is hit |

P&L on close: `(exit − entry) × qty × leverage` converted to GBP via `fx_rate_at_entry`, then minus `fx_fee_pct` applied to `position_value`.

For JPY-quoted forex CFDs (USDJPY=X), a two-step conversion is used: pip P&L → JPY → USD → GBP.

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
| `trades` | Full CFD trade ledger (open and closed) |
| `performance` | Rolling performance metrics per ticker (Sharpe, Sortino, Calmar, drawdown) |
| `system_metadata` | Key-value store for operational state (timestamps, learned weights, active overlays) |

Schema migrations (`_apply_migrations()`) run on every startup via `ALTER TABLE … ADD COLUMN`, each in its own transaction so a duplicate-column error never blocks the rest. One-time data fixes (`_apply_data_fixes()`) are guarded by value-based conditions so they are safe to call repeatedly. Stale tickers are purged from `price_data`, `indicators`, `signals`, and `correlations` on every startup via `_purge_unlisted_tickers()`.

---

## Configuration (`config.yaml`)

Key sections (file is gitignored — manage on server directly):

```yaml
database:
  path: database/investment_research.db

macro:
  fred_api_key: "YOUR_KEY_HERE"

watchlist:
  indices:
    - ticker: "^GSPC"
      name: "S&P 500"
      exchange: "NYSE"
      tradeable: false        # analysis-only — excluded from opportunity filtering
  commodities:
    - ticker: "SLV"
      name: "iShares Silver Trust"
      tradeable: true
      trading212_instrument: "XAG/USD"   # T212 name differs from yfinance ticker

notifications:
  email:
    enabled: false
    smtp_host: "smtp-relay.brevo.com"
    smtp_port: 587
    smtp_user: ""
    smtp_password: ""     # or set via SMTP_PASSWORD env var
    from_address: ""
    to_addresses: []
    alert_cooldown_hours: 8

learning:
  enabled: true
  min_trades_before_adjustment: 20
  max_single_weight: 0.30
  min_single_weight: 0.05

trading:
  accounts:
    - name: "Trading212 Practice"
      type: PRACTICE
    - name: "Trading212 Live"
      type: LIVE
  default_account: "Trading212 Practice"
  default_leverage: 5
  risk_per_trade_pct: 2.0     # % of account balance to risk per trade
  account_balance_gbp: 1000.0
```

### Tradeable vs analysis-only

Each watchlist entry has a `tradeable: true/false` flag. Assets with `tradeable: false` (e.g. ^GSPC, ^IXIC, CPER, CORN) are included in analysis but excluded from the opportunity filter on the Daily Briefing and from the Trade Log ticker list. The Signals page shows all assets; the "Tradeable" sub-count on every signal distribution is the count from tradeable-only assets.

### Trading212 instrument mapping

When a watchlist entry has `trading212_instrument`, that name is used in the Trade Log instead of the yfinance ticker. The mapping is loaded into a `t212_names` dict at startup and passed through to the Trade Log form and trade cards.

---

## Email alerts

When `notifications.email.enabled: true`, the app sends an alert email when any open trade reaches WEAKENING, EXIT SIGNAL, or STOP LOSS status (subject to an 8-hour cooldown). The Status page has a "Send Test Email" button that bypasses the enabled flag to verify SMTP delivery. Brevo (smtp-relay.brevo.com:587) is the default relay; any STARTTLS-capable relay works. The SMTP password can be passed via the `SMTP_PASSWORD` environment variable instead of the config file.

---

## Server deployment

### Infrastructure

| Item | Value |
|------|-------|
| Provider | Cloud VPS |
| Plan | 2 vCPU / 4 GB |
| OS | Ubuntu 22.04 LTS |
| App directory | `/root/investment-research` |
| Python | 3.11 (system-wide, no virtualenv) |
| Proxy | Caddy (automatic TLS + basic auth) |
| Process manager | systemd (`investment-research.service`) |

### First-time setup

```bash
git clone https://github.com/YOUR/investment-research.git /root/investment-research
cd /root/investment-research
bash setup.sh your.domain.com
```

`setup.sh` installs Python 3.11, compiles the TA-Lib C library from source, installs Caddy, installs all Python packages system-wide, writes `.streamlit/config.toml`, copies the Caddyfile, and registers the systemd service.

Set the dashboard password after setup:

```bash
caddy hash-password          # copy the hash output
nano /etc/caddy/Caddyfile    # replace REPLACE_WITH_HASH
systemctl reload caddy
```

### Deploying updates

The pre-push hook (installed by `setup-hooks.sh`) deploys automatically on every push to `main`:

```bash
bash setup-hooks.sh   # run once after cloning
git push origin main  # triggers deploy automatically
```

To push without deploying: `DEPLOYING=1 git push origin main`

Manual deploy:

```bash
bash deploy.sh        # Git Bash
.\deploy.ps1          # PowerShell (gitignored, local-only)
```

### Useful server commands

```bash
journalctl -u investment-research -f   # live logs
systemctl status investment-research
systemctl restart investment-research
systemctl reload caddy                 # apply Caddyfile changes
```

### Files not in git (manage on server directly)

| File | Contains |
|------|---------|
| `config.yaml` | FRED API key, watchlist, scheduler timing, SMTP credentials |
| `Caddyfile` | Domain name, bcrypt password hash |
| `.streamlit/config.toml` | Generated by setup.sh |
| `database/*.db` | SQLite database |
| `deploy.ps1` | PowerShell deploy script (contains SSH key path) |

---

## Adding new assets

Edit `config.yaml` under `watchlist:` — add a dict with `ticker`, `name`, `exchange`, and `tradeable`. No code changes needed. If adding a new group, also add its label to `_GROUP_LABELS` in `main.py`.

Forex pairs (e.g. `EURUSD=X`) return zero volume from yfinance — the volume module scores neutral for these, which is expected.

## Adding a new analysis module

1. Create `analysis/your_module.py` with `compute_your_module(df, config) -> dict` returning `{"score": int, ...raw_values}`.
2. Call it in `analysis/engine.py:analyse_ticker()` and merge its raw values.
3. Call it in `analysis/confluence.py:score_ticker()` and include its score.
4. Add `"your_module"` with a weight to every regime dict in `REGIME_WEIGHTS` — weights must still sum to 1.0 per regime.
5. Add `your_module_score` to the `Signal` model in `data/database.py`, to `_apply_migrations()`, and to the upsert in `confluence.py:_persist_signal()`.
6. Add any new raw-value columns to the `Indicator` model and to `_apply_migrations()`.
7. Update `total_signal_modules` in `config.yaml`.

Always maintain both TA-Lib and pure-pandas/numpy fallback paths. Every module tries `import talib` and sets `_TALIB_AVAILABLE`; all computation paths have a pandas fallback.

## TA-Lib

`requirements.txt` has both package names commented out with notes. `setup.sh` installs `TA-Lib` (Linux wrapper) after compiling the C library from source. Windows dev installs `TA-Lib-Precompiled` manually if needed. All analysis modules have pure-pandas fallbacks so TA-Lib is optional on both platforms.
