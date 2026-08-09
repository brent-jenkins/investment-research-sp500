"""Tests for data/scheduler.py.

Covers the missed-EOD catch-up check, job registration (EOD, membership
refresh), and the simplified _run_analysis_pipeline() body, which just
delegates straight to brokers/trading212.py's maybe_place_orders(), since
there is no separate "analysis pipeline" step in the momentum +
crisis-override system (see data/scheduler.py's module docstring).

The Tue-Fri 07:00 pre-market and 13:30 US-open "analysis only" jobs were
removed 2026-07-31 (confirmed genuinely redundant with the hourly trade
alert job — see CLAUDE.md's "Pre-market/US-open scheduled runs removed"
section) — there is no `_pre_market_job()`/`_us_open_job()` left to test.
The Monday-only 07:00 UTC "Monday Pre-Market" job (stale weekend BUY order
cancellation + an order-logic re-run) was removed entirely 2026-08-03 for
the identical reason — see CLAUDE.md's "Monday Pre-Market removed"
section — there is no `_monday_premarket_job()` left to test either.

The single, unconditional hourly `_trade_alert_job()` (poll + exit check +
reconciliation + snapshot refresh, on a hardcoded "0-21,23 mon-fri" cron
window) was itself split 2026-08-03 into `_market_hours_check_job()`
(order poll + exit check, internally gated on a genuine, live
`_is_nyse_open_now()` check rather than an approximated UTC window) and
`_account_state_job()` (reconciliation + snapshot refresh, unconditional)
— see both functions' own docstrings and CLAUDE.md's "Splitting the
hourly trade-alert job" section for the full investigation and rationale.
There is no `_trade_alert_job()` left to test either.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as sa_text

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


def _patch_common(monkeypatch, *, now_hour, now_minute, last_refresh_hours_ago, pipeline_calls):
    monkeypatch.setattr(scheduler, "datetime", _frozen_datetime(now_hour, now_minute))

    if last_refresh_hours_ago is None:
        monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    else:
        now_naive = datetime(*_NOW_DATE, now_hour, now_minute)
        last_dt = now_naive - timedelta(hours=last_refresh_hours_ago)
        monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: last_dt.isoformat())

    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "clear_metadata", lambda *a, **k: None)
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


def test_missed_eod_catch_up_marks_and_clears_job_in_progress(db_engine, monkeypatch):
    """The catch-up thread queues real candidates too (it's the same
    _run_analysis_pipeline() call the real EOD job makes), so deploy.sh's
    check_eod_job_in_progress.py must see it as in-progress just like a
    full scheduled run -- not only the 21:30 job itself (2026-08-05, see
    CLAUDE.md's "GitHub webhook deploy" for the incident this closes).
    Uses the real set_metadata/clear_metadata (via db_engine) rather than
    _patch_common's no-op versions, specifically to observe the marker's
    real state."""
    seen_status_during_call: list = []

    def fake_pipeline(config, tickers, run_type):
        seen_status_during_call.append(scheduler.get_job_in_progress_status())

    monkeypatch.setattr(scheduler, "datetime", _frozen_datetime(22, 0))
    now_naive = datetime(*_NOW_DATE, 22, 0)
    last_dt = now_naive - timedelta(hours=25)

    # Only fake "last_full_refresh" (the staleness check this test needs
    # to force) -- everything else, notably the job-in-progress marker
    # key, must fall through to the REAL, db_engine-backed get_metadata_str
    # so this test can genuinely observe _mark_job_started()'s effect.
    from data.database import get_metadata_str as _real_get_metadata_str

    def fake_get_metadata_str(key):
        if key == "last_full_refresh":
            return last_dt.isoformat()
        return _real_get_metadata_str(key)

    monkeypatch.setattr(scheduler, "get_metadata_str", fake_get_metadata_str)
    monkeypatch.setattr(scheduler, "get_all_tickers", lambda config: ["AAPL"])
    monkeypatch.setattr(scheduler, "_run_analysis_pipeline", fake_pipeline)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)

    scheduler._check_missed_eod_run({"some": "config"})

    assert len(seen_status_during_call) == 1
    assert seen_status_during_call[0] is not None, (
        "the job-in-progress marker must already be set by the time the real "
        "pipeline call happens, not only after it returns"
    )
    assert seen_status_during_call[0]["job"] == "eod-catch-up"
    # Cleared once the (synchronously-run, via _ImmediateThread) thread finishes.
    assert scheduler.get_job_in_progress_status() is None


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


def test_scheduler_never_registers_monday_premarket_job(monkeypatch):
    """The Monday-only 07:00 UTC "Monday Pre-Market" job (stale weekend BUY
    order cancellation + an order-logic re-run) was removed entirely
    2026-08-03 -- both halves confirmed unnecessary: cancellation, because
    the decide-then-submit architecture never places an order anywhere near
    market close or over a weekend (submission happens at 14:30 UTC
    mid-session); the order-logic re-run, because it duplicated the hourly
    trade_alert_check job, which fires at this exact same 07:00 UTC minute
    every Monday (see CLAUDE.md's "Monday Pre-Market removed" section for
    the real production-log evidence). Must not come back under any job id,
    mirroring the identical "confirmed removed, stays removed" check the
    Tue-Fri pre-market/US-open jobs and the ETF-submission job already
    have."""
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
        assert sched.get_job("monday_premarket") is None
        assert not hasattr(scheduler, "_monday_premarket_job")
        assert not hasattr(scheduler, "_cancel_stale_weekend_orders")

        # scheduled_eod is registered with NO day_of_week filter as of
        # 2026-08-02 -- it fires every day and decides for itself,
        # internally, via _is_scheduled_trading_day() (see that function's
        # own docstring and the dedicated calendar-gating tests below).
        # APScheduler's CronTrigger renders an unset field as "*".
        job = sched.get_job("scheduled_eod")
        assert job is not None
        assert next(str(f) for f in job.trigger.fields if f.name == "day_of_week") == "*"

        # The removed Tue-Fri pre-market and 13:30 US-open jobs must not
        # come back under any job id either.
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
    CLAUDE.md's dedicated section for the empirical basis). Fires every
    single day now (no day_of_week cron filter), gated internally on
    _is_nyse_open_now() instead -- 2026-08-03 fix, correcting this test's
    own previously-stale "mon-fri" assertion, which encoded the exact
    weekday-only-cron gap this fix closed (see the dedicated
    "NYSE-session gating fix" test section below for full coverage of the
    new gating behaviour: real-holiday skip, real-weekend skip, genuine
    market-open run, and the trigger's own no-filter registration).
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
        assert field("stock_order_submission", "day_of_week") == "*"
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
    # set_metadata is mocked here (2026-08-03) -- the job now also stamps
    # last_scheduled_run/last_scheduled_run_name (see the dedicated stamping
    # test below); unmocked, this would call the real data.database.set_metadata,
    # which resolves its own DB path independently of data.scheduler._load_config
    # above and would silently write to the real local project database.
    mocker.patch("data.scheduler.set_metadata")

    scheduler._weekly_summary_job()

    mock_send.assert_called_once_with({"k": "v"})


def test_weekly_summary_job_never_raises_on_failure(mocker):
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mocker.patch("output.briefing.send_weekly_summary_email", side_effect=RuntimeError("SMTP is down"))
    mocker.patch("data.scheduler.set_metadata")

    scheduler._weekly_summary_job()  # must not raise


def test_weekly_summary_job_stamps_last_scheduled_run(mocker):
    """2026-08-03 fix — this job previously never touched
    last_scheduled_run/last_scheduled_run_name at all, so the Status page's
    "Last Scheduled Run" card, the Pipeline "Order Logic" row, and the
    sidebar's "Last scheduled run" caption (all three read the same key)
    could keep showing a much older job as the most recent one indefinitely,
    even on a Sunday when this was genuinely the most recent job to fire."""
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mocker.patch("output.briefing.send_weekly_summary_email", return_value=(True, "ok"))
    mock_meta = mocker.patch("data.scheduler.set_metadata")

    scheduler._weekly_summary_job()

    mock_meta.assert_any_call("last_scheduled_run_name", "Weekly Summary")
    assert any(c.args[0] == "last_scheduled_run" for c in mock_meta.call_args_list)


def test_stock_order_submission_job_calls_submit_pending_stock_orders(monkeypatch, mocker):
    # 2026-08-03 fix: this job now gates internally on the real NYSE
    # session (_is_nyse_open_now()) before doing anything at all -- frozen
    # to a real, independently-verified open moment (same date/hour
    # test_market_hours_check_job_runs_when_nyse_genuinely_open already
    # uses) so this test is no longer wall-clock dependent.
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_submit = mocker.patch("brokers.trading212.submit_pending_stock_orders", return_value=1)
    # set_metadata is mocked here for the same reason as the weekly-summary
    # tests above -- see that comment.
    mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()

    mock_submit.assert_called_once_with({"k": "v"})


def test_stock_order_submission_job_never_raises_on_failure(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mocker.patch("brokers.trading212.submit_pending_stock_orders", side_effect=RuntimeError("boom"))
    mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()  # must not raise


def test_stock_order_submission_job_stamps_last_scheduled_run(monkeypatch, mocker):
    """2026-08-03 fix — same issue, same reason as the weekly-summary job
    above. This is the job that actually places real orders (see the
    2026-08-03 incident: 16 real positions opened at 14:30 UTC / 15:30 BST,
    yet the Status page's job-recency widgets still showed the 07:00 UTC /
    08:00 BST Monday Pre-Market run as the most recent event, since this
    job never stamped last_scheduled_run at all)."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mocker.patch("brokers.trading212.submit_pending_stock_orders", return_value=1)
    mock_meta = mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()

    mock_meta.assert_any_call("last_scheduled_run_name", "Stock Order Submission")
    assert any(c.args[0] == "last_scheduled_run" for c in mock_meta.call_args_list)


# ---------------------------------------------------------------------------
# NYSE-session gating fix (2026-08-03) — confirmed a real, live gap: this
# job was still registered with a bare day_of_week="mon-fri" cron filter
# and had NO internal calendar/session check of its own at all, unlike
# every other job touched the same day. A plain weekday filter has no
# concept of a US market holiday — on a Monday that also happens to be a
# genuine NYSE closure, this job would still have fired at 14:30 UTC
# believing it was a normal trading day and attempted to submit real
# orders with the market closed. Fixed by reusing the exact,
# already-proven _is_nyse_open_now() mechanism market_hours_check_job()
# already uses (the stricter, live-session check, not the day-level
# _is_scheduled_trading_day() scheduled_eod/membership_refresh use — see
# the job's own docstring for why the stricter check is the correct one
# here). Mirrors the market_hours_check_job() test pattern above exactly,
# reusing the same real, independently-verified dates
# (test_is_scheduled_trading_day_false_on_real_market_holiday /
# test_is_scheduled_trading_day_false_on_weekend already confirm 2026-11-26
# and 2026-08-01 compute as closed via the real pandas_market_calendars
# library, not a stubbed one).
# ---------------------------------------------------------------------------

def test_stock_order_submission_job_skips_on_real_market_holiday(monkeypatch, mocker):
    """A real, independently-verified NYSE holiday that falls on a weekday
    (2026-11-26, Thanksgiving) — submission must not be attempted at all,
    and last_scheduled_run must not be overwritten by a run that did
    nothing (stamping happens AFTER the gate)."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 11, 26, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_submit = mocker.patch("brokers.trading212.submit_pending_stock_orders")
    mock_meta = mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()

    mock_submit.assert_not_called()
    mock_meta.assert_not_called()


def test_stock_order_submission_job_skips_on_real_weekend(monkeypatch, mocker):
    """Explicit proof — not an assumption — that a genuine Saturday is
    correctly skipped through the SAME mechanism as the holiday case above
    (_is_nyse_open_now()), with no separate day-of-week logic anywhere in
    this job at all: NYSE is never in session on a Saturday, so the
    identical live-calendar check naturally returns False here too,
    exactly as it already does for market_hours_check_job() every
    weekend."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 1, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_submit = mocker.patch("brokers.trading212.submit_pending_stock_orders")
    mock_meta = mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()

    mock_submit.assert_not_called()
    mock_meta.assert_not_called()


def test_stock_order_submission_job_runs_when_nyse_genuinely_open(monkeypatch, mocker):
    """A real, independently-verified NYSE-open moment (mid-session on an
    ordinary Tuesday, the same date/hour market_hours_check_job()'s own
    "genuinely open" test uses) — submission must actually run."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_submit = mocker.patch("brokers.trading212.submit_pending_stock_orders", return_value=1)
    mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()

    mock_submit.assert_called_once_with({"k": "v"})


def test_stock_order_submission_job_still_skips_during_extended_hours_session(monkeypatch, mocker):
    """Confirms this job's own behaviour is genuinely unaffected by the
    2026-08-04 extended-hours change (see CLAUDE.md's "extend Open Trade
    Monitor to T212's extended-hours sessions" section) — not merely "the
    code wasn't touched," but empirically: frozen to 2026-08-04 07:00 UTC,
    the exact moment test_is_nyse_extended_hours_open_true_during_overnight
    confirms is genuinely OPEN under the new wider check (and therefore
    now genuinely runs market_hours_check_job(), see the sibling test
    above) — this job must still decline, since it deliberately kept using
    the narrower, regular-hours-only _is_nyse_open_now() (confirmed False
    at this exact moment by test_is_nyse_open_now_false_before_session_open)."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=7, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"k": "v"})
    mock_submit = mocker.patch("brokers.trading212.submit_pending_stock_orders", return_value=1)
    mocker.patch("data.scheduler.set_metadata")

    scheduler._stock_order_submission_job()

    mock_submit.assert_not_called()


def test_stock_order_submission_registered_daily_no_day_of_week_filter(monkeypatch):
    """Confirms the registration itself, not just the job's own internal
    behaviour — the load-bearing part of the "fires unconditionally,
    decides internally" pattern: if the trigger still carried a
    day_of_week filter, the job would never even wake up to run its own
    _is_nyse_open_now() gate on the days the filter excluded. Mirrors
    test_market_hours_check_and_account_state_registered_hourly_no_cron_filter
    above exactly."""
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
        job = sched.get_job("stock_order_submission")
        assert job is not None
        assert job.func is scheduler._stock_order_submission_job

        def field(name: str) -> str:
            return next(str(f) for f in job.trigger.fields if f.name == name)

        assert field("day_of_week") == "*"
        assert field("hour") == "14"
        assert field("minute") == "30"
    finally:
        scheduler.stop_scheduler()


def test_stock_order_submission_trigger_actually_fires_on_a_real_weekend_date(monkeypatch):
    """Confirms the registration's own computed next-fire-time, not just
    the raw field values — mirrors
    test_market_hours_check_trigger_actually_fires_on_a_real_weekend_date
    above: the trigger must genuinely land on a real Saturday, proving the
    job gets the chance to wake up and decide for itself (via
    _is_nyse_open_now()) rather than being excluded by a cron filter
    before it ever runs."""
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
        job = sched.get_job("stock_order_submission")
        assert job is not None
        just_after_friday_midnight = datetime(2026, 7, 31, 23, 1, tzinfo=timezone.utc)
        next_fire = job.trigger.get_next_fire_time(None, just_after_friday_midnight)
        assert next_fire is not None
        assert next_fire.date() == datetime(2026, 8, 1).date()  # Saturday
    finally:
        scheduler.stop_scheduler()


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
# _is_nyse_open_now() -- real, unmocked pandas_market_calendars NYSE data,
# same discipline as the calendar-gating tests below: verified directly
# before writing these, not assumed. 2026-08-04 (an ordinary Tuesday) has a
# real NYSE session of 13:30-20:00 UTC; 2026-08-01 is a genuine Saturday
# (fully closed all day).
# ---------------------------------------------------------------------------

def test_is_nyse_open_now_true_during_real_session():
    now = datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)  # mid-session
    assert scheduler._is_nyse_open_now(now) is True


def test_is_nyse_open_now_false_before_session_open():
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)  # hours before 13:30 open
    assert scheduler._is_nyse_open_now(now) is False


