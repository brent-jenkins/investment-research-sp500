"""Investment Research Dashboard — Streamlit entry point."""

import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so sibling packages resolve correctly
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import html
import logging
import subprocess
import time
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from analysis.confluence import (
    generate_signals,
    get_last_signal_timestamp,
    get_latest_signals,
    get_learned_weights,
    get_regime_thresholds,
    get_weight_history,
)
from analysis.correlations import (
    build_correlation_matrix,
    compute_all_pairs,
    get_active_opportunity_flags,
    get_stored_correlations,
)
from analysis.regime import OVERLAY_DESCRIPTIONS
from analysis.engine import run_analysis
from analysis.regime import (
    REGIME_COLOURS,
    REGIME_DESCRIPTIONS,
    REGIME_WEIGHTS,
    Regime,
    detect_regime,
    is_entry_allowed,
    resolve_entry_signal,
)
from data.database import get_engine, get_metadata, get_metadata_str, init_db, set_metadata
from data.ingestion import (
    get_all_tickers,
    get_price_df,
    pull_incremental,
)
from data.scheduler import start_scheduler
from output.performance import (
    FOREX_QUOTE_CCY,
    get_current_signal,
    get_latest_price,
)
from analysis.macro import get_macro_dashboard, fetch_and_store_macro_indicators, SIGNALS_ACTIVE
from analysis.backtest import (
    delete_run,
    get_all_runs,
    get_run,
    get_run_trades,
    run_backtest,
)
from analysis.backtest_metrics import (
    ANALYSIS_PERIODS,
    MODULE_LABELS,
    compute_metrics,
    equity_curve as bt_equity_curve,
    exit_reason_breakdown,
    forex_holding_sensitivity,
    module_attribution,
    period_breakdown,
    regime_performance,
    signal_decay,
    walk_forward,
)
from output.briefing import (
    STATUS_COLOURS as _BRIEF_STATUS_COLOURS,
    STATUS_ICONS   as _BRIEF_STATUS_ICONS,
    _generate_daily_narrative,
    build_opportunities,
    get_current_atr,
    get_macro_context,
    send_test_email,
    send_weekly_summary_email,
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

    Note: st.cache_resource is process-wide but is only invoked when the Streamlit
    script first runs (i.e. on first page visit). This function therefore serves as
    a fallback for environments where the standalone daemon is not running.
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
    if key := os.environ.get("FRED_API_KEY"):
        cfg.setdefault("macro", {})["fred_api_key"] = key
    if pw := os.environ.get("SMTP_PASSWORD"):
        cfg.setdefault("notifications", {}).setdefault("email", {})["smtp_password"] = pw
    return cfg


def _build_watchlist(config: dict) -> dict[str, str]:
    """Return an ordered dict of {ticker: display_name} for all watchlist assets."""
    items: dict[str, str] = {}
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry:
                items[entry["ticker"]] = entry.get("name", entry["ticker"])
    return items


def _build_tradeable_set(config: dict) -> set[str]:
    """Return the set of tickers marked tradeable: true in config (default true if absent)."""
    result: set[str] = set()
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry and entry.get("tradeable", True):
                result.add(entry["ticker"])
    return result


def _build_t212_names(config: dict) -> dict[str, str]:
    """Return {ticker: trading212_instrument} for assets where the T212 name differs."""
    result: dict[str, str] = {}
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry and "trading212_instrument" in entry:
                result[entry["ticker"]] = entry["trading212_instrument"]
    return result


@st.cache_data(ttl=300)
def _cached_latest_price(ticker: str) -> float | None:
    """Live price from yfinance, cached for 5 minutes to limit API calls."""
    return get_latest_price(ticker)


# Maps (instrument_ccy, account_ccy) → (yfinance_ticker, invert).
# "invert" means the stored fx_rate = 1/close rather than close.
# All entries use well-known pairs that yfinance reliably serves.
# Convention: fx_rate = "1 account_ccy = fx_rate instrument_ccy"
# so P&L conversion is always: pnl_account = pnl_instrument / fx_rate.
_CCY_PAIR_LOOKUP: "dict[tuple[str, str], tuple[str, bool]]" = {
    ("USD", "GBP"): ("GBPUSD=X", False),   # 1 GBP = 1.34 USD; pnl_gbp = pnl_usd / 1.34
    ("EUR", "GBP"): ("GBPEUR=X", False),   # 1 GBP = 1.18 EUR; pnl_gbp = pnl_eur / 1.18
    ("GBP", "USD"): ("GBPUSD=X", True),    # fx_rate = 1/GBPUSD ≈ 0.746; pnl_usd = pnl_gbp / 0.746
    ("GBP", "EUR"): ("EURGBP=X", False),   # 1 EUR = 0.86 GBP; pnl_eur = pnl_gbp / 0.86
    ("USD", "EUR"): ("EURUSD=X", False),   # 1 EUR = 1.08 USD; pnl_eur = pnl_usd / 1.08
    ("EUR", "USD"): ("EURUSD=X", True),    # fx_rate = 1/EURUSD ≈ 0.926; pnl_usd = pnl_eur / 0.926
}

_CCY_SYM: dict[str, str] = {"GBP": "£", "USD": "$", "JPY": "¥", "EUR": "€"}


def _quote_ccy_sym(ticker: str) -> str:
    """Return the currency symbol for the QUOTE currency of *ticker*'s price.

    The raw P&L from a price move is always in the quote currency:
      GBPUSD=X  → USD ($)   USDJPY=X → JPY (¥)   EURGBP=X → GBP (£)
      US ETFs   → USD ($)   LSE .L   → GBP (£)
    """
    # FOREX_QUOTE_CCY has explicit mappings for all tracked pairs (covers EURGBP=X → GBP, etc.)
    if ticker in FOREX_QUOTE_CCY:
        return _CCY_SYM.get(FOREX_QUOTE_CCY[ticker], "$")
    # Pattern fallbacks for anything not in the explicit dict
    if ticker.endswith("JPY=X"): return "¥"
    if ticker.endswith("=X"):    return "$"   # most forex pairs quoted in USD
    if ticker.endswith(".L"):    return "£"
    return "$"                                # default: US-listed instruments


def _fx_pair_label(instrument_ccy: str, account_ccy: str) -> str:
    """Return the canonical pair name for a currency combo, e.g. 'GBPUSD'."""
    if instrument_ccy == account_ccy:
        return ""
    pair_info = _CCY_PAIR_LOOKUP.get((instrument_ccy, account_ccy))
    return pair_info[0].replace("=X", "") if pair_info else ""


def _fetch_fx_live(yf_ticker: str) -> "float | None":
    """Try four methods to get a live close price for *yf_ticker*. Returns None if all fail."""
    import yfinance as yf
    # Method 1: fast_info — fastest, single HTTP call, no DataFrame
    try:
        price = yf.Ticker(yf_ticker).fast_info["last_price"]
        if price and float(price) > 0:
            return float(price)
    except Exception:
        pass

    # Method 2: yf.download 1d (classic API)
    try:
        data = yf.download(yf_ticker, period="1d", progress=False, auto_adjust=True)
        if not data.empty:
            if hasattr(data.columns, "levels"):
                data.columns = [col[0] for col in data.columns]
            val = float(data["Close"].iloc[-1])
            if val > 0:
                return val
    except Exception:
        pass

    # Method 3: Ticker.history 1d
    try:
        hist = yf.Ticker(yf_ticker).history(period="1d")
        if not hist.empty:
            val = float(hist["Close"].iloc[-1])
            if val > 0:
                return val
    except Exception:
        pass

    # Method 4: Ticker.history 5d — picks up weekends/holidays
    try:
        hist = yf.Ticker(yf_ticker).history(period="5d")
        if not hist.empty:
            val = float(hist["Close"].iloc[-1])
            if val > 0:
                return val
    except Exception:
        pass

    return None


def _get_fx_db_fallback(pair_key: str) -> "tuple[float | None, str | None]":
    """Read the last persisted rate from system_metadata. Returns (rate, timestamp_str)."""
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                sa_text("SELECT value, updated_at FROM system_metadata WHERE key = :k"),
                {"k": f"last_{pair_key}_rate"},
            ).fetchone()
        if row and row[0]:
            ts = str(row[1])[:16].replace("T", " ") if row[1] else None
            return float(row[0]), ts
    except Exception:
        pass
    return None, None


def _prefetch_fx_rates() -> None:
    """Fetch and store common FX rates to DB on startup so the DB fallback always has data.

    Called once per session during _initialise(). Stores the raw yfinance close price
    under the same key that _get_fx_db_fallback() reads (last_{pair_key}_rate), ensuring
    the Level-3 DB fallback in _get_fx_rate() returns a usable rate even if the first
    live fetch in a user session fails.
    """
    import yfinance as yf
    _pairs = [
        ("GBPUSD=X", "gbpusd"),
        ("EURUSD=X", "eurusd"),
        ("USDJPY=X", "usdjpy"),
        ("AUDUSD=X", "audusd"),
    ]
    for _ticker, _key in _pairs:
        try:
            _rate = yf.Ticker(_ticker).fast_info["last_price"]
            if _rate and float(_rate) > 0:
                set_metadata(f"last_{_key}_rate", str(float(_rate)))
        except Exception:
            pass


_FX_SESSION_TTL = 300  # seconds before session-state cache is considered stale


def _get_fx_rate(instrument_ccy: str, account_ccy: str) -> "tuple[float | None, str]":
    """Multi-level FX rate fetch. Returns (rate, source).

    source values:
      'session'       — in-memory session cache, still fresh (< 5 min)
      'live'          — freshly fetched from yfinance and cached
      'db:TIMESTAMP'  — DB fallback used because live fetch failed
      'unavailable'   — all sources (live + DB) failed
    """
    if instrument_ccy == account_ccy:
        return 1.0, "same_currency"

    pair_info = _CCY_PAIR_LOOKUP.get((instrument_ccy, account_ccy))
    if pair_info is None:
        return None, "unsupported"

    yf_ticker, invert = pair_info
    pair_key   = yf_ticker.replace("=X", "").lower()   # e.g. "gbpusd"
    sess_key   = f"_fx_{pair_key}"
    sess_ts    = f"_fx_{pair_key}_ts"

    # ── Level 1: session-state cache ──────────────────────────────────────
    if sess_key in st.session_state:
        age = (datetime.utcnow() - st.session_state[sess_ts]).total_seconds()
        if age < _FX_SESSION_TTL:
            return st.session_state[sess_key], "session"

    # ── Level 2: live fetch (primary ticker) ──────────────────────────────
    close = _fetch_fx_live(yf_ticker)

    # ── Level 2b: alternative ticker (GBP=X instead of GBPUSD=X) ─────────
    if close is None and yf_ticker.endswith("USD=X"):
        alt_ticker = yf_ticker[:-len("USD=X")] + "=X"   # e.g. "GBP=X"
        close = _fetch_fx_live(alt_ticker)

    if close is not None and close > 0:
        rate = (1.0 / close) if invert else close
        st.session_state[sess_key] = rate
        st.session_state[sess_ts]  = datetime.utcnow()
        set_metadata(f"last_{pair_key}_rate", str(rate))
        return rate, "live"

    # ── Level 3: DB fallback ───────────────────────────────────────────────
    db_rate, db_ts = _get_fx_db_fallback(pair_key)
    if db_rate is not None:
        return db_rate, f"db:{db_ts or 'unknown time'}"

    return None, "unavailable"


def _hex_rgba(hx: str, alpha: float) -> str:
    """Convert a 6-digit hex colour string to a CSS rgba() value."""
    h = hx.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{alpha})"


def _hmm_status_pill_html(regime_result: dict, font_size: str = "0.78rem") -> str:
    """Build the inline HMM confidence pill shown alongside the regime badge
    on the Daily Briefing status bar and the Signals page header block —
    see analysis/regime.py:detect_regime()'s 'hmm' dict and CLAUDE.md's HMM
    confidence gate section.

    Three cases:
    - Rule-based regime was TRENDING_BULL and HMM confirmed it: green
      "✓ HMM NN% confident".
    - Rule-based regime was TRENDING_BULL but HMM downgraded it to MIXED:
      amber "⚠ Downgraded from TRENDING_BULL (HMM NN% bull confidence)".
    - Any other regime — HMM is informational only, never a veto there:
      grey "HMM: NN% <state> (informational only)".

    Returns "" (nothing rendered) when hmm_enabled is false or the model
    hasn't been fit yet (hmm_model_available is False) — there is nothing
    meaningful to show in either case.
    """
    hmm = regime_result.get("hmm") or {}
    if not hmm.get("hmm_model_available"):
        return ""

    rule_regime = hmm.get("rule_regime", "")
    downgraded  = hmm.get("hmm_downgraded", False)
    dominant    = hmm.get("hmm_dominant_state", "UNCERTAIN")
    confidence  = hmm.get("hmm_confidence", 0.0) or 0.0
    bull_prob   = hmm.get("hmm_bull_probability", 0.0) or 0.0

    if downgraded:
        colour = "#f59e0b"
        text = f"⚠ Downgraded from {rule_regime.replace('_', ' ')} (HMM {bull_prob:.0%} bull confidence)"
    elif rule_regime == "TRENDING_BULL":
        colour = "#16a34a"
        text = f"✓ HMM {confidence:.0%} confident"
    else:
        colour = "#6b7280"
        text = f"HMM: {confidence:.0%} {dominant.lower()} (informational only)"

    return (
        f'<span style="color:{colour};font-size:{font_size};font-weight:600;'
        f'white-space:nowrap">{html.escape(text)}</span>'
    )



def _has_price_data() -> bool:
    """Return True if the price_data table contains at least one row."""
    from sqlalchemy import text as sa_text
    from data.database import get_engine
    try:
        with get_engine().connect() as conn:
            row = conn.execute(sa_text("SELECT 1 FROM price_data LIMIT 1")).fetchone()
        return row is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# System status helpers (used by _render_home_page)
# ---------------------------------------------------------------------------

_LEVEL_COLOUR = {
    "ok":   "#16a34a",
    "warn": "#f59e0b",
    "error":"#dc2626",
    "info": "#6366f1",
}


