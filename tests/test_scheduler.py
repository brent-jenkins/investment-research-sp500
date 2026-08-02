"""Tests for data/scheduler.py.

Covers the missed-EOD catch-up check, the Monday pre-market stale-order
cancellation job, job registration (Monday-only pre-market, EOD,
membership refresh), and the simplified _run_analysis_pipeline() /
_trade_alert_job() bodies -- both of which now just delegate straight to
brokers/trading212.py's maybe_place_orders()/poll_placed_orders()/
maybe_exit_positions(), since there is no separate "analysis pipeline" step
in the momentum + crisis-override system (see data/scheduler.py's module
docstring).

The Tue-Fri 07:00 pre-market and 13:30 US-open "analysis only" jobs were
removed 2026-07-31 (confirmed genuinely redundant with the hourly
trade_alert_check job — see CLAUDE.md's "Pre-market/US-open scheduled runs
removed" section) — there is no `_pre_market_job()`/`_us_open_job()` left
to test.
"""

from datetime import datetime, timedelta, timezone

import pytest

import data.scheduler as scheduler

# Fixed "now" date used across tests — a Wednesday, so the weekday guard
# (no EOD job is ever scheduled on a weekend) never interferes.
_NOW_DATE = (2026, 7, 1)


class _ImmediateThread:
    """Stand-in for threading.Thread that runs its target synchronously,
    so tests can assert on the call without spinning up a real thread."""

    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _frozen_datetime(hour: int, minute: int = 0):
    """A datetime subclass whose .now() always returns the same fixed moment.

    Subclassing (rather than a bare mock) keeps fromisoformat(), replace(),
    and arithmetic working exactly as the real datetime module provides.
    """
    fixed = datetime(*_NOW_DATE, hour, minute, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz) if tz else fixed

    return _Frozen


def _frozen_utcnow(fixed: datetime):
    """A datetime subclass whose .utcnow() always returns *fixed*.

    Used to deterministically control the stale-order cutoff calculation in
    _cancel_stale_weekend_orders(), which is built entirely on datetime.utcnow().
    """
    class _Frozen(datetime):
        @classmethod
        def utcnow(cls):
            return fixed

    return _Frozen


def _patch_common(monkeypatch, *, now_hour, now_minute, last_refresh_hours_ago, pipeline_calls):
    monkeypatch.setattr(scheduler, "datetime", _frozen_datetime(now_hour, now_minute))

    if last_refresh_hours_ago is None:
        monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    else:
        now_naive = datetime(*_NOW_DATE, now_hour, now_minute)
        last_dt = now_naive - timedelta(hours=last_refresh_hours_ago)
        monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: last_dt.isoformat())

    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "get_all_tickers", lambda config: ["AAPL"])

    def fake_pipeline(config, tickers, run_type):
        pipeline_calls.append((config, tickers, run_type))

    monkeypatch.setattr(scheduler, "_run_analysis_pipeline", fake_pipeline)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)


# ---------------------------------------------------------------------------
# _build_ticker_exchange_map() (2026-08-02 fix) -- used to hand-roll its own
# merge over config.get("watchlist", {}) alone, covering only the 3
# reference tickers. _eod_job() below builds its actual price-pull ticker
# list from this map's own keys, so the bug meant the EOD job's real price
# pull was silently limited to ^GSPC/GBPUSD=X/EURUSD=X, never refreshing
# price data for any of the dynamic S&P 500 candidates
# get_live_stock_candidates() returns. Fixed by reusing
# data/ingestion.py:get_all_watchlist_entries() -- the same function
# get_all_tickers() already correctly delegates to.
# ---------------------------------------------------------------------------

def test_build_ticker_exchange_map_includes_dynamic_candidates(mocker):
    mocker.patch(
        "data.scheduler.get_all_watchlist_entries",
        return_value=[
            {"ticker": "^GSPC", "name": "S&P 500", "exchange": "NYSE"},
            {"ticker": "GBPUSD=X", "name": "GBP/USD", "exchange": "FX"},
            {"ticker": "AAPL", "name": "AAPL", "exchange": "NYSE"},
            {"ticker": "MSFT", "name": "MSFT", "exchange": "NYSE"},
        ],
    )

    mapping = scheduler._build_ticker_exchange_map({})

    assert mapping == {
        "^GSPC": "NYSE", "GBPUSD=X": "FX", "AAPL": "NYSE", "MSFT": "NYSE",
    }


def test_build_ticker_exchange_map_defaults_missing_exchange_to_nyse(mocker):
    mocker.patch(
        "data.scheduler.get_all_watchlist_entries",
        return_value=[{"ticker": "NEWCO", "name": "NEWCO"}],  # no "exchange" key
    )

    mapping = scheduler._build_ticker_exchange_map({})

    assert mapping == {"NEWCO": "NYSE"}


def test_missed_eod_detected(monkeypatch):
    """Stale last_full_refresh (25h ago) + current time inside the 21:45-23:59
    catch-up window -> the catch-up pipeline runs with run_type='full'."""
    calls: list = []
    _patch_common(
        monkeypatch, now_hour=22, now_minute=0,
        last_refresh_hours_ago=25, pipeline_calls=calls,
    )

    scheduler._check_missed_eod_run({"some": "config"})

    assert len(calls) == 1
    config, tickers, run_type = calls[0]
    assert tickers == ["AAPL"]
    assert run_type == scheduler.RUN_FULL


def test_missed_eod_not_triggered_outside_window(monkeypatch):
    """Same stale timestamp, but current time (08:00 UTC) is outside the
    21:45-23:59 catch-up window -> no catch-up fires."""
    calls: list = []
    _patch_common(
        monkeypatch, now_hour=8, now_minute=0,
        last_refresh_hours_ago=25, pipeline_calls=calls,
    )

    scheduler._check_missed_eod_run({"some": "config"})

    assert calls == []