def test_is_nyse_open_now_false_after_session_close():
    now = datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)  # hour after 20:00 close
    assert scheduler._is_nyse_open_now(now) is False


def test_is_nyse_open_now_false_on_real_weekend():
    now = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)  # Saturday, same clock time as the open-session case
    assert scheduler._is_nyse_open_now(now) is False


# ---------------------------------------------------------------------------
# _is_nyse_extended_hours_open() (2026-08-04) -- real, unmocked
# pandas_market_calendars NYSE data, same discipline as _is_nyse_open_now()'s
# own tests above. Every value below was independently verified directly
# (not assumed) before being written into a test -- see the function's own
# docstring for the pre/regular/after-hours/overnight boundary reasoning.
# The overnight case deliberately reuses 2026-08-04 07:00 UTC, the EXACT
# moment test_is_nyse_open_now_false_before_session_open above proves is
# CLOSED under the regular-hours-only check -- the sharpest possible
# contrast, on the identical timestamp, for the identical function's wider
# sibling.
# ---------------------------------------------------------------------------

def test_is_nyse_extended_hours_open_true_during_pre_market():
    now = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)  # 05:00 EDT -- inside 04:00-09:30 ET pre-market
    assert scheduler._is_nyse_extended_hours_open(now) is True


def test_is_nyse_extended_hours_open_true_during_regular_session():
    now = datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)  # same moment as the regular-session _is_nyse_open_now() test
    assert scheduler._is_nyse_extended_hours_open(now) is True


