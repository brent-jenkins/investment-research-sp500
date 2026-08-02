"""Investment Research Dashboard — Streamlit entry point.

The live system is a single momentum + crisis-override strategy (see
analysis/signals.py, analysis/crisis.py, analysis/universe.py,
analysis/backtest.py, brokers/trading212.py) — no regime classifier, no
six-module confluence score, no separate "analysis pipeline". Momentum
signal, index membership, and the crisis circuit breaker are all computed
on demand straight from price_data.
"""

import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so sibling packages resolve correctly
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import html
import json
import logging
import subprocess
import time
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from analysis.backtest import compute_metrics, equity_curve_gbp
from analysis.universe import MEMBERSHIP_STALE_HOURS, is_membership_refresh_stale
from data.database import get_engine, get_metadata, get_metadata_str, init_db, set_metadata
from data.ingestion import get_all_tickers, get_all_watchlist_entries, get_price_df, pull_incremental
from data.scheduler import start_scheduler
from output.performance import (
    POSITIONS_RULES_EXPLANATION,
    compute_position_context,
    get_brief_cards,
    get_crisis_status,
    get_pnl_rows,
    get_ticker_status,
    position_context_style,
    position_exit_rule_text,
    positions_summary_text,
    pnl_metrics_df,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UK-time display helpers
# ---------------------------------------------------------------------------
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


def _uk_sched_time(hour: int, minute: int) -> str:
    """Format a fixed UTC hour:minute as UK local time using today's DST offset."""
    ref = datetime.now(timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    uk = ref.astimezone(_UK_TZ)
    abbr = "BST" if uk.dst() else "GMT"
    return f"{uk.strftime('%H:%M')} {abbr}"


# Ensure the DB schema exists before any page renders or background jobs fire.
# init_db() is idempotent — create_all() and _apply_migrations() are no-ops
# when tables and columns are already present.
init_db()


@st.cache_resource
def _start_scheduler_at_startup() -> None:
    """Start the in-process scheduler unless the standalone daemon is already running.

    The standalone daemon (scheduler_daemon.py / investment-research-scheduler.service)
    writes a heartbeat to system_metadata every 60 seconds. If that heartbeat is
    fresh (< 90 seconds old), the daemon owns scheduling and we skip the in-process
    scheduler to avoid double-firing jobs. If no fresh heartbeat exists (local dev,
    or daemon not installed), we start the in-process scheduler as a fallback.
    """
    try:
        heartbeat_str = get_metadata_str("scheduler_heartbeat")
        if heartbeat_str:
            heartbeat_dt = datetime.fromisoformat(heartbeat_str)
            age_s = (datetime.utcnow() - heartbeat_dt).total_seconds()
            if age_s < 90:
                logger.info(
                    "Standalone scheduler daemon detected (heartbeat %ds ago) "
                    "— skipping in-process scheduler.",
                    int(age_s),
                )
                return
    except Exception:
        pass

    try:
        start_scheduler()
    except Exception as exc:
        logger.warning("In-process scheduler could not start: %s", exc)


_start_scheduler_at_startup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=0)
def _load_config() -> dict:
    """Load config.yaml, with secrets overridable via environment variables."""
    config_path = _ROOT / "config.yaml"
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    if pw := os.environ.get("SMTP_PASSWORD"):
        cfg.setdefault("notifications", {}).setdefault("email", {})["smtp_password"] = pw
    return cfg


def _build_watchlist(config: dict) -> dict[str, str]:
    """Return an ordered dict of {ticker: display_name} for all watchlist assets
    (watchlist.core + the top-level instruments: section)."""
    return {e["ticker"]: e.get("name", e["ticker"]) for e in get_all_watchlist_entries(config)}


def _hex_rgba(hx: str, alpha: float) -> str:
    """Convert a 6-digit hex colour string to a CSS rgba() value."""
    h = hx.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{alpha})"


def _has_price_data() -> bool:
    """Return True if the price_data table contains at least one row."""
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            row = conn.execute(sa_text("SELECT 1 FROM price_data LIMIT 1")).fetchone()
        return row is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# System status helpers (used by _render_status_page)
# ---------------------------------------------------------------------------

_LEVEL_COLOUR = {
    "ok":   "#16a34a",
    "warn": "#f59e0b",
    "error":"#dc2626",
    "info": "#6366f1",
}


def _status_card(col, label: str, value: str, sub: str = "", level: str = "ok") -> None:
    """Render a colour-coded status card into a Streamlit column."""
    colour  = _LEVEL_COLOUR.get(level, "#8899AA")
    sub_html = (
        f'<div style="font-size:0.78rem;color:#8899AA;margin-top:3px">'
        f'{html.escape(sub)}</div>'
    ) if sub else ""
    col.markdown(
        f'<div style="border-left:3px solid {colour};background:{colour}18;'
        f'padding:10px 14px;border-radius:0 6px 6px 0;min-height:72px">'
        f'<div style="font-size:0.68rem;color:{colour};text-transform:uppercase;'
        f'letter-spacing:0.08em;font-weight:700;margin-bottom:4px">'
        f'{html.escape(label)}</div>'
        f'<div style="font-size:0.95rem;font-weight:600">{html.escape(value)}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _age_level(ts: datetime | None, warn_hours: float = 48.0, error_hours: float = 168.0) -> str:
    """Return 'ok' / 'warn' / 'error' based on how many hours old *ts* is."""
    if ts is None:
        return "error"
    _ts = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
    hours = (datetime.utcnow() - _ts).total_seconds() / 3600
    if hours < warn_hours:
        return "ok"
    if hours < error_hours:
        return "warn"
    return "error"


def _age_display(ts: datetime | None) -> tuple[str, str]:
    """Return (primary_value, sub_caption) strings for a stored timestamp."""
    if ts is None:
        return "Never", "no data recorded"
    _ts = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
    hours = (datetime.utcnow() - _ts).total_seconds() / 3600
    if hours < 1:
        age = f"{int(hours * 60)}m ago"
    elif hours < 24:
        age = f"{int(hours)}h ago"
    else:
        age = f"{hours / 24:.1f}d ago"
    return _fmt_uk(_ts, "%Y-%m-%d %H:%M"), age


def _next_run_for_time(hour: int, minute: int) -> datetime:
    """Return the next weekday datetime when the job at *hour*:*minute* UTC fires."""
    now = datetime.utcnow()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:   # skip Sat (5) and Sun (6)
        candidate += timedelta(days=1)
    return candidate


def _next_monday_at(hour: int, minute: int) -> datetime:
    """Return the next Monday's hour:minute UTC occurrence (today counts if
    it's already Monday and that time hasn't passed yet). Used for the
    Monday-only pre-market job (data/scheduler.py:_monday_premarket_job()),
    which fires on no other day of the week -- unlike _next_run_for_time()
    above, which only ever skips weekends, not the other four weekdays."""
    now = datetime.utcnow()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (0 - candidate.weekday()) % 7   # 0 = Monday
    if days_ahead == 0 and candidate <= now:
        days_ahead = 7
    return candidate + timedelta(days=days_ahead)


def _git_log_info() -> tuple[str, str]:
    """Return (timestamp_str, short_message) for the most recent git commit."""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%ci|%s"],
            capture_output=True, text=True, cwd=str(_ROOT), timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            raw_ts, _, msg = res.stdout.strip().partition("|")
            raw_ts = raw_ts.strip()
            try:
                dt = datetime.strptime(raw_ts[:25], "%Y-%m-%d %H:%M:%S %z")
            except ValueError:
                dt = datetime.strptime(raw_ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            msg = msg.strip()
            if len(msg) > 60:
                msg = msg[:57] + "…"
            return _fmt_uk(dt, "%Y-%m-%d %H:%M"), msg
    except Exception:
        pass
    return "Unknown", ""


def _service_uptime() -> str:
    """Return a human-readable uptime string for the current process via psutil."""
    try:
        import psutil
        elapsed = time.time() - psutil.Process().create_time()
        d, rem = divmod(int(elapsed), 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return f"{d}d {h}h {m}m"
        if h:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "Unknown"


def _db_size(config: dict) -> tuple[str, str]:
    """Return (size_string, level) for the SQLite database file."""
    try:
        db_path = _ROOT / config["database"]["path"]
        if db_path.exists():
            mb = db_path.stat().st_size / (1024 * 1024)
            return f"{mb:.1f} MB", "ok"
        return "Not found", "error"
    except Exception:
        return "Unknown", "warn"


def _price_coverage(config: dict) -> tuple[list[str], list[str], list[str], int]:
    """Return (fresh, stale, missing, max_stale_trading_days) based on latest price date.

    Fresh   = data within 3 trading days on the ticker's home exchange.
    Stale   = data present but older than 3 trading days.
    Missing = no rows at all in price_data for that ticker.
    """
    import pandas_market_calendars as mcal

    ticker_exchange: dict[str, str] = {
        e["ticker"]: e.get("exchange", "NYSE") for e in get_all_watchlist_entries(config)
    }

    try:
        from sqlalchemy import text as sa_text
        with get_engine().connect() as conn:
            rows = conn.execute(
                sa_text("SELECT ticker, MAX(date) AS latest FROM price_data GROUP BY ticker")
            ).fetchall()
        latest_by_ticker: dict[str, object] = {r[0]: r[1] for r in rows}
    except Exception:
        latest_by_ticker = {}

    _MCAL_MAP: dict[str, str] = {
        "NYSE": "NYSE", "NASDAQ": "NASDAQ", "CBOE": "NYSE",
        "LSE": "LSE",
        "TSE": "JPX", "JPX": "JPX",
        "XETR": "NYSE",
        "HKEX": "HKEX", "FX": "NYSE", "Crypto": "NYSE",
    }

    today = datetime.utcnow().date()
    _lookback = (datetime.utcnow() - timedelta(days=30)).date()
    _cal_sessions: dict[str, list] = {}

    def _get_sessions(cal_name: str) -> list:
        if cal_name not in _cal_sessions:
            try:
                cal = mcal.get_calendar(cal_name)
                sched = cal.schedule(
                    start_date=_lookback.strftime("%Y-%m-%d"),
                    end_date=today.strftime("%Y-%m-%d"),
                )
                _cal_sessions[cal_name] = [pd.Timestamp(d).date() for d in sched.index]
            except Exception:
                if cal_name != "NYSE" and "NYSE" not in _cal_sessions:
                    try:
                        cal = mcal.get_calendar("NYSE")
                        sched = cal.schedule(
                            start_date=_lookback.strftime("%Y-%m-%d"),
                            end_date=today.strftime("%Y-%m-%d"),
                        )
                        _cal_sessions["NYSE"] = [pd.Timestamp(d).date() for d in sched.index]
                    except Exception:
                        _cal_sessions["NYSE"] = []
                _cal_sessions[cal_name] = _cal_sessions.get("NYSE", [])
        return _cal_sessions[cal_name]

    def _trading_days_since(last_dt: datetime, exchange: str) -> int:
        cal_name = _MCAL_MAP.get(exchange, "NYSE")
        sessions = _get_sessions(cal_name)
        last_date = last_dt.date() if hasattr(last_dt, "date") else last_dt
        return sum(1 for d in sessions if d > last_date)

    _FRESH_TD = 3

    fresh, stale, missing = [], [], []
    max_stale_trading_days = 0

    for ticker, exchange in ticker_exchange.items():
        raw = latest_by_ticker.get(ticker)
        if raw is None:
            missing.append(ticker)
            continue
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                missing.append(ticker)
                continue
        else:
            dt = raw
        dt = dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt

        td = _trading_days_since(dt, exchange)
        if td <= _FRESH_TD:
            fresh.append(ticker)
        else:
            stale.append(ticker)
            max_stale_trading_days = max(max_stale_trading_days, td)

    return fresh, stale, missing, max_stale_trading_days


def _parse_journal_line(raw: str) -> "dict | None":
    """Parse one journalctl short-iso line into a dict with ts, level, message, raw."""
    import re as _re
    _PAT = _re.compile(
        r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})'
        r'[^\s]*\s+'
        r'\S+\s+'
        r'[^\[]+\[\d+\]:\s*'
        r'(.*)$'
    )
    _m = _PAT.match(raw)
    if not _m:
        return None
    ts_raw, msg = _m.group(1), _m.group(2)
    ts = ts_raw.replace("T", " ")

    _lvl_m = _re.match(r'^(DEBUG|INFO|WARNING|ERROR|CRITICAL):', msg)
    level = _lvl_m.group(1) if _lvl_m else "INFO"
    if _lvl_m:
        _rest = msg[_lvl_m.end():]
        if ":" in _rest:
            _colon = _rest.index(":")
            msg = _rest[_colon + 1:]
        else:
            msg = _rest

    return {"ts": ts, "level": level, "message": msg, "raw": raw}


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _apply_dark_theme(fig: go.Figure) -> go.Figure:
    """Apply the SP500Invest dark-navy theme to a Plotly figure in place, and
    return it for chaining."""
    fig.update_layout(
        template="plotly_dark",
        font=dict(color="#CCDDEE"),
        xaxis=dict(gridcolor="#1E2D45", linecolor="#1E2D45"),
        yaxis=dict(gridcolor="#1E2D45", linecolor="#1E2D45"),
    )
    return fig


def _candlestick_chart(df: pd.DataFrame, ticker: str, name: str) -> go.Figure:
    """Build a Plotly candlestick chart for a ticker, with 20/50-day SMA overlays."""
    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=ticker,
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        )
    )

    df_valid = df[df["close"].notna() & (df["close"] > 0)]
    for window, colour in ((20, "#f59e0b"), (50, "#6366f1")):
        if len(df_valid) >= window:
            sma = df_valid["close"].rolling(window).mean()
            fig.add_trace(
                go.Scatter(
                    x=df_valid["date"],
                    y=sma,
                    mode="lines",
                    name=f"SMA {window}",
                    line=dict(color=colour, width=1.2),
                )
            )

    fig.update_layout(
        title=f"{name} ({ticker})",
        xaxis_title="Date",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        height=500,
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
    )
    return _apply_dark_theme(fig)