def test_no_missed_eod_when_recent(monkeypatch):
    """last_full_refresh only 2h old -> even inside the catch-up window, no
    catch-up is needed (a run from earlier today already happened)."""
    calls: list = []
    _patch_common(
        monkeypatch, now_hour=22, now_minute=0,
        last_refresh_hours_ago=2, pipeline_calls=calls,
    )

    scheduler._check_missed_eod_run({"some": "config"})

    assert calls == []


def test_no_missed_eod_on_fresh_install(monkeypatch):
    """last_full_refresh has never been recorded (fresh install) -> skip
    silently, no catch-up and no error."""
    calls: list = []
    _patch_common(
        monkeypatch, now_hour=22, now_minute=0,
        last_refresh_hours_ago=None, pipeline_calls=calls,
    )

    scheduler._check_missed_eod_run({"some": "config"})

    assert calls == []


def test_missed_eod_not_triggered_on_market_holiday(monkeypatch):
    """2026-08-02 fix -- _check_missed_eod_run() used to guard only against
    Saturday/Sunday (a plain weekday() >= 5 check), not genuine market
    holidays. A service restart during the 21:45-23:59 UTC window on a
    real NYSE holiday, with last_full_refresh stale from the last real
    trading day (correctly stale, since _eod_job() itself now legitimately
    no-ops on a holiday), must NOT be misread as a "missed" EOD run and
    must NOT fire a real catch-up (which places live orders) against
    stale price data on a day the market is genuinely closed. Frozen to
    2026-11-26 (Thanksgiving, a Thursday -- weekday() == 3, so the OLD
    weekday-only guard would have let this through), 22:00 UTC (inside
    the catch-up window), last_full_refresh 25h stale (over the 20h
    threshold) -- every OTHER condition this function checks says "fire
    the catch-up"; only the new calendar gate should stop it."""
    calls: list = []
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 11, 26, hour=22, minute=0))
    last_dt = datetime(2026, 11, 26, 22, 0) - timedelta(hours=25)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: last_dt.isoformat())
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "get_all_tickers", lambda config: ["AAPL"])
    monkeypatch.setattr(
        scheduler, "_run_analysis_pipeline",
        lambda config, tickers, run_type: calls.append((config, tickers, run_type)),
    )
    monkeypatch.setattr("threading.Thread", _ImmediateThread)

    scheduler._check_missed_eod_run({"some": "config"})

    assert calls == []


# ---------------------------------------------------------------------------
# Monday pre-market job — stale weekend order cancellation + fresh order run.
#
# _monday_premarket_job() cancels any PLACED BUY order in invest_orders older
# than scheduler._STALE_ORDER_HOURS (30h — comfortably covers a Friday 21:30
# UTC order, ~33.5h old by Monday 07:00) before re-running the order pipeline.
# All DB reads/writes go through the db_engine fixture's in-memory SQLite
# database (tests/conftest.py); the Trading212 HTTP client is mocked entirely
# via pytest-mock so no real API call is ever made.
# ---------------------------------------------------------------------------

from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402

from data.database import InvestOrder  # noqa: E402


def _seed_order(engine, *, hours_old: float, order_id="t212-order-1", ticker="AAPL", status="PLACED"):
    with Session(engine) as session:
        session.add(InvestOrder(
            order_id=order_id,
            ticker=ticker,
            t212_ticker=ticker,
            direction="BUY",
            quantity=5.0,
            status=status,
            account_type="DEMO",
            placed_at=datetime.utcnow() - timedelta(hours=hours_old),
        ))
        session.commit()


def _monday_mocks(monkeypatch, mocker):
    """Fake T212 creds + a mocked Trading212Client, and a no-op _run_analysis_pipeline
    recorder so tests can assert on it without exercising the real pipeline."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")

    mock_client = mocker.MagicMock()
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)

    pipeline_calls: list = []
    monkeypatch.setattr(
        scheduler, "_run_analysis_pipeline",
        lambda config, tickers, run_type: pipeline_calls.append((config, tickers, run_type)),
    )
    monkeypatch.setattr(scheduler, "get_all_tickers", lambda config: ["AAPL"])
    monkeypatch.setattr(scheduler, "_load_config", lambda: {"trading212_invest": {"live": False}})
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    return mock_client, pipeline_calls


def test_monday_premarket_cancels_stale_order(db_engine, monkeypatch, mocker):
    """A PLACED BUY order older than the 30h threshold gets cancelled via the
    T212 API and its DB row flipped to CANCELLED."""
    mock_client, _ = _monday_mocks(monkeypatch, mocker)
    _seed_order(db_engine, hours_old=33.5, order_id="stale-order")

    scheduler._monday_premarket_job()

    mock_client.cancel_order.assert_called_once_with("stale-order")
    with Session(db_engine) as session:
        order = session.query(InvestOrder).filter_by(order_id="stale-order").one()
    assert order.status == "CANCELLED"
    assert order.cancelled_at is not None


def test_monday_premarket_does_not_cancel_recent_order(db_engine, monkeypatch, mocker):
    """A PLACED BUY order placed within the last 30h is left alone — it might
    still be a legitimate same-day order, not a weekend leftover."""
    mock_client, _ = _monday_mocks(monkeypatch, mocker)
    _seed_order(db_engine, hours_old=5, order_id="recent-order")

    scheduler._monday_premarket_job()

    mock_client.cancel_order.assert_not_called()
    with Session(db_engine) as session:
        order = session.query(InvestOrder).filter_by(order_id="recent-order").one()
    assert order.status == "PLACED"


def test_monday_premarket_cutoff_comparison_correct_when_dates_match(db_engine, monkeypatch, mocker):
    """Regression test: the cutoff string passed into the SQL WHERE clause must
    use the same 'YYYY-MM-DD HH:MM:SS.ffffff' (space-separated) format
    SQLAlchemy's SQLite dialect stores placed_at in, not datetime.isoformat()'s
    'T'-separated format. SQLite compares these as plain strings — a ' '
    (0x20) sorts before 'T' (0x54), so whenever an order's placed_at falls on
    the SAME calendar date as the cutoff, a T-separated cutoff string would
    make the order compare as "older" regardless of its actual clock time.

    Frozen "now" = 2026-07-06 07:00:00 -> cutoff = now - 30h = 2026-07-05
    01:00:00. An order placed at 2026-07-05 03:00:00 is only 28h old (younger
    than the 30h threshold, must NOT be cancelled) but shares cutoff's
    calendar date — exactly the condition that reproduces the bug.
    """
    mock_client, _ = _monday_mocks(monkeypatch, mocker)
    monkeypatch.setattr(scheduler, "datetime", _frozen_utcnow(datetime(2026, 7, 6, 7, 0, 0)))

    with Session(db_engine) as session:
        session.add(InvestOrder(
            order_id="same-day-not-stale",
            ticker="AAPL", t212_ticker="AAPL", direction="BUY", quantity=5.0,
            status="PLACED", account_type="DEMO",
            placed_at=datetime(2026, 7, 5, 3, 0, 0),  # 28h before frozen "now"
        ))
        session.commit()

    scheduler._monday_premarket_job()

    mock_client.cancel_order.assert_not_called()
    with Session(db_engine) as session:
        order = session.query(InvestOrder).filter_by(order_id="same-day-not-stale").one()
    assert order.status == "PLACED"


def test_monday_premarket_calls_order_pipeline_after_cancellation(db_engine, monkeypatch, mocker):
    """After stale-order cancellation, the order-logic-only pipeline runs so
    entries/exits are re-evaluated fresh against Friday's closing prices."""
    _, pipeline_calls = _monday_mocks(monkeypatch, mocker)
    _seed_order(db_engine, hours_old=33.5)

    scheduler._monday_premarket_job()

    assert len(pipeline_calls) == 1
    config, tickers, run_type = pipeline_calls[0]
    assert tickers == ["AAPL"]
    assert run_type == scheduler.RUN_ANALYSIS_ONLY


