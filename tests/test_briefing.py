"""Tests for output/briefing.py: _smtp_send() (the sole surviving,
shared low-level email helper -- see the module's own docstring for why
everything else was removed along with V1) and the weekly summary email
(_gather_weekly_data() / send_weekly_summary_email()), the one piece of
the original V1 scope with a genuine V2 equivalent.
"""
from datetime import datetime, timedelta

import pytest

from data.database import set_metadata
from output.briefing import (
    _gather_weekly_data,
    _smtp_send,
    _weekly_summary_html,
    _weekly_summary_text,
    send_instrument_resolution_failure_alert,
    send_membership_staleness_alert,
    send_position_reconciliation_alert,
    send_weekly_summary_email,
)


def _config(enabled=True, to_addresses=None):
    return {
        "notifications": {
            "email": {
                "enabled": enabled,
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_user": "user@example.com",
                "smtp_password": "secret",
                "from_address": "hello@example.com",
                "to_addresses": to_addresses if to_addresses is not None else ["alerts@example.com"],
            },
        },
    }


def test_smtp_send_disabled_never_connects(mocker):
    """notifications.email.enabled=false must skip sending entirely --
    no SMTP connection attempt at all, not just a swallowed failure."""
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, detail = _smtp_send("Subject", "text", "<p>html</p>", _config(enabled=False))

    assert sent is False
    assert "disabled" in detail.lower()
    smtp_cls.assert_not_called()


def test_smtp_send_enabled_attempts_delivery(mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, detail = _smtp_send("Subject", "text", "<p>html</p>", _config(enabled=True))

    assert sent is True
    smtp_cls.assert_called_once()
    mock_server.login.assert_called_once()
    mock_server.sendmail.assert_called_once()


def test_smtp_send_no_recipients_fails_without_connecting(mocker):
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, detail = _smtp_send("Subject", "text", "<p>html</p>", _config(to_addresses=[]))

    assert sent is False
    smtp_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Weekly summary -- content gathering, tone-neutrality, and the separate
# trigger path (deliberately not entangled with brokers/trading212.py's
# error-alert cooldown -- see output/briefing.py's own module-level note).
# ---------------------------------------------------------------------------

def _open_card(ticker, entry_date, sig_on, unreal_pnl_gbp):
    return {
        "ticker": ticker, "entry_date": entry_date, "current_sig_on": sig_on,
        "unreal_pnl_gbp": unreal_pnl_gbp, "status": "ON TRACK", "status_priority": 0,
    }


def _closed_row(ticker, exit_date, exit_reason, pnl_gbp):
    return {"ticker": ticker, "exit_date": exit_date, "exit_reason": exit_reason, "pnl_gbp": pnl_gbp}


def test_gather_weekly_data_defaults_to_7_day_window_with_no_prior_summary(db_engine, mocker):
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert abs((data["generated_at"] - data["window_start"]) - timedelta(days=7)) < timedelta(seconds=5)


def test_gather_weekly_data_uses_last_summary_timestamp_as_window_start(db_engine, mocker):
    last_sent = datetime.utcnow() - timedelta(days=3)
    set_metadata("last_weekly_summary_sent", last_sent.isoformat())
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert abs(data["window_start"] - last_sent) < timedelta(seconds=1)


def test_gather_weekly_data_filters_closed_positions_to_the_window(db_engine, mocker):
    last_sent = datetime.utcnow() - timedelta(days=7)
    set_metadata("last_weekly_summary_sent", last_sent.isoformat())
    inside = (datetime.utcnow() - timedelta(days=2)).isoformat()
    outside = (datetime.utcnow() - timedelta(days=10)).isoformat()
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[
        _closed_row("AAPL", inside, "STOP_LOSS", -40.0),
        _closed_row("MSFT", outside, "MAX_HOLDING", 100.0),
    ])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert [r["ticker"] for r in data["closed_this_week"]] == ["AAPL"]
    assert data["realized_pnl_gbp"] == -40.0