def test_is_nyse_extended_hours_open_true_during_after_hours():
    now = datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)  # 17:00 EDT -- inside 16:00-20:00 ET after-hours
    assert scheduler._is_nyse_extended_hours_open(now) is True


def test_is_nyse_extended_hours_open_true_during_overnight():
    """The decisive contrast case: identical timestamp to
    test_is_nyse_open_now_false_before_session_open above (2026-08-04
    07:00 UTC, confirmed closed under the regular-hours-only check) --
    genuinely open under this wider one, since 07:00 UTC falls inside the
    prior trading day's overnight session (Blue Ocean ATS, 20:00-04:00 ET)."""
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    assert scheduler._is_nyse_open_now(now) is False  # reconfirmed: still closed under the regular-hours check
    assert scheduler._is_nyse_extended_hours_open(now) is True  # but open under the extended-hours one


def test_is_nyse_extended_hours_open_false_on_real_weekend():
    now = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)  # a genuine Saturday, comfortably mid-weekend
    assert scheduler._is_nyse_extended_hours_open(now) is False


def test_is_nyse_extended_hours_open_false_on_real_market_holiday():
    now = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)  # Thanksgiving -- genuinely closed all day
    assert scheduler._is_nyse_extended_hours_open(now) is False


# ---------------------------------------------------------------------------
# _market_hours_check_job() -- order poll + exit check, internally gated on
# _is_nyse_extended_hours_open() (2026-08-04 -- widened from the
# regular-hours-only _is_nyse_open_now(), 2026-08-03 split, replacing the
# old single _trade_alert_job()). Reuses _frozen_date_at() (already used
# throughout this file for the calendar-gating tests below) to drive the
# job's internal datetime.now(timezone.utc) call to a specific, real
# NYSE-open or NYSE-closed moment -- not a mocked/assumed calendar
# response, the genuine pandas_market_calendars check runs end-to-end.
# ---------------------------------------------------------------------------