def test_monday_premarket_runs_pipeline_even_with_no_stale_orders(db_engine, monkeypatch, mocker):
    """No PLACED orders at all -> cancellation is a no-op, but the order
    pipeline still runs (Monday morning re-evaluation isn't conditional on
    there being anything to cancel)."""
    mock_client, pipeline_calls = _monday_mocks(monkeypatch, mocker)

    scheduler._monday_premarket_job()

    mock_client.cancel_order.assert_not_called()
    assert len(pipeline_calls) == 1


def test_scheduler_registers_monday_job_mon_only_fixed_at_0700(monkeypatch):
    """start_scheduler() must register monday_premarket with day_of_week='mon',
    fixed at 07:00 UTC directly (no longer derived from a scheduler.runs.pre_market
    slot, since the Tue-Fri sibling job that slot used to also drive was
    removed 2026-07-31 as confirmed-redundant)."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        def field(job_id: str, name: str) -> str:
            job = sched.get_job(job_id)
            assert job is not None, f"job '{job_id}' was not registered"
            return next(str(f) for f in job.trigger.fields if f.name == name)

        assert field("monday_premarket", "day_of_week") == "mon"
        assert field("monday_premarket", "hour") == "7"
        assert field("monday_premarket", "minute") == "0"
        # scheduled_eod is registered with NO day_of_week filter as of
        # 2026-08-02 -- it fires every day and decides for itself,
        # internally, via _is_scheduled_trading_day() (see that function's
        # own docstring and the dedicated calendar-gating tests below).
        # APScheduler's CronTrigger renders an unset field as "*".
        assert field("scheduled_eod", "day_of_week") == "*"

        # The removed Tue-Fri pre-market and 13:30 US-open jobs must not
        # come back under any job id.
        assert sched.get_job("scheduled_pre_market") is None
        assert sched.get_job("scheduled_us_open") is None
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_membership_refresh_job(monkeypatch):
    """start_scheduler() must actually register membership_refresh on the
    schedule -- not just leave refresh_membership_from_source() callable in
    isolation. Fires 15 minutes ahead of the 21:30 EOD job daily -- no
    day_of_week filter as of 2026-08-02, same market-calendar gate as
    scheduled_eod (see the dedicated calendar-gating tests below)."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        job = sched.get_job("membership_refresh")
        assert job is not None, "membership_refresh was not registered"
        assert job.func is scheduler._membership_refresh_job

        def field(name: str) -> str:
            return next(str(f) for f in job.trigger.fields if f.name == name)

        # No day_of_week filter -- fires daily, gated internally.
        assert field("day_of_week") == "*"
        assert field("hour") == "21"
        assert field("minute") == "15"
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_stock_submission_job_not_etf(monkeypatch):
    """start_scheduler() must register stock_order_submission at 14:30 UTC
    (60 minutes after NYSE's 13:30 UTC open -- moved 2026-08-02, see
    CLAUDE.md's dedicated section for the empirical basis), weekdays only.
    etf_order_submission must NOT be registered at all -- removed entirely
    2026-08-02 alongside the rest of the ETF sleeve (see CLAUDE.md's "ETF
    sleeve removed from live trading" section), not merely left disabled."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        def field(job_id: str, name: str) -> str:
            job = sched.get_job(job_id)
            assert job is not None, f"job '{job_id}' was not registered"
            return next(str(f) for f in job.trigger.fields if f.name == name)

        assert sched.get_job("etf_order_submission") is None
        assert not hasattr(scheduler, "_etf_order_submission_job")

        stock_job = sched.get_job("stock_order_submission")
        assert stock_job is not None
        assert stock_job.func is scheduler._stock_order_submission_job
        assert field("stock_order_submission", "day_of_week") == "mon-fri"
        assert field("stock_order_submission", "hour") == "14"
        assert field("stock_order_submission", "minute") == "30"
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_weekly_summary_job_genuinely_uk_timezone(monkeypatch):
    """start_scheduler() must register weekly_summary at Sunday 09:00 --
    critically, on a genuine Europe/London CronTrigger, not this
    scheduler's own default UTC (`tz` in start_scheduler()) -- a fixed UTC
    hour would silently be wrong by an hour across the BST/GMT boundary."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        job = sched.get_job("weekly_summary")
        assert job is not None, "weekly_summary was not registered"
        assert job.func is scheduler._weekly_summary_job

        def field(name: str) -> str:
            return next(str(f) for f in job.trigger.fields if f.name == name)

        assert field("day_of_week") == "sun"
        assert field("hour") == "9"
        assert field("minute") == "0"
        # The trigger's own timezone, not the scheduler's default -- this is
        # what actually makes the job fire at 9am UK local time year-round.
        assert str(job.trigger.timezone) == "Europe/London"
    finally:
        scheduler.stop_scheduler()