def _volume_bar_chart(df: pd.DataFrame) -> go.Figure:
    """Build a volume bar chart coloured by daily direction."""
    colours = [
        "#26a69a" if c >= o else "#ef5350"
        for c, o in zip(df["close"], df["open"])
    ]
    fig = go.Figure(
        go.Bar(x=df["date"], y=df["volume"], marker_color=colours, name="Volume")
    )
    fig.update_layout(
        yaxis_title="Volume",
        height=180,
        margin=dict(l=40, r=20, t=10, b=30),
        showlegend=False,
    )
    return _apply_dark_theme(fig)


# ---------------------------------------------------------------------------
# Trade card / cached wrappers
# ---------------------------------------------------------------------------

_STATUS_COLOURS: dict[str, str] = {
    "ON TRACK":      "#16a34a",
    "SIGNAL OFF":    "#d97706",
    "MAX HOLDING":   "#ea580c",
    "STOP LOSS":     "#dc2626",
    "INDEX REMOVAL": "#dc2626",
    "CRISIS EXIT":   "#991b1b",
}
_STATUS_ICONS: dict[str, str] = {
    "ON TRACK":      "✅",
    "SIGNAL OFF":    "⚠️",
    "MAX HOLDING":   "⏱️",
    "STOP LOSS":     "🛑",
    "INDEX REMOVAL": "🚫",
    "CRISIS EXIT":   "🔴",
}


