"""Shared low-level email-sending helper.

The only survivor of this module's original, much larger scope (daily
narrative, macro context, opportunity building, weekly summary, VIX-spike
and central-bank alert emails) — all of that was signal/regime-driven
content tied to the six-module confluence system and had no equivalent to
carry forward. _smtp_send() itself has no such dependency (pure SMTP
mechanics reading notifications.email from config.yaml) and is still used
by brokers/trading212.py's order-placement error alert.
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _smtp_send(subject: str, text_body: str, html_body: str, config: dict) -> tuple[bool, str]:
    """Build and send a multipart plain+HTML email via SMTP."""
    notif     = config.get("notifications", {}).get("email", {})
    smtp_host = notif.get("smtp_host",     "smtp-relay.brevo.com")
    smtp_port = int(notif.get("smtp_port", 587))
    smtp_user = notif.get("smtp_user",     "")
    smtp_pass = notif.get("smtp_password", "") or os.environ.get("SMTP_PASSWORD", "")
    from_addr = notif.get("from_address")  or smtp_user
    to_addrs  = notif.get("to_addresses") or []

    if not to_addrs:
        return False, "No recipient addresses configured in notifications.email.to_addresses"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(to_addrs)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        return True, f"Sent to {len(to_addrs)} recipient(s)"
    except Exception as exc:
        logger.error("SMTP send failed: %s", exc)
        return False, f"Email send failed: {exc}"