def test_weekly_summary_cron_trigger_correctly_resolves_gmt_and_bst():
    """Confirmed directly, not assumed: a Europe/London CronTrigger fires
    at 09:00 UTC in GMT (winter) and 08:00 UTC in BST (summer) -- the same
    UK wall-clock time, different UTC offsets. This is what "genuinely UK
    local time" means in practice, and is checked against APScheduler's
    real CronTrigger rather than trusted from documentation alone."""
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(day_of_week="sun", hour=9, minute=0, timezone="Europe/London")

    # 2026-01-04 is a Sunday, deep in GMT (UTC+0).
    gmt_prev = datetime(2026, 1, 3, tzinfo=timezone.utc)
    gmt_fire = trigger.get_next_fire_time(None, gmt_prev)
    assert gmt_fire.astimezone(timezone.utc).strftime("%H:%M") == "09:00"

    # 2026-07-05 is a Sunday, deep in BST (UTC+1) -- 09:00 local is 08:00 UTC.
    bst_prev = datetime(2026, 7, 4, tzinfo=timezone.utc)
    bst_fire = trigger.get_next_fire_time(None, bst_prev)
    assert bst_fire.astimezone(timezone.utc).strftime("%H:%M") == "08:00"


def test_weekly_summary_job_calls_send_weekly_summary_email(mocker):
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_send = mocker.patch("output.briefing.send_weekly_summary_email", return_value=(True, "Sent to 1 recipient(s)"))

    scheduler._weekly_summary_job()

    mock_send.assert_called_once_with({"k": "v"})


def test_weekly_summary_job_never_raises_on_failure(mocker):
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mocker.patch("output.briefing.send_weekly_summary_email", side_effect=RuntimeError("SMTP is down"))

    scheduler._weekly_summary_job()  # must not raise


def test_stock_order_submission_job_calls_submit_pending_stock_orders(mocker):
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_submit = mocker.patch("brokers.trading212.submit_pending_stock_orders", return_value=1)

    scheduler._stock_order_submission_job()

    mock_submit.assert_called_once_with({"k": "v"})


def test_stock_order_submission_job_never_raises_on_failure(mocker):
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mocker.patch("brokers.trading212.submit_pending_stock_orders", side_effect=RuntimeError("boom"))

    scheduler._stock_order_submission_job()  # must not raise


def test_membership_refresh_job_calls_refresh_and_logs_changes(monkeypatch, mocker, caplog):
    # Frozen to a real, verified ordinary trading day (2026-08-04, a
    # Tuesday) -- see the calendar-gating tests further below. Without
    # this, the market-calendar gate added 2026-08-02 short-circuits the
    # whole job before mock_refresh is ever called whenever this suite
    # happens to run on a real weekend/holiday, and the test would pass
    # vacuously (refresh simply never attempted) rather than genuinely
    # exercising the refresh-and-log behaviour it claims to cover.
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mock_refresh = mocker.patch(
        "analysis.universe.refresh_membership_from_source",
        return_value={"checked": 40, "changed": [{"ticker": "XYZ", "old": [], "new": [("2026-01-01", "2026-12-31")]}], "errors": []},
    )
    # Staleness/lag checks are exercised in their own dedicated tests below --
    # kept a deterministic "not stale" here so this test stays focused on the
    # refresh-and-log behaviour and never risks a real SMTP/network attempt.
    mocker.patch("analysis.universe.get_last_membership_refresh_success", return_value=datetime.utcnow())
    mocker.patch("analysis.universe.is_membership_refresh_stale", return_value=False)
    mocker.patch("analysis.universe.check_wikipedia_lag", return_value={"checked": 39, "disagreements": []})
    with caplog.at_level("INFO"):
        scheduler._membership_refresh_job()
    mock_refresh.assert_called_once()
    assert any("XYZ" in r.message for r in caplog.records)


