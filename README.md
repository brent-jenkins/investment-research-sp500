# SP500Invest

A self-hosted Streamlit application running a single, validated trading strategy — 12-month price momentum, long/flat, with a rare-firing crisis circuit breaker — against the full, dynamically-refreshed S&P 500, with automated Trading212 Invest order placement.

Live at [sp500.example.com](https://sp500.example.com) (password protected).

---

## What it does

The strategy is deliberately simple, not a legacy of an earlier, more complex system that got trimmed down — it's the outcome of a structured, phased validation process (see [CLAUDE.md](CLAUDE.md)'s "V2 architecture" section for the full history) that found no configuration of a more elaborate multi-module design held up under genuine out-of-sample testing.

**The signal.** A stock is a long candidate whenever its trailing 12-month total return is positive; otherwise it's flat. No regime classifier, no confluence scoring, no technical-indicator ensemble.

**The universe.** Every genuine, current S&P 500 constituent — sourced dynamically and refreshed daily from a public, point-in-time membership dataset (`analysis/universe.py:get_live_stock_candidates()`), not a hand-maintained watchlist. A new index addition becomes a live candidate automatically the day after it's added; a removal drops out the same way. No code or config change is needed for either. There are no discretionary exclusions — a stock is eligible on any date it was a genuine index constituent, nothing else (see [CLAUDE.md](CLAUDE.md)'s "The live trading universe" section for the full reasoning, including why an earlier version of this system's subjective exclusions were deliberately not carried forward).

**The crisis override.** A single-day S&P 500 decline of 7% or worse — the real NYSE Level 1 market-wide circuit breaker level — force-exits every open position that day and blocks new entries for its duration plus a 5-trading-day cooldown. Chosen over a VIX-spike alternative after both were backtested; the price-gap trigger fired on only 7 days across the full 2007–2026 validation window and cost no measured edge anywhere it didn't touch. Re-checked fresh — using a genuinely live quote, not just the last stored daily close — immediately before any order is actually submitted, not only at the point the decision was originally queued the night before, so a crisis developing between decision and submission still blocks a stale entry.

**Position sizing.** Historical VaR, scaled to each ticker's own holding period via the square-root-of-time rule — position size scales inversely with a ticker's own volatility rather than every trade getting an identical allocation. The per-trade ceiling is a percentage of the account's *current* total value (fetched fresh on every decision), so sizing scales automatically as the account compounds.

**Execution.** A decide/submit split: the EOD job queues BUY decisions once a day; a separate, exchange-timed job actually submits them roughly an hour after the market opens (avoiding the most volatile opening window), re-checking both the price and the crisis state fresh before placing the order. Exits (crisis, stop-loss, signal turning off, max-holding) are checked hourly and act immediately, independent of the queue.

**Validation.** The full strategy — this exact universe, this exact signal, this exact crisis override — has been backtested continuously from 2007 to the present, split into a 2007–2019 train window and a 2020–present test window, and checked block-by-block against 16 historical sub-periods benchmarked against real S&P 500 performance over the same windows. The Backtest page runs this validated battery on demand, using the same engine that drives live order placement, and can export the result as a standalone due-diligence PDF.

---

## Running locally

```powershell
pip install -r requirements.txt
python -m streamlit run main.py
```

There is no separate "Run Analysis" / "Generate Signals" step — momentum signals are computed live, on demand, directly from stored price history at decision time, not pre-computed into a database table ahead of use. The one genuine first-run requirement is price history: either wait for the next scheduled EOD run, or click "Refresh Data + Orders Now" on the Status page to pull it and run the order pipeline immediately. `trading212_invest.enabled: true` in `config.yaml`, plus `T212_API_KEY`/`T212_API_SECRET` in the environment, are required before any order gets placed — see "Configuration" and "Server deployment" below.

---

## Pages

The app has five pages, toggled via a sidebar radio (default: **Dashboard**).

### Dashboard

The operational home page.