def test_gather_weekly_data_total_pnl_is_realised_plus_unrealised(db_engine, mocker):
    mocker.patch("output.performance.get_brief_cards", return_value=[
        _open_card("VOO", "2024-01-01T00:00:00", True, 25.0),
        _open_card("XLK", "2024-02-01T00:00:00", False, -10.0),
    ])
    mocker.patch("output.performance.get_pnl_rows", return_value=[
        _closed_row("AAPL", datetime.utcnow().isoformat(), "SIGNAL_OFF", 60.0),
    ])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert data["unrealized_pnl_gbp"] == pytest.approx(15.0)
    assert data["realized_pnl_gbp"] == pytest.approx(60.0)
    assert data["total_pnl_gbp"] == pytest.approx(75.0)


def test_gather_weekly_data_account_total_from_snapshot(db_engine, mocker):
    import json
    set_metadata("t212_account_snapshot", json.dumps({"totalValue": 54321.99}))
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert data["total_value_gbp"] == pytest.approx(54321.99)


def test_gather_weekly_data_no_snapshot_reports_not_available(db_engine, mocker):
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert data["total_value_gbp"] is None


def test_gather_weekly_data_crisis_fired_this_week_named_plainly(db_engine, mocker):
    import pandas as pd

    dates = pd.date_range(end=datetime.utcnow().date(), periods=30, freq="D")
    closes = [100.0] * (len(dates) - 3) + [100.0, 92.0, 93.0]  # a >=7% single-day drop 2 days ago
    price_df = pd.DataFrame({"date": dates, "close": closes})

    set_metadata("last_weekly_summary_sent", (datetime.utcnow() - timedelta(days=7)).isoformat())
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=price_df)

    data = _gather_weekly_data({})

    assert len(data["crisis_fired_dates"]) == 1


def test_gather_weekly_data_crisis_did_not_fire_stated_plainly_not_omitted(db_engine, mocker):
    """Absence of a crisis this week must still produce an explicit,
    checkable field -- not silence."""
    import pandas as pd

    dates = pd.date_range(end=datetime.utcnow().date(), periods=30, freq="D")
    price_df = pd.DataFrame({"date": dates, "close": [100.0] * len(dates)})

    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=price_df)

    data = _gather_weekly_data({})

    assert data["crisis_fired_dates"] == []
    text = _weekly_summary_text(data)
    assert "did not fire this week" in text


def test_gather_weekly_data_unresolved_error_checked_under_both_keys(db_engine, mocker):
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    set_metadata("t212_invest_error", "snapshot fetch failed")
    data = _gather_weekly_data({})
    assert data["unresolved_error"] == "snapshot fetch failed"


# ---------------------------------------------------------------------------
# Tone -- same standard as the Daily Briefing / Performance page rebuild:
# no colour-coded urgency, no ranking by risk or performance, no
# call-to-action language, regardless of whether a position or the week
# made or lost money.
# ---------------------------------------------------------------------------

_FORBIDDEN_WORDS = ["risk", "danger", "urgent", "warning", "review", "click", "consider"]


def _sample_weekly_data(pnl_sign: float) -> dict:
    return {
        "generated_at": datetime.utcnow(),
        "window_start": datetime.utcnow() - timedelta(days=7),
        "open_positions": [
            _open_card("VOO", "2024-01-01T00:00:00", True, 250.0 * pnl_sign),
            _open_card("XLK", "2024-02-01T00:00:00", False, -75.0 * pnl_sign),
        ],
        "total_value_gbp": 55000.0,
        "closed_this_week": [
            _closed_row("AAPL", datetime.utcnow().isoformat(), "STOP_LOSS", -40.0 * pnl_sign),
            _closed_row("MSFT", datetime.utcnow().isoformat(), "MAX_HOLDING", 90.0 * pnl_sign),
        ],
        "realized_pnl_gbp": 50.0 * pnl_sign,
        "unrealized_pnl_gbp": 175.0 * pnl_sign,
        "total_pnl_gbp": 225.0 * pnl_sign,
        "crisis_fired_dates": [],
        "last_full_refresh": datetime.utcnow(),
        "eod_recent": True,
        "unresolved_error": "",
    }


