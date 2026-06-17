"""APScheduler-based scheduled jobs: three daily runs with per-exchange holiday awareness.

Run schedule (weekdays only, UTC):
  07:00  Pre-market  — analysis only, no data pull
  13:30  US open     — analysis only, no data pull
  21:30  End of day  — full data refresh then analysis (primary run)
  14–21  Hourly      — open-trade health check; email alert on STOP LOSS / EXIT SIGNAL

All three analysis runs execute the complete analysis pipeline:
  run_analysis()       → indicators table
  generate_signals()   → signals table  (also stamps last_analysis_run)
  compute_all_pairs()  → correlations table

Metadata keys written to system_metadata:
  last_full_refresh         — UTC timestamp of the last successful data pull (EOD run or manual)
  last_analysis_run         — UTC timestamp of the last completed analysis (any run type)
  last_run_type             — 'full' or 'analysis_only'
  trade_alert_status:{id}   — last health status for which an alert email was sent per trade
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from analysis.confluence import generate_signals
from analysis.correlations import compute_all_pairs
from analysis.engine import run_analysis
from analysis.macro import fetch_and_store_macro_indicators
from data.database import get_metadata_str, set_metadata
from data.ingestion import get_all_tickers, pull_incremental

logger = logging.getLogger(__name__)

RUN_FULL          = "full"
RUN_ANALYSIS_ONLY = "analysis_only"

_scheduler: BackgroundScheduler | None = None


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _build_ticker_exchange_map(config: dict) -> dict[str, str]:
    """Return {ticker: exchange_calendar_name} for every watchlist entry."""
    mapping: dict[str, str] = {}
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry:
                mapping[entry["ticker"]] = entry.get("exchange", "NYSE")
    return mapping


def _is_exchange_open(calendar_name: str, check_date: date) -> bool:
    """Return True if *calendar_name* has a trading session on *check_date*.

    Crypto markets trade 24/7 and are never skipped.
    Fails open (returns True) on any calendar lookup error so assets are
    never silently skipped due to a misconfigured calendar name.
    """
    if calendar_name == "Crypto":
        return True
    try:
        cal = mcal.get_calendar(calendar_name)
        schedule = cal.schedule(
            start_date=check_date.isoformat(),
            end_date=check_date.isoformat(),
        )
        return not schedule.empty
    except Exception as exc:
        logger.warning(
            "Calendar check failed for %s on %s: %s — assuming open.",
            calendar_name, check_date, exc,
        )
        return True


def _process_virtual_trades(config: dict) -> None:
    """Auto-open/close virtual positions based on current signals.

    Called at the end of every analysis pipeline run.  Has no effect unless
    virtual_trading.enabled: true in config.yaml.

    Close conditions  (checked first):
      - Current price has crossed the stored stop_loss_price, or
      - Signal has reversed direction (LONG trade when signal is SELL/STRONG_SELL, or vice-versa).

    Open conditions:
      - Signal is STRONG_BUY or STRONG_SELL (or BUY/SELL if entry_signal: "BUY" in config),
      - Ticker is tradeable,
      - No existing open virtual position for that ticker,
      - Total open virtual positions < max_open_positions.
    """
    vt = config.get("virtual_trading", {})
    if not vt.get("enabled", False):
        return

    from sqlalchemy import text as sa_text  # noqa: PLC0415
    from data.database import get_engine    # noqa: PLC0415

    entry_threshold = vt.get("entry_signal", "STRONG_BUY")
    max_positions   = int(vt.get("max_open_positions", 5))
    position_pct    = float(vt.get("position_pct", 0.05))
    portfolio_gbp   = float(vt.get("portfolio_size_gbp", 10000))
    atr_mult        = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))

    tradeable: set[str] = {
        e["ticker"]
        for g in config.get("watchlist", {}).values()
        if isinstance(g, list)
        for e in g
        if isinstance(e, dict) and e.get("tradeable")
    }

    if not tradeable:
        return

    eng = get_engine()
    now = datetime.utcnow()

    # ── Load data ─────────────────────────────────────────────────────────────
    with eng.connect() as conn:
        open_rows = conn.execute(sa_text(
            "SELECT * FROM trades WHERE account_type='VIRTUAL' AND is_open=1"
        )).mappings().all()

        sig_rows = conn.execute(sa_text("""
            SELECT s.ticker, s.signal, s.confluence_score, s.regime,
                   s.trend_score, s.mean_reversion_score, s.volatility_score,
                   s.volume_score, s.macro_score
            FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM signals GROUP BY ticker
            ) latest ON s.ticker = latest.ticker AND s.date = latest.md
        """)).mappings().all()

        price_rows = conn.execute(sa_text("""
            SELECT p.ticker, p.close
            FROM price_data p
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM price_data GROUP BY ticker
            ) latest ON p.ticker = latest.ticker AND p.date = latest.md
        """)).mappings().all()

        atr_rows = conn.execute(sa_text("""
            SELECT i.ticker, i.atr
            FROM indicators i
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM indicators GROUP BY ticker
            ) latest ON i.ticker = latest.ticker AND i.date = latest.md
        """)).mappings().all()

    open_trades   = [dict(r) for r in open_rows]
    open_by_ticker = {t["ticker"]: t for t in open_trades}
    signals       = {r["ticker"]: dict(r) for r in sig_rows if r["ticker"] in tradeable}
    prices        = {r["ticker"]: float(r["close"]) for r in price_rows if r["close"] is not None}
    atrs          = {r["ticker"]: float(r["atr"]) for r in atr_rows if r["atr"] is not None}

    # ── Close phase ───────────────────────────────────────────────────────────
    to_close: list[tuple[int, float, str]] = []  # (trade_id, exit_price, reason)
    for ticker, trade in list(open_by_ticker.items()):
        live_price = prices.get(ticker)
        if live_price is None:
            continue

        direction   = trade.get("direction", "LONG")
        entry_price = float(trade.get("entry_price") or 0)
        sl_price    = trade.get("stop_loss_price")
        trade_id    = trade["id"]

        # Fall back to ATR-based stop if none stored
        if sl_price is None and entry_price and ticker in atrs:
            if direction == "LONG":
                sl_price = entry_price - atr_mult * atrs[ticker]
            else:
                sl_price = entry_price + atr_mult * atrs[ticker]

        stop_hit = (
            (direction == "LONG"  and sl_price is not None and live_price <= sl_price) or
            (direction == "SHORT" and sl_price is not None and live_price >= sl_price)
        )

        sig_now = signals.get(ticker, {}).get("signal", "NEUTRAL")
        signal_reversal = (
            (direction == "LONG"  and sig_now in ("SELL", "STRONG_SELL")) or
            (direction == "SHORT" and sig_now in ("BUY",  "STRONG_BUY"))
        )

        if stop_hit or signal_reversal:
            reason = "STOP LOSS" if stop_hit else "SIGNAL REVERSAL"
            to_close.append((trade_id, live_price, reason, direction, entry_price, trade.get("entry_date")))
            del open_by_ticker[ticker]

    if to_close:
        with eng.begin() as conn:
            for trade_id, live_price, reason, direction, entry_price, entry_dt in to_close:
                pnl_pct = 0.0
                if entry_price:
                    pnl_pct = ((live_price - entry_price) / entry_price
                               if direction == "LONG"
                               else (entry_price - live_price) / entry_price)
                hold_days = 0
                if entry_dt:
                    try:
                        entry_dt_parsed = (
                            entry_dt if isinstance(entry_dt, datetime)
                            else datetime.fromisoformat(str(entry_dt).split(".")[0])
                        )
                        hold_days = max(0, (now - entry_dt_parsed).days)
                    except Exception:
                        pass
                conn.execute(sa_text("""
                    UPDATE trades
                    SET is_open=0, exit_date=:exit_date, exit_price=:exit_price,
                        pnl_pct=:pnl_pct, holding_days=:hold_days
                    WHERE id=:trade_id
                """), {
                    "exit_date":  now.isoformat(),
                    "exit_price": live_price,
                    "pnl_pct":    round(pnl_pct, 6),
                    "hold_days":  hold_days,
                    "trade_id":   trade_id,
                })
                logger.info(
                    "Virtual trade closed: %s at %.4f (P&L %.2f%%) — %s",
                    trade_id, live_price, pnl_pct * 100, reason,
                )

    # ── Open phase ────────────────────────────────────────────────────────────
    open_count = len(open_by_ticker)
    if open_count >= max_positions:
        logger.info("Virtual trading: at max positions (%d/%d) — no new entries.", open_count, max_positions)
        return

    entry_signal_set = (
        {"STRONG_BUY", "BUY", "STRONG_SELL", "SELL"}
        if entry_threshold == "BUY"
        else {"STRONG_BUY", "STRONG_SELL"}
    )

    to_open: list[dict] = []
    for ticker, sig in signals.items():
        if open_count + len(to_open) >= max_positions:
            break
        if ticker in open_by_ticker:
            continue
        sig_type = sig.get("signal", "NEUTRAL")
        if sig_type not in entry_signal_set:
            continue
        live_price = prices.get(ticker)
        if not live_price:
            continue

        direction  = "LONG" if sig_type in ("STRONG_BUY", "BUY") else "SHORT"
        atr        = atrs.get(ticker)
        sl_price   = None
        if atr:
            sl_price = (live_price - atr_mult * atr if direction == "LONG"
                        else live_price + atr_mult * atr)

        to_open.append({
            "ticker":               ticker,
            "direction":            direction,
            "entry_date":           now.isoformat(),
            "entry_price":          live_price,
            "signal_type":          sig_type,
            "confluence_score":     sig.get("confluence_score"),
            "regime":               sig.get("regime"),
            "trend_score":          sig.get("trend_score"),
            "mean_reversion_score": sig.get("mean_reversion_score"),
            "volatility_score":     sig.get("volatility_score"),
            "volume_score":         sig.get("volume_score"),
            "macro_score":          sig.get("macro_score"),
            "investment":           round(portfolio_gbp * position_pct, 2),
            "sl":                   round(sl_price, 6) if sl_price else None,
        })

    if to_open:
        with eng.begin() as conn:
            for row in to_open:
                conn.execute(sa_text("""
                    INSERT INTO trades (
                        ticker, account_type, account_name, account_id,
                        direction, entry_date, entry_price, signal_type,
                        confluence_score, regime, trend_score, mean_reversion_score,
                        volatility_score, volume_score, macro_score,
                        investment_amount, stop_loss_price, is_open, created_at
                    ) VALUES (
                        :ticker, 'VIRTUAL', 'Virtual Portfolio', 'virtual',
                        :direction, :entry_date, :entry_price, :signal_type,
                        :confluence_score, :regime, :trend_score, :mean_reversion_score,
                        :volatility_score, :volume_score, :macro_score,
                        :investment, :sl, 1, :now
                    )
                """), {**row, "now": now.isoformat()})
                logger.info(
                    "Virtual trade opened: %s %s at %.4f (signal=%s, regime=%s)",
                    row["direction"], row["ticker"], row["entry_price"],
                    row["signal_type"], row["regime"],
                )

    logger.info(
        "Virtual trading: closed %d, opened %d, total open %d/%d.",
        len(to_close), len(to_open), open_count - len(to_close) + len(to_open), max_positions,
    )


def _run_analysis_pipeline(config: dict, all_tickers: list[str], run_type: str) -> None:
    """Execute the full analysis pipeline and stamp metadata.

    Runs for all watchlist tickers regardless of which exchanges are open —
    stored price data is always analysed, even if no new rows were fetched.

    Steps:
      1. run_analysis()       → indicators + correlations tables
      2. generate_signals()   → signals table; also stamps last_analysis_run
      3. set last_run_type
    """
    try:
        run_analysis(tickers=all_tickers, config=config)
    except Exception as exc:
        logger.error("run_analysis failed: %s", exc, exc_info=True)

    try:
        generate_signals(tickers=all_tickers, config=config)
    except Exception as exc:
        logger.error("generate_signals failed: %s", exc, exc_info=True)

    # generate_signals() already stamps last_analysis_run; record run type separately.
    try:
        set_metadata("last_run_type", run_type)
    except Exception as exc:
        logger.warning("Could not stamp last_run_type: %s", exc)

    try:
        _process_virtual_trades(config)
    except Exception as exc:
        logger.error("Virtual trading automation failed: %s", exc, exc_info=True)

    logger.info("Analysis pipeline complete (run_type=%s).", run_type)


def _pre_market_job() -> None:
    """07:00 UTC — analysis only using most recently stored price data."""
    logger.info("Scheduled pre-market analysis starting (07:00 UTC)…")
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)


def _us_open_job() -> None:
    """13:30 UTC — analysis only using most recently stored price data."""
    logger.info("Scheduled US-open analysis starting (13:30 UTC)…")
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)


def _eod_job() -> None:
    """21:30 UTC — pull incremental data for open exchanges, then run full analysis."""
    logger.info("Scheduled end-of-day refresh starting (21:30 UTC)…")
    config = _load_config()
    ticker_exchange = _build_ticker_exchange_map(config)

    # International index tickers are always pulled regardless of their local
    # exchange calendar.  yfinance handles data availability internally, so these
    # are never silently skipped on local bank holidays.
    _ALWAYS_PULL: frozenset[str] = frozenset({
        "^FTSE", "^GDAXI", "^HSI", "^N225", "^STOXX50E",
    })

    # Determine which exchanges have sessions today (UTC date avoids timezone drift
    # at the midnight boundary; 21:30 UTC is safely within the same calendar day).
    today_utc: date = datetime.now(timezone.utc).date()
    unique_exchanges = set(ticker_exchange.values())
    open_exchanges: dict[str, bool] = {
        exch: _is_exchange_open(exch, today_utc)
        for exch in unique_exchanges
    }

    open_tickers:    list[str] = []
    skipped_tickers: list[str] = []
    for ticker, exchange in ticker_exchange.items():
        if ticker in _ALWAYS_PULL or open_exchanges[exchange]:
            open_tickers.append(ticker)
        else:
            logger.info("Skipping %s — %s closed today (%s).", ticker, exchange, today_utc)
            skipped_tickers.append(ticker)

    if not open_tickers:
        logger.info("All markets closed today (%s) — skipping data pull.", today_utc)
        # Still run analysis so scores stay current from yesterday's data.
        _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)
        return

    logger.info(
        "EOD refresh — pulling %d assets (%d skipped, market closed).",
        len(open_tickers), len(skipped_tickers),
    )
    results = pull_incremental(tickers=open_tickers)
    total_rows = sum(results.values())
    logger.info("Data pull complete — %d new rows across %d assets.", total_rows, len(open_tickers))

    # Stamp last_full_refresh immediately after a successful data pull.
    try:
        set_metadata("last_full_refresh", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Could not stamp last_full_refresh: %s", exc)

    # Fetch FRED macro indicators once per EOD run (not per-ticker).
    # Must run before analysis so compute_macro() reads fresh scores from DB.
    try:
        logger.info("Fetching FRED macro indicators…")
        fetch_and_store_macro_indicators(config)
    except Exception as exc:
        logger.error("FRED macro indicator fetch failed: %s", exc, exc_info=True)

    # Analyse all tickers (including those whose exchange was closed — their
    # stored history is still valid and their scores should remain up to date).
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)


def _trade_alert_job() -> None:
    """Hourly (14:00–21:00 UTC, weekdays) — check open trade health and send alerts.

    Fetches a live price for every open trade, enriches it with current signal
    and stop-loss status, then emails an alert when any trade reaches STOP LOSS
    or EXIT SIGNAL.  Deduplicates per trade: the same status for the same trade
    only triggers one email until the status changes.

    The global alert_cooldown_hours setting in config.yaml still applies — if an
    alert was sent very recently it is deferred until the cooldown elapses.
    """
    logger.info("Trade alert check starting…")
    config = _load_config()

    # Lazy imports to avoid circular dependencies at module load time.
    from output.performance import get_latest_price, get_open_trades  # noqa: PLC0415
    from output.briefing import (  # noqa: PLC0415
        enrich_open_trade,
        send_alert_email,
        should_send_alert,
    )

    trades = get_open_trades()
    if not trades:
        logger.info("Trade alert check: no open trades.")
        return

    _ALERT_STATUSES: frozenset[str] = frozenset({"STOP LOSS", "EXIT SIGNAL"})
    pending: list[dict] = []

    for trade in trades:
        ticker   = trade.get("ticker", "")
        trade_id = trade.get("id")

        try:
            live_price = get_latest_price(ticker)
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", ticker, exc)
            live_price = None

        try:
            enriched = enrich_open_trade(trade, live_price)
        except Exception as exc:
            logger.warning("enrich_open_trade failed for %s: %s", ticker, exc)
            continue

        status = enriched.get("status", "ON TRACK")
        if status not in _ALERT_STATUSES:
            continue

        # Only alert when the status is new (differs from what we last alerted for).
        meta_key = f"trade_alert_status:{trade_id}"
        if get_metadata_str(meta_key) == status:
            logger.info(
                "Trade %s (%s): already alerted for %s — skipping.",
                trade_id, ticker, status,
            )
            continue

        pending.append(enriched)

    if not pending:
        logger.info(
            "Trade alert check: no new conditions across %d open trade(s).", len(trades)
        )
        return

    logger.info("Trade alert check: %d new alert(s) pending.", len(pending))

    if not should_send_alert(config):
        logger.info("Alert email suppressed by cooldown or disabled config.")
        return

    ok, msg = send_alert_email(pending, config)
    logger.info("Alert email result: %s — %s", ok, msg)

    if ok:
        for enriched in pending:
            meta_key = f"trade_alert_status:{enriched.get('id')}"
            try:
                set_metadata(meta_key, enriched.get("status", ""))
            except Exception as exc:
                logger.warning(
                    "Could not stamp alert status for trade %s: %s",
                    enriched.get("id"), exc,
                )


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler with all three daily jobs if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    config   = _load_config()
    sched_cfg = config["scheduler"]
    tz        = sched_cfg["timezone"]

    _scheduler = BackgroundScheduler(timezone=tz)

    _JOB_FNS = {
        "pre_market": _pre_market_job,
        "us_open":    _us_open_job,
        "eod":        _eod_job,
    }

    for run_name, run_cfg in sched_cfg["runs"].items():
        job_fn = _JOB_FNS.get(run_name)
        if job_fn is None:
            logger.warning("Unknown scheduler run name '%s' — skipping.", run_name)
            continue
        _scheduler.add_job(
            job_fn,
            trigger=CronTrigger(
                hour=run_cfg["hour"],
                minute=run_cfg["minute"],
                day_of_week="mon-fri",
                timezone=tz,
            ),
            id=f"scheduled_{run_name}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled '%s' job at %02d:%02d %s on weekdays.",
            run_cfg.get("label", run_name), run_cfg["hour"], run_cfg["minute"], tz,
        )

    # Hourly trade alert check during US market hours (14:00–21:00 UTC, weekdays).
    # Fires on the hour; 14:00 catches the open (US market opens 14:30 UTC) and
    # 21:00 catches the close (US market closes 21:00 UTC).
    _scheduler.add_job(
        _trade_alert_job,
        trigger=CronTrigger(
            hour="14-21",
            minute=0,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id="trade_alert_check",
        replace_existing=True,
    )
    logger.info("Scheduled trade alert check hourly 14:00–21:00 %s on weekdays.", tz)

    _scheduler.start()
    logger.info("Scheduler started with %d jobs.", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None
