"""Daily briefing: trade status computation, macro context, opportunities, email alerts."""

import html
import logging
import os
import smtplib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import text

from analysis.regime import is_entry_allowed
from data.database import get_engine, get_metadata_str, set_metadata
from output.performance import get_closed_trades

logger = logging.getLogger(__name__)

_UK_TZ = ZoneInfo("Europe/London")


def _fmt_uk(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a naive-UTC (or UTC-aware) datetime to UK local time and format it.

    Appends 'GMT' or 'BST' depending on whether DST is currently active.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    uk = dt.astimezone(_UK_TZ)
    abbr = "BST" if uk.dst() else "GMT"
    return f"{uk.strftime(fmt)} {abbr}"

STATUS_COLOURS: dict[str, str] = {
    "ON TRACK":    "#16a34a",
    "WEAKENING":   "#d97706",
    "EXIT SIGNAL": "#ea580c",
    "STOP LOSS":   "#dc2626",
}

STATUS_ICONS: dict[str, str] = {
    "ON TRACK":    "✅",
    "WEAKENING":   "⚠️",
    "EXIT SIGNAL": "🔴",
    "STOP LOSS":   "🛑",
}


# ---------------------------------------------------------------------------
# HTML email helpers — shared across all notification types
# ---------------------------------------------------------------------------

_BASE_FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif"

_SIG_STYLES_H: dict[str, tuple[str, str]] = {
    "STRONG_BUY":  ("#16a34a", "#ffffff"),
    "BUY":         ("#bbf7d0", "#166534"),
    "NEUTRAL":     ("#e2e8f0", "#475569"),
    "SELL":        ("#fecaca", "#991b1b"),
    "STRONG_SELL": ("#dc2626", "#ffffff"),
}

_STATUS_COLORS_H: dict[str, str] = {
    "ON TRACK":           "#16a34a",
    "WEAKENING":          "#d97706",
    "EXIT SIGNAL":        "#ea580c",
    "STOP LOSS":          "#dc2626",
    "STOP LOSS HIT":      "#dc2626",
    "APPROACHING STOP":   "#f59e0b",
    "TAKE PROFIT REACHED": "#16a34a",
}


def _badge_h(text: str, bg: str, fg: str = "#ffffff", size: int = 11) -> str:
    return (
        f'<span style="display:inline-block;background-color:{bg};color:{fg};'
        f'padding:2px 8px;border-radius:4px;font-size:{size}px;font-weight:700">'
        f'{text}</span>'
    )


def _sig_badge_h(signal: str) -> str:
    bg, fg = _SIG_STYLES_H.get(signal, ("#e2e8f0", "#475569"))
    return _badge_h(signal.replace("_", " "), bg, fg)


def _pnl_color(val: float) -> str:
    return "#16a34a" if val >= 0 else "#dc2626"


def _table_h(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(
        f'<th style="padding:6px 8px;font-size:12px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.5px;color:#64748b;text-align:left;border-bottom:2px solid #e2e8f0">'
        f'{h}</th>'
        for h in headers
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td style="padding:6px 8px;font-size:13px;color:#1e293b;'
            f'border-bottom:1px solid #f1f5f9">{c}</td>'
            for c in row
        ) + "</tr>"
        for row in rows
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
        f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    )


def _section_h(title: str, content: str, border: str = "#3b82f6") -> str:
    title_html = (
        f'<p style="margin:0 0 10px;font-size:11px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:1px;color:#94a3b8">{title}</p>'
        if title else ""
    )
    return (
        f'<div style="background-color:#ffffff;margin-bottom:3px;'
        f'padding:18px 22px;border-left:4px solid {border}">'
        f'{title_html}{content}</div>'
    )


def _email_wrap_h(header: str, body: str, footer: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '</head>'
        f'<body style="margin:0;padding:16px 8px;background-color:#f1f5f9;{_BASE_FONT}">'
        '<div style="max-width:640px;margin:0 auto">'
        f'{header}{body}{footer}'
        '</div></body></html>'
    )


def _email_header_h(title: str, subtitle: str, accent: str = "#0f172a") -> str:
    return (
        f'<div style="background-color:{accent};padding:24px 26px;border-radius:8px 8px 0 0">'
        '<p style="margin:0 0 4px;font-size:10px;font-weight:700;text-transform:uppercase;'
        'letter-spacing:1.5px;color:#94a3b8">Investment Research Dashboard</p>'
        f'<h1 style="margin:0 0 4px;font-size:21px;font-weight:700;color:#ffffff">{title}</h1>'
        f'<p style="margin:0;font-size:13px;color:#94a3b8">{subtitle}</p>'
        '</div>'
    )


def _email_footer_h(note: str = "") -> str:
    extra = f' &nbsp;·&nbsp; {note}' if note else ''
    return (
        '<div style="background-color:#1e293b;padding:14px 22px;border-radius:0 0 8px 8px">'
        '<p style="margin:0;font-size:12px;color:#64748b">'
        '<a href="https://research.example.com" style="color:#60a5fa;text-decoration:none">'
        'research.example.com</a>'
        f'{extra}</p></div>'
    )


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


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    if pw := os.environ.get("SMTP_PASSWORD"):
        cfg.setdefault("notifications", {}).setdefault("email", {})["smtp_password"] = pw
    return cfg


def get_macro_context() -> dict:
    """Read VIX, yield curve, and dollar trend from stored indicator rows."""
    ctx: dict = {
        "vix":          None,
        "vix_regime":   None,
        "yield_spread": None,
        "yield_regime": None,
        "dollar_trend": None,
        "dollar_pct":   None,
    }
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT vix_level, vix_regime, yield_curve_spread, yield_curve_regime "
                    "FROM indicators WHERE ticker = '^GSPC' ORDER BY date DESC LIMIT 1"
                )
            ).mappings().first()
            if row:
                ctx["vix"]          = row.get("vix_level")
                ctx["vix_regime"]   = row.get("vix_regime")
                ctx["yield_spread"] = row.get("yield_curve_spread")
                ctx["yield_regime"] = row.get("yield_curve_regime")

            # Dollar trend: UUP price vs its 20-day SMA
            ind_row = conn.execute(
                text("SELECT sma_20 FROM indicators WHERE ticker = 'UUP' ORDER BY date DESC LIMIT 1")
            ).mappings().first()
            px_row = conn.execute(
                text("SELECT close FROM price_data WHERE ticker = 'UUP' ORDER BY date DESC LIMIT 1")
            ).mappings().first()
            if ind_row and px_row:
                sma20 = float(ind_row.get("sma_20") or 0)
                close = float(px_row.get("close")   or 0)
                if sma20 > 0:
                    pct = (close - sma20) / sma20 * 100
                    ctx["dollar_pct"]   = round(pct, 2)
                    ctx["dollar_trend"] = (
                        "rising"  if pct >  1.0 else
                        "falling" if pct < -1.0 else
                        "flat"
                    )
    except Exception as exc:
        logger.warning("Macro context read failed: %s", exc)
    return ctx


# ---------------------------------------------------------------------------
# Daily narrative — rule-based plain-English market summary for the Daily
# Briefing page. See _generate_daily_narrative() docstring for the approach.
# ---------------------------------------------------------------------------

_NARRATIVE_REGIME_PLAIN: dict[str, str] = {
    "TRENDING_BULL":   "a bullish trending",
    "TRENDING_BEAR":   "a bearish trending",
    "RANGING_LOW_VOL": "a low-volatility, range-bound",
    "HIGH_VOLATILITY": "a high-volatility",
    "MIXED":           "a mixed",
}

_NARRATIVE_OVERLAY_LABELS: dict[str, str] = {
    "MACRO_STRESS":     "Macro Stress",
    "VOL_REGIME_SHIFT":  "Vol Regime Shift",
    "CREDIT_STRESS":    "Credit Stress",
    "RECESSION_RISK":   "Recession Risk",
    "EM_STRESS":        "EM Stress",
}


