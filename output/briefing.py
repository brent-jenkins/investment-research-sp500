"""Daily briefing: trade status computation, macro context, opportunities, email alerts."""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import text

from data.database import get_engine, get_metadata, set_metadata
from output.performance import get_current_signal

logger = logging.getLogger(__name__)

_SIGNAL_RANK: dict[str, int] = {
    "STRONG_BUY":  2,
    "BUY":         1,
    "NEUTRAL":     0,
    "SELL":       -1,
    "STRONG_SELL": -2,
}

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

_STATUS_PRIORITY: dict[str, int] = {
    "ON TRACK":    1,
    "WEAKENING":   2,
    "EXIT SIGNAL": 3,
    "STOP LOSS":   4,
}


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


def compute_trade_status(
    direction: str,
    entry_signal: str,
    current_signal: str,
    entry_score: float,
    current_score: float,
    current_price: Optional[float],
    stop_loss: Optional[float],
) -> str:
    """Classify an open trade's current health.

    Priority order: STOP LOSS > EXIT SIGNAL > WEAKENING > ON TRACK.
    """
    # Stop loss — check first (highest urgency)
    if current_price is not None and stop_loss is not None:
        if direction == "LONG"  and current_price <= stop_loss:
            return "STOP LOSS"
        if direction == "SHORT" and current_price >= stop_loss:
            return "STOP LOSS"

    # Exit signal — current signal contradicts the trade direction
    _bearish = {"SELL", "STRONG_SELL"}
    _bullish = {"BUY",  "STRONG_BUY"}
    if direction == "LONG"  and current_signal in _bearish:
        return "EXIT SIGNAL"
    if direction == "SHORT" and current_signal in _bullish:
        return "EXIT SIGNAL"

    # Weakening — score fell more than 0.2 or signal rank downgraded
    delta = (current_score or 0.0) - (entry_score or 0.0)
    if delta < -0.2:
        return "WEAKENING"

    entry_rank   = _SIGNAL_RANK.get(entry_signal   or "NEUTRAL", 0)
    current_rank = _SIGNAL_RANK.get(current_signal or "NEUTRAL", 0)
    if direction == "LONG"  and entry_rank > 0 and current_rank < entry_rank:
        return "WEAKENING"
    if direction == "SHORT" and entry_rank < 0 and current_rank > entry_rank:
        return "WEAKENING"

    return "ON TRACK"


def enrich_open_trade(trade: dict, live_price: Optional[float]) -> dict:
    """Augment an open trade dict with current signal, stop loss, P&L, and status."""
    ticker      = trade.get("ticker", "")
    direction   = trade.get("direction", "LONG")
    entry_price = float(trade.get("entry_price") or 0)
    quantity    = float(trade.get("quantity")    or 1)
    entry_signal = trade.get("signal_type") or "NEUTRAL"
    entry_score  = float(trade.get("confluence_score") or 0)

    # Days held
    entry_dt = trade.get("entry_date")
    if isinstance(entry_dt, str):
        try:
            entry_dt = datetime.fromisoformat(entry_dt)
        except Exception:
            entry_dt = None
    days_held = max(0, (datetime.utcnow() - entry_dt).days) if entry_dt else 0

    # Current signal from signals table
    sig_row = get_current_signal(ticker)
    current_signal = sig_row.get("signal") or "NEUTRAL"
    current_score  = float(sig_row.get("confluence_score") or 0)

    # ATR-based stop loss: entry ± 2 × ATR
    atr: Optional[float] = get_current_atr(ticker)
    stop_loss: Optional[float] = None
    if atr is not None and entry_price > 0:
        if direction == "LONG":
            stop_loss = round(entry_price - 2 * atr, 4)
        else:
            stop_loss = round(entry_price + 2 * atr, 4)

    # Unrealised P&L
    unreal_pnl: Optional[float] = None
    unreal_pct: Optional[float] = None
    if live_price is not None and entry_price > 0:
        if direction == "LONG":
            unreal_pnl = (live_price - entry_price) * quantity
            unreal_pct = (live_price - entry_price) / entry_price * 100
        else:
            unreal_pnl = (entry_price - live_price) * quantity
            unreal_pct = (entry_price - live_price) / entry_price * 100
        unreal_pnl = round(unreal_pnl, 2)
        unreal_pct = round(unreal_pct, 2)

    status = compute_trade_status(
        direction, entry_signal, current_signal,
        entry_score, current_score, live_price, stop_loss,
    )

    return {
        **trade,
        "current_signal":  current_signal,
        "current_score":   current_score,
        "entry_signal":    entry_signal,
        "entry_score":     entry_score,
        "atr":             atr,
        "stop_loss":       stop_loss,
        "live_price":      live_price,
        "unreal_pnl":      unreal_pnl,
        "unreal_pct":      unreal_pct,
        "days_held":       days_held,
        "status":          status,
        "status_priority": _STATUS_PRIORITY.get(status, 0),
    }


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


