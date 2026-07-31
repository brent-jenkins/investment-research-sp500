"""APScheduler-based scheduled jobs: daily runs with per-exchange holiday awareness.

Run schedule (UTC):
  07:00  Monday pre-market (Mon only) — cancel stale weekend BUY orders left PLACED
                       from Friday's EOD run, then run the order-logic-only pipeline
                       so Friday's still-open orders are re-evaluated before markets
                       reopen (see _monday_premarket_job())
  21:15  Membership refresh — refresh point-in-time index-membership windows
                       15 minutes ahead of the EOD job's own trading decisions
  21:30  End of day  — full data refresh, then place/exit orders (all exchanges)
  00-21,23  Hourly   — T212 order-status poll, exit check, account snapshot refresh

The 07:00 (Tue-Fri) pre-market and 13:30 US-open "analysis only" runs that
used to sit between Monday's job and the EOD run were removed 2026-07-31 —
confirmed genuinely redundant, not just assumed. Both ever did was call
maybe_exit_positions() (no BUY phase — analysis_only always skips it, and
in the Tue-Fri pre-market job's case, its own data pull for "exchanges that
just closed" had already become a permanent no-op: the current V2 universe
is exclusively NYSE/FX-listed instruments, and NYSE never satisfies "closed
before 07:00 UTC" — confirmed directly by calling
_exchanges_closed_before() against the live config). The hourly
trade_alert_check job (below) already calls maybe_exit_positions() at the
exact same 07:00 minute and again at 13:00/14:00 either side of 13:30 — so
neither run added anything the hourly cadence didn't already cover. See
CLAUDE.md's "Pre-market/US-open scheduled runs removed" section for the
full investigation.

Every scheduled trading run calls brokers/trading212.py:maybe_place_orders()
(which itself calls maybe_exit_positions() first) — there is no separate
"analysis pipeline" step: momentum signal, index membership, and the crisis
circuit breaker are all computed on demand, straight from price_data, by
brokers/trading212.py's own _ticker_state() at order-decision time.

Metadata keys written to system_metadata:
  last_full_refresh          — UTC timestamp of the last successful data pull (EOD run or manual)
  last_run_type               — 'full' or 'analysis_only' — which pipeline variant last ran
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
    """Run the momentum + crisis-override order logic for every scheduled slot.

    "all_tickers" is accepted for call-site symmetry with the previous
    six-module pipeline (which needed a ticker list to analyse), but the
    momentum signal, index membership, and crisis check are all computed
    per-ticker on demand inside maybe_place_orders()/_ticker_state() from
    the top-level `instruments:` config.yaml section — this parameter is
    unused here.
    """
    try:
        set_metadata("last_run_type", run_type)
    except Exception as exc:
        logger.warning("Could not stamp last_run_type: %s", exc)

    try:
        from brokers.trading212 import maybe_place_orders  # noqa: PLC0415
        maybe_place_orders(config, run_type)
    except Exception as exc:
        logger.error("Order placement failed: %s", exc, exc_info=True)

    logger.info("Order pipeline complete (run_type=%s).", run_type)


def _cancel_stale_weekend_orders(config: dict) -> int:
    """Cancel any PLACED BUY order in invest_orders older than _STALE_ORDER_HOURS.

    The Friday 21:30 UTC EOD run can place BUY orders that are still sitting in
    PLACED status when markets reopen Monday — T212 doesn't fill market orders
    outside trading hours, so a Friday-evening order can sit unfilled all
    weekend. A lot can change over a weekend (geopolitical events, Sunday
    futures pricing, macro news), so an order that made sense Friday afternoon
    may no longer reflect a signal worth acting on by Monday.

    Cancelled orders are re-evaluated fresh by the order pipeline that
    _monday_premarket_job() runs right after this — a ticker whose momentum
    signal is still on gets a brand-new order; one whose signal turned off
    over the weekend does not.

    Never raises — a failure to cancel one order is logged and the rest
    still get a chance; matches the "never raises" contract the rest of
    brokers/trading212.py's automation functions follow.

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
                FROM invest_orders
                WHERE status = 'PLACED' AND direction = 'BUY' AND placed_at < :cutoff
            """), {"cutoff": cutoff_str}).mappings().all()
    except Exception as exc:
        logger.error("Monday pre-market: could not read invest_orders: %s", exc)
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
                    UPDATE invest_orders
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
    the order pipeline so entry/exit decisions are fresh before markets open.

    Registered separately from _pre_market_job() (which fires Tuesday-Friday
    only — see start_scheduler()) because Monday needs the extra stale-order
    cancellation step first. Unlike _pre_market_job(), this does NOT pull
    fresh Asian-market data — the point of this run is to re-evaluate signals
    against Friday's closing prices before deciding whether Friday's
    still-open BUY orders deserve to stand, not to ingest new data (prices
    haven't changed since Friday close for anything this job needs to act on).

    maybe_place_orders() (which calls maybe_exit_positions() first) fires as
    part of _run_analysis_pipeline() below (same as every other run) — a
    ticker whose momentum signal is still on after cancellation gets a fresh
    order; a signal that turned off does not get a replacement, and any
    position whose signal now calls for exit is closed.
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


def _eod_job() -> None:
    """21:30 UTC — pull incremental data for open exchanges, then run the full order pipeline."""
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

    _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("T212 snapshot fetch after EOD failed: %s", exc)


def _trade_alert_job() -> None:
    """Hourly (00:00–21:00 + 23:00 UTC, weekdays) — T212 order-status poll,
    exit check, and account snapshot refresh.

    Runs unconditionally on every firing:
      - T212 order-status polling (`poll_placed_orders()`) and account/positions
        snapshot refresh (`_fetch_t212_snapshot()`), so the Status page snapshot
        stays current.
      - The exit check — `brokers.trading212.maybe_exit_positions()` — so a
        crisis exit, index removal, stop loss, signal-off, or max-holding exit
        closes within about an hour rather than waiting for the next full
        (07:00/13:30/21:30) run.
    """
    logger.info("Trade alert check starting…")
    config = _load_config()

    try:
        from brokers.trading212 import poll_placed_orders  # noqa: PLC0415
        _n_updated = poll_placed_orders(config)
        if _n_updated:
            logger.info("T212 order poll: %d order(s) updated to terminal status.", _n_updated)
    except Exception as _poll_exc:
        logger.warning("T212 order poll failed: %s", _poll_exc)

    try:
        from brokers.trading212 import maybe_exit_positions  # noqa: PLC0415
        maybe_exit_positions(config)
    except Exception as exc:
        logger.error("Hourly exit check failed: %s", exc, exc_info=True)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("Hourly T212 snapshot refresh failed: %s", exc)


def _membership_refresh_job() -> None:
    """21:15 UTC weekdays — refresh point-in-time S&P 500 membership windows
    from source, 15 minutes before the 21:30 EOD job's live trading decisions
    run, so ticker eligibility is never computed against stale membership
    data (a ticker's real S&P 500 addition/removal must reach the
    eligibility check before it can affect that day's entries/exits).

    refresh_membership_from_source() is already internally "never raises"
    (per-ticker failures are caught and logged, not propagated) — the outer
    try/except here is an extra layer specifically because this job sits
    directly ahead of the EOD job in the schedule, and a refresh failure
    must never block that job from running.
    """
    from analysis.universe import refresh_membership_from_source  # noqa: PLC0415

    try:
        summary = refresh_membership_from_source()
        if summary.get("changed"):
            logger.info(
                "Membership refresh: %d ticker(s) changed — %s",
                len(summary["changed"]), [c["ticker"] for c in summary["changed"]],
            )
        if summary.get("errors"):
            logger.warning(
                "Membership refresh: %d error(s) — %s",
                len(summary["errors"]), summary["errors"],
            )
    except Exception as exc:
        logger.warning("Membership refresh job failed: %s", exc)


def _etf_order_submission_job() -> None:
    """07:00 UTC weekdays (LSE regular open) — submit queued ETF entry
    decisions from pending_orders.

    Signal computation and decision-queuing already happened at the prior
    EOD run (brokers.trading212:maybe_place_orders()); this job only submits
    whatever is still PENDING for ETFs, with its own price-sanity check
    against the price recorded at decide time. See CLAUDE.md's
    "Exchange-aware order submission" section — ETF submission ships
    disabled by default (trading212_invest.etf_submission_enabled) until
    manually verified, so this job is a no-op until that flag is set.
    """
    logger.info("ETF order submission job starting (07:00 UTC, LSE open)…")
    config = _load_config()
    try:
        from brokers.trading212 import submit_pending_etf_orders  # noqa: PLC0415
        n = submit_pending_etf_orders(config)
        if n:
            logger.info("ETF submission: %d order(s) placed.", n)
    except Exception as exc:
        logger.error("ETF order submission failed: %s", exc, exc_info=True)


def _stock_order_submission_job() -> None:
    """13:30 UTC weekdays (NYSE regular open) — submit queued stock entry
    decisions from pending_orders.

    Deliberately timed to the regular open rather than any after-hours
    window — see CLAUDE.md's "Exchange-aware order submission" section.
    """
    logger.info("Stock order submission job starting (13:30 UTC, NYSE open)…")
    config = _load_config()
    try:
        from brokers.trading212 import submit_pending_stock_orders  # noqa: PLC0415
        n = submit_pending_stock_orders(config)
        if n:
            logger.info("Stock submission: %d order(s) placed.", n)
    except Exception as exc:
        logger.error("Stock order submission failed: %s", exc, exc_info=True)


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

    The catch-up runs in a daemon thread so start_scheduler() returns promptly.
    """
    now_utc = datetime.now(timezone.utc)

    if now_utc.weekday() >= 5:  # Saturday (5) or Sunday (6) — no scheduled EOD
        return

    last_refresh_str = get_metadata_str("last_full_refresh")
    if last_refresh_str is None:
        logger.debug(
            "Missed-EOD check: no last_full_refresh recorded yet — "
            "startup init handles the first run."
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
    """Start the background scheduler with all scheduled jobs if not already running."""
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

    # Only "eod" remains in config.yaml's scheduler.runs — the 07:00 Tue-Fri
    # pre-market and 13:30 US-open "analysis only" jobs were removed
    # 2026-07-31 as confirmed-redundant (see this module's own docstring
    # above and CLAUDE.md's "Pre-market/US-open scheduled runs removed"
    # section). This loop is kept generic (rather than hardcoding "eod"
    # directly) so a future genuinely-new scheduled run only needs a
    # config.yaml entry + a _JOB_FNS mapping, not a change here too.
    _JOB_FNS = {
        "eod": _eod_job,
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
            "Scheduled '%s' job at %02d:%02d %s (mon-fri).",
            run_cfg.get("label", run_name), run_cfg["hour"], run_cfg["minute"], tz,
        )

    # Monday-only pre-market job: cancels stale weekend BUY orders (Friday EOD
    # orders that never filled over the weekend) before re-running the
    # order-logic-only pipeline. Fixed at 07:00 UTC directly — this no longer
    # tracks a config.yaml scheduler.runs.pre_market slot, since the Tue-Fri
    # sibling job that slot used to also drive was removed (see above); there
    # is still deliberately no separate scheduler.runs.monday_premarket
    # config key for this one weekly job.
    _scheduler.add_job(
        _monday_premarket_job,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=7,
            minute=0,
            timezone=tz,
        ),
        id="monday_premarket",
        name="Monday Pre-Market",
        replace_existing=True,
    )
    logger.info("Scheduled Monday pre-market job (stale-order cancellation + order logic) at 07:00 %s.", tz)

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
        "Scheduled trade alert check hourly 00:00–21:00 + 23:00 %s on weekdays.", tz,
    )

    # Exchange-aware order submission (2026-07-31) — see CLAUDE.md's
    # "Exchange-aware order submission" section. Decision-queuing happens at
    # EOD (unchanged, folded into maybe_place_orders() via the jobs above);
    # these two jobs submit whatever is still PENDING, each only for its own
    # asset class, during that exchange's own real trading session, rather
    # than everything firing at one fixed 21:30 UTC time regardless of
    # instrument.
    _scheduler.add_job(
        _etf_order_submission_job,
        trigger=CronTrigger(hour=7, minute=0, day_of_week="mon-fri", timezone=tz),
        id="etf_order_submission",
        replace_existing=True,
    )
    logger.info("Scheduled ETF order submission at 07:00 %s weekdays (LSE open).", tz)

    _scheduler.add_job(
        _stock_order_submission_job,
        trigger=CronTrigger(hour=13, minute=30, day_of_week="mon-fri", timezone=tz),
        id="stock_order_submission",
        replace_existing=True,
    )
    logger.info("Scheduled stock order submission at 13:30 %s weekdays (NYSE open).", tz)

    # Membership refresh — 15 minutes ahead of the EOD job's own 21:30 slot,
    # so any real S&P 500 addition/removal is reflected before that run's
    # trading decisions. Fixed at 21:15 rather than derived from
    # sched_cfg["runs"]["eod"] — unlike the Monday pre-market job (which
    # deliberately tracks the pre_market slot if it's ever retimed), this
    # job's only requirement is "shortly before EOD", not "exactly N
    # minutes before whatever EOD currently is".
    _scheduler.add_job(
        _membership_refresh_job,
        trigger=CronTrigger(
            hour=21,
            minute=15,
            day_of_week="mon-fri",
            timezone=tz,
        ),
        id="membership_refresh",
        replace_existing=True,
    )
    logger.info("Scheduled membership refresh at 21:15 %s weekdays.", tz)

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
