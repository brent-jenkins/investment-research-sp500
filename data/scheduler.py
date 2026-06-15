"""APScheduler-based scheduled jobs: three daily runs with per-exchange holiday awareness.

Run schedule (weekdays only, UTC):
  07:00  Pre-market  — analysis only, no data pull
  13:30  US open     — analysis only, no data pull
  21:30  End of day  — full data refresh then analysis (primary run)

All three runs execute the complete analysis pipeline:
  run_analysis()       → indicators table
  generate_signals()   → signals table  (also stamps last_analysis_run)
  compute_all_pairs()  → correlations table

Metadata keys written to system_metadata:
  last_full_refresh  — UTC timestamp of the last successful data pull (EOD run or manual)
  last_analysis_run  — UTC timestamp of the last completed analysis (any run type)
  last_run_type      — 'full' or 'analysis_only'
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
from data.database import set_metadata
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
    for group in config["watchlist"].values():
        for entry in group:
            mapping[entry["ticker"]] = entry.get("exchange", "NYSE")
    return mapping


def _is_exchange_open(calendar_name: str, check_date: date) -> bool:
    """Return True if *calendar_name* has a trading session on *check_date*.

    Fails open (returns True) on any calendar lookup error so assets are
    never silently skipped due to a misconfigured calendar name.
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
        if open_exchanges[exchange]:
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

    # Analyse all tickers (including those whose exchange was closed — their
    # stored history is still valid and their scores should remain up to date).
    _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)


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
