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

from datetime import datetime, timedelta

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
        assert field("scheduled_eod", "day_of_week") == "mon-fri"

        # The removed Tue-Fri pre-market and 13:30 US-open jobs must not
        # come back under any job id.
        assert sched.get_job("scheduled_pre_market") is None
        assert sched.get_job("scheduled_us_open") is None
    finally:
        scheduler.stop_scheduler()


def test_scheduler_registers_membership_refresh_job(monkeypatch):
    """start_scheduler() must actually register membership_refresh on the
    schedule -- not just leave refresh_membership_from_source() callable in
    isolation. Fires 15 minutes ahead of the 21:30 EOD job, weekdays only."""
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

        assert field("day_of_week") == "mon-fri"
        assert field("hour") == "21"
        assert field("minute") == "15"
    finally:
        scheduler.stop_scheduler()


def test_membership_refresh_job_calls_refresh_and_logs_changes(mocker, caplog):
    mock_refresh = mocker.patch(
        "analysis.universe.refresh_membership_from_source",
        return_value={"checked": 40, "changed": [{"ticker": "XYZ", "old": [], "new": [("2026-01-01", "2026-12-31")]}], "errors": []},
    )
    with caplog.at_level("INFO"):
        scheduler._membership_refresh_job()
    mock_refresh.assert_called_once()
    assert any("XYZ" in r.message for r in caplog.records)


def test_membership_refresh_job_never_raises_on_failure(mocker):
    mocker.patch(
        "analysis.universe.refresh_membership_from_source",
        side_effect=RuntimeError("network down"),
    )
    scheduler._membership_refresh_job()  # must not raise


# ---------------------------------------------------------------------------
# _run_analysis_pipeline() — now just stamps last_run_type and delegates to
# brokers.trading212.maybe_place_orders(). There is no separate signal-
# generation step to mock: momentum/crisis/membership are all computed on
# demand inside maybe_place_orders() itself.
# ---------------------------------------------------------------------------

def test_run_analysis_pipeline_calls_order_placement(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mock_orders = mocker.patch("brokers.trading212.maybe_place_orders")

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")

    mock_orders.assert_called_once_with({"k": "v"}, "full")


def test_run_analysis_pipeline_order_failure_does_not_raise(mocker):
    mocker.patch("data.scheduler.set_metadata")
    mocker.patch("brokers.trading212.maybe_place_orders", side_effect=RuntimeError("boom"))

    scheduler._run_analysis_pipeline({"k": "v"}, ["AAPL"], "full")  # must not raise


# ---------------------------------------------------------------------------
# _trade_alert_job() — hourly poll + exit check + snapshot refresh only (no
# VIX-spike branch, no CFD alerting -- both removed along with V1).
# ---------------------------------------------------------------------------

def test_trade_alert_job_calls_poll_and_exit_check(mocker):
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_poll = mocker.patch("brokers.trading212.poll_placed_orders")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._trade_alert_job()

    mock_poll.assert_called_once()
    mock_exit.assert_called_once()
    mock_snap.assert_called_once()


def test_trade_alert_job_failure_does_not_block_job(mocker):
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders", side_effect=RuntimeError("boom"))
    mocker.patch("brokers.trading212.maybe_exit_positions", side_effect=RuntimeError("boom"))
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._trade_alert_job()  # must not raise

    mock_snap.assert_called_once()