def test_membership_refresh_job_never_raises_on_failure(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch(
        "analysis.universe.refresh_membership_from_source",
        side_effect=RuntimeError("network down"),
    )
    # Both new checks (2026-08-02) also made to fail -- the job must still
    # never raise, each is in its own try/except.
    mocker.patch("analysis.universe.get_last_membership_refresh_success", side_effect=RuntimeError("db down"))
    mocker.patch("analysis.universe.check_wikipedia_lag", side_effect=RuntimeError("wiki down"))
    scheduler._membership_refresh_job()  # must not raise


# ---------------------------------------------------------------------------
# Membership refresh staleness alert + Wikipedia lag check (2026-08-02) --
# fja05680/sp500 is now the sole live eligibility gate and previously had no
# staleness timestamp, no alert, and no lag detector at all. See
# analysis/universe.py's MEMBERSHIP_STALE_HOURS/is_membership_refresh_stale()/
# check_wikipedia_lag() docstrings for the full rationale.
# ---------------------------------------------------------------------------

def test_membership_refresh_job_sends_alert_when_stale(monkeypatch, mocker):
    """When is_membership_refresh_stale() says stale, the job must call
    send_membership_staleness_alert() with the real age and threshold --
    is_membership_refresh_stale() itself is mocked here so this test is
    immune to which real weekday/time it happens to run on (its own
    formula is covered directly, and deterministically, in
    tests/test_universe.py). The job's own market-calendar gate (2026-08-02)
    is a separate, earlier decision -- still frozen to a real trading day
    here so this test doesn't depend on which day it happens to run on
    either."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("analysis.universe.refresh_membership_from_source", return_value={"checked": 0, "changed": [], "errors": []})
    stale_last_success = datetime.utcnow() - timedelta(hours=96)
    mocker.patch("analysis.universe.get_last_membership_refresh_success", return_value=stale_last_success)
    mocker.patch("analysis.universe.is_membership_refresh_stale", return_value=True)
    mocker.patch("analysis.universe.check_wikipedia_lag", return_value={"checked": 39, "disagreements": []})
    mocker.patch("data.scheduler._load_config", return_value={"notifications": {"email": {"enabled": True}}})
    mock_alert = mocker.patch("output.briefing.send_membership_staleness_alert")

    scheduler._membership_refresh_job()

    mock_alert.assert_called_once()
    age_hours, threshold_hours, cfg = mock_alert.call_args[0]
    assert age_hours == pytest.approx(96.0, abs=0.1)
    assert threshold_hours == pytest.approx(48.0)
    assert cfg == {"notifications": {"email": {"enabled": True}}}


def test_membership_refresh_job_does_not_alert_when_not_stale(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("analysis.universe.refresh_membership_from_source", return_value={"checked": 0, "changed": [], "errors": []})
    mocker.patch("analysis.universe.get_last_membership_refresh_success", return_value=datetime.utcnow())
    mocker.patch("analysis.universe.is_membership_refresh_stale", return_value=False)
    mocker.patch("analysis.universe.check_wikipedia_lag", return_value={"checked": 39, "disagreements": []})
    mock_alert = mocker.patch("output.briefing.send_membership_staleness_alert")

    scheduler._membership_refresh_job()

    mock_alert.assert_not_called()


def test_membership_refresh_job_alert_never_raises_when_alert_itself_fails(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("analysis.universe.refresh_membership_from_source", return_value={"checked": 0, "changed": [], "errors": []})
    mocker.patch("analysis.universe.get_last_membership_refresh_success", return_value=None)
    mocker.patch("analysis.universe.is_membership_refresh_stale", return_value=True)
    mocker.patch("analysis.universe.check_wikipedia_lag", return_value={"checked": 39, "disagreements": []})
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("output.briefing.send_membership_staleness_alert", side_effect=RuntimeError("smtp down"))

    scheduler._membership_refresh_job()  # must not raise


def test_membership_refresh_job_calls_wikipedia_lag_check(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("analysis.universe.refresh_membership_from_source", return_value={"checked": 0, "changed": [], "errors": []})
    mocker.patch("analysis.universe.get_last_membership_refresh_success", return_value=datetime.utcnow())
    mocker.patch("analysis.universe.is_membership_refresh_stale", return_value=False)
    mock_wiki = mocker.patch("analysis.universe.check_wikipedia_lag", return_value={"checked": 39, "disagreements": []})

    scheduler._membership_refresh_job()

    mock_wiki.assert_called_once()


# ---------------------------------------------------------------------------
# _run_analysis_pipeline() — now just stamps last_run_type and delegates to
# brokers.trading212.maybe_place_orders(). There is no separate signal-
# generation step to mock: momentum/crisis/membership are all computed on
# demand inside maybe_place_orders() itself.
# ---------------------------------------------------------------------------

def test_run_analysis_pipeline_calls_order_placement(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mock_orders = mocker.patch("brokers.trading212.maybe_place_orders")
    mocker.patch("brokers.trading212.get_failed_instrument_resolutions", return_value=[])

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")

    mock_orders.assert_called_once_with({"k": "v"}, "full")


def test_run_analysis_pipeline_order_failure_does_not_raise(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("brokers.trading212.maybe_place_orders", side_effect=RuntimeError("boom"))
    mocker.patch("brokers.trading212.get_failed_instrument_resolutions", return_value=[])

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")  # must not raise


# ---------------------------------------------------------------------------
# _run_analysis_pipeline()'s FAILED-instrument-resolution alert (2026-08-02)
# -- without this, a genuinely tradeable candidate excluded by
# resolve_and_cache_instrument()'s "flag for review, never auto-retry a
# genuine mismatch" contract would just sit in instrument_resolutions until
# someone happened to open the Status page. See CLAUDE.md's "wire the
# momentum-only, full-S&P-500 selection into live execution" section.
# ---------------------------------------------------------------------------

def test_run_analysis_pipeline_alerts_on_failed_resolutions(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("brokers.trading212.maybe_place_orders")
    failed = [{"ticker": "ZZZZ", "note": "no match found"}]
    mocker.patch("brokers.trading212.get_failed_instrument_resolutions", return_value=failed)
    mock_alert = mocker.patch("output.briefing.send_instrument_resolution_failure_alert")

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")

    mock_alert.assert_called_once_with(failed, {"k": "v"})


def test_run_analysis_pipeline_does_not_alert_when_nothing_failed(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("brokers.trading212.maybe_place_orders")
    mocker.patch("brokers.trading212.get_failed_instrument_resolutions", return_value=[])
    mock_alert = mocker.patch("output.briefing.send_instrument_resolution_failure_alert")

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")

    mock_alert.assert_not_called()


def test_run_analysis_pipeline_alert_failure_never_raises(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("brokers.trading212.maybe_place_orders")
    mocker.patch("brokers.trading212.get_failed_instrument_resolutions",
                 side_effect=RuntimeError("db down"))

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")  # must not raise


def test_run_analysis_pipeline_checks_for_failures_even_on_analysis_only_run(mocker):
    """A still-unresolved FAILED ticker from a prior EOD run must keep
    being re-flagged (subject to its own cooldown) on every scheduled run,
    not just the run_type='full' run that could have produced it."""
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("brokers.trading212.maybe_place_orders")
    failed = [{"ticker": "ZZZZ", "note": "no match found"}]
    mocker.patch("brokers.trading212.get_failed_instrument_resolutions", return_value=failed)
    mock_alert = mocker.patch("output.briefing.send_instrument_resolution_failure_alert")

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "analysis_only")

    mock_alert.assert_called_once_with(failed, {"k": "v"})


# ---------------------------------------------------------------------------
# _trade_alert_job() — hourly poll + exit check + snapshot refresh only (no
# VIX-spike branch, no CFD alerting -- both removed along with V1).
# ---------------------------------------------------------------------------

def test_trade_alert_job_calls_poll_and_exit_check(mocker):
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_poll = mocker.patch("brokers.trading212.poll_placed_orders")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")
    mock_reconcile = mocker.patch("brokers.trading212.reconcile_positions_with_broker")
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._trade_alert_job()

    mock_poll.assert_called_once()
    mock_exit.assert_called_once()
    mock_reconcile.assert_called_once()
    mock_snap.assert_called_once()


def test_trade_alert_job_failure_does_not_block_job(mocker):
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders", side_effect=RuntimeError("boom"))
    mocker.patch("brokers.trading212.maybe_exit_positions", side_effect=RuntimeError("boom"))
    mocker.patch("brokers.trading212.reconcile_positions_with_broker", side_effect=RuntimeError("boom"))
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._trade_alert_job()  # must not raise

    mock_snap.assert_called_once()


def test_trade_alert_job_reconciliation_failure_does_not_block_snapshot(mocker):
    """A reconciliation-check failure must not prevent the exit check or
    the account snapshot refresh from still running that hour -- own
    try/except, same "one path's failure never blocks another's"
    convention every other call in this job already follows."""
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")
    mocker.patch("brokers.trading212.reconcile_positions_with_broker", side_effect=RuntimeError("boom"))
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._trade_alert_job()  # must not raise

    mock_exit.assert_called_once()
    mock_snap.assert_called_once()


# ---------------------------------------------------------------------------
# Market-calendar-aware scheduling (2026-08-02) -- scheduled_eod and
# membership_refresh both moved from a Mon-Fri cron day_of_week filter to
# firing every single day, with a genuine NYSE-calendar gate
# (_is_scheduled_trading_day()) checked first thing inside each job. See
# CLAUDE.md's dedicated section for the full reasoning, including why a
# plain weekday filter has no concept of a market holiday and why this
# leaves room for a future extended-hours session.
#
# Real, unmocked pandas_market_calendars NYSE data is used throughout --
# not a stubbed calendar -- so these tests exercise the genuine library,
# not an assumption about what it would say. Verified directly before
# writing these tests: 2026-11-26 (Thanksgiving) and 2026-08-01 (Saturday)
# both compute as closed; 2026-08-04 (an ordinary Tuesday) computes as open.
# ---------------------------------------------------------------------------

def _frozen_date_at(year: int, month: int, day: int, hour: int = 21, minute: int = 30):
    """A datetime subclass whose .now(tz) always returns the given fixed
    calendar date/time -- unlike _frozen_datetime() above (hardcoded to
    the module-level _NOW_DATE constant), this lets each calendar-gating
    test pick a specific, real, independently-verified date."""
    fixed = datetime(year, month, day, hour, minute, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz) if tz else fixed

    return _Frozen


def test_is_scheduled_trading_day_true_on_ordinary_weekday():
    from datetime import date
    assert scheduler._is_scheduled_trading_day(date(2026, 8, 4)) is True  # Tuesday


def test_is_scheduled_trading_day_false_on_real_market_holiday():
    from datetime import date
    assert scheduler._is_scheduled_trading_day(date(2026, 11, 26)) is False  # Thanksgiving


def test_is_scheduled_trading_day_false_on_weekend():
    from datetime import date
    assert scheduler._is_scheduled_trading_day(date(2026, 8, 1)) is False  # Saturday


def test_eod_and_membership_refresh_use_the_identical_gate():
    """Both jobs must be genuinely aligned -- not two independently
    hand-written checks that could drift apart, the exact "one shared
    function, not two that are merely supposed to agree" lesson this
    project has already been burned by more than once."""
    import inspect
    eod_src = inspect.getsource(scheduler._eod_job)
    membership_src = inspect.getsource(scheduler._membership_refresh_job)
    assert "_is_scheduled_trading_day(today_utc)" in eod_src
    assert "_is_scheduled_trading_day(today_utc)" in membership_src


def test_scheduled_eod_trigger_actually_fires_on_a_real_weekend_date(monkeypatch):
    """Confirms the registration itself, not just the internal logic --
    the trigger's own computed next-fire-time must land on a genuine
    Saturday, proving the job gets the chance to wake up and decide for
    itself rather than being excluded by a cron day_of_week filter before
    it ever runs. This is what "no day_of_week filter" (asserted as
    field(...) == "*" in the registration tests above) actually buys."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        job = sched.get_job("scheduled_eod")
        assert job is not None
        # 2026-07-31 21:30 UTC is a Friday (just past that day's own fire
        # time) -- the very next fire time must be Saturday 2026-08-01
        # 21:30 UTC if the trigger genuinely has no weekday restriction.
        just_after_friday = datetime(2026, 7, 31, 21, 31, tzinfo=timezone.utc)
        next_fire = job.trigger.get_next_fire_time(None, just_after_friday)
        assert next_fire is not None
        assert next_fire.date() == datetime(2026, 8, 1).date()  # Saturday
    finally:
        scheduler.stop_scheduler()


def _mock_eod_dependencies(mocker):
    """Deliberately does NOT mock _is_exchange_open() or the job-level
    _is_scheduled_trading_day() gate that reuses it -- both must run
    against the real, unmocked pandas_market_calendars NYSE data so these
    tests genuinely exercise calendar awareness, not an assumption about
    what it would say."""
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mock_pull = mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mock_pipeline = mocker.patch("data.scheduler._run_analysis_pipeline")
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    return mock_pull, mock_pipeline, mock_snap


def test_eod_job_holiday_skips_pull_only_not_the_whole_job(monkeypatch, mocker):
    """Revised 2026-08-02 -- this used to assert a FULL no-op on a
    holiday. That was itself the bug this same day's later fix closed:
    the price-pull gate correctly excludes a holiday, but candidate
    selection must still run for whatever the next real trading day is
    (here, the day after Thanksgiving). See _has_upcoming_trading_day()'s
    dedicated test block above for the full reasoning."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 11, 26))  # Thanksgiving
    mock_pull, mock_pipeline, mock_snap = _mock_eod_dependencies(mocker)

    scheduler._eod_job()

    mock_pull.assert_not_called()
    mock_pipeline.assert_called_once()
    _, _, run_type = mock_pipeline.call_args[0]
    assert run_type == scheduler.RUN_FULL
    mock_snap.assert_called_once()


def test_eod_job_weekend_skips_pull_only_not_the_whole_job(monkeypatch, mocker):
    """Revised 2026-08-02 -- see test_eod_job_holiday_skips_pull_only_
    not_the_whole_job's docstring immediately above for why. A Saturday's
    upcoming trading day (Monday) is what candidate selection queues for."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 1))  # Saturday
    mock_pull, mock_pipeline, mock_snap = _mock_eod_dependencies(mocker)

    scheduler._eod_job()

    mock_pull.assert_not_called()
    mock_pipeline.assert_called_once()
    _, _, run_type = mock_pipeline.call_args[0]
    assert run_type == scheduler.RUN_FULL
    mock_snap.assert_called_once()


def test_eod_job_runs_normally_on_ordinary_weekday(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))  # Tuesday
    mock_pull, mock_pipeline, mock_snap = _mock_eod_dependencies(mocker)

    scheduler._eod_job()

    mock_pull.assert_called_once()
    mock_pipeline.assert_called_once()
    mock_snap.assert_called_once()
    # 2026-08-02 fix: candidate selection now runs at RUN_FULL whenever an
    # upcoming trading day exists, independently of the price-pull gate --
    # on an ordinary weeknight both gates agree, so this must be unchanged.
    _, _, run_type = mock_pipeline.call_args[0]
    assert run_type == scheduler.RUN_FULL


