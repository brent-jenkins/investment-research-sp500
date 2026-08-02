"""Email sending -- the shared SMTP helper, plus the weekly summary (the
one piece of the module's original, much larger V1 scope that has a
genuine V2 equivalent; daily narrative, macro context, opportunity
building, VIX-spike and central-bank alerts were all signal/regime-driven
content tied to the six-module confluence system and had no equivalent to
carry forward).
"""
import html
import json
import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

_UK_TZ = ZoneInfo("Europe/London")
_LAST_WEEKLY_SUMMARY_KEY = "last_weekly_summary_sent"
_MEMBERSHIP_STALENESS_EMAIL_LAST_SENT_KEY = "membership_staleness_email_last_sent"
_INSTRUMENT_RESOLUTION_FAILURE_EMAIL_LAST_SENT_KEY = "instrument_resolution_failure_email_last_sent"
_POSITION_RECONCILIATION_EMAIL_LAST_SENT_KEY = "position_reconciliation_email_last_sent"


def _smtp_send(subject: str, text_body: str, html_body: str, config: dict) -> tuple[bool, str]:
    """Build and send a multipart plain+HTML email via SMTP.

    Respects notifications.email.enabled (2026-07-31) — checked here, the
    single low-level chokepoint every caller goes through (currently just
    brokers/trading212.py's T212 error alert, but any future caller gets
    this for free too), rather than in each individual caller, where a new
    call site could easily forget it. When disabled, returns immediately
    without ever touching the network — no SMTP connection attempt at all.
    """
    notif = config.get("notifications", {}).get("email", {})
    if not notif.get("enabled", False):
        logger.debug("Email disabled (notifications.email.enabled=false) — not sending: %s", subject)
        return False, "Email disabled (notifications.email.enabled=false)"

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


