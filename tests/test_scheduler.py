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