- **EOD status badge** — a direct, unmissable "did tonight's EOD job actually run to completion" signal at the very top of the page, sourced from a completion record the job itself writes (not inferred from a timestamp's age alone) — flags a stale record (>30h old) distinctly from a genuinely fresh one.
- **Crisis-mode banner** — shown only when the crisis override is genuinely active right now, sourced from the exact same authoritative check order submission itself re-confirms before placing an order. States plainly that only *new entries* are blocked — existing positions continue to be monitored and exited normally. Nothing is shown at all when the override isn't active; there is no muted/inactive variant that could be mistaken for a real warning.
- **Account balance** — total value, available cash, invested, deployed %.
- **Account value history** — a £ account-value chart (week/month/year range selector), anchored to the real £50,000 starting balance so it reads correctly even with very little history yet.
- **Top Trades** — top 5 winning and top 5 losing closed positions.
- **Candidate Queue** — every BUY decision currently queued and awaiting its exchange-timed submission window. Each row shows a **Status** column — a live, read-only replay of the real momentum-ranked, capacity-aware admission logic against the account's current state, explicitly labelled a *projection, not a guarantee* (the real decision only happens at actual submission time) — and a **Strength** label (Weak/Moderate/Strong/Very Strong), a qualitative read on the candidate's momentum relative to today's own candidate pool, shown alongside the raw percentage, never replacing it. Both are filterable/sortable.
- **Placed, Unconfirmed Exits** — SELL orders Trading212 has accepted but not yet resolved to FILLED/CANCELLED/REJECTED; this account has a documented lag here (orders have been observed sitting in this state 60+ hours), so this reflects real order-status lag, not a stuck order needing intervention.

### Instruments

A searchable table of every instrument this system currently tracks — ticker, name, and a direct link to its Yahoo Finance quote page. Sourced live from the same dynamic S&P 500 candidate pool live order placement itself reads, plus the fixed reference tickers (`^GSPC`, `GBPUSD=X`, `EURUSD=X`). No in-app charting — Yahoo Finance's own quote page already does that; this page's job is "what's tracked and where do I look it up."

### Performance

Trading212 execution performance, driven entirely by real broker fills — no manual or simulated trade data feeds this page at all.

- **Open Positions** — full-detail cards (live P&L, current signal, days held) for every genuinely open position, sorted alphabetically by ticker — a deliberately calm, non-urgency-ranked ordering (see [CLAUDE.md](CLAUDE.md)'s "Decision-relevant, tone-neutral context on open position cards" section for why).
- **Closed Positions** — every closed position: entry/exit date and price, quantity, P&L in £ and %, holding days, momentum at entry, and exit reason (`CRISIS_EXIT` / `STOP_LOSS` / `SIGNAL_OFF` / `MAX_HOLDING`).
- **Performance Metrics** — gated behind 5 closed positions (real position closes are infrequent, so this takes a while to populate); below 5, a simple win/loss/total-P&L caption. At 5+: win rate, average/total return, profit factor, max drawdown, and a breakdown by exit reason.

### Backtest

Runs and displays the full validated battery described above — 2007 to present, the same live universe and the same live engine that drives real order placement, no separate backtest-only parameter set. Because it walks the full ~500-ticker live candidate pool over nearly two decades, one run takes roughly 12 minutes.

- **Live universe snapshot** — candidate universe size, how many are eligible today, how many were excluded (genuine index-membership figures, never Trading212-specific tradeability).
- **Train / Test Split vs S&P 500** — CAGR and drawdown for both windows, benchmarked against the real index over the identical dates.
- **Full History & Concentration Check** — the continuous 2007–present result, plus a check for whether the edge depends on a small number of outsized trades.
- **16 Historical Blocks vs S&P 500** — the full validation window split into 16 sub-periods, each independently checked against the real index over that exact window.
- **Transaction-Cost Sensitivity** — results recomputed at realistic round-trip cost assumptions.
- **Walk-Forward** — rolling 6-month windows.
- **Equity Curve** — the full-history £ equity curve.
- **Due Diligence Report** — generates a standalone PDF export of the same run for external review.

The page states explicitly, every run, that this is a **continuous walk** (one single simulated account, no balance reset at the train/test boundary) — the convention for reporting what one real, continuously-running account would actually have returned since inception, not a reset-at-boundary comparison between strategy variants. See [CLAUDE.md](CLAUDE.md)'s "Two backtest conventions" section for why this distinction matters and is always stated explicitly.

### Status

System health and manual controls.

- **Pipeline** — last price-data refresh and last order-logic run, with age-based OK/STALE/OVERDUE badges.
- **Scheduled Runs** — the current job schedule (see "Automatic scheduling" below), next-fire countdown for each.
- **Data Coverage** — how many tracked tickers have fresh price data.
- **Manual Controls** — "Refresh Data + Orders Now" (a full price pull plus the order pipeline, equivalent to the scheduled EOD job) and "Order Logic Only" (just the exit check, no data pull).
- **Email Notifications** — current SMTP configuration at a glance.
- **T212 Invest Account** — connection status, position sizing parameters, live account value/cash/deployed-%, a deployed-capital progress bar, and the last 10 automated orders with colour-coded status. An instrument-mapping expander shows every ticker's resolved Trading212 instrument ID, with FAILED rows flagged for manual review (a genuine mismatch is never auto-retried).
- **Scheduler Health** — membership-refresh freshness (including a Wikipedia cross-check for lag detection) and other operational signals.
- **Log Viewer** — recent application log lines, filterable.

---

## The trading logic

### Entry

A ticker becomes a genuine entry candidate only when **all three** hold (`analysis/decision.py:passes_entry_gate()`, the single shared implementation both live order placement and every backtest engine call — never independently re-derived):

1. Trailing 12-month momentum is positive.
2. It's currently a genuine S&P 500 index member.
3. It isn't inside the crisis entry-block window.

### Exit

Checked in this exact priority order (`analysis/decision.py:evaluate_exit_reason()`, again the single shared implementation):

1. **`CRISIS_EXIT`** — today is a raw (≥7%) S&P 500 single-day decline.
2. **`STOP_LOSS`** — price has reached the ATR-based stop fixed at entry (`entry_price − N × ATR(14)`).
3. **`SIGNAL_OFF`** — trailing 12-month momentum has turned non-positive.
4. **`MAX_HOLDING`** — the holding-days backstop (`trading212_invest.stock_max_holding_days`, default 30) is reached.

### Decide, then submit — and re-check before acting

The EOD job (21:30 UTC daily) computes the day's signal for every live candidate and, for anything newly qualifying, queues a sized BUY decision into `pending_orders` — it does **not** submit an order at that point. A separate job (14:30 UTC weekdays, 60 minutes after NYSE's regular open — chosen deliberately over submitting right at the open, to avoid the most volatile window for a market order's actual fill) walks the queue in momentum-ranked order and submits each one, but only after re-confirming, fresh, against real current data:

- **Price sanity** — if the ticker has moved more than `price_sanity_threshold_pct` (default 3%) since the decision was made, the order is skipped rather than executed on a stale decision.
- **Crisis status** — re-checked using a genuinely live S&P quote, not just the last stored daily close (which, at submission time, can only ever reflect *yesterday's* close and would otherwise make this re-check a no-op). If a crisis has developed since the decision was queued, every currently-queued entry for that run is skipped, not just the one being evaluated.
- **Capacity** — a skip-and-continue walk against `max_total_deployed` and free cash: a candidate that doesn't fit is skipped and the walk continues to the next (lower-priority) one, rather than either stopping outright or silently shrinking the position to fit.

A check *failure* (a data-read error, not a confirmed block) is treated differently from a confirmed block: the affected decision is left queued for the next window to retry, rather than either being discarded or assumed safe.

### Position sizing

Historical VaR (`analysis/volatility.py:_var_position_size_core()`), not a flat allocation: reads the last `var_lookback_days` (default 252) daily closes, computes the `(1 − var_confidence)` percentile daily loss, scales it to the ticker's own holding period via the square-root-of-time rule, then sizes the position so a holding-period move of that magnitude would lose at most `max_risk_pct` (default 1%) of the account's *current* total value. Floored at `min_position_gbp`, capped by `per_trade_pct` of current account value (itself bounded by a rarely-binding `absolute_max_position_gbp` safety ceiling). Position count is an emergent consequence of `per_trade_pct` and `max_total_deployed` together, not a separately-enforced cap.

---

## Automatic scheduling

Seven scheduled activities, fired by a **standalone scheduler daemon** (`scheduler_daemon.py`, managed by `investment-research-scheduler.service`) regardless of whether anyone has the dashboard open:

| Schedule | Job | What it does |
|---|---|---|
| Every 60 seconds | Job dispatcher | Refreshes the T212 account/positions snapshot when a UI-requested refresh is pending |
| Daily 21:15 UTC (NYSE-calendar-aware) | Membership refresh | Re-fetches point-in-time S&P 500 membership, 15 minutes ahead of EOD |
| Daily 21:30 UTC (NYSE-calendar-aware) | EOD | The only job that pulls prices; computes the day's signals, queues new BUY decisions, runs the exit check |
| Mon–Fri 14:30 UTC | Stock order submission | Submits queued decisions, with fresh price/crisis/capacity re-checks |
| Mon–Fri 00:00–21:00 + 23:00 UTC (hourly) | Trade alerts | Runs the exit check, polls order-status, refreshes the account snapshot |
| Monday 07:00 UTC | Monday pre-market | Cancels any BUY order still unfilled from Friday's EOD run, then re-decides against Friday's closing prices |
| Sunday 09:00 Europe/London (DST-correct) | Weekly summary | Emails a status summary — open positions, week's P&L, positions closed, whether the crisis override fired |

Both `investment-research` (the UI) and `investment-research-scheduler` (the daemon) must be restarted on every deploy — each keeps its own in-memory copy of the code, and a deploy that only restarts the UI leaves the daemon placing orders under the old logic. Both deploy scripts restart both services and warn (with a confirmation prompt) if run within 30 minutes of a scheduled job window.

The Streamlit UI also starts an in-process scheduler as a fallback, deferring to the standalone daemon if its heartbeat is less than 90 seconds old — this prevents duplicate job execution.

---

## Database schema

SQLite via SQLAlchemy, WAL mode, `NullPool` (each `with conn:` block gets a fresh connection — safe for Streamlit's multi-threaded reruns).

| Table | Purpose |
|---|---|
| `price_data` | Daily OHLCV per ticker — permanent, traceable storage for everything ever fetched, never purged for a ticker falling out of the live universe |
| `universe_membership` | Point-in-time S&P 500 membership windows per ticker, refreshed daily |
| `pending_orders` | BUY decisions queued at EOD, awaiting exchange-timed submission — resolved to `SUBMITTED`/`SKIPPED_PRICE_MOVED`/`SKIPPED_CRISIS_BLOCKED`/`REJECTED_CAPACITY`/etc.; resolved rows are purged after 30 days, still-`PENDING` rows never are |
| `invest_orders` | Every order attempt (BUY and SELL) — the execution audit trail |
| `pnl` | Reconstructed realised P&L per closed position, FIFO-paired from `invest_orders` |
| `instrument_resolutions` | Cached, verified Trading212 instrument-ID mapping per ticker — resolved once, never silently re-guessed |
| `position_reconciliations` | Discrepancies between this system's own order history and Trading212's real live positions (e.g. a broker-side corporate action closing a position with no SELL from this system) |
| `account_value_history` | Daily EOD account-value snapshots, powering the Dashboard's value chart |
| `system_metadata` | Key-value store for operational state |
| `schema_migrations` | Migration tracking |

Schema migrations run on every startup. `price_data` is genuinely permanent — a ticker falling out of the S&P 500 (or out of the frozen research universe) keeps its price history rather than having it deleted, so re-adding it later doesn't require a full re-fetch, and historical research always has the full record available.

---

## Configuration (`config.yaml`)

`config.yaml` is git-tracked (not gitignored) — secret fields (SMTP password) are committed as blank placeholders, with real values supplied only via environment variables. There is no `watchlist:` section — see "The live trading universe" above.

```yaml
reference_tickers:      # fixed, rarely-changed monitoring/conversion instruments only
  - {ticker: "^GSPC", name: "S&P 500", exchange: "NYSE"}
  - {ticker: "GBPUSD=X", name: "GBP/USD", exchange: "FX"}
  - {ticker: "EURUSD=X", name: "EUR/USD", exchange: "FX"}

data:
  history_start_date: "2007-01-01"    # fixed anchor for a ticker's first-download fetch
  rate_limit_seconds: 2.5
  max_retries: 5

database:
  path: "database/investment_research.db"

scheduler:
  timezone: "UTC"
  runs:
    eod: {hour: 21, minute: 30, label: "End of Day"}   # the only run that pulls prices

signals:
  atr_period: 14        # stop-loss sizing

notifications:
  email:
    enabled: true
    smtp_host: "smtp-relay.brevo.com"
    smtp_port: 587
    smtp_password: ""   # set via SMTP_PASSWORD env var
    to_addresses: ["you@example.com"]
    alert_cooldown_hours: 8
  weekly_summary:
    enabled: true

backtest:
  starting_capital_gbp: 50000   # notional -- VaR sizing's reference, not a real compounding balance in the backtest engine
  atr_stop_multiplier: 2.0

trading212_invest:
  enabled: true
  live: false            # NEVER set true -- place_market_order() blocks it in code regardless
  max_total_deployed: 0.8
  per_trade_pct: 0.05     # % of the account's CURRENT total value -- the VaR sizing ceiling
  absolute_max_position_gbp: 1000000   # rarely-binding safety ceiling
  var_confidence: 0.95
  var_lookback_days: 252
  max_risk_pct: 0.01
  min_position_gbp: 50
  stock_max_holding_days: 30
  stock_trading_enabled: true          # gates entry CANDIDACY
  stock_submission_enabled: true       # gates the submission job specifically
  price_sanity_threshold_pct: 0.03
  pending_orders_retention_days: 30
```

`trading212_invest.live` is a hard-coded safety block, not just a config convention — `Trading212Client.place_market_order()` raises if `live=True` is ever passed, so accidentally flipping this flag cannot place a real order without also removing that guard in code.

---

## Server deployment

| Item | Value |
|---|---|
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

Set the dashboard password:

```bash
caddy hash-password && nano /etc/caddy/Caddyfile   # replace REPLACE_WITH_HASH
systemctl reload caddy
```

### Managing server credentials

API keys and passwords live in a systemd drop-in file that deploys never touch:

```bash
mkdir -p /etc/systemd/system/investment-research.service.d/
nano /etc/systemd/system/investment-research.service.d/secrets.conf
```

```ini
[Service]
Environment=SMTP_PASSWORD=your_smtp_password
Environment=T212_API_KEY=your_t212_key
Environment=T212_API_SECRET=your_t212_secret
```

```bash
systemctl daemon-reload && systemctl restart investment-research investment-research-scheduler investment-research-webhook
```

The scheduler and webhook-listener services inherit the same credentials automatically — `setup.sh` and `deploy.sh` symlink their `.service.d/secrets.conf` to the same file as the main service.

### Deploying updates

Deploys are triggered by a GitHub webhook, not by pushing directly from a developer machine — see CLAUDE.md's "Deployment" section for the full design. Once the webhook and the GitHub Actions test gate are both configured (one-time, manual — see CLAUDE.md), `git push origin main` is enough:

```bash
git push origin main   # GitHub Actions runs the full test suite; on success it deploys automatically
```

There is no local pre-push hook any more — GitHub Actions is the real, unbypassable test gate (a local hook could always be skipped via `--no-verify` or simply not installed). Run `pytest` locally before pushing if you want faster feedback, but nothing enforces it client-side.

Manual/break-glass deploy (only needed if the webhook path itself is down): SSH in and run `bash deploy.sh` directly on the VM — it's the same script the webhook calls, always deploying immediately. All three systemd services are restarted every deploy — restarting only the UI leaves the scheduler daemon (which actually places orders) running the previous code.

```bash
# Verify all three services picked up the same deploy:
ps aux | grep -E "streamlit|scheduler_daemon|webhook_listener" | grep -v grep
systemctl status investment-research investment-research-scheduler investment-research-webhook
```

### Useful server commands

```bash
systemctl restart investment-research            # Streamlit UI
systemctl restart investment-research-scheduler  # Scheduler daemon
journalctl -u investment-research -f             # UI logs
journalctl -u investment-research-scheduler -f   # Scheduler logs
```

---

## Testing

```powershell
pytest
```

The suite is fully self-contained — no test touches the real project database, calls yfinance, or calls the live Trading212 API (an in-memory SQLite fixture and mocked HTTP clients throughout). It runs automatically on every `git push` to `main` via GitHub Actions (`.github/workflows/test-and-deploy.yml`), on GitHub's own infrastructure; a failing suite blocks the deploy.

---

## Further reading

[CLAUDE.md](CLAUDE.md) is the project's full engineering log — every design decision, every rejected alternative (with the numbers that ruled it out), and every incident, in chronological order. It is substantially larger than this document and includes an explicit historical-framing note pointing out which of its early sections describe a since-superseded design — start from "V2 architecture — phased rebuild, Stage 1" for the system as it actually runs today.