# ---------------------------------------------------------------------------
# _has_upcoming_trading_day() / EOD candidate-selection gate fixed
# separately from the price-pull gate (2026-08-02, same day as the
# original calendar-gating change) -- the combined single gate above
# silently broke candidate queuing on every non-trading night, most
# urgently a Sunday night no longer queuing anything for Monday at all.
# See CLAUDE.md's dedicated section and _eod_job()'s own docstring.
#
# Real, independently-verified dates used throughout (no mocking of
# pandas_market_calendars itself):
#   2026-08-02 (today, a Sunday) -> next trading day 2026-08-03 (Monday)
#   2026-09-04 (Friday) -> precedes a genuine 2-day-weekend-plus-Labor-Day
#     -holiday gap; the real next trading day is 2026-09-08 (Tuesday), not
#     the literal "tomorrow" (Saturday) -- the decisive proof this can't
#     be a naive single-day-ahead check
#   2026-11-26 (Thanksgiving, a Thursday) -> next trading day 2026-11-27
#     (the day after, NYSE trades a shortened session) -- a HOLIDAY case,
#     not just a weekend one
# ---------------------------------------------------------------------------

def test_has_upcoming_trading_day_finds_monday_from_sunday():
    from datetime import date
    assert scheduler._has_upcoming_trading_day(date(2026, 8, 2)) is True


