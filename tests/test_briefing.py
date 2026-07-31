"""Tests for output/briefing.py's _smtp_send() — the sole surviving,
shared low-level email helper (see the module's own docstring for why
everything else was removed along with V1).
"""
from output.briefing import _smtp_send


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
