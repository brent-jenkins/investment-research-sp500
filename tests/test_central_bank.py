"""Tests for BoJ/ECB central bank event monitoring — analysis/central_bank.py's
decision-day helpers, data/scheduler.py's _boj_reaction_job()/_ecb_reaction_job()
(which mirror the existing _fomc_reaction_job() pattern), and
output/briefing.py:send_central_bank_alert().

No DB access is needed anywhere in this file: analysis/central_bank.py reads
dates from a plain config dict (no DB/network), the scheduler job tests mock
every collaborator the same way tests/test_scheduler.py already does, and
send_central_bank_alert()'s collaborators (get_current_signal,
get_latest_regime_name, get_stored_correlations, _smtp_send) are all mocked.
"""

from datetime import date, datetime, timezone

import data.scheduler as scheduler
import output.briefing as briefing
from analysis.central_bank import (
    get_central_bank_events,
    is_boj_decision_day,
    is_ecb_decision_day,
)

_CFG = {
    "central_bank_events": {
        "boj": {"label": "Bank of Japan Policy Decision",
                "dates": ["2026-07-31", "2026-09-19"]},
        "ecb": {"label": "ECB Monetary Policy Decision",
                "dates": ["2026-09-11", "2026-10-30"]},
    }
}


# ---------------------------------------------------------------------------
# analysis/central_bank.py — pure decision-day helpers
# ---------------------------------------------------------------------------

def test_is_boj_decision_day_true_on_configured_date():
    assert is_boj_decision_day(_CFG, check_date=date(2026, 7, 31)) is True


def test_is_boj_decision_day_false_on_other_date():
    assert is_boj_decision_day(_CFG, check_date=date(2026, 8, 1)) is False


def test_is_ecb_decision_day_true_on_configured_date():
    assert is_ecb_decision_day(_CFG, check_date=date(2026, 9, 11)) is True


def test_is_ecb_decision_day_false_on_other_date():
    assert is_ecb_decision_day(_CFG, check_date=date(2026, 9, 12)) is False


def test_is_ecb_decision_day_false_when_section_missing():
    assert is_ecb_decision_day({"central_bank_events": {}}) is False


def test_is_boj_decision_day_false_when_config_missing_entirely():
    assert is_boj_decision_day({}) is False


def test_unparseable_dates_are_skipped_not_raised():
    cfg = {"central_bank_events": {"boj": {"dates": ["not-a-date", "2026-07-31"]}}}
    assert is_boj_decision_day(cfg, check_date=date(2026, 7, 31)) is True
    assert is_boj_decision_day(cfg, check_date=date(2099, 1, 1)) is False


def _frozen_datetime(year, month, day):
    fixed = datetime(year, month, day, 12, 0, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz) if tz else fixed

    return _Frozen


def test_get_central_bank_events_merges_both_banks_sorted_within_window(monkeypatch):
    import analysis.central_bank as cb_mod
    monkeypatch.setattr(cb_mod, "datetime", _frozen_datetime(2026, 7, 28))

    cfg = {
        "central_bank_events": {
            "boj": {"dates": ["2026-07-31"]},          # 3 days away — in window
            "ecb": {"dates": ["2026-09-11"]},          # far away — out of window
        }
    }
    events = get_central_bank_events(cfg, days_ahead=7)

    assert len(events) == 1
    assert events[0]["event_type"] == "BoJ Decision"
    assert events[0]["days_away"] == 3
    assert events[0]["date"] == date(2026, 7, 31)


def test_get_central_bank_events_empty_when_no_dates_in_window(monkeypatch):
    import analysis.central_bank as cb_mod
    monkeypatch.setattr(cb_mod, "datetime", _frozen_datetime(2026, 1, 1))

    assert get_central_bank_events(_CFG, days_ahead=7) == []


# ---------------------------------------------------------------------------
# data/scheduler.py — _boj_reaction_job() / _ecb_reaction_job()
# ---------------------------------------------------------------------------

def _patch_pipeline(monkeypatch, calls):
    monkeypatch.setattr(scheduler, "get_all_tickers", lambda config: ["AAPL"])

    def fake_pipeline(config, tickers, run_type):
        calls.append((config, tickers, run_type))

    monkeypatch.setattr(scheduler, "_run_analysis_pipeline", fake_pipeline)


def test_boj_reaction_job_noop_on_non_decision_day(monkeypatch):
    import analysis.central_bank as cb_mod
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(scheduler, "_load_config", lambda: _CFG)
    monkeypatch.setattr(cb_mod, "is_boj_decision_day", lambda config: False)

    scheduler._boj_reaction_job()

    assert calls == []


def test_boj_reaction_job_runs_pipeline_and_sends_alert_on_decision_day(monkeypatch):
    import analysis.central_bank as cb_mod
    calls: list = []
    alert_calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(scheduler, "_load_config", lambda: _CFG)
    monkeypatch.setattr(cb_mod, "is_boj_decision_day", lambda config: True)

    def fake_alert(**kwargs):
        alert_calls.append(kwargs)
        return True, "sent"

    monkeypatch.setattr(briefing, "send_central_bank_alert", fake_alert)

    scheduler._boj_reaction_job()

    assert len(calls) == 1
    assert calls[0][2] == scheduler.RUN_ANALYSIS_ONLY
    assert len(alert_calls) == 1
    assert alert_calls[0]["bank"] == "BoJ"
    assert "USDJPY=X" in alert_calls[0]["watch_tickers"]


