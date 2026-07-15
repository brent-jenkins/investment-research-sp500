"""APScheduler-based scheduled jobs: daily runs with per-exchange holiday awareness.

Run schedule (UTC):
  07:00  Pre-market (Tue-Fri) — pull fresh data for exchanges that just closed (Asian
                       markets), then run analysis
  07:00  Monday pre-market (Mon only) — cancel stale weekend BUY orders left PLACED
                       from Friday's EOD run, then the same analysis-only pipeline as
                       the Tue-Fri pre-market job (no data pull — see
                       _monday_premarket_job() for why Monday needs its own job
                       instead of just widening _pre_market_job() to "mon-fri")
  13:30  US open     — analysis only, no data pull
  21:30  End of day  — full data refresh then analysis (all exchanges); places T212 orders
  00-21,23  Hourly   — open-trade health check; email alert on STOP LOSS / EXIT SIGNAL
                       only fires for trades whose home exchange is currently in session

Every analysis run executes the complete analysis pipeline:
  run_analysis()       → indicators table
  generate_signals()   → signals table  (also stamps last_analysis_run)
  compute_all_pairs()  → correlations table

Metadata keys written to system_metadata:
  last_full_refresh          — UTC timestamp of the last successful data pull (EOD run or manual)
  last_analysis_run          — UTC timestamp of the last completed analysis (any run type)
  last_run_type              — 'full' or 'analysis_only'
  trade_alert_status:{id}    — last health status for which an alert email was sent per trade
  t212_account_snapshot      — JSON of latest T212 account summary (written by _fetch_t212_snapshot)
  t212_positions_snapshot    — JSON of latest T212 positions list (written by _fetch_t212_snapshot)
  t212_snapshot_fetched_at   — UTC ISO timestamp of last successful T212 fetch
  pending_t212_fetch         — "1" when the UI has requested a refresh; cleared by _dispatch_pending_jobs
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from analysis.confluence import generate_signals
from analysis.correlations import compute_all_pairs
from analysis.engine import run_analysis
from analysis.macro import fetch_and_store_macro_indicators, refresh_release_calendars
from data.database import get_metadata_str, set_metadata
from data.ingestion import get_all_tickers, pull_incremental

logger = logging.getLogger(__name__)

RUN_FULL          = "full"
RUN_ANALYSIS_ONLY = "analysis_only"

# Age threshold (hours) for _monday_premarket_job() to treat a PLACED BUY order as a
# stale weekend leftover. A Friday-21:30-UTC order is ~33.5h old by Monday 07:00 —
# 30h comfortably covers that while staying well clear of same-day orders if this
# job ever fires on an unexpected day.
_STALE_ORDER_HOURS = 30

_scheduler: BackgroundScheduler | None = None

# pandas_market_calendars does not recognise all exchange shorthands used in
# config.yaml.  Map the config name → the registered calendar name.
_CALENDAR_ALIASES: dict[str, str] = {
    "TSE": "JPX",   # Tokyo Stock Exchange → Japan Exchange Group
}

# Tickers that must always be pulled regardless of calendar status.  Includes
# European/Asian indices that close before the 21:30 UTC EOD run, and ^VIX
# which is on NYSE (closed for US federal holidays like Juneteenth).
_ALWAYS_PULL: frozenset[str] = frozenset({
    "^FTSE", "^GDAXI", "^HSI", "^N225", "^STOXX50E", "^VIX",
})

# Exchange types with no pandas_market_calendars mapping that trade continuously
# on weekdays.  The EOD job must not rely solely on _is_exchange_open() for
# these — an unrecognised calendar name could cause them to be skipped.
_ALWAYS_OPEN_EXCHANGES: frozenset[str] = frozenset({"FX", "Crypto"})

def _resolve_calendar(name: str) -> str:
    return _CALENDAR_ALIASES.get(name, name)


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

    Crypto and FX markets are treated as always open on weekdays.
    Fails open (returns True) on any calendar lookup error so assets are
    never silently skipped due to a misconfigured calendar name.
    """
    if calendar_name in ("Crypto", "FX"):
        return True
    name = _resolve_calendar(calendar_name)
    try:
        cal = mcal.get_calendar(name)
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


