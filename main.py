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
from data.ingestion import get_all_tickers, get_all_watchlist_entries, pull_incremental
from data.scheduler import start_scheduler
from output.performance import (
    POSITIONS_RULES_EXPLANATION,
    compute_position_context,
    get_account_value_history,
    get_brief_cards,
    get_crisis_status,
    get_current_account_summary,
    get_pending_candidates,
    get_pnl_rows,
    get_recent_orders,
    get_ticker_status,
    get_top_trades,
    get_unconfirmed_exits,
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
    """Return an ordered dict of {ticker: display_name} for every tracked
    asset (reference_tickers + the dynamic live candidate pool)."""
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


def _nyse_trading_days_since(ts: datetime | None) -> int:
    """Count genuine NYSE trading sessions strictly after *ts*'s date,
    through today — a calendar-aware alternative to a raw elapsed-hours
    count, for any status widget gated on a daily (or better) job cadence.

    A plain hours count misfires over an ordinary weekend: Friday 21:30 UTC
    to Monday is a real 63+ hour gap, but only ONE genuine NYSE trading day
    has actually passed, and must not read as several days stale (confirmed
    2026-08-03 — the "Last Data Refresh"/"Price Data Refresh" widgets were
    doing exactly this with a flat 48h/96h threshold, while the "Data
    Coverage" section on the same page already correctly used a genuine
    NYSE-trading-day count via _price_coverage()'s own per-ticker logic —
    an inconsistency between two widgets on the same page describing the
    same underlying concept two different ways).

    Deliberately NYSE-only, not routed through _price_coverage()'s own
    per-exchange `_MCAL_MAP` — this is used for whole-database timestamps
    (`last_full_refresh`, `last_scheduled_run`), not a specific ticker's own
    exchange, and NYSE is the only calendar this system's live decisions
    depend on since the ETF sleeve's removal (see CLAUDE.md's "ETF sleeve
    removed from live trading" section). Returns a large sentinel (999)
    when *ts* is None or the calendar lookup fails, so "never happened"/
    "couldn't determine" always sorts as maximally stale rather than
    silently reading as fresh.
    """
    if ts is None:
        return 999
    _ts = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
    last_date = _ts.date() if hasattr(_ts, "date") else _ts
    today = datetime.utcnow().date()
    lookback = (datetime.utcnow() - timedelta(days=30)).date()
    try:
        import pandas_market_calendars as mcal
        cal = mcal.get_calendar("NYSE")
        sched = cal.schedule(
            start_date=lookback.strftime("%Y-%m-%d"), end_date=today.strftime("%Y-%m-%d"),
        )
        sessions = [pd.Timestamp(d).date() for d in sched.index]
    except Exception:
        return 999
    return sum(1 for d in sessions if d > last_date)


def _trading_day_age_level(ts: datetime | None, warn_td: int = 3, error_td: int = 6) -> str:
    """NYSE-trading-day-aware counterpart to _age_level() — see
    _nyse_trading_days_since()'s own docstring for why a plain elapsed-hours
    count is wrong for a daily (or better) job. warn_td/error_td default to
    the same "Fresh (≤3 trading days)" convention already used for
    per-ticker price staleness in _price_coverage()'s Data Coverage section,
    so the whole Status page agrees with itself on what "stale" means.
    """
    if ts is None:
        return "error"
    td = _nyse_trading_days_since(ts)
    if td <= warn_td:
        return "ok"
    if td <= error_td:
        return "warn"
    return "error"


def _next_run_for_time(hour: int, minute: int) -> datetime:
    """Return the next weekday datetime when the job at *hour*:*minute* UTC fires."""
    now = datetime.utcnow()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:   # skip Sat (5) and Sun (6)
        candidate += timedelta(days=1)
    return candidate


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
# Instruments page (2026-08-02, replaces Charts) — a filterable list of
# every currently-tracked instrument, sourced from the same live, dynamic
# candidate pool everything else now reads from
# (data.ingestion.get_all_watchlist_entries(), which merges
# reference_tickers with analysis.universe.get_live_stock_candidates()).
# The old Charts page (grouped landing cards + a per-ticker candlestick
# detail view) only ever showed the three monitoring-only reference
# tickers as its own card group -- not useful now that the live candidate
# pool is the full, dynamic ~503-ticker S&P 500, and rendering ~503
# individual candlestick-capable cards would be a much larger, differently-
# scoped UI than this page's actual job (a searchable reference list, with
# Yahoo Finance doing the charting). Removed entirely, not left dormant.
# ---------------------------------------------------------------------------

def _render_instruments_page(config: dict) -> None:
    """Searchable table of every tracked instrument -- ticker, name, T212
    instrument-resolution status, and a direct link to its Yahoo Finance
    quote page. No in-app price chart -- Yahoo Finance's own quote page
    already does that; this page's job is "what's tracked and where do I
    look it up," not charting.

    The "Name" column reflects whatever name source already exists in
    get_all_watchlist_entries() -- a real display name for reference
    tickers (e.g. ^GSPC), but currently just the ticker symbol itself for
    the ~503 dynamic S&P 500 candidates, since this codebase has no
    company-name lookup/store for that pool. Stated here rather than
    silently presented as a real company-name lookup.

    Resolution Status/T212 Instrument/Currency/Note moved here 2026-08-03
    from a disconnected "Instrument Mapping" expander on the Status page
    (see CLAUDE.md's Status-page-restructuring section) -- a ticker's T212
    resolution is exactly the kind of "other detail" that belongs alongside
    it here, on the one page already organised per-instrument, rather than
    living on a separate page keyed by nothing but "system status." A
    ticker with no resolution row (every reference ticker; a live S&P 500
    candidate not yet evaluated) shows "—", not a fabricated status.
    """
    from urllib.parse import quote as _url_quote
    from brokers.trading212 import load_all_instrument_resolutions

    _render_page_wordmark("Instruments")
    st.markdown("## Instruments")
    st.caption(
        "Every instrument this system currently tracks — sourced live from the same dynamic "
        "S&P 500 candidate pool (`get_live_stock_candidates()`) live order placement itself reads, "
        "plus the core index/FX reference tickers. Click through to Yahoo Finance for a price chart."
    )

    resolutions_by_ticker = {r["ticker"]: r for r in load_all_instrument_resolutions()}

    entries = get_all_watchlist_entries(config)
    rows = sorted(
        (
            {
                "Ticker": e["ticker"],
                "Name": e.get("name", e["ticker"]),
                "Resolution Status": resolutions_by_ticker.get(e["ticker"], {}).get("status", "—"),
                "T212 Instrument": resolutions_by_ticker.get(e["ticker"], {}).get("t212_instrument_id") or "—",
                "Currency": resolutions_by_ticker.get(e["ticker"], {}).get("currency") or "—",
                "Note": resolutions_by_ticker.get(e["ticker"], {}).get("note") or "—",
                "Yahoo Finance": f"https://finance.yahoo.com/quote/{_url_quote(e['ticker'], safe='')}",
            }
            for e in entries
        ),
        key=lambda r: r["Ticker"],
    )

    search = st.text_input(
        "Search by ticker or name", value="", placeholder="e.g. AAPL, S&P 500, momentum…",
    )
    if search.strip():
        _q = search.strip().lower()
        rows = [r for r in rows if _q in r["Ticker"].lower() or _q in r["Name"].lower()]

    n_verified = sum(1 for r in rows if r["Resolution Status"] == "VERIFIED")
    n_failed = sum(1 for r in rows if r["Resolution Status"] == "FAILED")
    n_pending = sum(1 for r in rows if r["Resolution Status"] == "PENDING")
    _res_caption = f"{n_verified} verified"
    if n_failed:
        _res_caption += f" · {n_failed} failed — needs review"
    if n_pending:
        _res_caption += f" · {n_pending} pending retry"
    st.caption(f"{len(rows)} of {len(entries)} instruments shown · {_res_caption}")

    _cols = ["Ticker", "Name", "Resolution Status", "T212 Instrument", "Currency", "Note", "Yahoo Finance"]
    st.dataframe(
        pd.DataFrame(rows, columns=_cols).style.map(
            lambda v: "color:#16a34a" if v == "VERIFIED"
            else ("color:#dc2626" if v == "FAILED"
            else ("color:#d97706" if v == "PENDING" else "")),
            subset=["Resolution Status"],
        ),
        width="stretch",
        hide_index=True,
        column_config={
            "Yahoo Finance": st.column_config.LinkColumn(display_text="View on Yahoo Finance →"),
        },
    )

    _render_footer()


# ---------------------------------------------------------------------------
# Dashboard page (2026-08-02, replaces Daily Briefing)
# ---------------------------------------------------------------------------

_VALUE_CHART_RANGES = {"Week": 7, "Month": 30, "Year": 365}


@st.cache_data(ttl=60)
def _get_account_summary() -> dict:
    """Thin cached wrapper around output/performance.py:get_current_account_summary()."""
    return get_current_account_summary()


@st.cache_data(ttl=300)
def _get_value_history(days: int) -> list[dict]:
    """Thin cached wrapper around output/performance.py:get_account_value_history()."""
    return get_account_value_history(days)


@st.cache_data(ttl=60)
def _get_top_trades_cached() -> tuple[list[dict], list[dict]]:
    """Thin cached wrapper around output/performance.py:get_top_trades()."""
    return get_top_trades(limit=5)


@st.cache_data(ttl=60)
def _get_pending_candidates_cached() -> list[dict]:
    """Thin cached wrapper around output/performance.py:get_pending_candidates()."""
    return get_pending_candidates()


@st.cache_data(ttl=60)
def _get_projected_admission_cached(_config: dict) -> list[dict]:
    """Thin cached wrapper around brokers/trading212.py:
    project_pending_order_admission() (2026-08-02). Leading underscore on
    the parameter name is the Streamlit convention for "don't try to hash
    this argument" — config is a plain dict, not needed as a cache key
    here since there's only ever one config in play per page render."""
    from brokers.trading212 import project_pending_order_admission
    return project_pending_order_admission(_config)


@st.cache_data(ttl=60)
def _get_unconfirmed_exits_cached() -> list[dict]:
    """Thin cached wrapper around output/performance.py:get_unconfirmed_exits()."""
    return get_unconfirmed_exits()


@st.cache_data(ttl=60)
def _get_recent_orders_cached(limit: int = 10) -> list[dict]:
    """Thin cached wrapper around output/performance.py:get_recent_orders()."""
    return get_recent_orders(limit)


def _compute_momentum_strength_bands(momentum_values: pd.Series) -> dict:
    """Relative, percentile-based momentum-strength band boundaries,
    computed fresh from the CURRENT live candidate pool's own momentum
    distribution every render — not fixed thresholds (2026-08-02).

    Deliberate design choice, stated explicitly rather than assumed:
    RELATIVE bands, recalculated against today's own pool each time, not
    absolute fixed percentages. Reasoning — the same "don't hardcode a
    threshold that goes stale as conditions shift" trap this codebase has
    already been burned by for max-holding-days and the old flat position
    caps (see CLAUDE.md), just applied to a display label instead of a
    trading parameter: the whole candidate pool's momentum distribution
    moves with the market regime (a raging bull run lifts every
    candidate's momentum; a flat/choppy market compresses it), so a fixed
    absolute cutoff would either mislabel most candidates most of the
    time or need periodic manual re-tuning to stay meaningful. A relative
    percentile split adapts automatically and always reflects real,
    current conditions. The trade-off, stated honestly: labels are not
    numerically stable day to day — "Strong" today and "Strong" a month
    ago do not necessarily mean the same underlying momentum percentage —
    which is an accepted cost given the label's job is to communicate
    today's relative standing at a glance, not to be a fixed benchmark.

    Bands: bottom 25% "Weak", middle 50% (25th-75th percentile)
    "Moderate", next 15% (75th-90th) "Strong", top 10% (90th+) "Very
    Strong" — the standard percentile-decile convention, top decile as
    "Very Strong". Every candidate on this list already has a positive
    signal by construction (long/flat only — pending_orders never queues
    a negative-momentum candidate), so these bands describe the STRENGTH
    of positive momentum, not a bullish/bearish spectrum.

    Returns {} (every candidate then labels "—") when fewer than 4 usable
    values exist — not enough data to form 4 meaningful bands.
    """
    vals = momentum_values.dropna()
    if len(vals) < 4:
        return {}
    return {
        "q25": vals.quantile(0.25),
        "q75": vals.quantile(0.75),
        "q90": vals.quantile(0.90),
    }


def _momentum_strength_label(momentum_pct, bands: dict) -> str:
    """Maps one candidate's raw momentum_pct to a qualitative strength
    label via the relative bands from _compute_momentum_strength_bands()
    — see that function's docstring for the full reasoning. Displayed
    ALONGSIDE the raw percentage, never in place of it, same principle as
    the fixed-denominator/peak-relative drawdown pair shown together
    elsewhere on this page."""
    if not bands or pd.isna(momentum_pct):
        return "—"
    if momentum_pct >= bands["q90"]:
        return "Very Strong"
    if momentum_pct >= bands["q75"]:
        return "Strong"
    if momentum_pct >= bands["q25"]:
        return "Moderate"
    return "Weak"


def _value_history_chart(rows: list[dict]) -> go.Figure:
    """£ account-value line chart — a filled area under the line so the
    real starting-balance anchor (see get_account_value_history()'s own
    docstring) reads as a genuine floor, not a stray first point."""
    df = pd.DataFrame(rows)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["total_value_gbp"], mode="lines+markers",
        line=dict(color="#00C2FF", width=2), marker=dict(size=4),
        fill="tozeroy", fillcolor="rgba(0,194,255,0.08)",
        hovertemplate="%{x}<br>£%{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(tickprefix="£", tickformat=",.0f"),
        showlegend=False,
    )
    return _apply_dark_theme(fig)