def test_has_upcoming_trading_day_finds_tuesday_from_friday_before_holiday_monday():
    """The decisive proof this is a real lookahead, not a naive
    tomorrow-only check: 2026-09-04's literal tomorrow (Saturday) is never
    a trading day, and neither is the day after that (Sunday), nor Monday
    (Labor Day) -- the real next trading day is Tuesday, 4 calendar days
    out. A single-day-ahead check would incorrectly return False here."""
    from datetime import date
    assert scheduler._has_upcoming_trading_day(date(2026, 9, 4)) is True


def test_has_upcoming_trading_day_finds_day_after_thanksgiving():
    from datetime import date
    assert scheduler._has_upcoming_trading_day(date(2026, 11, 26)) is True


def test_eod_job_sunday_night_queues_candidates_for_monday(monkeypatch, mocker):
    """The specific, urgent bug this fix closes: a Sunday night EOD run
    must skip the price pull (nothing new to pull) but STILL run candidate
    selection at RUN_FULL, so Monday has real orders queued and ready for
    submission rather than nothing until Tuesday."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 2))  # Sunday
    mock_pull, mock_pipeline, mock_snap = _mock_eod_dependencies(mocker)

    scheduler._eod_job()

    mock_pull.assert_not_called()  # price-pull gate unaffected -- today is not a trading day
    mock_pipeline.assert_called_once()
    _, _, run_type = mock_pipeline.call_args[0]
    assert run_type == scheduler.RUN_FULL  # candidates ARE queued, not analysis-only
    mock_snap.assert_called_once()


def test_eod_job_friday_before_holiday_monday_still_runs_full(monkeypatch, mocker):
    """Friday 2026-09-04 is itself a genuine trading day (both gates
    already agree here) -- confirms the fix doesn't regress the ordinary
    "Friday queues for the next session" case even when that next session
    is several calendar days out due to a holiday-shifted weekend."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 9, 4))  # Friday
    mock_pull, mock_pipeline, mock_snap = _mock_eod_dependencies(mocker)

    scheduler._eod_job()

    mock_pull.assert_called_once()  # today IS a trading day -- price pull still runs
    mock_pipeline.assert_called_once()
    _, _, run_type = mock_pipeline.call_args[0]
    assert run_type == scheduler.RUN_FULL
    mock_snap.assert_called_once()


