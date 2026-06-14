"""APScheduler-based daily data refresh job."""

import logging
from pathlib import Path

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


def _daily_refresh_job() -> None:
    """Triggered by the scheduler to pull incremental price data."""
    logger.info("Scheduled daily refresh starting…")
    results = pull_incremental()
    total_rows = sum(results.values())
    logger.info("Daily refresh complete. %d new rows across %d tickers.", total_rows, len(results))


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler if it is not already running.

    Returns the scheduler instance so the caller can shut it down if needed.
    """
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