def _render_eod_status_badge() -> None:
    """A direct, unmissable "did tonight's EOD job actually run to
    completion" signal (2026-08-02) — distinct from and in addition to the
    specific downstream alerts already built (instrument resolution
    failures, position reconciliation, membership staleness) and from the
    Status page's existing last_full_refresh-staleness-based OVERDUE
    badge, which only ever infers a problem from an old timestamp rather
    than stating a direct verdict for the most recent run. Placed at the
    very top of the Dashboard page — the default landing page — so it's
    genuinely checkable first thing each morning, not something a reader
    has to know to go looking for on the Status page.

    Read from data.scheduler.get_eod_completion_status() (system_metadata
    key "eod_completion", written by _eod_job() itself). Never raises —
    an inability to read this signal must not break the rest of the
    Dashboard page.

    Staleness is checked explicitly, not trusted blindly — the job now
    fires every single day (even a non-trading day records a genuine
    NOOP_NOT_A_TRADING_WINDOW verdict, see _eod_job()'s own docstring), so
    a healthy system always has a record less than ~30 hours old. Without
    this check, a scheduler that had been down for days would still show
    yesterday's — or last week's — green "✅ completed" verdict, silently
    reading as fresh when it is not: exactly the "plausible-looking but
    wrong" failure mode this signal exists to avoid, not reproduce.
    """
    from datetime import datetime as _dt
    from data.scheduler import get_eod_completion_status

    try:
        status = get_eod_completion_status()
    except Exception:
        status = None

    if status is None:
        st.warning("⚪ **EOD status:** no run recorded yet.")
        return

    when = status.get("date", "?")
    recorded_at_raw = status.get("recorded_at")
    age_hours = None
    if recorded_at_raw:
        try:
            age_hours = (_dt.utcnow() - _dt.fromisoformat(recorded_at_raw)).total_seconds() / 3600.0
        except (ValueError, TypeError):
            age_hours = None

    if age_hours is not None and age_hours > 30:
        st.warning(
            f"⚠️ **EOD status is stale** — last recorded {age_hours:.0f}h ago ({when}, "
            f"{status['status']}). The scheduler may not have run recently — check the Status page."
        )
        return

    if status["status"] == "COMPLETED":
        pull = "price pull ran" if status.get("price_pull_ran") else "price pull skipped (non-trading day)"
        st.success(f"✅ **EOD {when}: completed** — {pull}, order pipeline ran.")
    elif status["status"] == "FAILED":
        st.error(f"❌ **EOD {when}: FAILED** — {status.get('error', 'unknown error')}")
    else:  # NOOP_NOT_A_TRADING_WINDOW
        st.info(f"⚪ **EOD {when}: skipped** — not a trading window (expected, not an error).")