def _status_card(col, label: str, value: str, sub: str = "", level: str = "ok") -> None:
    """Render a colour-coded status card into a Streamlit column."""
    colour  = _LEVEL_COLOUR.get(level, "#6b7280")
    sub_html = (
        f'<div style="font-size:0.78rem;color:#64748b;margin-top:3px">'
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
    """Return (primary_value, sub_caption) strings for a stored timestamp.

    Primary is the human-readable timestamp; sub is the relative age so the
    user sees both the exact time and how long ago it was at a glance.
    """
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
    max_stale_trading_days = trading sessions since last data for the worst stale ticker
                             (0 when the stale list is empty).

    Uses pandas_market_calendars so that weekends and public holidays on the
    ticker's home exchange are not counted as missed trading days.
    """
    import pandas_market_calendars as mcal  # already a dependency via data/scheduler.py

    # Build ticker → exchange mapping from config watchlist groups.
    ticker_exchange: dict[str, str] = {}
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for entry in assets:
            if isinstance(entry, dict) and "ticker" in entry:
                ticker_exchange[entry["ticker"]] = entry.get("exchange", "NYSE")

    # Query latest stored date per ticker.
    try:
        from sqlalchemy import text as sa_text
        with get_engine().connect() as conn:
            rows = conn.execute(
                sa_text("SELECT ticker, MAX(date) AS latest FROM price_data GROUP BY ticker")
            ).fetchall()
        latest_by_ticker: dict[str, object] = {r[0]: r[1] for r in rows}
    except Exception:
        latest_by_ticker = {}

    # Map config exchange names → pandas_market_calendars calendar names.
    _MCAL_MAP: dict[str, str] = {
        "NYSE": "NYSE", "NASDAQ": "NASDAQ", "CBOE": "NYSE",
        "LSE": "LSE",
        "TSE": "JPX", "JPX": "JPX",       # config uses "TSE"; mcal uses "JPX"
        "XETR": "NYSE",                    # XETRA not universally available; NYSE as proxy
        "HKEX": "HKEX", "FX": "NYSE", "Crypto": "NYSE",
    }

    today = datetime.utcnow().date()
    _lookback = (datetime.utcnow() - timedelta(days=30)).date()
    _cal_sessions: dict[str, list] = {}  # cal_name → sorted list of session dates

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
                # Unrecognised or unavailable calendar — fall back to NYSE.
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
        """Count trading sessions strictly after last_dt on the given exchange."""
        cal_name = _MCAL_MAP.get(exchange, "NYSE")
        sessions = _get_sessions(cal_name)
        last_date = last_dt.date() if hasattr(last_dt, "date") else last_dt
        return sum(1 for d in sessions if d > last_date)

    _FRESH_TD = 3  # ≤ 3 trading days = fresh

    fresh, stale, missing = [], [], []
    max_stale_trading_days = 0

    for ticker, exchange in ticker_exchange.items():
        raw = latest_by_ticker.get(ticker)
        if raw is None:
            missing.append(ticker)
            continue
        # SQLite returns dates as strings or datetime objects depending on driver.
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
    """Parse one journalctl short-iso line into a dict with ts, level, message, raw.

    Handles the format:
      2024-06-22T10:30:45+0000 hostname investment-research[pid]: LEVEL:module:msg
    Returns None for lines that don't match (e.g. header lines from older sessions).
    """
    import re as _re  # noqa: PLC0415
    _PAT = _re.compile(
        r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})'   # ISO timestamp (no tz)
        r'[^\s]*\s+'                                    # timezone suffix + space
        r'\S+\s+'                                       # hostname
        r'[^\[]+\[\d+\]:\s*'                            # service[pid]:
        r'(.*)$'                                         # message
    )
    _m = _PAT.match(raw)
    if not _m:
        return None
    ts_raw, msg = _m.group(1), _m.group(2)
    ts = ts_raw.replace("T", " ")

    _lvl_m = _re.match(r'^(DEBUG|INFO|WARNING|ERROR|CRITICAL):', msg)
    level = _lvl_m.group(1) if _lvl_m else "INFO"
    # Strip "LEVEL:module:" prefix from the displayed message
    if _lvl_m:
        # e.g. "INFO:data.scheduler:text" → "text"
        _rest = msg[_lvl_m.end():]
        if ":" in _rest:
            _colon = _rest.index(":")
            msg = _rest[_colon + 1:]
        else:
            msg = _rest

    return {"ts": ts, "level": level, "message": msg, "raw": raw}


def _candlestick_chart(df: pd.DataFrame, ticker: str, name: str) -> go.Figure:
    """Build a Plotly candlestick + volume chart for a ticker."""
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

    # Overlay 20- and 50-day SMAs — compute on valid rows only so a single
    # NaN/zero close (e.g. an incomplete intraday bar) does not break the line.
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
        template="plotly_dark",
        height=500,
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
    )
    return fig


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
        template="plotly_dark",
        height=180,
        margin=dict(l=40, r=20, t=10, b=30),
        showlegend=False,
    )
    return fig


def _render_indicator_panel(ticker: str) -> None:
    """Fetch stored indicator values via raw SQL and render them as metric tiles.

    Using a raw SQL query (rather than ORM) avoids any SQLAlchemy column-mapping
    issues that can silently return None when the schema has evolved since the
    session mapper was compiled.

    Reads the most recent row from the indicators table for *ticker*. If no
    analysis has been run yet, shows a prompt to run it instead.
    """
    from sqlalchemy import text as sa_text
    from data.database import get_engine

    row = None
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                sa_text(
                    "SELECT * FROM indicators "
                    "WHERE ticker = :ticker "
                    "ORDER BY date DESC LIMIT 1"
                ),
                {"ticker": ticker},
            ).mappings().first()
    except Exception as exc:
        st.error(f"Could not read indicator data: {exc}")
        return

    def _v(key: str, fmt: str = "{:,.2f}", suffix: str = "") -> str:
        """Format a value from the result row, returning '—' for NULL / NaN."""
        val = row.get(key) if row else None
        if val is None:
            return "—"
        try:
            return fmt.format(float(val)) + suffix
        except Exception:
            return str(val) if val else "—"

    with st.expander("Technical Indicators", expanded=True):
        if row is None:
            st.info("Price data loaded — indicators will be calculated at the next analysis run.")
            return

        date_str = row.get("date")
        if date_str:
            try:
                st.caption(f"As of {pd.Timestamp(date_str).strftime('%Y-%m-%d')}")
            except Exception:
                st.caption(f"As of {date_str}")

        # Trend
        st.markdown("**Trend**")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("SMA 50",  _v("sma_50"))
        c2.metric("SMA 200", _v("sma_200"))
        c3.metric("MACD",    _v("macd", "{:,.3f}"))
        c4.metric("ADX",     _v("adx",  "{:,.1f}"))
        raw_cross = row.get("golden_cross")
        cross_int = int(float(raw_cross)) if raw_cross is not None else 0
        cross_label = {1: "Golden ✓", -1: "Death ✗", 0: "—"}.get(cross_int, "—")
        c5.metric("MA Cross", cross_label)
        raw_ma_score = row.get("ma_cross_score")
        ma_score_int = int(float(raw_ma_score)) if raw_ma_score is not None else cross_int
        if cross_int != 0 and ma_score_int != cross_int:
            slope_dir = "declining" if cross_int == 1 else "rising"
            if ma_score_int == 0:
                note = f"SMA50 {slope_dir} — cross weakening"
            else:
                flip = "death" if cross_int == 1 else "golden"
                note = f"SMA50 converging — {flip} cross imminent"
            c5.caption(f"⚠ {note}")

        # Mean Reversion
        st.markdown("**Mean Reversion**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("RSI (14)",    _v("rsi",         "{:,.1f}"))
        c2.metric("BB %B",       _v("bb_pct_b",    "{:,.2f}"))
        c3.metric("BB Width",    _v("bb_width",    "{:,.2f}"))
        c4.metric("Z-Score 52w", _v("z_score_52w", "{:+.2f}"))

        # Volatility
        st.markdown("**Volatility**")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("ATR (14)",    _v("atr",         "{:,.3f}"))
        c2.metric("ATR %",       _v("atr_pct",     "{:.2%}"))
        c3.metric("Hist Vol 20", _v("hist_vol_20", "{:.1%}"))
        vix_val   = row.get("vix_level")
        vix_reg   = row.get("vix_regime") or ""
        c4.metric("VIX", f"{float(vix_val):,.1f} ({vix_reg})" if vix_val is not None else "—")

        vol_regime = (row.get("vol_regime") or "NORMAL").upper()
        _vol_regime_colours = {
            "LOW": "#26a69a", "NORMAL": "#6b7280", "HIGH": "#f59e0b", "ELEVATED": "#ef5350",
        }
        _vr_colour = _vol_regime_colours.get(vol_regime, "#6b7280")
        c5.markdown(
            f"<div style='font-size:0.78rem;color:#94a3b8'>Vol Regime</div>"
            f"<div style='font-size:1.15rem;font-weight:600;color:{_vr_colour}'>{html.escape(vol_regime)}</div>",
            unsafe_allow_html=True,
        )

        if row.get("structural_break"):
            _days_since = row.get("days_since_change_point")
            _days_str = str(int(_days_since)) if _days_since is not None else "an unknown number of"
            st.markdown(
                f"<div style='color:#f59e0b;font-size:0.78rem;margin-top:2px'>"
                f"⚠ Regime shift detected {html.escape(_days_str)} days ago</div>",
                unsafe_allow_html=True,
            )

        # Volume
        st.markdown("**Volume**")
        c1, c2 = st.columns(2)
        c1.metric("Vol Ratio", _v("volume_ratio", "{:,.2f}", "x"))
        c2.metric("OBV Trend", _v("obv_trend",    "{:,.0f}"))

        # Macro (only shown when at least one value is present)
        yc_spread = row.get("yield_curve_spread")
        yc_regime = row.get("yield_curve_regime")
        if yc_spread is not None or vix_reg:
            st.markdown("**Macro**")
            c1, c2 = st.columns(2)
            c1.metric(
                "Yield Curve (10Y-2Y)",
                f"{float(yc_spread):+.2f}% ({yc_regime})" if yc_spread is not None else "—",
            )
            c2.metric(
                "VIX Regime",
                vix_reg.capitalize() if vix_reg else "—",
            )


# ---------------------------------------------------------------------------
# Signals page
# ---------------------------------------------------------------------------

# CSS applied to the Signal column cells via Pandas Styler (space-separated labels)
_SIGNAL_CSS: dict[str, str] = {
    "STRONG BUY":  "background-color:#1a5c38;color:white;font-weight:600",
    "BUY":         "background-color:#26a69a;color:white;font-weight:600",
    "NEUTRAL":     "font-weight:600",
    "SELL":        "background-color:#f59e0b;color:white;font-weight:600",
    "STRONG SELL": "background-color:#ef5350;color:white;font-weight:600",
}

_SCORE_SYMBOL: dict[int, str] = {1: "▲", 0: "●", -1: "▼"}


def _render_signals_page(config: dict, watchlist: dict[str, str], tradeable_set: set[str]) -> None:
    """Render the Signals page: regime card, generate button, ranked table."""

    st.markdown("## Market Signals")

    # ---- Opportunity flags & regime overlays (top of page) ----------------
    catch_up_flags  = get_active_opportunity_flags()
    overlay_raw     = get_metadata_str("active_overlays") or ""
    active_overlays = {k for k in overlay_raw.split(",") if k and k != "none"}

    # ---- Regime data -------------------------------------------------------
    regime_result = detect_regime()
    regime  = regime_result["regime"]
    colour  = regime_result["colour"]
    details = regime_result["details"]

    price   = details.get("price")
    sma_200 = details.get("sma_200")
    adx     = details.get("adx")
    vix     = details.get("vix")

    price_pct = f"{(price / sma_200 - 1) * 100:+.1f}% vs SMA200" if (price and sma_200) else "—"
    adx_str   = f"ADX {adx:.1f}" if adx else "—"
    vix_str   = f"VIX {vix:.1f}" if vix else "—"

    _REGIME_NOTES: dict[str, str] = {
        "TRENDING_BULL":   "Trend signals carry the most weight. Favour momentum and breakout plays — mean reversion signals are less reliable in a strong uptrend.",
        "TRENDING_BEAR":   "Trend signals carry the most weight. Favour short positions and breakdown plays — avoid buying dips unless confirmed by multiple modules.",
        "HIGH_VOLATILITY": "Volatility signals are most informative. Prioritise defensive positions and tight risk management — directional signals are less reliable when VIX is elevated.",
        "RANGING_LOW_VOL": "Mean reversion signals carry the most weight. Look for oversold bounces and range-bound plays — trend-following signals are less reliable in a sideways market.",
        "MIXED":           "Regime is unclear — signals are weighted equally across all modules. Treat all calls with extra caution.",
    }
    note = _REGIME_NOTES.get(regime, "")

    # ---- Signal rows & counts ----------------------------------------------
    rows = get_latest_signals(list(watchlist.keys()))

    if not rows:
        st.info("No signals yet — signals are generated automatically after each data refresh.")
        return

    _DIST_CFG = [
        ("STRONG BUY",  "#16a34a"),
        ("BUY",         "#26a69a"),
        ("NEUTRAL",     "#6b7280"),
        ("SELL",        "#f59e0b"),
        ("STRONG SELL", "#ef5350"),
    ]
    counts   = {lbl: 0 for lbl, _ in _DIST_CFG}
    t_counts = {lbl: 0 for lbl, _ in _DIST_CFG}
    for r in rows:
        lbl = (r.get("signal") or "NEUTRAL").replace("_", " ")
        if lbl in counts:
            counts[lbl] += 1
            if r.get("ticker") in tradeable_set:
                t_counts[lbl] += 1

    # ---- Build compact header block ----------------------------------------
    overlay_strips = ""
    for overlay_key in ("MACRO_STRESS", "VOL_REGIME_SHIFT", "CREDIT_STRESS", "RECESSION_RISK", "EM_STRESS"):
        if overlay_key not in active_overlays:
            continue
        meta   = OVERLAY_DESCRIPTIONS.get(overlay_key, {})
        _oc    = meta.get("colour", "#6b7280")
        _label = html.escape(meta.get("label", overlay_key.replace("_", " ")))
        _body  = html.escape(meta.get("text", ""))
        overlay_strips += (
            f"<div style='border-left:3px solid {_oc};padding:3px 10px;"
            f"background:{_hex_rgba(_oc, 0.10)};border-radius:0 3px 3px 0;margin-bottom:3px'>"
            f"<span style='background:{_oc};color:#fff;padding:0 5px;"
            f"border-radius:2px;font-size:0.64rem;font-weight:800'>REGIME OVERLAY</span>"
            f"<strong style='color:{_oc};font-size:0.8rem'> {_label}</strong>"
            f"<span style='color:#94a3b8;font-size:0.78rem'> — {_body}</span></div>"
        )

    _cu_colour = "#d97706"
    catch_up_strips = ""
    for flag in catch_up_flags:
        _td  = html.escape(flag.get("ticker", ""))
        _sd  = html.escape(flag.get("flag_source", ""))
        _dd  = html.escape(flag.get("description", ""))
        _z   = flag.get("z_score")
        _zs  = f"z={_z:.2f}" if _z is not None else ""
        _cr  = flag.get("pair_corr_30d")
        _cs  = f" | 30d corr {_cr:+.3f}" if _cr is not None else ""
        catch_up_strips += (
            f"<div style='border-left:3px solid {_cu_colour};padding:3px 10px;"
            f"background:{_hex_rgba(_cu_colour, 0.10)};border-radius:0 3px 3px 0;margin-bottom:3px'>"
            f"<span style='background:{_cu_colour};color:#fff;padding:0 5px;"
            f"border-radius:2px;font-size:0.64rem;font-weight:800'>CATCH-UP</span>"
            f"<strong style='color:#fbbf24;font-size:0.8rem'> {_td}</strong>"
            f"<span style='color:#64748b;font-size:0.78rem'> {_sd} {_zs}{_cs}</span>"
            f"<span style='color:#94a3b8;font-size:0.78rem'> — {_dd}</span></div>"
        )

    hmm_strip = ""
    _hmm = regime_result.get("hmm") or {}
    if _hmm.get("hmm_model_available"):
        _hmm_states = [
            ("BULL",      _hmm.get("hmm_bull_probability", 0.0) or 0.0,      "#16a34a"),
            ("BEAR",      _hmm.get("hmm_bear_probability", 0.0) or 0.0,      "#ef5350"),
            ("UNCERTAIN", _hmm.get("hmm_uncertain_probability", 0.0) or 0.0, "#6b7280"),
        ]
        _hmm_pills = "".join(
            f"<span style='background:{c}18;border:1px solid {c}55;color:{c};padding:1px 7px;"
            f"border-radius:3px;font-size:0.72rem;font-weight:700;white-space:nowrap;margin-right:4px'>"
            f"{lbl} {p:.0%}</span>"
            for lbl, p, c in _hmm_states
        )
        _hmm_rule = _hmm.get("rule_regime", "")
        _hmm_note = (
            f"downgraded {_hmm_rule.replace('_', ' ')} to MIXED — bull confidence below threshold"
            if _hmm.get("hmm_downgraded")
            else f"confirms dominant state {_hmm.get('hmm_dominant_state', 'UNCERTAIN')}"
        )
        hmm_strip = (
            "<div style='border-left:3px solid #6366f1;padding:3px 10px;"
            f"background:{_hex_rgba('#6366f1', 0.10)};border-radius:0 3px 3px 0;margin-bottom:3px'>"
            "<span style='background:#6366f1;color:#fff;padding:0 5px;"
            "border-radius:2px;font-size:0.64rem;font-weight:800'>HMM CONFIDENCE</span> "
            f"{_hmm_pills}"
            f"<span style='color:#94a3b8;font-size:0.78rem'>{html.escape(_hmm_note)}</span></div>"
        )

    intel_strips = overlay_strips + catch_up_strips + hmm_strip

    dist_pills = "".join(
        f"<div style='text-align:center;padding:5px 8px;border-radius:6px;"
        f"background:{fg}22;border:1px solid {fg}55;flex:1;min-width:72px'>"
        f"<div style='color:{fg};font-weight:700;font-size:1.3rem'>{counts[lbl]}</div>"
        f"<div style='color:#6b7280;font-size:0.62rem;font-weight:600'>{t_counts[lbl]} tradeable</div>"
        f"<div style='color:#64748b;font-size:0.68rem;margin-top:1px'>{lbl}</div>"
        f"</div>"
        for lbl, fg in _DIST_CFG
    )

    last_ts = get_metadata("last_analysis_run") or get_last_signal_timestamp()
    ts_str  = _fmt_uk(last_ts, "%Y-%m-%d %H:%M") if last_ts else "not yet generated"

    st.markdown(
        f"<div style='border:1px solid #334155;border-radius:8px;padding:10px 14px;margin-bottom:8px'>"
        + (f"<div style='margin-bottom:5px'>{intel_strips}</div>" if intel_strips else "")
        + f"<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:3px'>"
        + f"<span style='background:{colour};color:#fff;padding:2px 10px;border-radius:12px;"
        + f"font-size:0.82rem;font-weight:700'>{regime.replace('_', ' ')}</span>"
        + f"<span style='color:#6b7280;font-size:0.79rem'>^GSPC {html.escape(price_pct)}</span>"
        + f"<span style='color:#6b7280;font-size:0.79rem'>{adx_str}</span>"
        + f"<span style='color:#6b7280;font-size:0.79rem'>{vix_str}</span>"
        + f"</div>"
        + (f"<div style='font-size:0.82rem;color:#94a3b8;line-height:1.4;margin-bottom:6px'>"
           f"{html.escape(note)}</div>" if note else "")
        + f"<div style='display:flex;gap:5px;margin-bottom:5px'>{dist_pills}</div>"
        + f"<div style='font-size:0.71rem;color:#475569'>Signals: {html.escape(ts_str)}"
        + f" · updates at {_uk_sched_time(7,0)}, {_uk_sched_time(13,30)} &amp; {_uk_sched_time(21,30)}</div>"
        + f"</div>",
        unsafe_allow_html=True,
    )

    # ---- Generate Signals button (top of page, right of header) -----------
    _gs_col, _ = st.columns([2, 5])
    with _gs_col:
        if st.button("Generate Signals", key="signals_gen_btn", width="stretch"):
            with st.spinner("Regenerating signals…"):
                try:
                    _tickers = get_all_tickers(config)
                    run_analysis(tickers=_tickers, config=config)
                    generate_signals(tickers=_tickers, config=config)
                    st.cache_data.clear()
                    st.toast("Signals updated.", icon="✅")
                    st.rerun()
                except Exception as _exc:
                    st.error(f"Signal generation failed: {_exc}")

    # ---- Market intelligence -----------------------------------------------
    bullish_n   = counts["STRONG BUY"] + counts["BUY"]
    bearish_n   = counts["SELL"] + counts["STRONG SELL"]
    total_n     = sum(counts.values())
    t_bullish_n = t_counts["STRONG BUY"] + t_counts["BUY"]
    t_bearish_n = t_counts["SELL"] + t_counts["STRONG SELL"]
    t_total_n   = sum(t_counts.values())
    _intel: list[str] = []

    if total_n > 0:
        bull_pct = bullish_n / total_n
        bear_pct = bearish_n / total_n
        if bull_pct >= 0.6:
            _intel.append(
                f"{bullish_n} of {total_n} assets show buy signals ({t_bullish_n} tradeable) — "
                f"broad bullish breadth supports adding long exposure on short-term pullbacks."
            )
        elif bear_pct >= 0.6:
            _intel.append(
                f"{bearish_n} of {total_n} assets show sell signals ({t_bearish_n} tradeable) — "
                f"broad bearish breadth suggests reducing longs and favouring cash or short positions."
            )
        elif bull_pct > bear_pct:
            _intel.append(
                f"Signals lean bullish ({bullish_n} buys vs {bearish_n} sells across {total_n} assets, "
                f"{t_bullish_n} tradeable opportunities) "
                f"— selective long exposure is favoured, though broad momentum is not overwhelming."
            )
        elif bear_pct > bull_pct:
            _intel.append(
                f"Signals lean bearish ({bearish_n} sells vs {bullish_n} buys from {total_n} assets, "
                f"{t_bearish_n} tradeable) "
                f"— consider lightening longs and waiting for clearer confirmation before new entries."
            )
        else:
            _intel.append(
                f"Signals are evenly split ({bullish_n} buys, {bearish_n} sells across {total_n} assets, "
                f"{t_bullish_n} tradeable buys) "
                f"— the market lacks a clear directional bias; patience or range-bound strategies are preferred."
            )

    if vix:
        if vix >= 30:
            _intel.append(
                f"VIX at {vix:.1f} signals elevated fear. Historically, spikes above 30 mark "
                f"panic-driven selling — contrarian long opportunities may emerge, but strict "
                f"position sizing is critical."
            )
        elif vix >= 20:
            _intel.append(
                f"VIX at {vix:.1f} indicates above-average uncertainty — options premiums are "
                f"elevated, so factor heightened risk into position sizing."
            )
        elif vix < 15:
            _intel.append(
                f"VIX at {vix:.1f} signals complacency. Low-volatility regimes can persist, but "
                f"historically precede sharper corrections — avoid overleveraging."
            )

    if regime == "TRENDING_BULL" and adx:
        if adx >= 35:
            _intel.append(
                f"ADX at {adx:.1f} confirms a very strong trend — entries on short-term pullbacks "
                f"to the 20-day moving average typically offer better risk/reward than chasing new highs."
            )
        elif adx >= 25:
            _intel.append(
                f"ADX at {adx:.1f} confirms a sustained trend — look for pullback entries "
                f"rather than buying extended moves."
            )
    elif regime == "TRENDING_BEAR":
        _intel.append(
            "In a confirmed downtrend, rallies toward the 20-day moving average tend to be "
            "selling opportunities rather than dip-buying entry points."
        )
    elif regime == "RANGING_LOW_VOL":
        _intel.append(
            "Low-volatility ranging conditions favour mean reversion: buy near support, "
            "sell near resistance, and use tighter profit targets than in trending markets."
        )
    elif regime == "HIGH_VOLATILITY":
        _intel.append(
            "In high-volatility regimes, reduce standard position sizes by 30–50% to account "
            "for wider price swings, and prioritise assets with the strongest multi-module agreement."
        )

    if _intel:
        st.info(" ".join(_intel[:2]))

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Top picks ----------------------------------------------------------
    _MODULE_KEYS = [
        "trend_score", "mean_reversion_score", "volatility_score",
        "volume_score", "macro_score",
    ]

    # Require score ≥ 0.5 in the correct direction AND at least 3/5 modules
    # agreeing — ensures top picks represent genuine multi-module conviction,
    # not a single high-weight module carrying a weak or borderline signal.
    def _qualifies(r, direction: int) -> bool:
        score = r.get("confluence_score") or 0.0
        if direction == 1 and score < 0.5:
            return False
        if direction == -1 and score > -0.5:
            return False
        agreeing = sum(1 for k in _MODULE_KEYS if (r.get(k) or 0) == direction)
        return agreeing >= 3

    long_picks  = [r for r in rows           if _qualifies(r,  1) and r.get("ticker") in tradeable_set][:3]
    short_picks = [r for r in reversed(rows) if _qualifies(r, -1) and r.get("ticker") in tradeable_set][:3]

    lc, sc = st.columns(2)

    # Per-module plain-English tips: {score: (long_trade_tip, short_trade_tip)}
    # The long_tip is used when direction==1; short_tip when direction==-1.
    # Supporting tip = module agrees with trade direction; caution tip = module disagrees.
    _MTIPS: dict[str, dict[int, tuple[str, str]]] = {
        "trend_score": {
            1:  ("price is above key moving averages with confirmed momentum (ADX)",
                 "price is above moving averages — trend is working against this short"),
            -1: ("price is below key moving averages — trend is working against this long",
                 "price is below key moving averages with confirmed downside momentum"),
        },
        "mean_reversion_score": {
            1:  ("RSI and Bollinger Bands indicate oversold conditions — pullback entry opportunity",
                 "RSI/Bollinger Bands show oversold — short squeeze risk, exercise caution"),
            -1: ("RSI/Bollinger Bands indicate overbought — elevated pullback risk",
                 "RSI and Bollinger Bands indicate overbought conditions — mean reversion expected"),
        },
        "volatility_score": {
            1:  ("low and stable volatility — favourable entry conditions",
                 "low volatility — short positions carry more risk of sudden reversals"),
            -1: ("elevated volatility — widen stops and reduce position size",
                 "elevated volatility — can amplify downside moves, but also reversals"),
        },
        "volume_score": {
            1:  ("above-average volume is confirming the price move",
                 "above-average volume on the downside adds conviction to this short"),
            -1: ("below-average volume — the move lacks broad participation",
                 "low volume — limited downside participation, watch for a reversal"),
        },
        "macro_score": {
            1:  ("macro backdrop is supportive — favourable yield curve and VIX readings",
                 "macro backdrop is supportive, which may create headwinds for this short"),
            -1: ("macro headwinds present — monitor yield curve shape and VIX trend",
                 "macro headwinds are reinforcing the bearish case"),
        },
    }

    def _pick_card(container, picks, heading, card_fg):
        container.markdown(f"**{heading}**")
        if not picks:
            container.caption("No qualifying signals.")
            return
        for r in picks:
            ticker    = html.escape(r.get("ticker", ""))
            name      = html.escape(watchlist.get(r.get("ticker", ""), r.get("ticker", "")))
            if len(name) > 35:
                name = name[:33] + "…"
            score     = r.get("confluence_score") or 0.0
            signal    = (r.get("signal") or "NEUTRAL").replace("_", " ")
            direction = 1 if score > 0 else -1
            agreeing  = sum(1 for k in _MODULE_KEYS if (r.get(k) or 0) == direction)
            pct       = min(int(abs(score) * 100), 100)

            support_tips: list[str] = []
            caution_tips: list[str] = []
            for mk in _MODULE_KEYS:
                ms = r.get(mk) or 0
                if ms == 0:
                    continue
                tip_pair = _MTIPS.get(mk, {}).get(ms)
                if tip_pair is None:
                    continue
                long_tip, short_tip = tip_pair
                tip = long_tip if direction == 1 else short_tip
                if ms == direction:
                    support_tips.append(tip)
                else:
                    caution_tips.append(tip)

            support_html = (
                f'<div style="color:#6b7280;font-size:0.72rem;margin-top:5px">'
                f'✓ {" · ".join(support_tips[:2])}</div>'
            ) if support_tips else ""
            caution_html = (
                f'<div style="color:#f59e0b;font-size:0.72rem;margin-top:2px">'
                f'⚠ {" · ".join(caution_tips[:2])}</div>'
            ) if caution_tips else ""

            container.markdown(
                f'<div style="background:{card_fg}18;border-left:3px solid {card_fg};'
                f'padding:8px 12px;border-radius:4px;margin-bottom:8px">'
                f'<span style="font-weight:700;color:{card_fg}">{ticker}</span>'
                f'<span style="color:#64748b;font-size:0.8rem;margin-left:8px">{name}</span><br>'
                f'<span style="color:{card_fg};font-size:0.85rem">{signal}</span>'
                f'<span style="float:right;font-weight:700;color:{card_fg}">{score:+.0%}</span>'
                f'<div style="background:#374151;border-radius:2px;height:3px;margin-top:6px">'
                f'<div style="background:{card_fg};width:{pct}%;height:3px;border-radius:2px"></div>'
                f'</div>'
                f'<span style="color:#6b7280;font-size:0.7rem">{agreeing}/5 modules agree</span>'
                f'{support_html}'
                f'{caution_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

    _pick_card(lc, long_picks,  "Top Long Opportunities",  "#16a34a")
    _pick_card(sc, short_picks, "Top Short Opportunities", "#ef5350")

    st.divider()

    # ---- Full rankings table -----------------------------------------------
    st.markdown("### Asset Rankings")

    _col_cap, _col_tog = st.columns([3, 2])
    _col_cap.caption("▲ bullish  ●  neutral  ▼ bearish")
    _sig_filter = _col_tog.radio(
        "Show",
        ["Tradeable Only", "Show All"],
        horizontal=True,
        key="signals_filter",
        index=0,
        label_visibility="collapsed",
    )
    _show_all_assets = _sig_filter == "Show All"

    with st.expander("Module key — what each column means"):
        st.markdown(
            "| Column | What it measures | ▲ Bullish | ▼ Bearish |\n"
            "|---|---|---|---|\n"
            "| **Trend** | Moving averages (SMA 20/50/200) + ADX | Price above SMAs with confirmed momentum | Price below SMAs, downtrend confirmed |\n"
            "| **Mean Rev** | RSI (14) + Bollinger Bands | Oversold — bounce expected | Overbought — pullback expected |\n"
            "| **Volatility** | ATR + VIX levels | Low/stable volatility — favourable entry conditions | Elevated volatility — widen stops, reduce size |\n"
            "| **Volume** | Volume vs 20-day average | Above-average volume confirms the price move | Below-average — the move lacks conviction |\n"
            "| **Macro** | FRED series (yield curve, credit spreads, VIX) | Macro backdrop supportive | Macro headwinds present |\n\n"
            "**Score** = regime-weighted average of all modules (−1 to +1). "
            "In a trending market, Trend carries more weight; in a ranging market, Mean Rev carries more weight. "
            "STRONG BUY/SELL thresholds are regime-aware (0.55–0.70); see Daily Briefing for the current value. "
            "BUY ≥ 0.30 · SELL ≤ −0.30."
        )

    def _sym(val) -> str:
        return _SCORE_SYMBOL.get(int(val) if val is not None else 0, "●")

    n_tradeable_rows = sum(1 for r in rows if r.get("ticker") in tradeable_set)
    n_analysis_rows  = len(rows) - n_tradeable_rows

    records = []
    for r in rows:
        ticker = r.get("ticker", "")
        if not _show_all_assets and ticker not in tradeable_set:
            continue
        records.append({
            "Ticker":     ticker,
            "Name":       watchlist.get(ticker, ticker),
            "Signal":     (r.get("signal") or "NEUTRAL").replace("_", " "),
            "Score":      r.get("confluence_score") or 0.0,
            "Trend":      _sym(r.get("trend_score")),
            "Mean Rev":   _sym(r.get("mean_reversion_score")),
            "Volatility": _sym(r.get("volatility_score")),
            "Volume":     _sym(r.get("volume_score")),
            "Macro":      _sym(r.get("macro_score")),
            "Type":       "Tradeable" if ticker in tradeable_set else "Analysis only",
        })

    table_df = pd.DataFrame(records)

    def _style_signal(val: str) -> str:
        return _SIGNAL_CSS.get(val, "")

    def _style_score(val: float) -> str:
        if val > 0.1:  return "color:#16a34a;font-weight:bold"
        if val < -0.1: return "color:#dc2626;font-weight:bold"
        return ""

    def _style_module(val: str) -> str:
        if val == "▲": return "color:#16a34a;font-weight:bold"
        if val == "▼": return "color:#dc2626;font-weight:bold"
        return ""

    module_cols = ["Trend", "Mean Rev", "Volatility", "Volume", "Macro"]
    styler = (
        table_df.style
        .map(_style_signal, subset=["Signal"])
        .map(_style_score,  subset=["Score"])
        .map(_style_module, subset=module_cols)
        .map(lambda v: (
            "color:#16a34a;font-size:0.75rem;font-weight:600" if v == "Tradeable"
            else "color:#6b7280;font-size:0.75rem" if v == "Analysis only"
            else ""
        ), subset=["Type"])
        .format({"Score": "{:+.3f}"})
    )
    row_height_px = 35
    header_px     = 38
    st.dataframe(
        styler,
        width="stretch",
        hide_index=True,
        height=header_px + row_height_px * len(records),
    )
    if _show_all_assets:
        st.caption(f"Showing all {len(rows)} assets ({n_analysis_rows} analysis-only)")
    else:
        st.caption(
            f"Showing {n_tradeable_rows} tradeable assets"
            + (f" · {n_analysis_rows} analysis-only hidden" if n_analysis_rows else "")
        )


# ---------------------------------------------------------------------------
# Status page (also shown on Charts landing when no asset is selected)
# ---------------------------------------------------------------------------

def _pipeline_row(
    label: str,
    ts: "datetime | None",
    warn_h: float,
    error_h: float,
    desc: str,
    never_badge: str = "PENDING",
) -> str:
    """Return an HTML flex row for one pipeline step."""
    _COLOURS = {"ok": "#16a34a", "warn": "#f59e0b", "error": "#dc2626"}
    _BADGES  = {"ok": "OK", "warn": "STALE", "error": "OVERDUE"}

    if ts is None:
        colour   = "#94a3b8"
        ts_str   = "Never"
        age_str  = "—"
        badge    = html.escape(never_badge)
        badge_bg = "#94a3b820"
        badge_fg = "#94a3b8"
    else:
        _ts     = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
        hours   = (datetime.utcnow() - _ts).total_seconds() / 3600
        ts_str  = _fmt_uk(_ts, "%Y-%m-%d %H:%M")
        age_str = (
            f"{int(hours * 60)}m ago" if hours < 1
            else f"{int(hours)}h ago" if hours < 24
            else f"{hours / 24:.1f}d ago"
        )
        level    = _age_level(ts, warn_hours=warn_h, error_hours=error_h)
        colour   = _COLOURS[level]
        badge    = _BADGES[level]
        badge_bg = f"{colour}20"
        badge_fg = colour

    return (
        f'<div style="display:flex;align-items:center;padding:9px 0;'
        f'border-bottom:1px solid #f1f5f9;gap:0;font-size:0.88rem">'
        f'<div style="width:32px;flex-shrink:0;display:flex;justify-content:center;align-items:center">'
        f'<span style="display:block;width:10px;height:10px;border-radius:50%;background:{colour}"></span>'
        f'</div>'
        f'<div style="flex:0 0 18%;min-width:0;font-weight:600;color:#1e293b;padding-right:8px">'
        f'{html.escape(label)}</div>'
        f'<div style="flex:0 0 17%;min-width:0;color:#475569;font-size:0.82rem;padding-right:8px;white-space:nowrap">'
        f'{html.escape(ts_str)}</div>'
        f'<div style="flex:0 0 7%;min-width:0;color:#94a3b8;font-size:0.82rem;padding-right:8px;white-space:nowrap">'
        f'{html.escape(age_str)}</div>'
        f'<div style="flex:0 0 8%;min-width:0;padding-right:8px">'
        f'<span style="background:{badge_bg};color:{badge_fg};padding:2px 9px;border-radius:4px;'
        f'font-size:0.73rem;font-weight:700;letter-spacing:0.04em">{badge}</span></div>'
        f'<div style="flex:1;min-width:0;color:#64748b;font-size:0.82rem">'
        f'{html.escape(desc)}</div>'
        f'</div>'
    )


def _render_home_page(config: dict, watchlist: dict[str, str]) -> None:
    """System status panel — rendered as the Status page and as the Charts landing."""
    st.cache_data.clear()
    st.markdown("## System Status")

    # ── Collect data needed for both status rows ──────────────────────────
    deploy_ts, deploy_msg = _git_log_info()
    uptime                = _service_uptime()
    db_size_str, db_level = _db_size(config)

    last_refresh   = get_metadata("last_full_refresh") or get_metadata("last_refresh_run")
    last_analysis  = get_metadata("last_analysis_run")
    last_sched     = get_metadata("last_scheduled_run") or last_analysis
    last_sched_name = get_metadata_str("last_scheduled_run_name") or ""

    # ── Status grid row 1: system info ───────────────────────────────────
    sc1, sc2, sc3 = st.columns(3)
    _status_card(sc1, "Last Deployment", deploy_ts, deploy_msg, "info")
    _status_card(sc2, "Service Uptime",  uptime, "Process running", "ok")
    _status_card(sc3, "Database Size",   db_size_str, "", db_level)

    # ── Status grid row 2: scheduling health ─────────────────────────────
    st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
    pr1, pr2, pr3 = st.columns(3)

    # Last Data Refresh
    _ref_val, _ref_sub = _age_display(last_refresh)
    _status_card(pr1, "Last Data Refresh", _ref_val, _ref_sub,
                 _age_level(last_refresh, warn_hours=48.0, error_hours=96.0))

    # Last Scheduled Run — red when >26 h on a weekday (missed cycle indicator)
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

    # Next Scheduled Run — soonest upcoming job across all configured runs.
    # The pre_market slot is Monday Pre-Market (_monday_premarket_job — adds
    # stale weekend-order cancellation) on Mondays and the regular Pre-Market
    # job (_pre_market_job, Tue-Fri only) every other weekday; _next_run_for_time()
    # still finds the correct next weekday occurrence for either since a job
    # fires at this hour:minute every weekday, just a different function on Monday.
    _sched_cfg_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_LABELS = {"pre_market": "Pre-Market", "us_open": "US Open", "eod": "End of Day"}
    _nxt_dt, _nxt_label = None, ""
    for _rn, _rl in _RUN_LABELS.items():
        _rc = _sched_cfg_runs.get(_rn, {})
        _nt = _next_run_for_time(int(_rc.get("hour", 0)), int(_rc.get("minute", 0)))
        _rl_eff = "Monday Pre-Market" if (_rn == "pre_market" and _nt.weekday() == 0) else _rl
        if _nxt_dt is None or _nt < _nxt_dt:
            _nxt_dt, _nxt_label = _nt, _rl_eff
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

    # ── Collect pipeline data ─────────────────────────────────────────────
    last_fred      = get_metadata("last_fred_run")
    last_run_type  = get_metadata_str("last_run_type")
    active_overlays = get_metadata_str("active_overlays") or "none"
    fred_key       = bool(config.get("macro", {}).get("fred_api_key", ""))
    series_count   = len(config.get("macro", {}).get("fred_series", {}))

    fresh, stale, missing, _max_stale_td = _price_coverage(config)
    total = len(watchlist)

    try:
        from sqlalchemy import text as sa_text
        with get_engine().connect() as _conn:
            _cr = _conn.execute(
                sa_text(
                    "SELECT COUNT(*) AS cnt, "
                    "SUM(CASE WHEN divergence='DIVERGING' THEN 1 ELSE 0 END) AS divg, "
                    "MAX(created_at) AS last_ts "
                    "FROM correlations"
                )
            ).mappings().first()
            corr_cnt  = int(_cr["cnt"] or 0)
            corr_divg = int(_cr["divg"] or 0)

            _sr = _conn.execute(
                sa_text(
                    "SELECT COUNT(*) AS cnt, "
                    "SUM(CASE WHEN signal IN ('BUY','STRONG_BUY') THEN 1 ELSE 0 END) AS buys, "
                    "SUM(CASE WHEN signal IN ('SELL','STRONG_SELL') THEN 1 ELSE 0 END) AS sells "
                    "FROM signals WHERE date=(SELECT MAX(date) FROM signals)"
                )
            ).mappings().first()
            sig_cnt  = int(_sr["cnt"]  or 0)
            sig_buys = int(_sr["buys"] or 0)
            sig_sells= int(_sr["sells"]or 0)

            _mac = _conn.execute(
                sa_text("SELECT COUNT(DISTINCT series_id) AS cnt FROM macro_indicators")
            ).mappings().first()
            fred_stored = int(_mac["cnt"] or 0)

            _ind = _conn.execute(
                sa_text("SELECT COUNT(DISTINCT ticker) AS cnt FROM indicators")
            ).mappings().first()
            ind_tickers = int(_ind["cnt"] or 0)
    except Exception:
        corr_cnt = corr_divg = sig_cnt = sig_buys = sig_sells = fred_stored = ind_tickers = 0
        corr_ts = None

    regime_result  = detect_regime()
    regime_name    = str(regime_result["regime"]).replace("Regime.", "")
    overlay_list   = [k for k in (active_overlays or "").split(",") if k and k != "none"]

    # ── Price Data Refresh description ────────────────────────────────────
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

    # ── FRED description ──────────────────────────────────────────────────
    if not fred_key:
        fred_desc = (
            "No FRED API key — macro scoring uses VIX and stored data only. "
            "Add fred_api_key to config.yaml to enable full macro indicators."
        )
        fred_badge = "NO KEY"
    elif fred_stored == 0:
        fred_desc  = f"{series_count} series configured — not yet fetched (runs at EOD)"
        fred_badge = "PENDING"
    else:
        fred_desc  = f"{fred_stored} series stored · {series_count} configured"
        fred_badge = "OK" if last_fred else "PENDING"

    # ── Analysis description ──────────────────────────────────────────────
    run_label   = {"full": "Full Refresh", "analysis_only": "Analysis Only"}.get(last_run_type or "", "")
    analysis_desc = (
        f"{ind_tickers} tickers analysed"
        + (f" · {run_label}" if run_label else "")
    ) if ind_tickers else "Not yet run"

    # ── Correlations description ──────────────────────────────────────────
    corr_desc = (
        f"{corr_cnt} pairs computed · {corr_divg} DIVERGING"
        if corr_cnt else "Not yet computed"
    )

    # ── Signals description ───────────────────────────────────────────────
    sig_neutral = sig_cnt - sig_buys - sig_sells
    signals_desc = (
        f"{sig_cnt} signals · {sig_buys} BUY/STRONG_BUY · {sig_sells} SELL/STRONG_SELL"
        + (f" · {sig_neutral} NEUTRAL" if sig_neutral else "")
        if sig_cnt else "No signals generated yet"
    )

    # ── Regime description ────────────────────────────────────────────────
    regime_desc = regime_name
    if overlay_list:
        regime_desc += " · Overlays: " + ", ".join(overlay_list)
    else:
        regime_desc += " · No overlays active"

    # ── Render pipeline table ─────────────────────────────────────────────
    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)
    st.markdown("### Pipeline")

    _THEAD = (
        '<div style="font-size:0.88rem">'
        '<div style="display:flex;align-items:center;padding:4px 0 6px;border-bottom:2px solid #e2e8f0;'
        'color:#94a3b8;font-size:0.70rem;text-transform:uppercase;letter-spacing:0.07em">'
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
            "1 · Price Data Refresh",
            last_refresh,
            warn_h=48.0, error_h=96.0,
            desc=price_desc,
            never_badge="PENDING",
        ),
        _pipeline_row(
            "2 · FRED Macro Indicators",
            last_fred if fred_key else None,
            warn_h=48.0, error_h=168.0,
            desc=fred_desc,
            never_badge="NO KEY" if not fred_key else "PENDING",
        ),
        _pipeline_row(
            "3 · Technical Analysis",
            last_analysis,
            warn_h=48.0, error_h=72.0,
            desc=analysis_desc,
            never_badge="PENDING",
        ),
        _pipeline_row(
            "4 · Correlation Pairs",
            last_analysis,
            warn_h=48.0, error_h=72.0,
            desc=corr_desc,
            never_badge="PENDING",
        ),
        _pipeline_row(
            "5 · Signal Generation",
            last_analysis,
            warn_h=48.0, error_h=72.0,
            desc=signals_desc,
            never_badge="PENDING",
        ),
        _pipeline_row(
            "6 · Regime & Overlays",
            last_analysis,
            warn_h=48.0, error_h=72.0,
            desc=regime_desc,
            never_badge="PENDING",
        ),
    ])

    st.markdown(_THEAD + rows + _TFOOT, unsafe_allow_html=True)

    # ── Scheduled runs ────────────────────────────────────────────────────
    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)
    st.markdown("### Scheduled Runs")
    sched_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_DISPLAY = {
        "pre_market": ("Pre-Market",  "Tue–Fri: analysis only — Asian closed, European opening. "
                                       "Mon: Monday Pre-Market job runs instead — cancels stale "
                                       "weekend BUY orders first, then the same analysis-only run."),
        "us_open":    ("US Open",     "Analysis only — catches overnight moves before US open"),
        "eod":        ("End of Day",  "Full refresh + analysis after NYSE close"),
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

    # ── Data coverage ─────────────────────────────────────────────────────
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

    # ── Manual controls ───────────────────────────────────────────────────
    st.divider()
    st.markdown("### Manual Controls")
    st.caption("Trigger the analysis pipeline immediately without waiting for the next scheduled run.")
    _mc1, _mc2 = st.columns(2)

    with _mc1:
        if st.button("Run Analysis Now", width="stretch", type="primary",
                     key="status_run_analysis"):
            _cfg     = _load_config()
            _tickers = get_all_tickers(_cfg)
            with st.spinner("Running analysis pipeline…"):
                try:
                    run_analysis(tickers=_tickers, config=_cfg)
                    generate_signals(tickers=_tickers, config=_cfg)
                    st.success(
                        f"Analysis complete — signals updated at "
                        f"{_fmt_uk(datetime.utcnow(), '%H:%M')}."
                    )
                    st.cache_data.clear()
                except Exception as _exc:
                    st.error(f"Analysis failed: {_exc}")

    with _mc2:
        if st.button("Refresh Data + Analysis", width="stretch",
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
            with st.spinner("Fetching FRED macro indicators…"):
                try:
                    fetch_and_store_macro_indicators(_cfg)
                except Exception as _exc:
                    st.warning(f"FRED fetch failed (continuing): {_exc}")
            with st.spinner("Running analysis pipeline…"):
                try:
                    run_analysis(tickers=_tickers, config=_cfg)
                    generate_signals(tickers=_tickers, config=_cfg)
                    st.success(
                        f"Done — {_new_rows} new price rows · signals updated at "
                        f"{_fmt_uk(datetime.utcnow(), '%H:%M')}."
                    )
                    st.cache_data.clear()
                except Exception as _exc:
                    st.error(f"Analysis failed: {_exc}")

    # ── HMM Model ──────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### HMM Model")
    st.caption(
        "3-state Gaussian HMM regime confidence layer — validates (never replaces) "
        "the rule-based regime detection above. See analysis/hmm_regime.py."
    )

    try:
        from analysis.hmm_regime import get_hmm_regime_confidence as _get_hmm_conf
        _hmm_status = _get_hmm_conf(config)
    except Exception:
        _hmm_status = {"hmm_model_available": False}

    _hmm_last_fit = get_metadata("hmm_last_fit")
    _hmm_col1, _hmm_col2, _hmm_col3 = st.columns(3)
    _status_card(
        _hmm_col1, "Last Fit",
        _fmt_uk(_hmm_last_fit, "%Y-%m-%d %H:%M") if _hmm_last_fit else "Never",
        "Refits weekly (Sunday) or on demand",
        "ok" if _hmm_last_fit else "warn",
    )
    _status_card(
        _hmm_col2, "Training Days",
        str(_hmm_status.get("model_trained_on_days", 0)) if _hmm_status.get("hmm_model_available") else "—",
        "^GSPC daily history used for the current fit",
        "ok" if _hmm_status.get("hmm_model_available") else "warn",
    )
    if _hmm_status.get("hmm_model_available"):
        _hmm_probs_str = (
            f"BULL {_hmm_status['hmm_bull_probability']:.0%} · "
            f"BEAR {_hmm_status['hmm_bear_probability']:.0%} · "
            f"UNCERTAIN {_hmm_status['hmm_uncertain_probability']:.0%}"
        )
        _status_card(_hmm_col3, "Current State", _hmm_status.get("hmm_dominant_state", "—"), _hmm_probs_str, "info")
    else:
        _status_card(_hmm_col3, "Current State", "Not available",
                     "Model not yet fit — needs signals.hmm_min_training_days of ^GSPC history", "warn")

    if st.button("Refit HMM Now", key="status_refit_hmm"):
        with st.spinner("Refitting HMM…"):
            try:
                from analysis.hmm_regime import fit_hmm as _fit_hmm_now
                _model = _fit_hmm_now(_load_config())
                if _model is not None:
                    st.success("HMM model refit complete.")
                    st.cache_data.clear()
                else:
                    st.warning("Refit skipped — not enough ^GSPC history yet (see signals.hmm_min_training_days).")
            except Exception as _exc:
                st.error(f"HMM refit failed: {_exc}")

    # ── Email notification test ────────────────────────────────────────────
    notif_cfg = config.get("notifications", {}).get("email", {})
    st.divider()
    st.markdown("### Email Notifications")
    _from    = notif_cfg.get("from_address") or notif_cfg.get("smtp_user", "—")
    _to      = ", ".join(notif_cfg.get("to_addresses") or []) or "—"
    _relay   = f"{notif_cfg.get('smtp_host', 'smtp-relay.brevo.com')}:{notif_cfg.get('smtp_port', 587)}"
    _enabled = notif_cfg.get("enabled", False)
    st.caption(
        f"From: **{_from}** · To: **{_to}** · Via: {_relay} · "
        f"Alerts: {'**enabled**' if _enabled else 'disabled'}"
    )
    _btn_col1, _btn_col2 = st.columns(2)
    with _btn_col1:
        if st.button("Send Test Email", key="status_send_test_email"):
            _regime_str = detect_regime().get("regime", "MIXED")
            _open_count = len(_get_t212_invest_brief_cards(config))
            with st.spinner("Sending…"):
                _ok, _msg = send_test_email(config, _regime_str, last_analysis, _open_count)
            if _ok:
                st.success(_msg)
            else:
                st.error(_msg)
        st.caption("Confirms SMTP delivery is working.")
    with _btn_col2:
        if not st.session_state.get("_weekly_summary_confirm"):
            if st.button("Send Weekly Summary Now", key="status_send_weekly"):
                st.session_state["_weekly_summary_confirm"] = True
                st.rerun()
            st.caption("Sends the full weekly summary immediately — useful for testing.")
        else:
            notif_to = ", ".join(
                config.get("notifications", {}).get("email", {}).get("to_addresses") or []
            ) or "configured recipients"
            st.warning(f"This will send the weekly summary email to **{notif_to}** now.")
            _wc1, _wc2 = st.columns(2)
            with _wc1:
                if st.button("Confirm Send", type="primary", key="status_weekly_confirm_yes"):
                    st.session_state["_weekly_summary_confirm"] = False
                    with st.spinner("Gathering data and sending…"):
                        _ok, _msg = send_weekly_summary_email(config)
                    if _ok:
                        st.success(_msg)
                    else:
                        st.error(_msg)
            with _wc2:
                if st.button("Cancel", key="status_weekly_cancel"):
                    st.session_state["_weekly_summary_confirm"] = False
                    st.rerun()

    # ── T212 Invest Account ───────────────────────────────────────────────
    st.divider()
    st.markdown("### T212 Invest Account")

    _t212_cfg     = config.get("trading212_invest", {})
    _t212_enabled = _t212_cfg.get("enabled", False)
    _t212_live    = _t212_cfg.get("live", False)
    _t212_error   = get_metadata_str("t212_invest_error")
    _t212_has_key = bool(os.environ.get("T212_API_KEY", "")) and bool(os.environ.get("T212_API_SECRET", ""))

    if not _t212_enabled:
        st.info(
            "T212 Invest integration is **disabled**. "
            "Set `trading212_invest.enabled: true` in config.yaml (on the server) to activate. "
            "Add `T212_API_KEY` and `T212_API_SECRET` to the systemd service environment first."
        )
    else:
        _env_label  = "DEMO" if not _t212_live else "LIVE"
        _env_colour = "#f97316" if _t212_live else "#6366f1"
        st.markdown(
            f'<span style="background:{_env_colour};color:#fff;padding:2px 8px;'
            f'border-radius:4px;font-size:0.78rem;font-weight:600">{_env_label}</span> '
            f'max £{_t212_cfg.get("max_position_gbp", 500):,.0f}/order · '
            f'max {int(float(_t212_cfg.get("max_total_deployed", 0.5)) * 100)}% deployed',
            unsafe_allow_html=True,
        )
        _t212_regime = detect_regime()["regime"].value
        if is_entry_allowed(config, _t212_regime, "trading212_invest"):
            _t212_entry_eff = resolve_entry_signal(_t212_cfg, _t212_regime)
            _t212_entry_overridden = _t212_regime in _t212_cfg.get("entry_signal_overrides", {})
            st.caption(
                f"Entry threshold: **{_t212_entry_eff}** "
                f"({'regime override for ' if _t212_entry_overridden else 'global default, regime '}"
                f"{_t212_regime.replace('_', ' ')})"
            )
        else:
            st.caption(f"⛔ **Entry blocked** ({_t212_regime.replace('_', ' ')} regime not in allowed_entry_regimes)")

        if _t212_cfg.get("position_sizing", "fixed") == "var":
            st.caption(
                f"Position sizing: **VaR** "
                f"({int(float(_t212_cfg.get('var_confidence', 0.95)) * 100)}% confidence, "
                f"{int(_t212_cfg.get('var_lookback_days', 252))}-day lookback, "
                f"{float(_t212_cfg.get('max_risk_pct', 0.01)) * 100:.0f}% max risk — "
                f"floor £{float(_t212_cfg.get('min_position_gbp', 50)):.0f}, "
                f"ceiling £{float(_t212_cfg.get('max_position_gbp', 500)):.0f})"
            )
        else:
            st.caption(
                f"Position sizing: **Fixed** "
                f"(flat £{float(_t212_cfg.get('max_position_gbp', 500)):.0f} per order)"
            )
        st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)

        if _t212_error:
            _err_col, _err_btn_col = st.columns([8, 1])
            with _err_col:
                st.warning(f"Last API error: {_t212_error}")
            with _err_btn_col:
                st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
                if st.button("Clear", key="clear_t212_error"):
                    from data.database import set_metadata as _set_meta
                    _set_meta("t212_invest_error", "")
                    st.rerun()

        if not _t212_has_key:
            st.error(
                "API credentials not found. Set `T212_API_KEY` and `T212_API_SECRET` "
                "in the systemd service file and restart the service."
            )
        else:
            _tab_account, _tab_stocks, _tab_mapping = st.tabs(["Account", "Stocks", "Mapping"])

            with _tab_account:
                # ── Account data from scheduler snapshot (reads DB; no direct API calls) ──
                import json as _json  # noqa: PLC0415
                _snap_raw      = get_metadata_str("t212_account_snapshot")
                _pos_raw       = get_metadata_str("t212_positions_snapshot")
                _snap_fetched  = get_metadata_str("t212_snapshot_fetched_at")
                _pending_fetch = get_metadata_str("pending_t212_fetch")

                _t2_col_status, _t2_col_btn = st.columns([3, 1])
                with _t2_col_btn:
                    if st.button("Refresh Account", key="t212_refresh_btn"):
                        from data.database import set_metadata as _set_meta_t2  # noqa: PLC0415
                        _set_meta_t2("pending_t212_fetch", "1")
                        st.rerun()

                # Build _t2_data from stored JSON blobs
                _t2_data = None
                if _snap_raw:
                    try:
                        _t2_data = {
                            "summary":    _json.loads(_snap_raw),
                            "positions":  _json.loads(_pos_raw) if _pos_raw else [],
                            "fetched_at": _snap_fetched or "",
                            "error":      None,
                        }
                    except Exception as _je:
                        _t2_data = {
                            "error":      f"Snapshot parse error: {_je}",
                            "fetched_at": _snap_fetched or "",
                        }

                with _t2_col_status:
                    if _pending_fetch:
                        st.info("Refresh requested — updating within 60s.")
                    elif _snap_fetched:
                        try:
                            _age_s = (
                                datetime.utcnow() - datetime.fromisoformat(_snap_fetched)
                            ).total_seconds()
                            _age_str = (
                                f"{int(_age_s / 3600)}h ago" if _age_s >= 3600
                                else f"{int(_age_s / 60)}m ago" if _age_s >= 60
                                else f"{int(_age_s)}s ago"
                            )
                            st.caption(f"Last updated: {_age_str}")
                        except Exception:
                            pass

                if _t2_data is None:
                    st.caption("No account data yet — click Refresh Account to fetch.")
                elif _t2_data.get("error"):
                    st.error(f"Connection failed: {_t2_data['error']}")
                else:
                    _sum       = _t2_data["summary"]
                    _positions = _t2_data["positions"]
                    _fetched   = _t2_data.get("fetched_at", "")

                    # T212 account summary structure (v0 API):
                    # {"totalValue": 5000, "currency": "GBP",
                    #  "cash": {"availableToTrade": 5000, "reservedForOrders": 0, "inPies": 0},
                    #  "investments": {"currentValue": 0, "totalCost": 0,
                    #                  "realizedProfitLoss": 0, "unrealizedProfitLoss": 0}}
                    _cash_obj  = _sum.get("cash", {}) if isinstance(_sum.get("cash"), dict) else {}
                    _inv_obj   = _sum.get("investments", {}) if isinstance(_sum.get("investments"), dict) else {}
                    _total     = float(_sum.get("totalValue", 0) or 0)
                    _free      = float(_cash_obj.get("availableToTrade", 0) or 0)
                    _invested  = float(_inv_obj.get("currentValue", 0) or 0)
                    _result    = float(
                        (_inv_obj.get("unrealizedProfitLoss", 0) or 0) +
                        (_inv_obj.get("realizedProfitLoss",   0) or 0)
                    )
                    _deployed = (_invested / _total * 100) if _total else 0.0
                    _pos_count = len(_positions) if isinstance(_positions, list) else 0

                    _ta1, _ta2, _ta3, _ta4 = st.columns(4)
                    _ta1.metric("Account Value",   f"£{_total:,.2f}")
                    _ta2.metric("Available Cash",  f"£{_free:,.2f}")
                    _ta3.metric("Invested",        f"£{_invested:,.2f}",
                                 delta=f"{_deployed:.0f}% deployed")
                    _ta4.metric("Open Positions",  str(_pos_count),
                                 delta=f"P&L £{_result:+,.2f}" if _result else None)

                    # Deployed capital progress bar
                    _deploy_limit = float(_t212_cfg.get("max_total_deployed", 0.5))
                    _bar_pct      = min(1.0, (_invested / _total) if _total else 0.0)
                    _bar_colour   = "#dc2626" if _bar_pct >= _deploy_limit else (
                        "#f97316" if _bar_pct >= _deploy_limit * 0.85 else "#6366f1"
                    )
                    st.markdown(
                        f'<div style="margin:4px 0 2px;font-size:0.75rem;color:#64748b">'
                        f'Deployed: £{_invested:,.0f} of £{_total:,.0f} '
                        f'(limit {int(_deploy_limit * 100)}%)</div>'
                        f'<div style="background:#e2e8f0;border-radius:4px;height:6px">'
                        f'<div style="background:{_bar_colour};width:{_bar_pct * 100:.1f}%;'
                        f'height:6px;border-radius:4px"></div></div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

                    st.caption("Open positions are shown in the **Performance** and **Daily Briefing** pages.")

                    if _fetched:
                        try:
                            _fetched_disp = _fmt_uk(datetime.fromisoformat(_fetched))
                        except (ValueError, TypeError):
                            _fetched_disp = _fetched[:16].replace("T", " ")
                        st.caption(f"Updated {_fetched_disp}")

                # ── Recent T212 orders from our DB ────────────────────────────────────
                try:
                    from sqlalchemy import text as _sa_text
                    with get_engine().connect() as _t2_conn:
                        _ord_rows = _t2_conn.execute(_sa_text("""
                            SELECT id, ticker, t212_ticker, direction, quantity,
                                   estimated_value_gbp, var_pct, status, signal_type,
                                   confluence_score, regime, account_type,
                                   error_message, placed_at
                            FROM t212_invest_orders
                            ORDER BY placed_at DESC
                            LIMIT 10
                        """)).mappings().all()
                    _ord_data = [dict(r) for r in _ord_rows]
                except Exception:
                    _ord_data = []

                st.markdown("#### Recent Orders")
                if _ord_data:
                    _ord_df = pd.DataFrame([{k: v for k, v in r.items() if k != "id"} for r in _ord_data])
                    _ord_df["var_pct"] = _ord_df["var_pct"].map(
                        lambda v: f"{v * 100:.2f}%" if pd.notna(v) else "—"
                    )
                    _ord_df.columns = [
                        "Ticker", "UCITS Ticker", "Dir", "Qty",
                        "Est. Value (£)", "VaR at Entry", "Status", "Signal",
                        "Score", "Regime", "Account",
                        "Error", "Placed At",
                    ]
                    st.dataframe(
                        _ord_df.style.map(
                            lambda v: "color:#16a34a" if v == "FILLED"
                            else ("color:#d97706" if v == "PLACED"
                            else ("color:#dc2626" if v in ("FAILED", "CANCELLED", "REJECTED") else "")),
                            subset=["Status"],
                        ).map(
                            lambda v: "color:#16a34a" if v in ("BUY",)
                            else ("color:#dc2626" if v == "SELL" else ""),
                            subset=["Dir"],
                        ),
                        width="stretch", hide_index=True,
                    )

                    # ── Retry buttons for FAILED orders ──────────────────────────────
                    _failed_orders = [r for r in _ord_data if r.get("status") == "FAILED"]
                    if _failed_orders and _t212_has_key:
                        st.markdown("**Retry failed orders**")
                        for _fo in _failed_orders:
                            _fo_id      = _fo["id"]
                            _fo_ticker  = _fo["ticker"]
                            _fo_ucits   = _fo["t212_ticker"]
                            _fo_dir     = _fo["direction"]
                            _fo_qty     = float(_fo["quantity"] or 0)
                            _fo_err     = (_fo.get("error_message") or "")[:80]
                            _fo_placed_raw = _fo.get("placed_at") or ""
                            try:
                                _fo_placed = _fmt_uk(datetime.fromisoformat(_fo_placed_raw))
                            except (ValueError, TypeError):
                                _fo_placed = _fo_placed_raw[:16].replace("T", " ")

                            _rc1, _rc2, _rc3 = st.columns([5, 1, 1])
                            with _rc1:
                                st.caption(
                                    f"**{_fo_ticker}** → `{_fo_ucits}` | "
                                    f"{_fo_dir} {_fo_qty:.4f} | {_fo_placed} | "
                                    f"_{_fo_err}_"
                                )
                            with _rc3:
                                if st.button("Delete", key=f"delete_order_{_fo_id}"):
                                    from sqlalchemy import text as _sa_del_text
                                    with get_engine().begin() as _del_conn:
                                        _del_conn.execute(
                                            _sa_del_text("DELETE FROM t212_invest_orders WHERE id = :row_id"),
                                            {"row_id": _fo_id},
                                        )
                                    st.rerun()
                            with _rc2:
                                if st.button("Retry", key=f"retry_order_{_fo_id}"):
                                    with st.spinner(f"Placing {_fo_dir} order for {_fo_ticker}…"):
                                        try:
                                            import math as _math
                                            from brokers.trading212 import (
                                                Trading212Client as _T212Retry,
                                            )
                                            _rc_client = _T212Retry(live=_t212_live)

                                            # Apply minimum-quantity guard.
                                            # Always floor to integer — all ucits_mapping
                                            # instruments are EU/UK ETFs requiring whole shares.
                                            # T212's maxQuantityPrecision metadata is unreliable.
                                            _r_instruments = _rc_client.get_instruments()
                                            _r_imeta = {
                                                i["ticker"]: i
                                                for i in (_r_instruments or [])
                                                if "ticker" in i
                                            }
                                            _r_meta = _r_imeta.get(_fo_ucits, {})
                                            _r_min  = float(_r_meta.get("minTradeQuantity", 1.0))
                                            _r_qty  = _math.floor(_fo_qty)

                                            if _r_qty < _r_min:
                                                st.error(
                                                    f"Quantity {_r_qty} is below the instrument "
                                                    f"minimum of {_r_min} for {_fo_ucits}. "
                                                    f"Original qty {_fo_qty:.4f} was floored to {_r_qty}."
                                                )
                                            else:
                                                _r_signed = _r_qty if _fo_dir == "BUY" else -_r_qty
                                                _r_resp   = _rc_client.place_market_order(_fo_ucits, _r_signed)
                                                _r_order_id = str(_r_resp.get("id", ""))
                                                # Update the original FAILED row in-place so it
                                                # drops out of the retry list on next render.
                                                from sqlalchemy import text as _sa_retry_text
                                                with get_engine().begin() as _rc_conn:
                                                    _rc_conn.execute(_sa_retry_text("""
                                                        UPDATE t212_invest_orders
                                                        SET status        = 'PLACED',
                                                            order_id      = :order_id,
                                                            quantity      = :qty,
                                                            error_message = NULL,
                                                            placed_at     = :now
                                                        WHERE id = :row_id
                                                    """), {
                                                        "order_id": _r_order_id,
                                                        "qty":      _r_qty,
                                                        "now":      datetime.utcnow().isoformat(),
                                                        "row_id":   _fo_id,
                                                    })
                                                st.success(
                                                    f"{_fo_dir} {_r_qty} × {_fo_ucits} placed. "
                                                    f"Order ID: {_r_order_id or '?'}"
                                                )
                                                st.rerun()
                                        except Exception as _r_exc:
                                            st.error(f"Retry failed: {_r_exc}")
                else:
                    st.caption("No orders recorded yet.")


            with _tab_stocks:
                _stock_cfg = config.get("trading212_stocks", {})
                if not _stock_cfg.get("enabled", False):
                    st.info(
                        "T212 Stocks automation is **disabled**. "
                        "Set `trading212_stocks.enabled: true` in config.yaml (on the server) to activate."
                    )
                else:
                    _stock_regime = detect_regime()["regime"].value
                    if is_entry_allowed(config, _stock_regime, "trading212_stocks"):
                        _stock_entry_eff = resolve_entry_signal(_stock_cfg, _stock_regime)
                        _stock_overridden = _stock_regime in _stock_cfg.get("entry_signal_overrides", {})
                        st.caption(
                            f"Entry threshold: **{_stock_entry_eff}** "
                            f"({'regime override for ' if _stock_overridden else 'global default, regime '}"
                            f"{_stock_regime.replace('_', ' ')})"
                        )
                    else:
                        st.caption(f"⛔ **Entry blocked** ({_stock_regime.replace('_', ' ')} regime not in allowed_entry_regimes)")
                    st.caption(
                        f"max £{_stock_cfg.get('max_position_gbp', 200):,.0f}/order · "
                        f"max {int(float(_stock_cfg.get('max_total_deployed', 0.3)) * 100)}% deployed · "
                        f"allowed regimes: {', '.join(_stock_cfg.get('allowed_entry_regimes', []) or ['all'])}"
                    )

                    _stock_tickers = {
                        e["ticker"] for e in config.get("watchlist", {}).get("us_large_cap", [])
                        if isinstance(e, dict) and "ticker" in e
                    }
                    st.markdown("#### Recent Stock Orders")
                    if _stock_tickers:
                        try:
                            from sqlalchemy import text as _sa_stock_text
                            with get_engine().connect() as _stock_conn:
                                _stock_placeholders = ", ".join(f":t{i}" for i in range(len(_stock_tickers)))
                                _stock_params = {f"t{i}": t for i, t in enumerate(_stock_tickers)}
                                _stock_ord_rows = _stock_conn.execute(
                                    _sa_stock_text(f"""
                                        SELECT ticker, t212_ticker, direction, quantity,
                                               estimated_value_gbp, var_pct, status, signal_type,
                                               confluence_score, regime, account_type,
                                               error_message, placed_at
                                        FROM t212_invest_orders
                                        WHERE ticker IN ({_stock_placeholders})
                                        ORDER BY placed_at DESC
                                        LIMIT 10
                                    """),  # noqa: S608 — placeholders are :t0.. bind params, not interpolated values
                                    _stock_params,
                                ).mappings().all()
                            _stock_ord_data = [dict(r) for r in _stock_ord_rows]
                        except Exception:
                            _stock_ord_data = []

                        if _stock_ord_data:
                            _stock_ord_df = pd.DataFrame(_stock_ord_data)
                            _stock_ord_df["var_pct"] = _stock_ord_df["var_pct"].map(
                                lambda v: f"{v * 100:.2f}%" if pd.notna(v) else "—"
                            )
                            _stock_ord_df.columns = [
                                "Ticker", "T212 Ticker", "Dir", "Qty",
                                "Est. Value (£)", "VaR at Entry", "Status", "Signal",
                                "Score", "Regime", "Account",
                                "Error", "Placed At",
                            ]
                            st.dataframe(
                                _stock_ord_df.style.map(
                                    lambda v: "color:#16a34a" if v == "FILLED"
                                    else ("color:#d97706" if v == "PLACED"
                                    else ("color:#dc2626" if v in ("FAILED", "CANCELLED", "REJECTED") else "")),
                                    subset=["Status"],
                                ).map(
                                    lambda v: "color:#16a34a" if v in ("BUY",)
                                    else ("color:#dc2626" if v == "SELL" else ""),
                                    subset=["Dir"],
                                ),
                                width="stretch", hide_index=True,
                            )
                        else:
                            st.caption("No stock orders recorded yet.")
                    else:
                        st.caption("No tickers in watchlist.us_large_cap.")


            with _tab_mapping:
                _ucits_map = _t212_cfg.get("ucits_mapping", {})
                if _ucits_map:
                    st.caption(
                        "Automated T212 Invest orders are only placed for tickers listed here. "
                        "The UCITS Ticker is the T212 internal instrument ID used in API calls."
                    )
                    _ucits_rows = []
                    for _uk, _uv in _ucits_map.items():
                        _ut, _uc = _ucits_entry(_uv)
                        _ucits_rows.append({"US Ticker": _uk, "T212 UCITS Ticker": _ut, "Currency": _uc})
                    st.dataframe(
                        pd.DataFrame(_ucits_rows),
                        width="stretch", hide_index=True,
                    )
                else:
                    st.caption("No UCITS mapping configured.")
    # ── Scheduler Health ──────────────────────────────────────────────────
    st.divider()
    st.markdown("### Scheduler Health")

    try:
        import data.scheduler as _sched_mod  # noqa: PLC0415
        _aps        = getattr(_sched_mod, "_scheduler", None)
        _aps_running = _aps is not None and getattr(_aps, "running", False)
    except Exception:
        _aps        = None
        _aps_running = False

    # Check for the standalone daemon heartbeat (written every 60s by the
    # scheduler daemon process via _dispatch_pending_jobs).
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
        "scheduled_pre_market": ("Pre-Market",    f"{_uk_sched_time(7, 0)} · Mon–Fri"),
        "scheduled_us_open":    ("US Open",       f"{_uk_sched_time(13, 30)} · Mon–Fri"),
        "scheduled_eod":        ("End of Day",    f"{_uk_sched_time(21, 30)} · Mon–Fri"),
        "trade_alert_check":    ("Trade Alerts",  "Hourly 00–21, 23 · Mon–Fri"),
        "weekly_summary":       ("Weekly Summary",f"{_uk_sched_time(10, 0)} · Sunday"),
        "fomc_reaction":        ("FOMC Reaction", f"{_uk_sched_time(19, 5)} · Mon–Fri (FOMC days only)"),
        "job_dispatcher":       ("job_dispatcher","—"),
    }

    if _daemon_active:
        _aps_label  = "RUNNING"
        _mode_note  = f"(daemon · heartbeat {int(_daemon_age_s)}s ago)"
        _aps_colour = "#16a34a"
    elif _aps_running:
        _aps_label  = "RUNNING"
        _mode_note  = "(in-process)"
        _aps_colour = "#16a34a"
    else:
        _aps_label  = "STOPPED"
        _mode_note  = ""
        _aps_colour = "#dc2626"

    st.markdown(
        f'<div style="margin-bottom:10px">'
        f'<span style="font-size:0.82rem;color:#64748b">Scheduler: </span>'
        f'<span style="font-size:0.82rem;font-weight:700;color:{_aps_colour}">{_aps_label}</span>'
        + (f'<span style="font-size:0.75rem;color:#94a3b8;margin-left:6px">{html.escape(_mode_note)}</span>' if _mode_note else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    if _aps_running and _aps is not None:
        # In-process scheduler: show live next-run times from APScheduler.
        _aps_jobs   = _aps.get_jobs()
        _now_utc_tz = datetime.now(timezone.utc)
        _sh_rows: list[str] = []
        for _job in _aps_jobs:
            _jlabel, _jsched = _JOB_LABELS.get(_job.id, (_job.id, "—"))
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

            # Overdue flag for EOD: last_full_refresh > 26 h on a weekday
            if _job.id == "scheduled_eod":
                _ref_age = (
                    (datetime.utcnow() - last_refresh.replace(tzinfo=None)).total_seconds() / 3600
                    if last_refresh else float("inf")
                )
                if _ref_age > 26 and datetime.utcnow().weekday() < 5:
                    _dot, _badge = "#dc2626", "OVERDUE"

            _sh_rows.append(
                f'<div style="display:flex;padding:5px 0;border-bottom:1px solid #f1f5f9;'
                f'align-items:center;font-size:0.78rem">'
                f'<div style="flex:0 0 22%;font-weight:600">{html.escape(_jlabel)}</div>'
                f'<div style="flex:0 0 28%;color:#64748b;font-size:0.73rem">{html.escape(_jsched)}</div>'
                f'<div style="flex:0 0 25%">{html.escape(_nrt_str)}</div>'
                f'<div style="flex:0 0 12%;color:#64748b">{html.escape(_until_str)}</div>'
                f'<div style="flex:0 0 13%;color:{_dot};font-weight:700">{html.escape(_badge)}</div>'
                f'</div>'
            )
        _sh_head = (
            '<div style="font-size:0.82rem">'
            '<div style="display:flex;padding:3px 0 5px;border-bottom:2px solid #e2e8f0;'
            'color:#94a3b8;font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em">'
            '<div style="flex:0 0 22%">Job</div>'
            '<div style="flex:0 0 28%">Schedule</div>'
            '<div style="flex:0 0 25%">Next Run</div>'
            '<div style="flex:0 0 12%">In</div>'
            '<div style="flex:0 0 13%">Status</div>'
            '</div>'
        )
        st.markdown(_sh_head + "".join(_sh_rows) + "</div>", unsafe_allow_html=True)
    elif _daemon_active:
        # Daemon mode: show static schedule from config; next-run times computed
        # via APScheduler trigger objects (no running scheduler required).
        from apscheduler.triggers.cron import CronTrigger as _CronTrigger  # noqa: PLC0415
        from apscheduler.triggers.interval import IntervalTrigger as _ItvTrigger  # noqa: PLC0415
        _cfg_s  = _load_config()["scheduler"]
        _tz_s   = _cfg_s["timezone"]
        _now_tz = datetime.now(timezone.utc)

        # Static job definitions mirroring scheduler.py
        _static_jobs: list[tuple[str, str, str, object]] = []
        for _rn, _rc in _cfg_s.get("runs", {}).items():
            _sid = f"scheduled_{_rn}"
            _jlabel, _jsched = _JOB_LABELS.get(_sid, (_sid, "—"))
            _trigger = _CronTrigger(
                hour=_rc["hour"], minute=_rc["minute"],
                day_of_week="mon-fri", timezone=_tz_s,
            )
            _static_jobs.append((_sid, _jlabel, _jsched, _trigger))

        _extra_static = [
            ("job_dispatcher",    "job_dispatcher",    "—",                          _ItvTrigger(seconds=60)),
            ("trade_alert_check", "Trade Alerts",      "Hourly 00–21, 23 · Mon–Fri", _CronTrigger(hour="0-21,23", minute=0, day_of_week="mon-fri", timezone=_tz_s)),
            ("weekly_summary",    "Weekly Summary",    f"{_uk_sched_time(10, 0)} · Sunday",         _CronTrigger(hour=10, minute=0, day_of_week="sun", timezone=_tz_s)),
            ("fomc_reaction",     "FOMC Reaction",     f"{_uk_sched_time(19, 5)} · Mon–Fri (FOMC days only)", _CronTrigger(hour=19, minute=5, day_of_week="mon-fri", timezone=_tz_s)),
        ]
        _static_jobs.extend(_extra_static)

        _sh_rows_d: list[str] = []
        for _sid, _jlabel, _jsched, _trigger in _static_jobs:
            if _jlabel == "job_dispatcher":
                continue  # internal — don't show
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
            if _sid == "scheduled_eod":
                _ref_age2 = (
                    (datetime.utcnow() - last_refresh.replace(tzinfo=None)).total_seconds() / 3600
                    if last_refresh else float("inf")
                )
                if _ref_age2 > 26 and datetime.utcnow().weekday() < 5:
                    _dot, _badge = "#dc2626", "OVERDUE"

            _sh_rows_d.append(
                f'<div style="display:flex;padding:5px 0;border-bottom:1px solid #f1f5f9;'
                f'align-items:center;font-size:0.78rem">'
                f'<div style="flex:0 0 22%;font-weight:600">{html.escape(_jlabel)}</div>'
                f'<div style="flex:0 0 28%;color:#64748b;font-size:0.73rem">{html.escape(_jsched)}</div>'
                f'<div style="flex:0 0 25%">{html.escape(_nrt_str)}</div>'
                f'<div style="flex:0 0 12%;color:#64748b">{html.escape(_until_str)}</div>'
                f'<div style="flex:0 0 13%;color:{_dot};font-weight:700">{html.escape(_badge)}</div>'
                f'</div>'
            )
        _sh_head_d = (
            '<div style="font-size:0.82rem">'
            '<div style="display:flex;padding:3px 0 5px;border-bottom:2px solid #e2e8f0;'
            'color:#94a3b8;font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em">'
            '<div style="flex:0 0 22%">Job</div>'
            '<div style="flex:0 0 28%">Schedule</div>'
            '<div style="flex:0 0 25%">Next Run</div>'
            '<div style="flex:0 0 12%">In</div>'
            '<div style="flex:0 0 13%">Status</div>'
            '</div>'
        )
        st.markdown(_sh_head_d + "".join(_sh_rows_d) + "</div>", unsafe_allow_html=True)
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
            "Category", ["All", "Errors Only", "Scheduler", "Data", "Analysis", "Trades"],
            key="status_log_filter", label_visibility="collapsed",
        )
    with _lc3:
        _log_auto_refresh = st.toggle("Auto-refresh", key="status_log_auto_refresh")

    # Read last 200 lines from journalctl
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
        # Parse and filter
        _FILTER_KEYWORDS: dict[str, frozenset[str]] = {
            "Errors Only": frozenset(),   # handled separately by level
            "Scheduler":   frozenset({"scheduler", "pre-market", "us-open", "eod",
                                      "analysis starting", "pipeline complete",
                                      "weekly summary", "fomc", "catch-up",
                                      "missed eod", "trade alert", "scheduled_"}),
            "Data":        frozenset({"pull_incremental", "yfinance", "new rows", "fred",
                                      "price data", "data pull", "incremental",
                                      "ingestion", "rows across"}),
            "Analysis":    frozenset({"run_analysis", "generate_signals",
                                      "compute_all_pairs", "analysis pipeline",
                                      "indicators", "signals", "confluence", "regime"}),
            "Trades":      frozenset({"stop loss", "exit signal",
                                      "alert email", "pnl", "open trade", "alerted"}),
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

        # Display newest-first
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
                f'<span style="color:#475569">{html.escape(_p["ts"])}</span>'
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

        # Download buttons
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

    # Auto-refresh for log viewer only (opt-in via toggle).
    if _log_auto_refresh:
        import time as _time_mod  # noqa: PLC0415
        _time_mod.sleep(30)
        st.rerun()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _initialise() -> None:
    """Ensure the DB schema is current and run any first-session setup.

    init_db() is called on every render because it is idempotent — create_all()
    is a no-op when tables exist and _apply_migrations() skips columns that are
    already present. This guarantees schema changes are picked up immediately
    after a code update without requiring a Streamlit session restart.

    The scheduler is started at module load via _start_scheduler_at_startup()
    (a st.cache_resource call), so jobs fire even if nobody has visited the
    page since the last server restart. This function handles only the
    once-per-session setup that requires session_state (FX pre-fetch, startup
    analysis on fresh installs).
    """
    init_db()
    if "app_initialised" not in st.session_state:
        try:
            _prefetch_fx_rates()
        except Exception as exc:
            logger.warning("FX rate pre-fetch failed: %s", exc)

        # First-run HMM fit — fires in a background thread so it never blocks
        # the page load; a full EM fit can take several seconds and there's
        # no need for the user to wait on it (get_hmm_regime_confidence()'s
        # own uniform-probability fallback is a safe placeholder in the
        # meantime — see analysis/hmm_regime.py). Only fires when no cached
        # model exists yet — the weekly scheduler job keeps it fresh after
        # that.
        if get_metadata_str("hmm_model_params") is None:
            try:
                import threading  # noqa: PLC0415
                from analysis.hmm_regime import fit_hmm  # noqa: PLC0415
                _cfg = _load_config()
                threading.Thread(
                    target=fit_hmm, args=(_cfg,), name="hmm-startup-fit", daemon=True,
                ).start()
            except Exception as exc:
                logger.warning("Could not start background HMM fit: %s", exc)

        # Run analysis on startup only on the very first run (last_analysis_run is None),
        # i.e. a fresh install where price data exists but has never been analysed.
        # For all subsequent startups the scheduler keeps analysis current — blocking
        # the page load to reprocess identical data is not worthwhile.
        if _has_price_data() and get_metadata("last_analysis_run") is None:
            try:
                _cfg = _load_config()
                _tickers = get_all_tickers(_cfg)
                run_analysis(tickers=_tickers, config=_cfg)
                generate_signals(tickers=_tickers, config=_cfg)
                ts = _fmt_uk(datetime.utcnow(), '%H:%M')
                st.session_state["_startup_msg"] = ("ok", f"Initial analysis complete — {ts}")
                logger.info("First-run startup analysis complete.")
            except Exception as exc:
                logger.warning("Startup analysis failed: %s", exc)
                st.session_state["_startup_msg"] = ("err", f"Startup analysis failed: {exc}")

        st.session_state["app_initialised"] = True


# ---------------------------------------------------------------------------
# Relationships page
# ---------------------------------------------------------------------------

_DIV_COLOURS = {
    "CONFIRMING": "#16a34a",
    "DIVERGING":  "#ef5350",
    "NEUTRAL":    "#6b7280",
}
_DIV_ICONS = {
    "CONFIRMING": "✓",
    "DIVERGING":  "⚠",
    "NEUTRAL":    "●",
}


def _render_relationships_page(config: dict) -> None:
    """Render the Relationships page: divergence narratives, pair table, correlation heatmap."""
    from analysis.correlations import RELATIONSHIP_MAP as _REL_MAP
    _corr_ts  = get_metadata("last_analysis_run")
    _ts_str   = _fmt_uk(_corr_ts, "%Y-%m-%d %H:%M") if _corr_ts else "pending"
    _n_pairs  = len(_REL_MAP)
    st.caption(
        f"**Cross-Asset Relationships** &nbsp;·&nbsp; {_n_pairs} pairs &nbsp;·&nbsp; "
        f"Last computed: {_ts_str} &nbsp;·&nbsp; "
        f"Updates: {_uk_sched_time(7,0)} / {_uk_sched_time(13,30)} / {_uk_sched_time(21,30)}"
    )

    stored = get_stored_correlations()
    _has_data = bool(stored) and any(p.get("corr_30d") is not None for p in stored)

    # ── Plain-English divergence narratives (most actionable — shown first) ───
    st.markdown("### Notable Relationships")

    if not _has_data:
        st.info(f"No correlation data yet — analysis runs automatically at {_uk_sched_time(7,0)}, {_uk_sched_time(13,30)} and {_uk_sched_time(21,30)}.")
    else:
        diverging = [p for p in stored if p.get("divergence") == "DIVERGING"]
        confirming_notable = [
            p for p in stored
            if p.get("divergence") == "CONFIRMING"
            and p.get("z_score") is not None
            and abs(p["z_score"]) <= 0.5
        ]

        if diverging:
            st.markdown("**Unusual / Diverging Pairs**")
            for p in diverging:
                z   = p.get("z_score")
                c30 = p.get("corr_30d")
                z_str  = f"Z={z:+.2f}" if z is not None else ""
                c_str  = f"30d corr: {c30:+.3f}" if c30 is not None else ""
                st.markdown(
                    f'<div style="border-left:3px solid #ef5350;background:#ef535018;'
                    f'padding:8px 14px;border-radius:0 6px 6px 0;margin-bottom:6px">'
                    f'<span style="color:#ef5350;font-weight:700">⚠ {html.escape(p["label"])}</span>'
                    f'<span style="color:#64748b;font-size:0.8rem;margin-left:10px">'
                    f'{html.escape(c_str)} · {html.escape(z_str)}</span><br>'
                    f'<span style="font-size:0.88rem">{html.escape(p["diverge_template"])}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.success("No significant divergences detected — all tracked relationships are within historical norms.")

        if confirming_notable:
            st.markdown("**Strongly Confirming Pairs**")
            for p in confirming_notable:
                c30   = p.get("corr_30d")
                c_str = f"30d corr: {c30:+.3f}" if c30 is not None else ""
                st.markdown(
                    f'<div style="border-left:3px solid #16a34a;background:#16a34a18;'
                    f'padding:8px 14px;border-radius:0 6px 6px 0;margin-bottom:6px">'
                    f'<span style="color:#16a34a;font-weight:700">✓ {html.escape(p["label"])}</span>'
                    f'<span style="color:#64748b;font-size:0.8rem;margin-left:10px">'
                    f'{html.escape(c_str)}</span><br>'
                    f'<span style="font-size:0.88rem">{html.escape(p["confirm_template"])}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── Pair-level table ──────────────────────────────────────────────────────
    st.markdown("### Tracked Pair Details")
    st.caption(
        "30d Corr: current 30-day rolling correlation · "
        "90d Corr: 90-day rolling · "
        "Norm: historical average · "
        "Z: standard deviations from the historical norm · "
        "Status: CONFIRMING / NEUTRAL / DIVERGING"
    )

    if not _has_data:
        st.info(f"No correlation data yet — analysis runs automatically at {_uk_sched_time(7,0)}, {_uk_sched_time(13,30)} and {_uk_sched_time(21,30)}.")
    else:
        records = []
        for p in stored:
            corr_30 = p.get("corr_30d")
            corr_90 = p.get("corr_90d")
            hist_m  = p.get("hist_mean")
            z       = p.get("z_score")
            div     = p.get("divergence", "NEUTRAL")

            records.append({
                "Pair":       p["label"],
                "30d Corr":   corr_30,
                "90d Corr":   corr_90,
                "Norm":       hist_m,
                "Z-Score":    z,
                "Status":     div,
                "Score":      p.get("pair_score", 0),
                "Expected":   "↑↑ Positive" if p["expected_sign"] > 0 else "↑↓ Inverse",
            })

        pair_df = pd.DataFrame(records)

        def _style_status(val: str) -> str:
            colours = {
                "CONFIRMING": "background-color:#1a5c38;color:white;font-weight:600",
                "DIVERGING":  "background-color:#7f1d1d;color:white;font-weight:600",
                "NEUTRAL":    "color:#6b7280",
            }
            return colours.get(val, "")

        def _style_corr(val) -> str:
            if not isinstance(val, float):
                return ""
            if val > 0.5:   return "color:#16a34a"
            if val < -0.5:  return "color:#ef5350"
            return ""

        def _style_zscore(val) -> str:
            if not isinstance(val, float):
                return ""
            if abs(val) > 2: return "color:#ef5350;font-weight:bold"
            if abs(val) > 1: return "color:#f59e0b"
            return ""

        def _style_score(val) -> str:
            if val == 1:  return "color:#16a34a;font-weight:bold"
            if val == -1: return "color:#ef5350;font-weight:bold"
            return ""

        styler = (
            pair_df.style
            .map(_style_status, subset=["Status"])
            .map(_style_corr,   subset=["30d Corr", "90d Corr", "Norm"])
            .map(_style_zscore, subset=["Z-Score"])
            .map(_style_score,  subset=["Score"])
            .format({
                "30d Corr": lambda v: f"{v:+.3f}" if isinstance(v, float) else "—",
                "90d Corr": lambda v: f"{v:+.3f}" if isinstance(v, float) else "—",
                "Norm":     lambda v: f"{v:+.3f}" if isinstance(v, float) else "—",
                "Z-Score":  lambda v: f"{v:+.2f}" if isinstance(v, float) else "—",
                "Score":    lambda v: {1: "▲ +1", 0: "● 0", -1: "▼ -1"}.get(v, "—"),
            })
        )
        st.dataframe(
            styler,
            width="stretch",
            hide_index=True,
            height=38 + 35 * len(records),
        )

    st.divider()

    # ── Correlation Proxy Mappings ────────────────────────────────────────────
    st.markdown("### Correlation Proxy Mappings")
    st.caption(
        "Assets not directly tracked in correlation pairs inherit a partial score from "
        "a related proxy asset. This ensures all assets receive a meaningful correlation "
        "weight rather than defaulting to zero."
    )

    from analysis.correlations import (
        CATEGORY_PROXY as _PROXY_MAP,
        _ALL_PAIR_TICKERS as _DIRECT_TICKERS,
    )

    # Build ticker → display name from watchlist config
    _wl_names: dict[str, str] = {}
    for _grp in config.get("watchlist", {}).values():
        if isinstance(_grp, list):
            for _wl_e in _grp:
                if isinstance(_wl_e, dict) and "ticker" in _wl_e:
                    _wl_names[_wl_e["ticker"]] = _wl_e.get("name", _wl_e["ticker"])

    # Proxy inheritance table — one row per CATEGORY_PROXY entry
    _proxy_rows = [
        {
            "Ticker":          _pt,
            "Name":            _wl_names.get(_pt, _pt),
            "Proxy Ticker":    _pp,
            "Proxy Name":      _wl_names.get(_pp, _pp),
            "Inherited Score": f"50% of {_pp} correlation score",
        }
        for _pt, _pp in sorted(_PROXY_MAP.items())
    ]
    st.dataframe(
        pd.DataFrame(_proxy_rows),
        width="stretch",
        hide_index=True,
        height=38 + 35 * len(_proxy_rows),
    )

    # Coverage breakdown — every watchlist asset classified
    st.markdown("#### Coverage — all watchlist assets")

    _cov_rows = []
    for _ct, _cn in _wl_names.items():
        if _ct in _DIRECT_TICKERS:
            _cov_rows.append({
                "Ticker":   _ct,
                "Name":     _cn,
                "Coverage": "DIRECT",
                "Detail":   "Appears in a tracked pair — most reliable score",
            })
        elif _ct in _PROXY_MAP:
            _pp2 = _PROXY_MAP[_ct]
            _cov_rows.append({
                "Ticker":   _ct,
                "Name":     _cn,
                "Coverage": "PROXY",
                "Detail":   f"via {_pp2} ({_wl_names.get(_pp2, _pp2)}) — 50% inherited",
            })
        else:
            _cov_rows.append({
                "Ticker":   _ct,
                "Name":     _cn,
                "Coverage": "NONE",
                "Detail":   "Correlation score defaults to 0",
            })

    _cov_df = pd.DataFrame(_cov_rows)
    _sort_key = {"DIRECT": 0, "PROXY": 1, "NONE": 2}
    _cov_df = (
        _cov_df.assign(_s=_cov_df["Coverage"].map(_sort_key))
        .sort_values(["_s", "Ticker"])
        .drop(columns="_s")
        .reset_index(drop=True)
    )

    _n_direct = int((_cov_df["Coverage"] == "DIRECT").sum())
    _n_proxy  = int((_cov_df["Coverage"] == "PROXY").sum())
    _n_none   = int((_cov_df["Coverage"] == "NONE").sum())
    st.caption(
        f"**{_n_direct}** direct &nbsp;·&nbsp; **{_n_proxy}** proxy &nbsp;·&nbsp; "
        f"**{_n_none}** unmapped &nbsp;·&nbsp; {len(_cov_df)} total watchlist assets"
    )

    def _style_coverage(val: str) -> str:
        return {
            "DIRECT": "background-color:#1a5c38;color:white;font-weight:600",
            "PROXY":  "background-color:#78350f;color:white;font-weight:600",
            "NONE":   "color:#6b7280",
        }.get(val, "")

    st.dataframe(
        _cov_df.style.map(_style_coverage, subset=["Coverage"]),
        width="stretch",
        hide_index=True,
        height=38 + 35 * len(_cov_df),
    )

    st.divider()

    # ── Correlation heatmap (detail view — at the bottom) ─────────────────────
    st.markdown("### Correlation Matrix (30-day window)")
    st.caption(f"Rolling 30-day pairwise correlation for all tracked assets · as of {_ts_str}")

    with st.spinner("Building correlation matrix…"):
        corr_df, tickers_in_matrix = build_correlation_matrix(window=30)

    if corr_df.empty:
        st.info(f"Insufficient price data to build the correlation matrix — data refreshes automatically at {_uk_sched_time(21,30)}.")
    else:
        z_vals    = corr_df.values.tolist()
        fig_heat  = go.Figure(
            data=go.Heatmap(
                z=z_vals,
                x=tickers_in_matrix,
                y=tickers_in_matrix,
                colorscale=[
                    [0.0,  "#ef5350"],
                    [0.5,  "#1f2937"],
                    [1.0,  "#16a34a"],
                ],
                zmin=-1,
                zmax=1,
                text=[[f"{v:.2f}" for v in row] for row in z_vals],
                texttemplate="%{text}",
                textfont={"size": 10},
                hoverongaps=False,
                hovertemplate="<b>%{y} vs %{x}</b><br>Correlation: %{z:.3f}<extra></extra>",
            )
        )
        fig_heat.update_layout(
            template="plotly_dark",
            height=500,
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis=dict(side="bottom", tickangle=-45),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig_heat, width="stretch")


# ---------------------------------------------------------------------------
# Charts landing (grouped asset cards)
# ---------------------------------------------------------------------------

_GROUP_LABELS = {
    "indices":               "Indices",
    "sector_etfs":           "Sector ETFs",
    "energy":                "Energy",
    "commodities":           "Commodities",
    "bonds":                 "Bonds",
    "volatility":            "Volatility",
    "international":         "International",
    "forex":                 "Forex",
    "crypto":                "Crypto",
    "volatility_complex":    "Volatility (Complex)",
    "tech_subsectors":       "Tech Sub-Sectors",
    "financial_subsectors":  "Financial Sub-Sectors",
    "healthcare_subsectors": "Healthcare Sub-Sectors",
    "international_expanded": "International (Expanded)",
    "real_assets":           "Real Assets",
    "fixed_income_expanded": "Fixed Income (Expanded)",
    "commodities_expanded":  "Commodities (Expanded)",
    "crypto_expanded":       "Crypto (Expanded)",
    "uk_etfs":               "UK ETFs (GBP)",
    "us_large_cap":          "US Large Cap Stocks",
}


def _render_charts_landing(config: dict) -> None:
    """Grouped asset cards shown on Charts before any ticker is selected."""
    from sqlalchemy import text as sa_text
    from data.database import get_engine

    st.markdown("## Charts")
    st.caption("Select an asset to view its price chart and indicators.")

    # Build signal lookup for badges
    _all_tickers_cl = [
        e["ticker"]
        for g in config.get("watchlist", {}).values()
        if isinstance(g, list)
        for e in g
        if isinstance(e, dict) and "ticker" in e
    ]
    _sig_rows_cl = get_latest_signals(_all_tickers_cl)
    _sig_map_cl: dict[str, str] = {
        r["ticker"]: (r.get("signal") or "NEUTRAL") for r in _sig_rows_cl
    }

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

    _SIG_CARD_ICONS = {
        "STRONG_BUY": "▲▲", "BUY": "▲", "NEUTRAL": "●",
        "SELL": "▼", "STRONG_SELL": "▼▼",
    }

    for group_key, group_label in _GROUP_LABELS.items():
        group = config["watchlist"].get(group_key, [])
        if not group:
            continue
        st.markdown(f"#### {group_label}")
        cols = st.columns(4)
        for i, entry in enumerate(group):
            ticker  = entry["ticker"]
            name    = entry["name"]
            _signal = _sig_map_cl.get(ticker, "NEUTRAL")
            _sym    = _SIG_CARD_ICONS.get(_signal, "●")
            _chg    = _chg_map_cl.get(ticker)
            _chg_str = f"  {_chg:+.2f}%" if _chg is not None else ""
            with cols[i % 4]:
                if st.button(
                    f"**{ticker}**  \n{name}  \n{_sym}{_chg_str}",
                    key=f"cl_{ticker}",
                    width="stretch",
                ):
                    st.session_state["selected_ticker"] = ticker
                    st.rerun()
        st.markdown("")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Daily Briefing page
# ---------------------------------------------------------------------------

def _render_trade_card(e: dict) -> None:
    """Render one enriched open trade as a styled HTML status card."""
    status  = e.get("status", "ON TRACK")
    colour  = _BRIEF_STATUS_COLOURS.get(status, "#6b7280")
    icon    = _BRIEF_STATUS_ICONS.get(status, "")
    bg      = _hex_rgba(colour, 0.08)

    ticker         = html.escape(str(e.get("ticker",    "")))
    _ucits         = e.get("ucits_ticker", "")
    ucits_suffix   = (f" <span style='color:#94a3b8;font-size:0.82rem;font-weight:400'>"
                      f"({html.escape(str(_ucits))})</span>"
                      if _ucits and _ucits != e.get("ticker") else "")
    direction      = html.escape(str(e.get("direction", "LONG")))
    days_held      = int(e.get("days_held") or 0)
    days_remaining = int(e.get("days_remaining") or 0)
    near_max_hold  = bool(e.get("near_max_hold"))
    _raw_max       = e.get("max_holding_days")
    max_hold_days  = int(_raw_max) if _raw_max is not None else None
    acct_name      = html.escape(str(e.get("account_name") or e.get("account_type") or ""))

    # ── P&L — GBP only, no raw secondary ─────────────────────────────────────
    unreal_pnl_gbp    = e.get("unreal_pnl_gbp")
    unreal_pct        = e.get("unreal_pct")
    price_unavailable = e.get("price_unavailable", e.get("live_price") is None)

    if price_unavailable:
        pnl_html = "⚠ Price unavailable"
        pnl_col  = "#f59e0b"
    elif unreal_pnl_gbp is not None and unreal_pct is not None:
        pnl_col  = "#16a34a" if unreal_pnl_gbp >= 0 else "#dc2626"
        pnl_html = html.escape(f"£{unreal_pnl_gbp:+.2f} ({unreal_pct:+.2f}%)")
    elif e.get("unreal_pnl") is not None and unreal_pct is not None:
        _upnl    = e["unreal_pnl"]
        pnl_col  = "#16a34a" if _upnl >= 0 else "#dc2626"
        pnl_html = html.escape(
            f"{_quote_ccy_sym(e.get('ticker') or '')}{_upnl:+.2f} ({unreal_pct:+.2f}%)"
        )
    else:
        pnl_html = "—"
        pnl_col  = "#6b7280"

    # ── Signal / score — delta only when non-zero ─────────────────────────────
    entry_sig = html.escape((e.get("entry_signal")   or "—").replace("_", " "))
    curr_sig  = html.escape((e.get("current_signal") or "—").replace("_", " "))
    e_score   = float(e.get("entry_score")   or 0)
    c_score   = float(e.get("current_score") or 0)
    delta     = c_score - e_score

    score_str = html.escape(f"Score: {e_score:+.3f} → {c_score:+.3f}")
    if abs(delta) >= 0.001:
        delta_col  = "#16a34a" if delta >= 0 else "#f59e0b"
        score_html = (
            f"{score_str}"
            f" <span style='color:{delta_col}'>{html.escape(f'(Δ {delta:+.3f})')}</span>"
        )
    else:
        score_html = score_str

    # ── SL / TP / live R:R ────────────────────────────────────────────────────
    # Prefer the stored trade-plan SL over the ATR-computed one
    _stored_sl = e.get("stop_loss_price")
    _atr_sl    = e.get("stop_loss")
    sl_price   = (float(_stored_sl) if _stored_sl is not None
                  else float(_atr_sl) if _atr_sl is not None else None)
    tp_price   = float(e["take_profit_price"]) if e.get("take_profit_price") is not None else None

    live_price  = e.get("live_price")
    entry_price = float(e.get("entry_price") or 0)
    _mult       = 1 if e.get("direction", "LONG") == "LONG" else -1
    live_rr     = None
    if sl_price is not None and live_price is not None and entry_price > 0:
        sl_risk = abs(entry_price - sl_price)
        if sl_risk > 0:
            live_rr = _mult * (live_price - entry_price) / sl_risk

    # ── Build compact dot-separated detail row ────────────────────────────────
    detail_parts = [
        f"Signal: {entry_sig} → <strong>{curr_sig}</strong>",
        score_html,
    ]
    if sl_price is not None:
        _sl_dist_pct = e.get("stop_distance_pct")
        _sl_label    = e.get("stop_label") or "ATR stop (fallback)"
        _sl_str = f"SL: {sl_price:,.4f}"
        if _sl_dist_pct is not None:
            _sl_str += f" (-{float(_sl_dist_pct):.1f}%)"
        _sl_str += f" · {_sl_label}"
        detail_parts.append(html.escape(_sl_str))
    if tp_price is not None:
        detail_parts.append(html.escape(f"TP: {tp_price:,.4f}"))
    if live_rr is not None:
        detail_parts.append(html.escape(f"R:R: {live_rr:+.2f}"))
    _entry_var_pct = e.get("entry_var_pct")
    if _entry_var_pct is not None:
        detail_parts.append(html.escape(f"VaR at entry: {_entry_var_pct * 100:.2f}%"))
    if near_max_hold:
        detail_parts.append(
            f"<span style='color:#f59e0b;font-weight:700'>⏱ {days_remaining}d left</span>"
        )

    st.markdown(
        f"<div style='border-left:4px solid {colour};padding:10px 16px;"
        f"background:{bg};border-radius:0 6px 6px 0;margin-bottom:8px'>"
        f"<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:4px'>"
        f"<span style='background:{colour};color:#fff;padding:2px 9px;"
        f"border-radius:4px;font-size:0.72rem;font-weight:800;letter-spacing:0.05em'>"
        f"{icon} {status}</span>"
        f"<strong style='font-size:1rem'>{ticker}</strong>{ucits_suffix}"
        f"<span style='color:#64748b;font-size:0.85rem'>{direction} · "
        f"{'Day ' + str(days_held) + '/' + str(max_hold_days) if max_hold_days is not None else 'Day ' + str(days_held)}</span>"
        f"<span style='color:{pnl_col};font-weight:700;font-size:0.9rem'>{pnl_html}</span>"
        f"<span style='color:#64748b;font-size:0.78rem'>{acct_name}</span>"
        f"</div>"
        f"<div style='font-size:0.82rem;color:#94a3b8'>"
        f"{' · '.join(detail_parts)}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_trade_card_compact(e: dict) -> None:
    """Render one open T212 position as a single-line summary row.

    Used on the Daily Briefing, where full per-position detail (signal delta,
    SL/TP, live R:R) would crowd a page that already covers regime, macro,
    and opportunities — that detail lives on the Performance page's Open
    Positions section via the full _render_trade_card().
    """
    status = e.get("status", "ON TRACK")
    colour = _BRIEF_STATUS_COLOURS.get(status, "#6b7280")
    icon   = _BRIEF_STATUS_ICONS.get(status, "")
    bg     = _hex_rgba(colour, 0.08)

    ticker    = html.escape(str(e.get("ticker", "")))
    asset_tag = html.escape(str(e.get("asset_type", "")))
    days_held = int(e.get("days_held") or 0)

    unreal_pnl_gbp    = e.get("unreal_pnl_gbp")
    unreal_pct        = e.get("unreal_pct")
    price_unavailable = e.get("price_unavailable", e.get("live_price") is None)

    if price_unavailable:
        pnl_html = "⚠ Price unavailable"
        pnl_col  = "#f59e0b"
    elif unreal_pnl_gbp is not None and unreal_pct is not None:
        pnl_col  = "#16a34a" if unreal_pnl_gbp >= 0 else "#dc2626"
        pnl_html = html.escape(f"£{unreal_pnl_gbp:+.2f} ({unreal_pct:+.2f}%)")
    else:
        pnl_html = "—"
        pnl_col  = "#6b7280"

    # Stop-loss detail is only surfaced here when it's the active reason for
    # the badge — otherwise this stays a single line by design (see
    # docstring); full stop price/distance/label for every position lives on
    # the Performance page's full _render_trade_card().
    stop_html = ""
    if status == "STOP LOSS":
        _sl_price = e.get("stop_loss_price")
        _sl_dist  = e.get("stop_distance_pct")
        _sl_label = e.get("stop_label") or "ATR stop (fallback)"
        if _sl_price is not None:
            _sl_text = f"Stop: {_sl_price:,.4f}"
            if _sl_dist is not None:
                _sl_text += f" (-{float(_sl_dist):.1f}%)"
            _sl_text += f" · {_sl_label}"
            stop_html = (
                f"<span style='color:#dc2626;font-size:0.78rem;font-weight:600'>"
                f"{html.escape(_sl_text)}</span>"
            )

    st.markdown(
        f"<div style='border-left:4px solid {colour};padding:6px 14px;"
        f"background:{bg};border-radius:0 6px 6px 0;margin-bottom:6px;"
        f"display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        f"<span style='background:{colour};color:#fff;padding:1px 8px;"
        f"border-radius:4px;font-size:0.68rem;font-weight:800;letter-spacing:0.05em'>"
        f"{icon} {status}</span>"
        f"<strong style='font-size:0.92rem'>{ticker}</strong>"
        f"<span style='color:#64748b;font-size:0.78rem'>{asset_tag} · Day {days_held}</span>"
        f"<span style='color:{pnl_col};font-weight:700;font-size:0.85rem'>{pnl_html}</span>"
        f"{stop_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def _expected_last_eod(now: datetime) -> datetime:
    """Return the UTC datetime of the most recent EOD run that should have fired by now.

    The EOD run fires at 21:30 UTC Mon–Fri.  If today's run hasn't fired yet (or today
    is a weekend) we walk back to the previous weekday's slot.
    """
    eod_today = now.replace(hour=21, minute=30, second=0, microsecond=0)
    expected = eod_today if now >= eod_today else eod_today - timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    return expected


def _is_refresh_overdue(last_refresh: "datetime | None") -> "tuple[bool, bool]":
    """Return (is_warning, is_critical) based on the scheduled EOD run time.

    is_warning  — last refresh missed the expected EOD by more than 1 hour
    is_critical — last refresh missed the expected EOD by more than 25 hours (full day)
    Both return False on weekends (no EOD run is scheduled).
    """
    now = datetime.utcnow()
    if now.weekday() >= 5:
        return False, False
    if last_refresh is None:
        return True, True
    expected = _expected_last_eod(now)
    missed_by = (expected - last_refresh).total_seconds() / 3600
    return missed_by > 1, missed_by > 25


@st.cache_data(ttl=300)
def _system_health_check() -> list[dict]:
    """Check system health and return a list of alert dicts (level, message).

    Cached for 5 minutes to avoid DB / subprocess overhead on every page render.
    """
    alerts: list[dict] = []
    now = datetime.utcnow()
    today = now.date()
    is_weekday = today.weekday() < 5

    # 1. Data staleness — schedule-aware check against the expected EOD run time.
    #    A simple age_hours threshold causes false alarms because the EOD run fires at
    #    21:30 UTC and data will naturally be 18-23 h old during the following day.
    last_refresh = get_metadata("last_full_refresh")
    is_warning, is_critical = _is_refresh_overdue(last_refresh)
    if is_critical:
        last_str = _fmt_uk(last_refresh) if last_refresh else "never"
        alerts.append({
            "level": "red",
            "message": (
                f"Data refresh overdue — EOD run appears to have been missed "
                f"(last updated: {last_str})."
            ),
        })
    elif is_warning:
        alerts.append({
            "level": "amber",
            "message": "Data refresh missed — last EOD run did not complete as scheduled.",
        })

    # 2. Forex / crypto price freshness — flag only when data predates the most recent
    #    expected EOD run, not based on an absolute calendar-day count.
    _FC_TICKERS = ["GBPUSD=X", "USDJPY=X", "EURUSD=X", "AUDUSD=X", "BTC-USD", "ETH-USD"]
    if is_weekday:
        try:
            expected_date = _expected_last_eod(now).date()
            params = {f"t{i}": t for i, t in enumerate(_FC_TICKERS)}
            placeholders = ",".join(f":t{i}" for i in range(len(_FC_TICKERS)))
            with get_engine().connect() as _conn:
                rows = _conn.execute(
                    text(
                        f"SELECT ticker, MAX(date) AS last_date FROM price_data "
                        f"WHERE ticker IN ({placeholders}) GROUP BY ticker"
                    ),
                    params,
                ).fetchall()
            stale = [
                r[0] for r in rows
                if r[1] and date.fromisoformat(str(r[1])) < expected_date
            ]
            if stale:
                alerts.append({
                    "level": "red",
                    "message": (
                        f"Forex/crypto data overdue for {len(stale)} ticker(s): "
                        f"{', '.join(stale[:3])}{'…' if len(stale) > 3 else ''}."
                    ),
                })
        except Exception:
            pass

    # 3. Signal generation staleness
    last_signal = get_metadata("last_analysis_run")
    if last_signal and is_weekday:
        sig_age = (now - last_signal).total_seconds() / 3600
        if sig_age > 26:
            alerts.append({
                "level": "amber",
                "message": (
                    f"Signals not regenerated in {sig_age:.0f}h — analysis pipeline "
                    "may have failed."
                ),
            })

    # 4. EOD pull returned 0 rows — silent data failure (e.g. yfinance rate-limit)
    last_pull_rows_str = get_metadata_str("last_pull_rows")
    if last_pull_rows_str is not None and is_weekday and last_refresh:
        try:
            if int(last_pull_rows_str) == 0 and last_refresh.date() == today:
                alerts.append({
                    "level": "red",
                    "message": (
                        "Last EOD data pull returned 0 rows — refresh may have "
                        "silently failed."
                    ),
                })
        except ValueError:
            pass

    # 5. Recent ERROR lines in the systemd journal (Linux / server only)
    try:
        result = subprocess.run(
            [
                "journalctl", "-u", "investment-research",
                "--since", "2 hours ago",
                "--grep", "ERROR",
                "-q", "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            error_count = len(result.stdout.strip().splitlines())
            alerts.append({
                "level": "amber",
                "message": (
                    f"{error_count} ERROR log line(s) detected in the last 2 hours."
                ),
            })
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return alerts


def _render_system_health_banner() -> None:
    """Render dismissible amber/red system health alerts at the top of the page."""
    alerts = _system_health_check()
    if not alerts:
        return

    dismissed: set[str] = st.session_state.get("health_dismissed", set())
    active = [a for a in alerts if f"{a['level']}:{a['message']}" not in dismissed]
    if not active:
        return

    for i, alert in enumerate(active):
        alert_key = f"{alert['level']}:{alert['message']}"
        c_msg, c_nav, c_x = st.columns([15, 2, 1])
        with c_msg:
            if alert["level"] == "red":
                st.error(f"⚠️ {alert['message']}")
            else:
                st.warning(f"⚠️ {alert['message']}")
        with c_nav:
            if st.button(
                "View Status →",
                key=f"health_nav_{i}",
                width="stretch",
            ):
                st.session_state["page"] = "status"
                st.rerun()
        with c_x:
            if st.button(
                "✕",
                key=f"health_dismiss_{i}",
                help="Dismiss this alert for this session",
                width="stretch",
            ):
                dismissed.add(alert_key)
                st.session_state["health_dismissed"] = dismissed
                st.rerun()


def _ucits_entry(raw) -> "tuple[str, str]":
    """Extract (ucits_ticker, currency) from a ucits_mapping value.

    Handles both the legacy flat-string format ("I50Dl_EQ") and the new
    nested-dict format ({"ticker": "I50Dl_EQ", "currency": "USD"}).
    Defaults currency to "USD" for backward-compat with old configs.
    """
    if isinstance(raw, dict):
        return raw.get("ticker", ""), raw.get("currency", "USD")
    return str(raw), "USD"


def _t212_ccy_to_gbp(amount: float, currency: str) -> float:
    """Convert *amount* from the instrument's trading currency to GBP.

    Uses _get_fx_rate() for USD/EUR; GBX is handled as pence (÷100).
    Falls back to 1:1 when the rate is unavailable.
    """
    if not currency or currency == "GBP":
        return amount
    if currency == "GBX":
        return amount / 100.0
    rate, _ = _get_fx_rate(currency, "GBP")
    if rate and rate > 0:
        return amount / rate
    return amount  # fallback: return unconverted rather than zero


def _get_t212_invest_brief_cards(config: dict) -> list[dict]:
    """Return enriched trade-card dicts for open T212 Invest positions.

    Reads the scheduler-maintained ``t212_positions_snapshot`` from system_metadata
    (written by ``_fetch_t212_snapshot`` in the scheduler).  No direct T212 API
    calls are made here — the scheduler owns all outbound requests.  Covers both
    T212 automation paths — UCITS ETFs (trading212_invest.ucits_mapping) and
    direct US stocks (trading212_stocks + watchlist.us_large_cap) — since both
    write into the same live T212 portfolio and the same t212_invest_orders
    table. Each card carries ``"asset_type"``: ``"ETF"`` or ``"STOCK"``, so
    callers can render a type badge.
    """
    if not (os.environ.get("T212_API_KEY", "") and os.environ.get("T212_API_SECRET", "")):
        return []

    t212_cfg  = config.get("trading212_invest", {})
    stock_cfg = config.get("trading212_stocks", {})

    # Build a unified t212_ticker -> (research_ticker, asset_type) map across both
    # automation paths. ETF mapping takes priority on a ticker collision — the two
    # ticker universes are disjoint by design (see brokers.trading212._stock_universe).
    reverse_map: dict[str, tuple[str, str]] = {}
    ucits_currencies: dict[str, str] = {}

    if t212_cfg.get("enabled", False):
        for _rt, _raw in t212_cfg.get("ucits_mapping", {}).items():
            _ut, _ccy = _ucits_entry(_raw)
            reverse_map[_ut] = (_rt, "ETF")
            ucits_currencies[_ut] = _ccy

    if stock_cfg.get("enabled", False):
        for _entry in config.get("watchlist", {}).get("us_large_cap", []):
            if not isinstance(_entry, dict) or "ticker" not in _entry:
                continue
            _rt  = _entry["ticker"]
            _t2t = _entry.get("trading212_instrument", _rt)
            if _t2t not in reverse_map:
                reverse_map[_t2t] = (_rt, "STOCK")

    if not reverse_map:
        return []

    # Read positions from the scheduler-maintained DB snapshot (no direct API calls).
    import json as _json_brief  # noqa: PLC0415
    _pos_raw_brief = get_metadata_str("t212_positions_snapshot")
    t2_positions: list | None = None
    if _pos_raw_brief:
        try:
            t2_positions = _json_brief.loads(_pos_raw_brief) or []
        except Exception:
            pass

    if t2_positions is None:
        return []

    # Build a lookup: t212_ticker → position dict (for O(1) access)
    pos_by_t212 = {p.get("ticker", ""): p for p in (t2_positions or [])}

    # Load entry info (signal, score, fill date, currency) from t212_invest_orders
    try:
        from data.database import get_engine as _get_eng  # noqa: PLC0415
        from sqlalchemy import text as _sa_t              # noqa: PLC0415
        with _get_eng().connect() as _tc:
            _entry_rows = _tc.execute(_sa_t("""
                SELECT t212_ticker, signal_type, confluence_score, filled_at, currency_code, var_pct,
                       fill_price, stop_price, stop_distance_pct, vol_source
                FROM t212_invest_orders
                WHERE direction = 'BUY' AND status = 'FILLED'
                ORDER BY filled_at ASC
            """)).mappings().all()
        # Keep only the earliest fill per instrument ticker (= opening entry)
        entry_by_t212: dict[str, dict] = {}
        for _r in _entry_rows:
            _u = _r["t212_ticker"]
            if _u not in entry_by_t212:
                entry_by_t212[_u] = dict(_r)
    except Exception:
        entry_by_t212 = {}

    cards: list[dict] = []
    for t212_ticker, (research_ticker, asset_type) in reverse_map.items():
        pos = pos_by_t212.get(t212_ticker)
        if not pos:
            continue  # no open position for this mapping

        avg_price     = float(pos.get("averagePrice") or 0)
        qty           = float(pos.get("quantity")     or 0)
        current_price = float(pos.get("currentPrice") or 0)
        ppl           = float(pos.get("ppl")          or 0)

        if qty <= 0 or avg_price <= 0:
            continue

        unreal_pct = round((current_price - avg_price) / avg_price * 100, 2) if avg_price > 0 else 0.0

        entry_info    = entry_by_t212.get(t212_ticker, {})
        entry_signal  = entry_info.get("signal_type")  or "NEUTRAL"
        entry_score   = float(entry_info.get("confluence_score") or 0)
        entry_var_pct = entry_info.get("var_pct")  # None when placed under position_sizing: fixed

        # Stop loss — prefer the stored GARCH/ATR-fallback stop computed at
        # order time (analysis/volatility.py:compute_garch_stop()); fall back
        # to an on-the-fly ATR stop for positions opened before this feature
        # existed (stop_price is NULL for those rows).
        _stored_stop_price = entry_info.get("stop_price")
        if _stored_stop_price is not None:
            stop_loss_price    = float(_stored_stop_price)
            stop_distance_pct  = entry_info.get("stop_distance_pct")
            stop_label         = "GARCH stop" if entry_info.get("vol_source") == "garch" else "ATR stop (fallback)"
        else:
            try:
                from analysis.volatility import _atr_fallback_stop  # noqa: PLC0415
                _fallback = _atr_fallback_stop(research_ticker, avg_price, "LONG", config)
                stop_loss_price   = _fallback.get("stop_price")
                stop_distance_pct = _fallback.get("stop_distance_pct")
            except Exception:
                stop_loss_price   = None
                stop_distance_pct = None
            stop_label = "ATR stop (fallback)"

        # Resolve instrument currency: prefer DB-stored value (set at order time from T212 API),
        # then fall back to config ucits_mapping currency field (ETF only; stocks default USD).
        _db_ccy  = entry_info.get("currency_code")
        currency = _db_ccy or ucits_currencies.get(t212_ticker, "USD")

        # Convert position value and P&L from instrument currency to GBP.
        current_value_gbp = round(_t212_ccy_to_gbp(qty * current_price, currency), 2)
        pnl_gbp           = round(_t212_ccy_to_gbp(ppl, currency), 2)
        filled_at_str = entry_info.get("filled_at")

        entry_dt: datetime | None = None
        if filled_at_str:
            try:
                entry_dt = datetime.fromisoformat(str(filled_at_str))
            except Exception:
                pass
        days_held = max(0, (datetime.utcnow() - entry_dt).days) if entry_dt else 0

        try:
            sig_row       = get_current_signal(research_ticker)
            current_signal = sig_row.get("signal") or "NEUTRAL"
            current_score  = float(sig_row.get("confluence_score") or 0)
        except Exception:
            current_signal = "NEUTRAL"
            current_score  = 0.0

        # Status for buy-and-hold ETF/stock — mirrors the exit logic in
        # maybe_place_t212_orders() / maybe_place_stock_orders(): immediate exits
        # (SELL/STRONG_SELL), confirmed NEUTRAL, and backstop time limit. Reads
        # from whichever config section matches this card's asset_type.
        _section_cfg     = t212_cfg if asset_type == "ETF" else stock_cfg
        _default_hold    = 90 if asset_type == "ETF" else 30
        _t212_max_hold   = int(_section_cfg.get("max_holding_days", _default_hold))
        _exit_on_signal  = _section_cfg.get("exit_on_signal", True)
        _conf_runs       = int(config.get("signals", {}).get("reversal_confirmation_runs", 2))
        _conf_hours      = _conf_runs * 3
        _neutral_prefix  = "t212_neutral_since" if asset_type == "ETF" else "t212_stock_neutral_since"
        _neutral_key     = f"{_neutral_prefix}:{t212_ticker}"

        # Stop loss takes priority over signal status — mirrors the hourly
        # enforcement check in maybe_exit_t212_positions()/maybe_exit_stock_positions().
        if (stop_loss_price is not None and current_price > 0
                and current_price <= stop_loss_price):
            status = "STOP LOSS"
        elif current_signal in ("SELL", "STRONG_SELL"):
            status = "EXIT SIGNAL"
        elif _exit_on_signal and current_signal not in ("BUY", "STRONG_BUY"):
            # NEUTRAL — check whether the confirmation timer has elapsed
            try:
                from data.database import get_metadata_str as _gms  # noqa: PLC0415
                _first_neutral = _gms(_neutral_key)
            except Exception:
                _first_neutral = None
            if _first_neutral is not None:
                try:
                    _hours = (
                        datetime.utcnow() - datetime.fromisoformat(_first_neutral)
                    ).total_seconds() / 3600
                    status = "EXIT SIGNAL" if _hours >= _conf_hours else "WEAKENING"
                except Exception:
                    status = "WEAKENING"
            else:
                status = "WEAKENING"   # timer not yet started (first NEUTRAL run)
        elif days_held >= _t212_max_hold:
            status = "EXIT SIGNAL"     # backstop time limit
        else:
            status = "ON TRACK"

        days_remaining = max(0, _t212_max_hold - days_held)
        near_max_hold  = 0 < days_remaining <= 7
        status_priority = {"STOP LOSS": 3, "EXIT SIGNAL": 2, "WEAKENING": 1, "ON TRACK": 0}[status]

        cards.append({
            "ticker":            research_ticker,
            "ucits_ticker":      t212_ticker,
            "asset_type":        asset_type,
            "direction":         "LONG",
            "entry_price":       avg_price,
            "quantity":          qty,
            "entry_date":        entry_dt.isoformat() if entry_dt else None,
            "days_held":         days_held,
            "days_remaining":    days_remaining,
            "near_max_hold":     near_max_hold,
            "max_holding_days":  _t212_max_hold,
            "live_price":        current_price,
            "currency":          currency,
            "current_value_gbp": current_value_gbp,
            "unreal_pnl":        ppl,
            "unreal_pnl_gbp":    pnl_gbp,
            "unreal_pct":        unreal_pct,
            "price_unavailable": current_price == 0,
            "current_signal":    current_signal,
            "current_score":     current_score,
            "entry_signal":      entry_signal,
            "entry_score":       entry_score,
            "entry_var_pct":     entry_var_pct,
            "status":            status,
            "status_priority":   status_priority,
            "account_name":      "T212 Invest" if asset_type == "ETF" else "T212 Stocks",
            "account_type":      "T212_INVEST",
            "stop_loss_price":   stop_loss_price,
            "stop_loss":         None,
            "stop_distance_pct": stop_distance_pct,
            "stop_label":        stop_label,
            "take_profit_price": None,
        })

    return cards


def _render_briefing_page(config: dict, watchlist: dict[str, str], tradeable_set: set[str], t212_names: dict[str, str]) -> None:
    """Render the Daily Briefing: market overview, open trade monitor, opportunities."""
    st.markdown("## Daily Briefing")

    _render_system_health_banner()

    last_analysis = get_metadata("last_analysis_run")
    if last_analysis:
        st.caption(
            f"As of: {_fmt_uk(last_analysis, '%Y-%m-%d %H:%M')} · "
            f"Updates: {_uk_sched_time(7,0)}, {_uk_sched_time(13,30)} and {_uk_sched_time(21,30)}"
        )
    else:
        st.caption("No analysis run yet — updates run automatically on weekdays.")

    # ── Market Overview (compact status bar) ────────────────────────────────
    regime_result = detect_regime()
    regime  = regime_result["regime"]
    colour  = regime_result["colour"]
    details = regime_result["details"]
    price   = details.get("price")
    sma_200 = details.get("sma_200")
    adx     = details.get("adx")
    price_pct = f"^GSPC {(price/sma_200-1)*100:+.1f}% vs SMA200" if (price and sma_200) else ""
    adx_str   = f"ADX {adx:.1f}" if adx else ""

    ctx     = get_macro_context()
    vix_v   = ctx.get("vix")
    vix_col = (
        "#dc2626" if vix_v and vix_v > 30 else
        "#f59e0b" if vix_v and vix_v > 20 else
        "#16a34a"
    )
    vix_str = f"VIX {vix_v:.1f}" if vix_v else "VIX —"
    ys      = ctx.get("yield_spread")
    yr      = (ctx.get("yield_regime") or "—").lower()
    ys_str  = f"Yield {ys:+.2f}% ({yr})" if ys is not None else f"Yield: {yr}"
    dp      = ctx.get("dollar_pct")
    dt      = ctx.get("dollar_trend") or "—"
    usd_str = f"USD {dt} ({dp:+.1f}% vs SMA20)" if dp is not None else f"USD {dt}"

    overlay_raw     = get_metadata_str("active_overlays") or ""
    active_overlays = {k for k in overlay_raw.split(",") if k and k != "none"}

    _overlay_pills_html = ""
    for _ok in ("MACRO_STRESS", "VOL_REGIME_SHIFT", "CREDIT_STRESS", "RECESSION_RISK", "EM_STRESS"):
        if _ok not in active_overlays:
            continue
        _om = OVERLAY_DESCRIPTIONS.get(_ok, {})
        _oc = _om.get("colour", "#6b7280")
        _overlay_pills_html += (
            f'<span style="background:{_oc}20;border:1px solid {_oc}55;color:{_oc};'
            f'padding:1px 7px;border-radius:3px;font-size:0.72rem;font-weight:700;'
            f'white-space:nowrap">'
            f'{html.escape(_om.get("label", _ok.replace("_", " ")))}</span>'
        )

    st.markdown(
        f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;'
        f'border:1px solid #e2e8f0;border-radius:8px;padding:8px 14px;margin-bottom:4px">'
        f'<span style="background:{colour}18;border:1px solid {colour}44;color:{colour};'
        f'font-weight:700;padding:3px 10px;border-radius:14px;font-size:0.85rem;white-space:nowrap">'
        f'{regime.replace("_", " ")}</span>'
        + _hmm_status_pill_html(regime_result)
        + (f'<span style="color:#6b7280;font-size:0.8rem">{html.escape(price_pct)}</span>' if price_pct else '')
        + (f'<span style="color:#6b7280;font-size:0.8rem">{adx_str}</span>' if adx_str else '')
        + f'<span style="color:#cbd5e1;font-size:0.8rem">·</span>'
        f'<span style="color:{vix_col};font-size:0.82rem;font-weight:600">{html.escape(vix_str)}</span>'
        f'<span style="color:#6b7280;font-size:0.82rem">{html.escape(ys_str)}</span>'
        f'<span style="color:#6b7280;font-size:0.82rem">{html.escape(usd_str)}</span>'
        + (f'<span style="color:#cbd5e1;font-size:0.8rem">·</span>{_overlay_pills_html}' if _overlay_pills_html else '')
        + f'</div>',
        unsafe_allow_html=True,
    )
    st.caption(regime_result["description"])

    _buy_th, _sell_th = get_regime_thresholds(regime.value, config)
    _total_mods = config.get("confluence", {}).get("total_signal_modules", 7)
    _buy_mods  = round(_buy_th  * _total_mods)
    _sell_mods = round(_sell_th * _total_mods)
    if _buy_th == _sell_th:
        _thresh_label = (
            f"Threshold: {_buy_th:.2f} ({regime.replace('_', ' ')}) "
            f"— requires ~{_buy_mods}/{_total_mods} modules to agree"
        )
    else:
        _thresh_label = (
            f"Thresholds: Buy {_buy_th:.2f} (~{_buy_mods}/{_total_mods} modules) "
            f"· Sell {_sell_th:.2f} (~{_sell_mods}/{_total_mods} modules) "
            f"({regime.replace('_', ' ')})"
        )
    st.caption(_thresh_label)

    # Effective automation entry threshold for the current regime (per-regime
    # overrides in trading212_invest / trading212_stocks can differ from the
    # global default — see analysis/regime.py:resolve_entry_signal()). A regime
    # not in allowed_entry_regimes blocks entries entirely, ahead of the
    # threshold check — see analysis/regime.py:is_entry_allowed().
    def _automation_bit(cfg: dict, section: str, label: str) -> None:
        allowed = cfg.get("allowed_entry_regimes")
        note = f" ({'/'.join(allowed)} only)" if allowed else ""
        if is_entry_allowed(config, regime.value, section):
            eff = resolve_entry_signal(cfg, regime.value)
            overridden = regime.value in cfg.get("entry_signal_overrides", {})
            _auto_bits.append(f"{label} {eff}{' (override)' if overridden else ''}{note}")
        else:
            _auto_bits.append(f"{label} blocked{note}")
            _blocked_paths.append(label)

    _auto_bits = []
    _blocked_paths = []
    _t212_cfg_b = config.get("trading212_invest", {})
    if _t212_cfg_b.get("enabled", False):
        _automation_bit(_t212_cfg_b, "trading212_invest", "T212 ETF")
    _stock_cfg_b = config.get("trading212_stocks", {})
    if _stock_cfg_b.get("enabled", False):
        _automation_bit(_stock_cfg_b, "trading212_stocks", "T212 Stocks")
    if _auto_bits:
        st.caption(f"Automation entry: {' · '.join(_auto_bits)}")
    if _blocked_paths:
        st.caption(f"⛔ Entries blocked — {regime.replace('_', ' ')} regime ({', '.join(_blocked_paths)})")

    # Active overlay detail strips
    for overlay_key in ("MACRO_STRESS", "VOL_REGIME_SHIFT", "CREDIT_STRESS", "RECESSION_RISK", "EM_STRESS"):
        if overlay_key not in active_overlays:
            continue
        meta  = OVERLAY_DESCRIPTIONS.get(overlay_key, {})
        oc    = meta.get("colour", "#6b7280")
        olabel = html.escape(meta.get("label", overlay_key.replace("_", " ")))
        obody  = html.escape(meta.get("text", ""))
        obg    = _hex_rgba(oc, 0.10)
        st.markdown(
            f"<div style='border-left:4px solid {oc};padding:8px 14px;"
            f"background:{obg};border-radius:0 4px 4px 0;margin:6px 0'>"
            f"<span style='background:{oc};color:#fff;padding:1px 7px;"
            f"border-radius:3px;font-size:0.68rem;font-weight:800'>OVERLAY</span> "
            f"<strong style='color:{oc}'>{olabel}</strong>"
            f"<p style='font-size:0.82rem;line-height:1.5;margin:4px 0 0 0'>"
            f"{obody}</p></div>",
            unsafe_allow_html=True,
        )

    # ── Market Narrative (rule-based plain-English summary; see
    # output/briefing.py:_generate_daily_narrative()) — placed near the top
    # of the page, directly below the regime status bar. Reuses regime_result,
    # ctx and active_overlays already computed above; macro_dash and the
    # signal-distribution counts are re-fetched here under a "_narrative_"
    # prefix (cheap, read-only DB calls) since the page's own copies of
    # those aren't computed until further down. Open-position status comes
    # from _get_t212_invest_brief_cards(), the same helper the Open Trade
    # Monitor section uses further down. ────────────────────────────────────
    _narrative_macro_dash   = get_macro_dashboard(config)
    relationships_data      = get_stored_correlations()
    _narrative_all_signals  = get_latest_signals(list(watchlist.keys()))
    _narrative_trd_signals  = [s for s in _narrative_all_signals if s.get("ticker") in tradeable_set]

    def _narrative_count_signals(signals_list: list) -> dict:
        c = dict.fromkeys(["STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"], 0)
        for s in signals_list:
            lbl = (s.get("signal") or "NEUTRAL").replace("_", " ")
            if lbl in c:
                c[lbl] += 1
        return c

    _narrative_open_trades: list[dict] = _get_t212_invest_brief_cards(config)

    _narrative_macro_data = {**ctx, **_narrative_macro_dash}
    _narrative_signals = {
        "counts":           _narrative_count_signals(_narrative_all_signals),
        "tradeable_counts": _narrative_count_signals(_narrative_trd_signals),
        "total":            len(_narrative_all_signals),
        "tradeable_total":  len(_narrative_trd_signals),
    }

    _narrative_html = _generate_daily_narrative(
        regime_result,
        _narrative_macro_data,
        relationships_data,
        _narrative_signals,
        active_overlays,
        _narrative_open_trades,
        config,
    )
    st.markdown(
        "<div style='border:1px solid #e2e8f0;border-radius:8px;padding:14px 18px;margin:10px 0'>"
        "<div style='font-size:0.75rem;font-weight:700;color:#94a3b8;"
        "text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px'>Today's Briefing</div>"
        f"{_narrative_html}"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Key Events (FOMC / Fed calendar) ───────────────────────────────────
    try:
        from analysis.fomc import get_fomc_events  # noqa: PLC0415
        _fomc_events = get_fomc_events(days_ahead=7)
    except Exception:
        _fomc_events = []

    if _fomc_events:
        _EV_COLOURS = {
            "FOMC Decision":   "#dc2626",
            "FOMC Minutes":    "#f59e0b",
            "Fed Speech":      "#3b82f6",
            "Fed Announcement": "#6b7280",
        }
        _ev_parts: list[str] = []
        _caution_days: list[tuple[int, str]] = []   # (days_away, event_type)

        for ev in _fomc_events:
            ec      = _EV_COLOURS.get(ev["event_type"], "#6b7280")
            da      = ev["days_away"]
            da_str  = "Today" if da == 0 else ("Tomorrow" if da == 1 else f"in {da}d")
            _ev_parts.append(
                f'<span style="background:{ec}18;border:1px solid {ec}44;'
                f'border-radius:4px;padding:2px 9px;font-size:0.8rem;'
                f'color:{ec};font-weight:600;white-space:nowrap">'
                f'{html.escape(ev["event_type"])} &nbsp;'
                f'<span style="font-weight:400">{html.escape(ev["date"].strftime("%b") + " " + ev["date"].strftime("%d").lstrip("0"))} '
                f'({da_str})</span></span>'
            )
            if ev["event_type"] == "FOMC Decision" and da <= 2:
                _caution_days.append((da, ev["event_type"]))

        _ev_html = (
            '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;'
            'padding:6px 0 4px">'
            '<span style="font-size:0.75rem;font-weight:700;color:#94a3b8;'
            'text-transform:uppercase;letter-spacing:0.06em;white-space:nowrap">'
            'Key Events</span> '
            + " ".join(_ev_parts)
            + "</div>"
        )
        st.markdown(_ev_html, unsafe_allow_html=True)

        for days_away, ev_type in _caution_days:
            _when = "Today" if days_away == 0 else ("Tomorrow" if days_away == 1 else f"in {days_away} days")
            st.markdown(
                '<div style="border-left:4px solid #f59e0b;padding:7px 14px;'
                'background:#fef3c720;border-radius:0 4px 4px 0;margin:4px 0 6px">'
                f'<span style="background:#f59e0b;color:#fff;padding:1px 7px;'
                f'border-radius:3px;font-size:0.68rem;font-weight:800">CAUTION</span> '
                f'<strong style="color:#b45309">Fed Decision {_when}</strong>'
                f'<span style="font-size:0.82rem;color:#92400e"> — consider reducing '
                f'position sizes ahead of the announcement</span></div>',
                unsafe_allow_html=True,
            )

    # ── Top Long / Short Opportunities ─────────────────────────────────────
    all_signals = get_latest_signals(list(watchlist.keys()))

    # Ticker sets for overlay confidence warnings
    _EQ_BOND_GROUPS = {"indices", "sector_etfs", "bonds", "international"}
    _eq_bond_tickers: set[str] = {
        entry["ticker"]
        for gname, group in config.get("watchlist", {}).items()
        if gname in _EQ_BOND_GROUPS and isinstance(group, list)
        for entry in group
        if isinstance(entry, dict) and "ticker" in entry
    }
    _EQUITY_GROUPS_BROAD = {
        "indices", "sector_etfs", "international", "uk_etfs",
        "tech_subsectors", "financial_subsectors", "healthcare_subsectors",
        "international_expanded",
    }
    _equity_tickers: set[str] = {
        entry["ticker"]
        for gname, group in config.get("watchlist", {}).items()
        if gname in _EQUITY_GROUPS_BROAD and isinstance(group, list)
        for entry in group
        if isinstance(entry, dict) and "ticker" in entry
    }
    _EM_GROUPS = {"international", "international_expanded"}
    _em_tickers: set[str] = {
        entry["ticker"]
        for gname, group in config.get("watchlist", {}).items()
        if gname in _EM_GROUPS and isinstance(group, list)
        for entry in group
        if isinstance(entry, dict) and "ticker" in entry
    }
    macro_stress_active    = "MACRO_STRESS"   in active_overlays
    recession_risk_active  = "RECESSION_RISK" in active_overlays
    em_stress_active       = "EM_STRESS"      in active_overlays

    _BRIEF_MODULE_KEYS = [
        "trend_score", "mean_reversion_score", "volatility_score",
        "volume_score", "macro_score",
    ]

    # Signal distribution counts (all assets and tradeable-only)
    _SIG_ORDER  = ["STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"]
    _SIG_COLOUR = {
        "STRONG BUY": "#16a34a", "BUY": "#26a69a", "NEUTRAL": "#6b7280",
        "SELL": "#f59e0b", "STRONG SELL": "#ef5350",
    }

    def _count_signals(signals_list: list) -> dict:
        c = dict.fromkeys(_SIG_ORDER, 0)
        for s in signals_list:
            lbl = (s.get("signal") or "NEUTRAL").replace("_", " ")
            if lbl in c:
                c[lbl] += 1
        return c

    _trd_signals_all = [s for s in all_signals if s.get("ticker") in tradeable_set]
    _all_counts      = _count_signals(all_signals)
    _trd_counts      = _count_signals(_trd_signals_all)

    def _counts_html(counts: dict, total: int, label: str) -> str:
        parts = " &nbsp;·&nbsp; ".join(
            f"<span style='color:{_SIG_COLOUR[lbl]};font-weight:700'>{counts[lbl]}</span>"
            f"&nbsp;<span style='color:#64748b;font-size:0.75rem'>{lbl}</span>"
            for lbl in _SIG_ORDER
        )
        return (
            f"<div style='font-size:0.8rem;margin-bottom:2px'>"
            f"<span style='color:#94a3b8;min-width:120px;display:inline-block'>{label} ({total}):</span>"
            f" {parts}</div>"
        )

    st.markdown(
        _counts_html(_all_counts, len(all_signals), "All assets")
        + _counts_html(_trd_counts, len(_trd_signals_all), "Tradeable"),
        unsafe_allow_html=True,
    )

    def _brief_qualifies(s: dict, direction: int) -> bool:
        score  = s.get("confluence_score") or 0.0
        signal = (s.get("signal") or "").upper()
        if direction == 1:
            if score < 0.5:
                return False
            # STRONG BUY (≥ 0.6) always qualifies; BUY needs multi-module agreement
            if "STRONG" in signal:
                return True
        else:
            if score > -0.5:
                return False
            if "STRONG" in signal:
                return True
        agreeing = sum(1 for k in _BRIEF_MODULE_KEYS if (s.get(k) or 0) == direction)
        return agreeing >= 3

    _include_analysis = st.checkbox(
        "Include analysis-only assets in opportunities",
        value=False,
        key="brief_include_analysis",
    )
    _picks_pool = all_signals if _include_analysis else _trd_signals_all
    long_picks  = [s for s in _picks_pool           if _brief_qualifies(s,  1)]
    short_picks = [s for s in reversed(_picks_pool) if _brief_qualifies(s, -1)]

    def _brief_rationale(sig: dict, direction: int) -> str:
        _labels = {
            "trend_score":          "trend momentum",
            "mean_reversion_score": "mean reversion",
            "volatility_score":     "volatility conditions",
            "volume_score":         "volume confirmation",
            "macro_score":          "macro backdrop",
        }
        agreeing = [lbl for key, lbl in _labels.items() if (sig.get(key) or 0) == direction]
        if not agreeing:
            return "Cross-module signal with limited individual confirmation."
        if len(agreeing) >= 4:
            return f"Strong conviction — {', '.join(agreeing[:3])} and more all align."
        if len(agreeing) >= 2:
            return f"{agreeing[0].capitalize()} and {agreeing[1]} align in the same direction."
        return f"{agreeing[0].capitalize()} is the primary driver of this signal."

    def _opp_card(sig: dict, direction: int, fg: str, strong_fg: str, live_price=None) -> str:
        ticker    = sig.get("ticker", "")
        score     = sig.get("confluence_score") or 0.0
        signal    = sig.get("signal", "")
        is_strong = "STRONG" in signal
        col       = strong_fg if is_strong else fg
        weight    = "800" if is_strong else "600"
        sig_label = signal.replace("_", " ")
        name      = html.escape(watchlist.get(ticker, ticker))
        if len(name) > 32:
            name = name[:30] + "…"

        badges = ""
        if ticker not in tradeable_set:
            badges += (
                "<span style='background:#475569;color:#e2e8f0;padding:1px 6px;"
                "border-radius:3px;font-size:0.65rem;margin-left:5px'>Analysis only</span>"
            )
        if direction == 1 and macro_stress_active and ticker in _eq_bond_tickers:
            badges += (
                "<span style='background:#92400e;color:#fef3c7;padding:1px 6px;"
                "border-radius:3px;font-size:0.65rem;margin-left:5px'>Confidence −20%</span>"
            )
        if direction == 1 and recession_risk_active and ticker in _equity_tickers:
            badges += (
                "<span style='background:#7f1d1d;color:#fee2e2;padding:1px 6px;"
                "border-radius:3px;font-size:0.65rem;margin-left:5px'>Confidence −25%</span>"
            )
        if direction == 1 and em_stress_active and ticker in _em_tickers:
            badges += (
                "<span style='background:#78350f;color:#fef3c7;padding:1px 6px;"
                "border-radius:3px;font-size:0.65rem;margin-left:5px'>Confidence −15%</span>"
            )

        if live_price is not None:
            p = live_price
            p_str = f"{p:.4f}" if p < 10 else (f"{p:,.2f}" if p < 100_000 else f"{p:,.0f}")
            price_html = f"<span style='color:#64748b;font-size:0.78rem'>Last: {p_str}</span>"
        else:
            price_html = (
                "<span style='color:#6b7280;font-size:0.72rem'>"
                "⚠ Market closed · price unavailable</span>"
            )

        return (
            f"<div style='background:{col}18;border-left:3px solid {col};"
            f"padding:10px 14px;border-radius:4px;margin-bottom:8px'>"
            f"<div style='display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-bottom:4px'>"
            f"<span style='font-weight:{weight};color:{col};font-size:0.95rem'>"
            f"{html.escape(ticker)}</span>"
            f"<span style='color:#94a3b8;font-size:0.78rem'>{name}</span>"
            f"{badges}</div>"
            f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:5px'>"
            f"<span style='color:{col};font-size:0.8rem;font-weight:{weight}'>{sig_label}</span>"
            f"<span style='color:{col};font-weight:700'>{score:+.3f}</span>"
            f"{price_html}"
            f"</div>"
            f"<div style='color:#94a3b8;font-size:0.75rem;line-height:1.4'>"
            f"{html.escape(_brief_rationale(sig, direction))}</div>"
            f"</div>"
        )

    # Pre-fetch live prices for all qualifying opportunity tickers (cached 5 min)
    _opp_tickers = tuple(sorted({
        s.get("ticker") for s in long_picks + short_picks if s.get("ticker")
    }))
    with st.spinner("Fetching live prices…"):
        _opp_prices: dict = {t: _cached_latest_price(t) for t in _opp_tickers}

    st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)
    lc, rc = st.columns(2)

    with lc:
        n_long = len(long_picks)
        st.markdown(f"**Long Opportunities** ({n_long})" if n_long else "**Long Opportunities**")
        if long_picks:
            for sig in long_picks:
                lp = _opp_prices.get(sig.get("ticker", ""))
                st.markdown(_opp_card(sig, 1, "#26a69a", "#16a34a", live_price=lp), unsafe_allow_html=True)
        else:
            st.caption("No qualifying long signals today.")

    with rc:
        n_short = len(short_picks)
        st.markdown(f"**Short Opportunities** ({n_short})" if n_short else "**Short Opportunities**")
        if short_picks:
            for sig in short_picks:
                lp = _opp_prices.get(sig.get("ticker", ""))
                st.markdown(_opp_card(sig, -1, "#f59e0b", "#ef5350", live_price=lp), unsafe_allow_html=True)
        else:
            st.caption("No qualifying short signals today.")

    _filter_note = (
        "Showing tradeable assets only · Toggle above to include analysis-only assets"
        if not _include_analysis
        else "Showing all assets including analysis-only"
    )
    st.caption(_filter_note)

    # Catch-up opportunities
    catch_ups = get_active_opportunity_flags()
    if catch_ups:
        n = len(catch_ups)
        st.markdown(
            f"<div style='margin-top:10px'><strong>"
            f"{n} catch-up opportunit{'y' if n == 1 else 'ies'} detected"
            f"</strong></div>",
            unsafe_allow_html=True,
        )
        for flag in catch_ups:
            td = html.escape(flag.get("ticker", ""))
            dd = html.escape(flag.get("description", ""))
            sd = html.escape(flag.get("flag_source", ""))
            z  = flag.get("z_score")
            zs = f"z={z:.2f}" if z is not None else ""
            st.caption(f"• **{td}** — {dd} *({sd} {zs})*")

    st.divider()

    # ── Section 2: Macro Dashboard ─────────────────────────────────────────
    st.markdown("### Macro Dashboard")

    macro_dash = get_macro_dashboard(config)

    if not macro_dash.get("has_data"):
        st.info(
            "No FRED macro data yet — the first fetch runs automatically at the next "
            f"EOD scheduler run ({_uk_sched_time(21,30)} on weekdays)."
        )
    else:
        # ── Composite health score bar ──
        comp   = macro_dash["composite_score"]
        nb     = macro_dash["n_bullish"]
        nbr    = macro_dash["n_bearish"]
        nn     = macro_dash["n_neutral"]
        ns     = macro_dash["n_series"]
        score_colour = (
            "#16a34a" if comp >= 0.25 else
            "#ef5350" if comp <= -0.25 else
            "#f59e0b"
        )
        score_label = (
            "BULLISH"  if comp >= 0.25 else
            "BEARISH"  if comp <= -0.25 else
            "NEUTRAL"
        )
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:16px;flex-wrap:wrap;"
            f"background:{_hex_rgba(score_colour,0.08)};border:1px solid {score_colour}44;"
            f"padding:8px 16px;border-radius:8px;margin-bottom:8px'>"
            f"<div>"
            f"<span style='font-size:1.1rem;font-weight:700;color:{score_colour}'>"
            f"Macro Health: {comp:+.2f}</span> "
            f"<span style='background:{score_colour};color:#fff;padding:1px 8px;"
            f"border-radius:3px;font-size:0.7rem;font-weight:800'>{score_label}</span>"
            f"</div>"
            f"<div style='color:#64748b;font-size:0.82rem'>"
            f"{ns} indicators · "
            f"<span style='color:#16a34a'>▲ {nb} bullish</span> · "
            f"<span style='color:#6b7280'>◆ {nn} neutral</span> · "
            f"<span style='color:#ef5350'>▼ {nbr} bearish</span>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Plain-English summary
        summary = html.escape(macro_dash.get("summary", ""))
        st.markdown(
            f"<p style='font-size:0.84rem;color:#64748b;margin:0 0 10px 0;font-style:italic'>"
            f"{summary}</p>",
            unsafe_allow_html=True,
        )

        # ── Metric cards ────────────────────────────────────────────────────
        _TREND_ARROW = {"rising": "↑", "falling": "↓", "flat": "→"}
        _YC_COLOUR   = {"normal": "#16a34a", "flat": "#f59e0b", "inverted": "#ef5350"}
        _CS_COLOUR   = {"NORMAL": "#16a34a", "ELEVATED": "#f59e0b", "STRESSED": "#ef5350"}

        def _metric_card(
            title: str, value_str: str, sub: str,
            colour: str = "#6b7280", bg_alpha: float = 0.06,
            tag: str = "",
        ) -> str:
            bg = _hex_rgba(colour, bg_alpha)
            tag_html = (
                "<span style='color:#16a34a;font-size:0.58rem;margin-left:4px"
                ";font-weight:400;text-transform:none'>✓ Signals</span>"
                if tag == "signals" else
                "<span style='color:#94a3b8;font-size:0.58rem;margin-left:4px"
                ";font-weight:400;text-transform:none'>Composite</span>"
                if tag == "composite" else ""
            )
            return (
                f"<div style='background:{bg};border:1px solid {colour}33;"
                f"border-radius:8px;padding:10px 14px;min-height:72px'>"
                f"<div style='font-size:0.72rem;font-weight:600;color:#6b7280;"
                f"text-transform:uppercase;letter-spacing:0.05em'>{html.escape(title)}"
                f"{tag_html}</div>"
                f"<div style='font-size:1.05rem;font-weight:700;color:{colour};margin:2px 0'>"
                f"{html.escape(value_str)}</div>"
                f"<div style='font-size:0.76rem;color:#64748b'>{html.escape(sub)}</div>"
                f"</div>"
            )

        # Row 1: Real yields | Breakeven | HY Credit spread
        c1, c2, c3 = st.columns(3)
        with c1:
            ry    = macro_dash["real_yield"]
            rt    = macro_dash["real_yield_trend"]
            rt_ar = _TREND_ARROW.get(rt, "→")
            ry_c  = "#16a34a" if rt == "falling" else ("#ef5350" if rt == "rising" else "#f59e0b")
            v_str = f"{ry:+.2f}%" if ry is not None else "—"
            st.markdown(
                _metric_card("10Y Real Yield", f"{rt_ar} {v_str}", f"Trend: {rt}", ry_c, tag="signals"),
                unsafe_allow_html=True,
            )
        with c2:
            bk  = macro_dash["breakeven"]
            bt  = macro_dash["breakeven_trend"]
            bt_ar = _TREND_ARROW.get(bt, "→")
            bk_c  = "#16a34a" if 1.5 <= (bk or 0) <= 2.5 else "#f59e0b"
            v_str = f"{bk:.2f}%" if bk is not None else "—"
            st.markdown(
                _metric_card("10Y Breakeven Inflation", f"{bt_ar} {v_str}", f"Trend: {bt}", bk_c, tag="signals"),
                unsafe_allow_html=True,
            )
        with c3:
            hy_bps = macro_dash["hy_oas_bps"]
            cs     = macro_dash["credit_status"] or "—"
            cs_c   = _CS_COLOUR.get(cs, "#6b7280")
            v_str  = f"{hy_bps:.0f} bps" if hy_bps is not None else "—"
            p30    = macro_dash["hy_oas_pct_30d"]
            sub    = f"Status: {cs}" + (f" | 30d: {p30:+.1f}%" if p30 is not None else "")
            st.markdown(
                _metric_card("HY Credit Spread (OAS)", v_str, sub, cs_c, tag="signals"),
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Row 2: Yield curve 10Y2Y | Yield curve 10Y3M | IG spread + Composite details
        c4, c5, c6 = st.columns(3)
        with c4:
            yc2y  = macro_dash["t10y2y"]
            sh2y  = macro_dash["t10y2y_shape"] or "—"
            yc2_c = _YC_COLOUR.get(sh2y, "#6b7280")
            v_str = f"{yc2y:+.2f}%" if yc2y is not None else "—"
            st.markdown(
                _metric_card("Yield Curve 10Y-2Y", v_str, f"Shape: {sh2y}", yc2_c, tag="signals"),
                unsafe_allow_html=True,
            )
        with c5:
            yc3m  = macro_dash["t10y3m"]
            sh3m  = macro_dash["t10y3m_shape"] or "—"
            yc3_c = _YC_COLOUR.get(sh3m, "#6b7280")
            v_str = f"{yc3m:+.2f}%" if yc3m is not None else "—"
            st.markdown(
                _metric_card("Yield Curve 10Y-3M", v_str, f"Shape: {sh3m}", yc3_c, tag="signals"),
                unsafe_allow_html=True,
            )
        with c6:
            ig_bps = macro_dash["ig_oas_bps"]
            ig_c   = "#16a34a" if ig_bps is not None and ig_bps < 100 else (
                     "#ef5350" if ig_bps is not None and ig_bps > 150 else "#f59e0b"
            )
            v_str  = f"{ig_bps:.0f} bps" if ig_bps is not None else "—"
            st.markdown(
                _metric_card("IG Credit Spread (OAS)", v_str, "Investment Grade bonds", ig_c, tag="signals"),
                unsafe_allow_html=True,
            )

        # Row 3: PERMIT (specialty) | Fed Rate (specialty) | first extra series (dynamic)
        extra_series = macro_dash.get("extra_series", [])
        permit_val   = macro_dash.get("permit")
        permit_pct   = macro_dash.get("permit_pct1m")
        permit_sc    = macro_dash.get("permit_score")

        fed_rate_val   = macro_dash.get("fed_rate")
        fed_rate_trend = macro_dash.get("fed_rate_trend", "unchanged")
        fed_rate_sc    = macro_dash.get("fed_rate_score")

        if permit_val is not None or fed_rate_val is not None or extra_series:
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            c7, c8, c9 = st.columns(3)
            with c7:
                permit_c = (
                    "#16a34a" if permit_sc == 1 else
                    "#ef5350" if permit_sc == -1 else "#f59e0b"
                )
                if permit_val is not None:
                    ar  = _TREND_ARROW.get("rising" if (permit_pct or 0) > 1.0 else "falling" if (permit_pct or 0) < -1.0 else "flat", "→")
                    sub = f"MoM: {permit_pct:+.1f}%" if permit_pct is not None else "MoM: —"
                    st.markdown(
                        _metric_card(
                            "Housing Permits (SAAR)",
                            f"{ar} {permit_val:.0f}k units",
                            sub + " · thousands, SA",
                            permit_c,
                            tag="signals",
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(_metric_card("Housing Permits (SAAR)", "—", "No data", "#6b7280", tag="signals"), unsafe_allow_html=True)
            with c8:
                if fed_rate_val is not None:
                    _FED_TREND_LABEL = {"cut": "Rate cut ↓", "hiked": "Rate hiked ↑", "unchanged": "Unchanged →"}
                    _FED_TREND_COLOUR = {"cut": "#16a34a", "hiked": "#ef5350", "unchanged": "#6b7280"}
                    fr_c   = _FED_TREND_COLOUR.get(fed_rate_trend, "#6b7280")
                    fr_lbl = _FED_TREND_LABEL.get(fed_rate_trend, "Unchanged →")
                    st.markdown(
                        _metric_card(
                            "Fed Funds Rate (Upper Bound)",
                            f"{fed_rate_val:.2f}%",
                            fr_lbl,
                            fr_c,
                            tag="signals",
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(_metric_card("Fed Funds Rate (Upper Bound)", "—", "No data", "#6b7280", tag="signals"), unsafe_allow_html=True)
            with c9:
                if extra_series:
                    es   = extra_series[0]
                    es_c = "#16a34a" if es["score"] == 1 else "#ef5350" if es["score"] == -1 else "#f59e0b"
                    ar   = _TREND_ARROW.get(es["trend"], "→")
                    dv   = es["display_value"]
                    st.markdown(
                        _metric_card(
                            es["label"],
                            f"{ar} {dv}" if dv != "—" else "—",
                            es["context"],
                            es_c,
                            tag="signals" if es["in_signals"] else "composite",
                        ),
                        unsafe_allow_html=True,
                    )

        # Remaining extra series — render in rows of 3
        for _row_start in range(1, len(extra_series), 3):
            _chunk = extra_series[_row_start:_row_start + 3]
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            _ecols = st.columns(3)
            for _ec, _es in zip(_ecols, _chunk):
                with _ec:
                    _es_c = "#16a34a" if _es["score"] == 1 else "#ef5350" if _es["score"] == -1 else "#f59e0b"
                    _ar   = _TREND_ARROW.get(_es["trend"], "→")
                    _dv   = _es["display_value"]
                    st.markdown(
                        _metric_card(
                            _es["label"],
                            f"{_ar} {_dv}" if _dv != "—" else "—",
                            _es["context"],
                            _es_c,
                            tag="signals" if _es["in_signals"] else "composite",
                        ),
                        unsafe_allow_html=True,
                    )

    st.divider()

    # ── Section 3: Open Trade Monitor ──────────────────────────────────────
    st.markdown("### Open Trade Monitor")

    t212_invest_cards = _get_t212_invest_brief_cards(config)

    if not t212_invest_cards:
        st.info("No open T212 Invest positions.")
    else:
        all_cards = sorted(
            t212_invest_cards, key=lambda x: x.get("status_priority", 0), reverse=True
        )
        for e in all_cards:
            _render_trade_card_compact(e)
        st.caption("Full position detail (signal delta, SL/TP, live R:R) is on the **Performance** page.")

    st.divider()

    # ── Section 4: Today's Opportunities ───────────────────────────────────
    st.markdown("### Today's Opportunities")

    open_tickers = {c["ticker"] for c in t212_invest_cards}
    tradeable_signals = [s for s in all_signals if s.get("ticker") in tradeable_set]
    opps = build_opportunities(tradeable_signals, open_tickers, active_overlays, watchlist)

    if not opps:
        # Use regime-specific thresholds (computed earlier in col_regime block)
        _near_miss = sum(
            1 for s in tradeable_signals
            if s.get("ticker") not in open_tickers
            and (
                0.35 <= (s.get("confluence_score") or 0.0) < _buy_th
                or -_sell_th < (s.get("confluence_score") or 0.0) <= -0.35
            )
        )
        _overlay_labels = [
            OVERLAY_DESCRIPTIONS.get(k, {}).get("label", k.replace("_", " "))
            for k in active_overlays
        ]
        _why_parts = [f"Regime: **{regime.replace('_', ' ')}**"]
        if _overlay_labels:
            _why_parts.append(f"Active overlay{'s' if len(_overlay_labels) > 1 else ''}: **{', '.join(_overlay_labels)}**")
        if _near_miss > 0:
            _thresh_note = (
                f"0.35–{_buy_th:.2f}"
                if _buy_th == _sell_th
                else f"buy 0.35–{_buy_th:.2f} or sell 0.35–{_sell_th:.2f}"
            )
            _why_parts.append(
                f"**{_near_miss}** signal{'s' if _near_miss > 1 else ''} within {_thresh_note} "
                f"(below STRONG threshold)"
            )
        st.info(
            "No strong signals outside current positions right now.  \n"
            + " · ".join(_why_parts)
        )
    else:
        def _mod_str(o: dict) -> str:
            def _s(v: int) -> str:
                return "+" if v > 0 else ("-" if v < 0 else "·")
            return (
                f"T{_s(o['trend'])} MR{_s(o['mean_rev'])} "
                f"V{_s(o['volatility'])} Vol{_s(o['volume'])} M{_s(o['macro'])}"
            )

        # Fetch ATR and live price for each opportunity to compute SL/TP suggestions
        _opp_atr_cache: dict[str, float | None] = {}
        _opp_px_cache:  dict[str, float | None] = {}
        for _o in opps:
            _tk = _o["ticker"]
            if _tk not in _opp_atr_cache:
                _opp_atr_cache[_tk] = get_current_atr(_tk)
            if _tk not in _opp_px_cache:
                _opp_px_cache[_tk] = _cached_latest_price(_tk)

        opp_rows = []
        for o in opps:
            _otk   = o["ticker"]
            _oatr  = _opp_atr_cache.get(_otk)
            _opx   = _opp_px_cache.get(_otk)
            _is_buy = o["signal"] == "STRONG_BUY"
            _omult  = 1 if _is_buy else -1

            _osl_str = "—"
            _otp1_str = "—"
            _otp2_str = "—"
            _orisk_str = "—"

            if _oatr is not None and _opx is not None and _opx > 0:
                _osl_dist = 2 * _oatr
                _osl_px   = _opx - _omult * _osl_dist
                _otp1_px  = _opx + _omult * _osl_dist
                _otp2_px  = _opx + _omult * _osl_dist * 2
                _osl_str  = f"{_osl_px:.4f}"
                _otp1_str = f"{_otp1_px:.4f}"
                _otp2_str = f"{_otp2_px:.4f}"
                # Risk example assumes £150 @ leverage from config default (show plain %)
                _osl_pct  = _osl_dist / _opx * 100
                _orisk_str = f"{_osl_pct:.2f}%/1×lev"

            opp_rows.append({
                "Ticker":  _otk,
                "Name":    o["name"],
                "Signal":  o["signal"].replace("_", " "),
                "Score":   o["score"],
                "Modules": _mod_str(o),
                "Sug. SL": _osl_str,
                "TP1 (1:1)": _otp1_str,
                "TP2 (2:1)": _otp2_str,
                "Risk/lev": _orisk_str,
                "Note":    o["overlay_note"] or "",
            })

        opp_df = pd.DataFrame(opp_rows)

        def _style_opp_sig(val: str) -> str:
            if "STRONG BUY"  in val: return "color:#16a34a;font-weight:bold"
            if "STRONG SELL" in val: return "color:#ef5350;font-weight:bold"
            return ""

        styler = opp_df.style.map(_style_opp_sig, subset=["Signal"]).format({"Score": "{:+.3f}"})
        st.dataframe(styler, width="stretch", hide_index=True,
                     height=38 + 35 * len(opp_rows))
        st.caption(
            "Sug. SL = entry ± 2×ATR based on yfinance reference price. "
            "TP1/TP2 = 1:1 and 2:1 R:R targets. "
            "Risk/lev = SL distance as % of price per unit of leverage — "
            "multiply by investment × leverage for GBP risk estimate."
        )


# ---------------------------------------------------------------------------
# Backtest page
# ---------------------------------------------------------------------------

def _render_backtest_page(config: dict, watchlist: dict[str, str]) -> None:
    st.markdown("## Backtest")
    st.caption(
        "Replay the current signal logic over historical price data. "
        "Uses vectorized pandas — no look-ahead bias. "
        "Correlation module excluded (contributes a fixed weight of 0.10); "
        "remaining module weights are re-normalised."
    )

    tab_run, tab_period, tab_attr, tab_decay, tab_wf = st.tabs([
        "▶ Run & Overview",
        "Period Analysis",
        "🎯 Module Attribution",
        "⏱ Signal Decay",
        "Walk-Forward",
    ])

    # ── shared: load runs list once ──────────────────────────────────────────
    all_runs  = get_all_runs()
    complete  = [r for r in all_runs if r.get("status") == "complete"]
    run_opts  = {f"#{r['id']} — {r['name']} ({r.get('total_trades',0)} trades)": r["id"]
                 for r in complete}

    # ── Tab 1: Run & Overview ─────────────────────────────────────────────────
    with tab_run:
        st.markdown("### Configure & Run")
        st.caption(
            "The backtest uses the same settings as the live system to show what "
            "would have happened under current configuration. To test different "
            "parameters, adjust config.yaml and re-run."
        )

        tradeable = [t for g in config.get("watchlist", {}).values()
                     if isinstance(g, list)
                     for t in g
                     if isinstance(t, dict) and t.get("tradeable")]
        tradeable_tickers = [e["ticker"] for e in tradeable]
        all_tickers_list  = get_all_tickers(config)

        # Tickers that have a Trading212 CFD instrument mapping
        t212_tickers = [
            e["ticker"]
            for g in config.get("watchlist", {}).values()
            if isinstance(g, list)
            for e in g
            if isinstance(e, dict) and e.get("trading212_instrument")
        ]

        col_l, col_r = st.columns(2)
        with col_l:
            preset = st.selectbox("Date range preset", [
                "Full history (2019–present)",
                "COVID era (2019–2021)",
                "Bear market (2022)",
                "Recovery (2023–2024)",
                "Custom",
            ], key="bt_preset")
            presets = {
                "Full history (2019–present)": (datetime(2019, 1, 1), datetime.utcnow()),
                "COVID era (2019–2021)":        (datetime(2019, 1, 1), datetime(2021, 12, 31)),
                "Bear market (2022)":           (datetime(2022, 1, 1), datetime(2022, 12, 31)),
                "Recovery (2023–2024)":         (datetime(2023, 1, 1), datetime(2024, 12, 31)),
            }
            if preset != "Custom":
                sd, ed = presets[preset]
            else:
                sd = datetime.combine(st.date_input("Start date", value=datetime(2019, 1, 1).date(), key="bt_sd"), datetime.min.time())
                ed = datetime.combine(st.date_input("End date",   value=datetime.utcnow().date(), key="bt_ed"),     datetime.min.time())

            bt_name = st.text_input("Run name (optional)", placeholder="e.g. Full history LONG-only", key="bt_name")

        with col_r:
            ticker_scope = st.radio(
                "Tickers to test",
                ["Tradeable assets only", "T212 CFD Only", "All assets"],
                key="bt_scope",
            )
            if ticker_scope == "Tradeable assets only":
                tickers_to_test = tradeable_tickers
            elif ticker_scope == "T212 CFD Only":
                tickers_to_test = t212_tickers
            else:
                tickers_to_test = all_tickers_list
            st.caption(f"{len(tickers_to_test)} tickers selected")

        _bt_cfg  = config.get("backtest", {})
        _etf_max_hold   = config.get("trading212_invest", {}).get("max_holding_days", 90)
        _stock_max_hold = config.get("trading212_stocks", {}).get("max_holding_days", 30)
        _bond_max_hold  = _bt_cfg.get("max_holding_days_bonds", 45)
        _atr_mult       = _bt_cfg.get("atr_stop_multiplier", 2.0)
        _entry_signal   = _bt_cfg.get("entry_signal", "STRONG_BUY")
        _allowed_regimes = _bt_cfg.get("allowed_entry_regimes")
        _regime_note = f"{'/'.join(_allowed_regimes)} only" if _allowed_regimes else "all regimes"
        _use_garch_stops = config.get("signals", {}).get("use_garch_stops", False)
        _stops_note = "GARCH" if _use_garch_stops else f"ATR {_atr_mult:.1f}×"
        st.caption(
            f"Using live config: ETF max hold {_etf_max_hold}d · "
            f"Stock max hold {_stock_max_hold}d · Bond max hold {_bond_max_hold}d · "
            f"entry {_entry_signal} · {_regime_note} · stops: {_stops_note}"
        )

        if st.button("Run Backtest", type="primary", key="bt_run"):
            with st.spinner(f"Running backtest for {len(tickers_to_test)} tickers ({sd.date()} → {ed.date()})…"):
                try:
                    new_id = run_backtest(tickers_to_test, sd, ed, config, name=bt_name)
                    st.success(f"Backtest #{new_id} complete.")
                    st.session_state["bt_selected_run"] = new_id
                    st.rerun()
                except Exception as exc:
                    st.error(f"Backtest failed: {exc}")

        st.divider()
        st.markdown("### Previous Runs")

        if not complete:
            st.info("No completed backtest runs yet — configure and run one above.")
        else:
            sel_label = st.selectbox("Select run to view", list(run_opts.keys()), key="bt_sel_run")
            sel_id    = run_opts[sel_label]
            st.session_state["bt_selected_run"] = sel_id

            run  = get_run(sel_id)
            df   = get_run_trades(sel_id)
            mets = compute_metrics(df)

            # Delete button
            with st.expander("Manage run"):
                if st.button(f"Delete run #{sel_id}", key="bt_del"):
                    db_path = config.get("database", {}).get("path", "database/investment_research.db")
                    size_before = os.path.getsize(db_path) if os.path.exists(db_path) else None

                    delete_run(sel_id)

                    if size_before is not None and os.path.exists(db_path):
                        size_after = os.path.getsize(db_path)
                        reclaimed = size_before - size_after
                        if reclaimed > 0:
                            mb_before = size_before / (1024 * 1024)
                            mb_after  = size_after / (1024 * 1024)
                            mb_reclaimed = reclaimed / (1024 * 1024)
                            st.success(
                                f"✓ Run #{sel_id} deleted — database reduced from "
                                f"{mb_before:.1f} MB to {mb_after:.1f} MB "
                                f"(−{mb_reclaimed:.1f} MB reclaimed)"
                            )
                        else:
                            st.success(f"✓ Run #{sel_id} deleted — database optimised.")
                    else:
                        st.success(f"✓ Run #{sel_id} deleted.")
                    st.rerun()

            # Metrics strip
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Trades", mets.get("total_trades", 0))
            wr_pct = round((mets.get("win_rate") or 0) * 100, 1)
            c2.metric("Win Rate", f"{wr_pct}%",
                      delta=f"{wr_pct - 50:.1f}pp vs 50%",
                      delta_color="normal")
            c3.metric("Avg Return", f"{mets.get('avg_return_pct', 0):+.2f}%")
            c4.metric("Total Return", f"{mets.get('total_return_pct', 0):+.1f}%")
            c5.metric("Sharpe", f"{mets.get('sharpe_ratio', 0):.2f}")
            c6.metric("Max Drawdown", f"{mets.get('max_drawdown_pct', 0):.1f}%")

            c7, c8, c9 = st.columns(3)
            c7.metric("Avg Hold", f"{mets.get('avg_holding_days', 0):.0f} days")
            c8.metric("Profit Factor", f"{mets.get('profit_factor', 0):.2f}")
            c9.metric("Max Loss Streak", mets.get("max_loss_streak", 0))

            # Equity curve — bt_equity_curve() (analysis/backtest_metrics.py:
            # equity_curve()) returns one row per calendar day (P&L realised
            # in exit-date order, days with no exit carrying forward the
            # prior value), not one row per trade, so the date axis below is
            # always monotonically increasing with no backwards jumps even
            # when trades overlap.
            if not df.empty:
                ec = bt_equity_curve(df)
                if not ec.empty:
                    import plotly.graph_objects as go
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=ec["date"], y=ec["equity"],
                        mode="lines", name="Portfolio (normalised to 1.0)",
                        line=dict(color="#3b82f6", width=2, shape="linear"),
                        fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                        hovertemplate="%{x|%Y-%m-%d}<br>Equity: %{y:.4f}<extra></extra>",
                    ))
                    fig.update_layout(
                        title=f"Equity Curve — Run #{sel_id} (daily portfolio value)",
                        xaxis_title="Date", yaxis_title="Portfolio Value",
                        height=350, margin=dict(l=0, r=0, t=40, b=0),
                        yaxis=dict(tickformat=".2f"),
                        xaxis=dict(type="date"),
                    )
                    st.plotly_chart(fig, width="stretch")

                # Trades table
                st.markdown("#### Simulated Trades")
                show_df = df.copy()
                if "stop_distance_pct" not in show_df.columns:
                    show_df["stop_distance_pct"] = None
                if "vol_source" not in show_df.columns:
                    show_df["vol_source"] = None
                show_df["vol_source"] = show_df["vol_source"].map(
                    {"garch": "GARCH", "atr_fallback": "ATR fallback"}
                ).fillna("—")
                show_df = show_df[[
                    "ticker", "direction", "entry_date", "exit_date",
                    "entry_price", "exit_price", "pnl_pct",
                    "exit_reason", "regime_at_entry", "holding_days", "is_win",
                    "stop_distance_pct", "vol_source",
                ]].copy()
                show_df["pnl_pct"] = (show_df["pnl_pct"] * 100).round(2)
                show_df["stop_distance_pct"] = show_df["stop_distance_pct"].round(2)
                show_df.columns = [
                    "Ticker", "Dir", "Entry Date", "Exit Date",
                    "Entry Price", "Exit Price", "P&L %",
                    "Exit Reason", "Regime", "Hold Days", "Win",
                    "Stop Distance %", "Vol Source",
                ]
                st.dataframe(
                    show_df.style.map(lambda v: "color:#16a34a" if v is True else ("color:#dc2626" if v is False else ""), subset=["Win"])
                              .map(lambda v: f"color:{'#16a34a' if v > 0 else '#dc2626'}" if isinstance(v, (int, float)) else "", subset=["P&L %"]),
                    width="stretch", hide_index=True,
                )

            # ── T212 CFD Comparison ───────────────────────────────────────────
            st.divider()
            with st.expander("📊 T212 CFD Only — Comparison vs Full Run", expanded=False):
                st.caption(
                    "Filters this run's trades to only tickers that have a "
                    "`trading212_instrument` mapping in config.yaml — i.e. assets "
                    "actually available as CFDs on Trading212. No re-run required; "
                    "metrics are recomputed on the filtered subset instantly."
                )

                _t212_set  = set(t212_tickers)
                _df_full   = df
                _df_t212   = df[df["ticker"].isin(_t212_set)].copy() if not df.empty else df

                _mets_full = mets  # already computed above
                _mets_t212 = compute_metrics(_df_t212)

                def _fmt_wr(m: dict) -> str:
                    v = m.get("win_rate") or 0
                    return f"{round(v * 100, 1)}%"

                def _fmt_pf(m: dict) -> str:
                    v = m.get("profit_factor") or 0
                    return "∞" if v >= 99.9 else f"{v:.2f}"

                _comparison_rows = [
                    ("Trades",         str(_mets_full.get("total_trades", 0)),       str(_mets_t212.get("total_trades", 0))),
                    ("Win Rate",       _fmt_wr(_mets_full),                           _fmt_wr(_mets_t212)),
                    ("Total Return",   f"{_mets_full.get('total_return_pct', 0):+.1f}%", f"{_mets_t212.get('total_return_pct', 0):+.1f}%"),
                    ("Avg Return",     f"{_mets_full.get('avg_return_pct', 0):+.2f}%",   f"{_mets_t212.get('avg_return_pct', 0):+.2f}%"),
                    ("Profit Factor",  _fmt_pf(_mets_full),                           _fmt_pf(_mets_t212)),
                    ("Sharpe Ratio",   f"{_mets_full.get('sharpe_ratio', 0):.2f}",    f"{_mets_t212.get('sharpe_ratio', 0):.2f}"),
                    ("Max Drawdown",   f"{_mets_full.get('max_drawdown_pct', 0):.1f}%", f"{_mets_t212.get('max_drawdown_pct', 0):.1f}%"),
                    ("Avg Hold Days",  f"{_mets_full.get('avg_holding_days', 0):.0f}", f"{_mets_t212.get('avg_holding_days', 0):.0f}"),
                    ("Max Loss Streak", str(_mets_full.get("max_loss_streak", 0)),    str(_mets_t212.get("max_loss_streak", 0))),
                ]
                _cmp_df = pd.DataFrame(_comparison_rows, columns=["Metric", "Full Universe", "T212 CFD Only"])

                def _colour_compare(row: pd.Series) -> list[str]:
                    styles = ["font-weight:bold", "", ""]
                    if row["Metric"] in ("Trades", "Avg Hold Days", "Max Loss Streak"):
                        return styles
                    try:
                        fv = float(str(row["Full Universe"]).replace("%", "").replace("+", "").replace("∞", "999"))
                        tv = float(str(row["T212 CFD Only"]).replace("%", "").replace("+", "").replace("∞", "999"))
                        better_is_higher = row["Metric"] not in ("Max Drawdown", "Max Loss Streak")
                        t212_better = tv > fv if better_is_higher else tv < fv
                        styles[2] = "color:#16a34a;font-weight:bold" if t212_better else "color:#dc2626"
                    except (ValueError, TypeError):
                        pass
                    return styles

                st.dataframe(
                    _cmp_df.style.apply(_colour_compare, axis=1),
                    width="stretch", hide_index=True,
                )

                # ── T212 tickers caption ──────────────────────────────────────
                st.caption(
                    f"T212 instruments included ({len(_t212_set)}): "
                    + ", ".join(sorted(_t212_set))
                )

                # ── Excluded tickers and their trade counts ───────────────────
                st.markdown("#### Tickers Excluded by T212 Filter")
                st.caption(
                    "These tickers appear in the full run but are not available as "
                    "T212 CFD instruments. Shows how many simulated trades each contributed."
                )

                if df.empty:
                    st.info("No trade data available.")
                else:
                    _excluded_tickers = sorted(set(df["ticker"].unique()) - _t212_set)
                    if not _excluded_tickers:
                        st.success("All tickers in this run have T212 instruments — no exclusions.")
                    else:
                        # Build name lookup from config
                        _name_lookup: dict[str, str] = {}
                        for _g in config.get("watchlist", {}).values():
                            if not isinstance(_g, list):
                                continue
                            for _e in _g:
                                if isinstance(_e, dict) and "ticker" in _e:
                                    _name_lookup[_e["ticker"]] = _e.get("name", _e["ticker"])

                        _excl_rows = []
                        for _tkr in _excluded_tickers:
                            _sub = df[df["ticker"] == _tkr]
                            _em  = compute_metrics(_sub)
                            _excl_rows.append({
                                "Ticker":      _tkr,
                                "Name":        _name_lookup.get(_tkr, _tkr),
                                "Trades":      _em.get("total_trades", 0),
                                "Win Rate":    _fmt_wr(_em),
                                "Avg Return":  f"{_em.get('avg_return_pct', 0):+.2f}%",
                                "Total Return": f"{_em.get('total_return_pct', 0):+.1f}%",
                            })

                        _excl_df = pd.DataFrame(_excl_rows).sort_values("Trades", ascending=False)
                        st.dataframe(_excl_df, width="stretch", hide_index=True)

                        _total_excl_trades = sum(r["Trades"] for r in _excl_rows)
                        _total_full_trades = _mets_full.get("total_trades", 0)
                        _excl_pct = (_total_excl_trades / _total_full_trades * 100) if _total_full_trades else 0
                        st.caption(
                            f"{_total_excl_trades} of {_total_full_trades} trades "
                            f"({_excl_pct:.0f}%) come from non-T212 tickers."
                        )

    # ── Tab 2: Period Analysis ─────────────────────────────────────────────────
    with tab_period:
        st.markdown("### Performance by Market Period")
        if not complete:
            st.info("Run a backtest first.")
        else:
            sel_id = st.session_state.get("bt_selected_run") or list(run_opts.values())[0]
            df     = get_run_trades(sel_id)
            bdown  = period_breakdown(df)

            for period_name, m in bdown.items():
                colour  = m.get("colour", "#6b7280")
                desc    = m.get("desc", "")
                n       = m.get("total_trades", 0)

                with st.expander(f"{period_name}  —  {n} trade{'s' if n != 1 else ''}", expanded=True):
                    st.caption(desc)
                    if n == 0:
                        st.info("No trades simulated in this period with the selected run.")
                        continue
                    pc1, pc2, pc3, pc4 = st.columns(4)
                    wr = round((m.get("win_rate") or 0) * 100, 1)
                    pc1.metric("Win Rate",     f"{wr}%")
                    pc2.metric("Avg Return",   f"{m.get('avg_return_pct', 0):+.2f}%")
                    pc3.metric("Total Return", f"{m.get('total_return_pct', 0):+.1f}%")
                    pc4.metric("Max Drawdown", f"{m.get('max_drawdown_pct', 0):.1f}%")

            # Regime performance
            st.divider()
            st.markdown("### Performance by Regime")
            rp = regime_performance(df)
            if not rp.empty:
                st.dataframe(rp, width="stretch")

    # ── Tab 3: Module Attribution ─────────────────────────────────────────────
    with tab_attr:
        st.markdown("### Which Modules Drive Winning Trades?")
        st.caption(
            "Agreement = module score direction matches trade direction at entry. "
            "High 'Win Rate When Agree' shows the module is a useful signal."
        )
        if not complete:
            st.info("Run a backtest first.")
        else:
            sel_id = st.session_state.get("bt_selected_run") or list(run_opts.values())[0]
            df     = get_run_trades(sel_id)
            attr   = module_attribution(df)

            if attr.empty:
                st.info("Not enough trade data for attribution analysis.")
            else:
                # Bar chart: win rate when agree vs disagree
                import plotly.graph_objects as go
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    name="Win rate when module agrees",
                    x=attr.index.tolist(),
                    y=attr["win_rate_when_agree"].tolist(),
                    marker_color="#22c55e",
                ))
                fig.add_trace(go.Bar(
                    name="Win rate when module disagrees",
                    x=attr.index.tolist(),
                    y=attr["win_rate_when_disagree"].tolist(),
                    marker_color="#ef4444",
                ))
                fig.add_hline(y=50, line_dash="dot", line_color="#94a3b8",
                              annotation_text="50% baseline")
                fig.update_layout(
                    barmode="group", title="Module Win Rate: Agreement vs Disagreement",
                    yaxis_title="Win Rate %", height=350,
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig, width="stretch")

                # Detailed table
                st.markdown("#### Module Detail")
                show = attr.reset_index().rename(columns={
                    "module":                 "Module",
                    "win_rate_when_agree":    "Win% (Agree)",
                    "win_rate_when_disagree": "Win% (Disagree)",
                    "avg_score_wins":         "Avg Score (Wins)",
                    "avg_score_losses":       "Avg Score (Losses)",
                    "agreement_pct":          "Agreement %",
                    "n_trades":               "N Trades",
                })
                st.dataframe(show, width="stretch", hide_index=True)

    # ── Tab 4: Signal Decay ───────────────────────────────────────────────────
    with tab_decay:
        st.markdown("### How Long Do Signals Last?")
        st.caption("Distribution of holding periods and exit reasons across all simulated trades.")
        if not complete:
            st.info("Run a backtest first.")
        else:
            sel_id = st.session_state.get("bt_selected_run") or list(run_opts.values())[0]
            df     = get_run_trades(sel_id)
            decay  = signal_decay(df)

            if not decay:
                st.info("Not enough trade data.")
            else:
                m1, m2, m3 = st.columns(3)
                m1.metric("Median Hold",   f"{decay.get('median_hold', 0):.0f} days")
                m2.metric("Mean Hold",     f"{decay.get('mean_hold', 0):.0f} days")
                m3.metric("Stop Loss %",   f"{decay.get('pct_stop_loss', 0):.1f}%")

                m4, m5, m6 = st.columns(3)
                m4.metric("Signal Reversal %", f"{decay.get('pct_signal_reversal', 0):.1f}%")
                m5.metric("Max Holding %",     f"{decay.get('pct_max_holding', 0):.1f}%")
                m6.metric("End of Data %",     f"{decay.get('pct_end_of_data', 0):.1f}%")

                # ── Performance by Exit Reason ────────────────────────────────
                st.markdown("#### Performance by Exit Reason")
                _erb = exit_reason_breakdown(df)
                if _erb.empty:
                    st.info("Not enough trade data for an exit reason breakdown.")
                else:
                    _has_stop_dist = "avg_stop_distance_pct" in _erb.columns
                    _erb_df = _erb.reset_index().rename(columns={
                        "exit_reason":           "Exit Reason",
                        "trades":                "Trades",
                        "win_rate":               "Win Rate",
                        "avg_return":             "Avg Return",
                        "avg_hold":               "Avg Hold (days)",
                        "avg_stop_distance_pct":  "Avg Stop Distance %",
                    })

                    def _style_erb_return(val):
                        if not isinstance(val, (int, float)): return ""
                        return ("color:#16a34a;font-weight:bold" if val > 0
                                else "color:#dc2626;font-weight:bold" if val < 0 else "")

                    _erb_format = {
                        "Win Rate":        "{:.1f}%",
                        "Avg Return":      "{:+.2f}%",
                        "Avg Hold (days)": "{:.1f}",
                    }
                    if _has_stop_dist:
                        _erb_format["Avg Stop Distance %"] = "{:.2f}%"

                    st.dataframe(
                        _erb_df.style.map(_style_erb_return, subset=["Avg Return"]).format(_erb_format),
                        width="stretch", hide_index=True,
                    )
                    _garch_enabled = config.get("signals", {}).get("use_garch_stops", False)
                    if _has_stop_dist and _garch_enabled:
                        st.caption(
                            "Avg Stop Distance % shows how wide the GARCH (or ATR-fallback) stop was at entry "
                            "for trades that exited each way — compare STOP_LOSS's distance against the others "
                            "to see whether GARCH stops are calibrated sensibly for this ticker set. If "
                            "MAX_HOLDING shows a higher win rate and avg return than STOP_LOSS, the stop may be "
                            "cutting profitable trades short — consider widening `signals.garch_stop_multiplier`. "
                            "If STOP_LOSS outperforms MAX_HOLDING, the time limit may be holding losers too long."
                        )
                    else:
                        st.caption(
                            "If MAX_HOLDING shows a higher win rate and avg return than STOP_LOSS, the ATR stop "
                            "may be cutting profitable trades short — consider widening `backtest.atr_stop_multiplier` "
                            "in config.yaml. If STOP_LOSS outperforms MAX_HOLDING, the time limit may be holding "
                            "losers too long."
                        )

                # Histogram
                bins = decay.get("hold_bins", [])
                if bins:
                    import plotly.graph_objects as go
                    fig = go.Figure(go.Bar(
                        x=[b["bin"] for b in bins],
                        y=[b["count"] for b in bins],
                        marker_color="#6366f1",
                    ))
                    fig.update_layout(
                        title="Holding Period Distribution",
                        xaxis_title="Days Held", yaxis_title="Number of Trades",
                        height=300, margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig, width="stretch")

                # Avg hold by regime
                rh = decay.get("regime_holds", {})
                if rh:
                    st.markdown("#### Average Holding by Regime")
                    rh_df = pd.DataFrame(list(rh.items()), columns=["Regime", "Avg Days"])
                    st.dataframe(rh_df, width="stretch", hide_index=True)

                # ── Forex Holding Sensitivity ─────────────────────────────────
                st.markdown("---")
                st.markdown("#### Forex Holding Sensitivity")
                st.caption(
                    "Profit factor and win rate for forex trades (tickers ending in '=X') "
                    "at different max-hold caps — helps calibrate `max_holding_days_forex` "
                    "in config.yaml. Trades already closed by stop-loss or signal reversal "
                    "within the cap are included; those that ran longer are excluded."
                )
                _fx_sens = forex_holding_sensitivity(df)
                if not _fx_sens:
                    st.info("No forex trades in this backtest run (no tickers ending in '=X').")
                else:
                    _fx_df = pd.DataFrame(_fx_sens)
                    _fx_df.columns = ["Max Days", "N Trades", "Profit Factor", "Win Rate %", "Avg Return %"]
                    _fx_df["Profit Factor"] = _fx_df["Profit Factor"].apply(
                        lambda v: "∞ (no losses)" if v is None else f"{v:.2f}"
                    )
                    st.dataframe(_fx_df, width="stretch", hide_index=True)

                    # Highlight the current configured value
                    _cfg_fx_hold = int(config.get("backtest", {}).get("max_holding_days_forex", 5))
                    st.caption(
                        f"Current config: `max_holding_days_forex = {_cfg_fx_hold}` · "
                        f"Look for the row where profit factor peaks to find the optimal cap."
                    )

    # ── Tab 5: Walk-Forward ───────────────────────────────────────────────────
    with tab_wf:
        st.markdown("### Rolling 6-Month Window Validation")
        st.caption(
            "Each data point = performance of all trades entered within a 6-month window. "
            "Consistent win rates across windows suggest the signal logic has stable edge."
        )
        if not complete:
            st.info("Run a backtest first.")
        else:
            sel_id  = st.session_state.get("bt_selected_run") or list(run_opts.values())[0]
            df      = get_run_trades(sel_id)
            wf_data = walk_forward(df, window_months=config.get("backtest", {}).get("walk_forward_months", 6))

            if not wf_data:
                st.info("Not enough data for walk-forward analysis (need trades across multiple 6-month windows).")
            else:
                wf_df = pd.DataFrame(wf_data)

                import plotly.graph_objects as go
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=wf_df["window_start"], y=(wf_df["win_rate"] * 100).round(1),
                    mode="lines+markers", name="Win Rate %",
                    line=dict(color="#3b82f6", width=2),
                ))
                fig.add_hline(y=50, line_dash="dot", line_color="#94a3b8",
                              annotation_text="50% baseline")
                fig.update_layout(
                    title="Rolling Win Rate (6-month windows)",
                    xaxis_title="Window Start", yaxis_title="Win Rate %",
                    height=320, margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig, width="stretch")

                # Avg return line
                fig2 = go.Figure()
                fig2.add_trace(go.Bar(
                    x=wf_df["window_start"],
                    y=wf_df["avg_return_pct"].round(2),
                    marker_color=["#22c55e" if v >= 0 else "#ef4444" for v in wf_df["avg_return_pct"]],
                    name="Avg Return %",
                ))
                fig2.add_hline(y=0, line_color="#94a3b8")
                fig2.update_layout(
                    title="Average Return per Window",
                    xaxis_title="Window Start", yaxis_title="Avg Return %",
                    height=280, margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig2, width="stretch")

                st.markdown("#### Window Detail")
                show_wf = wf_df.copy()
                show_wf["win_rate"] = (show_wf["win_rate"] * 100).round(1)
                show_wf.columns = ["Window Start", "Window End", "Win Rate %",
                                    "Avg Return %", "N Trades", "Sharpe", "Max DD %"]
                st.dataframe(show_wf, width="stretch", hide_index=True)


def _t212_asset_type_map(config: dict) -> dict[str, str]:
    """Map research ticker -> 'ETF' or 'STOCK' via the T212 automation config.

    t212_pnl.ticker stores the research ticker, not the T212 instrument ticker,
    so this is keyed the opposite way to _get_t212_invest_brief_cards()'s
    internal reverse_map (which is keyed by T212 instrument ticker).
    """
    asset_types: dict[str, str] = {}
    for rt in config.get("trading212_invest", {}).get("ucits_mapping", {}):
        asset_types[rt] = "ETF"
    if config.get("trading212_stocks", {}):
        for entry in config.get("watchlist", {}).get("us_large_cap", []):
            if isinstance(entry, dict) and "ticker" in entry:
                asset_types.setdefault(entry["ticker"], "STOCK")
    return asset_types


def _get_t212_pnl_rows() -> list[dict]:
    """Return all reconstructed closed T212 positions, most recently exited first.

    Reads brokers/t212_learning.py:_reconstruct_t212_pnl()'s output table directly
    — no T212 API calls, no re-derivation of P&L.
    """
    from sqlalchemy import text as sa_text
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                sa_text("SELECT * FROM t212_pnl ORDER BY exit_date DESC")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _t212_pnl_metrics_df(pnl_rows: list[dict]) -> pd.DataFrame:
    """Adapt t212_pnl rows to the column shape analysis/backtest_metrics.py expects.

    backtest_metrics.py's functions (compute_metrics, equity_curve,
    exit_reason_breakdown, regime_performance) were written against
    BacktestTrade rows and read pnl_pct/is_win/holding_days/entry_date/
    exit_date/regime_at_entry — reused here rather than reimplementing the
    same aggregation logic a second time for t212_pnl, which carries the same
    information under slightly different column names (regime vs
    regime_at_entry, no is_win/direction columns at all since T212 Invest is
    long-only cash-account and every row is a realised close).
    """
    if not pnl_rows:
        return pd.DataFrame()
    df = pd.DataFrame(pnl_rows)
    df["entry_date"]      = pd.to_datetime(df["entry_date"])
    df["exit_date"]       = pd.to_datetime(df["exit_date"])
    df["pnl_pct"]         = df["pnl_pct"].astype(float)
    df["is_win"]          = df["pnl_gbp"] > 0
    df["direction"]       = "LONG"
    df["regime_at_entry"] = df["regime"]
    return df


def _render_performance_page(config: dict) -> None:
    """T212 Invest execution performance — open positions, closed positions,
    aggregate metrics (once enough history exists), and feedback-learning status.

    All P&L here is derived from actual T212 order fills (see "T212 P&L
    reconstruction" in CLAUDE.md), not simulated/paper trades — there is no
    other trade source left in the app.
    """
    st.title("Performance")

    # ── Section 1: Open Positions ───────────────────────────────────────────
    st.markdown("### Open Positions")
    open_cards = _get_t212_invest_brief_cards(config)
    if not open_cards:
        st.info("No open T212 Invest positions.")
    else:
        all_cards = sorted(open_cards, key=lambda x: x.get("status_priority", 0), reverse=True)
        for e in all_cards:
            _render_trade_card(e)

    st.divider()

    # ── Section 2: Closed Positions ─────────────────────────────────────────
    st.markdown("### Closed Positions")
    pnl_rows = _get_t212_pnl_rows()

    if not pnl_rows:
        st.info("No closed positions yet — data will appear as T212 positions close.")
    else:
        asset_types = _t212_asset_type_map(config)
        show_df = pd.DataFrame(pnl_rows)
        show_df["Type"] = show_df["ticker"].map(lambda t: asset_types.get(t, "—"))
        show_df["entry_date"] = pd.to_datetime(show_df["entry_date"]).dt.strftime("%Y-%m-%d")
        show_df["exit_date"]  = pd.to_datetime(show_df["exit_date"]).dt.strftime("%Y-%m-%d")
        show_df["pnl_pct"]    = (show_df["pnl_pct"] * 100).round(2)
        show_df["pnl_gbp"]    = show_df["pnl_gbp"].round(2)
        if "stop_distance_pct" in show_df.columns:
            show_df["stop_distance_pct"] = show_df["stop_distance_pct"].round(2)
        else:
            show_df["stop_distance_pct"] = None
        if "vol_source" in show_df.columns:
            show_df["vol_source"] = show_df["vol_source"].map(
                {"garch": "GARCH", "atr_fallback": "ATR Fallback"}
            ).fillna("—")
        else:
            show_df["vol_source"] = "—"

        show_df = show_df[[
            "ticker", "Type", "entry_date", "exit_date", "entry_price", "exit_price",
            "quantity", "currency", "pnl_gbp", "pnl_pct", "holding_days",
            "signal_type", "regime", "exit_reason", "stop_distance_pct", "vol_source",
        ]]
        show_df.columns = [
            "Ticker", "Type", "Entry Date", "Exit Date", "Entry Price", "Exit Price",
            "Qty", "Ccy", "P&L £", "P&L %", "Hold Days",
            "Entry Signal", "Regime", "Exit Reason", "Stop % at Entry", "Vol Source",
        ]
        st.dataframe(
            show_df.style
                .map(lambda v: f"color:{'#16a34a' if v >= 0 else '#dc2626'}", subset=["P&L £"])
                .map(lambda v: f"color:{'#16a34a' if v >= 0 else '#dc2626'}", subset=["P&L %"]),
            width="stretch", hide_index=True,
        )

    st.divider()

    # ── Section 3: Performance Metrics ──────────────────────────────────────
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
        mdf  = _t212_pnl_metrics_df(pnl_rows)
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

        ec = bt_equity_curve(mdf)
        if not ec.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=ec["date"], y=ec["equity"],
                mode="lines", name="Portfolio (normalised to 1.0)",
                line=dict(color="#3b82f6", width=2, shape="linear"),
                fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                hovertemplate="%{x|%Y-%m-%d}<br>Equity: %{y:.4f}<extra></extra>",
            ))
            fig.update_layout(
                title="Equity Curve (daily, normalised to 1.0)",
                xaxis_title="Date", yaxis_title="Portfolio Value",
                height=320, margin=dict(l=0, r=0, t=40, b=0),
                yaxis=dict(tickformat=".2f"),
                xaxis=dict(type="date"),
            )
            st.plotly_chart(fig, width="stretch")

        rp = regime_performance(mdf)
        if not rp.empty:
            st.markdown("#### Performance by Regime at Entry")
            rp_show = rp.copy()
            rp_show.columns = ["N Trades", "Win Rate %", "Avg Return %", "Max DD %", "Sharpe"]
            st.dataframe(rp_show, width="stretch")

        erb = exit_reason_breakdown(mdf)
        if not erb.empty:
            st.markdown("#### Performance by Exit Reason")
            erb_show = erb.copy()
            erb_show.columns = ["Trades", "Win Rate %", "Avg Return %", "Avg Hold Days"]
            st.dataframe(erb_show, width="stretch")

    st.divider()

    # ── Section 4: Learning System ──────────────────────────────────────────
    st.markdown("### Learning System")

    learning_cfg = config.get("learning", {})
    min_trades   = learning_cfg.get("min_trades_before_adjustment", 20)
    n_pnl        = len(pnl_rows)

    st.caption(
        f"{n_pnl} T212 closed position{'s' if n_pnl != 1 else ''} toward the next weight "
        f"adjustment (fires at {min_trades})."
    )

    learned = get_learned_weights()
    if learned:
        weights   = learned.get("weights", {})
        win_rates = learned.get("win_rates", {})
        modules   = ["trend", "mean_reversion", "volatility", "volume", "macro", "correlation"]

        avg_static = {
            m: sum(REGIME_WEIGHTS[r][m] for r in REGIME_WEIGHTS) / len(REGIME_WEIGHTS)
            for m in modules
        }

        st.markdown("#### Learned Weights vs Static Regime Weights (avg across regimes)")
        cmp_df = pd.DataFrame([
            {
                "Module":            m.replace("_", " ").title(),
                "Learned Weight":    weights.get(m),
                "Static Avg Weight": round(avg_static.get(m, 0), 4),
                "Win Rate":          win_rates.get(m),
            }
            for m in modules
        ])
        st.dataframe(cmp_df, width="stretch", hide_index=True)
        st.caption(f"Last computed from {learned.get('n_trades', 0)} T212 closed positions.")

        history = get_weight_history()
        if history:
            with st.expander(f"Weight history ({len(history)} snapshots)"):
                hist_rows = [
                    {"Timestamp": h.get("ts"), "N Trades": h.get("n_trades"), **h.get("weights", {})}
                    for h in history
                ]
                st.dataframe(pd.DataFrame(hist_rows), width="stretch", hide_index=True)
    else:
        st.caption("No learned weights have been computed yet — insufficient T212 closed-position history.")


# Main UI
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Investment Research",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _initialise()

    # Show a brief toast if startup analysis just ran (fires once per session)
    _startup = st.session_state.pop("_startup_msg", None)
    if _startup:
        _lvl, _msg = _startup
        st.toast(_msg, icon="✅" if _lvl == "ok" else "⚠️")

    config = _load_config()
    watchlist = _build_watchlist(config)
    tradeable_set = _build_tradeable_set(config)
    t212_names    = _build_t212_names(config)

    # ---- Sidebar -------------------------------------------------------
    with st.sidebar:
        st.title("Investment Research")
        st.divider()

        # Page navigation
        _PAGES = ["Daily Briefing", "Signals", "Charts", "Relationships", "Performance", "Backtest", "Status"]
        _PAGE_KEYS = {
            "Daily Briefing": "briefing",
            "Signals":       "signals",
            "Charts":        "charts",
            "Relationships": "relationships",
            "Performance":   "performance",
            "Backtest":      "backtest",
            "Status":        "status",
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

        # Pipeline status — last data pull and last analysis timestamps.
        _sb_refresh  = get_metadata("last_full_refresh") or get_metadata("last_refresh_run")
        _sb_analysis = get_metadata("last_analysis_run")
        _sb_r_str = _fmt_uk(_sb_refresh,  "%Y-%m-%d %H:%M") if _sb_refresh  else "Pending"
        _sb_a_str = _fmt_uk(_sb_analysis, "%Y-%m-%d %H:%M") if _sb_analysis else "Pending"
        st.caption(f"**Last refresh:** {_sb_r_str}")
        st.caption(f"**Last analysis:** {_sb_a_str}")


    # ---- Main content --------------------------------------------------
    page_key = st.session_state.get("page", "briefing")

    if page_key == "briefing":
        _render_briefing_page(config, watchlist, tradeable_set, t212_names)
        return

    if page_key == "signals":
        _render_signals_page(config, watchlist, tradeable_set)
        return

    if page_key == "status":
        _render_home_page(config, watchlist)
        return

    if page_key == "relationships":
        _render_relationships_page(config)
        return

    if page_key == "performance":
        _render_performance_page(config)
        return

    if page_key == "backtest":
        _render_backtest_page(config, watchlist)
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

    st.markdown(f"## {name}")
    st.caption(f"Ticker: **{selected}**")

    # Chart range selector — persists across renders but resets when switching
    # to a new ticker so the user always starts fresh on the default view.
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
        return

    # Key metrics row — always computed from the full loaded window so YTD
    # uses this calendar year regardless of which range is selected.
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

    # Price chart
    st.plotly_chart(_candlestick_chart(df, selected, name), width="stretch")

    # Volume chart
    st.plotly_chart(_volume_bar_chart(df), width="stretch")

    # Indicator panel
    _render_indicator_panel(selected)

    # Raw data expander
    with st.expander("Raw OHLCV data"):
        display_df = df.copy()
        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            display_df.sort_values("date", ascending=False).reset_index(drop=True),
            width="stretch",
        )


if __name__ == "__main__":
    main()