def _get_calendar_session(
    calendar_name: str, check_date: date
) -> tuple[datetime, datetime] | None:
    """Return (open_utc, close_utc) for the exchange on check_date, or None if closed.

    Crypto and FX are treated as open 24 h — returns None (callers handle the
    special case by treating None as 'always eligible').
    """
    if calendar_name in ("Crypto", "FX"):
        return None  # always open; caller treats None as unconditionally eligible
    name = _resolve_calendar(calendar_name)
    try:
        cal = mcal.get_calendar(name)
        schedule = cal.schedule(
            start_date=check_date.isoformat(),
            end_date=check_date.isoformat(),
        )
        if schedule.empty:
            return None
        row = schedule.iloc[0]
        open_dt  = row["market_open"].to_pydatetime()
        close_dt = row["market_close"].to_pydatetime()
        # Ensure UTC-aware
        if open_dt.tzinfo is None:
            open_dt  = open_dt.replace(tzinfo=timezone.utc)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        return open_dt, close_dt
    except Exception as exc:
        logger.warning("Session lookup failed for %s on %s: %s", calendar_name, check_date, exc)
        return None


def _exchanges_closed_before(
    exchanges: set[str], cutoff_utc: datetime
) -> set[str]:
    """Return exchanges whose session ENDED today before *cutoff_utc*.

    Used by the pre-market (07:00 UTC) run to identify which exchanges just
    closed and therefore have fresh end-of-day data ready to pull.
    Crypto and FX are excluded (they never 'close').
    """
    closed: set[str] = set()
    check_date = cutoff_utc.date()
    for exch in exchanges:
        if exch in ("Crypto", "FX"):
            continue
        session = _get_calendar_session(exch, check_date)
        if session is None:
            continue  # market was closed all day — nothing to pull
        _, close_dt = session
        if close_dt < cutoff_utc:
            closed.add(exch)
    return closed