def _generate_daily_narrative(
    regime: dict,
    macro_data: dict,
    relationships: list,
    signals: dict,
    overlays,
    open_trades: list,
    config: dict,
) -> str:
    """Generate a rule-based, plain-English 3-paragraph market narrative.

    This is intentionally rule-based — conditional sentence fragments
    assembled from threshold checks against live data — not AI-generated,
    following the same pattern as the "Market Intelligence" summary on the
    Signals page (main.py:_render_signals_page, the `_intel` list joined
    into a single st.info() call). No call is made to the Anthropic API or
    any other external service, nothing is written to the database, and no
    scheduler job is involved — the narrative is regenerated fresh on every
    Daily Briefing render from data already computed for that render.
    Migrating to Claude API generation (for more natural phrasing, or to
    weave the fragments together more fluidly) is a considered future
    enhancement, not yet implemented.

    Args:
        regime: detect_regime() output — {"regime": Regime, "details":
            {"price", "sma_200", "adx", "vix"}, "description", "colour"}.
        macro_data: get_macro_context() and get_macro_dashboard() dicts
            merged into one — vix, yield_spread, yield_regime, dollar_trend,
            dollar_pct, composite_score, n_bullish, n_bearish, n_neutral,
            n_series, has_data, real_yield, real_yield_trend, breakeven,
            breakeven_trend, credit_status, fed_rate, fed_rate_trend,
            extra_series (list of dicts, one of which may have
            series_id == "CFNAI").
        relationships: get_stored_correlations() output — list of pair
            dicts with label, divergence, z_score, corr_30d,
            diverge_template, confirm_template.
        signals: {"counts": {signal_label: count}, "tradeable_counts":
            {...}, "total": int, "tradeable_total": int} — signal
            distribution, using the same "STRONG BUY"/"BUY"/... labels as
            the Daily Briefing's own signal-distribution display.
        overlays: iterable of active overlay keys (e.g. {"CREDIT_STRESS"}) —
            same set as system_metadata's active_overlays.
        open_trades: list of enriched trade/position dicts, each with a
            "status" key in {"ON TRACK", "WEAKENING", "EXIT SIGNAL",
            "STOP LOSS"} (the same shape used elsewhere on the Daily
            Briefing for the open trade monitor).
        config: full app config — read for allowed_entry_regimes per
            automation path (trading212_invest, trading212_stocks)
            via analysis.regime.is_entry_allowed().

    Returns:
        HTML string of up to three <p> paragraphs (each html.escape()'d
        internally), ready to embed inside an existing bordered card via
        st.markdown(..., unsafe_allow_html=True). Never empty for any of
        the five Regime values — always returns at least the environment
        paragraph.
    """
    overlays = set(overlays or [])
    # regime["regime"] may be a Regime (str, Enum) instance or a plain string
    # depending on the caller — str() on a (str, Enum) member renders as
    # "Regime.MIXED" (its default Enum __str__), not the plain value, so pull
    # .value explicitly when present rather than relying on str().
    _regime_raw = regime.get("regime", "MIXED")
    regime_name = str(getattr(_regime_raw, "value", _regime_raw))
    details = regime.get("details", {}) or {}
    adx     = details.get("adx")
    price   = details.get("price")
    sma_200 = details.get("sma_200")
    vix     = macro_data.get("vix")

    # ── Paragraph 1: market environment ─────────────────────────────────────
    regime_plain = _NARRATIVE_REGIME_PLAIN.get(regime_name, "an unclassified")

    if adx is None:
        trend_phrase = "trend strength (ADX) is not yet available"
    elif adx < 20:
        trend_phrase = f"trend momentum is weak (ADX {adx:.1f}, below the 25 threshold for a confirmed trend)"
    elif adx < 25:
        trend_phrase = f"trend momentum is moderate (ADX {adx:.1f}, approaching but not yet confirming a trend)"
    else:
        trend_phrase = f"trend momentum is strong (ADX {adx:.1f}, a confirmed trend above the 25 threshold)"

    if vix is None:
        vol_phrase = "VIX data is not yet available"
    elif vix < 15:
        vol_phrase = f"volatility is suppressed (VIX {vix:.1f})"
    elif vix < 20:
        vol_phrase = f"volatility is calm but not suppressed (VIX {vix:.1f})"
    elif vix < 30:
        vol_phrase = f"volatility is elevated (VIX {vix:.1f})"
    else:
        vol_phrase = f"volatility is high (VIX {vix:.1f})"

    p1_sentences: list[str] = [
        f"Markets are in {regime_plain} regime — {trend_phrase} and {vol_phrase}."
    ]

    if price is not None and sma_200:
        pct = (price / sma_200 - 1) * 100
        p1_sentences.append(
            f"The S&P 500 is trading {abs(pct):.1f}% {'above' if pct >= 0 else 'below'} its 200-day moving average."
        )

    yield_regime = macro_data.get("yield_regime")
    dollar_trend = macro_data.get("dollar_trend")
    if yield_regime and dollar_trend:
        p1_sentences.append(f"The yield curve is {yield_regime} and the dollar is {dollar_trend}.")
    elif yield_regime:
        p1_sentences.append(f"The yield curve is {yield_regime}.")
    elif dollar_trend:
        p1_sentences.append(f"The dollar is {dollar_trend}.")

    composite = macro_data.get("composite_score")
    n_bullish = macro_data.get("n_bullish")
    n_series  = macro_data.get("n_series")
    if macro_data.get("has_data") and composite is not None and n_series:
        if composite > 0.3:
            macro_word = "broadly supportive"
        elif composite < -0.3:
            macro_word = "broadly cautionary"
        else:
            macro_word = "mixed"
        macro_sentence = f"The macro backdrop is {macro_word} with {n_bullish} of {n_series} indicators bullish"

        cfnai_entry = next(
            (e for e in (macro_data.get("extra_series") or []) if e.get("series_id") == "CFNAI"),
            None,
        )
        cfnai_val: Optional[float] = None
        if cfnai_entry and cfnai_entry.get("display_value") not in (None, "—"):
            try:
                cfnai_val = float(cfnai_entry["display_value"])
            except (TypeError, ValueError):
                cfnai_val = None
        if cfnai_val is not None:
            if cfnai_val < -0.70:
                cfnai_word = "recessionary"
            elif cfnai_val <= 0:
                cfnai_word = "running below trend"
            else:
                cfnai_word = "running above trend"
            macro_sentence += f", though growth is {cfnai_word} (CFNAI {cfnai_val:+.2f})"
        p1_sentences.append(macro_sentence + ".")
    elif macro_data.get("has_data") is False:
        p1_sentences.append("Macro indicator data has not been fetched yet.")

    if overlays:
        overlay_labels = [
            _NARRATIVE_OVERLAY_LABELS.get(k, k.replace("_", " ").title())
            for k in sorted(overlays)
        ]
        p1_sentences.append(
            f"Active risk overlay{'s' if len(overlay_labels) > 1 else ''}: {', '.join(overlay_labels)}."
        )

    all_counts = signals.get("counts", {}) or {}
    tradeable_total = signals.get("tradeable_total")
    if tradeable_total:
        buy_n  = all_counts.get("STRONG BUY", 0) + all_counts.get("BUY", 0)
        sell_n = all_counts.get("SELL", 0) + all_counts.get("STRONG SELL", 0)
        p1_sentences.append(
            f"Signal breadth across the tradeable watchlist stands at {buy_n} buy-leaning vs "
            f"{sell_n} sell-leaning signals out of {tradeable_total}."
        )

    p1 = " ".join(p1_sentences)

    # ── Paragraph 2: cross-asset relationships ──────────────────────────────
    relationships = relationships or []
    scored = [p for p in relationships if p.get("z_score") is not None]
    diverging  = [p for p in scored if p.get("divergence") == "DIVERGING"]
    confirming = [p for p in scored if p.get("divergence") == "CONFIRMING"]

    if not scored:
        p2 = "Cross-asset relationship data is not yet available."
    elif not diverging:
        p2 = "Cross-asset relationships are broadly normal — no pairs are currently diverging from historical norms"
        if confirming:
            p2 += f", and {len(confirming)} pair(s) are actively confirming their expected direction"
        p2 += "."
    else:
        notable = [p for p in diverging if abs(p.get("z_score") or 0) > 1.5] or diverging
        notable.sort(key=lambda p: abs(p.get("z_score") or 0), reverse=True)
        lead = notable[0]
        frag = f"{lead['label']} (Z={lead['z_score']:+.2f})"
        others = notable[1:2]
        if others:
            frag += f" and {others[0]['label']} (Z={others[0]['z_score']:+.2f})"
        p2 = (
            f"{len(diverging)} pair{'s are' if len(diverging) != 1 else ' is'} diverging from historical "
            f"norms — notably {frag}."
        )
        lead_template = (lead.get("diverge_template") or "").rstrip()
        if lead_template:
            if not lead_template.endswith((".", "!", "?")):
                lead_template += "."
            p2 += f" {lead_template}"
        if confirming:
            p2 += f" The remaining {len(confirming)} tracked pair(s) are confirming as expected."

    # ── Paragraph 3: system status and what to watch ────────────────────────
    _AUTOMATION_SECTIONS = (
        ("trading212_invest",  "T212 ETF"),
        ("trading212_stocks",  "T212 Stocks"),
    )
    enabled_paths: list[tuple[str, str]] = [
        (section, label) for section, label in _AUTOMATION_SECTIONS
        if config.get(section, {}).get("enabled")
    ]
    active_paths  = [label for section, label in enabled_paths if is_entry_allowed(config, regime_name, section)]
    blocked_paths = [label for section, label in enabled_paths if not is_entry_allowed(config, regime_name, section)]

    regime_label = regime_name.replace("_", " ").title()

    if regime_name == "TRENDING_BULL" and not overlays and active_paths:
        p3_lead = f"The system is actively entering new positions via {', '.join(active_paths)}"
        if blocked_paths:
            p3_lead += f", while {', '.join(blocked_paths)} remain blocked"
        p3_lead += "."
    else:
        reasons: list[str] = []
        if blocked_paths:
            reasons.append(
                f"{', '.join(blocked_paths)} {'is' if len(blocked_paths) == 1 else 'are'} blocked in the "
                f"current {regime_label} regime"
            )
        if overlays:
            overlay_labels = [
                _NARRATIVE_OVERLAY_LABELS.get(k, k.replace("_", " ").title())
                for k in sorted(overlays)
            ]
            reasons.append(f"active risk overlay{'s' if len(overlay_labels) > 1 else ''} ({', '.join(overlay_labels)})")
        if not reasons:
            reasons.append(f"the current {regime_label} regime does not meet entry criteria")
        p3_lead = (
            f"The system is not opening new positions — {'; '.join(reasons)}. "
            "Entries resume once a TRENDING_BULL regime is confirmed (ADX above 25 with price above "
            "the 200-day moving average, and VIX below 20)."
        )

    open_trades = open_trades or []
    if open_trades:
        status_counts = Counter(t.get("status", "ON TRACK") for t in open_trades)
        n_open = len(open_trades)
        breakdown = ", ".join(
            f"{status_counts[s]} {s.lower()}"
            for s in ("ON TRACK", "WEAKENING", "EXIT SIGNAL", "STOP LOSS")
            if status_counts.get(s)
        )
        p3_positions = (
            f"{n_open} existing position{'s are' if n_open != 1 else ' is'} being monitored"
            + (f" ({breakdown})" if breakdown else "")
            + "."
        )
    else:
        p3_positions = "No positions are currently open."

    p3 = f"{p3_lead} {p3_positions}"

    paragraphs = [p for p in (p1, p2, p3) if p]
    return "".join(
        f"<p style='margin:0 0 8px 0;line-height:1.6;font-size:0.9rem'>{html.escape(p)}</p>"
        for p in paragraphs
    )


