# Investment Research Dashboard

A self-hosted Streamlit application for daily market analysis, multi-module confluence signal generation, cross-asset correlation tracking, historical backtesting, and automated T212 Invest order placement.

Live at [research.example.com](https://research.example.com) (password protected).

---

## What it does

The app tracks ~104 assets across 20 groups (indices, ETFs, commodities, forex, crypto, bonds, US large-cap stocks, etc.), runs a seven-module technical and macro analysis pipeline, detects the current market regime, and scores every asset on a weighted confluence scale from −1 to +1. Signals are regenerated automatically on eight scheduler jobs. Open T212 Invest positions are monitored live with P&L in GBP and automated health status, and exit automatically on signal reversal. A backtesting engine replays signal logic over up to 10 years of stored history. A Performance page tracks real T212 execution outcomes — win rate, P&L, and a feedback-learning system that adjusts module weights based on closed-position accuracy.

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
- **Open Trade Monitor** — a compact one-line status card per open T212 Invest position (UCITS ETFs and direct US stocks), sorted by urgency: status badge, ticker, asset type, day count, live GBP P&L. Tickers with an open T212 Invest position are excluded from Today's Opportunities to avoid duplicate signals. Full detail — signal delta since entry, SL/TP, live R:R — is on the **Performance** page (see below), which reads the same underlying position data.
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

### Performance

T212 Invest execution performance, driven entirely by real broker fills — no manually-logged or simulated trade data feeds this page at all (see "Removed: virtual trading" and "Removed: manual CFD trade logging" below). Four sections:

- **Open Positions** — the same full-detail cards as the Daily Briefing's monitor used to show before it was simplified to a one-line summary: live GBP P&L, current signal, score delta since entry, SL/TP, live R:R, health status, and VaR at entry when the position was sized under `position_sizing: var` (see "VaR position sizing" below). Reads the same scheduler-maintained snapshot, no direct T212 API calls.
- **Closed Positions** — a table of every closed T212 position (`t212_pnl`): ticker, type, entry/exit date and price, quantity, P&L in £ and %, holding days, entry signal, regime, exit reason. Always shown, regardless of row count — `"No closed positions yet — data will appear as T212 positions close"` when the table is empty.
- **Performance Metrics** — gated behind 5 closed positions (T212 exits are infrequent — maybe 5–10/year — so this can take a while to populate). Below 5, just a win/loss count and total P&L. At 5+: win rate, avg/total return, profit factor, max drawdown, an equity curve, and performance broken down by regime-at-entry and by exit reason. Reuses the same metrics functions as the Backtest page (`analysis/backtest_metrics.py`) via a column-renaming adapter, rather than a second implementation of the same aggregation logic.
- **Learning System** — live count of `t212_pnl` rows against `learning.min_trades_before_adjustment` (default 20 — see "Feedback learning" below), and, once any weights have been learned, a comparison of learned per-module weights against the static regime-weight average.

### Backtest
Replay the current signal logic over up to 10 years of stored historical price data — no look-ahead bias (vectorized pandas rolling operations). The backtest mirrors live system config rather than accepting independent runtime parameters: `config.yaml` is the single source of truth for holding limits, ATR stop multiplier, entry/exit thresholds, and the regime gate, for backtesting exactly as it is for live trading. To test different settings, edit `config.yaml` and re-run — there's no separate backtest-only parameter set.

- **Run & Overview** — select date range (presets: Full History / COVID Era / Bear Market / Recovery / Custom) and ticker scope (tradeable-only / **T212 CFD Only** / all assets) — the only genuinely runtime-controlled inputs, alongside an optional run name. A read-only caption summarises the live config values being applied, e.g. "Using live config: ETF max hold 90d · Stock max hold 30d · Bond max hold 45d · ATR stop 2.0× · entry STRONG_BUY · TRENDING_BULL only". Runs asynchronously and stores results in the database. Equity curve, full metrics strip (win rate, Sharpe, max drawdown, profit factor), and complete simulated trade table. A collapsible **T212 CFD comparison** panel instantly filters any existing run's trades to only tickers with a `trading212_instrument` mapping and shows a side-by-side metrics table plus a breakdown of excluded tickers and how many trades each contributed — no re-run required.
- **Period Analysis** — performance breakdown for COVID Crash (Feb–May 2020), 2022 Bear Market, and 2023–24 Recovery, plus per-regime win rate/return table.
- **Module Attribution** — for each analysis module, win rate when it agreed vs disagreed with the trade direction. Shows which modules have genuine predictive edge.
- **Signal Decay** — holding period distribution histogram, exit reason breakdown (STOP_LOSS / SIGNAL_REVERSAL / MAX_HOLDING / END_OF_DATA), average hold by regime.
- **Walk-Forward** — rolling 6-month performance windows in 1-month steps. Consistent win rates across windows indicate the signal logic has stable edge.

### Status
System status grid (Last Deployment, Service Uptime, Database Size, Last Data Refresh, Last Analysis Run, Next Scheduled Run). Manual Controls: **Run Analysis Now** (analysis + signals only) and **Refresh Data + Analysis** (price pull + FRED macro fetch + full analysis pipeline — equivalent to the EOD scheduler job). Send Test Email and Send Weekly Summary Now buttons for verifying email delivery.

A **T212 Invest Account** panel shows the live connection state when `trading212_invest.enabled: true` in config.yaml, including the DEMO/LIVE badge, position/deployment limits, a "Position sizing" caption showing **VaR** (with its confidence/lookback/max-risk/floor/ceiling params) or **Fixed**, and the effective entry threshold for the current regime (flagged "(override)" when `entry_signal_overrides` applies one, or a `⛔ Entry blocked` caption in place of the threshold when the current regime isn't in `allowed_entry_regimes` — see "Per-regime entry signal overrides" below). It is split into three tabs:
- **Account tab** — account value, available cash, deployed-capital progress bar, and the last 10 automated orders with colour-coded status (FILLED green / PLACED amber / FAILED·CANCELLED·REJECTED red) plus a "VaR at Entry" column. Data is read from a scheduler-maintained DB snapshot; the **Refresh Account** button queues an out-of-band fetch (handled by the 60-second job dispatcher, "updating within 60s"). When FAILED orders are present a **Retry failed orders** list appears — each row shows the ticker, direction, quantity, and error with a **Retry** and **Delete** button. The Retry button applies the instrument precision/minimum-quantity guard and re-submits.
- **Stocks tab** — the `trading212_stocks` entry threshold (or blocked-entry caption), position/deployment limits, and the last 10 automated orders filtered from the same `t212_invest_orders` table down to tickers in `watchlist.us_large_cap` — see "T212 Stocks automation" below.
- **Mapping tab** — UCITS instrument mapping table (which research tickers map to T212 instrument IDs).

**Note:** T212 removes filled market orders from the orders endpoint once they become open positions — a 404 response does not mean cancelled. The poller checks for an active position on 404; if one exists the order is marked FILLED with `averagePrice` from the position (derived as `currentPrice − ppl/quantity` if `averagePrice` is null), and `estimated_value_gbp` is updated with `fill_price × quantity` converted to GBP using live FX rates — T212 returns prices in the instrument's native currency (USD, EUR, or GBP) so conversion is required for correct cost basis. Only if no position is found is the order marked CANCELLED. Open positions are displayed on the **Daily Briefing** page, not here.

> **Forex pairs excluded by design:** Tickers ending in `=X` (e.g. `GBPUSD=X`, `EURUSD=X`) are never automated through T212 Invest. T212 Invest is a cash/equity account that supports stocks and ETFs only — forex is CFD-only, and the T212 CFD account has no public API. There is currently no automation path for forex at all (manual CFD trade logging was removed — see "Removed: manual CFD trade logging" below).
>
> **Ticker substitutions in `ucits_mapping`:** Some research tickers are mapped to a different T212 instrument where T212 doesn't carry the exact fund. For example, `VIXY` maps to `VXX` — VXX (iPath S&P 500 VIX Short-Term Futures ETN) is the T212-available equivalent, tracking the same VIX short-term futures index. Each substitution is documented as an inline comment on the relevant `ucits_mapping` entry in `config.yaml`.
>
> **`SHY`, `JNK`, and `XLRE` are analysis-only:** none has a `ucits_mapping` entry, so all three are `tradeable: false` (see "Tradeable vs analysis-only" below) — they still generate signals and feed correlations/macro scoring, but never receive automated orders or appear in opportunities. `JNK`'s exclusion is intentional: `HYG` is already mapped and covers essentially the same US high-yield bond exposure (adding JNK would open a duplicate position in the same index). `SHY` and `XLRE` are intentionally analysis-only — UCITS equivalents exist but not currently mapped; add to `ucits_mapping` if automation is desired (iShares $ Treasury Bond 1-3yr ETF for SHY; SPDR S&P U.S. Real Estate Select Sector UCITS ETF for XLRE — both inconsistent with the pattern of TLT/IEF/TIP being mapped and all other major SPDR sector ETFs being mapped). Adding either to `ucits_mapping` also needs its watchlist `tradeable` flag flipped back to `true` to match.

### T212 Stocks automation

A separate `trading212_stocks` config block automates direct US large-cap stock trading (`watchlist.us_large_cap` — 31 S&P 500 stocks across 9 sectors: Technology (AAPL, MSFT, NVDA, AMZN, ADBE, CRM), Financials (JPM, GS, BAC, V, MA, SPGI), Healthcare (LLY, UNH, JNJ, PFE, MRK, TMO), Semiconductors (AMD, AVGO, QCOM), Consumer Staples (COST, WMT, PG), Consumer Discretionary (HD), Energy (XOM, CVX), Industrials (CAT, RTX), Gold Miners (NEM, GOLD) — chosen for liquidity and for having a corresponding sector ETF already in the watchlist for correlation context; TSLA, META, GOOGL, and TSM were deliberately excluded despite otherwise fitting, for CEO/antitrust/geopolitical risk respectively), running alongside — not instead of — the UCITS ETF automation above. `brokers/trading212.py:maybe_place_stock_orders()` is structurally identical to `maybe_place_t212_orders()` (same two-phase EXIT/BUY flow, same duplicate-order guard, same `t212_invest_orders` table) but with three differences that all reflect individual stocks carrying more idiosyncratic risk than a diversified ETF basket:

- **Regime gate:** `allowed_entry_regimes` is `[TRENDING_BULL]` only — the same as the ETF path (see "Regime entry gate" above for the full per-regime backtest rationale, including why TRENDING_BEAR was removed). A single stock's downside in a bear or high-vol regime is far less diversified than an ETF's, so this was always intended to be at least as strict for stocks. As with the ETF path, the EXIT phase is never regime-gated — existing stock positions always continue to be evaluated for exit.
- **Smaller, tighter sizing:** `max_position_gbp: 200` (vs 500), `max_total_deployed: 0.3` (vs 0.5), `max_holding_days: 30` (vs 90) — VaR position sizing (below) applies on top of this ceiling.
- **Fractional-share ordering:** no `ucits_mapping` — the T212 instrument ticker defaults to the research ticker itself (override per watchlist entry via `trading212_instrument` where it differs). When `fractional_shares: true` and the T212 instruments API reports `maxQuantityPrecision > 0` for a ticker, the order quantity is a decimal — `get_quantity_for_gbp()`'s GBP→USD-converted value rounded to that precision — instead of floored to a whole share the way UCITS ETF orders are. This uses the same `/equity/orders/market` endpoint as whole-share orders (T212 accepts a decimal `quantity` directly for fractional-eligible instruments); no separate value-based order call is implemented.

> **Earnings gap risk:** neither T212 automation path checks an earnings calendar. A position can gap through overnight on an earnings announcement — the next scheduled exit check fills at whatever price is then available, not any intended level. This is a materially larger risk for individual stocks than for diversified ETFs and is not currently mitigated in code.

### VaR position sizing

Both T212 automation paths size new BUY orders with **Historical VaR** rather than a flat `max_position_gbp` for every ticker, when `position_sizing: var` (the default for both `trading212_invest` and `trading212_stocks` — `fixed` reproduces the pre-VaR flat-allocation behaviour exactly). Position size scales inversely with the ticker's own historical volatility, so e.g. NVDA sizes smaller than WMT for the same £ ceiling instead of both getting an identical allocation regardless of risk.

`analysis/volatility.py:compute_var_position_size()`: reads the last `var_lookback_days` (default 252, ~1 year) of daily closes from `price_data` (no yfinance call), computes daily log returns, and takes the `(1 - var_confidence)` percentile as the daily VaR (Historical, not Parametric — equity/ETF returns are fat-tailed — and not Monte Carlo, which is unnecessarily expensive for a per-ticker sizing check on every EOD run). Scaled to the position's actual holding period via the square-root-of-time rule (`holding_var = daily_var × √holding_days`, using the same per-ticker `get_max_holding_days()` the live exit checks and backtest use), then sized so a holding-period move of that magnitude would lose at most `max_risk_pct` (default 1%) of account value. Floored at `min_position_gbp` (default £50), capped at `max_position_gbp` (unchanged — still a hard ceiling). Falls back to `max_position_gbp × 0.5` when fewer than 60 daily returns are available (new listing, data gap).

```yaml
trading212_invest:            # and, identically, trading212_stocks:
  position_sizing: var        # "var" or "fixed" (flat max_position_gbp — the pre-VaR behaviour)
  var_confidence: 0.95
  var_lookback_days: 252
  max_risk_pct: 0.01
  min_position_gbp: 50
```

The holding-period VaR used to size each order is stored on `t212_invest_orders.var_pct` and shown as "VaR at Entry" in the Status page's Recent Orders tables and the Performance page's Open Positions cards. See "VaR position sizing" in [CLAUDE.md](CLAUDE.md) for the full formula, the shared-core design that keeps live and backtested sizing from drifting apart, and how the backtest avoids look-ahead bias when reusing the same math.

---

## Analysis pipeline

### Seven scoring modules

Each module returns a score of −1 (bearish), 0 (neutral), or +1 (bullish):

| Module | What it measures |
|--------|-----------------|
| **Trend** | SMA20/50/200 alignment, MACD histogram, ADX direction, golden/death cross |
| **Mean Reversion** | RSI (oversold/overbought), Bollinger Band %B, 52-week z-score |
| **Volatility** | ATR%, historical volatility, Bollinger Band width regime, change-point-aware structural break adjustment |
| **Volume** | Volume vs 20-day SMA, OBV trend direction |
| **Macro** | VIX regime, yield curve, plus 12 additional FRED series with per-ticker routing (see below) |
| **Correlation** | Cross-asset pair status for pairs involving the ticker (CONFIRMING = +1, DIVERGING = −1, NEUTRAL = 0) |
| **Regime** | Not directly scored per ticker; determines the weights applied to the other six modules |

> **Trend module's golden/death cross vote is direction-aware, not purely positional.** A golden cross (SMA50 > SMA200) only votes bullish while SMA50 is rising — a declining SMA50 that's still above SMA200 from an earlier uptrend votes neutral (stale cross), or flips bearish if the two lines have converged within `signals.ma_cross_near_pct` (default 2%) of each other, since that's a warning of an imminent death cross rather than confirmation of bullish momentum. Death crosses are scored with the same logic mirrored. The slope lookback is `signals.ma_cross_slope_window` (default 10 trading days). The positional `golden_cross` flag shown on the Charts page indicator panel ("Golden ✓" / "Death ✗") is unchanged and reflects the raw SMA50-vs-SMA200 position; a "⚠ SMA50 declining/rising" caption appears underneath when the direction-aware vote disagrees with it.

> **Volatility module distinguishes "always volatile" from "recently became volatile."** `detect_change_points()` locates the most recent structural break in a ticker's return-variance regime (cumulative-sum-of-squares statistic by default — see CLAUDE.md for why a textbook rolling-variance CUSUM was tried and rejected). A break within `signals.cpd_recent_window` (default 90 days) recomputes ATR from only the post-break data rather than the full fixed window, and adjusts the volatility score: vol expanding >1.5× after a break subtracts 1 (caution), vol compressing to <0.67× adds 1 (constructive). The Charts page indicator panel shows a colour-coded Vol Regime tile (LOW/NORMAL/HIGH/ELEVATED) and an amber "⚠ Regime shift detected" caption when a recent break is active. This is currently live-scoring only — `analysis/backtest.py`'s vectorized engine does not yet mirror change point detection, so backtested results don't reflect the score adjustment (see CLAUDE.md's "Volatility module" section).

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

The backtest engine applies the same per-date regime thresholds. All downstream consumers (email alerts, T212 automation, trade health status) read the stored `signal` classification from the database — no re-classification from raw scores occurs outside `generate_signals()`.

The Daily Briefing shows the current effective thresholds and the approximate number of signal modules that must agree (e.g. "Threshold: 0.60 (MIXED) — requires ~4/7 modules to agree").

### Feedback learning

After 20+ closed T212 Invest positions (tracked in `t212_pnl` — see "T212 P&L reconstruction" below), `recalculate_learned_weights()` computes per-module accuracy and adjusts confluence weights proportionally, clamped between 0.05 and 0.30. Learned weights override static regime weights and are stored in `system_metadata` under `learned_weights`. Since T212 positions close infrequently (roughly 5–10/year), reaching the 20-position threshold takes time — the Performance page's Learning System section shows live progress toward it.

### Automatic scheduling

Eight APScheduler jobs run automatically from a **standalone scheduler daemon** (`scheduler_daemon.py`) managed by `investment-research-scheduler.service`. The daemon starts at boot and fires jobs on their UTC schedule regardless of whether anyone has visited the dashboard — no browser connection required.

| Schedule | Type | What it does |
|----------|------|--------------|
| **Every 60 seconds** | Job dispatcher | Writes `scheduler_heartbeat` to `system_metadata`; checks `pending_t212_fetch` flag and, when set, calls `_fetch_t212_snapshot()` to refresh the T212 account/positions snapshot |
| **Mon 07:00 UTC** | Monday pre-market | Cancels any PLACED BUY order still open from Friday's EOD run (older than 30h), then the same analysis-only pipeline as Tue–Fri pre-market — see "Monday pre-market job" below |
| **Tue–Fri 07:00 UTC** | Asian EOD | `pull_incremental()` for exchanges closed before 07:00 UTC (e.g. JPX/Tokyo), then full analysis pipeline |
| **Mon–Fri 13:30 UTC** | Analysis only | `run_analysis()` → `generate_signals()` → `compute_all_pairs()` — before US open, no data pull |
| **Mon–Fri 19:05 UTC** | FOMC reaction | Analysis-only pipeline on FOMC decision days only (Fed statement released ~18:00–19:00 UTC); silently skips on non-FOMC days |
| **Mon–Fri 21:30 UTC** | Full refresh | `pull_incremental()` for open exchanges, then full analysis pipeline + FRED macro fetch; places T212 orders |
| **Mon–Fri 00:00–21:00 + 23:00 UTC (hourly)** | Trade alerts | Fetches live prices for open trades; emails alert on STOP LOSS or EXIT SIGNAL — filtered per-trade by home exchange session; runs the T212 Invest exit check; refreshes the T212 account/positions snapshot unconditionally on each run |
| **Sunday 10:00 UTC** | Weekly summary | Sends the weekly summary email covering signals, open trades, closed P&L, accuracy progress, macro pulse, diverging pairs |

The Streamlit web UI (`investment-research.service`) also starts an in-process scheduler as a fallback, but checks the `scheduler_heartbeat` key in `system_metadata` first — if the heartbeat is less than 90 seconds old the daemon is active and the in-process scheduler is skipped, preventing duplicate job execution.

### Monday pre-market job

BUY orders placed by the Friday 21:30 UTC EOD run can still be sitting `PLACED` when markets reopen Monday — T212 doesn't fill market orders outside trading hours. A lot can change over a weekend (geopolitical events, Sunday futures pricing, macro news), so `_monday_premarket_job()` cancels any `PLACED` `BUY` order older than 30 hours via the T212 API before re-running the standard analysis-only pipeline against Friday's closing prices (no data pull). Signal placement, exit checks, and everything else run exactly as they do on any other analysis-only run — a ticker still showing `STRONG_BUY` in `TRENDING_BULL` after cancellation gets a fresh order; a weakened signal doesn't. Fires Mondays only; `_pre_market_job()` itself now runs Tuesday–Friday.

---

## Backtesting

`analysis/backtest.py` replays the current signal logic over stored historical price data. The correlation module is excluded (it requires pair-level data and is not stored per-date in isolation); its weight is re-normalised across the remaining five modules.

Entry is gated per date by the same two-step regime logic used by live automation (see "Per-regime entry signal overrides" and "Regime entry gate" in Configuration): `simulate_trades()` first checks `analysis/regime.py:is_entry_allowed()` against `backtest.allowed_entry_regimes` — a regime not in that list (MIXED, RANGING_LOW_VOL) blocks entry outright for that date, before any signal is even considered — then, for an allowed regime, resolves the effective threshold via `analysis/regime.py:resolve_entry_signal()` against `backtest.entry_signal_overrides`, entering only on STRONG_BUY/STRONG_SELL by default, or also on plain BUY/SELL where that regime's override loosens the threshold. This keeps simulated results consistent with what the live system would actually have traded, rather than a fixed STRONG_BUY-only bar (or worse, no regime gate at all) across every regime. This entry-gating config lives under `backtest:` rather than `trading212_invest:`/`trading212_stocks:` because simulated backtest trades can go either LONG or SHORT, unlike those two long-only cash-account automation paths.

Exit conditions (checked in priority order, unaffected by either entry gate):
1. **Stop Loss** — price crosses entry ± N × ATR (`backtest.atr_stop_multiplier`, default 2.0)
2. **Signal Reversal** — signal moves opposite to trade direction, gated by `trading212_invest.exit_signal`: "SELL" (default) reverses on SELL/STRONG_SELL for LONG (BUY/STRONG_BUY for SHORT); "STRONG_SELL" requires the strong variant only — mirroring the live exit threshold `maybe_place_t212_orders()` uses.
3. **Max Holding Days** — position closed after a per-ticker cap resolved by `analysis/regime.py:get_max_holding_days(ticker, config)`: ETFs in `trading212_invest.ucits_mapping` use `trading212_invest.max_holding_days` (default 90d), `watchlist.us_large_cap` stocks use `trading212_stocks.max_holding_days` (default 30d), bond ETFs not in `ucits_mapping` use `backtest.max_holding_days_bonds` (default 45d), forex uses `backtest.max_holding_days_forex` (default 5d), crypto uses `backtest.max_holding_days_crypto` (default 14d), and everything else uses `backtest.max_holding_days` (default 30d). The same helper is used by the live T212 exit checks in `brokers/trading212.py`, so backtested and live holding limits always agree per ticker.

None of these values are accepted as override parameters by `simulate_trades()` or `run_backtest()` — they're read directly from `config`, so the backtest always reflects whatever `config.yaml` currently says the live system should do.

Each entry is sized with the same VaR position sizing live trading uses (see "VaR position sizing" above) rather than every simulated trade being equal-weight — a `position_gbp` and `pnl_gbp` (`position_gbp × pnl_pct`) is stored per trade. `backtest.starting_capital_gbp` (default £10,000) is a notional constant, not a real compounding balance, since `simulate_trades()` runs one ticker at a time with no shared cross-ticker portfolio.

Results are stored in three tables (`backtest_runs`, `backtest_signals`, `backtest_trades`) and analysed by `analysis/backtest_metrics.py` which provides: full metrics (Sharpe, Calmar, profit factor, max drawdown — plus `_gbp`-suffixed £ metrics when `pnl_gbp` is populated), a normalised % equity curve and a true £ equity curve (`equity_curve_gbp()` — additive, not compounding), period breakdown, module attribution, signal decay histogram, and rolling walk-forward validation.

---

## Removed: virtual trading

A third automated entry path, `virtual_trading`, used to run alongside `trading212_invest` and `trading212_stocks`: it opened and closed simulated (no real money) LONG/SHORT positions live after every analysis pipeline run and fed them into the feedback-learning system. It has been removed entirely, along with its Backtest-page tab and its section in the weekly summary email.

`simulate_trades()` still needs its own LONG+SHORT entry-gating config, though — unlike the two long-only T212 cash-account paths, backtest trades can go either direction. Rather than deleting that config, its three keys (`entry_signal`, `entry_signal_overrides`, `allowed_entry_regimes`) moved from the old `virtual_trading:` section into the `backtest:` section shown above — a config relocation only, not a value or logic change.

Historical `trades` table rows with `account_type = 'VIRTUAL'` are not deleted, but are no longer opened, closed, or read by any live code path or UI page.

---

## Removed: manual CFD trade logging

The original Trade Log page (manual trade entry form, open-position tracker, close-trade form, position-sizing guidance) and the original Performance page (equity curve, win rate by signal type/regime, indicator hit-rate table, closed trades list — both driven by manually-logged rows in the `trades` table) were removed together. The Trade Log page is gone permanently. The Performance page has since been **rebuilt from scratch**, driven entirely by real T212 execution data instead — see "Performance" above and "T212 P&L reconstruction" below.

Removed alongside the two original pages (permanently — not reintroduced by the Performance page rebuild): `log_trade()`, `close_trade()`, `update_trade_plan()`, `get_open_trades()`, `get_all_trades()`, `classify_trade_theme()` (`output/performance.py`); `enrich_open_trade()`, `compute_trade_status()`, and the manual-CFD alert-email chain — `send_alert_email()`, `should_send_alert()`, `_build_alert_html()`, and their exclusive helpers (`output/briefing.py`); the CFD-trade alerting loop and price-level SL/TP proximity check in the hourly `_trade_alert_job()` (`data/scheduler.py`); and the `accounts:` config section (Practice/Live CFD account balances and position-sizing percentages), which only the Trade Log page read.

---

## T212 P&L reconstruction

`brokers/t212_learning.py:_reconstruct_t212_pnl()` derives realised P&L for closed T212 Invest positions directly from `t212_invest_orders` (the order-execution audit trail both automation paths already write to) rather than requiring any manual trade-logging step. It FIFO-pairs each FILLED BUY with the next FILLED SELL for the same ticker, computes GBP P&L (converting from the instrument's native currency using historical FX rates as of the exit date), and writes one row per closed position to `t212_pnl` — idempotently, so repeated calls only ever add newly-closed positions. Called from both `maybe_exit_t212_positions()` and `maybe_exit_stock_positions()` on every exit check (hourly + EOD), so there's no separate scheduler job for it.

`t212_pnl` is now the **only** closed-position data source in the app — it feeds the Performance page's Closed Positions/Metrics sections and the feedback-learning system (see "Feedback learning" above). `get_t212_closed_positions()` in `output/performance.py` (renamed from `get_closed_trades()`) reads it for the weekly summary email.

Any CFD trade rows already in the `trades` table (account_type PRACTICE or LIVE) are not deleted, but are no longer opened, closed, monitored, or alerted on — see "Trade model" below.

---

## Email alerts

Email functions send **HTML emails with plain-text fallback** via a shared `_smtp_send()` helper in `output/briefing.py`. Emails are built as `MIMEMultipart("alternative")` with inline CSS only (Outlook-compatible — no external stylesheets, no flex/grid, `bgcolor` on TD elements). SMTP password is read from config or the `SMTP_PASSWORD` environment variable.

| Type | When | Controlled by |
|------|------|--------------|
| **VIX spike alert** | Triggered ad hoc (checked hourly) when VIX rises >20% intraday vs. the previous close | Fires at most once per calendar day |
| **Weekly summary** | Every Sunday at 10:00 UTC | `notifications.weekly_summary.enabled` |
| **Test email** | On demand via Status page button | Always sends (ignores enabled flag) |

Manual CFD trade alerting (the old "Trade alert" email — STOP LOSS/EXIT SIGNAL emails for manually-logged trades) was removed along with the Trade Log page — see "Removed: manual CFD trade logging" above. T212 Invest positions exit automatically and don't need email alerts.

The weekly summary covers: regime + overlays, signal snapshot with tradeable counts, 7-day signal performance (directional hit rate), trades closed that week, signal accuracy progress toward the 20-trade learning threshold (HTML progress bar), FRED macro pulse (VIX + key series with week-over-week change), and diverging pairs and catch-up flags.

Default relay: Brevo (smtp-relay.brevo.com:587). Any STARTTLS-capable relay works. Set the SMTP password via the `SMTP_PASSWORD` environment variable rather than the config file.

---

## Trade model

**Historical only** — manual CFD trade logging was removed (see "Removed: manual CFD trade logging" above), so no new rows are written to this schema. Existing rows remain in the `trades` table and are still read by the weekly summary email for historical accuracy stats. Documented here for reference:

| Field | Description |
|-------|-------------|
| `direction` | LONG or SHORT |
| `account_type` | PRACTICE or LIVE (VIRTUAL also appears on legacy rows — see "Removed: virtual trading" below) |
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
| `trades` | Legacy CFD trade ledger (open and closed; also holds legacy VIRTUAL rows) — historical only, no longer written to or read by any live code path; see "Removed: virtual trading" and "Removed: manual CFD trade logging" above |
| `performance` | Rolling performance metrics per ticker (Sharpe, Sortino, Calmar, drawdown) — not to be confused with the Performance *page*, which reads `t212_pnl` instead |
| `backtest_runs` | Metadata for each backtest run (params, status, summary metrics) |
| `backtest_signals` | Per-ticker per-date signal history generated during a backtest run |
| `backtest_trades` | Simulated trades from each backtest run with full entry/exit/P&L detail |
| `t212_invest_orders` | Every T212 Invest order attempt (BUY and SELL, ETF and stock) — the execution audit trail both automation paths write to |
| `t212_pnl` | Reconstructed realised P&L per closed T212 Invest position, derived from `t212_invest_orders`; see "T212 P&L reconstruction" above |
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
  starting_capital_gbp: 10000     # notional account size for VaR position sizing — see "VaR position sizing" above
  max_holding_days: 30           # default max hold — equities, ETFs, commodities
  max_holding_days_forex: 5      # FX pairs (ticker ends with =X) — swap costs make long holds expensive
  max_holding_days_crypto: 14    # crypto (ticker ends with -USD) — high volatility, shorter cycles
  max_holding_days_bonds: 45     # bond ETFs (TLT, IEF, SHY, TIP) — slow-moving, longer trend duration
  atr_stop_multiplier: 2.0       # stop loss = entry ± N × ATR
  walk_forward_months: 6
  walk_forward_step: 1
  # Entry gating for analysis/backtest.py:simulate_trades() — kept here (not under
  # trading212_invest/trading212_stocks) because simulated backtest trades can go
  # either LONG or SHORT, unlike those two long-only cash-account automation paths.
  entry_signal: "STRONG_BUY"    # global default — STRONG_BUY = strict; BUY = looser
  entry_signal_overrides:       # per-regime override, keyed by Regime enum name — falls
    MIXED: STRONG_BUY           # back to entry_signal above for regimes not listed here.
    RANGING_LOW_VOL: STRONG_BUY # trading212_invest.entry_signal_overrides uses the same
    HIGH_VOLATILITY: STRONG_BUY # shape — see the Status page's T212 Invest Account section.
    TRENDING_BULL: STRONG_BUY
    TRENDING_BEAR: STRONG_BUY
  allowed_entry_regimes:        # checked BEFORE entry_signal_overrides — a regime not
    - TRENDING_BULL             # listed here blocks new entries outright, regardless of
                                 # signal strength. TRENDING_BEAR, MIXED, RANGING_LOW_VOL,
                                 # and HIGH_VOLATILITY are deliberately absent — see below.

learning:
  enabled: true
  min_trades_before_adjustment: 20
  max_single_weight: 0.30
  min_single_weight: 0.05
```

### Per-regime entry signal overrides

Backtesting showed uneven edge by regime: MIXED had 416 trades at a 37.5% win rate, profit factor 0.123, and −91.26% max drawdown, while a short-sample backtest of TRENDING_BULL (PF 0.631) and TRENDING_BEAR (PF 0.202) initially looked like it had genuine edge. `entry_signal_overrides` (shown on `backtest` above; `trading212_invest` and `trading212_stocks` support the identical key) is the mechanism for raising the conviction threshold per regime — currently all five regimes are set to `STRONG_BUY`. A regime with no entry in the overrides dict falls back to the global `entry_signal`. Both T212 automation paths (`brokers/trading212.py:maybe_place_t212_orders()` and `maybe_place_stock_orders()`) resolve this once per run via `analysis/regime.py:resolve_entry_signal()`, keyed on the regime most recently stamped on `^GSPC`'s row in the `signals` table — so the threshold applied always matches the regime that scored the signal being acted on. Only entries are affected; `exit_signal` has no regime override. The resolved regime is what gets logged to the `regime` column on `t212_invest_orders`, keeping the applied threshold auditable.

**Why TRENDING_BULL/TRENDING_BEAR aren't looser.** They were briefly set to `BUY` based on the short-sample numbers above, then reverted after a full 10-year backtest showed that was a mistake. `simulate_trades()`'s signal-reversal exit has always accepted plain SELL/BUY, not just STRONG_SELL/STRONG_BUY; pairing that with a loosened BUY *entry* created high-churn whipsaw trading in trending regimes — TRENDING_BULL and TRENDING_BEAR were only ~25% of all regime-days across 2016–2026 but generated 90% of all simulated trades, and win rate/profit factor/drawdown all got materially worse. Confirmed as a real strategy effect (not a code bug) via an independent from-scratch reimplementation of the entry/exit walk-forward that produced identical trade lists to the production function on real data. After reverting to uniform `STRONG_BUY`, a full-history production backtest exactly reproduced the original pre-feature baseline (757 trades / 41.2% win rate / PF 1.35 / −85.4% max drawdown), confirming the current config is a functional no-op relative to the hardcoded behaviour that existed before this feature.

**The backtest engine applies the same `entry_signal_overrides` logic** (against its own `backtest` config — see "Backtesting" above), so simulated results reflect the live system's current per-regime entry gating rather than a single fixed threshold across every regime.

### Regime entry gate

`entry_signal_overrides` alone can only tighten a regime down to `STRONG_BUY` — the 10-year backtest showed that isn't enough. MIXED is 56.8% of all regime-days but has no demonstrable edge even at STRONG_BUY-only (416 trades, 37.5% win rate, −91.26% max drawdown), and RANGING_LOW_VOL has only 1 trade of backtest data — too little to trust either way. `allowed_entry_regimes` (shown on `backtest` above; `trading212_invest` and `trading212_stocks` support the identical key, all three kept in sync) is a stricter gate, checked *before* `entry_signal_overrides`: a regime not in the list blocks new entries entirely, no matter how strong the signal.

**Why the list is `[TRENDING_BULL]` only.** TRENDING_BEAR and HIGH_VOLATILITY were originally allowed alongside TRENDING_BULL. A full-history backtest (2019–present, tradeable assets, 404 trades under that config) broke profit factor down by regime and showed TRENDING_BULL is the standout regime on every metric — profit factor **1.78**, average return **+1.14%**, max drawdown **-35.1%** — while TRENDING_BEAR came back at a profit factor of only **1.09** and an average return of **+0.19%** (barely above breakeven) with a **-46.9%** max drawdown: no meaningful edge next to TRENDING_BULL. `trading212_invest` and `trading212_stocks` are long-only cash accounts, so bear-regime entries have no demonstrable edge there and were removed from the list; the backtest engine can also go short but is kept in sync with the other two anyway, the same way MIXED and RANGING_LOW_VOL already were. HIGH_VOLATILITY, previously kept allowed "for lack of data to exclude it," was excluded on the same "not proven" basis once TRENDING_BULL was confirmed as the standout regime. (An earlier version of this note cited profit factor 0.613/0.10 for TRENDING_BULL/TRENDING_BEAR — those numbers were incorrect; 1.78/1.09 are the figures verified by re-running `run_backtest()` against production data after this gate shipped.)

`analysis/regime.py:is_entry_allowed(config, regime_name, section)` implements the check — `section` is `"trading212_invest"`, `"trading212_stocks"`, or `"backtest"`. If `allowed_entry_regimes` is absent from a config section entirely, every regime is allowed (the backwards-compatible default for configs written before this gate existed).

**The exit phase is always active, regardless of regime.** The gate only ever suppresses *new* entries:
- `maybe_place_t212_orders()` and `maybe_place_stock_orders()` check it at the very start of the BUY phase (before the deployed-capital check) and return immediately if blocked, logging `"Skipping T212 Invest BUY phase — regime is TRENDING_BEAR, entries blocked"` (or the stock-path equivalent). The EXIT phase runs first, unconditionally — existing positions keep exiting normally on signal reversal, confirmed NEUTRAL, or the backstop time limit in any regime.
- `simulate_trades()` checks it per date, only when no position is open; a trade that opened in an allowed regime still exits normally even if the regime later rolls into a blocked one.

The Daily Briefing and the Status page's T212 Invest Account section both show a `⛔ Entries blocked — MIXED regime` caption in place of the entry-threshold caption whenever the current regime isn't in `allowed_entry_regimes`, so it's immediately visible that no new positions will open. These captions read `allowed_entry_regimes` from config at render time, so they stay accurate automatically as the list changes.

### Tradeable vs analysis-only

Each watchlist entry has a `tradeable: true/false` flag. **`tradeable: true` means "eligible for automated T212 Invest orders"** — the ticker is in `trading212_invest.ucits_mapping` or `watchlist.us_large_cap`; everything else is `tradeable: false`. Assets with `tradeable: false` are still fully analysed — signals, correlations, macro scoring, regime detection — but are excluded from opportunity filtering and can never receive an automated order. The Signals page shows all assets with a tradeable sub-count on each signal distribution. Currently 64 of the 104 watchlist tickers are `tradeable: true` (33 `ucits_mapping` + 31 `us_large_cap`).

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

Both scripts check the current UTC time before doing anything else: if it falls within 30 minutes of a scheduled job window (07:00 pre-market, 13:30 US open, 21:30 EOD, or 10:00 Sunday weekly summary), they print a warning and require a `y` confirmation before proceeding, since a mid-deploy restart during one of these windows can run order logic against transitional code/config. See "Deployment timing" in [CLAUDE.md](CLAUDE.md) for the full rationale.

**Two systemd services, both must be restarted.** `investment-research` is the Streamlit UI; `investment-research-scheduler` is the standalone daemon that actually fires the eight scheduled jobs (including order placement) whether or not anyone has the dashboard open. Each keeps its own in-memory copy of the code until restarted — if a deploy only restarts the UI, the scheduler daemon keeps running the old code, so a newly-deployed regime gate or entry-signal change silently fails to apply and orders keep going out under the previous logic. `deploy.sh`/`deploy.ps1`/`setup.sh` always restart (or enable+start, on first setup) both services for this reason.

After deploying, verify both services picked up the same code:

```bash
ps aux | grep -E "streamlit|scheduler_daemon" | grep -v grep
systemctl status investment-research investment-research-scheduler
```

### Useful server commands

```bash
systemctl restart investment-research            # Streamlit UI
systemctl restart investment-research-scheduler  # Scheduler daemon
journalctl -u investment-research -f             # UI logs
journalctl -u investment-research-scheduler -f   # Scheduler logs
systemctl status investment-research
systemctl status investment-research-scheduler
```

---

## Extending the app

### Adding assets

Edit `config.yaml` under `watchlist:` — add a dict with `ticker`, `name`, `exchange`, and `tradeable`. No code changes needed. If adding a new group, also add its label to `_GROUP_LABELS` in `main.py`.

Set `tradeable: true` only if the ticker is (or will be, in the same change) in `trading212_invest.ucits_mapping` or `watchlist.us_large_cap` — see "Tradeable vs analysis-only" above. Everything else should be `tradeable: false`.

### Adding a new analysis module

1. Create `analysis/your_module.py` with `compute_your_module(df, config) -> dict` returning `{"score": int, ...raw_values}`.
2. Call it in `analysis/engine.py:analyse_ticker()` and merge raw values.
3. Call it in `analysis/confluence.py:score_ticker()` and include its score.
4. Add weights to every regime dict in `REGIME_WEIGHTS` — weights must sum to 1.0.
5. Add `your_module_score` to the `Signal` model, `_apply_migrations()`, and the upsert in `_persist_signal()`.
6. Add raw-value columns to the `Indicator` model and `_apply_migrations()`.
7. Update `total_signal_modules` in `config.yaml`.

Always maintain both TA-Lib and pure-pandas fallback paths (`_TALIB_AVAILABLE` flag pattern).