def test_market_hours_check_job_skips_when_nyse_genuinely_closed(monkeypatch, mocker):
    """A real, independently-verified NYSE-closed moment (Saturday) --
    poll/retry/exit must not be attempted at all."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 1, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_poll = mocker.patch("brokers.trading212.poll_placed_orders")
    mock_retry = mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")

    scheduler._market_hours_check_job()

    mock_poll.assert_not_called()
    mock_retry.assert_not_called()
    mock_exit.assert_not_called()


def test_market_hours_check_job_runs_when_nyse_genuinely_open(monkeypatch, mocker):
    """A real, independently-verified NYSE-open moment (mid-session on an
    ordinary Tuesday) -- poll, the missing-stop retry, and the exit check
    must all run."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_poll = mocker.patch("brokers.trading212.poll_placed_orders")
    mock_retry = mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")

    scheduler._market_hours_check_job()

    mock_poll.assert_called_once()
    mock_retry.assert_called_once()
    mock_exit.assert_called_once()


def test_market_hours_check_job_failure_does_not_raise(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders", side_effect=RuntimeError("boom"))
    mocker.patch("brokers.trading212.retry_missing_broker_stops", side_effect=RuntimeError("boom"))
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions", side_effect=RuntimeError("boom"))

    scheduler._market_hours_check_job()  # must not raise


def test_market_hours_check_job_retry_failure_does_not_block_the_exit_check(monkeypatch, mocker):
    """Per-step exception isolation, same contract as every other call in
    this job: a failure in the missing-stop retry pass specifically must
    not prevent the exit check from still running."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mocker.patch("brokers.trading212.retry_missing_broker_stops", side_effect=RuntimeError("boom"))
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")

    scheduler._market_hours_check_job()  # must not raise

    mock_exit.assert_called_once()


def test_market_hours_check_job_runs_during_overnight_session_previously_skipped(monkeypatch, mocker):
    """The actual behavioural change this task ships (2026-08-04): frozen
    to 2026-08-04 07:00 UTC -- the exact moment
    test_is_nyse_open_now_false_before_session_open (above) confirms is
    CLOSED under the old regular-hours-only gate this job used to use.
    Under the new extended-hours gate, that identical moment is now
    genuinely inside the overnight session, and poll/retry/exit must all
    run."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=7, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_poll = mocker.patch("brokers.trading212.poll_placed_orders")
    mock_retry = mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")

    scheduler._market_hours_check_job()

    mock_poll.assert_called_once()
    mock_retry.assert_called_once()
    mock_exit.assert_called_once()


# ---------------------------------------------------------------------------
# _market_hours_check_job()'s crisis-status snapshot for display (2026-08-09)
# -- see CLAUDE.md's "UI render-path blocking audit" section, finding 3.
# Stores crisis_mode_active/crisis_mode_checked_at so the Dashboard's crisis
# banner can read a stored value instead of calling yfinance itself. This is
# a SEPARATE call from, and does not affect, the order-submission path's own
# live is_crisis_mode_active_now() call -- confirmed by inspection
# (brokers/trading212.py's stock-submission logic calls the same function
# independently, with no shared state).
# ---------------------------------------------------------------------------

def test_market_hours_check_job_stores_confirmed_crisis_active(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mocker.patch("brokers.trading212.maybe_exit_positions")
    mocker.patch("brokers.trading212.check_stop_health")
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=True)
    mock_set = mocker.patch("data.scheduler.set_metadata")

    scheduler._market_hours_check_job()

    mock_set.assert_any_call("crisis_mode_active", "true")
    assert any(c.args[0] == "crisis_mode_checked_at" for c in mock_set.call_args_list)


def test_market_hours_check_job_stores_confirmed_crisis_inactive(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mocker.patch("brokers.trading212.maybe_exit_positions")
    mocker.patch("brokers.trading212.check_stop_health")
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=False)
    mock_set = mocker.patch("data.scheduler.set_metadata")

    scheduler._market_hours_check_job()

    mock_set.assert_any_call("crisis_mode_active", "false")


def test_market_hours_check_job_leaves_stored_crisis_value_on_unknown_check(monkeypatch, mocker):
    """A None result (check failure) must NOT overwrite the previously
    stored value/timestamp with 'unknown' -- the last known-good snapshot
    stays in place, mirroring how the order-submission path itself treats
    a None result as 'unknown, don't act' rather than a definite answer."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mocker.patch("brokers.trading212.maybe_exit_positions")
    mocker.patch("brokers.trading212.check_stop_health")
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", return_value=None)
    mock_set = mocker.patch("data.scheduler.set_metadata")

    scheduler._market_hours_check_job()

    assert not any(c.args[0] in ("crisis_mode_active", "crisis_mode_checked_at") for c in mock_set.call_args_list)


def test_market_hours_check_job_crisis_check_exception_does_not_raise_or_block_other_steps(monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mocker.patch("brokers.trading212.retry_missing_broker_stops")
    mock_exit = mocker.patch("brokers.trading212.maybe_exit_positions")
    mock_stop_health = mocker.patch("brokers.trading212.check_stop_health")
    mocker.patch("brokers.trading212.is_crisis_mode_active_now", side_effect=RuntimeError("boom"))

    scheduler._market_hours_check_job()  # must not raise

    mock_exit.assert_called_once()
    mock_stop_health.assert_called_once()


# ---------------------------------------------------------------------------
# _account_state_job() -- reconciliation + snapshot refresh, deliberately
# UNconditional (no NYSE-hours gate at all -- 2026-08-03 split). Both the
# genuinely-open and genuinely-closed cases must behave identically, proving
# there is no hidden gate anywhere in this job's own body.
# ---------------------------------------------------------------------------

def test_account_state_job_runs_when_nyse_open(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_reconcile = mocker.patch("brokers.trading212.reconcile_positions_with_broker")
    # Explicit return_value (2026-08-09) -- an unconfigured MagicMock is
    # truthy, which would make _account_state_job()'s new "only refresh the
    # cards snapshot when the positions snapshot genuinely came back fresh"
    # branch fire unpredictably. Fixed False here since this test's own
    # purpose (reconciliation + snapshot-refresh get called) doesn't need
    # the cards-refresh branch at all -- see the dedicated tests below for
    # that behaviour, including db_engine (added here too, belt-and-
    # suspenders, so a real DB write can never land outside the in-memory
    # test database even if this mock's return value is ever changed).
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot", return_value=False)
    mock_cards = mocker.patch("data.scheduler._refresh_open_position_cards_snapshot")

    scheduler._account_state_job()

    mock_reconcile.assert_called_once()
    mock_snap.assert_called_once()
    mock_cards.assert_not_called()


def test_account_state_job_runs_when_nyse_genuinely_closed(db_engine, monkeypatch, mocker):
    """The decisive proof this job is unconditional: a real, verified
    NYSE-closed (weekend) moment must still run reconciliation and the
    snapshot refresh, unlike _market_hours_check_job() above."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 1, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mock_reconcile = mocker.patch("brokers.trading212.reconcile_positions_with_broker")
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot", return_value=False)
    mock_cards = mocker.patch("data.scheduler._refresh_open_position_cards_snapshot")

    scheduler._account_state_job()

    mock_reconcile.assert_called_once()
    mock_snap.assert_called_once()
    mock_cards.assert_not_called()


def test_account_state_job_reconciliation_failure_does_not_block_snapshot(db_engine, monkeypatch, mocker):
    """A reconciliation-check failure must not prevent the account snapshot
    refresh from still running that hour -- own try/except, same "one
    path's failure never blocks another's" convention every other call in
    this job already follows."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.reconcile_positions_with_broker", side_effect=RuntimeError("boom"))
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot", return_value=False)
    mock_cards = mocker.patch("data.scheduler._refresh_open_position_cards_snapshot")

    scheduler._account_state_job()  # must not raise

    mock_snap.assert_called_once()
    mock_cards.assert_not_called()


# ---------------------------------------------------------------------------
# _account_state_job() -> _refresh_open_position_cards_snapshot() (2026-08-09)
# -- see CLAUDE.md's "UI render-path blocking audit" follow-up, finding 4.
# The cards snapshot must only be (re)computed when the positions snapshot
# it depends on genuinely came back fresh THIS cycle -- otherwise the
# derived cards would carry a freshly-stamped "fetched_at" timestamp for
# underlying data that didn't actually change, misleading the Dashboard's
# own "as of" display.
# ---------------------------------------------------------------------------

def test_account_state_job_refreshes_cards_snapshot_when_positions_snapshot_is_fresh(
    db_engine, monkeypatch, mocker,
):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={"trading212_invest": {"enabled": True}})
    mocker.patch("brokers.trading212.reconcile_positions_with_broker")
    mocker.patch("data.scheduler._fetch_t212_snapshot", return_value=True)
    mock_cards = mocker.patch("data.scheduler._refresh_open_position_cards_snapshot")

    scheduler._account_state_job()

    mock_cards.assert_called_once_with({"trading212_invest": {"enabled": True}})


def test_account_state_job_skips_cards_snapshot_refresh_when_positions_snapshot_fetch_failed(
    db_engine, monkeypatch, mocker,
):
    """A failed/disabled/no-credentials T212 fetch this cycle must not
    trigger a cards-snapshot recompute at all -- get_brief_cards() would
    just read the same (now stale) t212_positions_snapshot again, and
    stamping a new fetched_at on that unchanged result would misrepresent
    how current the Dashboard's cards actually are."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.reconcile_positions_with_broker")
    mocker.patch("data.scheduler._fetch_t212_snapshot", return_value=False)
    mock_cards = mocker.patch("data.scheduler._refresh_open_position_cards_snapshot")

    scheduler._account_state_job()

    mock_cards.assert_not_called()


def test_account_state_job_cards_snapshot_failure_leaves_prior_stored_values_untouched(
    db_engine, monkeypatch, mocker,
):
    """"Only overwrite stored state on a confirmed result" -- if
    _refresh_open_position_cards_snapshot() itself raises, the job must not
    propagate the exception, and whatever was already stored in
    open_position_cards_snapshot/_fetched_at (a real prior success) must
    survive untouched, exactly like the crisis-status write's own
    established pattern."""
    from data.database import set_metadata, get_metadata_str
    set_metadata("open_position_cards_snapshot", '[{"ticker": "OLD"}]')
    set_metadata("open_position_cards_fetched_at", "2026-08-04T14:00:00")

    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4, hour=15, minute=0))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.reconcile_positions_with_broker")
    mocker.patch("data.scheduler._fetch_t212_snapshot", return_value=True)
    mocker.patch(
        "data.scheduler._refresh_open_position_cards_snapshot",
        side_effect=RuntimeError("boom"),
    )

    scheduler._account_state_job()  # must not raise

    assert get_metadata_str("open_position_cards_snapshot") == '[{"ticker": "OLD"}]'
    assert get_metadata_str("open_position_cards_fetched_at") == "2026-08-04T14:00:00"


