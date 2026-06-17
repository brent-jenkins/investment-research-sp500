"""Daily briefing: trade status computation, macro context, opportunities, email alerts."""

import logging
import os
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import text

from data.database import get_engine, get_metadata, get_metadata_str, set_metadata
from output.performance import get_closed_trades, get_current_signal, get_open_trades

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
    import math

    ticker      = trade.get("ticker", "")
    direction   = trade.get("direction", "LONG")
    entry_price = float(trade.get("entry_price") or 0)
    quantity    = float(trade.get("quantity")    or 1)
    entry_signal = trade.get("signal_type") or "NEUTRAL"
    entry_score  = float(trade.get("confluence_score") or 0)

    # Treat NaN live price the same as unavailable
    if live_price is not None and math.isnan(live_price):
        live_price = None

    # Days held
    entry_dt = trade.get("entry_date")
    if isinstance(entry_dt, str):
        try:
            entry_dt = datetime.fromisoformat(entry_dt)
        except Exception:
            entry_dt = None
    days_held = max(0, (datetime.utcnow() - entry_dt).days) if entry_dt else 0

    # Current signal from signals table
    try:
        sig_row = get_current_signal(ticker)
        current_signal = sig_row.get("signal") or "NEUTRAL"
        current_score  = float(sig_row.get("confluence_score") or 0)
    except Exception:
        current_signal = "NEUTRAL"
        current_score  = 0.0

    # ATR-based stop loss: entry ± 2 × ATR
    try:
        atr: Optional[float] = get_current_atr(ticker)
    except Exception:
        atr = None
    stop_loss: Optional[float] = None
    if atr is not None and entry_price > 0:
        if direction == "LONG":
            stop_loss = round(entry_price - 2 * atr, 4)
        else:
            stop_loss = round(entry_price + 2 * atr, 4)

    # Unrealised P&L
    unreal_pnl: Optional[float] = None
    unreal_pct: Optional[float] = None
    try:
        if live_price is not None and entry_price > 0:
            if direction == "LONG":
                unreal_pnl = (live_price - entry_price) * quantity
                unreal_pct = (live_price - entry_price) / entry_price * 100
            else:
                unreal_pnl = (entry_price - live_price) * quantity
                unreal_pct = (entry_price - live_price) / entry_price * 100
            unreal_pnl = round(unreal_pnl, 2)
            unreal_pct = round(unreal_pct, 2)
    except Exception:
        unreal_pnl = None
        unreal_pct = None

    try:
        status = compute_trade_status(
            direction, entry_signal, current_signal,
            entry_score, current_score, live_price, stop_loss,
        )
    except Exception:
        status = "ON TRACK"

    return {
        **trade,
        "current_signal":     current_signal,
        "current_score":      current_score,
        "entry_signal":       entry_signal,
        "entry_score":        entry_score,
        "atr":                atr,
        "stop_loss":          stop_loss,
        "live_price":         live_price,
        "price_unavailable":  live_price is None,
        "unreal_pnl":         unreal_pnl,
        "unreal_pct":         unreal_pct,
        "days_held":          days_held,
        "status":             status,
        "status_priority":    _STATUS_PRIORITY.get(status, 0),
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


def send_test_email(
    config: dict,
    regime: str,
    last_analysis: "datetime | None",
    open_trade_count: int,
) -> "tuple[bool, str]":
    """Send a test notification email to verify SMTP delivery.

    Bypasses the enabled flag — the purpose is to verify delivery works
    before committing to enabling live alerts.
    Subject is always 'Investment Research — Test Notification'.
    Body includes sender, recipient, current regime, last analysis time,
    and open trade count so the recipient can confirm end-to-end delivery.
    Returns (success, human-readable message).
    """
    notif = config.get("notifications", {}).get("email", {})

    smtp_host  = notif.get("smtp_host",     "smtp-relay.brevo.com")
    smtp_port  = int(notif.get("smtp_port", 587))
    smtp_user  = notif.get("smtp_user",     "")
    smtp_pass  = notif.get("smtp_password", "")
    from_addr  = notif.get("from_address")  or smtp_user
    to_addrs   = notif.get("to_addresses")  or []

    if not to_addrs:
        return False, "No recipient addresses configured in notifications.email.to_addresses"

    now = datetime.utcnow()
    subject = "Investment Research — Test Notification"

    last_run_str = last_analysis.strftime("%Y-%m-%d %H:%M UTC") if last_analysis else "—"

    lines = [
        "Investment Research Dashboard — Test Notification",
        f"Sent:      {now.strftime('%Y-%m-%d %H:%M')} UTC",
        f"From:      {from_addr}",
        f"To:        {', '.join(to_addrs)}",
        f"Via:       {smtp_host}:{smtp_port}",
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
        return True, f"Test email sent to {', '.join(to_addrs)}"
    except Exception as exc:
        logger.error("Failed to send test email: %s", exc)
        return False, f"Email send failed: {exc}"


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
    "UNKNOWN":         "Mixed signals — ADX between thresholds, neutral positioning preferred",
}

_MACRO_LABELS: dict[str, str] = {
    "T10Y2Y":       "Yield curve (10Y−2Y)",
    "T10Y3M":       "Yield curve (10Y−3M)",
    "BAMLH0A0HYM2": "High yield OAS",
    "BAMLC0A0CM":   "Investment grade OAS",
    "DFII10":       "10Y real yield (TIPS)",
    "T10YIE":       "Breakeven inflation (10Y)",
    "USSLIND":      "US leading index (Philly Fed)",
}


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

    # Trades — exclude VIRTUAL from real accuracy stats
    open_trades   = [t for t in get_open_trades()          if t.get("account_type") != "VIRTUAL"]
    closed_week   = [t for t in get_closed_trades(from_date=week_ago) if t.get("account_type") != "VIRTUAL"]
    all_closed    = [t for t in get_closed_trades()        if t.get("account_type") != "VIRTUAL"]

    vt_cfg    = config.get("virtual_trading", {})
    vt_enabled = vt_cfg.get("enabled", False)
    virt_open  = get_open_trades(account_type="VIRTUAL")          if vt_enabled else []
    virt_week  = get_closed_trades(from_date=week_ago, account_type="VIRTUAL") if vt_enabled else []

    return {
        "now":            now,
        "week_ago":       week_ago,
        "curr_signals":   [dict(r) for r in curr_sig_rows],
        "week_signals":   [dict(r) for r in week_sig_rows],
        "current_prices": current_prices,
        "vix_prev":       float(vix_prev_row[0]) if vix_prev_row else None,
        "macro_latest":   {r["series_id"]: dict(r) for r in macro_latest_rows},
        "macro_prev":     {r["series_id"]: float(r["value"]) for r in macro_prev_rows if r["value"] is not None},
        "active_overlays": [o.strip() for o in (get_metadata_str("active_overlays") or "none").split(",")
                            if o.strip() not in ("", "none")],
        "div_pairs":      [dict(r) for r in div_rows],
        "opp_flags":      [dict(r) for r in flag_rows],
        "open_trades":    open_trades,
        "closed_week":    closed_week,
        "all_closed":     all_closed,
        "vt_enabled":     vt_enabled,
        "virt_open":      virt_open,
        "virt_week":      virt_week,
        "vt_portfolio":   vt_cfg.get("portfolio_size_gbp", 10000),
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
        f"Generated {now.strftime('%Y-%m-%d %H:%M')} UTC"
    )

    # ── Market Regime & Overlays ──────────────────────────────────────
    section("MARKET REGIME")
    curr = data["curr_signals"]
    regime = curr[0]["regime"] if curr else "UNKNOWN"
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

    # ── Open Trades ───────────────────────────────────────────────────
    section(f"OPEN TRADES  ({len(data['open_trades'])} position{'s' if len(data['open_trades']) != 1 else ''})")
    if not data["open_trades"]:
        L.append("  No open trades.")
    else:
        for t in data["open_trades"]:
            ticker     = t.get("ticker", "?")
            direction  = t.get("direction", "?")
            entry_p    = float(t.get("entry_price") or 0)
            entry_sig  = t.get("signal_type", "?")
            entry_raw  = t.get("entry_date")
            acct       = t.get("account_type", "PRACTICE")
            try:
                entry_dt  = datetime.fromisoformat(str(entry_raw).split(".")[0]) if entry_raw else None
                hold_days = (now - entry_dt).days if entry_dt else 0
                entry_str = entry_dt.strftime("%d %b") if entry_dt else "?"
            except Exception:
                hold_days, entry_str = 0, "?"

            curr_p = curr_prices.get(ticker)
            if curr_p and entry_p:
                ret = ((curr_p - entry_p) / entry_p * 100 if direction == "LONG"
                       else (entry_p - curr_p) / entry_p * 100)
                pnl_str = f"{ret:+.1f}%"
            else:
                pnl_str = "  —"

            acct_label = f" [{acct}]" if acct not in ("PRACTICE", "") else ""
            L.append(
                f"  {direction:<5} {ticker:<12}  entered {entry_str} ({hold_days}d)  "
                f"signal: {entry_sig:<12}  {pnl_str}{acct_label}"
            )
        valid = [t for t in data["open_trades"]
                 if curr_prices.get(t["ticker"]) and t.get("entry_price")]
        if valid:
            avg = sum(
                ((curr_prices[t["ticker"]] - float(t["entry_price"])) / float(t["entry_price"]) * 100
                 if t.get("direction") == "LONG"
                 else (float(t["entry_price"]) - curr_prices[t["ticker"]]) / float(t["entry_price"]) * 100)
                for t in valid
            ) / len(valid)
            L.append(f"\n  Average price change across open positions: {avg:+.1f}%")
            L.append("  (Price change only — excludes FX conversion and leverage.)")

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
    for series_id, label in _MACRO_LABELS.items():
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

    # ── Virtual Portfolio ─────────────────────────────────────────────
    if data["vt_enabled"]:
        section(f"VIRTUAL PORTFOLIO  (GBP {data['vt_portfolio']:,.0f})")
        v_open = data["virt_open"]
        v_week = data["virt_week"]
        L.append(f"  Open positions: {len(v_open)}")
        if v_open:
            L.append("")
            for t in v_open:
                ticker    = t.get("ticker", "?")
                direction = t.get("direction", "?")
                entry_p   = float(t.get("entry_price") or 0)
                curr_p    = curr_prices.get(ticker)
                if curr_p and entry_p:
                    ret = ((curr_p - entry_p) / entry_p * 100 if direction == "LONG"
                           else (entry_p - curr_p) / entry_p * 100)
                    pnl_str = f"{ret:+.1f}%"
                else:
                    pnl_str = "?"
                L.append(
                    f"    {direction:<5} {ticker:<12}  {t.get('signal_type','?'):<12}  {pnl_str}"
                )
        if v_week:
            v_wins = [t for t in v_week if float(t.get("pnl_pct") or 0) > 0]
            v_net  = sum(float(t.get("pnl_pct") or 0) * 100 for t in v_week)
            L.append("")
            L.append(
                f"  Closed this week: {len(v_week)} trade{'s' if len(v_week)!=1 else ''}  |  "
                f"{len(v_wins)} wins  |  Net: {v_net:+.1f}%"
            )
        else:
            L.append("  No virtual trades closed this week.")

    # ── Footer ────────────────────────────────────────────────────────
    L.append("")
    rule("-", 60)
    L.append("Investment Research Dashboard  |  research.example.com")
    next_sunday = now + timedelta(days=(6 - now.weekday() + 7) % 7 or 7)
    L.append(f"Next weekly summary: Sunday {next_sunday.strftime('%d %b %Y')} at 10:00 UTC")
    L.append("To disable: notifications.weekly_summary.enabled: false in config.yaml")
    return "\n".join(L)


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

    smtp_host = notif.get("smtp_host",     "smtp-relay.brevo.com")
    smtp_port = int(notif.get("smtp_port", 587))
    smtp_user = notif.get("smtp_user",     "")
    smtp_pass = notif.get("smtp_password", "")
    from_addr = notif.get("from_address")  or smtp_user
    to_addrs  = notif.get("to_addresses")  or []

    if not to_addrs:
        return False, "No recipient addresses configured in notifications.email.to_addresses"

    try:
        data = _gather_weekly_data(config)
    except Exception as exc:
        logger.error("Weekly summary data gathering failed: %s", exc, exc_info=True)
        return False, f"Data gathering failed: {exc}"

    try:
        body = _build_weekly_email(data, config)
    except Exception as exc:
        logger.error("Weekly summary email build failed: %s", exc, exc_info=True)
        return False, f"Email build failed: {exc}"

    now      = data["now"]
    week_ago = data["week_ago"]
    subject  = (
        f"Investment Research — Weekly Summary "
        f"{week_ago.strftime('%d %b')}–{now.strftime('%d %b %Y')}"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(to_addrs)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        set_metadata("last_weekly_summary_sent", now.isoformat())
        logger.info("Weekly summary email sent to %d recipient(s).", len(to_addrs))
        return True, f"Weekly summary sent to {len(to_addrs)} recipient(s)"
    except Exception as exc:
        logger.error("Failed to send weekly summary email: %s", exc)
        return False, f"Email send failed: {exc}"