def should_send_alert(config: dict) -> bool:
    """Return True if email alerts are enabled and the cooldown window has elapsed."""
    notif = config.get("notifications", {}).get("email", {})
    if not notif.get("enabled"):
        return False
    cooldown_h = int(notif.get("alert_cooldown_hours", 8))
    last_sent = get_metadata("last_alert_email_sent")
    if last_sent is None:
        return True
    last_sent = last_sent.replace(tzinfo=None) if getattr(last_sent, "tzinfo", None) else last_sent
    return (datetime.utcnow() - last_sent).total_seconds() > cooldown_h * 3600


def send_alert_email(alerts: list[dict], config: dict) -> tuple[bool, str]:
    """Send a plain-text alert email via SMTP.

    Defaults to Brevo SMTP (smtp-relay.brevo.com:587) but any STARTTLS-capable
    SMTP relay works.  Returns (success, human-readable message).
    """
    notif = config.get("notifications", {}).get("email", {})
    if not notif.get("enabled"):
        return False, "Email notifications not enabled in config.yaml"

    smtp_host  = notif.get("smtp_host",     "smtp-relay.brevo.com")
    smtp_port  = int(notif.get("smtp_port", 587))
    smtp_user  = notif.get("smtp_user",     "")
    smtp_pass  = notif.get("smtp_password", "")
    from_addr  = notif.get("from_address")  or smtp_user
    to_addrs   = notif.get("to_addresses")  or []

    if not to_addrs:
        return False, "No recipient addresses configured in notifications.email.to_addresses"

    now     = datetime.utcnow()
    n_pos   = len(alerts)
    subject = (
        f"Trade Alert — {n_pos} position{'s' if n_pos != 1 else ''} require attention "
        f"({now.strftime('%Y-%m-%d %H:%M')} UTC)"
    )

    lines = [
        "Investment Research Dashboard — Trade Alert",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"POSITIONS REQUIRING ATTENTION ({n_pos})",
        "=" * 45,
        "",
    ]

    for a in alerts:
        lines.append(f"[{a['status']}]  {a['ticker']}  {a.get('direction', '?')}")
        ep  = a.get("entry_price") or 0
        lp  = a.get("live_price")
        up  = a.get("unreal_pnl")
        upc = a.get("unreal_pct")
        lp_str  = f"${lp:,.4f}" if lp  is not None else "—"
        pnl_str = (
            f"{up:+.2f} ({upc:+.2f}%)" if up is not None and upc is not None else "—"
        )
        lines.append(f"  Entry: ${ep:,.4f}  |  Current: {lp_str}  |  Unrealised: {pnl_str}")
        es     = a.get("entry_signal",   "—")
        cs     = a.get("current_signal", "—")
        e_sc   = float(a.get("entry_score")   or 0)
        c_sc   = float(a.get("current_score") or 0)
        delta  = c_sc - e_sc
        lines.append(
            f"  Signal: {es} → {cs}  |  Score: {e_sc:+.3f} → {c_sc:+.3f}  (Δ {delta:+.3f})"
        )
        if a.get("stop_loss") is not None:
            sl  = a["stop_loss"]
            atr = a.get("atr")
            sl_str = f"${sl:,.4f}"
            if atr:
                dir_sym = "−" if a.get("direction") == "LONG" else "+"
                sl_str += f"  (entry {dir_sym} 2×ATR {atr:.4f})"
            lines.append(f"  Stop loss: {sl_str}")
        lines.append("")

    lines += [
        "---",
        "To disable these alerts: set notifications.email.enabled: false in config.yaml",
    ]

    msg = MIMEText("\n".join(lines), "plain")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(to_addrs)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        set_metadata("last_alert_email_sent", now.isoformat())
        return True, f"Alert sent to {len(to_addrs)} recipient(s)"
    except Exception as exc:
        logger.error("Failed to send alert email: %s", exc)
        return False, f"Email send failed: {exc}"