def test_refresh_open_position_cards_snapshot_writes_result_and_timestamp(db_engine, mocker):
    from data.database import get_metadata_str
    import json as _json

    fake_cards = [{"ticker": "VOO", "status": "ON TRACK"}]
    mocker.patch("output.performance.get_brief_cards", return_value=fake_cards)

    scheduler._refresh_open_position_cards_snapshot({"trading212_invest": {"enabled": True}})

    assert _json.loads(get_metadata_str("open_position_cards_snapshot")) == fake_cards
    assert get_metadata_str("open_position_cards_fetched_at") is not None


def test_refresh_open_position_cards_snapshot_writes_empty_list_as_a_confirmed_result(db_engine, mocker):
    """A confirmed-clean cycle (get_brief_cards() genuinely returns []) IS
    written, not treated as "unknown" -- see the function's own docstring
    for why this differs from the ambiguous no-credentials case."""
    from data.database import get_metadata_str
    mocker.patch("output.performance.get_brief_cards", return_value=[])

    scheduler._refresh_open_position_cards_snapshot({})

    assert get_metadata_str("open_position_cards_snapshot") == "[]"
    assert get_metadata_str("open_position_cards_fetched_at") is not None


# ---------------------------------------------------------------------------
# Registration -- both jobs must genuinely carry no hour= or day_of_week=
# cron filter at all (field == "*" on both), the load-bearing part of the
# "fires unconditionally, decides internally" design: if either job were
# still cron-filtered, it would never even wake up to run its own internal
# gate on the days/hours the filter excluded.
#
# account_state_refresh fires at minute=5, not minute=0 like its sibling
# (2026-08-03 fix) -- registering both at the identical minute let
# APScheduler run their T212 API calls concurrently, and on their first
# joint firing in production this genuinely exhausted T212's rate limit,
# causing maybe_exit_positions() to give up after 3 retries and skip the
# exit check for that whole cycle. See CLAUDE.md's investigation of the
# resulting "T212 request failed after 3 attempts" alert.
# ---------------------------------------------------------------------------