def _fmt_uk(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a naive-UTC (or UTC-aware) datetime to UK local time and
    format it, appending 'GMT'/'BST' — the identical rule main.py's own
    _fmt_uk() applies (never bake a literal 'UTC' into user-facing text).
    Defined independently here rather than imported from main.py, since
    main.py can never be imported outside the running app (it starts a
    real background scheduler at module level — see tests/test_main_ui.py).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    uk = dt.astimezone(_UK_TZ)
    abbr = "BST" if uk.dst() else "GMT"
    return f"{uk.strftime(fmt)} {abbr}"


# ---------------------------------------------------------------------------
# Weekly summary — Sunday 09:00 Europe/London (see data/scheduler.py:
# _weekly_summary_job()). Own trigger path, deliberately separate from
# brokers/trading212.py's error-alert cooldown (_T212_ERROR_EMAIL_LAST_SENT_KEY,
# notifications.email.alert_cooldown_hours) — that cooldown exists to stop a
# persistent error from flooding inboxes with duplicate alerts, a completely
# different concern from "send exactly one scheduled email a week". Sharing
# a gate between them would mean a real error alert could go unsent because
# the weekly summary "used up" the cooldown window, or vice versa. The only
# state this path tracks is _LAST_WEEKLY_SUMMARY_KEY, and it is used purely
# to bound *content* ("what happened since last time"), never to decide
# whether to send — the schedule alone decides that.
#
# Tone (2026-07-31, same standard as the Daily Briefing / Performance page
# tone-neutral rebuild): no colour-coded urgency, no ranking of positions by
# risk or performance, no call-to-action language. Every position and every
# closed trade is a plain factual record regardless of whether it made or
# lost money — this is a calm status summary, not a prompt to react.
# ---------------------------------------------------------------------------

def _gather_weekly_data(config: dict) -> dict:
    """Pure data gathering for the weekly summary — no sending, no HTML/text
    building. Every figure reuses an existing, already-tested data source
    (output/performance.py's Daily-Briefing/Performance-page functions)
    rather than a fresh, parallel computation of the same thing.
    """
    from data.database import get_metadata_str
    from data.ingestion import get_price_df
    from analysis import crisis
    from output.performance import get_brief_cards, get_pnl_rows

    now = datetime.utcnow()

    last_sent_str = get_metadata_str(_LAST_WEEKLY_SUMMARY_KEY)
    window_start = now - timedelta(days=7)
    if last_sent_str:
        try:
            window_start = datetime.fromisoformat(last_sent_str)
        except Exception:
            pass  # corrupt/unexpected stored value -- fall back to the 7-day default above

    # Open positions -- same source, same neutral shape, as the Daily
    # Briefing/Performance page. Alphabetical by ticker, never by status or
    # P&L -- no ranking by risk or performance (see this section's own
    # docstring above).
    open_cards = sorted(get_brief_cards(config), key=lambda c: c["ticker"])

    account_raw = get_metadata_str("t212_account_snapshot")
    total_value_gbp = None
    if account_raw:
        try:
            total_value_gbp = float(json.loads(account_raw).get("totalValue", 0) or 0)
        except Exception:
            total_value_gbp = None

    closed_this_week = []
    for row in get_pnl_rows():
        exit_dt = row.get("exit_date")
        if isinstance(exit_dt, str):
            try:
                exit_dt = datetime.fromisoformat(exit_dt)
            except Exception:
                continue
        if exit_dt is not None and exit_dt >= window_start:
            closed_this_week.append(row)
    closed_this_week.sort(key=lambda r: r["ticker"])

    realized_pnl_gbp = sum(float(r.get("pnl_gbp") or 0) for r in closed_this_week)
    unrealized_pnl_gbp = sum(float(c.get("unreal_pnl_gbp") or 0) for c in open_cards)

    crisis_fired_dates: list[str] = []
    try:
        gspc_df = get_price_df("^GSPC", days=60)
        if not gspc_df.empty:
            gspc_df = gspc_df.set_index("date")
            gspc_df.index = pd.to_datetime(gspc_df.index)
            close = gspc_df["close"].astype(float)
            raw_trigger = crisis.compute_raw_trigger(close)
            fired = raw_trigger[raw_trigger.index >= pd.Timestamp(window_start)]
            crisis_fired_dates = [d.date().isoformat() for d in fired[fired].index]
    except Exception:
        pass

    last_full_refresh_str = get_metadata_str("last_full_refresh")
    last_full_refresh_dt: "datetime | None" = None
    if last_full_refresh_str:
        try:
            last_full_refresh_dt = datetime.fromisoformat(last_full_refresh_str)
        except Exception:
            pass
    # 3 days covers a normal Friday-EOD-to-Sunday-morning gap (~1.5 days)
    # with a comfortable margin, without needing the Monday-specific
    # weekend exemption the Status page's own OVERDUE badge uses (this
    # check only ever runs on a Sunday).
    eod_recent = bool(last_full_refresh_dt) and (now - last_full_refresh_dt) <= timedelta(days=3)

    unresolved_error = (
        get_metadata_str("invest_error") or get_metadata_str("t212_invest_error") or ""
    ).strip()

    return {
        "generated_at": now,
        "window_start": window_start,
        "open_positions": open_cards,
        "total_value_gbp": total_value_gbp,
        "closed_this_week": closed_this_week,
        "realized_pnl_gbp": realized_pnl_gbp,
        "unrealized_pnl_gbp": unrealized_pnl_gbp,
        "total_pnl_gbp": realized_pnl_gbp + unrealized_pnl_gbp,
        "crisis_fired_dates": crisis_fired_dates,
        "last_full_refresh": last_full_refresh_dt,
        "eod_recent": eod_recent,
        "unresolved_error": unresolved_error,
    }


def _weekly_summary_text(data: dict) -> str:
    """Plain-text body -- a calm, factual record. Every line states what
    happened; none of them are worded differently depending on whether the
    underlying number is good or bad."""
    lines = [
        "SP500INVEST -- Weekly Summary",
        f"Generated {_fmt_uk(data['generated_at'])}",
        "Reflects Friday's market close -- markets are shut over the weekend, "
        "so this is the most recent close available, not stale data.",
        "",
        (
            f"Account total: £{data['total_value_gbp']:,.2f}"
            if data["total_value_gbp"] is not None
            else "Account total: not available"
        ),
        f"Total P&L this week (realised + unrealised): £{data['total_pnl_gbp']:,.2f}",
        f"  Realised (positions closed this week): £{data['realized_pnl_gbp']:,.2f}",
        f"  Unrealised (currently open positions): £{data['unrealized_pnl_gbp']:,.2f}",
        "",
        f"Open positions ({len(data['open_positions'])}):",
    ]
    if not data["open_positions"]:
        lines.append("  None.")
    for c in data["open_positions"]:
        sig = c.get("current_sig_on")
        sig_text = "signal on" if sig is True else ("signal off" if sig is False else "signal unknown")
        entry = (c.get("entry_date") or "")[:10] or "unknown"
        lines.append(
            f"  {c['ticker']}: entered {entry}, {sig_text}, "
            f"unrealised P&L £{c.get('unreal_pnl_gbp', 0):,.2f}"
        )

    lines.append("")
    lines.append(f"Closed this week ({len(data['closed_this_week'])}):")
    if not data["closed_this_week"]:
        lines.append("  None.")
    for r in data["closed_this_week"]:
        exit_date = str(r.get("exit_date") or "")[:10]
        lines.append(
            f"  {r['ticker']}: closed {exit_date}, reason {r.get('exit_reason')}, "
            f"P&L £{r.get('pnl_gbp', 0):,.2f}"
        )

    lines.append("")
    if data["crisis_fired_dates"]:
        lines.append(f"Crisis override: fired on {', '.join(data['crisis_fired_dates'])}.")
    else:
        lines.append("Crisis override: did not fire this week.")

    lines.append("")
    refresh_text = _fmt_uk(data["last_full_refresh"]) if data["last_full_refresh"] else "unknown"
    status_text = "no unresolved errors" if not data["unresolved_error"] else f"unresolved error recorded: {data['unresolved_error']}"
    ok_text = "system healthy" if data["eod_recent"] and not data["unresolved_error"] else "system status uncertain"
    lines.append(f"System health: last data refresh {refresh_text}; {status_text} ({ok_text}).")

    return "\n".join(lines)


def _weekly_summary_html(data: dict) -> str:
    """HTML body -- same content as _weekly_summary_text(), same neutral
    styling throughout: one plain colour for every row regardless of sign,
    no conditional styling on any figure's own value."""
    _NEUTRAL = "#334155"  # one colour, used unconditionally -- see this
                          # module's own tone note above.

    def esc(s) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    account_line = (
        f"£{data['total_value_gbp']:,.2f}" if data["total_value_gbp"] is not None else "not available"
    )

    rows_open = ""
    for c in data["open_positions"]:
        sig = c.get("current_sig_on")
        sig_text = "signal on" if sig is True else ("signal off" if sig is False else "signal unknown")
        entry = (c.get("entry_date") or "")[:10] or "unknown"
        rows_open += (
            f'<tr><td style="padding:4px 8px;color:{_NEUTRAL}">{esc(c["ticker"])}</td>'
            f'<td style="padding:4px 8px;color:{_NEUTRAL}">{esc(entry)}</td>'
            f'<td style="padding:4px 8px;color:{_NEUTRAL}">{esc(sig_text)}</td>'
            f'<td style="padding:4px 8px;color:{_NEUTRAL}">£{c.get("unreal_pnl_gbp", 0):,.2f}</td></tr>'
        )
    if not data["open_positions"]:
        rows_open = f'<tr><td colspan="4" style="padding:4px 8px;color:{_NEUTRAL}">None.</td></tr>'

    rows_closed = ""
    for r in data["closed_this_week"]:
        exit_date = str(r.get("exit_date") or "")[:10]
        rows_closed += (
            f'<tr><td style="padding:4px 8px;color:{_NEUTRAL}">{esc(r["ticker"])}</td>'
            f'<td style="padding:4px 8px;color:{_NEUTRAL}">{esc(exit_date)}</td>'
            f'<td style="padding:4px 8px;color:{_NEUTRAL}">{esc(r.get("exit_reason"))}</td>'
            f'<td style="padding:4px 8px;color:{_NEUTRAL}">£{r.get("pnl_gbp", 0):,.2f}</td></tr>'
        )
    if not data["closed_this_week"]:
        rows_closed = f'<tr><td colspan="4" style="padding:4px 8px;color:{_NEUTRAL}">None.</td></tr>'

    crisis_text = (
        f"fired on {esc(', '.join(data['crisis_fired_dates']))}"
        if data["crisis_fired_dates"] else "did not fire this week"
    )
    refresh_text = _fmt_uk(data["last_full_refresh"]) if data["last_full_refresh"] else "unknown"
    status_text = (
        "no unresolved errors" if not data["unresolved_error"]
        else f"unresolved error recorded: {esc(data['unresolved_error'])}"
    )
    ok_text = "system healthy" if data["eod_recent"] and not data["unresolved_error"] else "system status uncertain"

    return f"""
    <div style="font-family:Arial,sans-serif;color:{_NEUTRAL};max-width:640px">
      <h2 style="color:{_NEUTRAL}">SP500INVEST &mdash; Weekly Summary</h2>
      <p style="color:{_NEUTRAL}">Generated {esc(_fmt_uk(data['generated_at']))}<br>
      Reflects Friday's market close &mdash; markets are shut over the weekend,
      so this is the most recent close available, not stale data.</p>

      <p style="color:{_NEUTRAL}">
        <b>Account total:</b> {account_line}<br>
        <b>Total P&amp;L this week (realised + unrealised):</b> £{data['total_pnl_gbp']:,.2f}<br>
        &nbsp;&nbsp;Realised (closed this week): £{data['realized_pnl_gbp']:,.2f}<br>
        &nbsp;&nbsp;Unrealised (currently open): £{data['unrealized_pnl_gbp']:,.2f}
      </p>

      <h3 style="color:{_NEUTRAL}">Open Positions ({len(data['open_positions'])})</h3>
      <table style="border-collapse:collapse;width:100%">
        <tr style="color:{_NEUTRAL};font-weight:bold">
          <td style="padding:4px 8px">Ticker</td><td style="padding:4px 8px">Entered</td>
          <td style="padding:4px 8px">Signal</td><td style="padding:4px 8px">Unrealised P&amp;L</td>
        </tr>
        {rows_open}
      </table>

      <h3 style="color:{_NEUTRAL}">Closed This Week ({len(data['closed_this_week'])})</h3>
      <table style="border-collapse:collapse;width:100%">
        <tr style="color:{_NEUTRAL};font-weight:bold">
          <td style="padding:4px 8px">Ticker</td><td style="padding:4px 8px">Closed</td>
          <td style="padding:4px 8px">Reason</td><td style="padding:4px 8px">P&amp;L</td>
        </tr>
        {rows_closed}
      </table>

      <p style="color:{_NEUTRAL}"><b>Crisis override:</b> {crisis_text}.</p>
      <p style="color:{_NEUTRAL}">
        <b>System health:</b> last data refresh {esc(refresh_text)}; {esc(status_text)} ({esc(ok_text)}).
      </p>
    </div>
    """


def send_weekly_summary_email(config: dict) -> tuple[bool, str]:
    """Gather this week's data, build the email, and send it via
    _smtp_send() directly -- deliberately not routed through
    brokers/trading212.py's error-alert cooldown (see this section's own
    module-level note above for why the two must stay separate). On a
    genuinely successful send, records when for next time's "since last
    summary" content window -- this timestamp only ever bounds *content*,
    never gates *whether* to send; the Sunday 09:00 schedule alone decides
    that.
    """
    from data.database import set_metadata

    try:
        data = _gather_weekly_data(config)
    except Exception as exc:
        logger.error("Weekly summary data gathering failed: %s", exc, exc_info=True)
        return False, f"Data gathering failed: {exc}"

    subject = f"SP500INVEST Weekly Summary — {data['generated_at'].date().isoformat()}"
    text_body = _weekly_summary_text(data)
    html_body = _weekly_summary_html(data)

    sent, detail = _smtp_send(subject, text_body, html_body, config)
    if sent:
        try:
            set_metadata(_LAST_WEEKLY_SUMMARY_KEY, data["generated_at"].isoformat())
        except Exception as exc:
            logger.warning("Could not record last_weekly_summary_sent: %s", exc)
    return sent, detail


def send_membership_staleness_alert(age_hours: float, threshold_hours: float, config: dict) -> tuple[bool, str]:
    """Alert that the point-in-time S&P 500 membership refresh
    (analysis/universe.py:refresh_membership_from_source(), the SOLE live
    eligibility gate for stock entries) has gone unsuccessful for longer
    than threshold_hours (~2 reconciliation cycles).

    Gated the same two ways as brokers/trading212.py's
    _send_t212_error_email() (2026-08-02 precedent): notifications.email.
    enabled (checked inside _smtp_send() itself) and its OWN dedicated
    cooldown key, _MEMBERSHIP_STALENESS_EMAIL_LAST_SENT_KEY -- deliberately
    separate from every other email's cooldown (t212_error_email_last_sent,
    the weekly summary's own timestamp), for the identical reason the
    weekly summary already keeps its own trigger path (see this module's
    docstring and send_weekly_summary_email() above): a persistent
    membership-refresh failure is a different problem from a T212 order
    error or a scheduled weekly send, and sharing one cooldown would let
    any one of them silently "use up" another's alert window. The
    timestamp is only updated on a genuinely successful send, so a
    disabled config or a transient SMTP failure never suppresses the next
    real alert once email is re-enabled or the SMTP issue clears.
    """
    from data.database import get_metadata_str, set_metadata

    cooldown_hours = float(config.get("notifications", {}).get("email", {}).get("alert_cooldown_hours", 8))
    last_sent_str = get_metadata_str(_MEMBERSHIP_STALENESS_EMAIL_LAST_SENT_KEY)
    if last_sent_str:
        try:
            hours_since = (datetime.utcnow() - datetime.fromisoformat(last_sent_str)).total_seconds() / 3600
            if hours_since < cooldown_hours:
                logger.info(
                    "Membership staleness email skipped — within %.1fh cooldown (last sent %.1fh ago)",
                    cooldown_hours, hours_since,
                )
                return False, "within cooldown"
        except ValueError:
            pass  # corrupt/unexpected stored value -- treat as no prior send

    age_str = f"{age_hours:.1f}h" if age_hours != float("inf") else "an unknown period (never recorded)"
    subject = "SP500INVEST: S&P 500 membership refresh is stale"
    text_body = (
        "The point-in-time S&P 500 membership refresh "
        "(analysis/universe.py:refresh_membership_from_source(), the SOLE "
        "live eligibility gate for stock entries) has not completed "
        f"successfully in {age_str} — past the {threshold_hours:.0f}h "
        "(~2 reconciliation cycle) threshold.\n\n"
        "This does NOT necessarily mean a real index change has been "
        "missed — the underlying source (github.com/fja05680/sp500) only "
        "updates every 1-5 months in practice. It means the DAILY refresh "
        "job that keeps this system's local copy in sync has itself "
        "stopped succeeding — most likely because the source is "
        "unreachable, renamed, rate-limiting, or its raw CSV URL has "
        "changed. Until this is fixed, live stock eligibility is being "
        "decided against a frozen, aging snapshot with no further checks.\n\n"
        "Check the Status page's Scheduler Health table (Membership "
        "Refresh row) and server logs for "
        "'refresh_membership_from_source' / 'Membership refresh'."
    )
    html_body = (
        '<p style="color:#dc2626;font-weight:bold">S&amp;P 500 membership refresh is stale</p>'
        f'<p>Last successful reconciliation: {html.escape(age_str)} ago '
        f'(threshold: {threshold_hours:.0f}h, ~2 reconciliation cycles).</p>'
        '<p>This does <b>not</b> necessarily mean a real index change was missed — '
        'the underlying source updates every 1-5 months in practice — it means '
        'the <b>daily refresh job itself has stopped succeeding</b>, most likely '
        'because github.com/fja05680/sp500 is unreachable, renamed, or its raw '
        'CSV URL changed. Live stock eligibility is being decided against a '
        'frozen snapshot until this is fixed.</p>'
        "<p>Check the Status page's Scheduler Health table (Membership Refresh "
        "row) and server logs.</p>"
    )
    sent, detail = _smtp_send(subject, text_body, html_body, config)
    if sent:
        try:
            set_metadata(_MEMBERSHIP_STALENESS_EMAIL_LAST_SENT_KEY, datetime.utcnow().isoformat())
        except Exception as exc:
            logger.warning("Could not record membership_staleness_email_last_sent: %s", exc)
    return sent, detail


def send_instrument_resolution_failure_alert(failed: list[dict], config: dict) -> tuple[bool, str]:
    """Alert that one or more live candidate tickers have a FAILED T212
    instrument-ID resolution (brokers/trading212.py:resolve_and_cache_
    instrument()) -- a genuine, otherwise-tradeable candidate is being
    silently excluded until a human reviews it, per that mechanism's own
    "flag for review, never auto-retry a genuine mismatch" contract (see
    CLAUDE.md's "wire the momentum-only, full-S&P-500 selection into live
    execution" section). Without this alert a FAILED row would just sit in
    instrument_resolutions until someone happened to open the Status page
    -- the exact "sits silently wrong" gap this project's own standing
    review discipline exists to close, and the same gap the membership-
    staleness alert immediately above this function was built to close for
    a different mechanism.

    Gated the same two ways as send_membership_staleness_alert() (2026-08-02
    precedent, itself following brokers/trading212.py's _send_t212_error_
    email()): notifications.email.enabled (checked inside _smtp_send()
    itself) and its OWN dedicated cooldown key,
    _INSTRUMENT_RESOLUTION_FAILURE_EMAIL_LAST_SENT_KEY -- deliberately
    separate from every other email's cooldown, for the identical reason
    every other alert here keeps its own trigger path: a persistent
    instrument-resolution failure is a different problem from a stale
    membership refresh, a T212 order error, or a scheduled weekly send, and
    sharing one cooldown would let any one of them silently "use up"
    another's alert window. Called on every scheduled run (not just the
    EOD run that can actually produce a NEW failure) specifically so an
    already-known, still-unresolved FAILED ticker keeps getting
    re-flagged periodically (subject to its own cooldown) for as long as
    it stays unresolved, not just once on the day it first failed.
    """
    from data.database import get_metadata_str, set_metadata

    if not failed:
        return False, "no failed resolutions"

    cooldown_hours = float(config.get("notifications", {}).get("email", {}).get("alert_cooldown_hours", 8))
    last_sent_str = get_metadata_str(_INSTRUMENT_RESOLUTION_FAILURE_EMAIL_LAST_SENT_KEY)
    if last_sent_str:
        try:
            hours_since = (datetime.utcnow() - datetime.fromisoformat(last_sent_str)).total_seconds() / 3600
            if hours_since < cooldown_hours:
                logger.info(
                    "Instrument resolution failure email skipped — within %.1fh cooldown (last sent %.1fh ago)",
                    cooldown_hours, hours_since,
                )
                return False, "within cooldown"
        except ValueError:
            pass  # corrupt/unexpected stored value -- treat as no prior send

    tickers = sorted(r["ticker"] for r in failed)
    n = len(tickers)
    subject = f"SP500INVEST: {n} ticker(s) failed T212 instrument resolution"
    ticker_lines = "\n".join(
        f"  - {r['ticker']}: {r['note'] or '(no note recorded)'}"
        for r in sorted(failed, key=lambda r: r["ticker"])
    )
    text_body = (
        f"{n} live candidate ticker(s) have a FAILED T212 instrument-ID "
        "resolution (brokers/trading212.py:resolve_and_cache_instrument()) "
        "-- each is a genuine, otherwise-tradeable candidate (index member, "
        "momentum signal on) that cannot be traded because no confident, "
        "unambiguous match was found against T212's own instrument "
        "metadata. Excluded from trading until a human reviews it -- this "
        "is never auto-retried, since a genuine mismatch would just "
        "reproduce the same wrong answer.\n\n"
        f"{ticker_lines}\n\n"
        "Check the Status page's T212 Invest Account section (Instrument "
        "mapping expander) to review. To force re-resolution once a fix is "
        "identified, delete the ticker's row from the instrument_resolutions "
        "table."
    )
    ticker_rows_html = "".join(
        f"<li><b>{html.escape(r['ticker'])}</b>: {html.escape(r['note'] or '(no note recorded)')}</li>"
        for r in sorted(failed, key=lambda r: r["ticker"])
    )
    html_body = (
        f'<p style="color:#dc2626;font-weight:bold">{n} ticker(s) failed T212 instrument resolution</p>'
        "<p>Each is a genuine, otherwise-tradeable candidate (index member, momentum "
        "signal on) excluded from trading because no confident, unambiguous match was "
        "found against T212's own instrument metadata. Never auto-retried -- a genuine "
        "mismatch needs a human, not another guess.</p>"
        f"<ul>{ticker_rows_html}</ul>"
        "<p>Check the Status page's T212 Invest Account section (Instrument mapping "
        "expander) to review. Delete the ticker's row from instrument_resolutions to "
        "force re-resolution once a fix is identified.</p>"
    )
    sent, detail = _smtp_send(subject, text_body, html_body, config)
    if sent:
        try:
            set_metadata(_INSTRUMENT_RESOLUTION_FAILURE_EMAIL_LAST_SENT_KEY, datetime.utcnow().isoformat())
        except Exception as exc:
            logger.warning("Could not record instrument_resolution_failure_email_last_sent: %s", exc)
    return sent, detail


def send_position_reconciliation_alert(summary: dict, config: dict) -> tuple[bool, str]:
    """Alert that brokers/trading212.py:reconcile_positions_with_broker()
    found one or more discrepancies between what this system's own order
    history implies is open and what T212's real, live positions list
    actually shows — see that function's own docstring and CLAUDE.md's
    "Live position reconciliation" section (2026-08-02) for the two
    shapes this covers (a cash-out acquisition the broker closed without
    any SELL from this system, or a stock-swap acquisition that converted
    a held position into an unrecognized instrument). Without this alert
    either discrepancy would sit silently in position_reconciliations
    until someone happened to open the Status page — the same "sits
    silently wrong" gap send_instrument_resolution_failure_alert() and
    send_membership_staleness_alert() were each already built to close
    for their own mechanism.

    Own dedicated cooldown key, same reasoning as every other alert in
    this file: a persistent broker-reconciliation discrepancy is a
    different problem from a stale membership refresh or a failed
    instrument resolution, and sharing a cooldown would let one silently
    consume another's alert window.
    """
    from data.database import get_metadata_str, set_metadata

    broker_closed = summary.get("broker_closed_no_sell") or []
    unrecognized = summary.get("unrecognized_position") or []
    if not broker_closed and not unrecognized:
        return False, "no discrepancies"

    cooldown_hours = float(config.get("notifications", {}).get("email", {}).get("alert_cooldown_hours", 8))
    last_sent_str = get_metadata_str(_POSITION_RECONCILIATION_EMAIL_LAST_SENT_KEY)
    if last_sent_str:
        try:
            hours_since = (datetime.utcnow() - datetime.fromisoformat(last_sent_str)).total_seconds() / 3600
            if hours_since < cooldown_hours:
                logger.info(
                    "Position reconciliation email skipped — within %.1fh cooldown (last sent %.1fh ago)",
                    cooldown_hours, hours_since,
                )
                return False, "within cooldown"
        except ValueError:
            pass

    n = len(broker_closed) + len(unrecognized)
    subject = f"SP500INVEST: {n} live position reconciliation discrepancy(ies)"

    closed_lines = "\n".join(f"  - {t} — broker closed this position with no SELL from us" for t in sorted(broker_closed))
    unrec_lines = "\n".join(f"  - {t} — held by T212, no recognized mapping" for t in sorted(unrecognized))
    text_body = (
        f"{n} discrepancy(ies) found between this system's own order history and T212's "
        "real, live account state (brokers/trading212.py:reconcile_positions_with_broker()).\n\n"
        + (f"Broker closed, no SELL from us (likely a cash-out acquisition):\n{closed_lines}\n\n" if closed_lines else "")
        + (f"Unrecognized position held (likely a stock-swap acquisition):\n{unrec_lines}\n\n" if unrec_lines else "")
        + "Check the position_reconciliations table (or a future Status page view of it) "
        "for the full note on each — exact proceeds/identity are not guessed, only flagged "
        "for manual review."
    )
    closed_html = "".join(f"<li><b>{html.escape(t)}</b> — broker closed this position with no SELL from us</li>" for t in sorted(broker_closed))
    unrec_html = "".join(f"<li><b>{html.escape(t)}</b> — held by T212, no recognized mapping</li>" for t in sorted(unrecognized))
    html_body = (
        f'<p style="color:#dc2626;font-weight:bold">{n} live position reconciliation discrepancy(ies)</p>'
        "<p>Found between this system's own order history and T212's real, live account state.</p>"
        + (f"<p><b>Broker closed, no SELL from us (likely a cash-out acquisition):</b></p><ul>{closed_html}</ul>" if closed_html else "")
        + (f"<p><b>Unrecognized position held (likely a stock-swap acquisition):</b></p><ul>{unrec_html}</ul>" if unrec_html else "")
        + "<p>Check the position_reconciliations table for the full note on each.</p>"
    )
    sent, detail = _smtp_send(subject, text_body, html_body, config)
    if sent:
        try:
            set_metadata(_POSITION_RECONCILIATION_EMAIL_LAST_SENT_KEY, datetime.utcnow().isoformat())
        except Exception as exc:
            logger.warning("Could not record position_reconciliation_email_last_sent: %s", exc)
    return sent, detail