def _fetch_t212_snapshot(config: dict) -> None:
    """Fetch T212 account summary + positions and persist as JSON in system_metadata.

    Called by the 60-second dispatcher (on-demand UI requests) and by the EOD job
    so the snapshot stays reasonably fresh without manual refresh.  All T212 API
    calls are centralised here — the UI only reads from the DB.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return
    if not (os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET")):
        return

    live = t212_cfg.get("live", False)
    try:
        from brokers.trading212 import (  # noqa: PLC0415
            Trading212Client,
            poll_placed_orders as _poll,
        )
        client    = Trading212Client(live=live)
        summary   = client.get_account_summary()
        positions = client.get_positions()
        set_metadata("t212_account_snapshot",  json.dumps(summary))
        set_metadata("t212_positions_snapshot", json.dumps(positions or []))
        set_metadata("t212_snapshot_fetched_at", datetime.utcnow().isoformat())
        n_pos = len(positions) if isinstance(positions, list) else 0
        logger.info("T212 account snapshot updated (%d position(s)).", n_pos)

        # Update the drawdown-scaling high water mark on every snapshot refresh —
        # see analysis/risk.py:update_peak_portfolio_value().
        try:
            from analysis.risk import update_peak_portfolio_value  # noqa: PLC0415
            _total_val = float((summary or {}).get("totalValue", 0) or 0)
            if _total_val > 0:
                update_peak_portfolio_value(_total_val, config)
        except Exception as exc:
            logger.warning("Drawdown peak update failed: %s", exc)
    except Exception as exc:
        logger.error("T212 snapshot fetch failed: %s", exc)
        set_metadata("t212_invest_error", str(exc))
        return

    try:
        _poll(config)
    except Exception as exc:
        logger.warning("T212 order poll failed after snapshot: %s", exc)


def _dispatch_pending_jobs() -> None:
    """60-second job: execute any pending tasks queued by the UI.

    Currently handles a single flag: ``pending_t212_fetch``.  The flag is
    cleared *before* the fetch so a second UI click while a fetch is in
    progress queues another one rather than being silently swallowed.

    Also writes a heartbeat timestamp so the Streamlit UI can detect whether
    the standalone scheduler daemon is running (vs. the in-process scheduler).
    """
    try:
        set_metadata("scheduler_heartbeat", datetime.utcnow().isoformat())
    except Exception:
        pass

    config = _load_config()

    if get_metadata_str("pending_t212_fetch"):
        set_metadata("pending_t212_fetch", "")
        logger.info("Dispatching pending T212 snapshot fetch (UI-requested)…")
        _fetch_t212_snapshot(config)


def _run_analysis_pipeline(
    config: dict,
    all_tickers: list[str],
    run_type: str,
) -> None:
    """Execute the full analysis pipeline and stamp metadata."""
    try:
        run_analysis(tickers=all_tickers, config=config)
    except Exception as exc:
        logger.error("run_analysis failed: %s", exc, exc_info=True)

    try:
        generate_signals(tickers=all_tickers, config=config)
    except Exception as exc:
        logger.error("generate_signals failed: %s", exc, exc_info=True)

    try:
        set_metadata("last_run_type", run_type)
    except Exception as exc:
        logger.warning("Could not stamp last_run_type: %s", exc)

    try:
        from brokers.trading212 import maybe_place_t212_orders  # noqa: PLC0415
        maybe_place_t212_orders(config, run_type)
    except Exception as exc:
        logger.error("T212 Invest order placement failed: %s", exc, exc_info=True)

    try:
        from brokers.trading212 import maybe_place_stock_orders  # noqa: PLC0415
        maybe_place_stock_orders(config, run_type)
    except Exception as exc:
        logger.error("T212 Stocks order placement failed: %s", exc, exc_info=True)

    logger.info("Analysis pipeline complete (run_type=%s).", run_type)


def _pre_market_job() -> None:
    """07:00 UTC — pull fresh data for exchanges that just closed, then run analysis.

    Asian markets (JPX/Tokyo ~06:30, HKEX ~08:00) close before this run fires.
    For any exchange whose session ended before 07:30 UTC today, we pull
    incremental price data for those tickers. The 13:30 and 21:30 runs
    handle European and US markets respectively.
    """
    logger.info("Scheduled pre-market analysis starting (07:00 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "Pre-Market")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)
    config = _load_config()
    ticker_exchange = _build_ticker_exchange_map(config)

    now_utc = datetime.now(timezone.utc)
    # Allow a 30-min grace window — catches exchanges that close at/around run time
    cutoff_utc = now_utc

    unique_exchanges = set(ticker_exchange.values())
    recently_closed = _exchanges_closed_before(unique_exchanges, cutoff_utc)

    if recently_closed:
        closed_tickers = [t for t, e in ticker_exchange.items() if e in recently_closed]
        logger.info(
            "Pre-market: exchanges closed before %s UTC — %s (%d tickers). Pulling fresh data.",
            now_utc.strftime("%H:%M"), recently_closed, len(closed_tickers),
        )
        try:
            results = pull_incremental(tickers=closed_tickers)
            logger.info(
                "Pre-market data pull complete — %d new rows.", sum(results.values())
            )
        except Exception as exc:
            logger.error("Pre-market incremental pull failed: %s", exc, exc_info=True)

    _run_analysis_pipeline(
        config,
        get_all_tickers(config),
        run_type=RUN_ANALYSIS_ONLY,
    )


def _cancel_stale_weekend_orders(config: dict) -> int:
    """Cancel any PLACED BUY order in t212_invest_orders older than _STALE_ORDER_HOURS.

    The Friday 21:30 UTC EOD run can place BUY orders that are still sitting in
    PLACED status when markets reopen Monday — T212 doesn't fill market orders
    outside trading hours, so a Friday-evening order can sit unfilled all
    weekend. A lot can change over a weekend (geopolitical events, Sunday
    futures pricing, macro news), so an order that made sense Friday afternoon
    may no longer reflect a signal worth acting on by Monday.

    Cancelled orders are re-evaluated fresh by the analysis pipeline that
    _monday_premarket_job() runs right after this — a ticker still showing
    STRONG_BUY in TRENDING_BULL gets a brand-new order; one whose signal
    weakened over the weekend does not.

    Covers both automation paths (ETF and stock orders share t212_invest_orders)
    — a stale order is a stale order regardless of which path placed it. No-op
    (returns 0) if T212 credentials are not configured. Never raises — a
    failure to cancel one order is logged and the rest still get a chance;
    matches the "never raises" contract the rest of brokers/trading212.py's
    automation functions follow.

    Returns the number of orders cancelled.
    """
    if not (os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET")):
        return 0

    from sqlalchemy import text as sa_text  # noqa: PLC0415
    from data.database import get_engine  # noqa: PLC0415

    cutoff = datetime.utcnow() - timedelta(hours=_STALE_ORDER_HOURS)
    engine = get_engine()

    # SQLAlchemy's SQLite DateTime dialect stores "YYYY-MM-DD HH:MM:SS.ffffff"
    # (space-separated). datetime.isoformat() produces "YYYY-MM-DDTHH:MM:SS.ffffff"
    # (T-separated) — SQLite compares these as plain strings, and ' ' (0x20) sorts
    # before 'T' (0x54), so any placed_at on the SAME calendar date as cutoff would
    # lexicographically compare as "less than" regardless of actual clock time,
    # incorrectly flagging same-day orders younger than _STALE_ORDER_HOURS as stale.
    # strftime with the matching format avoids the mismatch entirely.
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S.%f")

    try:
        with engine.connect() as conn:
            rows = conn.execute(sa_text("""
                SELECT id, order_id, ticker, placed_at
                FROM t212_invest_orders
                WHERE status = 'PLACED' AND direction = 'BUY' AND placed_at < :cutoff
            """), {"cutoff": cutoff_str}).mappings().all()
    except Exception as exc:
        logger.error("Monday pre-market: could not read t212_invest_orders: %s", exc)
        return 0

    if not rows:
        return 0

    live = config.get("trading212_invest", {}).get("live", False)
    from brokers.trading212 import Trading212Client  # noqa: PLC0415
    client = Trading212Client(live=live)

    cancelled = 0
    for row in rows:
        placed_raw = row["placed_at"]
        try:
            placed_dt = (
                placed_raw if isinstance(placed_raw, datetime)
                else datetime.fromisoformat(placed_raw)
            )
            age_hours = (datetime.utcnow() - placed_dt).total_seconds() / 3600
        except Exception:
            age_hours = -1.0

        logger.warning(
            "Monday pre-market: cancelling stale weekend order for %s (placed %.1f hours ago).",
            row["ticker"], age_hours,
        )
        try:
            if row["order_id"]:
                client.cancel_order(row["order_id"])
            with engine.begin() as upd_conn:
                upd_conn.execute(sa_text("""
                    UPDATE t212_invest_orders
                    SET status = 'CANCELLED', cancelled_at = :now
                    WHERE id = :row_id
                """), {"now": datetime.utcnow().isoformat(), "row_id": row["id"]})
            cancelled += 1
        except Exception as exc:
            logger.error(
                "Monday pre-market: failed to cancel order id=%s (%s): %s",
                row["id"], row["ticker"], exc,
            )

    return cancelled


def _monday_premarket_job() -> None:
    """07:00 UTC every Monday — cancel stale weekend BUY orders, then re-run
    analysis so entry/exit decisions are fresh before markets open.

    Registered separately from _pre_market_job() (which fires Tuesday-Friday
    only — see start_scheduler()) because Monday needs the extra stale-order
    cancellation step first. Unlike _pre_market_job(), this does NOT pull
    fresh Asian-market data — the point of this run is to re-evaluate signals
    against Friday's closing prices before deciding whether Friday's
    still-open BUY orders deserve to stand, not to ingest new data (prices
    haven't changed since Friday close for anything this job needs to act on).

    maybe_place_t212_orders() / maybe_place_stock_orders() and
    maybe_exit_t212_positions() / maybe_exit_stock_positions() all fire as
    part of _run_analysis_pipeline() below (same as every other analysis
    run) — a ticker still showing STRONG_BUY in TRENDING_BULL after
    cancellation gets a fresh order; a weakened signal does not get a
    replacement, and any position whose signal now calls for exit is closed.
    """
    logger.info("Scheduled Monday pre-market job starting (07:00 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "Monday Pre-Market")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)

    config = _load_config()

    try:
        n_cancelled = _cancel_stale_weekend_orders(config)
        if n_cancelled:
            logger.info("Monday pre-market: cancelled %d stale weekend order(s).", n_cancelled)
    except Exception as exc:
        logger.error("Monday pre-market: stale order cancellation failed: %s", exc, exc_info=True)

    _run_analysis_pipeline(config, get_all_tickers(config), run_type=RUN_ANALYSIS_ONLY)


def _us_open_job() -> None:
    """13:30 UTC — analysis only using most recently stored price data.

    European markets are mid-session; US is about to open. No data pull —
    requires a complete session close.
    """
    logger.info("Scheduled US-open analysis starting (13:30 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "US Open")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)


def _eod_job() -> None:
    """21:30 UTC — pull incremental data for open exchanges, then run full analysis."""
    logger.info("Scheduled end-of-day refresh starting (21:30 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "End of Day")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)
    config = _load_config()
    ticker_exchange = _build_ticker_exchange_map(config)

    today_utc: date = datetime.now(timezone.utc).date()
    unique_exchanges = set(ticker_exchange.values())
    open_exchanges: dict[str, bool] = {
        exch: _is_exchange_open(exch, today_utc)
        for exch in unique_exchanges
    }

    open_tickers:    list[str] = []
    skipped_tickers: list[str] = []
    for ticker, exchange in ticker_exchange.items():
        if ticker in _ALWAYS_PULL or exchange in _ALWAYS_OPEN_EXCHANGES or open_exchanges[exchange]:
            open_tickers.append(ticker)
        else:
            logger.info("Skipping %s — %s closed today (%s).", ticker, exchange, today_utc)
            skipped_tickers.append(ticker)

    if not open_tickers:
        logger.info("All markets closed today (%s) — skipping data pull.", today_utc)
        _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)
        return

    logger.info(
        "EOD refresh — pulling %d assets (%d skipped, market closed).",
        len(open_tickers), len(skipped_tickers),
    )
    results = pull_incremental(tickers=open_tickers)
    total_rows = sum(results.values())
    logger.info("Data pull complete — %d new rows across %d assets.", total_rows, len(open_tickers))

    try:
        set_metadata("last_full_refresh", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Could not stamp last_full_refresh: %s", exc)

    try:
        set_metadata("last_pull_rows", str(total_rows))
        set_metadata("last_pull_ticker_count", str(len([v for v in results.values() if v > 0])))
    except Exception as exc:
        logger.warning("Could not stamp last_pull_rows: %s", exc)

    try:
        logger.info("Fetching FRED macro indicators…")
        fetch_and_store_macro_indicators(config)
        set_metadata("last_fred_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.error("FRED macro indicator fetch failed: %s", exc, exc_info=True)

    try:
        logger.info("Refreshing FRED release calendars…")
        refresh_release_calendars(config)
        set_metadata("last_fred_release_calendar_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.error("FRED release-calendar refresh failed: %s", exc, exc_info=True)

    _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("T212 snapshot fetch after EOD failed: %s", exc)


def _trade_alert_job() -> None:
    """Hourly (00:00–21:00 + 23:00 UTC, weekdays) — VIX spike check + T212 upkeep.

    Runs unconditionally on every firing:
      - VIX spike check: on a >20% intraday VIX jump vs. the previous close,
        runs an immediate analysis-only pipeline and sends a spike alert email
        (once per calendar day).
      - T212 order-status polling (`poll_placed_orders()`) and account/positions
        snapshot refresh (`_fetch_t212_snapshot()`), so the Status page snapshot
        stays current.
      - The T212 Invest exit check — `brokers.trading212.maybe_exit_t212_positions()`
        / `maybe_exit_stock_positions()` — so a confirmed EXIT SIGNAL or backstop
        time-limit exit closes within about an hour rather than waiting for the
        next EOD run (see "Broker integration — Trading212 Invest" in CLAUDE.md
        for the exit trigger priority and the 24h duplicate-SELL guard shared
        with the EOD run's own delegated call to the same functions).

    Manually-logged CFD trade alerting (STOP LOSS / EXIT SIGNAL emails, and the
    price-level SL/TP proximity check) was removed along with the Trade Log
    page — see "Removed: manual CFD trade logging" in CLAUDE.md. Any CFD trade
    rows already in the `trades` table are no longer monitored or alerted on.
    """
    logger.info("Trade alert check starting…")
    config = _load_config()

    # ── VIX spike check ────────────────────────────────────────────────────
    # Fire once per calendar day when VIX rises >20% vs the previous close.
    # Triggers an immediate analysis pipeline run and sends a spike alert email.
    try:
        from data.ingestion import get_price_df as _gpdf  # noqa: PLC0415
        _vix_df = _gpdf("^VIX", days=5)
        if _vix_df is not None and len(_vix_df) >= 2:
            _vix_prev  = float(_vix_df["close"].iloc[-2])
            _vix_curr  = float(_vix_df["close"].iloc[-1])
            if _vix_prev > 0:
                _vix_chg_pct = (_vix_curr - _vix_prev) / _vix_prev * 100
                if _vix_chg_pct >= 20.0:
                    _today_str = date.today().isoformat()
                    _spike_key = "last_vix_spike_alert"
                    _last_spike = get_metadata_str(_spike_key) or ""
                    if _last_spike != _today_str:
                        logger.warning(
                            "VIX spike detected: %.2f → %.2f (+%.1f%%) — "
                            "running immediate analysis.",
                            _vix_prev, _vix_curr, _vix_chg_pct,
                        )
                        # Stamp immediately so repeated hourly firings don't
                        # trigger another analysis run even if the email fails.
                        set_metadata(_spike_key, _today_str)
                        _run_analysis_pipeline(
                            config, get_all_tickers(config), RUN_ANALYSIS_ONLY
                        )
                        from output.briefing import send_vix_spike_alert  # noqa: PLC0415
                        _ok, _msg = send_vix_spike_alert(
                            _vix_chg_pct, _vix_curr, _vix_prev, config
                        )
                        logger.info("VIX spike alert email: %s — %s", _ok, _msg)
    except Exception as _vix_exc:
        logger.warning("VIX spike check failed: %s", _vix_exc)
    # ── End VIX spike check ────────────────────────────────────────────────

    # ── T212 Invest: poll orders + refresh account snapshot ───────────────
    # These run unconditionally every hour regardless of alert state, so the
    # Status page snapshot stays current even when there are no new alerts.
    try:
        from brokers.trading212 import poll_placed_orders as _poll_t212  # noqa: PLC0415
        _n_updated = _poll_t212(config)
        if _n_updated:
            logger.info("T212 order poll: %d order(s) updated to terminal status.", _n_updated)
    except Exception as _poll_exc:
        logger.warning("T212 order poll failed: %s", _poll_exc)

    # Exit checks run hourly so a confirmed EXIT SIGNAL closes within ~1h
    # rather than waiting up to 24h for the next EOD run. The EOD pipeline
    # calls the same two functions via maybe_place_t212_orders() /
    # maybe_place_stock_orders() — the 24h duplicate-SELL guard inside each
    # (_has_recent_sell_order()) keeps the two calls from both selling the
    # same position.
    try:
        from brokers.trading212 import maybe_exit_t212_positions  # noqa: PLC0415
        maybe_exit_t212_positions(config)
    except Exception as exc:
        logger.error("T212 Invest hourly exit check failed: %s", exc, exc_info=True)

    try:
        from brokers.trading212 import maybe_exit_stock_positions  # noqa: PLC0415
        maybe_exit_stock_positions(config)
    except Exception as exc:
        logger.error("T212 Stocks hourly exit check failed: %s", exc, exc_info=True)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("Hourly T212 snapshot refresh failed: %s", exc)
    # ── End T212 ───────────────────────────────────────────────────────────


def _weekly_summary_job() -> None:
    """Every Sunday at 10:00 UTC — send the weekly performance summary email."""
    logger.info("Weekly summary job starting…")
    config = _load_config()
    from output.briefing import send_weekly_summary_email  # noqa: PLC0415
    ok, msg = send_weekly_summary_email(config)
    logger.info("Weekly summary: %s — %s", "sent" if ok else "failed", msg)


def _fomc_reaction_job() -> None:
    """19:05 UTC weekdays — run analysis-only if today is an FOMC decision day.

    FOMC statements are released at 2:00 PM US/Eastern (18:00–19:00 UTC).
    This job fires at 19:05 UTC, captures the immediate post-decision market
    reaction before the regular 21:30 EOD run, and refreshes signals so the
    Daily Briefing reflects any regime shift triggered by the Fed announcement.
    Silently exits on non-FOMC days so it adds no cost on normal weekdays.
    """
    from analysis.fomc import is_fomc_decision_day  # noqa: PLC0415

    if not is_fomc_decision_day():
        logger.debug("FOMC reaction check: not an FOMC decision day — skipping.")
        return

    logger.info("FOMC decision day — running post-decision analysis (19:05 UTC)…")
    config = _load_config()
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_ANALYSIS_ONLY)
    logger.info("FOMC reaction analysis complete.")


def _check_missed_eod_run(config: dict) -> None:
    """Trigger an immediate EOD catch-up if today's 21:30 UTC EOD run was missed.

    Called at scheduler startup, after all jobs are registered.  Both
    conditions below must hold before a catch-up fires:

    1. last_full_refresh is more than 20 hours old. A run from earlier the
       same day (e.g. a manual "Refresh Data" click, or the real EOD job
       having already fired minutes ago) does not count as missed — only a
       genuinely stale refresh does.
    2. Current UTC time is between 21:45 and 23:59 — the post-EOD window.
       21:45 gives the real 21:30 cron trigger a 15-minute grace period
       before concluding it didn't fire. Outside this window we skip
       silently: a restart at, say, 08:00 UTC has no reason to believe the
       EOD job was missed, and the next scheduled job (07:00 pre-market or
       tomorrow's 21:30 EOD) will naturally pick up stale data anyway.

    If last_full_refresh has never been recorded (fresh install, no data
    yet), skip silently — there's nothing to catch up on.

    The Saturday/Sunday guard is preserved from the previous version: no EOD
    job is ever scheduled on a weekend, so a stale last_full_refresh there
    is expected, not missed.

    NOTE: this replaces the previous _maybe_catch_up_missed_eod, which used a
    26-hour staleness threshold OR'd with an unbounded "past today's EOD
    time" check (anywhere from 21:30 UTC through the next morning could
    re-trigger it) and re-ran the full _eod_job (incremental data pull +
    FRED fetch + T212 snapshot). Deployments were landing inside that same
    window and racing the live EOD job — see "Deployment timing" in
    CLAUDE.md. The narrower 20h/21:45-23:59 AND-gate makes catch-up fire
    deterministically within one bounded window instead of however long the
    service happens to stay down, and it now runs _run_analysis_pipeline()
    directly (analysis + signals + order logic) rather than re-pulling
    data — a same-day restart in this window means the pipeline ran on a
    transitional deploy, not that prices are missing.

    The catch-up runs in a daemon thread so start_scheduler() returns promptly.
    """
    now_utc = datetime.now(timezone.utc)

    if now_utc.weekday() >= 5:  # Saturday (5) or Sunday (6) — no scheduled EOD
        return

    last_refresh_str = get_metadata_str("last_full_refresh")
    if last_refresh_str is None:
        logger.debug(
            "Missed-EOD check: no last_full_refresh recorded yet — "
            "startup init handles the first analysis run."
        )
        return

    try:
        last_dt = datetime.fromisoformat(last_refresh_str)
    except ValueError:
        logger.warning(
            "Missed-EOD check: could not parse last_full_refresh '%s' — skipping.",
            last_refresh_str,
        )
        return

    now_naive  = now_utc.replace(tzinfo=None)
    last_naive = last_dt.replace(tzinfo=None) if last_dt.tzinfo else last_dt
    hours_since = (now_naive - last_naive).total_seconds() / 3600

    window_start = now_naive.replace(hour=21, minute=45, second=0, microsecond=0)
    window_end   = now_naive.replace(hour=23, minute=59, second=59, microsecond=999999)
    if not (window_start <= now_naive <= window_end):
        logger.debug(
            "Missed-EOD check: outside 21:45-23:59 UTC catch-up window (now %s UTC) — "
            "next scheduled job will catch up naturally.",
            now_naive.strftime("%H:%M"),
        )
        return

    if hours_since <= 20:
        logger.debug(
            "Missed-EOD check: last refresh %.1fh ago (<=20h threshold) — "
            "no catch-up needed.", hours_since,
        )
        return

    logger.warning("Missed EOD run detected — running catch-up pipeline now")
    set_metadata("missed_eod_catch_up", now_utc.isoformat())

    import threading  # noqa: PLC0415
    t = threading.Thread(
        target=_run_analysis_pipeline,
        args=(config, get_all_tickers(config), RUN_FULL),
        name="eod-catch-up",
        daemon=True,
    )
    t.start()


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler with all three daily jobs if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    config    = _load_config()
    sched_cfg = config["scheduler"]
    tz        = sched_cfg["timezone"]

    _scheduler = BackgroundScheduler(timezone=tz)

    # Suppress APScheduler's per-job "Running job / executed successfully" INFO spam.
    # Errors and warnings from job functions still surface via the root logger.
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

    _JOB_FNS = {
        "pre_market": _pre_market_job,
        "us_open":    _us_open_job,
        "eod":        _eod_job,
    }

    # pre_market fires Tue-Fri only — Monday morning is handled by the separate
    # monday_premarket job below, which adds stale weekend-order cancellation
    # before running the same analysis-only pipeline. Every other job below
    # still fires mon-fri.
    _JOB_DAY_OVERRIDES = {"pre_market": "tue-fri"}

    for run_name, run_cfg in sched_cfg["runs"].items():
        job_fn = _JOB_FNS.get(run_name)
        if job_fn is None:
            logger.warning("Unknown scheduler run name '%s' — skipping.", run_name)
            continue
        day_of_week = _JOB_DAY_OVERRIDES.get(run_name, "mon-fri")
        _scheduler.add_job(
            job_fn,
            trigger=CronTrigger(
                hour=run_cfg["hour"],
                minute=run_cfg["minute"],
                day_of_week=day_of_week,
                timezone=tz,
            ),
            id=f"scheduled_{run_name}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled '%s' job at %02d:%02d %s (%s).",
            run_cfg.get("label", run_name), run_cfg["hour"], run_cfg["minute"], tz, day_of_week,
        )

    # Monday-only pre-market job: cancels stale weekend BUY orders (Friday EOD
    # orders that never filled over the weekend) before re-running the same
    # analysis-only pipeline the regular pre-market job runs Tue-Fri. Hour/minute
    # follow the same config.yaml scheduler.runs.pre_market slot so both jobs
    # always fire at the same time of day even if that's ever retimed.
    _pre_market_cfg = sched_cfg["runs"].get("pre_market", {})
    _scheduler.add_job(
        _monday_premarket_job,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=_pre_market_cfg.get("hour", 7),
            minute=_pre_market_cfg.get("minute", 0),
            timezone=tz,
        ),
        id="monday_premarket",
        name="Monday Pre-Market",
        replace_existing=True,
    )
    logger.info(
        "Scheduled Monday pre-market job (stale-order cancellation + analysis) at %02d:%02d %s.",
        _pre_market_cfg.get("hour", 7), _pre_market_cfg.get("minute", 0), tz,
    )

    # 60-second dispatcher: executes pending UI-requested jobs (e.g. T212 refresh).
    _scheduler.add_job(
        _dispatch_pending_jobs,
        trigger=IntervalTrigger(seconds=60),
        id="job_dispatcher",
        replace_existing=True,
    )
    logger.info("Scheduled 60-second job dispatcher.")

    # Hourly trade alert check across all major market sessions (weekdays only).
    # Hour range covers: Asian (00-08 UTC), European (08-17 UTC), US (14-21 UTC),
    # plus 23:00 UTC for Sydney/Auckland pre-market.
    # The job itself filters per-trade by exchange session, so only relevant alerts
    # fire at each hour — no spurious alerts outside market hours.
    _scheduler.add_job(
        _trade_alert_job,
        trigger=CronTrigger(
            hour="0-21,23",
            minute=0,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id="trade_alert_check",
        replace_existing=True,
    )
    logger.info(
        "Scheduled trade alert check hourly 00:00–21:00 + 23:00 %s on weekdays "
        "(filtered per-trade by home exchange session).", tz,
    )

    _scheduler.add_job(
        _weekly_summary_job,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=10,
            minute=0,
            timezone=tz,
        ),
        id="weekly_summary",
        replace_existing=True,
    )
    logger.info("Scheduled weekly summary email every Sunday 10:00 %s.", tz)

    # FOMC reaction run: fires every weekday at 19:05 UTC but executes the
    # analysis pipeline only on days matching the hardcoded FOMC decision schedule.
    _scheduler.add_job(
        _fomc_reaction_job,
        trigger=CronTrigger(
            hour=19,
            minute=5,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id="fomc_reaction",
        replace_existing=True,
    )
    logger.info(
        "Scheduled FOMC reaction analysis at 19:05 %s weekdays "
        "(active on FOMC decision days only).", tz,
    )

    _scheduler.start()
    logger.info("Scheduler started with %d jobs.", len(_scheduler.get_jobs()))

    # Write an immediate heartbeat so the Streamlit UI detects a running
    # scheduler straight away (the dispatcher job writes subsequent heartbeats
    # every 60 seconds, but takes up to 60s to fire for the first time).
    try:
        set_metadata("scheduler_heartbeat", datetime.utcnow().isoformat())
    except Exception:
        pass

    # Check for missed EOD runs after starting, so the scheduler is already
    # accepting jobs before we potentially queue a catch-up run.
    _check_missed_eod_run(config)

    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None