def test_market_hours_check_and_account_state_registered_hourly_no_cron_filter(monkeypatch):
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

        mh_job = sched.get_job("market_hours_check")
        assert mh_job is not None
        assert mh_job.func is scheduler._market_hours_check_job
        assert field("market_hours_check", "day_of_week") == "*"
        assert field("market_hours_check", "hour") == "*"
        assert field("market_hours_check", "minute") == "0"

        as_job = sched.get_job("account_state_refresh")
        assert as_job is not None
        assert as_job.func is scheduler._account_state_job
        assert field("account_state_refresh", "day_of_week") == "*"
        assert field("account_state_refresh", "hour") == "*"
        assert field("account_state_refresh", "minute") == "5"

        assert not hasattr(scheduler, "_trade_alert_job")
    finally:
        scheduler.stop_scheduler()


def test_market_hours_check_and_account_state_do_not_share_a_cron_minute(monkeypatch):
    """Direct regression guard for the 2026-08-03 rate-limit collision: both
    jobs' T212 API calls firing at the identical wall-clock minute is what
    caused the real production incident (both registered at minute=0 raced
    for T212's rate limit, and maybe_exit_positions() lost). Confirms the
    two jobs' minute fields are genuinely different, not just that
    account_state_refresh happens to equal "5" (which the test above
    already checks) -- this is the actual invariant that matters."""
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
            return next(str(f) for f in job.trigger.fields if f.name == name)

        mh_minute = field("market_hours_check", "minute")
        as_minute = field("account_state_refresh", "minute")
        assert mh_minute != as_minute, (
            "market_hours_check and account_state_refresh must not share a "
            "cron minute -- concurrent T212 API calls at the same minute "
            "caused a real production rate-limit failure (2026-08-03)."
        )
    finally:
        scheduler.stop_scheduler()


def test_market_hours_check_trigger_actually_fires_on_a_real_weekend_date(monkeypatch):
    """Confirms the registration itself, not just the field values -- the
    trigger's own computed next-fire-time must land on a genuine Saturday
    hour, proving the job gets the chance to wake up and decide for itself
    (via _is_nyse_open_now()) rather than being excluded by a cron filter
    before it ever runs."""
    fake_config = {"scheduler": {"timezone": "UTC", "runs": {
        "eod": {"hour": 21, "minute": 30, "label": "End of Day", "type": "full"},
    }}}
    monkeypatch.setattr(scheduler, "_load_config", lambda: fake_config)
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    sched = scheduler.start_scheduler()
    try:
        job = sched.get_job("market_hours_check")
        assert job is not None
        just_after_friday_midnight = datetime(2026, 7, 31, 23, 1, tzinfo=timezone.utc)
        next_fire = job.trigger.get_next_fire_time(None, just_after_friday_midnight)
        assert next_fire is not None
        assert next_fire.date() == datetime(2026, 8, 1).date()  # Saturday
    finally:
        scheduler.stop_scheduler()


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
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
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
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    mock_snap.assert_called_once()
    _, kwargs = mock_snap.call_args
    assert kwargs.get("record_history") is True


# ---------------------------------------------------------------------------
# _record_eod_completion() / get_eod_completion_status() -- a direct "did
# tonight's EOD job actually run to completion" signal (2026-08-02),
# distinct from the specific downstream alerts already built (instrument
# resolution failures, position reconciliation, membership staleness) and
# from the existing last_full_refresh-staleness-based OVERDUE badge, which
# only ever infers a problem from an old timestamp.
# ---------------------------------------------------------------------------

def test_record_and_read_eod_completion_round_trips(db_engine):
    from datetime import date as _date
    scheduler._record_eod_completion(
        "COMPLETED", _date(2026, 8, 4), price_pull_ran=True, order_pipeline_ran=True,
    )
    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["date"] == "2026-08-04"
    assert status["price_pull_ran"] is True
    assert status["order_pipeline_ran"] is True
    assert "recorded_at" in status


def test_get_eod_completion_status_none_when_never_recorded(db_engine):
    assert scheduler.get_eod_completion_status() is None


def test_record_eod_completion_never_raises_on_db_failure(db_engine, mocker):
    mocker.patch("data.scheduler.set_metadata", side_effect=RuntimeError("db down"))
    from datetime import date as _date
    scheduler._record_eod_completion("COMPLETED", _date(2026, 8, 4))  # must not raise


def test_eod_job_records_completed_on_the_normal_path(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))  # ordinary trading Tuesday
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["price_pull_ran"] is True
    assert status["order_pipeline_ran"] is True
    assert status["snapshot_ran"] is True


def test_eod_job_records_noop_on_a_non_trading_window(db_engine, monkeypatch, mocker):
    """A pathological >10-day closure -- neither today nor any of the next
    10 days is a trading day. Confirmed via a real, unmocked forward scan
    landing genuinely outside NYSE's actual holiday calendar depth by
    directly monkeypatching _has_upcoming_trading_day itself, since a real
    10-day NYSE gap essentially never occurs and isn't worth constructing."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    monkeypatch.setattr(scheduler, "_is_scheduled_trading_day", lambda d: False)
    monkeypatch.setattr(scheduler, "_has_upcoming_trading_day", lambda d: False)

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "NOOP_NOT_A_TRADING_WINDOW"


def test_eod_job_records_failed_when_pull_incremental_raises(db_engine, monkeypatch, mocker):
    """The core case this signal exists for: an unhandled exception escapes
    a step that was NOT individually wrapped in its own try/except at the
    _eod_job() level (pull_incremental() itself) -- before this feature,
    this would silently kill the job with no explicit record anywhere."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", side_effect=RuntimeError("yfinance is down"))
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mock_pipeline = mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "FAILED"
    assert "yfinance is down" in status["error"]
    assert status["price_pull_ran"] is False
    # The job must genuinely stop, not limp forward -- neither the order
    # pipeline nor the account snapshot should have run after the crash.
    mock_pipeline.assert_not_called()
    mock_snap.assert_not_called()