def test_weekly_summary_text_has_no_forbidden_tone_language():
    text = _weekly_summary_text(_sample_weekly_data(pnl_sign=1.0)).lower()
    for word in _FORBIDDEN_WORDS:
        assert word not in text, f"forbidden word '{word}' found in weekly summary text"


def test_weekly_summary_html_has_no_forbidden_tone_language():
    html = _weekly_summary_html(_sample_weekly_data(pnl_sign=1.0)).lower()
    for word in _FORBIDDEN_WORDS:
        assert word not in html, f"forbidden word '{word}' found in weekly summary html"


def test_weekly_summary_html_uses_one_colour_regardless_of_pnl_sign():
    """A losing week/position must render with the exact same styling as a
    winning one -- no conditional colour based on any figure's own sign."""
    html_positive = _weekly_summary_html(_sample_weekly_data(pnl_sign=1.0))
    html_negative = _weekly_summary_html(_sample_weekly_data(pnl_sign=-1.0))

    import re
    colours_positive = set(re.findall(r"color:(#[0-9a-fA-F]{3,6})", html_positive))
    colours_negative = set(re.findall(r"color:(#[0-9a-fA-F]{3,6})", html_negative))

    assert len(colours_positive) == 1
    assert colours_positive == colours_negative


def test_gather_weekly_data_open_positions_alphabetical_not_by_risk_or_pnl(db_engine, mocker):
    """_gather_weekly_data() -- not the render layer -- is what guarantees
    ordering (mirroring output/performance.py's own get_brief_cards()
    consumers), so this checks the actual sorted output rather than
    assuming the text builder re-sorts whatever it's handed."""
    mocker.patch("output.performance.get_brief_cards", return_value=[
        _open_card("ZAL", "2024-01-01T00:00:00", True, 999.0),    # best P&L, last alphabetically
        _open_card("AAA", "2024-01-01T00:00:00", False, -999.0),  # worst P&L, first alphabetically
    ])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    data = _gather_weekly_data({})

    assert [c["ticker"] for c in data["open_positions"]] == ["AAA", "ZAL"]


# ---------------------------------------------------------------------------
# Separate trigger path -- must never share state with the T212 error-alert
# cooldown (brokers/trading212.py's _T212_ERROR_EMAIL_LAST_SENT_KEY /
# notifications.email.alert_cooldown_hours).
# ---------------------------------------------------------------------------

def test_send_weekly_summary_sends_even_when_error_alert_cooldown_is_active(db_engine, mocker):
    """A T212 error-alert cooldown in effect must never suppress the
    weekly summary -- they are deliberately unrelated trigger paths."""
    set_metadata("t212_error_email_last_sent", datetime.utcnow().isoformat())  # error cooldown freshly used
    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, detail = send_weekly_summary_email(_config())

    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_send_weekly_summary_does_not_write_the_error_cooldown_key(db_engine, mocker):
    """A successful weekly send must never touch the T212 error-alert
    cooldown timestamp -- confirms the two paths share no state at all."""
    from data.database import get_metadata_str

    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    send_weekly_summary_email(_config())

    assert get_metadata_str("t212_error_email_last_sent") is None
    assert get_metadata_str("last_weekly_summary_sent") is not None


