"""Tests for the missed-EOD catch-up check in data/scheduler.py.

_check_missed_eod_run() is a startup-time safety net: if the real 21:30 UTC
EOD job didn't fire (e.g. the service was mid-deploy/restart), and we're
still within the short post-EOD window, it triggers the analysis pipeline
once in a background thread so signals/orders aren't left on stale state
until the next scheduled job fires naturally. All external calls (metadata
store, ticker list, threading) are mocked so no real DB, network call, or
background thread is touched — the pipeline call is captured synchronously
via a fake Thread that runs its target immediately.
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
# Monday pre-market job — stale weekend order cancellation + fresh analysis.
#
# _monday_premarket_job() cancels any PLACED BUY order in t212_invest_orders
# older than scheduler._STALE_ORDER_HOURS (30h — comfortably covers a Friday
# 21:30 UTC order, ~33.5h old by Monday 07:00) before re-running the analysis
# pipeline. All DB reads/writes go through the db_engine fixture's in-memory
# SQLite database (tests/conftest.py); the Trading212 HTTP client is mocked
# entirely via pytest-mock so no real API call is ever made.
# ---------------------------------------------------------------------------

from sqlalchemy.orm import Session  # noqa: E402

from data.database import T212InvestOrder  # noqa: E402


def _seed_order(engine, *, hours_old: float, order_id="t212-order-1", ticker="AAPL", status="PLACED"):
    with Session(engine) as session:
        session.add(T212InvestOrder(
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
        order = session.query(T212InvestOrder).filter_by(order_id="stale-order").one()
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
        order = session.query(T212InvestOrder).filter_by(order_id="recent-order").one()
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
        session.add(T212InvestOrder(
            order_id="same-day-not-stale",
            ticker="AAPL", t212_ticker="AAPL", direction="BUY", quantity=5.0,
            status="PLACED", account_type="DEMO",
            placed_at=datetime(2026, 7, 5, 3, 0, 0),  # 28h before frozen "now"
        ))
        session.commit()

    scheduler._monday_premarket_job()

    mock_client.cancel_order.assert_not_called()
    with Session(db_engine) as session:
        order = session.query(T212InvestOrder).filter_by(order_id="same-day-not-stale").one()
    assert order.status == "PLACED"


def test_monday_premarket_calls_analysis_pipeline_after_cancellation(db_engine, monkeypatch, mocker):
    """After stale-order cancellation, the analysis-only pipeline runs so
    signals/orders are re-evaluated fresh against Friday's closing prices."""
    _, pipeline_calls = _monday_mocks(monkeypatch, mocker)
    _seed_order(db_engine, hours_old=33.5)

    scheduler._monday_premarket_job()

    assert len(pipeline_calls) == 1
    config, tickers, run_type = pipeline_calls[0]
    assert tickers == ["AAPL"]
    assert run_type == scheduler.RUN_ANALYSIS_ONLY


def test_monday_premarket_runs_pipeline_even_with_no_stale_orders(db_engine, monkeypatch, mocker):
    """No PLACED orders at all -> cancellation is a no-op, but the analysis
    pipeline still runs (Monday morning re-evaluation isn't conditional on
    there being anything to cancel)."""
    mock_client, pipeline_calls = _monday_mocks(monkeypatch, mocker)

    scheduler._monday_premarket_job()

    mock_client.cancel_order.assert_not_called()
    assert len(pipeline_calls) == 1


def test_scheduler_registers_monday_job_mon_only_and_premarket_tue_fri(monkeypatch):
    """start_scheduler() must register monday_premarket with day_of_week='mon'
    and the regular pre_market job with 'tue-fri' — not both firing Monday."""
    fake_config = {
        "scheduler": {
            "timezone": "UTC",
            "runs": {
                "pre_market": {"hour": 7, "minute": 0, "label": "Pre-Market", "type": "analysis_only"},
                "us_open":    {"hour": 13, "minute": 30, "label": "US Open", "type": "analysis_only"},
                "eod":        {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
            },
        },
    }
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        def day_of_week(job_id: str) -> str:
            job = sched.get_job(job_id)
            assert job is not None, f"job '{job_id}' was not registered"
            return next(str(f) for f in job.trigger.fields if f.name == "day_of_week")

        assert day_of_week("monday_premarket") == "mon"
        assert day_of_week("scheduled_pre_market") == "tue-fri"
        assert day_of_week("scheduled_us_open") == "mon-fri"
        assert day_of_week("scheduled_eod") == "mon-fri"
    finally:
        scheduler.stop_scheduler()