def test_eod_job_records_failed_when_order_pipeline_raises(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline", side_effect=RuntimeError("boom"))
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mock_snap = mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "FAILED"
    assert status["price_pull_ran"] is True  # the pull itself succeeded before the crash
    assert status["order_pipeline_ran"] is False
    mock_snap.assert_not_called()


def test_eod_job_completed_even_when_only_the_snapshot_step_fails(db_engine, monkeypatch, mocker):
    """The T212 snapshot fetch is already individually wrapped in its own
    try/except inside _eod_job() -- a failure there must still record an
    overall COMPLETED verdict (with snapshot_ran=False), not FAILED, since
    the actual trading-relevant work (price pull + order pipeline) did
    complete."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot", side_effect=RuntimeError("t212 api down"))

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["order_pipeline_ran"] is True
    assert status["snapshot_ran"] is False
    assert "t212 api down" in status["snapshot_error"]


# ---------------------------------------------------------------------------
# pending_orders purge wiring (2026-08-02) -- _eod_job() must call the
# real purge_old_pending_orders() every run, in its own try/except so a
# purge failure never blocks anything else, matching every other step here.
# ---------------------------------------------------------------------------

def test_eod_job_calls_the_real_pending_orders_purge(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mock_purge = mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=42)

    scheduler._eod_job()

    mock_purge.assert_called_once()
    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["pending_orders_purged"] == 42


def test_eod_job_completed_even_when_purge_fails(db_engine, monkeypatch, mocker):
    """A purge failure is routine, non-trading-relevant maintenance -- it
    must never turn an otherwise-successful EOD run into a FAILED verdict."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", side_effect=RuntimeError("boom"))

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["order_pipeline_ran"] is True
    assert "pending_orders_purged" not in status


# ---------------------------------------------------------------------------
# Price cliff monitoring backstop (2026-08-06) -- data.ingestion.
# detect_price_cliffs() / output.briefing.send_price_cliff_alert() run
# every EOD job, in their own try/except so a monitoring failure never
# blocks anything else, matching the pending_orders purge pattern above.
# ---------------------------------------------------------------------------

def test_eod_job_calls_price_cliff_monitor_and_records_hit_count(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=0)
    mock_detect = mocker.patch(
        "data.ingestion.detect_price_cliffs",
        return_value=[{"ticker": "X", "date": "2026-01-02", "date_prev": "2026-01-01",
                        "close": 1.0, "close_prev": 4.0, "ratio": 0.25}],
    )
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["X"])
    mocker.patch("brokers.trading212.get_held_research_tickers", return_value=[])
    mock_alert = mocker.patch("output.briefing.send_price_cliff_alert", return_value=(False, "disabled"))

    scheduler._eod_job()

    mock_detect.assert_called_once()
    mock_alert.assert_called_once()
    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["price_cliff_hits"] == 1
    assert status["price_cliff_hits_total"] == 1


def test_eod_job_price_cliff_alert_excludes_non_live_non_held_tickers(db_engine, monkeypatch, mocker):
    """detect_price_cliffs() scans the full price_data table, which includes
    ~700 historical/delisted tickers that can never be traded or held
    (2026-08-06 ingestion broadening) -- a cliff hit there is backtest-data
    housekeeping, not a live-money risk, and must not be emailed about or
    counted in the alert-worthy price_cliff_hits figure. Only a hit whose
    ticker is a current live candidate or a currently-held position should
    reach send_price_cliff_alert()."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=0)
    mocker.patch(
        "data.ingestion.detect_price_cliffs",
        return_value=[
            {"ticker": "LIVE", "date": "2026-01-02", "date_prev": "2026-01-01",
             "close": 1.0, "close_prev": 4.0, "ratio": 0.25},
            {"ticker": "DEAD", "date": "2026-01-02", "date_prev": "2026-01-01",
             "close": 1.0, "close_prev": 4.0, "ratio": 0.25},
        ],
    )
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["LIVE"])
    mocker.patch("brokers.trading212.get_held_research_tickers", return_value=[])
    mock_alert = mocker.patch("output.briefing.send_price_cliff_alert", return_value=(False, "disabled"))

    scheduler._eod_job()

    alerted_hits = mock_alert.call_args[0][0]
    assert [h["ticker"] for h in alerted_hits] == ["LIVE"]
    status = scheduler.get_eod_completion_status()
    assert status["price_cliff_hits"] == 1
    assert status["price_cliff_hits_total"] == 2


def test_eod_job_price_cliff_alert_excludes_known_exceptions(db_engine, monkeypatch, mocker):
    """A hit matching one of the 14 individually-classified (ticker, date)
    pairs from research/split_cliff_remediation_2026-08-08/ (2026-08-08)
    must not reach send_price_cliff_alert(), even though it's both live-
    scoped and a genuine detector hit -- and the suppressed count must be
    recorded separately from price_cliff_hits, not silently folded in."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=0)
    mocker.patch(
        "data.ingestion.detect_price_cliffs",
        return_value=[
            {"ticker": "AIG", "date": "2008-09-15", "date_prev": "2008-09-12",
             "close": 95.20, "close_prev": 242.80, "ratio": 0.392},  # known exception
            {"ticker": "NEWCO", "date": "2026-01-02", "date_prev": "2026-01-01",
             "close": 1.0, "close_prev": 4.0, "ratio": 0.25},  # not a known exception
        ],
    )
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AIG", "NEWCO"])
    mocker.patch("brokers.trading212.get_held_research_tickers", return_value=[])
    mock_alert = mocker.patch("output.briefing.send_price_cliff_alert", return_value=(False, "disabled"))

    scheduler._eod_job()

    alerted_hits = mock_alert.call_args[0][0]
    assert [h["ticker"] for h in alerted_hits] == ["NEWCO"]
    status = scheduler.get_eod_completion_status()
    assert status["price_cliff_hits"] == 2  # live-scoped count, unaffected by exception suppression
    assert status["price_cliff_hits_total"] == 2
    assert status["price_cliff_hits_known_exceptions"] == 1