def _render_crisis_banner() -> None:
    """A prominent warning banner shown ONLY when the crisis override is
    genuinely active right now (2026-08-03) -- sourced from
    output/performance.py:get_crisis_status()'s `entry_blocked` field,
    which itself defers entirely to brokers.trading212.is_crisis_mode_
    active_now() -- the exact same single, authoritative check order
    submission re-confirms fresh before ever placing a new entry. Never a
    second, independently-derived computation -- see that function's own
    docstring and CLAUDE.md's "Crisis-mode dashboard banner" section.

    Nothing is rendered at all when crisis mode isn't active -- including
    when the underlying check couldn't be determined (get_crisis_status()
    maps that to entry_blocked=False) -- deliberately, per this feature's
    own requirement: a muted/inactive version of a warning banner risks
    being mistaken for a real one, so the only two states are "shown" and
    "not present on the page at all," never a third, quieter variant.
    """
    try:
        status = _get_crisis_status()
    except Exception:
        status = {}

    if not status.get("entry_blocked"):
        return

    st.error(
        "🚨 **Crisis override active — new entries are currently blocked.** "
        "The S&P 500 has triggered the ≥7% single-day decline circuit breaker "
        "(or remains within its post-trigger cooldown window). **Existing "
        "positions continue to be monitored and exited normally — only new "
        "entries are affected.**"
    )