def test_ecb_reaction_job_noop_on_non_decision_day(monkeypatch):
    import analysis.central_bank as cb_mod
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(scheduler, "_load_config", lambda: _CFG)
    monkeypatch.setattr(cb_mod, "is_ecb_decision_day", lambda config: False)

    scheduler._ecb_reaction_job()

    assert calls == []


def test_ecb_reaction_job_runs_pipeline_and_sends_alert_on_decision_day(monkeypatch):
    import analysis.central_bank as cb_mod
    calls: list = []
    alert_calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(scheduler, "_load_config", lambda: _CFG)
    monkeypatch.setattr(cb_mod, "is_ecb_decision_day", lambda config: True)

    def fake_alert(**kwargs):
        alert_calls.append(kwargs)
        return True, "sent"

    monkeypatch.setattr(briefing, "send_central_bank_alert", fake_alert)

    scheduler._ecb_reaction_job()

    assert len(calls) == 1
    assert calls[0][2] == scheduler.RUN_ANALYSIS_ONLY
    assert len(alert_calls) == 1
    assert alert_calls[0]["bank"] == "ECB"
    assert ("VGK", "ECBDFR") in alert_calls[0]["correlation_pairs"]


def test_boj_reaction_job_survives_alert_email_failure(monkeypatch):
    """A broken email send must never prevent the reaction job from completing
    (matches _trade_alert_job()'s VIX-spike-check try/except-per-step style)."""
    import analysis.central_bank as cb_mod
    calls: list = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(scheduler, "_load_config", lambda: _CFG)
    monkeypatch.setattr(cb_mod, "is_boj_decision_day", lambda config: True)

    def broken_alert(**kwargs):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(briefing, "send_central_bank_alert", broken_alert)

    scheduler._boj_reaction_job()  # must not raise

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# output/briefing.py:send_central_bank_alert()
# ---------------------------------------------------------------------------

def test_send_central_bank_alert_returns_false_when_email_disabled():
    config = {"notifications": {"email": {"enabled": False}}}
    ok, msg = briefing.send_central_bank_alert(
        bank="BoJ", label="Bank of Japan Policy Decision",
        watch_tickers=["USDJPY=X"], correlation_pairs=[("USDJPY=X", "^GSPC")],
        context="test context", config=config,
    )
    assert ok is False
    assert "not enabled" in msg


def test_send_central_bank_alert_happy_path(monkeypatch):
    config = {
        "notifications": {"email": {
            "enabled": True, "to_addresses": ["x@example.com"],
        }},
    }
    monkeypatch.setattr(briefing, "get_latest_regime_name", lambda: "TRENDING_BULL")
    monkeypatch.setattr(
        briefing, "get_current_signal",
        lambda t: {"signal": "BUY", "confluence_score": 0.42},
    )

    import analysis.correlations as corr_mod
    monkeypatch.setattr(
        corr_mod, "get_stored_correlations",
        lambda: [{
            "ticker_a": "USDJPY=X", "ticker_b": "^GSPC",
            "label": "USD/JPY vs S&P 500", "z_score": 1.8, "divergence": "DIVERGING",
        }],
    )

    sent: dict = {}

    def fake_smtp_send(subject, text_body, html_body, config):
        sent["subject"] = subject
        sent["text_body"] = text_body
        sent["html_body"] = html_body
        return True, "Sent to 1 recipient(s)"

    monkeypatch.setattr(briefing, "_smtp_send", fake_smtp_send)

    ok, msg = briefing.send_central_bank_alert(
        bank="BoJ", label="Bank of Japan Policy Decision",
        watch_tickers=["USDJPY=X"], correlation_pairs=[("USDJPY=X", "^GSPC")],
        context="Watch USDJPY for carry-trade stress.", config=config,
    )

    assert ok is True
    assert "BoJ" in msg
    assert "Bank of Japan Policy Decision" in sent["subject"]
    assert "TRENDING BULL" in sent["text_body"]
    assert "USDJPY=X" in sent["text_body"]
    assert "USD/JPY vs S&P 500" in sent["text_body"]
    assert "DIVERGING" in sent["text_body"]
    assert "Watch USDJPY for carry-trade stress." in sent["text_body"]


def test_send_central_bank_alert_survives_correlation_lookup_failure(monkeypatch):
    """A broken correlation lookup must not prevent the email from sending —
    it should just omit the correlation-pair rows."""
    config = {
        "notifications": {"email": {
            "enabled": True, "to_addresses": ["x@example.com"],
        }},
    }
    monkeypatch.setattr(briefing, "get_latest_regime_name", lambda: "MIXED")
    monkeypatch.setattr(briefing, "get_current_signal", lambda t: {})

    import analysis.correlations as corr_mod

    def broken_lookup():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(corr_mod, "get_stored_correlations", broken_lookup)

    sent: dict = {}

    def fake_smtp_send(subject, text_body, html_body, config):
        sent["text_body"] = text_body
        return True, "Sent to 1 recipient(s)"

    monkeypatch.setattr(briefing, "_smtp_send", fake_smtp_send)

    ok, _ = briefing.send_central_bank_alert(
        bank="ECB", label="ECB Monetary Policy Decision",
        watch_tickers=["VGK"], correlation_pairs=[("VGK", "ECBDFR")],
        context="ECB context.", config=config,
    )

    assert ok is True
    assert "Correlation pairs" not in sent["text_body"]