def test_eod_job_completed_even_when_price_cliff_monitor_fails(db_engine, monkeypatch, mocker):
    """A monitoring failure is not trading-relevant -- it must never turn
    an otherwise-successful EOD run into a FAILED verdict, same as a purge
    failure above."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=0)
    mocker.patch("data.ingestion.detect_price_cliffs", side_effect=RuntimeError("boom"))
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["order_pipeline_ran"] is True
    assert "price_cliff_hits" not in status


# ---------------------------------------------------------------------------
# Ticker data-quality backstop scan (2026-08-07) -- data.ingestion.
# scan_unverified_tickers() / output.briefing.send_data_quality_scan_alert()
# run every EOD job, in their own try/except immediately after the price-
# cliff backstop above, matching its exact isolation pattern -- a
# monitoring failure must never block anything else in this job.
# ---------------------------------------------------------------------------

def test_eod_job_calls_data_quality_scan_and_records_counts(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=0)
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mock_scan = mocker.patch(
        "data.ingestion.scan_unverified_tickers",
        return_value={
            "checked": 3, "remaining": 457,
            "newly_quarantined": ["BADCO"], "newly_needs_review": ["MAYBECO"], "errors": [],
        },
    )
    mock_alert = mocker.patch("output.briefing.send_data_quality_scan_alert", return_value=(False, "disabled"))

    scheduler._eod_job()

    mock_scan.assert_called_once()
    mock_alert.assert_called_once()
    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["data_quality_checked"] == 3
    assert status["data_quality_remaining"] == 457
    assert status["data_quality_newly_quarantined"] == 1
    assert status["data_quality_newly_needs_review"] == 1


def test_eod_job_completed_even_when_data_quality_scan_fails(db_engine, monkeypatch, mocker):
    """A monitoring failure is not trading-relevant -- it must never turn
    an otherwise-successful EOD run into a FAILED verdict, same as the
    price-cliff monitor and purge failures above."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")
    mocker.patch("brokers.trading212.purge_old_pending_orders", return_value=0)
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", side_effect=RuntimeError("boom"))

    scheduler._eod_job()

    status = scheduler.get_eod_completion_status()
    assert status["status"] == "COMPLETED"
    assert status["order_pipeline_ran"] is True
    assert "data_quality_checked" not in status
    # 2026-08-08 fix: a scan failure must now be VISIBLE in the completion
    # record, not merely absent from it -- absence-on-failure and absence-
    # because-it-never-ran-at-all were indistinguishable before this,
    # which is exactly how the real backstop's own silent failure went
    # undetected for two full EOD cycles in production.
    assert status["data_quality_scan_error"] == "boom"


# ---------------------------------------------------------------------------
# _mark_job_started() / _mark_job_finished() / get_job_in_progress_status()
# -- real-time "is EOD (or its catch-up sibling) genuinely still running"
# detection (2026-08-05), used by deploy.sh (via check_eod_job_in_progress.py)
# to defer a deploy on the job's actual state rather than only a static
# wall-clock window. See CLAUDE.md's "GitHub webhook deploy" for the
# incident this closes: a deploy correctly deferred through the static
# EOD window, then hit its own wait cap and force-restarted
# investment-research-scheduler while that night's EOD job was still
# genuinely mid-run.
# ---------------------------------------------------------------------------

def test_mark_and_get_job_in_progress_status_round_trips(db_engine):
    scheduler._mark_job_started("eod")
    status = scheduler.get_job_in_progress_status()
    assert status["job"] == "eod"
    assert "started_at" in status
    assert status["elapsed_seconds"] >= 0


def test_get_job_in_progress_status_none_when_never_marked(db_engine):
    assert scheduler.get_job_in_progress_status() is None


def test_mark_job_finished_clears_the_marker(db_engine):
    scheduler._mark_job_started("eod")
    assert scheduler.get_job_in_progress_status() is not None

    scheduler._mark_job_finished()

    assert scheduler.get_job_in_progress_status() is None


def test_mark_job_started_never_raises_on_db_failure(db_engine, mocker):
    mocker.patch("data.scheduler.set_metadata", side_effect=RuntimeError("db down"))
    scheduler._mark_job_started("eod")  # must not raise


def test_mark_job_finished_never_raises_on_db_failure(db_engine, mocker):
    mocker.patch("data.scheduler.clear_metadata", side_effect=RuntimeError("db down"))
    scheduler._mark_job_finished()  # must not raise


def test_get_job_in_progress_status_none_on_malformed_payload(db_engine):
    """A corrupt/partial payload (e.g. a future schema change dropping a
    field) must read back as "nothing in progress", not raise -- this
    function's own contract is "never raises", matching every other
    metadata-read helper in this module."""
    scheduler.set_metadata(scheduler._JOB_IN_PROGRESS_KEY, "not valid json")
    assert scheduler.get_job_in_progress_status() is None


def test_eod_job_marks_in_progress_before_the_price_pull(db_engine, monkeypatch, mocker):
    """The marker must be set BEFORE the real work starts, not after --
    otherwise a deploy racing the very start of the job could still slip
    through. Verified by checking the marker's state from inside a faked
    pull_incremental(), the very first real-work step _eod_job() calls."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})

    seen_status_during_pull: list = []

    def fake_pull(tickers):
        seen_status_during_pull.append(scheduler.get_job_in_progress_status())
        return {"^GSPC": 1}

    mocker.patch("data.scheduler.pull_incremental", side_effect=fake_pull)
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    assert len(seen_status_during_pull) == 1
    assert seen_status_during_pull[0] is not None
    assert seen_status_during_pull[0]["job"] == "eod"


def test_eod_job_clears_in_progress_marker_on_success(db_engine, monkeypatch, mocker):
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", return_value={"^GSPC": 1})
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    assert scheduler.get_eod_completion_status()["status"] == "COMPLETED"
    assert scheduler.get_job_in_progress_status() is None


def test_eod_job_clears_in_progress_marker_on_failure(db_engine, monkeypatch, mocker):
    """The marker must be cleared via the try/finally even when the job
    raises -- a FAILED run must not leave deploy.sh believing EOD is
    still running for the rest of the JOB_IN_PROGRESS_STALE_AFTER_SECONDS
    window."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("data.scheduler._build_ticker_exchange_map", return_value={"^GSPC": "NYSE"})
    mocker.patch("data.scheduler.pull_incremental", side_effect=RuntimeError("yfinance is down"))
    mocker.patch("data.ingestion.detect_price_cliffs", return_value=[])
    mocker.patch("data.ingestion.scan_unverified_tickers", return_value={
        "checked": 0, "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    })
    mocker.patch("data.scheduler._run_analysis_pipeline")
    mocker.patch("data.scheduler.get_all_tickers", return_value=["^GSPC"])
    mocker.patch("data.scheduler._fetch_t212_snapshot")

    scheduler._eod_job()

    assert scheduler.get_eod_completion_status()["status"] == "FAILED"
    assert scheduler.get_job_in_progress_status() is None


def test_eod_job_noop_path_never_marks_in_progress(db_engine, monkeypatch):
    """The pathological >10-day-closure no-op path returns before any real
    work -- it must never leave a stray in-progress marker for deploy.sh
    to find (there's nothing running to protect)."""
    monkeypatch.setattr(scheduler, "datetime", _frozen_date_at(2026, 8, 4))
    monkeypatch.setattr(scheduler, "_is_scheduled_trading_day", lambda d: False)
    monkeypatch.setattr(scheduler, "_has_upcoming_trading_day", lambda d: False)

    scheduler._eod_job()

    assert scheduler.get_job_in_progress_status() is None