def _mock_membership_refresh_dependencies(mocker):
    mock_refresh = mocker.MagicMock(return_value={"changed": [], "errors": []})
    mocker.patch("analysis.universe.refresh_membership_from_source", mock_refresh)
    mocker.patch("analysis.universe.get_last_membership_refresh_success", return_value=None)
    mocker.patch("analysis.universe.is_membership_refresh_stale", return_value=False)
    mock_lag = mocker.patch("analysis.universe.check_wikipedia_lag")
    return mock_refresh, mock_lag


def test_membership_refresh_job_noop_on_real_market_holiday(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 11, 26))
    mock_refresh, mock_lag = _mock_membership_refresh_dependencies(mocker)

    scheduler._membership_refresh_job()

    mock_refresh.assert_not_called()
    mock_lag.assert_not_called()


def test_membership_refresh_job_noop_on_weekend(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 1))
    mock_refresh, mock_lag = _mock_membership_refresh_dependencies(mocker)

    scheduler._membership_refresh_job()

    mock_refresh.assert_not_called()
    mock_lag.assert_not_called()


def test_membership_refresh_job_runs_normally_on_ordinary_weekday(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mock_refresh, mock_lag = _mock_membership_refresh_dependencies(mocker)

    scheduler._membership_refresh_job()

    mock_refresh.assert_called_once()
    mock_lag.assert_called_once()


# ---------------------------------------------------------------------------
# _fetch_t212_snapshot(record_history=...) / _record_account_value_history()
# (2026-08-02) -- powers the Dashboard page's account-value chart. Only the
# EOD job's own call passes record_history=True; the 60-second dispatcher's
# and hourly job's calls to the same function must NOT also write a history
# row, or account_value_history would accumulate many rows a day instead of
# staying a genuine daily time series. See data/database.py:
# AccountValueHistory's own docstring for the full reasoning.
# ---------------------------------------------------------------------------

def _t212_snapshot_mocks(monkeypatch, mocker, *, total_value=52_000.0, cash=47_000.0, invested=5_000.0):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    mock_client = mocker.MagicMock()
    mock_client.get_account_summary.return_value = {
        "totalValue": total_value,
        "cash": {"availableToTrade": cash},
        "investments": {"currentValue": invested},
    }
    mock_client.get_positions.return_value = []
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)
    mocker.patch("brokers.trading212.poll_placed_orders")
    return mock_client


def _account_value_history_rows(engine) -> list:
    with engine.connect() as conn:
        return conn.execute(sa_text(
            "SELECT date, total_value_gbp, cash_gbp, invested_gbp FROM account_value_history"
        )).mappings().all()


def test_fetch_t212_snapshot_default_does_not_record_history(db_engine, monkeypatch, mocker):
    """The dispatcher's and hourly job's own calls use the default
    (record_history=False) -- must never write a history row."""
    _t212_snapshot_mocks(monkeypatch, mocker)

    scheduler._fetch_t212_snapshot({"trading212_invest": {"enabled": True, "live": False}})

    assert _account_value_history_rows(db_engine) == []


def test_fetch_t212_snapshot_record_history_true_writes_row(db_engine, monkeypatch, mocker):
    """The EOD job's own call (record_history=True) must write a real row
    with the correct values from the just-fetched summary."""
    _t212_snapshot_mocks(monkeypatch, mocker, total_value=52_340.50, cash=47_000.0, invested=5_340.50)

    scheduler._fetch_t212_snapshot(
        {"trading212_invest": {"enabled": True, "live": False}}, record_history=True,
    )

    rows = _account_value_history_rows(db_engine)
    assert len(rows) == 1
    assert rows[0]["total_value_gbp"] == 52_340.50
    assert rows[0]["cash_gbp"] == 47_000.0
    assert rows[0]["invested_gbp"] == 5_340.50


def test_fetch_t212_snapshot_record_history_upserts_same_day(db_engine, monkeypatch, mocker):
    """A manual same-day 'Refresh Data' re-run (or a second real EOD call
    the same UTC day) must refresh that day's row, not create a duplicate
    -- exactly one row per calendar day, always."""
    _t212_snapshot_mocks(monkeypatch, mocker, total_value=50_000.0)
    scheduler._fetch_t212_snapshot(
        {"trading212_invest": {"enabled": True, "live": False}}, record_history=True,
    )

    _t212_snapshot_mocks(monkeypatch, mocker, total_value=51_500.0)
    scheduler._fetch_t212_snapshot(
        {"trading212_invest": {"enabled": True, "live": False}}, record_history=True,
    )

    rows = _account_value_history_rows(db_engine)
    assert len(rows) == 1
    assert rows[0]["total_value_gbp"] == 51_500.0  # the later value won


def test_fetch_t212_snapshot_record_history_disabled_config_writes_nothing(db_engine, monkeypatch, mocker):
    _t212_snapshot_mocks(monkeypatch, mocker)

    scheduler._fetch_t212_snapshot(
        {"trading212_invest": {"enabled": False}}, record_history=True,
    )

    assert _account_value_history_rows(db_engine) == []


def test_record_account_value_history_never_raises_on_malformed_summary(db_engine):
    """Never raises -- a history-recording failure must not affect the
    account snapshot already written by the time this runs."""
    scheduler._record_account_value_history({"totalValue": "not-a-number"})  # must not raise


def test_eod_job_passes_record_history_true_to_snapshot_call(monkeypatch, mocker):
    """Confirms the EOD job itself is the call site that opts in --
    verified directly against the call arguments, not just that some
    call happened."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))  # ordinary trading Tuesday
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    mock_snap.assert_called_once()
    _, kwargs = mock_snap.call_args
    assert kwargs.get("record_history") is True