def test_send_weekly_summary_records_its_own_timestamp_only_on_success(db_engine, mocker):
    from data.database import get_metadata_str

    mocker.patch("output.performance.get_brief_cards", return_value=[])
    mocker.patch("output.performance.get_pnl_rows", return_value=[])
    mocker.patch("data.ingestion.get_price_df", return_value=__import__("pandas").DataFrame())

    # Disabled -- must not send, and must not record a timestamp.
    sent, _ = send_weekly_summary_email(_config(enabled=False))
    assert sent is False
    assert get_metadata_str("last_weekly_summary_sent") is None


# ---------------------------------------------------------------------------
# send_membership_staleness_alert() (2026-08-02) -- mirrors brokers/
# trading212.py:_send_t212_error_email()'s enabled-gate + own-dedicated-
# cooldown pattern exactly, but for the membership-refresh staleness alert
# (see analysis/universe.py's MEMBERSHIP_STALE_HOURS docstring for what
# triggers this). Must use its OWN cooldown key, never the T212 error
# alert's or the weekly summary's -- same reasoning as the weekly summary's
# own separate-trigger-path tests above.
# ---------------------------------------------------------------------------

_MEMBERSHIP_STALENESS_KEY = "membership_staleness_email_last_sent"


def test_membership_staleness_alert_disabled_skips_cleanly(db_engine, mocker):
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, detail = send_membership_staleness_alert(96.0, 48.0, _config(enabled=False))

    assert sent is False
    smtp_cls.assert_not_called()