def _render_dashboard_page(config: dict) -> None:
    """Render the Dashboard: account balance, account-value history, top
    winning/losing closed trades, the queued-candidate list, and
    placed-but-unconfirmed exits. Replaces the Daily Briefing page
    (2026-08-02) — full open-position monitoring remains on the
    Performance page (unchanged), and per-ticker momentum/eligibility
    detail remains reachable via the Status page; this page's job is
    account-level overview, not per-position or per-ticker detail."""
    _render_page_wordmark("Dashboard")
    st.markdown("## Dashboard")
    _render_eod_status_badge()

    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        st.info("Trading is disabled. Set `trading212_invest.enabled: true` in config.yaml to activate.")
        _render_footer()
        return

    _render_crisis_banner()

    # --- Account balance ---------------------------------------------------
    summary = _get_account_summary()
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Account Value", f"£{summary['total_value_gbp']:,.2f}")
    a2.metric("Available Cash", f"£{summary['cash_gbp']:,.2f}")
    a3.metric("Invested", f"£{summary['invested_gbp']:,.2f}", delta=f"{summary['deployed_pct']:.0f}% deployed")
    a4.metric("Starting Balance", "£50,000.00")

    # --- Account value history ---------------------------------------------
    st.markdown("### Account Value")
    range_label = st.radio(
        "Range", options=list(_VALUE_CHART_RANGES.keys()), index=0,
        horizontal=True, label_visibility="collapsed", key="dashboard_value_range",
    )
    hist_rows = _get_value_history(_VALUE_CHART_RANGES[range_label])
    st.plotly_chart(_value_history_chart(hist_rows), width="stretch")
    if len(hist_rows) <= 2:
        st.caption(
            "The account was reset to a clean £50,000 with no trading history yet — "
            "this chart fills in day by day as real T212 EOD snapshots accumulate, not an error."
        )

    st.divider()

    # --- Top winners / losers -----------------------------------------
    st.markdown("### Top Trades")
    winners, losers = _get_top_trades_cached()
    if not winners and not losers:
        st.info("No closed trades yet — no live position has closed since the account reset. "
                 "This section will populate as real trading accumulates.")
    else:
        wcol, lcol = st.columns(2)
        with wcol:
            st.markdown("**Top 5 Winners**")
            if winners:
                wdf = pd.DataFrame(winners)[["ticker", "exit_date", "pnl_gbp", "pnl_pct", "exit_reason"]].copy()
                wdf["exit_date"] = pd.to_datetime(wdf["exit_date"]).dt.strftime("%Y-%m-%d")
                wdf["pnl_gbp"] = wdf["pnl_gbp"].map(lambda v: f"+£{v:,.2f}")
                wdf["pnl_pct"] = wdf["pnl_pct"].map(lambda v: f"+{v * 100:.1f}%")
                wdf.columns = ["Ticker", "Closed", "P&L (£)", "P&L (%)", "Exit Reason"]
                st.dataframe(wdf, width="stretch", hide_index=True)
            else:
                st.caption("No winning trades yet.")
        with lcol:
            st.markdown("**Top 5 Losers**")
            if losers:
                ldf = pd.DataFrame(losers)[["ticker", "exit_date", "pnl_gbp", "pnl_pct", "exit_reason"]].copy()
                ldf["exit_date"] = pd.to_datetime(ldf["exit_date"]).dt.strftime("%Y-%m-%d")
                ldf["pnl_gbp"] = ldf["pnl_gbp"].map(lambda v: f"£{v:,.2f}")
                ldf["pnl_pct"] = ldf["pnl_pct"].map(lambda v: f"{v * 100:.1f}%")
                ldf.columns = ["Ticker", "Closed", "P&L (£)", "P&L (%)", "Exit Reason"]
                st.dataframe(ldf, width="stretch", hide_index=True)
            else:
                st.caption("No losing trades yet.")

    st.divider()

    # --- Recent orders ---------------------------------------------------
    # Moved here 2026-08-03 from the Status page (see CLAUDE.md's
    # Status-page-restructuring section) -- invest_orders is real,
    # permanent trade history (any status: PLACED/FILLED/FAILED/
    # CANCELLED/REJECTED, BUY and SELL), a genuinely different table from
    # pending_orders (the transient pre-trade decision queue the Candidate
    # Queue section below reads) -- this is a relocation for
    # organisation, not a de-duplication, since there was no overlap to
    # begin with.
    st.markdown("### Recent Orders")
    orders = _get_recent_orders_cached()
    if not orders:
        st.caption("No orders recorded yet.")
    else:
        odf = pd.DataFrame(orders).copy()
        odf["Type"] = odf["is_etf"].map(lambda b: "ETF" if b else "STOCK")
        odf["var_pct"] = odf["var_pct"].map(lambda v: f"{v * 100:.2f}%" if pd.notna(v) else "—")
        odf["momentum_pct"] = odf["momentum_pct"].map(lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "—")
        odf = odf[[
            "ticker", "t212_ticker", "Type", "direction", "quantity",
            "estimated_value_gbp", "var_pct", "momentum_pct", "status",
            "account_type", "error_message", "placed_at",
        ]]
        odf.columns = [
            "Ticker", "T212 Ticker", "Type", "Dir", "Qty",
            "Est. Value (£)", "VaR at Entry", "Momentum at Entry", "Status",
            "Account", "Error", "Placed At",
        ]
        st.dataframe(
            odf.style.map(
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

    st.divider()

    # --- Candidate queue -----------------------------------------------
    st.markdown("### Candidate Queue")
    st.caption(
        "Every decision queued at EOD, still retained by the 30-day pending_orders "
        "retention policy — whatever became of it: still awaiting its exchange-timed "
        "submission window (14:30 UTC for stocks — see CLAUDE.md's \"Exchange-aware order "
        "submission\" section), or already resolved (submitted, skipped, rejected on "
        "capacity, or superseded by a fresh re-run). A resolved row is a real, final, "
        "historical outcome — not something that disappears once decided."
    )
    candidates = _get_pending_candidates_cached()
    if not candidates:
        st.info("No candidates currently queued or retained.")
    else:
        projected = _get_projected_admission_cached(config)
        projected_by_ticker = {p["ticker"]: p["projected_status"] for p in projected}

        cdf = pd.DataFrame(candidates).copy()
        # Real status wins for an already-resolved row — only a genuinely
        # still-PENDING row gets the live re-projected status. Looked up per
        # ROW, not by ticker: a ticker can have several historical rows
        # (e.g. an earlier SUPERSEDED decision and the later, final one that
        # replaced it), and a ticker-keyed map would silently show one row's
        # status on the other (2026-08-03 fix).
        cdf["Status"] = cdf.apply(
            lambda r: r["status"] if r["status"] != "PENDING"
            else projected_by_ticker.get(r["ticker"], "PROJECTION_UNAVAILABLE"),
            axis=1,
        )
        cdf["Note"] = cdf["resolution_note"].fillna("—")
        cdf["decided_date"] = pd.to_datetime(cdf["decided_at"]).dt.date
        cdf["decided_at"] = pd.to_datetime(cdf["decided_at"]).dt.strftime("%Y-%m-%d %H:%M")

        # Momentum-strength bands are computed per decision-date cohort, not
        # across the whole table (2026-08-03) -- now that resolved rows from
        # earlier days are shown alongside today's, computing one set of
        # bands across every day at once would silently mix different days'
        # momentum distributions together, breaking _compute_momentum_
        # strength_bands()'s own documented "relative to that day's pool"
        # design. Grouping by decided_date keeps each day's candidates
        # judged only against their own day's real pool, exactly as before
        # this fix for whichever day is the most recent.
        def _label_group(s: pd.Series) -> pd.Series:
            bands = _compute_momentum_strength_bands(s)
            return s.map(lambda v: _momentum_strength_label(v, bands))
        cdf["Strength"] = cdf.groupby("decided_date")["momentum_pct"].transform(_label_group)

        cdf["momentum_pct"] = cdf["momentum_pct"].map(lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "—")
        cdf["position_gbp"] = cdf["position_gbp"].map(lambda v: f"£{v:,.2f}" if pd.notna(v) else "—")

        st.caption(
            "⚠️ **A `PROJECTED_*` status is a projection, not a guarantee** — shown only for "
            "a row still genuinely `PENDING`. Computed by replaying the same momentum-ranked, "
            "skip-and-continue capacity check real submission uses, against the account's "
            "real state *right now* — the actual decision only happens at real submission "
            "time, against whatever the account's state is *at that moment*, which can "
            "genuinely change in between (a position closing, an emergency exit). Any other "
            "status shown is the row's real, final, historical outcome — not a projection."
        )
        st.caption(
            "**Strength** is relative to that candidate's own decision-date cohort (bottom "
            "25% Weak · middle 50% Moderate · next 15% Strong · top 10% Very Strong), "
            "recalculated fresh per day — not a fixed threshold — so it reflects that day's "
            "real conditions rather than going stale as the market moves. The raw percentage "
            "is always shown alongside it."
        )
        status_options = sorted(cdf["Status"].unique().tolist())
        status_filter = st.multiselect(
            "Filter by status", options=status_options, default=status_options,
            key="candidate_status_filter",
        )
        cdf = cdf[cdf["Status"].isin(status_filter)]

        cdf = cdf[["ticker", "Status", "momentum_pct", "Strength", "position_gbp", "decided_at", "Note"]]
        cdf.columns = ["Ticker", "Status", "Momentum", "Strength", "Sized Position", "Decided At", "Note"]
        st.caption(f"{len(cdf)} of {len(candidates)} candidates shown")
        st.dataframe(cdf, width="stretch", hide_index=True)

    st.divider()

    # --- Placed, unconfirmed exits --------------------------------------
    st.markdown("### Placed, Unconfirmed Exits")
    st.caption(
        "SELL orders T212 has accepted but not yet resolved to FILLED/CANCELLED/REJECTED. "
        "Status here only updates when poll_placed_orders() next runs — hourly via the "
        "Open Trade Monitor job, or sooner via a manual account refresh — so a row a few minutes "
        "old is routine, not stuck. A 2026-07 finding once cited a 60+ hour resolution gap "
        "on this account as a documented characteristic; tracing its origin (2026-08-03) "
        "found that figure was based on this system's own polling timing, not T212's real "
        "fill timestamp, and could not be independently re-verified — every currently-real "
        "fill on this account has resolved within seconds. Treat a row that lingers for an "
        "unusually long time as worth checking directly against the T212 app, not "
        "automatically dismissed as a known, harmless artifact."
    )
    exits = _get_unconfirmed_exits_cached()
    if not exits:
        st.info("No exits currently awaiting confirmation.")
    else:
        edf = pd.DataFrame(exits).copy()
        edf["placed_at"] = pd.to_datetime(edf["placed_at"])
        edf["age_hours"] = ((datetime.utcnow() - edf["placed_at"]).dt.total_seconds() / 3600).round(1)
        edf["placed_at"] = edf["placed_at"].dt.strftime("%Y-%m-%d %H:%M")
        edf = edf[["ticker", "exit_reason", "quantity", "placed_at", "age_hours"]]
        edf.columns = ["Ticker", "Exit Reason", "Quantity", "Placed At", "Age (hours)"]
        st.dataframe(edf, width="stretch", hide_index=True)

    _render_footer()


# ---------------------------------------------------------------------------
# Status page
# ---------------------------------------------------------------------------

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

    # Both cards below use _trading_day_age_level() (2026-08-03), a genuine
    # NYSE-calendar-aware staleness check, in place of a naive elapsed-hours
    # count — a plain hours threshold misreads an ordinary Friday-to-Monday
    # weekend gap (63+ real hours, but only ONE genuine trading day) as
    # several days stale. See _nyse_trading_days_since()'s own docstring.
    _ref_val, _ref_sub = _age_display(last_refresh)
    _status_card(pr1, "Last Data Refresh", _ref_val, _ref_sub,
                 _trading_day_age_level(last_refresh))

    _sched_val, _sched_sub = _age_display(last_sched)
    if last_sched_name:
        _sched_sub = f"{last_sched_name} · {_sched_sub}"
    _status_card(pr2, "Last Scheduled Run", _sched_val, _sched_sub,
                 _trading_day_age_level(last_sched))

    _sched_cfg_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_LABELS = {"eod": "End of Day"}
    _nxt_dt, _nxt_label = None, ""
    for _rn, _rl in _RUN_LABELS.items():
        _rc = _sched_cfg_runs.get(_rn, {})
        _nt = _next_run_for_time(int(_rc.get("hour", 0)), int(_rc.get("minute", 0)))
        if _nxt_dt is None or _nt < _nxt_dt:
            _nxt_dt, _nxt_label = _nt, _rl
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

    # ── Scheduler Health ──────────────────────────────────────────────────
    # Lives here — near the top of the page, in the old "Pipeline"
    # section's former position — rather than further down, per the
    # 2026-08-03 consolidation: Pipeline and Scheduler Health used to be
    # two separate sections both answering "what's happening with the
    # scheduled jobs," with Pipeline showing only a simplified two-step
    # subset of what this section already covers more completely across
    # every registered job. Pipeline's own two data points (price-refresh
    # coverage detail, last-run-type label) are folded into this section's
    # "End of Day" row's Description column below — that job is the one
    # that actually performs both the price pull and the order-decide
    # logic Pipeline used to track separately, so nothing genuinely unique
    # from Pipeline was lost, only its standalone section.
    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)
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

    # Labels must match the *real* jobs data/scheduler.py:start_scheduler()
    # registers, not a stale snapshot — scheduled_eod/membership_refresh
    # fire daily now (no day_of_week filter, NYSE-calendar-gated
    # internally, see _is_scheduled_trading_day()), and
    # stock_order_submission/weekly_summary are genuine, order-affecting
    # jobs that must appear here, not silently fall back to a raw job id.
    _JOB_LABELS: dict[str, tuple[str, str]] = {
        "scheduled_eod":          ("End of Day",       f"{_uk_sched_time(21, 30)} daily · NYSE-gated"),
        "market_hours_check":     ("Open Trade Monitor", "Hourly, every day · NYSE-hours-gated"),
        "account_state_refresh":  ("Account State Refresh", "Hourly, every day · unconditional"),
        "membership_refresh":     ("Membership Refresh", f"{_uk_sched_time(21, 15)} daily · NYSE-gated"),
        "stock_order_submission": ("Stock Order Submission", f"{_uk_sched_time(14, 30)} · Mon–Fri"),
        "weekly_summary":         ("Weekly Summary",    "09:00 UK time · Sunday"),
        "job_dispatcher":         ("job_dispatcher",    "—"),
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

    # scheduled_eod's Description column folds in exactly what the old,
    # separate "Pipeline" section used to show for its two steps (price
    # refresh coverage, last-run-type) — both are genuinely produced by
    # this one job, so nothing is lost by not having a standalone section.
    _eod_detail = f"{price_desc} · {order_desc}"

    def _sched_row_html(jlabel: str, jsched: str, nrt_str: str, until_str: str, dot: str, badge: str,
                         detail: str = "") -> str:
        return (
            f'<div style="display:flex;padding:5px 0;border-bottom:1px solid #1E2D45;'
            f'align-items:center;font-size:0.78rem">'
            f'<div style="flex:0 0 16%;font-weight:600">{html.escape(jlabel)}</div>'
            f'<div style="flex:0 0 20%;color:#8899AA;font-size:0.72rem">{html.escape(jsched)}</div>'
            f'<div style="flex:0 0 18%">{html.escape(nrt_str)}</div>'
            f'<div style="flex:0 0 9%;color:#8899AA">{html.escape(until_str)}</div>'
            f'<div style="flex:0 0 10%;color:{dot};font-weight:700">{html.escape(badge)}</div>'
            f'<div style="flex:1;color:#8899AA;font-size:0.72rem;padding-left:8px">{html.escape(detail)}</div>'
            f'</div>'
        )

    _sh_head = (
        '<div style="font-size:0.82rem">'
        '<div style="display:flex;padding:3px 0 5px;border-bottom:2px solid #1E2D45;'
        'color:#8899AA;font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em">'
        '<div style="flex:0 0 16%">Job</div>'
        '<div style="flex:0 0 20%">Schedule</div>'
        '<div style="flex:0 0 18%">Next Run</div>'
        '<div style="flex:0 0 9%">In</div>'
        '<div style="flex:0 0 10%">Status</div>'
        '<div style="flex:1;padding-left:8px">Description</div>'
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
            _detail = _eod_detail if _job.id == "scheduled_eod" else ""
            _sh_rows.append(_sched_row_html(_jlabel, _jsched, _nrt_str, _until_str, _dot, _badge, _detail))
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
            # No day_of_week filter — matches the real trigger in
            # data/scheduler.py:start_scheduler() exactly. These jobs fire
            # every calendar day and gate themselves internally via
            # _is_scheduled_trading_day() (NYSE calendar); a reconstructed
            # Mon-Fri-only trigger here would silently show the wrong next
            # fire time on a weekend/holiday.
            _trigger = _CronTrigger(
                hour=_rc["hour"], minute=_rc["minute"],
                timezone=_tz_s,
            )
            _static_jobs.append((_sid, _jlabel, _jsched, _trigger))

        # membership_refresh is fixed at 21:15 rather than derived from
        # scheduler.runs, and — like scheduled_eod above — has no
        # day_of_week filter on its real trigger. stock_order_submission
        # (14:30 UTC weekdays) and weekly_summary (09:00 Europe/London,
        # Sunday — its own explicit timezone, not this scheduler's UTC
        # default) are real, currently registered jobs that were
        # previously missing from this reconstruction entirely, making the
        # actual order-submission job invisible on the Status page
        # whenever the standalone daemon (rather than the in-process
        # fallback) is what's running — exactly the case in normal
        # production operation. monday_premarket was removed entirely
        # 2026-08-03 — confirmed genuinely redundant with the hourly
        # trade alert job, which fired at the identical 07:00 UTC minute
        # every Monday (at the time, the single unconditional
        # _trade_alert_job() this file registered — split 2026-08-03 into
        # market_hours_check/account_state_refresh, both still hourly
        # every day, so this redundancy finding is unaffected) — see
        # CLAUDE.md's "Monday Pre-Market removed" section.
        #
        # market_hours_check/account_state_refresh (2026-08-03 split, see
        # data/scheduler.py's own docstrings and CLAUDE.md's "Splitting
        # the hourly trade-alert job" section) both register with no
        # hour= or day_of_week= filter at all — genuinely hourly, every
        # day — so both reconstruct here as a plain hourly trigger with no
        # cron restriction, unlike every other job in this list.
        _extra_static = [
            ("market_hours_check", "Open Trade Monitor", "Hourly, every day · NYSE-hours-gated",
             _CronTrigger(minute=0, timezone=_tz_s)),
            ("account_state_refresh", "Account State Refresh", "Hourly, every day · unconditional",
             _CronTrigger(minute=0, timezone=_tz_s)),
            ("membership_refresh", "Membership Refresh", f"{_uk_sched_time(21, 15)} daily · NYSE-gated",
             _CronTrigger(hour=21, minute=15, timezone=_tz_s)),
            ("stock_order_submission", "Stock Order Submission", f"{_uk_sched_time(14, 30)} · Mon–Fri",
             _CronTrigger(hour=14, minute=30, day_of_week="mon-fri", timezone=_tz_s)),
            ("weekly_summary", "Weekly Summary", "09:00 UK time · Sunday",
             _CronTrigger(day_of_week="sun", hour=9, minute=0, timezone="Europe/London")),
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
            _detail = _eod_detail if _sid == "scheduled_eod" else ""
            _sh_rows_d.append(_sched_row_html(_jlabel, _jsched, _nrt_str, _until_str, _dot, _badge, _detail))
        st.markdown(_sh_head + "".join(_sh_rows_d) + "</div>", unsafe_allow_html=True)
    else:
        st.warning(
            "Scheduler is not running — scheduled jobs will not fire until the service restarts. "
            "On the server, ensure investment-research-scheduler.service is enabled and running."
        )

    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)
    st.markdown("### Scheduled Runs")
    st.caption("Next fire time for each automated data/order-pipeline run.")
    sched_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_DISPLAY = {
        "eod": ("End of Day", "Full data refresh + order logic after NYSE close"),
    }
    run_cols = st.columns(len(_RUN_DISPLAY))
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

            # Live account value/cash/invested/open-position count, the
            # Recent Orders table, and instrument-resolution status all
            # moved off this page 2026-08-03 (see CLAUDE.md's
            # Status-page-restructuring section) -- the first is genuinely
            # duplicated by the Dashboard's own account cards; the latter
            # two are relocations for organisation, not de-duplication.
            # This button is kept here since it's the one thing in this
            # section that's an ACTION, not state -- it refreshes the same
            # underlying T212 snapshot both this page and the Dashboard
            # read, so it still belongs with the account configuration
            # context above rather than only on the page that displays the
            # resulting numbers.
            st.caption(
                "Account value, cash, invested, and open-position count are on the "
                "**Dashboard** page. Recent Orders is there too. Instrument resolution "
                "status is on the **Instruments** page."
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
                                      "trade alert", "market-hours check", "account state",
                                      "membership", "scheduled_", "catch-up",
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

    # ── Danger Zone ────────────────────────────────────────────────────────
    # Manual Controls moved here 2026-08-03 (see CLAUDE.md's
    # Status-page-restructuring section) — same visual pattern GitHub uses
    # for destructive/high-consequence actions: separated from routine
    # informational sections, at the very bottom of the page, with a
    # deliberately red-tinted heading rather than sitting inline with
    # everything else. Both buttons can trigger real T212 order placement
    # immediately — genuinely live-affecting, unlike anything else on this
    # page — so they get that visual weight. Functionally unchanged: same
    # two buttons, same behaviour, only the placement/styling changed.
    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(
            '<div style="border-left:3px solid #dc2626;background:#dc262614;'
            'padding:10px 14px;border-radius:0 6px 6px 0;margin-bottom:10px">'
            '<div style="color:#dc2626;font-weight:700;font-size:1.05rem">⚠ Danger Zone</div>'
            '<div style="color:#8899AA;font-size:0.82rem;margin-top:2px">'
            'Both actions below can trigger real T212 order placement immediately, '
            'without waiting for the next scheduled run.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
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

    _render_footer()

    if _log_auto_refresh:
        time.sleep(30)
        st.rerun()


# ---------------------------------------------------------------------------
# Performance page
# ---------------------------------------------------------------------------

def _asset_type_map(config: dict) -> dict[str, str]:
    """Map research ticker -> 'ETF' or 'STOCK', sourced from every VERIFIED
    T212 instrument resolution (see CLAUDE.md's "wire the momentum-only,
    full-S&P-500 selection into live execution" section). Every resolved
    ticker is a stock in practice today -- the ETF sleeve was removed from
    live trading entirely -- but this reads genuinely per-ticker rather
    than assuming that, in case a future re-addition ever changes it."""
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
    methodology and CLAUDE.md's "rebuild the backtest/DD-report page to
    genuinely run against the current, real live engine" (2026-08-02) and
    "V2 architecture" sections for validation history.

    Genuinely runs the current, real live engine (2026-08-02) —
    live_universe=True (the full, dynamically-refreshed S&P 500 stock
    candidate pool get_live_stock_candidates() itself uses, no ETFs —
    the sleeve was removed from live trading entirely) and
    mark_to_market=True (simulate_portfolio_compounding_mtm(), the engine
    confirmed to genuinely match what live order placement's own
    account-value reads do). Both flags are passed explicitly, matching
    the same explicit-not-implicit convention already established for
    engine_used/deployment_basis elsewhere in this module.
    """
    from analysis.validation import live_universe_snapshot

    _render_page_wordmark("Backtest")
    st.markdown("## Backtest")
    st.caption(
        "12-month momentum signal, the full dynamically-refreshed S&P 500 stock "
        "universe (no ETFs — removed from live trading entirely), and the "
        "≥7% single-day crisis override — the same engine and the same live "
        "candidate pool that drive real order placement (brokers/trading212.py), "
        "run over its full validated 2007-present history."
    )
    st.caption(
        "📈 **Continuous walk** — one single, unbroken simulated account runs start to end with no "
        "balance reset at the train/test boundary. This reports what one real, continuously-running "
        "account would actually have returned since inception — not a reset-at-boundary comparison "
        "between variants (see CLAUDE.md's \"Two backtest conventions\" section)."
    )

    snap = live_universe_snapshot()
    uc1, uc2, uc3 = st.columns(3)
    uc1.metric("Candidate Universe", f"{snap['candidate_universe_count']}",
               "distinct tickers ever discovered as genuine S&P 500 constituents")
    uc2.metric("Live-Eligible Today", f"{snap['live_eligible_count']}",
               "genuine S&P 500 members right now — no ETFs, index membership only")
    uc3.metric("Excluded Today", str(snap["excluded_count"]),
               ("e.g. " + ", ".join(snap["excluded_sample"])) if snap["excluded_sample"] else "none")
    st.caption(
        "These figures are genuine S&P 500 index membership eligibility — the same universe the "
        "backtest itself simulates — never T212-specific tradeability or mapping status, which is a "
        "separate, live-execution-only concern."
    )

    st.divider()

    if st.button("Run Full Validated Battery (2007–present)", type="primary", key="run_full_validation"):
        with st.spinner(
            "Running the full battery across every live S&P 500 candidate (~500 tickers) — this "
            "typically takes around 12 minutes at this universe size…"
        ):
            try:
                from analysis.validation import run_full_validation
                result = run_full_validation(config, live_universe=True, mark_to_market=True)
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
                   f"{result['n_tickers_with_data']}/{result['n_tickers_total']} tickers had usable data · "
                   f"engine: {result.get('engine_used')} ({result.get('deployment_basis')})")

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
    prd = result.get("peak_relative_drawdown", {})
    tc1, tc2 = st.columns(2)
    for col, label, m, sp, prd_key in (
        (tc1, "TRAIN (2007–2019)", result["train"], result.get("train_sp", {}), "train"),
        (tc2, "TEST (2020–2026)", result["test"], result.get("test_sp", {}), "test"),
    ):
        with col:
            st.markdown(f"**{label}**")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Trades", m.get("total_trades", 0))
            r2.metric("CAGR", f"{m.get('cagr_pct', 0):+.2f}%",
                      f"vs S&P {sp.get('cagr_pct', 0):+.2f}%" if sp.get("cagr_pct") is not None else None)
            r3.metric("Max DD (vs starting capital)", f"{m.get('max_drawdown_pct', 0):.2f}%",
                      f"vs S&P {sp.get('max_drawdown_pct', 0):.2f}%" if sp.get("max_drawdown_pct") is not None else None)
            r4.metric("Win Rate", f"{m.get('win_rate', 0) * 100:.1f}%")
            _prd_val = prd.get(prd_key)
            if _prd_val is not None:
                st.metric("Max DD (genuine peak-relative)", f"{_prd_val:.2f}%")
    st.caption(
        "⚠️ \"Max DD (vs starting capital)\" is the fixed-denominator convention used throughout this "
        "report — how far below the ORIGINAL £50,000 starting balance the account ever fell. On a run "
        "that grew substantially before any later decline, this can read far worse than the genuine "
        "risk an investor holding at any point along the way actually experienced — \"Max DD (genuine "
        "peak-relative)\", shown above whenever it differs, is how far the account ever fell below its "
        "OWN prior high. Both are real, correctly-computed numbers off the identical equity curve — "
        "they answer different questions (see CLAUDE.md's \"No market-cap gate at all\" section)."
    )

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
        _full_prd = prd.get("full")
        _prd_suffix = f" (peak-relative: {_full_prd:.2f}%)" if _full_prd is not None else ""
        st.caption(
            f"{raw.get('total_trades', 0)} trades · CAGR {raw.get('cagr_pct', 0):+.2f}% · "
            f"Max DD {raw.get('max_drawdown_pct', 0):.2f}%{_prd_suffix} · Win rate {raw.get('win_rate', 0) * 100:.1f}% · "
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
        f"**{result['n_blocks_pass']}/{result['n_blocks_total']} blocks pass** — strategy max drawdown no "
        "worse than 1.5× the S&P's own over the same window, AND strategy profit factor greater than "
        "1.0 over the same window (the reconciled formula — see CLAUDE.md's \"Block pass/fail formula "
        "reconciliation\" section; there is no per-block CAGR-vs-S&P gate)."
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
        # proximity to a threshold, which would imply some positions need
        # more attention than others (see CLAUDE.md's "Decision-relevant,
        # tone-neutral context on open position cards" section).
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

        _PAGES = ["Dashboard", "Instruments", "Performance", "Backtest", "Status"]
        _PAGE_KEYS = {
            "Dashboard":      "dashboard",
            "Instruments":    "instruments",
            "Performance":    "performance",
            "Backtest":       "backtest",
            "Status":         "status",
        }
        current_key = st.session_state.get("page", "dashboard")
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
    page_key = st.session_state.get("page", "dashboard")

    if page_key == "dashboard":
        _render_dashboard_page(config)
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

    # Instruments page (2026-08-02, replaces Charts)
    _render_instruments_page(config)


if __name__ == "__main__":
    main()