def _render_trade_card(e: dict) -> None:
    """Render one open position as a styled HTML status card — momentum/
    membership/crisis context (see output/performance.py:get_brief_cards())."""
    status = e.get("status", "ON TRACK")
    colour = _STATUS_COLOURS.get(status, "#8899AA")
    icon   = _STATUS_ICONS.get(status, "")
    bg     = _hex_rgba(colour, 0.08)

    ticker      = html.escape(str(e.get("ticker", "")))
    asset_type  = html.escape(str(e.get("asset_type", "")))
    days_held   = int(e.get("days_held") or 0)
    max_hold    = e.get("max_holding_days")

    unreal_pnl_gbp = e.get("unreal_pnl_gbp")
    unreal_pct     = e.get("unreal_pct")
    if unreal_pnl_gbp is not None and unreal_pct is not None:
        pnl_col  = "#16a34a" if unreal_pnl_gbp >= 0 else "#dc2626"
        pnl_html = html.escape(f"£{unreal_pnl_gbp:+.2f} ({unreal_pct:+.2f}%)")
    else:
        pnl_html = "—"
        pnl_col  = "#8899AA"

    mom_entry = e.get("momentum_pct_at_entry")
    sig_now   = e.get("current_sig_on")
    detail_parts = []
    if mom_entry is not None:
        detail_parts.append(html.escape(f"Momentum at entry: {mom_entry * 100:+.1f}%"))
    detail_parts.append(f"Momentum now: <strong>{'long' if sig_now else 'flat' if sig_now is False else '—'}</strong>")
    if e.get("current_is_member") is False:
        detail_parts.append("<span style='color:#dc2626'>No longer index-eligible</span>")
    if e.get("crisis_now"):
        detail_parts.append("<span style='color:#991b1b;font-weight:700'>Crisis override active today</span>")
    stop_price = e.get("stop_price")
    if stop_price is not None:
        detail_parts.append(html.escape(f"Stop: {stop_price:,.4f}"))

    # Decision-relevant context, added 2026-07-31 — plainly descriptive of
    # how this position's exit rules work, never color-coded or ranked by
    # proximity (position_context_style() always returns the same neutral
    # colour regardless of either value — see its own docstring and
    # tests/test_performance.py's proximity-sweep test).
    ctx = compute_position_context(e)
    ctx_colour = position_context_style(ctx["pct_above_stop"], ctx["days_remaining"])
    ctx_parts = []
    if ctx["pct_above_stop"] is not None:
        ctx_parts.append(html.escape(f"{ctx['pct_above_stop']:.1f}% above stop"))
    if ctx["days_remaining"] is not None:
        ctx_parts.append(html.escape(
            f"day {days_held} of {max_hold} ({ctx['days_remaining']} days until max-holding limit)"
        ))
    context_html = ""
    if ctx_parts:
        context_html = (
            f"<div style='font-size:0.82rem;color:{ctx_colour};margin-top:4px'>"
            f"{' · '.join(ctx_parts)}"
            f"</div>"
        )
    rule_html = (
        f"<div style='font-size:0.78rem;color:{ctx_colour};margin-top:2px'>"
        f"{html.escape(position_exit_rule_text(max_hold))}"
        f"</div>"
    )

    st.markdown(
        f"<div style='border-left:4px solid {colour};padding:10px 16px;"
        f"background:{bg};border-radius:0 6px 6px 0;margin-bottom:8px'>"
        f"<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:4px'>"
        f"<span style='background:{colour};color:#fff;padding:2px 9px;"
        f"border-radius:4px;font-size:0.72rem;font-weight:800;letter-spacing:0.05em'>"
        f"{icon} {status}</span>"
        f"<strong style='font-size:1rem'>{ticker}</strong>"
        f"<span style='color:#8899AA;font-size:0.85rem'>{asset_type} · "
        f"{'Day ' + str(days_held) + '/' + str(max_hold) if max_hold is not None else 'Day ' + str(days_held)}</span>"
        f"<span style='color:{pnl_col};font-weight:700;font-size:0.9rem'>{pnl_html}</span>"
        f"</div>"
        f"<div style='font-size:0.82rem;color:#8899AA'>"
        f"{' · '.join(detail_parts)}"
        f"</div>"
        f"{context_html}"
        f"{rule_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_trade_card_compact(e: dict) -> None:
    """Render one open position as a single-line summary row — used on the
    Daily Briefing, where full detail lives on the Performance page."""
    status = e.get("status", "ON TRACK")
    colour = _STATUS_COLOURS.get(status, "#8899AA")
    icon   = _STATUS_ICONS.get(status, "")
    bg     = _hex_rgba(colour, 0.08)

    ticker    = html.escape(str(e.get("ticker", "")))
    asset_tag = html.escape(str(e.get("asset_type", "")))
    days_held = int(e.get("days_held") or 0)

    entry_date_str = e.get("entry_date")
    entry_disp = "—"
    if entry_date_str:
        try:
            entry_disp = datetime.fromisoformat(str(entry_date_str)).strftime("%Y-%m-%d")
        except Exception:
            entry_disp = str(entry_date_str)[:10]

    sig_on = e.get("current_sig_on")
    sig_disp = "Long" if sig_on is True else ("Flat" if sig_on is False else "—")

    unreal_pnl_gbp = e.get("unreal_pnl_gbp")
    unreal_pct     = e.get("unreal_pct")
    if unreal_pnl_gbp is not None and unreal_pct is not None:
        pnl_col  = "#16a34a" if unreal_pnl_gbp >= 0 else "#dc2626"
        pnl_html = html.escape(f"£{unreal_pnl_gbp:+.2f} ({unreal_pct:+.2f}%)")
    else:
        pnl_html = "—"
        pnl_col  = "#8899AA"

    # Decision-relevant context, added 2026-07-31 — same neutral, non-ranked
    # treatment as the full-detail card on the Performance page (see
    # _render_trade_card() and output/performance.py:position_context_style()).
    ctx = compute_position_context(e)
    ctx_colour = position_context_style(ctx["pct_above_stop"], ctx["days_remaining"])
    ctx_bits = []
    if ctx["pct_above_stop"] is not None:
        ctx_bits.append(f"{ctx['pct_above_stop']:.1f}% above stop")
    if ctx["days_remaining"] is not None:
        ctx_bits.append(f"{ctx['days_remaining']}d until max-hold")
    ctx_html = (
        f"<span style='color:{ctx_colour};font-size:0.78rem'>{html.escape(' · '.join(ctx_bits))}</span>"
        if ctx_bits else ""
    )

    st.markdown(
        f"<div style='border-left:4px solid {colour};padding:6px 14px;"
        f"background:{bg};border-radius:0 6px 6px 0;margin-bottom:6px;"
        f"display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        f"<span style='background:{colour};color:#fff;padding:1px 8px;"
        f"border-radius:4px;font-size:0.68rem;font-weight:800;letter-spacing:0.05em'>"
        f"{icon} {status}</span>"
        f"<strong style='font-size:0.92rem'>{ticker}</strong>"
        f"<span style='color:#8899AA;font-size:0.78rem'>{asset_tag} · Day {days_held} · Entered {entry_disp}</span>"
        f"<span style='color:#8899AA;font-size:0.78rem'>Signal: {sig_disp}</span>"
        f"{ctx_html}"
        f"<span style='color:{pnl_col};font-weight:700;font-size:0.85rem'>{pnl_html}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=300)
def _get_ticker_status() -> list[dict]:
    """Per-ticker live status (momentum, index membership, crisis flags).
    Thin cached wrapper — the data-shaping logic lives in
    output/performance.py, which is directly unit-tested (main.py itself is
    not importable in tests). Reads config fresh via _load_config() since
    unhashable dicts can't be Streamlit cache keys."""
    return get_ticker_status(_load_config())


def _get_open_cards(config: dict) -> list[dict]:
    """Enriched trade-card dicts for open positions — thin wrapper around
    output/performance.py:get_brief_cards(), passing in this page's already-
    cached _get_ticker_status() so the computation isn't repeated."""
    return get_brief_cards(config, ticker_status_rows=_get_ticker_status())


def _get_closed_rows() -> list[dict]:
    """All reconstructed closed positions — thin wrapper around
    output/performance.py:get_pnl_rows()."""
    return get_pnl_rows()


@st.cache_data(ttl=300)
def _get_crisis_status() -> dict:
    """Today's crisis-override proximity — thin cached wrapper around
    output/performance.py:get_crisis_status()."""
    return get_crisis_status()


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

def _render_page_wordmark(page_name: str) -> None:
    """Small "SP500INVEST · <page>" brand prefix at the top of a page's main
    content area — a subtle identity mark, not a replacement for that
    page's own st.markdown("## ...") title which still follows it."""
    st.markdown(
        '<span style="font-size: 0.85rem; font-weight: 700; letter-spacing: 0.15em; color: #00C2FF;">SP500INVEST</span>'
        f'<span style="font-size: 0.85rem; color: #8899AA; margin-left: 0.5rem;">{page_name}</span>',
        unsafe_allow_html=True,
    )


def _render_footer() -> None:
    """Branded footer shown at the bottom of every page."""
    st.markdown("---")
    st.markdown(
        '<div style="text-align: center; color: #8899AA; font-size: 0.75rem; padding: 0.5rem 0;">'
        'SP500INVEST &nbsp;·&nbsp; Systematic large-cap momentum research. &nbsp;·&nbsp; '
        '<a href="mailto:hello@example.com" style="color: #00C2FF; text-decoration: none;">hello@example.com</a>'
        ' &nbsp;·&nbsp; <a href="https://example.com" style="color: #00C2FF; text-decoration: none;">example.com</a>'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _initialise() -> None:
    """Ensure the DB schema is current and run any first-session setup.

    init_db() is called on every render because it is idempotent — create_all()
    is a no-op when tables exist and _apply_migrations() skips columns that are
    already present. The scheduler is started at module load via
    _start_scheduler_at_startup() (a st.cache_resource call), so jobs fire even
    if nobody has visited the page since the last server restart.
    """
    init_db()
    st.session_state.setdefault("app_initialised", True)


# ---------------------------------------------------------------------------
# Charts landing (grouped asset cards)
# ---------------------------------------------------------------------------

_GROUP_LABELS = {
    "core":        "Core (index / FX reference)",
    "instruments": "Tracked Universe",
}


def _render_charts_landing(config: dict) -> None:
    """Grouped asset cards shown on Charts before any ticker is selected."""
    from sqlalchemy import text as sa_text

    _render_page_wordmark("Charts")
    st.markdown("## Charts")
    st.caption("Select an asset to view its price chart.")

    _all_tickers_cl = [e["ticker"] for e in get_all_watchlist_entries(config)]

    # Fetch last two closes per ticker in one query for 1-day change
    _chg_map_cl: dict[str, float] = {}
    try:
        with get_engine().connect() as _conn_cl:
            _price_rows_cl = _conn_cl.execute(sa_text(
                "WITH ranked AS ("
                "  SELECT ticker, close,"
                "         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn"
                "  FROM price_data"
                ") SELECT ticker, close, rn FROM ranked WHERE rn <= 2"
            )).mappings().all()
        _lat_cl: dict[str, float] = {}
        _prv_cl: dict[str, float] = {}
        for _row_cl in _price_rows_cl:
            _tk_cl, _cl_cl, _rn_cl = _row_cl["ticker"], _row_cl["close"], _row_cl["rn"]
            if _rn_cl == 1:
                _lat_cl[_tk_cl] = _cl_cl
            else:
                _prv_cl[_tk_cl] = _cl_cl
        for _tk_cl in _lat_cl:
            if _tk_cl in _prv_cl and _prv_cl[_tk_cl] > 0:
                _chg_map_cl[_tk_cl] = (_lat_cl[_tk_cl] - _prv_cl[_tk_cl]) / _prv_cl[_tk_cl] * 100
    except Exception:
        pass

    # `instruments:` is empty now (2026-08-02, see CLAUDE.md's "wire the
    # momentum-only, full-S&P-500 selection into live execution" section)
    # -- this grouped-card display only ever grouped the OLD hand-curated
    # 40-stock list under its own "Instruments" header; that group is
    # simply empty/absent now, a deliberate consequence, not a bug.
    # Rendering the full dynamic live candidate pool (potentially ~500
    # tickers) as individual grouped cards here would be a much larger UX
    # change than this task's scope -- _all_tickers_cl above already
    # includes those dynamic tickers for this page's other purposes
    # (1-day change lookups etc.), just not as their own card group.
    _groups_cl: dict[str, list] = dict(config.get("watchlist", {}))
    _groups_cl["instruments"] = [
        {**raw, "ticker": t} for t, raw in config.get("instruments", {}).items()
        if isinstance(raw, dict)
    ]

    for group_key, group_label in _GROUP_LABELS.items():
        group = _groups_cl.get(group_key, [])
        if not group:
            continue
        st.markdown(f"#### {group_label}")
        cols = st.columns(4)
        for i, entry in enumerate(sorted(group, key=lambda e: e["ticker"])):
            ticker  = entry["ticker"]
            name    = entry["name"]
            _chg    = _chg_map_cl.get(ticker)
            _chg_str = f"  {_chg:+.2f}%" if _chg is not None else ""
            with cols[i % 4]:
                if st.button(
                    f"**{ticker}**  \n{name}{_chg_str}",
                    key=f"cl_{ticker}",
                    width="stretch",
                ):
                    st.session_state["selected_ticker"] = ticker
                    st.rerun()
        st.markdown("")

    _render_footer()


# ---------------------------------------------------------------------------
# Daily Briefing page
# ---------------------------------------------------------------------------

def _render_briefing_page(config: dict) -> None:
    """Render the Daily Briefing: live momentum/crisis status and open positions."""
    _render_page_wordmark("Daily Briefing")
    st.markdown("## Daily Briefing")

    last_sched = get_metadata("last_scheduled_run")
    if last_sched:
        st.caption(
            f"As of: {_fmt_uk(last_sched, '%Y-%m-%d %H:%M')} · "
            f"Updates: {_uk_sched_time(7,0)}, {_uk_sched_time(13,30)} and {_uk_sched_time(21,30)}"
        )
    else:
        st.caption("No scheduled run yet — updates run automatically on weekdays.")

    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        st.info("Trading is disabled. Set `trading212_invest.enabled: true` in config.yaml to activate.")
        _render_footer()
        return

    status_rows = _get_ticker_status()
    valid_rows  = [r for r in status_rows if not r.get("insufficient_history")]
    long_count  = sum(1 for r in valid_rows if r.get("sig_on") is True)
    flat_count  = sum(1 for r in valid_rows if r.get("sig_on") is False)
    crisis_active = any(r.get("crisis_now") for r in valid_rows)
    entry_blocked = any(r.get("entry_blocked") for r in valid_rows)

    badge_colour = "#991b1b" if crisis_active else "#16a34a"
    badge_text   = "🔴 CRISIS OVERRIDE ACTIVE" if crisis_active else "🟢 LIVE (DEMO)"
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;'
        f'border:1px solid {badge_colour}44;border-radius:8px;padding:8px 14px;margin-bottom:6px">'
        f'<span style="background:{badge_colour};color:#fff;font-weight:700;'
        f'padding:2px 9px;border-radius:12px;font-size:0.8rem">{badge_text}</span>'
        f'<span style="color:#8899AA;font-size:0.85rem">'
        f'{long_count} long · {flat_count} flat of {len(valid_rows)} tickers'
        + (' · new entries blocked (crisis cooldown)' if entry_blocked else '')
        + '</span></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Entry: 12-month momentum > 0, current index membership, not in the "
        "crisis entry-block window. Exit priority: crisis override → index "
        "removal → ATR stop loss → momentum turning non-positive → max holding."
    )

    # --- Crisis override proximity ---------------------------------------
    cs = _get_crisis_status()
    if cs:
        today_ret = cs.get("today_return_pct")
        dist_pp = cs.get("distance_to_threshold_pp")
        if cs.get("entry_blocked"):
            since = cs.get("days_since_trigger")
            remaining = max(0, cs.get("cooldown_days", 5) - since) if since is not None else None
            cband, ctext = "#991b1b", (
                f"🔴 Entry-block cooldown active — triggered {cs.get('last_trigger_date', '?')}"
                + (f", {remaining} trading day(s) remaining" if remaining is not None else "")
            )
        elif today_ret is not None and dist_pp is not None and dist_pp <= 2.0:
            cband, ctext = "#b45309", (
                f"🟡 Approaching crisis threshold — today's S&P move {today_ret:+.2f}% "
                f"({dist_pp:.2f}pp from the -7.0% trigger)"
            )
        else:
            cband, ctext = "#16a34a", (
                f"🟢 No crisis override active — today's S&P move "
                f"{today_ret:+.2f}%" if today_ret is not None else "🟢 No crisis override active"
            )
        st.markdown(
            f'<div style="border:1px solid {cband}44;border-radius:8px;padding:6px 14px;'
            f'margin-bottom:10px;color:{cband};font-size:0.85rem;font-weight:600">{html.escape(ctext)}</div>',
            unsafe_allow_html=True,
        )

    # --- Next scheduled membership refresh --------------------------------
    next_refresh = _next_run_for_time(21, 15)
    st.caption(f"Next S&P 500 membership refresh: {_fmt_uk(next_refresh, '%Y-%m-%d %H:%M')} "
               f"(21:15 UTC weekdays, 15 minutes ahead of the EOD trading run)")

    st.divider()
    st.markdown("### Open Positions")
    open_cards = _get_open_cards(config)
    held_tickers = {c.get("ticker") for c in open_cards}
    st.markdown(f"**{positions_summary_text(len(open_cards))}**")
    if open_cards:
        st.caption(POSITIONS_RULES_EXPLANATION)
    if not open_cards:
        st.info("No open positions.")
    else:
        # Stable, neutral order — alphabetical by ticker. Deliberately not
        # sorted by status/proximity to a threshold, which would imply
        # some positions need more attention than others.
        all_cards = sorted(open_cards, key=lambda x: x.get("ticker", ""))
        for e in all_cards:
            _render_trade_card_compact(e)
        st.caption("Full position detail is on the **Performance** page.")

    st.divider()
    st.markdown(f"### Every Tracked Ticker — Eligibility & Momentum ({len(valid_rows)} tracked)")
    st.caption(
        "Every ticker in the live universe, not just open positions — momentum state, current "
        "index eligibility, and crisis entry-block status. \"Held\" marks a position currently open."
    )
    with st.expander("Show full ticker table", expanded=True):
        status_df = pd.DataFrame(valid_rows)
        if not status_df.empty:
            status_df = status_df.sort_values(by=["sig_on", "momentum_pct"], ascending=[False, False])
        status_df["Held"] = status_df["ticker"].map(lambda t: "●" if t in held_tickers else "")
        status_df["Type"] = status_df["is_etf"].map(lambda b: "ETF" if b else "STOCK")
        status_df["sig_on"] = status_df["sig_on"].map(lambda b: "Long" if b else "Flat")
        status_df["is_member"] = status_df["is_member"].map(lambda b: "Yes" if b else "No")
        status_df["momentum_pct"] = status_df["momentum_pct"].map(
            lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "—"
        )
        status_df["entry_blocked"] = status_df["entry_blocked"].map(lambda b: "Yes" if b else "No")
        status_df = status_df[["ticker", "Held", "Type", "sig_on", "momentum_pct", "is_member", "entry_blocked"]]
        status_df.columns = ["Ticker", "Held", "Type", "Momentum", "12m Return", "Index Eligible", "Entry Blocked"]
        st.dataframe(status_df, width="stretch", hide_index=True)

    _render_footer()


# ---------------------------------------------------------------------------
# Status page
# ---------------------------------------------------------------------------

def _pipeline_row(label: str, ts: "datetime | None", warn_h: float, error_h: float,
                   desc: str, never_badge: str = "PENDING") -> str:
    """Build one row of the Status page's Pipeline table as an HTML string."""
    if ts is None:
        badge, colour = never_badge, ("#8899AA" if never_badge != "PENDING" else "#f59e0b")
        age_str = "—"
    else:
        level = _age_level(ts, warn_h, error_h)
        colour = _LEVEL_COLOUR.get(level, "#8899AA")
        badge = {"ok": "OK", "warn": "STALE", "error": "OVERDUE"}.get(level, "OK")
        _, age_str = _age_display(ts)
    ts_str = _fmt_uk(ts, "%Y-%m-%d %H:%M") if ts else "Never"
    return (
        '<div style="display:flex;align-items:center;padding:6px 0;border-bottom:1px solid #1E2D45;'
        'font-size:0.85rem">'
        f'<div style="width:32px;flex-shrink:0;color:{colour}">●</div>'
        f'<div style="flex:0 0 18%;padding-right:8px;font-weight:600">{html.escape(label)}</div>'
        f'<div style="flex:0 0 17%;padding-right:8px;color:#8899AA">{html.escape(ts_str)}</div>'
        f'<div style="flex:0 0 7%;padding-right:8px;color:#8899AA">{html.escape(age_str)}</div>'
        f'<div style="flex:0 0 8%;padding-right:8px;color:{colour};font-weight:700">{badge}</div>'
        f'<div style="flex:1;color:#8899AA">{html.escape(desc)}</div>'
        '</div>'
    )


def _render_status_page(config: dict, watchlist: dict[str, str]) -> None:
    """System status panel — rendered as the Status page."""
    st.cache_data.clear()
    _render_page_wordmark("Status")
    st.markdown("## System Status")

    deploy_ts, deploy_msg = _git_log_info()
    uptime                = _service_uptime()
    db_size_str, db_level = _db_size(config)

    last_refresh = get_metadata("last_full_refresh")
    last_sched   = get_metadata("last_scheduled_run")
    last_sched_name = get_metadata_str("last_scheduled_run_name") or ""
    last_membership_refresh = get_metadata("last_membership_refresh_success")

    sc1, sc2, sc3 = st.columns(3)
    _status_card(sc1, "Last Deployment", deploy_ts, deploy_msg, "info")
    _status_card(sc2, "Service Uptime",  uptime, "Process running", "ok")
    _status_card(sc3, "Database Size",   db_size_str, "", db_level)

    st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
    pr1, pr2, pr3 = st.columns(3)

    _ref_val, _ref_sub = _age_display(last_refresh)
    _status_card(pr1, "Last Data Refresh", _ref_val, _ref_sub,
                 _age_level(last_refresh, warn_hours=48.0, error_hours=96.0))

    _sched_val, _sched_sub = _age_display(last_sched)
    if last_sched_name:
        _sched_sub = f"{last_sched_name} · {_sched_sub}"
    _now_check  = datetime.utcnow()
    _sched_hrs  = (
        (_now_check - last_sched.replace(tzinfo=None)).total_seconds() / 3600
        if last_sched else float("inf")
    )
    _is_weekday = _now_check.weekday() < 5
    _sched_lvl  = (
        "error" if (_sched_hrs > 26 and _is_weekday)
        else "warn" if _sched_hrs > 14
        else ("ok" if last_sched else "error")
    )
    _status_card(pr2, "Last Scheduled Run", _sched_val, _sched_sub, _sched_lvl)

    _sched_cfg_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_LABELS = {"eod": "End of Day"}
    _nxt_dt, _nxt_label = None, ""
    for _rn, _rl in _RUN_LABELS.items():
        _rc = _sched_cfg_runs.get(_rn, {})
        _nt = _next_run_for_time(int(_rc.get("hour", 0)), int(_rc.get("minute", 0)))
        if _nxt_dt is None or _nt < _nxt_dt:
            _nxt_dt, _nxt_label = _nt, _rl
    # Monday pre-market (stale-order cancellation) is a separate, Monday-only
    # job fixed at 07:00 UTC (data/scheduler.py:_monday_premarket_job()) —
    # included in the same "which fires soonest" comparison as EOD above.
    _nt_mon = _next_monday_at(7, 0)
    if _nxt_dt is None or _nt_mon < _nxt_dt:
        _nxt_dt, _nxt_label = _nt_mon, "Monday Pre-Market"
    if _nxt_dt:
        _nxt_delta = (_nxt_dt - datetime.utcnow()).total_seconds()
        _nxt_until = (
            f"in {int(_nxt_delta / 60)}m" if _nxt_delta < 3600
            else f"in {_nxt_delta / 3600:.1f}h" if _nxt_delta < 86400
            else f"in {_nxt_delta / 86400:.1f}d"
        )
        _nxt_val = _fmt_uk(_nxt_dt, "%a %H:%M")
        _nxt_sub = f"{_nxt_until} — {_nxt_label}"
    else:
        _nxt_val, _nxt_sub = "Unknown", ""
    _status_card(pr3, "Next Scheduled Run", _nxt_val, _nxt_sub, "info")

    fresh, stale, missing, _max_stale_td = _price_coverage(config)
    total = len(watchlist)

    if not fresh and not stale and not missing:
        price_desc = "No price data yet — use Manual Controls below to trigger an immediate pull"
    elif missing:
        price_desc = (
            f"{len(fresh)}/{total} tickers up to date"
            + (f" · {len(stale)} stale" if stale else "")
            + f" · {len(missing)} missing: {', '.join(missing[:4])}"
            + ("…" if len(missing) > 4 else "")
        )
    elif stale:
        price_desc = (
            f"{len(fresh)}/{total} fresh · {len(stale)} stale: "
            + ", ".join(stale[:5]) + ("…" if len(stale) > 5 else "")
        )
    else:
        price_desc = f"All {total} tickers up to date"

    last_run_type = get_metadata_str("last_run_type")
    run_label = {"full": "Full Refresh", "analysis_only": "Order Logic Only"}.get(last_run_type or "", "")
    order_desc = (
        f"Last run: {run_label}" if run_label else "Not yet run"
    )

    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)
    st.markdown("### Pipeline")

    _THEAD = (
        '<div style="font-size:0.88rem">'
        '<div style="display:flex;align-items:center;padding:4px 0 6px;border-bottom:2px solid #1E2D45;'
        'color:#8899AA;font-size:0.70rem;text-transform:uppercase;letter-spacing:0.07em">'
        '<div style="width:32px;flex-shrink:0"></div>'
        '<div style="flex:0 0 18%;padding-right:8px">Step</div>'
        '<div style="flex:0 0 17%;padding-right:8px">Last Run</div>'
        '<div style="flex:0 0 7%;padding-right:8px">Age</div>'
        '<div style="flex:0 0 8%;padding-right:8px">Status</div>'
        '<div style="flex:1">Description</div>'
        '</div>'
    )
    _TFOOT = "</div>"

    rows = "".join([
        _pipeline_row(
            "1 · Price Data Refresh", last_refresh,
            warn_h=48.0, error_h=96.0, desc=price_desc, never_badge="PENDING",
        ),
        _pipeline_row(
            "2 · Order Logic", last_sched,
            warn_h=48.0, error_h=72.0, desc=order_desc, never_badge="PENDING",
        ),
    ])
    st.markdown(_THEAD + rows + _TFOOT, unsafe_allow_html=True)

    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)
    st.markdown("### Scheduled Runs")
    st.caption(
        "The 07:00 (Tue–Fri) pre-market and 13:30 US-open analysis-only runs "
        "that used to sit here were removed 2026-07-31 — confirmed redundant "
        "with the hourly trade-alert job below, not just assumed. See "
        "CLAUDE.md's \"Pre-market/US-open scheduled runs removed\" section."
    )
    sched_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_DISPLAY = {
        "eod": ("End of Day", "Full data refresh + order logic after NYSE close"),
    }
    run_cols = st.columns(len(_RUN_DISPLAY) + 1)
    for col, (run_name, (label, desc)) in zip(run_cols, _RUN_DISPLAY.items()):
        run_cfg = sched_runs.get(run_name, {})
        h, m    = run_cfg.get("hour", 0), run_cfg.get("minute", 0)
        time_str = _uk_sched_time(h, m)
        nxt     = _next_run_for_time(h, m)
        delta   = (nxt - datetime.utcnow()).total_seconds()
        until   = (
            f"in {int(delta / 60)}m" if delta < 3600
            else f"in {delta / 3600:.1f}h" if delta < 86400
            else f"in {delta / 86400:.1f}d"
        )
        _status_card(col, f"{label} · {time_str}", _fmt_uk(nxt, "%a %H:%M"),
                     f"{until} — {desc}", "info")

    # Monday-only stale-order cancellation — fixed at 07:00 UTC directly in
    # data/scheduler.py, not driven by config.yaml's scheduler.runs (see that
    # module's own comment on why there is deliberately no separate
    # scheduler.runs.monday_premarket config key).
    _mon_nxt = _next_monday_at(7, 0)
    _mon_delta = (_mon_nxt - datetime.utcnow()).total_seconds()
    _mon_until = (
        f"in {int(_mon_delta / 60)}m" if _mon_delta < 3600
        else f"in {_mon_delta / 3600:.1f}h" if _mon_delta < 86400
        else f"in {_mon_delta / 86400:.1f}d"
    )
    _status_card(
        run_cols[-1], f"Monday Pre-Market · {_uk_sched_time(7, 0)}",
        _fmt_uk(_mon_nxt, "%a %H:%M"),
        f"{_mon_until} — Cancels stale weekend BUY orders, then order logic",
        "info",
    )

    st.divider()
    st.markdown("### Data Coverage")
    cc1, cc2, cc3 = st.columns(3)
    fresh_level   = "ok"    if len(fresh)   == total else ("warn" if len(fresh) >= total * 0.9 else "error")
    stale_level   = "ok"    if not stale    else ("error" if _max_stale_td > 7 else "warn")
    missing_level = "ok"    if not missing  else "error"
    _status_card(cc1, "Fresh (≤3 trading days)", f"{len(fresh)} / {total}",
                 "all up to date" if len(fresh) == total else f"{total - len(fresh)} not fresh", fresh_level)
    _status_card(cc2, "Stale (>3 trading days)", f"{len(stale)} ticker{'s' if len(stale) != 1 else ''}",
                 ", ".join(stale[:5]) + ("…" if len(stale) > 5 else "") if stale else "none", stale_level)
    _status_card(cc3, "Missing entirely", f"{len(missing)} ticker{'s' if len(missing) != 1 else ''}",
                 ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "") if missing else "none", missing_level)

    st.divider()
    st.markdown("### Manual Controls")
    st.caption("Trigger the data pull + order pipeline immediately without waiting for the next scheduled run.")
    _mc1, _mc2 = st.columns(2)

    with _mc1:
        if st.button("Refresh Data + Orders Now", width="stretch", type="primary",
                     key="status_full_refresh"):
            _cfg     = _load_config()
            _tickers = get_all_tickers(_cfg)
            with st.spinner("Pulling latest prices…"):
                try:
                    _results  = pull_incremental(tickers=_tickers)
                    _new_rows = sum(_results.values())
                    if _new_rows > 0:
                        set_metadata("last_full_refresh", datetime.utcnow().isoformat())
                except Exception as _exc:
                    st.error(f"Data pull failed: {_exc}")
                    _new_rows = 0
            with st.spinner("Running order pipeline…"):
                try:
                    from brokers.trading212 import maybe_place_orders
                    maybe_place_orders(_cfg, run_type="full")
                    set_metadata("last_run_type", "full")
                    st.success(
                        f"Done — {_new_rows} new price rows · order pipeline ran at "
                        f"{_fmt_uk(datetime.utcnow(), '%H:%M')}."
                    )
                    st.cache_data.clear()
                except Exception as _exc:
                    st.error(f"Order pipeline failed: {_exc}")

    with _mc2:
        if st.button("Order Logic Only", width="stretch", key="status_run_orders"):
            _cfg = _load_config()
            with st.spinner("Running order pipeline…"):
                try:
                    from brokers.trading212 import maybe_place_orders
                    maybe_place_orders(_cfg, run_type="analysis_only")
                    set_metadata("last_run_type", "analysis_only")
                    st.success(f"Done — exit check ran at {_fmt_uk(datetime.utcnow(), '%H:%M')}.")
                    st.cache_data.clear()
                except Exception as _exc:
                    st.error(f"Order pipeline failed: {_exc}")

    notif_cfg = config.get("notifications", {}).get("email", {})
    st.divider()
    st.markdown("### Email Notifications")
    _from    = notif_cfg.get("from_address") or notif_cfg.get("smtp_user", "—")
    _to      = ", ".join(notif_cfg.get("to_addresses") or []) or "—"
    _relay   = f"{notif_cfg.get('smtp_host', 'smtp-relay.brevo.com')}:{notif_cfg.get('smtp_port', 587)}"
    _enabled = notif_cfg.get("enabled", False)
    st.caption(
        f"From: **{_from}** · To: **{_to}** · Via: {_relay} · "
        f"Alerts: {'**enabled**' if _enabled else 'disabled'} "
        "(order-placement error alerts only — see brokers/trading212.py)"
    )

    # ── T212 Invest Account ───────────────────────────────────────────────
    st.divider()
    st.markdown("### T212 Invest Account")

    t212_cfg     = config.get("trading212_invest", {})
    t212_enabled = t212_cfg.get("enabled", False)
    t212_has_key = bool(os.environ.get("T212_API_KEY", "")) and bool(os.environ.get("T212_API_SECRET", ""))
    t212_error   = get_metadata_str("t212_invest_error")

    st.markdown(
        (
            '<span style="background:#16a34a;color:#fff;padding:2px 9px;border-radius:4px;'
            'font-size:0.78rem;font-weight:700">🟢 LIVE (DEMO)</span>'
            if t212_enabled else
            '<span style="background:#8899AA;color:#fff;padding:2px 9px;border-radius:4px;'
            'font-size:0.78rem;font-weight:700">NOT ENABLED</span>'
        ),
        unsafe_allow_html=True,
    )

    if not t212_enabled:
        st.info(
            "Trading is disabled. Set `trading212_invest.enabled: true` in config.yaml "
            "to activate. Add `T212_API_KEY` and `T212_API_SECRET` to the systemd service "
            "environment first."
        )
    else:
        t212_live = t212_cfg.get("live", False)
        # Dynamic live candidate pool (2026-08-02) -- config["instruments"]
        # is empty now, see CLAUDE.md's "wire the momentum-only,
        # full-S&P-500 selection into live execution" section.
        try:
            from analysis.universe import get_live_stock_candidates
            candidate_count = len(get_live_stock_candidates())
        except Exception:
            candidate_count = 0
        st.markdown(
            f'<span style="background:{"#f97316" if t212_live else "#6366f1"};color:#fff;'
            f'padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:600">'
            f'{"LIVE" if t212_live else "DEMO"}</span> '
            f'{candidate_count} tickers · '
            f'max {float(t212_cfg.get("per_trade_pct", 0.05)) * 100:.0f}% of account/order · '
            f'max {int(float(t212_cfg.get("max_total_deployed", 0.5)) * 100)}% deployed '
            f'(position count is emergent — no separate cap)',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Position sizing: VaR "
            f"({int(float(t212_cfg.get('var_confidence', 0.95)) * 100)}% confidence, "
            f"{int(t212_cfg.get('var_lookback_days', 252))}-day lookback, "
            f"{float(t212_cfg.get('max_risk_pct', 0.01)) * 100:.0f}% max risk — "
            f"floor £{float(t212_cfg.get('min_position_gbp', 50)):.0f})"
        )

        if t212_error:
            _err_col, _err_btn_col = st.columns([8, 1])
            with _err_col:
                st.warning(f"Last API error: {t212_error}")
            with _err_btn_col:
                st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
                if st.button("Clear", key="clear_t212_error"):
                    set_metadata("t212_invest_error", "")
                    st.rerun()

        if not t212_has_key:
            st.error(
                "API credentials not found. Set `T212_API_KEY` and `T212_API_SECRET` "
                "in the systemd service file and restart the service."
            )
        else:
            import json as _json
            snap_raw     = get_metadata_str("t212_account_snapshot")
            pos_raw      = get_metadata_str("t212_positions_snapshot")
            snap_fetched = get_metadata_str("t212_snapshot_fetched_at")
            pending_fetch = get_metadata_str("pending_t212_fetch")

            t2_col_status, t2_col_btn = st.columns([3, 1])
            with t2_col_btn:
                if st.button("Refresh Account", key="t212_refresh_btn"):
                    set_metadata("pending_t212_fetch", "1")
                    st.rerun()

            with t2_col_status:
                if pending_fetch:
                    st.info("Refresh requested — updating within 60s.")
                elif snap_fetched:
                    try:
                        age_s = (datetime.utcnow() - datetime.fromisoformat(snap_fetched)).total_seconds()
                        age_str = (
                            f"{int(age_s / 3600)}h ago" if age_s >= 3600
                            else f"{int(age_s / 60)}m ago" if age_s >= 60
                            else f"{int(age_s)}s ago"
                        )
                        st.caption(f"Last updated: {age_str}")
                    except Exception:
                        pass

            if snap_raw:
                try:
                    summary   = _json.loads(snap_raw)
                    positions = _json.loads(pos_raw) if pos_raw else []
                    cash_obj  = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
                    inv_obj   = summary.get("investments", {}) if isinstance(summary.get("investments"), dict) else {}
                    total_val = float(summary.get("totalValue", 0) or 0)
                    free_val  = float(cash_obj.get("availableToTrade", 0) or 0)
                    invested  = float(inv_obj.get("currentValue", 0) or 0)
                    deployed  = (invested / total_val * 100) if total_val else 0.0
                    pos_count = len(positions) if isinstance(positions, list) else 0

                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("Account Value", f"£{total_val:,.2f}")
                    a2.metric("Available Cash", f"£{free_val:,.2f}")
                    a3.metric("Invested", f"£{invested:,.2f}", delta=f"{deployed:.0f}% deployed")
                    a4.metric("Open Positions", str(pos_count))

                    deploy_limit = float(t212_cfg.get("max_total_deployed", 0.5))
                    bar_pct      = min(1.0, (invested / total_val) if total_val else 0.0)
                    bar_colour   = "#dc2626" if bar_pct >= deploy_limit else (
                        "#f97316" if bar_pct >= deploy_limit * 0.85 else "#6366f1"
                    )
                    st.markdown(
                        f'<div style="margin:4px 0 2px;font-size:0.75rem;color:#8899AA">'
                        f'Deployed: £{invested:,.0f} of £{total_val:,.0f} '
                        f'(limit {int(deploy_limit * 100)}%)</div>'
                        f'<div style="background:#1E2D45;border-radius:4px;height:6px">'
                        f'<div style="background:{bar_colour};width:{bar_pct * 100:.1f}%;'
                        f'height:6px;border-radius:4px"></div></div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)
                    st.caption("Open positions are shown in full on the **Performance** and **Daily Briefing** pages.")

                    if snap_fetched:
                        try:
                            fetched_disp = _fmt_uk(datetime.fromisoformat(snap_fetched))
                        except (ValueError, TypeError):
                            fetched_disp = snap_fetched[:16].replace("T", " ")
                        st.caption(f"Updated {fetched_disp}")
                except Exception:
                    st.caption("No account data yet — click Refresh Account to fetch.")
            else:
                st.caption("No account data yet — click Refresh Account to fetch.")

            try:
                from sqlalchemy import text as _sa_ord
                with get_engine().connect() as _conn:
                    ord_rows = _conn.execute(_sa_ord("""
                        SELECT ticker, t212_ticker, direction, quantity,
                               estimated_value_gbp, var_pct, status, is_etf,
                               momentum_pct, account_type, error_message, placed_at
                        FROM invest_orders
                        ORDER BY placed_at DESC
                        LIMIT 10
                    """)).mappings().all()
                ord_data = [dict(r) for r in ord_rows]
            except Exception:
                ord_data = []

            st.markdown("#### Recent Orders")
            if ord_data:
                ord_df = pd.DataFrame(ord_data)
                ord_df["Type"] = ord_df["is_etf"].map(lambda b: "ETF" if b else "STOCK")
                ord_df["var_pct"] = ord_df["var_pct"].map(
                    lambda v: f"{v * 100:.2f}%" if pd.notna(v) else "—"
                )
                ord_df["momentum_pct"] = ord_df["momentum_pct"].map(
                    lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "—"
                )
                ord_df = ord_df[[
                    "ticker", "t212_ticker", "Type", "direction", "quantity",
                    "estimated_value_gbp", "var_pct", "momentum_pct", "status",
                    "account_type", "error_message", "placed_at",
                ]]
                ord_df.columns = [
                    "Ticker", "T212 Ticker", "Type", "Dir", "Qty",
                    "Est. Value (£)", "VaR at Entry", "Momentum at Entry", "Status",
                    "Account", "Error", "Placed At",
                ]
                st.dataframe(
                    ord_df.style.map(
                        lambda v: "color:#16a34a" if v == "FILLED"
                        else ("color:#d97706" if v == "PLACED"
                        else ("color:#dc2626" if v in ("FAILED", "CANCELLED", "REJECTED") else "")),
                        subset=["Status"],
                    ).map(
                        lambda v: "color:#16a34a" if v == "BUY"
                        else ("color:#dc2626" if v == "SELL" else ""),
                        subset=["Dir"],
                    ),
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("No orders recorded yet.")

            # Instrument mapping is resolved dynamically now (2026-08-02) --
            # see CLAUDE.md's "wire the momentum-only, full-S&P-500
            # selection into live execution" section. Shows every ticker
            # ever resolved (any status), not just today's live candidates
            # -- a FAILED row is exactly what needs manual review, and a
            # formerly-eligible ticker's VERIFIED row must stay visible for
            # as long as a position might still be open against it.
            from brokers.trading212 import load_all_instrument_resolutions
            resolutions = load_all_instrument_resolutions()
            if resolutions:
                n_verified = sum(1 for r in resolutions if r["status"] == "VERIFIED")
                n_failed = sum(1 for r in resolutions if r["status"] == "FAILED")
                n_pending = sum(1 for r in resolutions if r["status"] == "PENDING")
                label = f"Instrument mapping ({n_verified} verified"
                if n_failed:
                    label += f", {n_failed} failed — needs review"
                if n_pending:
                    label += f", {n_pending} pending retry"
                label += ")"
                with st.expander(label):
                    map_rows = [
                        {
                            "Ticker": r["ticker"],
                            "T212 Instrument": r["t212_instrument_id"] or "—",
                            "Currency": r["currency"] or "—",
                            "Status": r["status"],
                            "Note": r["note"] or "",
                        }
                        for r in resolutions
                    ]
                    st.dataframe(
                        pd.DataFrame(map_rows).style.map(
                            lambda v: "color:#16a34a" if v == "VERIFIED"
                            else ("color:#dc2626" if v == "FAILED"
                            else ("color:#d97706" if v == "PENDING" else "")),
                            subset=["Status"],
                        ),
                        width="stretch", hide_index=True,
                    )
            else:
                st.caption("No instrument resolutions recorded yet — none needed until the first live candidate is evaluated.")

            # --- ETF pricing-path audit + manual seed control -------------
            from brokers.trading212 import audit_etf_pricing_paths, seed_instrument_price_gbp

            with st.expander("ETF pricing-path audit (currently inert — no ETFs in live trading)"):
                st.caption(
                    "The ETF sleeve was removed from live trading entirely (2026-08-02) — "
                    "this audit always reports 0 rows now, and the seed form below has "
                    "nothing to target. Kept visible rather than removed in case the ETF "
                    "sleeve is ever reconsidered, at which point this becomes live again "
                    "automatically."
                )
                st.caption(
                    "get_instrument_price_gbp()'s 3-tier pricing (live position → cached "
                    "price → refusal) means an ETF with no current holding and no cached "
                    "price has no automated path to a first price — a new BUY signal for it "
                    "sizes to 0 and is silently skipped. Locked-out ETFs need a manually "
                    "verified seed price before they can ever be newly entered."
                )
                try:
                    audit_rows = audit_etf_pricing_paths(config)
                except Exception as exc:
                    audit_rows = []
                    st.warning(f"Audit failed: {exc}")

                if audit_rows:
                    n_held = sum(1 for r in audit_rows if r["held"])
                    n_cached = sum(1 for r in audit_rows if r["cached"])
                    n_locked = sum(1 for r in audit_rows if r["locked_out"])
                    ac1, ac2, ac3 = st.columns(3)
                    ac1.metric("Held", n_held)
                    ac2.metric("Cached (unheld)", n_cached)
                    ac3.metric("Locked out", n_locked)

                    audit_df = pd.DataFrame(audit_rows)
                    audit_df["Status"] = audit_df.apply(
                        lambda r: "Held" if r["held"] else (
                            f"Cached ({r['cache_source'] or 'live-derived'})" if r["cached"]
                            else "LOCKED OUT"
                        ), axis=1,
                    )
                    audit_df = audit_df[["ticker", "t212_ticker", "Status"]]
                    audit_df.columns = ["Ticker", "T212 Instrument", "Status"]
                    st.dataframe(
                        audit_df.style.map(
                            lambda v: "color:#dc2626;font-weight:700" if v == "LOCKED OUT"
                            else ("color:#16a34a" if v == "Held" else "color:#8899AA"),
                            subset=["Status"],
                        ),
                        width="stretch", hide_index=True,
                    )

                st.markdown("#### Seed a manual price")
                st.caption(
                    "The price must come from directly reading T212's own displayed price "
                    "for the instrument — never from a research-ticker proxy (that proxy is "
                    "exactly what caused the XLF mis-sizing incident). Only use this for a "
                    "genuinely locked-out ETF you have personally verified on T212."
                )
                etf_tickers = sorted(
                    rt for rt, raw in mapping_full.items()
                    if isinstance(raw, dict) and raw.get("is_etf")
                )
                sc1, sc2, sc3 = st.columns([2, 2, 1])
                with sc1:
                    seed_rt = st.selectbox("Ticker", options=etf_tickers, key="seed_price_ticker")
                seed_currency_default = (
                    mapping_full.get(seed_rt, {}).get("currency", "USD")
                    if seed_rt else "USD"
                )
                with sc2:
                    seed_price = st.number_input(
                        "Price (instrument's own currency, as shown on T212)",
                        min_value=0.0, step=0.01, format="%.4f", key="seed_price_value",
                    )
                with sc3:
                    seed_currency = st.text_input(
                        "Currency", value=seed_currency_default, key="seed_price_currency",
                    )
                seed_confirm = st.checkbox(
                    "I have personally verified this price on T212's own platform right now.",
                    key="seed_price_confirm",
                )
                if st.button("Seed Price", key="seed_price_btn", disabled=not seed_confirm):
                    t212_ticker = mapping_full.get(seed_rt, {}).get("t212_instrument_id") if seed_rt else None
                    if not t212_ticker or seed_price <= 0:
                        st.error("Select a ticker and enter a positive price.")
                    else:
                        try:
                            price_gbp = seed_instrument_price_gbp(t212_ticker, seed_price, seed_currency)
                            st.success(
                                f"Seeded {seed_rt} ({t212_ticker}) at {seed_price:.4f} "
                                f"{seed_currency} (= £{price_gbp:.4f} GBP)."
                            )
                        except Exception as exc:
                            st.error(f"Seed failed: {exc}")

    # ── Scheduler Health ──────────────────────────────────────────────────
    st.divider()
    st.markdown("### Scheduler Health")

    _mr_val, _mr_sub = _age_display(last_membership_refresh)
    _mr_stale = is_membership_refresh_stale(last_membership_refresh)
    _mr_colour = "#dc2626" if _mr_stale else "#8899AA"
    _mr_caption = f"Membership refresh (fja05680/sp500): last successful {_mr_val} ({_mr_sub})"
    if _mr_stale:
        _mr_caption += f" — OVERDUE, past the {MEMBERSHIP_STALE_HOURS:.0f}h (~2 cycle) threshold"

    _wiki_json = get_metadata_str("membership_wikipedia_lag_check")
    _wiki_caption = "Wikipedia lag check: not yet run"
    _wiki_colour = "#8899AA"
    if _wiki_json:
        try:
            _wiki_result = json.loads(_wiki_json)
            _wiki_n = len(_wiki_result.get("disagreements", []))
            if _wiki_result.get("error"):
                _wiki_caption = f"Wikipedia lag check: last attempt failed — {_wiki_result['error']}"
                _wiki_colour = "#dc2626"
            elif _wiki_n:
                _wiki_caption = f"Wikipedia lag check: {_wiki_n} disagreement(s) flagged (lag detector only — not independent verification)"
                _wiki_colour = "#f59e0b"
            else:
                _wiki_caption = f"Wikipedia lag check: no disagreement ({_wiki_result.get('checked', 0)} ticker(s) checked — lag detector only, not independent verification)"
        except Exception:
            pass
    st.markdown(
        f'<div style="font-size:0.78rem;color:{_mr_colour};margin-bottom:2px">{html.escape(_mr_caption)}</div>'
        f'<div style="font-size:0.78rem;color:{_wiki_colour};margin-bottom:8px">{html.escape(_wiki_caption)}</div>',
        unsafe_allow_html=True,
    )

    try:
        import data.scheduler as _sched_mod
        _aps        = getattr(_sched_mod, "_scheduler", None)
        _aps_running = _aps is not None and getattr(_aps, "running", False)
    except Exception:
        _aps        = None
        _aps_running = False

    _daemon_active = False
    _daemon_age_s: float = float("inf")
    try:
        _hb_str = get_metadata_str("scheduler_heartbeat")
        if _hb_str:
            _hb_dt = datetime.fromisoformat(_hb_str)
            _daemon_age_s = (datetime.utcnow() - _hb_dt).total_seconds()
            _daemon_active = _daemon_age_s < 90
    except Exception:
        pass

    _JOB_LABELS: dict[str, tuple[str, str]] = {
        "scheduled_eod":        ("End of Day",    f"{_uk_sched_time(21, 30)} · Mon–Fri"),
        "monday_premarket":     ("Monday Pre-Market", f"{_uk_sched_time(7, 0)} · Monday"),
        "trade_alert_check":    ("Trade Alerts",  "Hourly 00–21, 23 · Mon–Fri"),
        "membership_refresh":   ("Membership Refresh", f"{_uk_sched_time(21, 15)} · Mon–Fri"),
        "job_dispatcher":       ("job_dispatcher","—"),
    }

    if _daemon_active:
        _aps_label, _mode_note, _aps_colour = "RUNNING", f"(daemon · heartbeat {int(_daemon_age_s)}s ago)", "#16a34a"
    elif _aps_running:
        _aps_label, _mode_note, _aps_colour = "RUNNING", "(in-process)", "#16a34a"
    else:
        _aps_label, _mode_note, _aps_colour = "STOPPED", "", "#dc2626"

    st.markdown(
        f'<div style="margin-bottom:10px">'
        f'<span style="font-size:0.82rem;color:#8899AA">Scheduler: </span>'
        f'<span style="font-size:0.82rem;font-weight:700;color:{_aps_colour}">{_aps_label}</span>'
        + (f'<span style="font-size:0.75rem;color:#8899AA;margin-left:6px">{html.escape(_mode_note)}</span>' if _mode_note else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    def _sched_row_html(jlabel: str, jsched: str, nrt_str: str, until_str: str, dot: str, badge: str) -> str:
        return (
            f'<div style="display:flex;padding:5px 0;border-bottom:1px solid #1E2D45;'
            f'align-items:center;font-size:0.78rem">'
            f'<div style="flex:0 0 22%;font-weight:600">{html.escape(jlabel)}</div>'
            f'<div style="flex:0 0 28%;color:#8899AA;font-size:0.73rem">{html.escape(jsched)}</div>'
            f'<div style="flex:0 0 25%">{html.escape(nrt_str)}</div>'
            f'<div style="flex:0 0 12%;color:#8899AA">{html.escape(until_str)}</div>'
            f'<div style="flex:0 0 13%;color:{dot};font-weight:700">{html.escape(badge)}</div>'
            f'</div>'
        )

    _sh_head = (
        '<div style="font-size:0.82rem">'
        '<div style="display:flex;padding:3px 0 5px;border-bottom:2px solid #1E2D45;'
        'color:#8899AA;font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em">'
        '<div style="flex:0 0 22%">Job</div>'
        '<div style="flex:0 0 28%">Schedule</div>'
        '<div style="flex:0 0 25%">Next Run</div>'
        '<div style="flex:0 0 12%">In</div>'
        '<div style="flex:0 0 13%">Status</div>'
        '</div>'
    )

    def _eod_overdue() -> bool:
        _ref_age = (
            (datetime.utcnow() - last_refresh.replace(tzinfo=None)).total_seconds() / 3600
            if last_refresh else float("inf")
        )
        _now_u = datetime.utcnow()
        _monday_before_eod = _now_u.weekday() == 0 and (_now_u.hour, _now_u.minute) < (21, 30)
        return _ref_age > 26 and _now_u.weekday() < 5 and not _monday_before_eod

    if _aps_running and _aps is not None:
        _aps_jobs   = _aps.get_jobs()
        _now_utc_tz = datetime.now(timezone.utc)
        _sh_rows: list[str] = []
        for _job in _aps_jobs:
            _jlabel, _jsched = _JOB_LABELS.get(_job.id, (_job.id, "—"))
            if _jlabel == "job_dispatcher":
                continue
            _nrt = _job.next_run_time
            if _nrt is None:
                _nrt_str, _until_str, _dot, _badge = "Paused", "—", "#94a3b8", "PAUSED"
            else:
                _d = (_nrt - _now_utc_tz).total_seconds()
                _nrt_str = _fmt_uk(_nrt, "%a %d %b %H:%M")
                _until_str = (
                    f"{int(_d / 60)}m"  if _d < 3600
                    else f"{_d / 3600:.1f}h" if _d < 86400
                    else f"{_d / 86400:.1f}d"
                )
                _dot, _badge = "#16a34a", "OK"
            if _job.id == "scheduled_eod" and _eod_overdue():
                _dot, _badge = "#dc2626", "OVERDUE"
            if _job.id == "membership_refresh" and is_membership_refresh_stale(last_membership_refresh):
                _dot, _badge = "#dc2626", "OVERDUE"
            _sh_rows.append(_sched_row_html(_jlabel, _jsched, _nrt_str, _until_str, _dot, _badge))
        st.markdown(_sh_head + "".join(_sh_rows) + "</div>", unsafe_allow_html=True)
    elif _daemon_active:
        from apscheduler.triggers.cron import CronTrigger as _CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger as _ItvTrigger
        _cfg_s  = _load_config()["scheduler"]
        _tz_s   = _cfg_s["timezone"]
        _now_tz = datetime.now(timezone.utc)

        _static_jobs: list[tuple[str, str, str, object]] = []
        for _rn, _rc in _cfg_s.get("runs", {}).items():
            _sid = f"scheduled_{_rn}"
            _jlabel, _jsched = _JOB_LABELS.get(_sid, (_sid, "—"))
            _trigger = _CronTrigger(
                hour=_rc["hour"], minute=_rc["minute"],
                day_of_week="mon-fri", timezone=_tz_s,
            )
            _static_jobs.append((_sid, _jlabel, _jsched, _trigger))

        # Monday pre-market is fixed at 07:00 UTC directly in
        # data/scheduler.py, not driven by scheduler.runs (see that
        # module's own comment on why).
        _extra_static = [
            ("monday_premarket", "Monday Pre-Market", f"{_uk_sched_time(7, 0)} · Monday",
             _CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=_tz_s)),
            ("trade_alert_check", "Trade Alerts", "Hourly 00–21, 23 · Mon–Fri",
             _CronTrigger(hour="0-21,23", minute=0, day_of_week="mon-fri", timezone=_tz_s)),
            ("membership_refresh", "Membership Refresh", f"{_uk_sched_time(21, 15)} · Mon–Fri",
             _CronTrigger(hour=21, minute=15, day_of_week="mon-fri", timezone=_tz_s)),
        ]
        _static_jobs.extend(_extra_static)

        _sh_rows_d: list[str] = []
        for _sid, _jlabel, _jsched, _trigger in _static_jobs:
            try:
                _nrt = _trigger.get_next_fire_time(None, _now_tz)
            except Exception:
                _nrt = None
            if _nrt is None:
                _nrt_str, _until_str = "—", "—"
            else:
                _d = (_nrt - _now_tz).total_seconds()
                _nrt_str = _fmt_uk(_nrt, "%a %d %b %H:%M")
                _until_str = (
                    f"{int(_d / 60)}m"  if _d < 3600
                    else f"{_d / 3600:.1f}h" if _d < 86400
                    else f"{_d / 86400:.1f}d"
                )
            _dot, _badge = "#16a34a", "OK"
            if _sid == "scheduled_eod" and _eod_overdue():
                _dot, _badge = "#dc2626", "OVERDUE"
            if _sid == "membership_refresh" and is_membership_refresh_stale(last_membership_refresh):
                _dot, _badge = "#dc2626", "OVERDUE"
            _sh_rows_d.append(_sched_row_html(_jlabel, _jsched, _nrt_str, _until_str, _dot, _badge))
        st.markdown(_sh_head + "".join(_sh_rows_d) + "</div>", unsafe_allow_html=True)
    else:
        st.warning(
            "Scheduler is not running — scheduled jobs will not fire until the service restarts. "
            "On the server, ensure investment-research-scheduler.service is enabled and running."
        )

    # ── Log Viewer ─────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Log Viewer")

    _lc1, _lc2, _lc3 = st.columns([3, 2, 1])
    with _lc1:
        _log_search = st.text_input(
            "Search logs", placeholder="Filter by keyword…",
            key="status_log_search", label_visibility="collapsed",
        )
    with _lc2:
        _log_filter = st.selectbox(
            "Category", ["All", "Errors Only", "Scheduler", "Data", "Orders"],
            key="status_log_filter", label_visibility="collapsed",
        )
    with _lc3:
        _log_auto_refresh = st.toggle("Auto-refresh", key="status_log_auto_refresh")

    try:
        _jctl_result = subprocess.run(
            ["journalctl", "-u", "investment-research", "--no-pager",
             "-n", "200", "--output=short-iso"],
            capture_output=True, text=True, timeout=10,
        )
        _raw_log_lines = (
            _jctl_result.stdout.strip().splitlines()
            if _jctl_result.returncode == 0 else []
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _raw_log_lines = []

    if not _raw_log_lines:
        st.info(
            "Log output not available on this machine. "
            "On the server, view logs with:  `journalctl -u investment-research -f`"
        )
    else:
        _FILTER_KEYWORDS: dict[str, frozenset[str]] = {
            "Errors Only": frozenset(),
            "Scheduler":   frozenset({"scheduler", "pre-market", "us-open", "eod",
                                      "order pipeline", "pipeline complete",
                                      "trade alert", "membership", "scheduled_", "catch-up",
                                      "missed eod"}),
            "Data":        frozenset({"pull_incremental", "yfinance", "new rows",
                                      "price data", "data pull", "incremental",
                                      "ingestion", "rows across"}),
            "Orders":      frozenset({"stop loss", "crisis", "index removal",
                                      "signal off", "max holding", "buy", "sell",
                                      "order", "pnl", "position", "price seed",
                                      "no viable price", "locked out"}),
        }
        _GREEN_KEYS = frozenset({
            "complete", "success", "opened", "closed at", "started", "sent",
            "pipeline complete", "new rows",
        })

        _parsed: list[dict] = []
        for _rl in _raw_log_lines:
            _p = _parse_journal_line(_rl)
            if _p:
                _parsed.append(_p)

        _filtered: list[dict] = []
        _search_lower = _log_search.lower() if _log_search else ""
        for _p in _parsed:
            _lvl = _p["level"]
            _msg_lower = _p["message"].lower()
            if _log_filter == "Errors Only":
                if _lvl not in ("ERROR", "CRITICAL", "WARNING"):
                    continue
            elif _log_filter in _FILTER_KEYWORDS:
                if not any(k in _msg_lower for k in _FILTER_KEYWORDS[_log_filter]):
                    continue
            if _search_lower and _search_lower not in _p["raw"].lower():
                continue
            _filtered.append(_p)

        _display_lines = _filtered[::-1]

        _line_htmls: list[str] = []
        for _p in _display_lines:
            _lvl = _p["level"]
            _msg = _p["message"]
            if _lvl in ("ERROR", "CRITICAL"):
                _mc, _lc_col = "#ef4444", "#ef4444"
            elif _lvl == "WARNING":
                _mc, _lc_col = "#f97316", "#f97316"
            elif any(k in _msg.lower() for k in _GREEN_KEYS):
                _mc, _lc_col = "#22c55e", "#22c55e"
            else:
                _mc, _lc_col = "#cbd5e1", "#94a3b8"
            _line_htmls.append(
                f'<div style="line-height:1.5;padding:1px 0">'
                f'<span style="color:#8899AA">{html.escape(_p["ts"])}</span>'
                f'<span style="color:{_lc_col};font-weight:600;margin-left:6px">'
                f'{html.escape(_lvl[:4])}</span>'
                f'<span style="color:{_mc};margin-left:6px;word-break:break-all">'
                f'{html.escape(_msg)}</span>'
                f'</div>'
            )

        _count_note = (
            f"{len(_filtered)} lines shown (newest first)"
            if _log_filter == "All" and not _log_search
            else f"{len(_filtered)} matching lines"
        )
        st.caption(_count_note)

        if _line_htmls:
            st.markdown(
                '<div style="background:#0f172a;border-radius:6px;padding:10px 14px;'
                'height:420px;overflow-y:auto;font-family:monospace;font-size:0.72rem">'
                + "".join(_line_htmls)
                + "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("No log lines match the current filter / search.")

        _dl1, _dl2, _dl3, _dl4 = st.columns([1, 1, 1, 3])
        with _dl1:
            st.download_button(
                "⬇ Current view",
                data="\n".join(_p["raw"] for _p in _filtered),
                file_name="investment-research-log.txt",
                mime="text/plain",
                key="log_dl_current",
            )
        with _dl2:
            try:
                _dl_24h = subprocess.run(
                    ["journalctl", "-u", "investment-research", "--no-pager",
                     "--since", "24 hours ago", "--output=short-iso"],
                    capture_output=True, text=True, timeout=15,
                ).stdout
            except Exception:
                _dl_24h = ""
            st.download_button(
                "⬇ Last 24 h",
                data=_dl_24h or "(not available)",
                file_name="investment-research-log-24h.txt",
                mime="text/plain",
                key="log_dl_24h",
            )
        with _dl3:
            try:
                _dl_7d = subprocess.run(
                    ["journalctl", "-u", "investment-research", "--no-pager",
                     "--since", "7 days ago", "--output=short-iso"],
                    capture_output=True, text=True, timeout=30,
                ).stdout
            except Exception:
                _dl_7d = ""
            st.download_button(
                "⬇ Last 7 days",
                data=_dl_7d or "(not available)",
                file_name="investment-research-log-7d.txt",
                mime="text/plain",
                key="log_dl_7d",
            )

    _render_footer()

    if _log_auto_refresh:
        time.sleep(30)
        st.rerun()


# ---------------------------------------------------------------------------
# Performance page
# ---------------------------------------------------------------------------

def _asset_type_map(config: dict) -> dict[str, str]:
    """Map research ticker -> 'ETF' or 'STOCK'.

    Sourced from every VERIFIED T212 instrument resolution now (2026-08-02
    -- config.yaml's `instruments:` section is empty, see CLAUDE.md's
    "wire the momentum-only, full-S&P-500 selection into live execution"
    section) rather than a static config dict. Every resolved ticker is a
    stock in practice today -- the ETF sleeve was removed from live
    trading entirely -- but this reads genuinely per-ticker rather than
    assuming that, in case a future re-addition ever changes it."""
    from brokers.trading212 import load_all_instrument_resolutions
    asset_types: dict[str, str] = {}
    for r in load_all_instrument_resolutions():
        if r["status"] != "VERIFIED":
            continue
        asset_types[r["ticker"]] = "STOCK"
    return asset_types


def _block_pass_style(val) -> str:
    return "color:#16a34a;font-weight:700" if val == "PASS" else "color:#dc2626;font-weight:700"


def _render_backtest_page(config: dict) -> None:
    """Trigger and view the full validated momentum + crisis-override
    battery: train (2007-2019) / test (2020-2026) CAGR, 16 historical
    blocks each checked against the real S&P 500 over the same window, a
    concentration check (results excluding the single best trade), and
    transaction-cost sensitivity. See analysis/validation.py for the exact
    methodology and CLAUDE.md's "Live pricing bug, exception isolation, and
    ETF mapping audit" / "V2 architecture" sections for validation history.
    """
    from analysis.validation import universe_snapshot

    _render_page_wordmark("Backtest")
    st.markdown("## Backtest")
    st.caption(
        "12-month momentum signal, point-in-time-gated universe, and the "
        "≥7% single-day crisis override — the same engine that drives live "
        "order placement (analysis/backtest.py), run over its full validated "
        "2007-present history."
    )

    snap = universe_snapshot()
    uc1, uc2, uc3 = st.columns(3)
    uc1.metric("Candidate Universe", f"{snap['total_candidates']}",
               f"{snap['etf_count']} ETFs + {snap['stock_candidate_count']} stocks")
    uc2.metric("Live-Eligible Today", f"{snap['live_tradeable_count']}",
               f"{snap['stock_member_count']}/{snap['stock_candidate_count']} stocks currently S&P members")
    uc3.metric("Excluded Today", str(len(snap["non_members"])),
               ", ".join(snap["non_members"]) or "none")

    st.divider()

    if st.button("Run Full Validated Battery (2007–present)", type="primary", key="run_full_validation"):
        with st.spinner("Running the full battery across all tracked tickers — this can take a minute or two…"):
            try:
                from analysis.validation import run_full_validation
                result = run_full_validation(config)
                st.session_state["_validation_result"] = result
                st.session_state["_validation_result_ts"] = datetime.utcnow().isoformat()
            except Exception as exc:
                st.error(f"Validation run failed: {exc}")

    result = st.session_state.get("_validation_result")
    if result is None:
        st.info(
            "Click the button above to run the battery. Reads live price history only — no writes. "
            "This also unlocks a downloadable due diligence PDF report, generated from this same run."
        )
        _render_footer()
        return

    _ts = st.session_state.get("_validation_result_ts")
    if _ts:
        st.caption(f"Last run: {_fmt_uk(datetime.fromisoformat(_ts))} · "
                   f"{result['start']} → {result['end']} · "
                   f"{result['n_tickers_with_data']}/{result['n_tickers_total']} tickers had usable data")

    st.markdown("### Download Due Diligence Report")
    if st.button("Generate Due Diligence Report (PDF)", key="gen_dd_report"):
        with st.spinner("Generating due diligence report…"):
            try:
                from analysis.dd_report import generate_dd_report_pdf
                pdf_bytes = generate_dd_report_pdf(result, config)
                st.session_state["_dd_report_pdf"] = pdf_bytes
            except Exception as exc:
                st.error(f"Report generation failed: {exc}")
    if st.session_state.get("_dd_report_pdf"):
        st.download_button(
            "📄 Download Due Diligence Report (PDF)",
            data=st.session_state["_dd_report_pdf"],
            file_name="sp500invest_due_diligence_report.pdf",
            mime="application/pdf",
            key="dl_dd_report",
        )

    st.divider()
    st.markdown("### Train / Test Split vs S&P 500")
    tc1, tc2 = st.columns(2)
    for col, label, m, sp in (
        (tc1, "TRAIN (2007–2019)", result["train"], result.get("train_sp", {})),
        (tc2, "TEST (2020–2026)", result["test"], result.get("test_sp", {})),
    ):
        with col:
            st.markdown(f"**{label}**")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Trades", m.get("total_trades", 0))
            r2.metric("CAGR", f"{m.get('cagr_pct', 0):+.2f}%",
                      f"vs S&P {sp.get('cagr_pct', 0):+.2f}%" if sp.get("cagr_pct") is not None else None)
            r3.metric("Max DD", f"{m.get('max_drawdown_pct', 0):.2f}%",
                      f"vs S&P {sp.get('max_drawdown_pct', 0):.2f}%" if sp.get("max_drawdown_pct") is not None else None)
            r4.metric("Win Rate", f"{m.get('win_rate', 0) * 100:.1f}%")

    gg = result.get("gbp_growth", {})
    if gg:
        st.markdown("**£ Invested — Real Ending Balance**")
        st.caption(
            "Additive-£ trajectory (equity_curve_gbp()) vs. buy-and-hold S&P 500 (price return, "
            "no dividend reinvestment — the same ^GSPC series behind the CAGR figures above), train "
            "and test kept separate rather than blended into one full-period number."
        )
        cap = gg.get("starting_capital_gbp", 0)
        gc1, gc2 = st.columns(2)
        for col, label, key in ((gc1, "TRAIN (2007–2019)", "train"), (gc2, "TEST (2020–2026)", "test")):
            with col:
                st.markdown(f"**{label}**")
                leg = gg.get(key, {})
                g1, g2 = st.columns(2)
                g1.metric("Strategy", f"£{leg.get('strategy_ending_gbp', cap):,.0f}",
                          f"from £{cap:,.0f}")
                g2.metric("S&P 500 (buy & hold)", f"£{leg.get('sp500_ending_gbp', cap):,.0f}",
                          f"from £{cap:,.0f}")

    st.divider()
    st.markdown("### Full History (2007–present) & Concentration Check")
    fc1, fc2 = st.columns(2)
    with fc1:
        st.markdown("**All trades**")
        raw = result["full_raw"]
        st.caption(
            f"{raw.get('total_trades', 0)} trades · CAGR {raw.get('cagr_pct', 0):+.2f}% · "
            f"Max DD {raw.get('max_drawdown_pct', 0):.2f}% · Win rate {raw.get('win_rate', 0) * 100:.1f}% · "
            f"Profit factor {raw.get('profit_factor', 0):.2f}"
        )
    with fc2:
        st.markdown("**Excluding the single best trade** (concentration check)")
        exb = result["full_ex_best"]
        st.caption(
            f"{exb.get('total_trades', 0)} trades · CAGR {exb.get('cagr_pct', 0):+.2f}% · "
            f"Max DD {exb.get('max_drawdown_pct', 0):.2f}%"
        )
        _delta = raw.get("cagr_pct", 0) - exb.get("cagr_pct", 0)
        st.caption(
            f"CAGR drops {_delta:.2f}pp with the single best trade removed — "
            + ("a broad, non-concentrated edge." if _delta < 2 else "some concentration in a handful of trades.")
        )

    st.divider()
    st.markdown("### 16 Historical Blocks vs S&P 500")
    st.caption(
        f"**{result['n_blocks_pass']}/{result['n_blocks_total']} blocks pass** — strategy CAGR ≥ S&P 500's own "
        "over the same window, max drawdown no worse than 1.5× the S&P's own, and no block loses more than "
        "20% of starting capital."
    )
    blocks_df = pd.DataFrame(result["blocks"])
    blocks_df["passed"] = blocks_df["passed"].map(lambda b: "PASS" if b else "FAIL")
    blocks_show = blocks_df[["block", "start", "end", "n_trades", "strat_cagr", "strat_dd", "sp_cagr", "sp_dd", "passed"]].copy()
    blocks_show.columns = ["Block", "Start", "End", "Trades", "Strategy CAGR %", "Strategy Max DD %", "S&P CAGR %", "S&P Max DD %", "Result"]
    st.dataframe(
        blocks_show.style.map(_block_pass_style, subset=["Result"]),
        width="stretch", hide_index=True,
    )

    st.divider()
    st.markdown("### Transaction-Cost Sensitivity")
    st.caption("Round-trip cost (entry + exit) subtracted from every trade's return, full 2007–present history.")
    cost_rows = []
    for bps, m in sorted(result["cost_sensitivity"].items()):
        cost_rows.append({
            "Cost (bps, round-trip)": bps, "CAGR %": m.get("cagr_pct", 0),
            "Max DD %": m.get("max_drawdown_pct", 0), "Win Rate %": round(m.get("win_rate", 0) * 100, 1),
            "Profit Factor": m.get("profit_factor", 0),
        })
    st.dataframe(pd.DataFrame(cost_rows), width="stretch", hide_index=True)

    st.divider()
    st.markdown("### Walk-Forward (6-month rolling windows)")
    wf = result["walk_forward"]
    wc1, wc2, wc3 = st.columns(3)
    wc1.metric("Windows Tested", wf.get("n_total", 0))
    wc2.metric("Profitable Windows", wf.get("n_positive", 0))
    wc3.metric("Success Rate", f"{wf.get('success_rate_pct', 0):.1f}%")

    st.divider()
    st.markdown("### Equity Curve (£, full history)")
    _ec_df = pd.DataFrame(result["trades"])
    if not _ec_df.empty:
        _ec = equity_curve_gbp(_ec_df, starting_capital_gbp=result["starting_capital_gbp"])
        if not _ec.empty:
            _fig = go.Figure()
            _fig.add_trace(go.Scatter(
                x=_ec["date"], y=_ec["equity_gbp"], mode="lines", name="Portfolio (£)",
                line=dict(color="#3b82f6", width=2),
                fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                hovertemplate="%{x|%Y-%m-%d}<br>Equity: £%{y:,.0f}<extra></extra>",
            ))
            _fig.update_layout(
                xaxis_title="Date", yaxis_title="Portfolio Value (£)",
                height=360, margin=dict(l=0, r=0, t=20, b=0),
            )
            _apply_dark_theme(_fig)
            st.plotly_chart(_fig, width="stretch")

    _render_footer()


def _render_performance_page(config: dict) -> None:
    """Execution performance driven entirely by real T212 order fills — see
    "T212 P&L reconstruction" in CLAUDE.md. No simulated/paper trades."""
    _render_page_wordmark("Performance")
    st.title("Performance")

    t212_enabled = config.get("trading212_invest", {}).get("enabled", False)
    st.markdown(
        (
            '<span style="background:#16a34a;color:#fff;padding:2px 9px;border-radius:4px;'
            'font-size:0.78rem;font-weight:700">🟢 LIVE (DEMO)</span>'
            if t212_enabled else
            '<span style="background:#8899AA;color:#fff;padding:2px 9px;border-radius:4px;'
            'font-size:0.78rem;font-weight:700">NOT ENABLED</span>'
        ),
        unsafe_allow_html=True,
    )

    st.divider()
    st.markdown("### Open Positions")
    open_cards = _get_open_cards(config)
    st.markdown(f"**{positions_summary_text(len(open_cards))}**")
    if open_cards:
        st.caption(POSITIONS_RULES_EXPLANATION)
    if not open_cards:
        st.info("No open positions.")
    else:
        # Stable, neutral order — alphabetical by ticker, not by status/
        # proximity to a threshold (see the Daily Briefing's identical
        # ordering choice above).
        all_cards = sorted(open_cards, key=lambda x: x.get("ticker", ""))
        for e in all_cards:
            _render_trade_card(e)

    st.divider()
    st.markdown("### Closed Positions")
    pnl_rows = _get_closed_rows()

    if not pnl_rows:
        st.info("No closed positions yet — data will appear as positions close.")
    else:
        show_df = pd.DataFrame(pnl_rows)
        show_df["Type"] = show_df["is_etf"].map(lambda b: "ETF" if b else "STOCK")
        show_df["entry_date"] = pd.to_datetime(show_df["entry_date"]).dt.strftime("%Y-%m-%d")
        show_df["exit_date"]  = pd.to_datetime(show_df["exit_date"]).dt.strftime("%Y-%m-%d")
        show_df["pnl_pct"]    = (show_df["pnl_pct"] * 100).round(2)
        show_df["pnl_gbp"]    = show_df["pnl_gbp"].round(2)
        show_df["momentum_pct_at_entry"] = (show_df["momentum_pct_at_entry"] * 100).round(1)

        show_df = show_df[[
            "ticker", "Type", "entry_date", "exit_date", "entry_price", "exit_price",
            "quantity", "currency", "pnl_gbp", "pnl_pct", "holding_days",
            "momentum_pct_at_entry", "exit_reason",
        ]]
        show_df.columns = [
            "Ticker", "Type", "Entry Date", "Exit Date", "Entry Price", "Exit Price",
            "Qty", "Ccy", "P&L £", "P&L %", "Hold Days",
            "Momentum at Entry %", "Exit Reason",
        ]
        st.dataframe(
            show_df.style
                .map(lambda v: f"color:{'#16a34a' if v >= 0 else '#dc2626'}", subset=["P&L £"])
                .map(lambda v: f"color:{'#16a34a' if v >= 0 else '#dc2626'}", subset=["P&L %"]),
            width="stretch", hide_index=True,
        )

    st.divider()
    st.markdown("### Performance Metrics")
    n_closed = len(pnl_rows)

    if n_closed < 5:
        st.caption(f"Statistical metrics unlock at 5 closed positions (currently {n_closed}/5).")
        if n_closed:
            wins = sum(1 for r in pnl_rows if (r.get("pnl_gbp") or 0) > 0)
            losses = n_closed - wins
            total_pnl = sum(r.get("pnl_gbp") or 0 for r in pnl_rows)
            st.caption(
                f"So far: {wins} win{'s' if wins != 1 else ''} / "
                f"{losses} loss{'es' if losses != 1 else ''} · "
                f"total realised P&L £{total_pnl:+,.2f}."
            )
    else:
        mdf  = pnl_metrics_df(pnl_rows)
        mets = compute_metrics(mdf)

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Closed Positions", mets.get("total_trades", 0))
        wr_pct = round((mets.get("win_rate") or 0) * 100, 1)
        c2.metric("Win Rate", f"{wr_pct}%")
        c3.metric("Avg Return", f"{mets.get('avg_return_pct', 0):+.2f}%")
        c4.metric("Total Return", f"{mets.get('total_return_pct', 0):+.1f}%")
        c5.metric("Profit Factor", f"{mets.get('profit_factor', 0):.2f}")
        c6.metric("Max Drawdown", f"{mets.get('max_drawdown_pct', 0):.1f}%")

        total_pnl_gbp = sum(r.get("pnl_gbp") or 0 for r in pnl_rows)
        st.caption(
            f"Total realised P&L: £{total_pnl_gbp:+,.2f} across {n_closed} closed positions · "
            f"avg hold {mets.get('avg_holding_days', 0):.0f} days."
        )

        starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
        ec = equity_curve_gbp(mdf, starting_capital_gbp=starting_capital)
        if not ec.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=ec["date"], y=ec["equity_gbp"],
                mode="lines", name="Portfolio (£)",
                line=dict(color="#3b82f6", width=2, shape="linear"),
                fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                hovertemplate="%{x|%Y-%m-%d}<br>Equity: £%{y:,.2f}<extra></extra>",
            ))
            fig.update_layout(
                title="Equity Curve (£, daily)",
                xaxis_title="Date", yaxis_title="Portfolio Value (£)",
                height=320, margin=dict(l=0, r=0, t=40, b=0),
                xaxis=dict(type="date"),
            )
            _apply_dark_theme(fig)
            st.plotly_chart(fig, width="stretch")

        exit_df = mdf.groupby("exit_reason").agg(
            trades=("pnl_pct", "count"),
            win_rate=("is_win", "mean"),
            avg_return=("pnl_pct", "mean"),
            avg_hold=("holding_days", "mean"),
        )
        if not exit_df.empty:
            st.markdown("#### Performance by Exit Reason")
            exit_show = exit_df.copy()
            exit_show["win_rate"] = (exit_show["win_rate"] * 100).round(1)
            exit_show["avg_return"] = (exit_show["avg_return"] * 100).round(2)
            exit_show["avg_hold"] = exit_show["avg_hold"].round(1)
            exit_show.columns = ["Trades", "Win Rate %", "Avg Return %", "Avg Hold Days"]
            st.dataframe(exit_show, width="stretch")

    _render_footer()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="SP500Invest",
        page_icon="Z",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _initialise()

    config = _load_config()
    watchlist = _build_watchlist(config)

    # ---- Sidebar -------------------------------------------------------
    with st.sidebar:
        st.sidebar.markdown("""
            <div style="padding: 0.5rem 0 1rem 0;">
                <div style="font-size: 1.4rem; font-weight: 800; letter-spacing: 0.12em; color: #00C2FF;">
                    SP500INVEST
                </div>
                <div style="font-size: 0.7rem; color: #8899AA; letter-spacing: 0.05em; margin-top: 0.1rem;">
                    Systematic large-cap momentum research.
                </div>
            </div>
        """, unsafe_allow_html=True)
        st.divider()

        _PAGES = ["Daily Briefing", "Charts", "Performance", "Backtest", "Status"]
        _PAGE_KEYS = {
            "Daily Briefing": "briefing",
            "Charts":         "charts",
            "Performance":    "performance",
            "Backtest":       "backtest",
            "Status":         "status",
        }
        current_key = st.session_state.get("page", "briefing")
        default_idx = next(
            (i for i, k in enumerate(_PAGE_KEYS.values()) if k == current_key), 0
        )
        page = st.radio(
            "Navigation",
            options=_PAGES,
            index=default_idx,
            label_visibility="collapsed",
        )
        current_page = _PAGE_KEYS[page]
        if st.session_state.get("page") != current_page:
            st.session_state["page"] = current_page
            st.rerun()

        st.divider()

        _sb_refresh = get_metadata("last_full_refresh")
        _sb_sched   = get_metadata("last_scheduled_run")
        _sb_r_str = _fmt_uk(_sb_refresh, "%Y-%m-%d %H:%M") if _sb_refresh else "Pending"
        _sb_s_str = _fmt_uk(_sb_sched,   "%Y-%m-%d %H:%M") if _sb_sched   else "Pending"
        st.caption(f"**Last refresh:** {_sb_r_str}")
        st.caption(f"**Last scheduled run:** {_sb_s_str}")

    # ---- Main content --------------------------------------------------
    page_key = st.session_state.get("page", "briefing")

    if page_key == "briefing":
        _render_briefing_page(config)
        return

    if page_key == "status":
        _render_status_page(config, watchlist)
        return

    if page_key == "performance":
        _render_performance_page(config)
        return

    if page_key == "backtest":
        _render_backtest_page(config)
        return

    # Charts page
    selected = st.session_state.get("selected_ticker")

    if selected is None:
        _render_charts_landing(config)
        return

    name = watchlist.get(selected, selected)

    if st.button("← All Assets", key="charts_back"):
        st.session_state["selected_ticker"] = None
        st.rerun()

    _render_page_wordmark("Charts")
    st.markdown(f"## {name}")
    st.caption(f"Ticker: **{selected}**")

    _RANGE_DAYS = {"1Y": 365, "3Y": 1095, "5Y": 1826, "10Y": 3652}
    if st.session_state.get("_chart_range_ticker") != selected:
        st.session_state["chart_range"] = "1Y"
        st.session_state["_chart_range_ticker"] = selected

    chart_range = st.radio(
        "Range",
        list(_RANGE_DAYS.keys()),
        index=list(_RANGE_DAYS.keys()).index(st.session_state.get("chart_range", "1Y")),
        horizontal=True,
        label_visibility="collapsed",
        key="chart_range",
    )

    df = get_price_df(selected, days=_RANGE_DAYS[chart_range])

    if df.empty:
        st.warning(
            f"No price data found for **{selected}**. "
            f"Data is refreshed automatically at {_uk_sched_time(21,30)} — use Manual Controls on the Status page to trigger an immediate pull."
        )
        _render_footer()
        return

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    daily_chg = (latest["close"] - prev["close"]) / prev["close"] * 100
    this_year = latest["date"].year
    ytd_rows = df[df["date"].dt.year == this_year]
    ytd_chg = (latest["close"] - ytd_rows.iloc[0]["close"]) / ytd_rows.iloc[0]["close"] * 100 if len(ytd_rows) > 1 else 0.0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Last Close", f"{latest['close']:,.2f}")
    col2.metric("Daily Change", f"{daily_chg:+.2f}%", delta=f"{daily_chg:+.2f}%")
    col3.metric("YTD Return", f"{ytd_chg:+.2f}%", delta=f"{ytd_chg:+.2f}%")
    col4.metric("Volume", f"{latest['volume']:,.0f}")

    st.plotly_chart(_candlestick_chart(df, selected, name), width="stretch")
    st.plotly_chart(_volume_bar_chart(df), width="stretch")

    with st.expander("Raw OHLCV data"):
        display_df = df.copy()
        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            display_df.sort_values("date", ascending=False).reset_index(drop=True),
            width="stretch",
        )

    _render_footer()


if __name__ == "__main__":
    main()