def test_membership_staleness_alert_sends_on_first_occurrence(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_membership_staleness_alert(96.0, 48.0, _config())

    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_membership_staleness_alert_handles_infinite_age_never_recorded(db_engine, mocker):
    """last_success=None upstream -> age_hours=inf -- must render sensible
    copy, not crash on formatting an infinite float."""
    import email as _email_pkg

    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_membership_staleness_alert(float("inf"), 48.0, _config())

    assert sent is True
    raw = mock_server.sendmail.call_args[0][2]
    # The message's plain/html parts may be base64/quoted-printable encoded
    # (MIMEText auto-selects an encoding once it sees the non-ASCII em-dash
    # this alert's copy uses) -- decode via the real email parser rather
    # than substring-matching the raw wire format.
    msg = _email_pkg.message_from_string(raw)
    decoded_parts = [
        part.get_payload(decode=True).decode("utf-8")
        for part in msg.walk() if part.get_content_maintype() == "text"
    ]
    combined = " ".join(decoded_parts).lower()
    assert "never recorded" in combined or "unknown period" in combined


def test_membership_staleness_alert_second_occurrence_within_cooldown_is_skipped(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    cfg = _config()
    send_membership_staleness_alert(96.0, 48.0, cfg)
    mock_server.sendmail.reset_mock()

    send_membership_staleness_alert(97.0, 48.0, cfg)

    mock_server.sendmail.assert_not_called()


def test_membership_staleness_alert_sends_again_once_cooldown_has_elapsed(db_engine, mocker):
    from data.database import set_metadata as _set_metadata

    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    stale_last_sent = (datetime.utcnow() - timedelta(hours=9)).isoformat()
    _set_metadata(_MEMBERSHIP_STALENESS_KEY, stale_last_sent)

    cfg = _config()
    cfg["notifications"]["email"]["alert_cooldown_hours"] = 8
    send_membership_staleness_alert(200.0, 48.0, cfg)

    mock_server.sendmail.assert_called_once()


def test_membership_staleness_alert_disabled_does_not_consume_cooldown(db_engine, mocker):
    from data.database import get_metadata_str
    mocker.patch("output.briefing.smtplib.SMTP")

    send_membership_staleness_alert(96.0, 48.0, _config(enabled=False))

    assert get_metadata_str(_MEMBERSHIP_STALENESS_KEY) is None


def test_membership_staleness_alert_uses_its_own_cooldown_key_not_shared_with_others(db_engine, mocker):
    """A T212-error-alert cooldown or weekly-summary timestamp already in
    effect must never suppress this alert -- deliberately separate state,
    same principle as the weekly summary's own separate-trigger-path
    tests above."""
    set_metadata("t212_error_email_last_sent", datetime.utcnow().isoformat())
    set_metadata("last_weekly_summary_sent", datetime.utcnow().isoformat())
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_membership_staleness_alert(96.0, 48.0, _config())

    assert sent is True


# ---------------------------------------------------------------------------
# send_instrument_resolution_failure_alert() (2026-08-02, dedup fixed
# 2026-08-02) -- alerts when a genuinely tradeable candidate has a FAILED
# T212 instrument-ID resolution, so it doesn't just sit silently excluded
# in instrument_resolutions until someone happens to open the Status page.
# Same enabled-gating as send_membership_staleness_alert() above, but
# deliberately CONTENT-based deduplication (compares the current failed
# ticker set against the set actually emailed last time), not a
# time-based cooldown -- a time-based cooldown was found to let a second,
# genuinely different ticker's failure go completely unalerted for the
# length of the cooldown window if it occurred while an earlier failure's
# cooldown was still active. Its own dedicated metadata key,
# _INSTRUMENT_RESOLUTION_FAILURE_LAST_TICKERS_KEY, deliberately separate
# from every other alert's own state.
# ---------------------------------------------------------------------------

_INSTRUMENT_RESOLUTION_FAILURE_KEY = "instrument_resolution_failure_last_tickers"


def _failed_row(ticker="ZZZZ", note="no US-listed equity instrument found with shortName == ZZZZ"):
    return {"ticker": ticker, "note": note}


def test_instrument_resolution_failure_alert_empty_list_never_sends(db_engine, mocker):
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, detail = send_instrument_resolution_failure_alert([], _config())

    assert sent is False
    assert "no failed" in detail.lower()
    smtp_cls.assert_not_called()


def test_instrument_resolution_failure_alert_disabled_skips_cleanly(db_engine, mocker):
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, _ = send_instrument_resolution_failure_alert([_failed_row()], _config(enabled=False))

    assert sent is False
    smtp_cls.assert_not_called()


def test_instrument_resolution_failure_alert_sends_on_first_occurrence(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_instrument_resolution_failure_alert([_failed_row()], _config())

    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_instrument_resolution_failure_alert_names_the_ticker_and_note(db_engine, mocker):
    import email as _email_pkg

    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    send_instrument_resolution_failure_alert(
        [_failed_row(ticker="ZZZZ", note="ambiguous -- 2 US-listed equity instruments matched")],
        _config(),
    )

    raw = mock_server.sendmail.call_args[0][2]
    msg = _email_pkg.message_from_string(raw)
    decoded_parts = [
        part.get_payload(decode=True).decode("utf-8")
        for part in msg.walk() if part.get_content_maintype() == "text"
    ]
    combined = " ".join(decoded_parts)
    assert "ZZZZ" in combined
    assert "ambiguous" in combined


def test_instrument_resolution_failure_alert_two_concurrent_failures_both_in_one_alert(db_engine, mocker):
    """Two different tickers failing before the next check must both
    appear in the SAME alert, not just whichever triggered it first --
    direct proof of the aggregation half of this task, using real
    ticker/note content in the sent email, not just a row count."""
    import email as _email_pkg

    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_instrument_resolution_failure_alert(
        [
            _failed_row(ticker="AAAA", note="no match for AAAA"),
            _failed_row(ticker="BBBB", note="ambiguous for BBBB"),
        ],
        _config(),
    )

    assert sent is True
    mock_server.sendmail.assert_called_once()
    raw = mock_server.sendmail.call_args[0][2]
    msg = _email_pkg.message_from_string(raw)
    combined = " ".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in msg.walk() if part.get_content_maintype() == "text"
    )
    assert "AAAA" in combined and "no match for AAAA" in combined
    assert "BBBB" in combined and "ambiguous for BBBB" in combined


def test_instrument_resolution_failure_alert_unchanged_set_does_not_resend(db_engine, mocker):
    """The identical failed-ticker set across two consecutive checks must
    not trigger a repeat email -- content-based dedup, not a cooldown
    window, is what prevents this."""
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    cfg = _config()
    send_instrument_resolution_failure_alert([_failed_row(ticker="ZZZZ")], cfg)
    mock_server.sendmail.reset_mock()

    sent, detail = send_instrument_resolution_failure_alert([_failed_row(ticker="ZZZZ")], cfg)

    assert sent is False
    assert "unchanged" in detail.lower()
    mock_server.sendmail.assert_not_called()


def test_instrument_resolution_failure_alert_new_ticker_added_sends_immediately_no_cooldown_wait(db_engine, mocker):
    """The exact gap this task closes: a SECOND, different ticker failing
    immediately after the first (zero elapsed time -- no cooldown window
    could possibly have expired) must still trigger a fresh alert, since
    the failed set itself genuinely changed. A time-based cooldown would
    have suppressed this."""
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    cfg = _config()
    send_instrument_resolution_failure_alert([_failed_row(ticker="AAAA")], cfg)
    mock_server.sendmail.reset_mock()

    # Second check, same instant, but a genuinely NEW ticker also failed.
    sent, _ = send_instrument_resolution_failure_alert(
        [_failed_row(ticker="AAAA"), _failed_row(ticker="BBBB")], cfg,
    )

    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_instrument_resolution_failure_alert_resolution_then_new_failure_sends_fresh_alert(db_engine, mocker):
    """A failure resolving (set shrinks to empty) followed by a genuinely
    NEW failure -- even reusing the same ticker symbol -- must send a
    fresh alert, not be silently treated as an already-alerted repeat."""
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    cfg = _config()
    send_instrument_resolution_failure_alert([_failed_row(ticker="AAAA")], cfg)
    mock_server.sendmail.reset_mock()

    # Resolved -- empty list clears the stored set.
    sent, _ = send_instrument_resolution_failure_alert([], cfg)
    assert sent is False
    mock_server.sendmail.assert_not_called()

    # Same ticker fails again later -- must be treated as new, not compared
    # against the stale pre-resolution record.
    sent, _ = send_instrument_resolution_failure_alert([_failed_row(ticker="AAAA")], cfg)
    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_instrument_resolution_failure_alert_disabled_does_not_record_ticker_set(db_engine, mocker):
    mocker.patch("output.briefing.smtplib.SMTP")

    send_instrument_resolution_failure_alert([_failed_row()], _config(enabled=False))

    from data.database import get_metadata_str
    assert get_metadata_str(_INSTRUMENT_RESOLUTION_FAILURE_KEY) is None


def test_instrument_resolution_failure_alert_uses_its_own_dedup_key_not_shared_with_others(db_engine, mocker):
    """A membership-staleness or T212-error-alert cooldown already in
    effect must never suppress this alert."""
    set_metadata("t212_error_email_last_sent", datetime.utcnow().isoformat())
    set_metadata(_MEMBERSHIP_STALENESS_KEY, datetime.utcnow().isoformat())
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_instrument_resolution_failure_alert([_failed_row()], _config())

    assert sent is True
    mock_server.sendmail.assert_called_once()


# ---------------------------------------------------------------------------
# send_position_reconciliation_alert() (2026-08-02) -- alerts when
# brokers.trading212.reconcile_positions_with_broker() finds a discrepancy
# between this system's own order history and T212's real, live positions
# (a broker-closed position with no SELL from us, or an unrecognized held
# instrument). Same cooldown/enabled-gating shape as the two alerts above,
# deliberately its own separate cooldown key.
# ---------------------------------------------------------------------------

_POSITION_RECONCILIATION_KEY = "position_reconciliation_email_last_sent"


def _reconciliation_summary(broker_closed=None, unrecognized=None):
    return {
        "broker_closed_no_sell": broker_closed or [],
        "unrecognized_position": unrecognized or [],
    }


def test_position_reconciliation_alert_empty_summary_never_sends(db_engine, mocker):
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, detail = send_position_reconciliation_alert(_reconciliation_summary(), _config())

    assert sent is False
    assert "no discrepancies" in detail.lower()
    smtp_cls.assert_not_called()


def test_position_reconciliation_alert_disabled_skips_cleanly(db_engine, mocker):
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    sent, _ = send_position_reconciliation_alert(
        _reconciliation_summary(broker_closed=["ACQD"]), _config(enabled=False),
    )

    assert sent is False
    smtp_cls.assert_not_called()


def test_position_reconciliation_alert_sends_on_broker_closed(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_position_reconciliation_alert(
        _reconciliation_summary(broker_closed=["ACQD"]), _config(),
    )

    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_position_reconciliation_alert_sends_on_unrecognized(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_position_reconciliation_alert(
        _reconciliation_summary(unrecognized=["SWAPCO_US_EQ"]), _config(),
    )

    assert sent is True
    mock_server.sendmail.assert_called_once()


def test_position_reconciliation_alert_names_both_ticker_kinds(db_engine, mocker):
    import email as _email_pkg

    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    send_position_reconciliation_alert(
        _reconciliation_summary(broker_closed=["ACQD"], unrecognized=["SWAPCO_US_EQ"]), _config(),
    )

    raw = mock_server.sendmail.call_args[0][2]
    msg = _email_pkg.message_from_string(raw)
    decoded_parts = [
        part.get_payload(decode=True).decode("utf-8")
        for part in msg.walk() if part.get_content_maintype() == "text"
    ]
    combined = " ".join(decoded_parts)
    assert "ACQD" in combined
    assert "SWAPCO_US_EQ" in combined


def test_position_reconciliation_alert_second_occurrence_within_cooldown_is_skipped(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    cfg = _config()
    send_position_reconciliation_alert(_reconciliation_summary(broker_closed=["ACQD"]), cfg)
    mock_server.sendmail.reset_mock()

    send_position_reconciliation_alert(_reconciliation_summary(broker_closed=["ACQD"]), cfg)

    mock_server.sendmail.assert_not_called()


def test_position_reconciliation_alert_sends_again_once_cooldown_has_elapsed(db_engine, mocker):
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    stale_last_sent = (datetime.utcnow() - timedelta(hours=9)).isoformat()
    set_metadata(_POSITION_RECONCILIATION_KEY, stale_last_sent)

    cfg = _config()
    cfg["notifications"]["email"]["alert_cooldown_hours"] = 8
    send_position_reconciliation_alert(_reconciliation_summary(broker_closed=["ACQD"]), cfg)

    mock_server.sendmail.assert_called_once()


def test_position_reconciliation_alert_disabled_does_not_consume_cooldown(db_engine, mocker):
    mocker.patch("output.briefing.smtplib.SMTP")

    send_position_reconciliation_alert(_reconciliation_summary(broker_closed=["ACQD"]), _config(enabled=False))

    from data.database import get_metadata_str
    assert get_metadata_str(_POSITION_RECONCILIATION_KEY) is None


def test_position_reconciliation_alert_uses_its_own_cooldown_key_not_shared_with_others(db_engine, mocker):
    """A membership-staleness, instrument-resolution-failure, or
    T212-error-alert cooldown already in effect must never suppress this
    alert."""
    set_metadata("t212_error_email_last_sent", datetime.utcnow().isoformat())
    set_metadata(_MEMBERSHIP_STALENESS_KEY, datetime.utcnow().isoformat())
    set_metadata(_INSTRUMENT_RESOLUTION_FAILURE_KEY, datetime.utcnow().isoformat())
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    sent, _ = send_position_reconciliation_alert(_reconciliation_summary(broker_closed=["ACQD"]), _config())

    assert sent is True
    mock_server.sendmail.assert_called_once()
