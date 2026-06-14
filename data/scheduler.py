"""APScheduler-based daily data refresh job with per-exchange holiday awareness."""

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from data.ingestion import pull_incremental

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _build_ticker_exchange_map(config: dict) -> dict[str, str]:
    """Return {ticker: exchange_calendar_name} for every watchlist entry.

    Falls back to "NYSE" for any entry that lacks an exchange field so that
    existing config files without the field continue to work.
    """
    mapping: dict[str, str] = {}
    for group in config["watchlist"].values():
        for entry in group:
            mapping[entry["ticker"]] = entry.get("exchange", "NYSE")
    return mapping


def _is_exchange_open(calendar_name: str, check_date: date) -> bool:
    """Return True if *calendar_name* has a trading session on *check_date*.

    Uses the UTC date supplied by the caller so the result is independent of
    the server's local timezone. Fails open (returns True) if the calendar
    lookup raises an exception — better to attempt a fetch than to silently
    skip on a misconfigured name.
    """
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


def _daily_refresh_job() -> None:
    """Triggered by the scheduler; skips assets whose exchange is closed today."""
    config = _load_config()
    ticker_exchange = _build_ticker_exchange_map(config)

    # Use UTC date explicitly to avoid server-timezone/midnight-boundary issues.
    # The job fires at 18:30 ET (22:30–23:30 UTC), which is always the same
    # calendar day as the markets' trading day.
    today_utc: date = datetime.now(timezone.utc).date()

    # Check each unique exchange once, then fan out to tickers.
    unique_exchanges = set(ticker_exchange.values())
    open_exchanges: dict[str, bool] = {
        exch: _is_exchange_open(exch, today_utc)
        for exch in unique_exchanges
    }

    open_tickers: list[str] = []
    skipped_tickers: list[str] = []

    for ticker, exchange in ticker_exchange.items():
        if open_exchanges[exchange]:
            open_tickers.append(ticker)
        else:
            logger.info(
                "Skipping %s — %s closed today (%s)", ticker, exchange, today_utc
            )
            skipped_tickers.append(ticker)

    if not open_tickers:
        logger.info("All markets closed today (%s), no data fetched.", today_utc)
        return

    logger.info(
        "Daily refresh starting — %d assets to fetch, %d skipped (market closed).",
        len(open_tickers), len(skipped_tickers),
    )
    results = pull_incremental(tickers=open_tickers)
    total_rows = sum(results.values())

    logger.info(
        "Refresh complete — fetched %d assets (%d new rows), skipped %d assets.",
        len(open_tickers), total_rows, len(skipped_tickers),
    )


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler if it is not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    config = _load_config()
    sched_cfg = config["scheduler"]

    _scheduler = BackgroundScheduler(timezone=sched_cfg["timezone"])
    _scheduler.add_job(
        _daily_refresh_job,
        trigger=CronTrigger(
            hour=sched_cfg["hour"],
            minute=sched_cfg["minute"],
            day_of_week="mon-fri",
            timezone=sched_cfg["timezone"],
        ),
        id="daily_refresh",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started. Daily refresh at %02d:%02d %s on weekdays.",
        sched_cfg["hour"],
        sched_cfg["minute"],
        sched_cfg["timezone"],
    )
    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None