def get_current_atr(ticker: str) -> Optional[float]:
    """Return the most recent ATR value for *ticker* from the indicators table."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT atr FROM indicators WHERE ticker = :t ORDER BY date DESC LIMIT 1"),
                {"t": ticker},
            ).mappings().first()
        if row and row.get("atr") is not None:
            return float(row["atr"])
    except Exception as exc:
        logger.warning("ATR read failed for %s: %s", ticker, exc)
    return None


def build_opportunities(
    all_signals: list[dict],
    open_tickers: set[str],
    active_overlays: set[str],
    watchlist: dict[str, str],
) -> list[dict]:
    """Return STRONG BUY/SELL signals for tickers not currently in open positions."""
    result = []
    for sig in all_signals:
        ticker = sig.get("ticker", "")
        if ticker in open_tickers:
            continue
        signal = sig.get("signal", "NEUTRAL")
        if signal not in ("STRONG_BUY", "STRONG_SELL"):
            continue

        overlay_note = ""
        if "MACRO_STRESS" in active_overlays and signal == "STRONG_BUY":
            overlay_note = "⚠ MACRO_STRESS — buy confidence reduced"

        result.append({
            "ticker":       ticker,
            "name":         watchlist.get(ticker, ticker),
            "signal":       signal,
            "score":        sig.get("confluence_score", 0),
            "trend":        sig.get("trend_score", 0),
            "mean_rev":     sig.get("mean_reversion_score", 0),
            "volatility":   sig.get("volatility_score", 0),
            "volume":       sig.get("volume_score", 0),
            "macro":        sig.get("macro_score", 0),
            "overlay_note": overlay_note,
        })

    return sorted(result, key=lambda x: abs(x.get("score") or 0), reverse=True)


def send_vix_spike_alert(
    vix_pct: float, vix_current: float, vix_prev: float, config: dict
) -> tuple[bool, str]:
    """Send an HTML+plain-text alert when VIX rises more than 20% in a single day.

    Args:
        vix_pct:      Intraday change in percent (positive number, e.g. 24.5).
        vix_current:  Current VIX close.
        vix_prev:     Previous VIX close.
        config:       App config dict.
    Returns:
        (success, message) tuple.
    """
    notif = config.get("notifications", {}).get("email", {})
    if not notif.get("enabled"):
        return False, "Email notifications not enabled in config.yaml"

    now     = datetime.utcnow()
    subject = (
        f"Volatility Spike — VIX +{vix_pct:.1f}% — Signals Refreshed "
        f"({_fmt_uk(now, '%Y-%m-%d %H:%M')})"
    )

    text_body = "\n".join([
        "Investment Research Dashboard — Volatility Spike Alert",
        f"Generated: {_fmt_uk(now, '%Y-%m-%d %H:%M')}",
        "",
        f"VIX has risen {vix_pct:.1f}% today ({vix_prev:.2f} → {vix_current:.2f}).",
        "This exceeds the 20% single-day threshold.",
        "",
        "Action taken: An immediate analysis run has been triggered.",
        "Signals have been refreshed to capture the current market conditions.",
        "",
        "Review your open positions on the Daily Briefing page.",
        "---",
        "To disable: set notifications.email.enabled: false in config.yaml",
    ])

    colour = "#dc2626"
    html_body = _email_wrap_h(
        _email_header_h(
            f"Volatility Spike: VIX +{vix_pct:.1f}%",
            _fmt_uk(now, "%Y-%m-%d %H:%M"),
            accent=colour,
        ),
        _section_h(
            "VOLATILITY SPIKE DETECTED",
            (
                f'<table width="100%" cellpadding="0" cellspacing="0" '
                f'style="border-collapse:collapse;margin-bottom:12px">'
                f'<tr>'
                f'<td style="padding:6px 0;font-size:13px;color:#64748b;width:40%">Previous VIX</td>'
                f'<td style="padding:6px 0;font-size:14px;font-weight:600;color:#1e293b">'
                f'{vix_prev:.2f}</td>'
                f'</tr>'
                f'<tr>'
                f'<td style="padding:6px 0;font-size:13px;color:#64748b">Current VIX</td>'
                f'<td style="padding:6px 0;font-size:14px;font-weight:700;color:{colour}">'
                f'{vix_current:.2f}</td>'
                f'</tr>'
                f'<tr>'
                f'<td style="padding:6px 0;font-size:13px;color:#64748b">Daily Change</td>'
                f'<td style="padding:6px 0">'
                f'{_badge_h(f"+{vix_pct:.1f}%", colour)}</td>'
                f'</tr>'
                f'</table>'
                f'<p style="margin:8px 0 0;font-size:13px;color:#475569">'
                f'An immediate analysis run has been triggered. '
                f'Signals have been refreshed to capture current conditions. '
                f'Review open positions on the Daily Briefing page.</p>'
            ),
            border=colour,
        ),
        _email_footer_h("To disable: set notifications.email.enabled: false in config.yaml"),
    )

    ok, msg = _smtp_send(subject, text_body, html_body, config)
    if ok:
        set_metadata("last_vix_spike_alert", now.date().isoformat())
        return True, f"VIX spike alert sent ({vix_pct:.1f}%)"
    return False, msg


def _build_test_html(
    config: dict,
    regime: str,
    last_analysis: "datetime | None",
    open_trade_count: int,
) -> str:
    """Build an HTML test-notification email body."""
    notif     = config.get("notifications", {}).get("email", {})
    from_addr = notif.get("from_address") or notif.get("smtp_user", "—")
    to_addrs  = notif.get("to_addresses") or []
    relay     = f"{notif.get('smtp_host', '—')}:{notif.get('smtp_port', '—')}"
    now       = datetime.utcnow()
    last_str  = _fmt_uk(last_analysis, "%Y-%m-%d %H:%M") if last_analysis else "—"

    rows_data = [
        ("Regime",        regime.replace("_", " ")),
        ("Last analysis", last_str),
        ("Open trades",   str(open_trade_count)),
        ("From",          from_addr),
        ("To",            ", ".join(to_addrs) if to_addrs else "—"),
        ("Via",           relay),
    ]
    table_html = (
        '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
        + "".join(
            f'<tr>'
            f'<td style="color:#64748b;font-size:13px;padding:6px 0;width:35%;'
            f'border-bottom:1px solid #f1f5f9">{k}</td>'
            f'<td style="font-size:13px;font-weight:600;padding:6px 0;'
            f'border-bottom:1px solid #f1f5f9">{v}</td>'
            f'</tr>'
            for k, v in rows_data
        )
        + '</table>'
    )

    return _email_wrap_h(
        _email_header_h(
            "Test Notification",
            f"Sent {_fmt_uk(now, '%Y-%m-%d %H:%M')}",
        ),
        _section_h("SYSTEM STATUS", table_html, "#3b82f6")
        + _section_h(
            "",
            '<p style="margin:0;font-size:13px;color:#1e293b">'
            'If you received this, email delivery is working correctly.</p>',
            "#16a34a",
        ),
        _email_footer_h(
            'To disable alerts: set '
            '<code style="background-color:#2d3748;padding:1px 4px;border-radius:3px;color:#a5f3fc">'
            'notifications.email.enabled: false</code>'
        ),
    )


def send_test_email(
    config: dict,
    regime: str,
    last_analysis: "datetime | None",
    open_trade_count: int,
) -> "tuple[bool, str]":
    """Send an HTML+plain-text test notification to verify SMTP delivery.

    Bypasses notifications.email.enabled — the purpose is to verify delivery
    works before committing to enabling live alerts.
    """
    notif     = config.get("notifications", {}).get("email", {})
    to_addrs  = notif.get("to_addresses") or []
    from_addr = notif.get("from_address") or notif.get("smtp_user", "")

    if not to_addrs:
        return False, "No recipient addresses configured in notifications.email.to_addresses"

    now          = datetime.utcnow()
    last_run_str = _fmt_uk(last_analysis, "%Y-%m-%d %H:%M") if last_analysis else "—"

    lines = [
        "Investment Research Dashboard — Test Notification",
        f"Sent:      {_fmt_uk(now, '%Y-%m-%d %H:%M')}",
        f"From:      {from_addr}",
        f"To:        {', '.join(to_addrs)}",
        f"Via:       {notif.get('smtp_host','—')}:{notif.get('smtp_port','—')}",
        "",
        "CURRENT SYSTEM STATUS",
        "=" * 45,
        f"Regime:        {regime.replace('_', ' ')}",
        f"Last analysis: {last_run_str}",
        f"Open trades:   {open_trade_count}",
        "",
        "---",
        "If you received this, email delivery is working correctly.",
        "To disable alerts: set notifications.email.enabled: false in config.yaml",
    ]

    html_body = _build_test_html(config, regime, last_analysis, open_trade_count)
    ok, msg   = _smtp_send("Investment Research — Test Notification",
                            "\n".join(lines), html_body, config)
    if ok:
        return True, f"Test email sent to {', '.join(to_addrs)}"
    return False, msg


# ---------------------------------------------------------------------------
# Weekly summary email
# ---------------------------------------------------------------------------

_OVERLAY_DESCRIPTIONS: dict[str, str] = {
    "MACRO_STRESS":     "Bonds/equities decoupling — flight-to-safety broken (buy confidence -20%)",
    "VOL_REGIME_SHIFT": "VIX and equities moving together — atypical fear-gauge behaviour",
    "CREDIT_STRESS":    "Credit/equities decoupling — HY stress may precede equity stress",
}

_REGIME_DESCRIPTIONS: dict[str, str] = {
    "TRENDING_BULL":   "Strong uptrend — momentum and trend systems in focus",
    "TRENDING_BEAR":   "Sustained downtrend — defensive posture and short signals preferred",
    "RANGING_LOW_VOL": "Tight low-volatility range — mean-reversion signals more reliable",
    "HIGH_VOLATILITY": "Elevated risk environment — reduce position size, watch stop losses",
    "MIXED":           "Mixed signals — ADX between thresholds, neutral positioning preferred",
}

def _macro_labels(config: dict) -> dict[str, str]:
    """Return series_id → label from config, falling back to sensible defaults."""
    defaults = {
        "T10Y2Y":       "Yield curve (10Y−2Y)",
        "T10Y3M":       "Yield curve (10Y−3M)",
        "BAMLH0A0HYM2": "High yield OAS",
        "BAMLC0A0CM":   "Investment grade OAS",
        "DFII10":       "10Y real yield (TIPS)",
        "T10YIE":       "Breakeven inflation (10Y)",
    }
    cfg_series = config.get("macro", {}).get("fred_series", {})
    return {sid: scfg.get("label", defaults.get(sid, sid)) for sid, scfg in cfg_series.items()}


def _gather_weekly_data(config: dict) -> dict:
    """Query all data needed for the weekly summary email. DB-only, no external API calls."""
    now      = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    engine = get_engine()
    with engine.connect() as conn:
        # Latest signal per ticker (current snapshot)
        curr_sig_rows = conn.execute(text("""
            SELECT s.ticker, s.signal, s.confluence_score, s.regime, s.date,
                   s.trend_score, s.mean_reversion_score, s.volatility_score,
                   s.volume_score, s.macro_score
            FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM signals GROUP BY ticker
            ) l ON s.ticker = l.ticker AND s.date = l.md
            ORDER BY s.confluence_score DESC
        """)).mappings().all()

        # Most-recent signal BEFORE 7 days ago (for performance comparison)
        week_sig_rows = conn.execute(text("""
            SELECT s.ticker, s.signal, s.confluence_score, s.date,
                   pd.close AS price_at_signal
            FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(date) AS md
                FROM signals WHERE date <= :week_ago
                GROUP BY ticker
            ) old ON s.ticker = old.ticker AND s.date = old.md
            LEFT JOIN price_data pd ON pd.ticker = s.ticker AND pd.date = s.date
            WHERE s.signal IN ('STRONG_BUY','BUY','SELL','STRONG_SELL')
        """), {"week_ago": week_ago}).mappings().all()

        # Latest close per ticker (for P&L calculations — no API call)
        price_rows = conn.execute(text("""
            SELECT p.ticker, p.close, p.date
            FROM price_data p
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM price_data GROUP BY ticker
            ) l ON p.ticker = l.ticker AND p.date = l.md
        """)).mappings().all()

        # VIX level 7 days ago (for change display)
        vix_prev_row = conn.execute(text("""
            SELECT close FROM price_data
            WHERE ticker = '^VIX' AND date <= :week_ago
            ORDER BY date DESC LIMIT 1
        """), {"week_ago": week_ago}).fetchone()

        # Latest FRED macro indicator values
        macro_latest_rows = conn.execute(text("""
            SELECT m.series_id, m.value, m.score, m.z_score_2y, m.date
            FROM macro_indicators m
            INNER JOIN (
                SELECT series_id, MAX(date) AS md FROM macro_indicators GROUP BY series_id
            ) l ON m.series_id = l.series_id AND m.date = l.md
        """)).mappings().all()

        # FRED values from ~7 days ago (for week-over-week change)
        macro_prev_rows = conn.execute(text("""
            SELECT m.series_id, m.value
            FROM macro_indicators m
            INNER JOIN (
                SELECT series_id, MAX(date) AS md
                FROM macro_indicators WHERE date <= :week_ago
                GROUP BY series_id
            ) old ON m.series_id = old.series_id AND m.date = old.md
        """), {"week_ago": week_ago}).mappings().all()

        # Diverging correlation pairs
        div_rows = conn.execute(text("""
            SELECT c.ticker_a, c.ticker_b, c.divergence, c.z_score,
                   c.corr_30d, c.pair_score
            FROM correlations c
            INNER JOIN (
                SELECT ticker_a, ticker_b, MAX(date) AS md
                FROM correlations GROUP BY ticker_a, ticker_b
            ) l ON c.ticker_a = l.ticker_a AND c.ticker_b = l.ticker_b AND c.date = l.md
            WHERE c.divergence = 'DIVERGING'
            ORDER BY ABS(c.z_score) DESC
        """)).mappings().all()

        # Active catch-up opportunity flags
        flag_rows = conn.execute(text("""
            SELECT of.ticker, of.flag_type, of.z_score, of.date
            FROM opportunity_flags of
            INNER JOIN (
                SELECT ticker, MAX(date) AS md FROM opportunity_flags GROUP BY ticker
            ) l ON of.ticker = l.ticker AND of.date = l.md
            WHERE of.date >= :week_ago
            ORDER BY of.z_score DESC
        """), {"week_ago": week_ago}).mappings().all()

    current_prices = {
        r["ticker"]: float(r["close"])
        for r in price_rows if r["close"] is not None
    }

    # Trades — exclude VIRTUAL (legacy rows only; no longer opened live) from
    # real accuracy stats.
    closed_week   = [t for t in get_closed_trades(from_date=week_ago) if t.get("account_type") != "VIRTUAL"]
    all_closed    = [t for t in get_closed_trades()        if t.get("account_type") != "VIRTUAL"]

    return {
        "now":            now,
        "week_ago":       week_ago,
        "curr_signals":   [dict(r) for r in curr_sig_rows],
        "week_signals":   [dict(r) for r in week_sig_rows],
        "current_prices": current_prices,
        "vix_prev":       float(vix_prev_row[0]) if vix_prev_row and vix_prev_row[0] is not None else None,
        "macro_latest":   {r["series_id"]: dict(r) for r in macro_latest_rows},
        "macro_prev":     {r["series_id"]: float(r["value"]) for r in macro_prev_rows if r["value"] is not None},
        "active_overlays": [o.strip() for o in (get_metadata_str("active_overlays") or "none").split(",")
                            if o.strip() not in ("", "none")],
        "div_pairs":      [dict(r) for r in div_rows],
        "opp_flags":      [dict(r) for r in flag_rows],
        "closed_week":    closed_week,
        "all_closed":     all_closed,
    }


def _build_weekly_email(data: dict, config: dict) -> str:
    """Format the weekly summary as a plain-text email body."""
    now      = data["now"]
    week_ago = data["week_ago"]

    L: list[str] = []

    def rule(char: str = "=", w: int = 60) -> None:
        L.append(char * w)

    def section(title: str) -> None:
        L.append("")
        rule()
        L.append(title)
        rule()

    # ── Header ────────────────────────────────────────────────────────
    L.append("INVESTMENT RESEARCH — WEEKLY SUMMARY")
    L.append(
        f"Week of {week_ago.strftime('%d %b')} "
        f"to {now.strftime('%d %b %Y')}  |  "
        f"Generated {_fmt_uk(now, '%Y-%m-%d %H:%M')}"
    )

    # ── Market Regime & Overlays ──────────────────────────────────────
    section("MARKET REGIME")
    curr = data["curr_signals"]
    regime = ((curr[0].get("regime") or "MIXED") if curr else "MIXED").replace("UNKNOWN", "MIXED")
    L.append(f"Regime:  {regime.replace('_', ' ')}  —  {_REGIME_DESCRIPTIONS.get(regime, '')}")
    overlays = data["active_overlays"]
    if overlays:
        L.append("")
        L.append("Active overlays (conditions affecting signal confidence):")
        for o in overlays:
            L.append(f"  ! {_OVERLAY_DESCRIPTIONS.get(o, o)}")
    else:
        L.append("Overlays: none active — all cross-asset relationships within normal range")

    # ── Signal Snapshot ───────────────────────────────────────────────
    section("SIGNAL SNAPSHOT")
    tradeable: set[str] = {
        e["ticker"]
        for g in config.get("watchlist", {}).values()
        if isinstance(g, list)
        for e in g
        if isinstance(e, dict) and e.get("tradeable")
    }
    by_sig: dict[str, list[str]] = defaultdict(list)
    for s in curr:
        by_sig[s["signal"]].append(s["ticker"])

    for sig in ("STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"):
        tickers = by_sig.get(sig, [])
        if not tickers:
            continue
        tr = [t for t in tickers if t in tradeable]
        an = [t for t in tickers if t not in tradeable]
        parts = (", ".join(tr) if tr else "") + (f"  [analysis: {', '.join(an)}]" if an else "")
        L.append(f"  {sig:<12} ({len(tickers):>2}):  {parts.strip() or '—'}")

    strong_buy_tr  = [t for t in by_sig.get("STRONG_BUY",  []) if t in tradeable]
    strong_sell_tr = [t for t in by_sig.get("STRONG_SELL", []) if t in tradeable]
    if strong_buy_tr or strong_sell_tr:
        L.append("")
        L.append(
            f"  Actionable this week: "
            f"{len(strong_buy_tr)} STRONG BUY, {len(strong_sell_tr)} STRONG SELL (tradeable only)"
        )

    # ── Last Week's Signal Performance ────────────────────────────────
    section("LAST WEEK'S SIGNAL PERFORMANCE  (7-day price change)")
    week_sigs    = data["week_signals"]
    curr_prices  = data["current_prices"]
    perf_rows: list[tuple] = []
    for ws in week_sigs:
        ticker = ws["ticker"]
        sig    = ws["signal"]
        entry  = ws.get("price_at_signal")
        curr_p = curr_prices.get(ticker)
        if not entry or not curr_p:
            continue
        ret = (curr_p - entry) / entry * 100
        correct = (ret > 0) if sig in ("STRONG_BUY", "BUY") else (ret < 0) if sig in ("STRONG_SELL", "SELL") else None
        perf_rows.append((ticker, sig, entry, curr_p, ret, correct))

    if perf_rows:
        perf_rows.sort(key=lambda x: abs(x[4]), reverse=True)
        n_correct = sum(1 for r in perf_rows if r[5] is True)
        n_total   = sum(1 for r in perf_rows if r[5] is not None)
        L.append(f"Based on signals as of ~{week_ago.strftime('%d %b %Y')}:")
        L.append("")
        for ticker, sig, entry, curr_p, ret, correct in perf_rows[:15]:
            chk = "[Y]" if correct else ("[N]" if correct is False else "[-]")
            arrow = "+" if ret >= 0 else ""
            L.append(f"  {chk}  {ticker:<12} {sig:<12}  {arrow}{ret:.1f}%  ({entry:.4g} -> {curr_p:.4g})")
        if len(perf_rows) > 15:
            L.append(f"  ... and {len(perf_rows) - 15} more tickers")
        L.append("")
        if n_total > 0:
            hit = n_correct / n_total * 100
            L.append(f"  7-day directional hit rate: {n_correct}/{n_total}  ({hit:.0f}%)")
    else:
        L.append("  No prior-week signal data available yet.")
        L.append("  (Needs signals from 7+ days ago to be stored in the database.)")

    # ── Closed This Week ──────────────────────────────────────────────
    closed = data["closed_week"]
    section(f"TRADES CLOSED THIS WEEK  ({len(closed)} trade{'s' if len(closed) != 1 else ''})")
    if not closed:
        L.append("  No trades closed this week.")
    else:
        wins   = [t for t in closed if float(t.get("pnl_pct") or 0) > 0]
        losses = [t for t in closed if float(t.get("pnl_pct") or 0) <= 0]
        net    = sum(float(t.get("pnl_pct") or 0) * 100 for t in closed)
        L.append(f"  {len(wins)} win{'s' if len(wins)!=1 else ''}, {len(losses)} loss{'es' if len(losses)!=1 else ''}  |  Net: {net:+.1f}%")
        L.append("")
        for t in closed:
            pnl  = float(t.get("pnl_pct") or 0) * 100
            hold = t.get("holding_days", 0)
            wl   = "W" if pnl > 0 else "L"
            sig  = t.get("signal_type", "?")
            L.append(
                f"  [{wl}]  {t.get('direction','?'):<5} {t.get('ticker','?'):<12}  "
                f"{pnl:+.1f}%  {hold}d  ({sig})"
            )

    # ── Signal Accuracy Progress ──────────────────────────────────────
    section("SIGNAL ACCURACY PROGRESS")
    all_c    = data["all_closed"]
    min_trd  = config.get("learning", {}).get("min_trades_before_adjustment", 20)
    n        = len(all_c)
    if n == 0:
        L.append("  No closed trades yet — accuracy tracking begins after the first closed trade.")
    else:
        wins_all = [t for t in all_c if float(t.get("pnl_pct") or 0) > 0]
        wr       = len(wins_all) / n * 100
        pct_done = min(n / min_trd * 100, 100)
        bar_w    = 20
        filled   = int(pct_done / 100 * bar_w)
        bar      = "#" * filled + "." * (bar_w - filled)
        L.append(f"  Closed trades:    {n}  (need {min_trd} for adaptive weight learning)")
        L.append(f"  Progress:         [{bar}]  {n}/{min_trd}  ({pct_done:.0f}%)")
        L.append(f"  Overall win rate: {wr:.1f}%")
        # By signal type
        sig_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for t in all_c:
            s = t.get("signal_type", "UNKNOWN") or "UNKNOWN"
            sig_stats[s][0] += 1
            if float(t.get("pnl_pct") or 0) > 0:
                sig_stats[s][1] += 1
        if sig_stats:
            L.append("")
            L.append("  Win rate by signal type:")
            for sig, (cnt, w) in sorted(sig_stats.items(), key=lambda x: -x[1][0]):
                wr_s = w / cnt * 100 if cnt else 0
                L.append(f"    {sig:<14} {cnt:>3} trades   {wr_s:>5.1f}% win rate")
        # Best and worst performing tickers
        ticker_pnl: dict[str, list[float]] = defaultdict(list)
        for t in all_c:
            ticker_pnl[t.get("ticker", "?")].append(float(t.get("pnl_pct") or 0) * 100)
        ticker_avg = {tk: sum(v)/len(v) for tk, v in ticker_pnl.items() if v}
        if len(ticker_avg) >= 2:
            best   = max(ticker_avg, key=ticker_avg.get)
            worst  = min(ticker_avg, key=ticker_avg.get)
            L.append("")
            L.append(f"  Best ticker:   {best:<12} avg {ticker_avg[best]:+.1f}% ({len(ticker_pnl[best])} trades)")
            L.append(f"  Worst ticker:  {worst:<12} avg {ticker_avg[worst]:+.1f}% ({len(ticker_pnl[worst])} trades)")

    # ── Macro Pulse ───────────────────────────────────────────────────
    section("MACRO PULSE")
    ml   = data["macro_latest"]
    mp   = data["macro_prev"]
    vix_now  = curr_prices.get("^VIX")
    vix_prev = data["vix_prev"]
    if vix_now is not None:
        vix_chg = f"  ({vix_now - vix_prev:+.1f} wow)" if vix_prev else ""
        level = "low" if vix_now < 15 else ("elevated" if vix_now < 25 else "HIGH")
        L.append(f"  {'VIX (fear gauge)':<30} {vix_now:>8.1f}  — {level}{vix_chg}")

    printed = False
    for series_id, label in _macro_labels(config).items():
        row = ml.get(series_id)
        if not row or row.get("value") is None:
            continue
        val_now = float(row["value"])
        prev    = mp.get(series_id)
        chg_str = ""
        if prev is not None:
            diff    = val_now - prev
            pct     = diff / abs(prev) * 100 if prev != 0 else 0
            arrow   = "+" if diff >= 0 else ""
            chg_str = f"  ({arrow}{diff:.3f} / {arrow}{pct:.1f}% wow)"
        score   = int(row.get("score") or 0)
        s_label = " [bullish]" if score == 1 else (" [bearish]" if score == -1 else "")
        L.append(f"  {label:<30} {val_now:>8.3f}{chg_str}{s_label}")
        printed = True

    if not printed and vix_now is None:
        L.append("  No FRED macro data — set macro.fred_api_key in config.yaml to enable.")
    else:
        L.append("")
        L.append("  Note: yield curve > 0 = normal slope; HY OAS > 5.0% = credit stress zone.")

    # ── Watch List ────────────────────────────────────────────────────
    section("WATCH LIST")
    div = data["div_pairs"]
    if div:
        L.append("  Diverging cross-asset relationships (worth monitoring):")
        for p in div[:6]:
            z    = float(p.get("z_score") or 0)
            exp  = "positive" if int(p.get("pair_score") or 1) == 1 else "inverse"
            L.append(
                f"    {p['ticker_a']:<10} / {p['ticker_b']:<10}  "
                f"expected {exp}, currently diverging  (z={z:+.1f})"
            )
    else:
        L.append("  No diverging pairs — all cross-asset relationships within normal range.")

    flags = data["opp_flags"]
    if flags:
        L.append("")
        L.append("  Catch-up opportunities flagged this week:")
        for f in flags[:5]:
            z = float(f.get("z_score") or 0)
            L.append(f"    {f['ticker']:<14}  {f['flag_type']}  (z={z:+.1f})")

    # ── Footer ────────────────────────────────────────────────────────
    L.append("")
    rule("-", 60)
    L.append("Investment Research Dashboard  |  research.example.com")
    next_sunday = now + timedelta(days=(6 - now.weekday() + 7) % 7 or 7)
    _uk_10 = _fmt_uk(next_sunday.replace(hour=10, minute=0, second=0, tzinfo=timezone.utc), "%H:%M")
    L.append(f"Next weekly summary: Sunday {next_sunday.strftime('%d %b %Y')} at {_uk_10}")
    L.append("To disable: notifications.weekly_summary.enabled: false in config.yaml")
    return "\n".join(L)


def _build_weekly_email_html(data: dict, config: dict) -> str:  # noqa: C901
    """Format the weekly summary as a styled HTML email body."""
    now          = data["now"]
    week_ago     = data["week_ago"]
    curr         = data["curr_signals"]
    curr_prices  = data["current_prices"]

    tradeable: set[str] = {
        e["ticker"]
        for g in config.get("watchlist", {}).values()
        if isinstance(g, list)
        for e in g
        if isinstance(e, dict) and e.get("tradeable")
    }

    sections = ""

    # ── Market Regime & Overlays ──────────────────────────────────────
    regime = ((curr[0].get("regime") or "MIXED") if curr else "MIXED").replace("UNKNOWN", "MIXED")
    regime_bg, regime_fg = {
        "TRENDING_BULL":   ("#16a34a", "#ffffff"),
        "TRENDING_BEAR":   ("#dc2626", "#ffffff"),
        "RANGING_LOW_VOL": ("#3b82f6", "#ffffff"),
        "HIGH_VOLATILITY": ("#ea580c", "#ffffff"),
        "MIXED":           ("#64748b", "#ffffff"),
    }.get(regime, ("#1e293b", "#ffffff"))

    regime_badge = _badge_h(regime.replace("_", " "), regime_bg, regime_fg, size=13)
    regime_desc  = _REGIME_DESCRIPTIONS.get(regime, "")

    overlays     = data["active_overlays"]
    overlay_html = ""
    for o in overlays:
        overlay_html += (
            f'<div style="background-color:#fffbeb;border-left:3px solid #d97706;'
            f'padding:8px 12px;margin-top:8px;border-radius:0 4px 4px 0;'
            f'font-size:13px;color:#92400e">&#9888; {_OVERLAY_DESCRIPTIONS.get(o, o)}</div>'
        )
    if not overlays:
        overlay_html = (
            '<p style="margin:8px 0 0;font-size:13px;color:#64748b">'
            'No overlays active — all cross-asset relationships within normal range</p>'
        )

    sections += _section_h(
        "MARKET REGIME",
        f'<div>{regime_badge} <span style="font-size:13px;color:#475569;margin-left:8px">'
        f'{regime_desc}</span></div>{overlay_html}',
        regime_bg,
    )

    # ── Signal Snapshot ───────────────────────────────────────────────
    by_sig: dict[str, list[str]] = defaultdict(list)
    for s in curr:
        by_sig[s["signal"]].append(s["ticker"])

    sig_rows = []
    for sig in ("STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"):
        tickers = by_sig.get(sig, [])
        if not tickers:
            continue
        tr = [t for t in tickers if t in tradeable]
        an = [t for t in tickers if t not in tradeable]
        parts = ""
        if tr:
            parts += ", ".join(f"<strong>{t}</strong>" for t in tr)
        if an:
            if parts:
                parts += ' &nbsp;<span style="color:#94a3b8">+'
            else:
                parts += '<span style="color:#94a3b8">'
            parts += f'{", ".join(an)}</span>'
        sig_rows.append([_sig_badge_h(sig), parts or "—", str(len(tickers)), str(len(tr))])

    strong_buy_tr  = [t for t in by_sig.get("STRONG_BUY",  []) if t in tradeable]
    strong_sell_tr = [t for t in by_sig.get("STRONG_SELL", []) if t in tradeable]
    summary_line   = (
        f'<p style="margin:10px 0 0;font-size:13px;color:#475569">'
        f'<strong>{len(strong_buy_tr)}</strong> actionable STRONG BUY &nbsp;·&nbsp; '
        f'<strong>{len(strong_sell_tr)}</strong> actionable STRONG SELL (tradeable only)</p>'
    )
    sections += _section_h(
        "SIGNAL SNAPSHOT",
        (_table_h(["Signal", "Assets", "Total", "Tradeable"], sig_rows) if sig_rows
         else '<p style="color:#64748b;font-size:13px">No signals.</p>') + summary_line,
        "#3b82f6",
    )

    # ── Last Week's Signal Performance ────────────────────────────────
    perf_rows = []
    for ws in data["week_signals"]:
        ticker = ws["ticker"]
        sig    = ws["signal"]
        entry  = ws.get("price_at_signal")
        curr_p = curr_prices.get(ticker)
        if not entry or not curr_p:
            continue
        ret     = (curr_p - entry) / entry * 100
        correct = (
            (ret > 0) if sig in ("STRONG_BUY", "BUY")
            else (ret < 0) if sig in ("STRONG_SELL", "SELL")
            else None
        )
        chk = (
            '<span style="color:#16a34a;font-weight:700">&#10003;</span>' if correct is True
            else '<span style="color:#dc2626;font-weight:700">&#10007;</span>' if correct is False
            else '<span style="color:#94a3b8">—</span>'
        )
        pc = _pnl_color(ret)
        perf_rows.append((ticker, sig, entry, curr_p, ret, correct, pc, chk))

    if perf_rows:
        perf_rows.sort(key=lambda x: abs(x[4]), reverse=True)
        n_correct = sum(1 for r in perf_rows if r[5] is True)
        n_total   = sum(1 for r in perf_rows if r[5] is not None)
        hit       = n_correct / n_total * 100 if n_total else 0
        hit_color = "#16a34a" if hit >= 50 else "#dc2626"

        table_rows = [
            [
                chk,
                f"<strong>{ticker}</strong>",
                _sig_badge_h(sig),
                f'<span style="color:{pc};font-weight:600">{ret:+.1f}%</span>',
                f'<span style="color:#94a3b8;font-size:12px">{entry:.4g} &rarr; {curr_p:.4g}</span>',
            ]
            for ticker, sig, entry, curr_p, ret, _, pc, chk in perf_rows[:15]
        ]
        hit_html = (
            f'<p style="margin:10px 0 0;font-size:13px">'
            f'<strong style="color:{hit_color}">{hit:.0f}%</strong> directional hit rate '
            f'({n_correct}/{n_total} correct)</p>'
        )
        perf_html = _table_h(["", "Ticker", "Signal", "7-day Return", "Price"], table_rows) + hit_html
        if len(perf_rows) > 15:
            perf_html += (
                f'<p style="margin:6px 0 0;font-size:12px;color:#94a3b8">'
                f'… and {len(perf_rows) - 15} more tickers</p>'
            )
    else:
        perf_html = (
            '<p style="color:#64748b;font-size:13px">'
            'No prior-week signal data — needs signals stored 7+ days ago.</p>'
        )

    sections += _section_h(
        f"LAST WEEK'S SIGNAL PERFORMANCE &nbsp;"
        f'<span style="font-weight:400;color:#94a3b8;font-size:11px">'
        f'~{week_ago.strftime("%d %b")}</span>',
        perf_html,
        "#8b5cf6",
    )

    # ── Closed This Week ──────────────────────────────────────────────
    closed = data["closed_week"]
    if closed:
        wins = [t for t in closed if float(t.get("pnl_pct") or 0) > 0]
        losses = [t for t in closed if float(t.get("pnl_pct") or 0) <= 0]
        net  = sum(float(t.get("pnl_pct") or 0) * 100 for t in closed)
        summary = (
            f'<p style="margin:0 0 10px;font-size:13px">'
            f'{_badge_h("W", "#16a34a")} {len(wins)} wins &nbsp; '
            f'{_badge_h("L", "#dc2626")} {len(losses)} losses &nbsp;·&nbsp; '
            f'Net: <strong style="color:{_pnl_color(net)}">{net:+.1f}%</strong></p>'
        )
        cl_rows = []
        for t in closed:
            pnl  = float(t.get("pnl_pct") or 0) * 100
            hold = t.get("holding_days", 0)
            sig  = t.get("signal_type", "?")
            d    = t.get("direction", "?")
            cl_rows.append([
                _badge_h("W", "#16a34a") if pnl > 0 else _badge_h("L", "#dc2626"),
                _badge_h(d, "#1e293b" if d == "LONG" else "#475569"),
                f"<strong>{t.get('ticker','?')}</strong>",
                f'<span style="color:{_pnl_color(pnl)};font-weight:600">{pnl:+.1f}%</span>',
                f"{hold}d",
                _sig_badge_h(sig) if sig in _SIG_STYLES_H else sig,
            ])
        closed_html = summary + _table_h(
            ["", "Dir", "Ticker", "P&amp;L", "Hold", "Entry Signal"], cl_rows
        )
    else:
        closed_html = '<p style="color:#64748b;font-size:13px">No trades closed this week.</p>'

    sections += _section_h(f"TRADES CLOSED THIS WEEK ({len(closed)})", closed_html, "#7c3aed")

    # ── Signal Accuracy Progress ──────────────────────────────────────
    all_c   = data["all_closed"]
    min_trd = config.get("learning", {}).get("min_trades_before_adjustment", 20)
    n       = len(all_c)

    if n == 0:
        acc_html = (
            '<p style="color:#64748b;font-size:13px">'
            'No closed trades yet — accuracy tracking begins after the first closed trade.</p>'
        )
    else:
        wins_all = [t for t in all_c if float(t.get("pnl_pct") or 0) > 0]
        wr       = len(wins_all) / n * 100
        pct_done = min(n / min_trd * 100, 100)
        bar_w    = 300
        fill_w   = int(pct_done / 100 * bar_w)

        bar_html = (
            f'<div style="background-color:#e2e8f0;border-radius:4px;height:10px;'
            f'width:{bar_w}px;max-width:100%;margin:8px 0">'
            f'<div style="background-color:#3b82f6;width:{fill_w}px;height:10px;'
            f'border-radius:4px"></div></div>'
        )
        wr_color = "#16a34a" if wr >= 50 else "#dc2626"
        stats    = (
            f'<p style="margin:6px 0;font-size:13px">'
            f'<strong>{n}</strong> closed trades / {min_trd} target &nbsp;·&nbsp; '
            f'Win rate: <strong style="color:{wr_color}">{wr:.1f}%</strong></p>'
        )
        sig_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for t in all_c:
            s = t.get("signal_type", "UNKNOWN") or "UNKNOWN"
            sig_stats[s][0] += 1
            if float(t.get("pnl_pct") or 0) > 0:
                sig_stats[s][1] += 1

        sig_rows_acc = [
            [
                _sig_badge_h(sig) if sig in _SIG_STYLES_H else sig,
                str(cnt),
                f'<span style="color:{"#16a34a" if (w/cnt*100 if cnt else 0) >= 50 else "#dc2626"};'
                f'font-weight:600">{w/cnt*100 if cnt else 0:.1f}%</span>',
            ]
            for sig, (cnt, w) in sorted(sig_stats.items(), key=lambda x: -x[1][0])
        ]

        ticker_pnl: dict[str, list[float]] = defaultdict(list)
        for t in all_c:
            ticker_pnl[t.get("ticker", "?")].append(float(t.get("pnl_pct") or 0) * 100)
        ticker_avg = {tk: sum(v) / len(v) for tk, v in ticker_pnl.items() if v}

        best_worst = ""
        if len(ticker_avg) >= 2:
            best  = max(ticker_avg, key=ticker_avg.get)  # type: ignore[arg-type]
            worst = min(ticker_avg, key=ticker_avg.get)  # type: ignore[arg-type]
            best_worst = (
                f'<p style="margin:10px 0 0;font-size:13px;color:#475569">'
                f'Best: <strong>{best}</strong> avg '
                f'<span style="color:#16a34a">{ticker_avg[best]:+.1f}%</span>'
                f' &nbsp;·&nbsp; '
                f'Worst: <strong>{worst}</strong> avg '
                f'<span style="color:#dc2626">{ticker_avg[worst]:+.1f}%</span></p>'
            )

        acc_html = (
            bar_html + stats
            + (_table_h(["Signal Type", "Trades", "Win Rate"], sig_rows_acc) if sig_rows_acc else "")
            + best_worst
        )

    sections += _section_h("SIGNAL ACCURACY PROGRESS", acc_html, "#f59e0b")

    # ── Macro Pulse ───────────────────────────────────────────────────
    ml       = data["macro_latest"]
    mp       = data["macro_prev"]
    vix_now  = curr_prices.get("^VIX")
    vix_prev = data["vix_prev"]

    macro_rows = []
    if vix_now is not None:
        vix_chg = f" ({vix_now - vix_prev:+.1f} wow)" if vix_prev else ""
        lvl     = "low" if vix_now < 15 else ("elevated" if vix_now < 25 else "HIGH")
        lvl_c   = "#16a34a" if vix_now < 15 else ("#d97706" if vix_now < 25 else "#dc2626")
        macro_rows.append([
            "VIX (fear gauge)",
            f"<strong>{vix_now:.1f}</strong>",
            f'<span style="color:#64748b;font-size:12px">{vix_chg}</span>',
            f'<span style="color:{lvl_c};font-size:12px">{lvl}</span>',
        ])

    for series_id, label in _macro_labels(config).items():
        row = ml.get(series_id)
        if not row or row.get("value") is None:
            continue
        val_now = float(row["value"])
        prev    = mp.get(series_id)
        chg_str = ""
        if prev is not None:
            diff    = val_now - prev
            pct     = diff / abs(prev) * 100 if prev != 0 else 0
            arrow   = "&#9650;" if diff > 0 else ("&#9660;" if diff < 0 else "&#9654;")
            chg_c   = "#dc2626" if diff > 0.05 else ("#16a34a" if diff < -0.05 else "#64748b")
            chg_str = (
                f'<span style="color:{chg_c};font-size:12px">'
                f'{arrow} {abs(diff):.3f} ({pct:+.1f}% wow)</span>'
            )
        score   = int(row.get("score") or 0)
        s_label = (
            '<span style="color:#16a34a;font-size:12px">&#8593; bullish</span>' if score == 1
            else '<span style="color:#dc2626;font-size:12px">&#8595; bearish</span>' if score == -1
            else '<span style="color:#94a3b8;font-size:12px">neutral</span>'
        )
        macro_rows.append([label, f"<strong>{val_now:.3f}</strong>", chg_str, s_label])

    if macro_rows:
        macro_html = _table_h(["Indicator", "Value", "WoW Change", "Signal"], macro_rows)
        macro_html += (
            '<p style="margin:8px 0 0;font-size:12px;color:#94a3b8">'
            'HY OAS &gt; 5% = credit stress zone. Yield curve &gt; 0 = normal slope.</p>'
        )
    else:
        macro_html = (
            '<p style="color:#64748b;font-size:13px">'
            'No FRED data available — set macro.fred_api_key in config.yaml.</p>'
        )

    sections += _section_h("MACRO PULSE", macro_html, "#0f766e")

    # ── Watch List ────────────────────────────────────────────────────
    div_pairs = data["div_pairs"]
    watch_html = ""
    if div_pairs:
        watch_html += (
            '<p style="margin:0 0 8px;font-size:13px;color:#475569;font-weight:600">'
            'Diverging cross-asset relationships:</p>'
        )
        for p in div_pairs[:6]:
            z   = float(p.get("z_score") or 0)
            ps  = int(p.get("pair_score") or 1)
            exp = "positive" if ps == 1 else "inverse"
            z_c = "#dc2626" if abs(z) >= 2.5 else "#d97706"
            watch_html += (
                f'<div style="border-left:3px solid {z_c};padding:6px 10px;margin-bottom:6px;'
                f'background-color:#fffbeb;border-radius:0 4px 4px 0;font-size:13px">'
                f'<strong>{p["ticker_a"]}</strong> / <strong>{p["ticker_b"]}</strong>'
                f' &nbsp;—&nbsp; expected {exp}, currently diverging'
                f' <span style="color:{z_c};font-weight:700">(z={z:+.1f})</span></div>'
            )
    else:
        watch_html += (
            '<p style="color:#64748b;font-size:13px">'
            'No diverging pairs — all cross-asset relationships within normal range.</p>'
        )

    flags = data["opp_flags"]
    if flags:
        watch_html += (
            '<p style="margin:12px 0 6px;font-size:13px;color:#475569;font-weight:600">'
            'Catch-up opportunities flagged this week:</p>'
        )
        for f_ in flags[:5]:
            z = float(f_.get("z_score") or 0)
            watch_html += (
                f'<p style="margin:3px 0;font-size:13px">'
                f'<strong>{f_["ticker"]}</strong> &nbsp; {f_["flag_type"]} &nbsp;'
                f'<span style="color:#d97706">(z={z:+.1f})</span></p>'
            )

    sections += _section_h("WATCH LIST", watch_html, "#b45309")

    # ── Assemble ──────────────────────────────────────────────────────
    next_sunday = now + timedelta(days=(6 - now.weekday() + 7) % 7 or 7)
    _ns_10_uk   = _fmt_uk(next_sunday.replace(hour=10, minute=0, second=0, tzinfo=timezone.utc), "%H:%M")
    footer_note = f"Next summary: Sunday {next_sunday.strftime('%d %b %Y')} {_ns_10_uk}"

    return _email_wrap_h(
        _email_header_h(
            "Weekly Summary",
            f"Week of {week_ago.strftime('%d %b')} – {now.strftime('%d %b %Y')} "
            f"&nbsp;·&nbsp; Generated {_fmt_uk(now, '%H:%M')}",
        ),
        sections,
        _email_footer_h(footer_note),
    )


def send_weekly_summary_email(config: dict) -> tuple[bool, str]:
    """Gather weekly data and send the summary email.

    Respects notifications.email.enabled and can be independently disabled
    with notifications.weekly_summary.enabled: false.
    """
    notif      = config.get("notifications", {}).get("email", {})
    weekly_cfg = config.get("notifications", {}).get("weekly_summary", {})

    if not notif.get("enabled"):
        return False, "Email notifications not enabled in config.yaml"
    if weekly_cfg.get("enabled") is False:
        return False, "Weekly summary disabled (notifications.weekly_summary.enabled: false)"

    try:
        data = _gather_weekly_data(config)
    except Exception as exc:
        logger.error("Weekly summary data gathering failed: %s", exc, exc_info=True)
        return False, f"Data gathering failed: {exc}"

    try:
        text_body = _build_weekly_email(data, config)
        html_body = _build_weekly_email_html(data, config)
    except Exception as exc:
        logger.error("Weekly summary email build failed: %s", exc, exc_info=True)
        return False, f"Email build failed: {exc}"

    now      = data["now"]
    week_ago = data["week_ago"]
    subject  = (
        f"Investment Research — Weekly Summary "
        f"{week_ago.strftime('%d %b')}–{now.strftime('%d %b %Y')}"
    )

    ok, msg = _smtp_send(subject, text_body, html_body, config)
    if ok:
        to_n = len(notif.get("to_addresses") or [])
        set_metadata("last_weekly_summary_sent", now.isoformat())
        logger.info("Weekly summary email sent to %d recipient(s).", to_n)
        return True, f"Weekly summary sent to {to_n} recipient(s)"
    return False, msg
