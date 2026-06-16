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
from datetime import datetime, date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from analysis.confluence import (
    generate_signals,
    get_last_signal_timestamp,
    get_latest_signals,
    get_learned_weights,
    get_weight_history,
    maybe_update_weights,
)
from analysis.correlations import (
    build_correlation_matrix,
    compute_all_pairs,
    get_active_opportunity_flags,
    get_stored_correlations,
)
from analysis.regime import OVERLAY_DESCRIPTIONS
from analysis.engine import run_analysis
from analysis.regime import REGIME_COLOURS, REGIME_DESCRIPTIONS, Regime, detect_regime
from data.database import get_engine, get_metadata, get_metadata_str, init_db, set_metadata
from data.ingestion import (
    get_all_tickers,
    get_price_df,
    pull_incremental,
)
from data.scheduler import start_scheduler
from output.performance import (
    FOREX_QUOTE_CCY,
    close_trade,
    get_all_trades,
    get_closed_trades,
    get_current_signal,
    get_latest_price,
    get_open_trades,
    log_trade,
)
from analysis.macro import get_macro_dashboard
from output.briefing import (
    STATUS_COLOURS as _BRIEF_STATUS_COLOURS,
    STATUS_ICONS   as _BRIEF_STATUS_ICONS,
    build_opportunities,
    enrich_open_trade,
    get_current_atr,
    get_macro_context,
    send_alert_email,
    send_test_email,
    should_send_alert,
)
from output.signals import (
    accuracy_by_asset_class,
    accuracy_by_confluence_band,
    accuracy_by_indicator,
    accuracy_by_regime,
    accuracy_by_signal_type,
    accuracy_summary,
    equity_curve,
    signal_validation,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ensure the DB schema exists before any page renders or background jobs fire.
# init_db() is idempotent — create_all() and _apply_migrations() are no-ops
# when tables and columns are already present.
init_db()


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


@st.cache_data(ttl=60)
def _cached_last_full_refresh() -> "datetime | None":
    """Timestamp of the last successful data pull (EOD scheduled run or manual refresh).

    Reads last_full_refresh first; falls back to last_refresh_run for backward
    compat with databases written before the multi-run scheduler was deployed.
    """
    return get_metadata("last_full_refresh") or get_metadata("last_refresh_run")


@st.cache_data(ttl=60)
def _cached_last_analysis() -> "datetime | None":
    """Last analysis run timestamp (any run type), cached for 60 s."""
    return get_metadata("last_analysis_run")


@st.cache_data(ttl=60)
def _cached_last_run_type() -> "str | None":
    """Run type of the most recent scheduled job ('full' or 'analysis_only')."""
    return get_metadata_str("last_run_type")


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
    return _ts.strftime("%Y-%m-%d %H:%M UTC"), age


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
            dt  = datetime.strptime(raw_ts.strip()[:19], "%Y-%m-%d %H:%M:%S")
            msg = msg.strip()
            if len(msg) > 60:
                msg = msg[:57] + "…"
            return dt.strftime("%Y-%m-%d %H:%M UTC"), msg
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


def _price_coverage(watchlist: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Return (fresh, stale, missing) ticker lists based on latest price date.

    Fresh  = data within the last 4 calendar days (covers long weekends).
    Stale  = data present but older than 4 days.
    Missing = no rows at all in price_data for that ticker.
    """
    try:
        from sqlalchemy import text as sa_text
        with get_engine().connect() as conn:
            rows = conn.execute(
                sa_text("SELECT ticker, MAX(date) AS latest FROM price_data GROUP BY ticker")
            ).fetchall()
        latest_by_ticker = {r[0]: r[1] for r in rows}
    except Exception:
        latest_by_ticker = {}

    cutoff = datetime.utcnow() - timedelta(days=4)
    fresh, stale, missing = [], [], []
    for ticker in watchlist:
        raw = latest_by_ticker.get(ticker)
        if raw is None:
            missing.append(ticker)
            continue
        # SQLite returns dates as strings or datetime objects depending on driver
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                missing.append(ticker)
                continue
        else:
            dt = raw
        dt = dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt
        (fresh if dt >= cutoff else stale).append(ticker)

    return fresh, stale, missing


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

    # Overlay 20- and 50-day SMAs
    for window, colour in ((20, "#f59e0b"), (50, "#6366f1")):
        if len(df) >= window:
            sma = df["close"].rolling(window).mean()
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
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

        # Mean Reversion
        st.markdown("**Mean Reversion**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("RSI (14)",    _v("rsi",         "{:,.1f}"))
        c2.metric("BB %B",       _v("bb_pct_b",    "{:,.2f}"))
        c3.metric("BB Width",    _v("bb_width",    "{:,.2f}"))
        c4.metric("Z-Score 52w", _v("z_score_52w", "{:+.2f}"))

        # Volatility
        st.markdown("**Volatility**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ATR (14)",    _v("atr",         "{:,.3f}"))
        c2.metric("ATR %",       _v("atr_pct",     "{:.2%}"))
        c3.metric("Hist Vol 20", _v("hist_vol_20", "{:.1%}"))
        vix_val   = row.get("vix_level")
        vix_reg   = row.get("vix_regime") or ""
        c4.metric("VIX", f"{float(vix_val):,.1f} ({vix_reg})" if vix_val is not None else "—")

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

    st.markdown("## 📊 Market Signals")

    # ---- Opportunity flags & regime overlays (top of page) ----------------
    catch_up_flags = get_active_opportunity_flags()
    overlay_raw    = get_metadata_str("active_overlays") or ""
    active_overlays = {k for k in overlay_raw.split(",") if k and k != "none"}

    has_overlays  = bool(active_overlays)
    has_catch_ups = bool(catch_up_flags)

    if has_overlays or has_catch_ups:
        st.markdown("### Active Intelligence")

        # Regime overlay cards
        for overlay_key in ("MACRO_STRESS", "VOL_REGIME_SHIFT", "CREDIT_STRESS"):
            if overlay_key not in active_overlays:
                continue
            meta   = OVERLAY_DESCRIPTIONS.get(overlay_key, {})
            colour = meta.get("colour", "#6b7280")
            label  = html.escape(meta.get("label", overlay_key.replace("_", " ")))
            body   = html.escape(meta.get("text", ""))
            bg     = _hex_rgba(colour, 0.10)
            st.markdown(
                f"<div style='border-left:4px solid {colour};padding:12px 18px;"
                f"background:{bg};border-radius:0 6px 6px 0;margin-bottom:10px'>"
                f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:6px'>"
                f"<span style='background:{colour};color:#fff;padding:2px 9px;"
                f"border-radius:4px;font-size:0.72rem;font-weight:800;letter-spacing:0.06em'>"
                f"REGIME OVERLAY</span>"
                f"<strong style='color:{colour};font-size:0.95rem'>{label}</strong></div>"
                f"<p style='font-size:0.875rem;line-height:1.55;margin:0'>{body}</p>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Catch-up opportunity cards
        _cu_colour = "#d97706"
        _cu_bg     = _hex_rgba(_cu_colour, 0.10)
        for flag in catch_up_flags:
            ticker_disp = html.escape(flag.get("ticker", ""))
            source_disp = html.escape(flag.get("flag_source", ""))
            desc_disp   = html.escape(flag.get("description", ""))
            z           = flag.get("z_score")
            z_str       = f"z={z:.2f}" if z is not None else ""
            corr        = flag.get("pair_corr_30d")
            corr_str    = f" | 30d corr {corr:+.3f}" if corr is not None else ""
            st.markdown(
                f"<div style='border-left:4px solid {_cu_colour};padding:12px 18px;"
                f"background:{_cu_bg};border-radius:0 6px 6px 0;margin-bottom:10px'>"
                f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:6px;"
                f"flex-wrap:wrap'>"
                f"<span style='background:{_cu_colour};color:#fff;padding:2px 9px;"
                f"border-radius:4px;font-size:0.72rem;font-weight:800;letter-spacing:0.06em'>"
                f"CATCH-UP OPPORTUNITY</span>"
                f"<strong style='color:#fbbf24;font-size:0.95rem'>{ticker_disp}</strong>"
                f"<span style='color:#64748b;font-size:0.8rem'>{source_disp}</span>"
                f"<span style='color:#64748b;font-size:0.8rem'>{z_str}{corr_str}</span>"
                f"</div>"
                f"<p style='font-size:0.875rem;line-height:1.55;margin:0'>{desc_disp}</p>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.divider()

    # ---- Regime pill (compact) ---------------------------------------------
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

    st.markdown(
        f'<div style="display:inline-flex;align-items:center;gap:16px;'
        f'background:{colour}18;border:1px solid {colour}44;'
        f'padding:6px 14px;border-radius:20px;margin-bottom:4px">'
        f'<span style="color:{colour};font-weight:700">⚡ {regime.replace("_", " ")}</span>'
        f'<span style="color:#6b7280;font-size:0.8rem">^GSPC {price_pct}</span>'
        f'<span style="color:#6b7280;font-size:0.8rem">{adx_str}</span>'
        f'<span style="color:#6b7280;font-size:0.8rem">{vix_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    _REGIME_NOTES: dict[str, str] = {
        "TRENDING_BULL":   "Trend signals carry the most weight. Favour momentum and breakout plays — mean reversion signals are less reliable in a strong uptrend.",
        "TRENDING_BEAR":   "Trend signals carry the most weight. Favour short positions and breakdown plays — avoid buying dips unless confirmed by multiple modules.",
        "HIGH_VOLATILITY": "Volatility signals are most informative. Prioritise defensive positions and tight risk management — directional signals are less reliable when VIX is elevated.",
        "RANGING_LOW_VOL": "Mean reversion signals carry the most weight. Look for oversold bounces and range-bound plays — trend-following signals are less reliable in a sideways market.",
        "UNKNOWN":         "Regime is unclear — signals are weighted equally across all modules. Treat all calls with extra caution.",
    }
    note = _REGIME_NOTES.get(regime, "")
    if note:
        st.caption(note)

    st.divider()

    # ---- Last run timestamp ------------------------------------------------
    last_ts = get_metadata("last_analysis_run") or get_last_signal_timestamp()
    if last_ts:
        st.caption(f"Signals last generated: {last_ts.strftime('%Y-%m-%d %H:%M')} UTC")
    else:
        st.caption("Signals not yet generated — updates run automatically at 21:30 UTC on weekdays.")

    st.divider()

    # ---- Ranked signals table ----------------------------------------------
    rows = get_latest_signals(list(watchlist.keys()))

    if not rows:
        st.info("No signals yet — signals are generated automatically after each data refresh.")
        return

    # ---- Signal distribution -----------------------------------------------
    _DIST_CFG = [
        ("STRONG BUY",  "#16a34a"),
        ("BUY",         "#26a69a"),
        ("NEUTRAL",     "#6b7280"),
        ("SELL",        "#f59e0b"),
        ("STRONG SELL", "#ef5350"),
    ]
    counts = {lbl: 0 for lbl, _ in _DIST_CFG}
    t_counts = {lbl: 0 for lbl, _ in _DIST_CFG}
    for r in rows:
        lbl = (r.get("signal") or "NEUTRAL").replace("_", " ")
        if lbl in counts:
            counts[lbl] += 1
            if r.get("ticker") in tradeable_set:
                t_counts[lbl] += 1

    dist_cols = st.columns(5)
    for col, (lbl, fg) in zip(dist_cols, _DIST_CFG):
        col.markdown(
            f'<div style="text-align:center;padding:10px 4px;border-radius:6px;'
            f'background:{fg}22;border:1px solid {fg}55">'
            f'<div style="color:{fg};font-weight:700;font-size:1.5rem">{counts[lbl]}</div>'
            f'<div style="color:#6b7280;font-size:0.65rem;font-weight:600">'
            f'{t_counts[lbl]} tradeable</div>'
            f'<div style="color:#64748b;font-size:0.72rem;margin-top:2px">{lbl}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

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

    _pick_card(lc, long_picks,  "🟢 Top Long Opportunities",  "#16a34a")
    _pick_card(sc, short_picks, "🔴 Top Short Opportunities", "#ef5350")

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
        use_container_width=True,
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
# Trade Log page
# ---------------------------------------------------------------------------

def _render_trade_log_page(config: dict, watchlist: dict[str, str], tradeable_set: set[str], t212_names: dict[str, str]) -> None:
    st.markdown("## 📋 Trade Log")

    accounts = config.get("accounts", [])

    # ---- Log New Trade -----------------------------------------------------
    with st.expander("➕ Log New Trade", expanded=True):
        # Account selector (outside form so it can affect the badge display)
        if accounts:
            _acct_ids    = [a["id"] for a in accounts]
            _acct_labels = {a["id"]: a["name"] for a in accounts}
            _default_idx = next((i for i, a in enumerate(accounts) if a.get("default")), 0)
            sel_acct_id  = st.selectbox(
                "Account",
                options=_acct_ids,
                format_func=lambda x: _acct_labels[x],
                index=_default_idx,
                key="log_account_id",
            )
            sel_acct = next((a for a in accounts if a["id"] == sel_acct_id), accounts[0])
        else:
            sel_acct = {"id": "t212_practice", "name": "Trading212 Practice", "type": "PRACTICE"}

        _acct_type_log   = sel_acct.get("type", "PRACTICE")
        _badge_colour = "#6366f1" if _acct_type_log == "PRACTICE" else "#16a34a"
        st.markdown(
            f"<span style='background:{_badge_colour};color:#fff;padding:2px 10px;"
            f"border-radius:4px;font-size:0.75rem;font-weight:bold;letter-spacing:0.05em'>"
            f"{html.escape(_acct_type_log)}</span>",
            unsafe_allow_html=True,
        )

        _log_show_all = st.checkbox(
            "Show all assets (including analysis-only)",
            value=False,
            key="log_show_all_tickers",
        )
        ticker_keys = (
            list(watchlist.keys()) if _log_show_all
            else [t for t in watchlist.keys() if t in tradeable_set]
        )

        def _fmt_trade_ticker(t: str) -> str:
            base = f"{t} — {watchlist[t]}"
            t212 = t212_names.get(t)
            return f"{base}  ·  T212: {t212}" if t212 else base

        sel_ticker = st.selectbox(
            "Asset",
            ticker_keys,
            format_func=_fmt_trade_ticker,
            key="log_ticker",
        )

        sig = get_current_signal(sel_ticker)
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Signal",           (sig.get("signal") or "—").replace("_", " "))
        sc2.metric("Confluence Score", f"{sig.get('confluence_score', 0):+.3f}" if sig else "—")
        sc3.metric("Regime",           (sig.get("regime") or "—").replace("_", " "))

        # ── Position sizing guidance ──────────────────────────────────────────
        # Fetch live price here (reused below for the yfinance reference warning)
        _ref_price = _cached_latest_price(sel_ticker)

        _overlay_raw_ps  = get_metadata_str("active_overlays") or ""
        _active_ovs_ps   = {k for k in _overlay_raw_ps.split(",") if k and k != "none"}
        _macro_stress_ps = "MACRO_STRESS" in _active_ovs_ps

        _acct_ccy_ps  = sel_acct.get("currency", "GBP")
        _acct_balance = float(
            sel_acct.get("practice_balance", 0) if _acct_type_log == "PRACTICE"
            else sel_acct.get("live_balance", 0)
        )
        _max_pos_pct  = float(sel_acct.get("max_position_pct", 0.05))
        _signal_raw   = sig.get("signal") or "NEUTRAL"
        _score_val    = float(sig.get("confluence_score") or 0)
        _atr_ps       = get_current_atr(sel_ticker)

        # Signal scaling by confluence score
        if abs(_score_val) >= 0.6:
            _sig_scale = 1.00
            _sig_label = f"{_signal_raw.replace('_', ' ')} ×1.00"
        elif abs(_score_val) >= 0.3:
            _sig_scale = 0.60
            _sig_label = f"{_signal_raw.replace('_', ' ')} ×0.60"
        else:
            _sig_scale = 0.00
            _sig_label = "NEUTRAL ×0.00 — no position suggested"

        _overlay_factor = 0.80 if _macro_stress_ps else 1.00
        _base_size      = _acct_balance * _max_pos_pct if _acct_balance > 0 else 0.0
        _suggested_inv  = round(_base_size * _sig_scale * _overlay_factor, 2)

        # Pre-fill investment field when ticker or account changes
        _size_ctx_key = f"{sel_ticker}|{sel_acct_id}"
        if st.session_state.get("_log_size_ctx") != _size_ctx_key:
            st.session_state["_log_size_ctx"] = _size_ctx_key
            if _suggested_inv > 0:
                st.session_state["log_investment"] = _suggested_inv

        if _acct_balance > 0:
            _sess_lev = int(st.session_state.get("log_leverage", 1))

            # Build card lines
            _ps_rows: list[str] = [
                f"<span style='color:#94a3b8'>Account Balance:        </span>"
                f"<span style='color:#e2e8f0'>{_acct_ccy_ps} {_acct_balance:>10,.2f}</span>",

                f"<span style='color:#94a3b8'>Max Position ({_max_pos_pct*100:.0f}%):     </span>"
                f"<span style='color:#e2e8f0'>{_acct_ccy_ps} {_base_size:>10,.2f}</span>",

                f"<span style='color:#94a3b8'>Signal Scaling:         </span>"
                f"<span style='color:{'#16a34a' if _sig_scale==1.0 else '#f59e0b' if _sig_scale>0 else '#ef5350'}'>"
                f"{html.escape(_sig_label)}</span>",
            ]
            if _macro_stress_ps:
                _ps_rows.append(
                    f"<span style='color:#94a3b8'>Overlay (MACRO_STRESS): </span>"
                    f"<span style='color:#f59e0b'>×0.80 (−20%)</span>"
                )
            _divider_html = "<span style='color:#1e3a5f'>──────────────────────────────────</span>"
            _ps_rows.append(_divider_html)

            _sug_col = "#16a34a" if _suggested_inv > 0 else "#6b7280"
            _ps_rows.append(
                f"<span style='color:#94a3b8'>Suggested Investment:   </span>"
                f"<span style='color:{_sug_col};font-weight:700'>"
                f"{_acct_ccy_ps} {_suggested_inv:>9,.2f}</span>"
            )
            if _sess_lev > 1 and _suggested_inv > 0:
                _pos_val_ps = _suggested_inv * _sess_lev
                _margin_ps  = _suggested_inv
                _ps_rows += [
                    f"<span style='color:#94a3b8'>Position Value (1:{_sess_lev}):    </span>"
                    f"<span style='color:#e2e8f0'>{_acct_ccy_ps} {_pos_val_ps:>9,.2f}</span>",

                    f"<span style='color:#94a3b8'>Suggested Margin:       </span>"
                    f"<span style='color:#e2e8f0'>{_acct_ccy_ps} {_margin_ps:>9,.2f}</span>",
                ]
            if sel_ticker in FOREX_QUOTE_CCY and _suggested_inv > 0:
                _ps_rows.append(
                    f"<span style='color:#94a3b8'>FX Conv. Fee:           </span>"
                    f"<span style='color:#6b7280;font-size:0.78rem'>"
                    f"0.50% of P&amp;L applied at close</span>"
                )
            if _atr_ps is not None:
                _stop_dist = 2 * _atr_ps
                _ps_rows.append(
                    f"<span style='color:#94a3b8'>ATR Stop Distance:      </span>"
                    f"<span style='color:#e2e8f0'>{_stop_dist:.4f} (2×ATR {_atr_ps:.4f})</span>"
                )
                if _ref_price and _ref_price > 0 and _suggested_inv > 0:
                    _risk_pct_ps = (_stop_dist / _ref_price) * _sess_lev * 100
                    _ps_rows.append(
                        f"<span style='color:#94a3b8'>Est. Risk % (vs invest): </span>"
                        f"<span style='color:#f59e0b'>~{_risk_pct_ps:.1f}%</span>"
                        f"<span style='color:#475569;font-size:0.78rem'>"
                        f" (2×ATR/price × leverage, uses yfinance ref)</span>"
                    )

            st.markdown(
                f"<div style='background:#0a1628;border:1px solid #1e3a5f;border-radius:6px;"
                f"padding:14px 18px;margin:10px 0 4px;font-family:monospace;"
                f"font-size:0.82rem;line-height:2'>"
                f"<div style='color:#60a5fa;margin-bottom:4px;font-family:sans-serif;"
                f"font-size:0.72rem;letter-spacing:0.08em;text-transform:uppercase;"
                f"font-weight:600'>Suggested Position Size</div>"
                + "".join(f"{r}<br>" for r in _ps_rows)
                + "</div>",
                unsafe_allow_html=True,
            )

            with st.expander("💡 Why this size?"):
                _why: list[str] = [
                    f"Based on your {_acct_ccy_ps} {_acct_balance:,.0f} "
                    f"{'practice' if _acct_type_log == 'PRACTICE' else 'live'} balance, "
                    f"maximum {_max_pos_pct*100:.0f}% per trade = "
                    f"{_acct_ccy_ps} {_base_size:,.0f}.",
                ]
                if _sig_scale == 1.0:
                    _why.append(
                        f"{_signal_raw.replace('_', ' ')} signal uses full allocation (×1.00)."
                    )
                elif _sig_scale == 0.6:
                    _why.append(
                        f"{_signal_raw.replace('_', ' ')} signal uses 60% allocation (×0.60)."
                    )
                else:
                    _why.append("NEUTRAL signal — no position suggested (allocation ×0.00).")
                if _macro_stress_ps:
                    _why.append(
                        f"MACRO_STRESS overlay is active — allocation reduced by 20%, "
                        f"giving {_acct_ccy_ps} {_suggested_inv:,.0f} suggested."
                    )
                elif _suggested_inv > 0:
                    _why.append(f"No active overlays — full signal allocation applies.")
                if _atr_ps is not None:
                    _why.append(
                        f"The ATR-based stop sits {2*_atr_ps:.4f} away from entry (2×ATR). "
                        f"In Trading212, set your stop loss at entry price ± {2*_atr_ps:.4f}."
                    )
                st.markdown(" ".join(_why))

            if _acct_type_log == "LIVE":
                st.warning(
                    "**This is a live account — real money.** "
                    "Review all inputs carefully before confirming."
                )

        st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

        # ── Trade inputs (no st.form — dynamic calculations need live rerenders) ──
        # Market price from yfinance shown for reference only — never used as default
        _ref_note  = (
            f"yfinance reference: **{_ref_price:,.4f}**"
            if _ref_price else "yfinance reference: unavailable"
        )
        st.markdown(
            f"<div style='background:#1a0808;border:1px solid #dc2626;border-radius:4px;"
            f"padding:6px 12px;font-size:0.8rem;color:#fca5a5;margin-bottom:10px'>"
            f"⚠️  Enter prices <strong>exactly as shown in Trading212</strong>. "
            f"Do not use the yfinance reference · {_ref_note}</div>",
            unsafe_allow_html=True,
        )

        _f1, _f2, _f3 = st.columns(3)
        direction   = _f1.radio("Direction", ["LONG", "SHORT"], horizontal=True, key="log_direction")
        entry_price = _f2.number_input(
            "Entry Price (T212 exact)",
            min_value=0.0, value=0.0, step=0.0001, format="%.4f",
            key="log_entry_price",
        )
        units = _f3.number_input(
            "Units (T212 exact)",
            min_value=0.0, value=0.0, step=0.000001, format="%.6f",
            help="Exact number of units shown in Trading212, e.g. 4.077272",
            key="log_units",
        )

        # Currency
        _c1, _c2 = st.columns(2)
        instr_ccy   = _c1.selectbox("Instrument Currency", ["GBP", "USD", "EUR"], key="log_instr_ccy")
        account_ccy = _c2.selectbox("Account Currency",    ["GBP", "USD", "EUR"], key="log_acct_ccy")

        fx_rate = 1.0
        fx_rate_pair = ""
        if instr_ccy != account_ccy:
            fx_rate_pair = _fx_pair_label(instr_ccy, account_ccy)
            _pair_key    = fx_rate_pair.lower()  # e.g. "gbpusd"

            # Refresh button must appear BEFORE _get_fx_rate so that clicking it
            # clears session state before the fetch happens in the same rerun.
            _fx_info_col, _fx_btn_col = st.columns([5, 1])
            if _fx_btn_col.button("↻ Rate", key="log_fx_refresh", help="Force a live re-fetch"):
                st.session_state.pop(f"_fx_{_pair_key}", None)
                st.session_state.pop(f"_fx_{_pair_key}_ts", None)

            _fetched_rate, _fx_source = _get_fx_rate(instr_ccy, account_ccy)

            if _fetched_rate is not None:
                fx_rate = _fetched_rate
                if _fx_source in ("live", "session"):
                    _src_label = "live" if _fx_source == "live" else "cached <5 min"
                    _fx_info_col.caption(
                        f"1 {account_ccy} = **{fx_rate:.4f}** {instr_ccy}"
                        f" ({fx_rate_pair}) — {_src_label}"
                    )
                else:
                    # DB fallback — still usable but flag it
                    _db_ts = _fx_source.split(":", 1)[1] if ":" in _fx_source else "unknown"
                    _fx_info_col.warning(
                        f"Live rate unavailable — using last saved rate from {_db_ts} UTC: "
                        f"1 {account_ccy} = {fx_rate:.4f} {instr_ccy} ({fx_rate_pair})"
                    )
            else:
                # All sources failed — manual entry required
                _fx_info_col.warning(
                    f"FX rate unavailable for {fx_rate_pair} — all fetch methods failed. "
                    f"Enter manually."
                )
                fx_rate = st.number_input(
                    f"FX Rate (1 {account_ccy} = ? {instr_ccy})",
                    min_value=0.0001, value=1.0, step=0.0001, format="%.4f",
                    key="log_fx_rate_manual",
                )

        # Leverage + investment
        _l1, _l2, _l3 = st.columns(3)
        investment_amount = _l1.number_input(
            f"Investment Amount ({account_ccy})",
            min_value=0.0, value=0.0, step=10.0, format="%.2f",
            key="log_investment",
        )
        leverage = _l2.number_input(
            "Leverage", min_value=1, value=1, step=1,
            key="log_leverage",
        )
        position_value   = investment_amount * leverage
        margin_required  = investment_amount / leverage if leverage > 1 else investment_amount
        _l3.markdown(
            f"<div style='padding-top:28px;font-size:0.82rem;color:#94a3b8'>"
            f"{account_ccy} {investment_amount:,.2f} × {leverage} = "
            f"<strong style='color:#e2e8f0'>{account_ccy} {position_value:,.2f}</strong> position"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Overnight rate + swap basis
        _o1, _o2, _o3 = st.columns([2, 2, 3])
        overnight_rate = _o1.number_input(
            "Overnight Rate (%)",
            min_value=0.0, value=0.0, step=0.0001, format="%.4f",
            help="Daily swap as a percentage of the basis amount (e.g. 0.0694)",
            key="log_overnight_rate",
        )
        _BASIS_OPTIONS = ["investment", "margin", "position"]
        _BASIS_LABELS  = {
            "investment": "Investment basis",
            "margin":     "Margin basis",
            "position":   "Position value basis",
        }
        swap_basis = _o2.selectbox(
            "Swap Basis",
            options=_BASIS_OPTIONS,
            format_func=lambda x: _BASIS_LABELS[x],
            index=0,
            help=(
                "Investment basis: rate × investment amount (matches Trading212). "
                "Margin basis: rate × margin locked up. "
                "Position value basis: rate × full position value."
            ),
            key="log_swap_basis",
        )
        _basis_amounts = {
            "investment": investment_amount,
            "margin":     margin_required,
            "position":   position_value,
        }
        _swap_base_amt = _basis_amounts[swap_basis]
        daily_swap   = _swap_base_amt * overnight_rate / 100
        monthly_swap = daily_swap * 30
        if overnight_rate > 0 and _swap_base_amt > 0:
            _o3.markdown(
                f"<div style='padding-top:28px;font-size:0.82rem;color:#94a3b8'>"
                f"{account_ccy} {_swap_base_amt:,.2f} × {overnight_rate:.4f}% = "
                f"<strong style='color:#f59e0b'>{account_ccy} {daily_swap:.2f}/night</strong>"
                f" ({_BASIS_LABELS[swap_basis].lower()})"
                f" · ~{account_ccy} {monthly_swap:.2f} over 30 days"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Spread cost + platform instrument
        _s1, _s2 = st.columns(2)
        spread_cost          = _s1.number_input(
            f"Spread Cost ({account_ccy})", min_value=0.0, value=0.0, step=0.01, format="%.4f",
            key="log_spread_cost",
        )
        default_platform_instr = t212_names.get(sel_ticker, sel_ticker)
        platform_instrument  = _s2.text_input(
            "Platform Instrument (T212 name)",
            value=default_platform_instr,
            key="log_platform_instrument",
        )

        # FX conversion fee — auto-filled to 0.50% for forex pairs, 0% otherwise.
        _fx_fee_default = 0.50 if sel_ticker in FOREX_QUOTE_CCY else 0.0
        _fx_fee_key     = f"log_fx_fee_pct_{sel_ticker}"  # reset when ticker changes
        if st.session_state.get("_log_fx_fee_ticker") != sel_ticker:
            st.session_state["_log_fx_fee_ticker"] = sel_ticker
            st.session_state["log_fx_fee_pct"]     = _fx_fee_default
        fx_fee_pct = st.number_input(
            "FX Conversion Fee (%)",
            min_value=0.0, max_value=5.0,
            value=float(st.session_state.get("log_fx_fee_pct", _fx_fee_default)),
            step=0.01, format="%.2f",
            help="T212 charges 0.50% on currency conversion for forex and cross-currency CFDs.",
            key="log_fx_fee_pct",
        )

        notes_text = st.text_input("Notes (optional)", key="log_notes")

        # ── Trade Plan inputs ─────────────────────────────────────────────────
        _tp_c1, _tp_c2, _tp_c3 = st.columns(3)
        preferred_rr = _tp_c1.number_input(
            "Preferred R:R ratio",
            min_value=0.5, value=float(st.session_state.get("log_preferred_rr", 2.0)),
            step=0.5, format="%.1f",
            help="The R:R ratio used to set your take-profit target (e.g. 2.0 = 2:1).",
            key="log_preferred_rr",
        )
        custom_sl_price = _tp_c2.number_input(
            "Custom Stop Loss (0 = ATR)",
            min_value=0.0, value=0.0, step=0.0001, format="%.4f",
            help="Override the ATR-based stop loss with a specific price level.",
            key="log_custom_sl",
        )
        custom_tp_price = _tp_c3.number_input(
            "Custom Take Profit (0 = R:R calc)",
            min_value=0.0, value=0.0, step=0.0001, format="%.4f",
            help="Override the R:R-based take profit with a specific price level.",
            key="log_custom_tp",
        )

        # ── Investment summary box ─────────────────────────────────────────────
        if investment_amount > 0 and entry_price > 0:
            _ccy = account_ccy

            # Break-even price: spread cost converted to instrument currency, divided by units
            _be_html = ""
            if spread_cost > 0 and units > 0 and fx_rate > 0:
                spread_in_instr = spread_cost * fx_rate  # GBP → instrument ccy
                be_delta        = spread_in_instr / units
                be_price        = (entry_price - be_delta) if direction == "SHORT" else (entry_price + be_delta)
                be_move_pct     = be_delta / entry_price * 100
                _be_html = (
                    f"<span style='color:#94a3b8'>Break-even price:</span>"
                    f"<span style='color:#e2e8f0'> {instr_ccy} {be_price:>9.4f}</span>"
                    f"<span style='color:#6b7280;font-size:0.72rem'>"
                    f" ({direction}: needs {be_move_pct:.4f}% move)</span><br>"
                )

            st.markdown(
                f"<div style='background:#1e293b;border:1px solid #334155;border-radius:6px;"
                f"padding:14px 18px;margin:12px 0;font-family:monospace;font-size:0.82rem;line-height:2'>"
                f"<div style='color:#94a3b8;margin-bottom:6px;font-family:sans-serif;"
                f"font-size:0.75rem;letter-spacing:0.08em;text-transform:uppercase'>Trade Summary</div>"
                f"<span style='color:#94a3b8'>Investment:      </span>"
                f"<span style='color:#e2e8f0'>{_ccy} {investment_amount:>10,.2f}</span><br>"
                f"<span style='color:#94a3b8'>Leverage:        </span>"
                f"<span style='color:#e2e8f0'>1:{leverage:<9}</span><br>"
                f"<span style='color:#94a3b8'>Position Value:  </span>"
                f"<span style='color:#e2e8f0'>{_ccy} {position_value:>10,.2f}</span><br>"
                f"<span style='color:#94a3b8'>Margin Required: </span>"
                f"<span style='color:#e2e8f0'>{_ccy} {margin_required:>10,.2f}</span><br>"
                + (
                    f"<span style='color:#94a3b8'>FX Rate:         </span>"
                    f"<span style='color:#e2e8f0'>1 {account_ccy} = {fx_rate:.4f} {instr_ccy}</span>"
                    f"<span style='color:#6b7280;font-size:0.72rem'> ({fx_rate_pair})</span><br>"
                    if instr_ccy != account_ccy else ""
                )
                + (f"<span style='color:#94a3b8'>Units:           </span>"
                   f"<span style='color:#e2e8f0'>{units:>14.6f}</span><br>"
                   if units > 0 else "")
                + (
                    f"<span style='color:#94a3b8'>Opening Spread:  </span>"
                    f"<span style='color:#e2e8f0'>{_ccy} {spread_cost:>10,.4f}</span><br>"
                    if spread_cost > 0 else ""
                )
                + (
                    f"<span style='color:#94a3b8'>Daily Swap:      </span>"
                    f"<span style='color:#f59e0b'>{_ccy} {daily_swap:>9.2f}/night</span>"
                    f"<span style='color:#6b7280;font-size:0.72rem'> ({_BASIS_LABELS[swap_basis].lower()})</span><br>"
                    f"<span style='color:#94a3b8'>30-day Swap Est: </span>"
                    f"<span style='color:#f59e0b'>{_ccy} {monthly_swap:>10.2f}</span><br>"
                    if overnight_rate > 0 else ""
                )
                + (
                    f"<span style='color:#94a3b8'>FX Conv. Fee:    </span>"
                    f"<span style='color:#6b7280;font-size:0.78rem'>"
                    f"0.50% of P&amp;L applied at close</span><br>"
                    if fx_fee_pct > 0 else ""
                )
                + _be_html
                + f"</div>",
                unsafe_allow_html=True,
            )

            # ── Trade Plan card ────────────────────────────────────────────────
            # Compute effective stop loss
            _tp_atr = _atr_ps  # already fetched above
            if custom_sl_price > 0:
                _eff_sl = custom_sl_price
                _sl_dist = abs(entry_price - custom_sl_price)
            elif _tp_atr is not None:
                _sl_dist = 2 * _tp_atr
                _eff_sl = round(
                    entry_price - _sl_dist if direction == "LONG"
                    else entry_price + _sl_dist, 6
                )
            else:
                _eff_sl = None
                _sl_dist = None

            if _eff_sl is not None and _sl_dist is not None and _sl_dist > 0:
                _sl_pct      = _sl_dist / entry_price * 100
                _gbp_risk    = investment_amount * (_sl_pct / 100) * leverage

                # Three standard TP targets
                _mult_dir = 1 if direction == "LONG" else -1
                _tp1 = round(entry_price + _mult_dir * _sl_dist * 1, 6)
                _tp2 = round(entry_price + _mult_dir * _sl_dist * 2, 6)
                _tp3 = round(entry_price + _mult_dir * _sl_dist * 3, 6)

                # Preferred R:R target (stored)
                _eff_tp = (
                    custom_tp_price if custom_tp_price > 0
                    else round(entry_price + _mult_dir * _sl_dist * preferred_rr, 6)
                )

                _tp1_gbp = _gbp_risk * 1
                _tp2_gbp = _gbp_risk * 2
                _tp3_gbp = _gbp_risk * 3
                _pref_tp_gbp = _gbp_risk * preferred_rr

                _sl_src = "custom" if custom_sl_price > 0 else "2×ATR"
                _tp_src = "custom" if custom_tp_price > 0 else f"{preferred_rr:.1f}:1 R:R"

                # Highlight the preferred R:R row
                def _tp_row(label: str, price: float, gbp: float, is_pref: bool) -> str:
                    _dist = abs(price - entry_price)
                    _col  = "#16a34a" if is_pref else "#94a3b8"
                    _mark = " ★" if is_pref else ""
                    return (
                        f"<span style='color:{_col}'>{html.escape(label)}{html.escape(_mark)}: "
                        f"{instr_ccy} {price:.4f} "
                        f"<span style='color:#6b7280'>({_dist:.4f} away)</span> → "
                        f"<span style='color:#16a34a'>+{account_ccy} {gbp:.2f}</span></span><br>"
                    )

                st.markdown(
                    f"<div style='background:#0a1628;border:1px solid #1e3a5f;border-radius:6px;"
                    f"padding:14px 18px;margin:8px 0;font-family:monospace;font-size:0.82rem;line-height:2'>"
                    f"<div style='color:#60a5fa;margin-bottom:4px;font-family:sans-serif;"
                    f"font-size:0.72rem;letter-spacing:0.08em;text-transform:uppercase;"
                    f"font-weight:600'>Trade Plan</div>"
                    f"<span style='color:#94a3b8'>Stop Loss ({html.escape(_sl_src)}): </span>"
                    f"<span style='color:#ef5350;font-weight:bold'>{instr_ccy} {_eff_sl:.4f}</span>"
                    f"<span style='color:#6b7280;font-size:0.76rem'> ({_sl_dist:.4f} away, "
                    f"{_sl_pct:.3f}%)</span>"
                    f"<span style='color:#f59e0b;font-size:0.76rem'> → ~{account_ccy} {_gbp_risk:.2f} risk</span><br>"
                    f"<span style='color:#475569'>──────────────────────────────────────</span><br>"
                    + _tp_row("TP1 (1:1)", _tp1, _tp1_gbp, preferred_rr == 1.0 and custom_tp_price == 0)
                    + _tp_row("TP2 (2:1)", _tp2, _tp2_gbp, preferred_rr == 2.0 and custom_tp_price == 0)
                    + _tp_row("TP3 (3:1)", _tp3, _tp3_gbp, preferred_rr == 3.0 and custom_tp_price == 0)
                    + (
                        (lambda _ctp_dist=abs(custom_tp_price - entry_price),
                                _ctp_gbp=investment_amount * (abs(custom_tp_price - entry_price) / entry_price) * leverage:
                         f"<span style='color:#c084fc'>Custom TP ★: {instr_ccy} {custom_tp_price:.4f} "
                         f"<span style='color:#6b7280'>({_ctp_dist:.4f} away)</span> → "
                         f"<span style='color:#16a34a'>+{account_ccy} {_ctp_gbp:.2f}</span></span><br>"
                        )()
                        if custom_tp_price > 0 else ""
                    )
                    + (
                        f"<span style='color:#c084fc'>Pref TP ({preferred_rr:.1f}:1) ★: {instr_ccy} {_eff_tp:.4f} "
                        f"<span style='color:#6b7280'>({abs(_eff_tp - entry_price):.4f} away)</span> → "
                        f"<span style='color:#16a34a'>+{account_ccy} {_pref_tp_gbp:.2f}</span></span><br>"
                        if preferred_rr not in (1.0, 2.0, 3.0) and custom_tp_price == 0 else ""
                    )
                    + f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                _eff_sl   = None
                _eff_tp   = None
                _gbp_risk = None

            confirmed = st.checkbox(
                "I confirm this matches what Trading212 shows",
                key="log_confirmed",
            )
        else:
            confirmed = True  # no summary needed if minimal inputs
            _eff_sl   = None
            _eff_tp   = None
            _gbp_risk = None

        # ── Submit ─────────────────────────────────────────────────────────────
        _submit_disabled = (not confirmed) or (entry_price <= 0)
        if st.button(
            "Log Trade", type="primary",
            disabled=_submit_disabled,
            key="log_submit_btn",
        ):
            trade_id = log_trade(
                ticker=sel_ticker,
                direction=direction,
                signal_type=sig.get("signal", ""),
                confluence_score=float(sig.get("confluence_score") or 0),
                regime=sig.get("regime", ""),
                trend_score=int(sig.get("trend_score") or 0),
                mean_reversion_score=int(sig.get("mean_reversion_score") or 0),
                volatility_score=int(sig.get("volatility_score") or 0),
                volume_score=int(sig.get("volume_score") or 0),
                macro_score=int(sig.get("macro_score") or 0),
                entry_price=entry_price,
                quantity=float(units) if units > 0 else 1.0,
                account_id=sel_acct["id"],
                account_name=sel_acct["name"],
                account_type=sel_acct.get("type", "PRACTICE"),
                notes=notes_text,
                currency=instr_ccy,
                account_currency=account_ccy,
                fx_rate_at_entry=fx_rate,
                fx_rate_pair=fx_rate_pair if fx_rate_pair else None,
                leverage=int(leverage),
                investment_amount=float(investment_amount),
                margin_required=float(margin_required),
                overnight_rate=float(overnight_rate),
                swap_basis=swap_basis,
                position_value=float(position_value),
                spread_cost=float(spread_cost) if spread_cost > 0 else None,
                fx_fee_pct=float(fx_fee_pct),
                platform_instrument=platform_instrument if platform_instrument else None,
                stop_loss_price=_eff_sl,
                take_profit_price=_eff_tp,
                risk_reward_ratio=float(preferred_rr) if _eff_sl is not None else None,
                planned_risk_gbp=_gbp_risk,
            )
            # Clear form state so widgets reset to defaults on next render
            for _k in [
                "log_entry_price", "log_units", "log_direction", "log_instr_ccy", "log_acct_ccy",
                "log_investment", "log_leverage", "log_overnight_rate", "log_swap_basis",
                "log_spread_cost", "log_fx_fee_pct", "log_platform_instrument", "log_notes",
                "log_confirmed", "log_fx_rate_manual", "_log_fx_fee_ticker",
                "log_preferred_rr", "log_custom_sl", "log_custom_tp",
            ]:
                st.session_state.pop(_k, None)
            st.success(
                f"Trade #{trade_id} logged for {sel_ticker} "
                f"({html.escape(sel_acct['name'])})."
            )
            st.rerun()

    st.divider()

    # ---- Account filter ----------------------------------------------------
    _tl_filter = st.radio(
        "Filter by account",
        ["All", "Practice", "Live"],
        horizontal=True,
        key="trade_log_acct_filter",
    )
    _tl_acct_type = None if _tl_filter == "All" else _tl_filter.upper()

    st.divider()

    # ---- Open Trades -------------------------------------------------------
    st.markdown("### Open Trades")
    open_trades = get_open_trades(account_type=_tl_acct_type)

    if not open_trades:
        st.info("No open trades. Use the form above to log one.")
    else:
        live_prices = {t["ticker"]: _cached_latest_price(t["ticker"])
                       for t in open_trades}

        # Pre-fetch unique FX pairs (session state cache means no redundant API calls)
        _fx_cache: "dict[tuple, float | None]" = {}
        for t in open_trades:
            ic = t.get("currency") or "GBP"
            ac = t.get("account_currency") or "GBP"
            if ic != ac and (ic, ac) not in _fx_cache:
                _live_rate, _ = _get_fx_rate(ic, ac)
                _fx_cache[(ic, ac)] = _live_rate  # None if all fetch methods failed

        # Pre-fetch live GBPUSD and USDJPY for JPY-pair two-step conversion.
        _has_jpy_trades = any(FOREX_QUOTE_CCY.get(t["ticker"]) == "JPY" for t in open_trades)
        _live_gbpusd_monitor = _cached_latest_price("GBPUSD=X")
        _live_usdjpy_monitor  = _cached_latest_price("USDJPY=X") if _has_jpy_trades else None

        import math as _math
        rows = []
        for t in open_trades:
            ticker      = t["ticker"]
            _raw_live   = live_prices.get(ticker)
            # Treat NaN same as unavailable
            live        = None if (_raw_live is not None and _math.isnan(_raw_live)) else _raw_live
            entry       = float(t.get("entry_price") or 0)
            qty         = float(t.get("quantity") or 0)
            direction   = t.get("direction", "LONG")
            leverage    = int(t.get("leverage") or 1)
            pos_val     = float(t.get("position_value") or 0)
            margin_req  = float(t.get("margin_required") or 0)
            inv_amt     = float(t.get("investment_amount") or 0)
            o_rate      = float(t.get("overnight_rate") or 0)
            s_basis     = t.get("swap_basis") or "investment"
            spread      = float(t.get("spread_cost") or 0)
            fx_fee_pct  = float(t.get("fx_fee_pct") or 0.0)
            instr_ccy   = t.get("currency") or "GBP"
            acct_ccy    = t.get("account_currency") or "GBP"
            # Use fx_rate_at_entry as fallback if live FX fetch failed
            _live_fx    = _fx_cache.get((instr_ccy, acct_ccy))
            _entry_fx   = t.get("fx_rate_at_entry")
            fx          = (_live_fx if (_live_fx is not None and not _math.isnan(_live_fx))
                           else (float(_entry_fx) if _entry_fx else 1.0))
            entry_dt    = pd.Timestamp(t["entry_date"]) if t.get("entry_date") else None
            days_held   = int((pd.Timestamp.now() - entry_dt).days) if entry_dt else 0
            mult        = 1 if direction == "LONG" else -1

            try:
                # Spread-adjusted effective close price.
                # SHORT closes at ASK (buy back at higher price); LONG closes at BID (lower).
                # spread_cost is stored in account currency (GBP); convert to instrument currency.
                effective_live = None
                if live is not None:
                    if spread > 0 and qty > 0:
                        spread_adj = (spread * fx) / qty  # GBP spread → instrument ccy per unit
                        effective_live = live + spread_adj if direction == "SHORT" else live - spread_adj
                    else:
                        effective_live = live

                # P&L in the instrument's quote currency using the effective close price
                pnl_instr = None
                if effective_live is not None and entry and qty:
                    pnl_instr = (effective_live - entry) * qty * mult

                # P&L in account currency (GBP).
                # JPY-quoted pairs (USDJPY etc.) need a two-step conversion:
                #   JPY → USD (divide by live USDJPY rate)
                #   USD → GBP (divide by live GBPUSD rate)
                _quote_ccy = FOREX_QUOTE_CCY.get(ticker)
                if _quote_ccy == "JPY" and pnl_instr is not None:
                    if (_live_usdjpy_monitor and _live_gbpusd_monitor
                            and _live_usdjpy_monitor > 0 and _live_gbpusd_monitor > 0):
                        pnl_acct = (pnl_instr / _live_usdjpy_monitor) / _live_gbpusd_monitor
                    else:
                        pnl_acct = None  # rates unavailable; can't convert
                else:
                    pnl_acct = (pnl_instr / fx) if (pnl_instr is not None and fx > 0) else None

                # Position P&L %: percentage price move in your direction
                # SHORT: (entry - effective_live) / entry × 100
                # LONG:  (effective_live - entry) / entry × 100
                pos_pnl_pct = None
                if effective_live is not None and entry:
                    pos_pnl_pct = mult * (effective_live - entry) / entry * 100

                # Margin P&L %: Position P&L % × leverage (return on margin locked up)
                margin_pnl_pct = (pos_pnl_pct * leverage) if pos_pnl_pct is not None else None

                # Accrued swap — always a cost (negative); use stored basis.
                # Leveraged CFDs with 0% overnight rate show £0.00, not "—".
                _swap_amt    = inv_amt if s_basis == "investment" else (margin_req if s_basis == "margin" else pos_val)
                daily_swap   = _swap_amt * o_rate / 100 if (_swap_amt > 0 and o_rate > 0) else 0.0
                accrued_swap = -(daily_swap * days_held) if daily_swap > 0 else (0.0 if leverage > 1 else None)

                # FX conversion fee: estimated on current unrealised P&L (applied at close, not at open)
                fx_fee_cost = -(abs(pnl_acct) * fx_fee_pct / 100) if (fx_fee_pct > 0 and pnl_acct is not None) else None

                # Net P&L = price move + swap + estimated FX fee (both costs are negative)
                if pnl_acct is not None:
                    net_pnl = pnl_acct
                    if accrued_swap is not None:
                        net_pnl += accrued_swap
                    if fx_fee_cost is not None:
                        net_pnl += fx_fee_cost
                else:
                    net_pnl = None
            except Exception:
                effective_live = pnl_instr = pnl_acct = pos_pnl_pct = margin_pnl_pct = accrued_swap = fx_fee_cost = net_pnl = None

            # SL / TP from stored trade plan
            _stored_sl = t.get("stop_loss_price")
            _stored_tp = t.get("take_profit_price")
            sl_price   = float(_stored_sl) if _stored_sl is not None else None
            tp_price   = float(_stored_tp) if _stored_tp is not None else None

            # Distance to SL and TP from current live price
            dist_to_sl  = None
            pct_to_sl   = None
            dist_to_tp  = None
            pct_to_tp   = None
            live_rr     = None
            progress    = None

            try:
                if live is not None and entry > 0:
                    if sl_price is not None:
                        # Distance is directional: positive = moving toward SL (bad)
                        dist_to_sl = mult * (sl_price - live)   # negative = away from SL
                        pct_to_sl  = dist_to_sl / entry * 100
                        # Current R:R = how much gained relative to risk taken
                        sl_risk = abs(entry - sl_price)
                        if sl_risk > 0:
                            live_rr = mult * (live - entry) / sl_risk

                    if tp_price is not None:
                        dist_to_tp = mult * (tp_price - live)   # positive = still has room
                        pct_to_tp  = dist_to_tp / entry * 100

                    if sl_price is not None and tp_price is not None:
                        total_range = abs(tp_price - sl_price)
                        if total_range > 0:
                            if direction == "LONG":
                                progress = max(0.0, min(100.0, (live - sl_price) / total_range * 100))
                            else:
                                progress = max(0.0, min(100.0, (sl_price - live) / total_range * 100))
            except Exception:
                pass

            rows.append({
                "ID":           t["id"],
                "Type":         t.get("account_type") or "PRACTICE",
                "Ticker":       ticker,
                "Dir":          direction,
                "Ccy":          instr_ccy,
                "Units":        qty if qty > 0 else None,
                "Entry":        entry,
                "Live":         live,
                "SL":           sl_price,
                "TP":           tp_price,
                "% to SL":      pct_to_sl,    # positive = price moving toward SL
                "% to TP":      pct_to_tp,    # positive = still has room to TP
                "Live R:R":     live_rr,       # positive = in profit vs risk
                "Progress":     progress,      # 0–100, where 0=SL, 100=TP
                "P&L (raw)":    (f"{_quote_ccy_sym(ticker)}{pnl_instr:+.4f}"
                                 if isinstance(pnl_instr, float) else "—"),
                "P&L £":        pnl_acct,     # in account currency (GBP)
                "Margin P&L %": margin_pnl_pct,
                "Swap":         accrued_swap,  # negative — always a cost
                "FX Fee £":     fx_fee_cost,   # estimated: 0.50% of current P&L (negative = cost)
                "Net P&L £":    net_pnl,
                "Days":         days_held,
            })

        open_df = pd.DataFrame(rows)

        def _style_signed(val):
            if isinstance(val, str):
                # P&L (raw) is pre-formatted as e.g. "$+0.5300" or "¥-25.53"
                s = val.lstrip("£$¥€")
                if s.startswith("+"): return "color:#16a34a;font-weight:bold"
                if s.startswith("-"): return "color:#dc2626;font-weight:bold"
                return ""
            if not isinstance(val, (int, float)): return ""
            return ("color:#16a34a;font-weight:bold" if val > 0
                    else "color:#dc2626;font-weight:bold" if val < 0 else "")

        def _style_swap(val):
            if not isinstance(val, (int, float)): return ""
            return "color:#f59e0b;font-weight:bold"  # amber — always a cost

        def _style_acct_badge(val):
            if val == "LIVE":     return "color:#6ee7b7;font-weight:bold"
            if val == "PRACTICE": return "color:#a5b4fc"
            return ""

        def _style_pct_to_sl(val):
            if not isinstance(val, (int, float)): return ""
            # Positive = moving toward SL (bad); negative = moving away from SL (good)
            return "color:#dc2626;font-weight:bold" if val > 0 else "color:#16a34a"

        def _style_pct_to_tp(val):
            if not isinstance(val, (int, float)): return ""
            # Positive = still has room to reach TP (good); negative = overshot TP
            return "color:#16a34a;font-weight:bold" if val > 0 else "color:#a78bfa"

        _has_sl_tp = any(r.get("SL") is not None or r.get("TP") is not None for r in rows)

        _fmt: dict = {
            "Entry":        "{:,.4f}",
            "Live":         lambda v: f"{v:,.4f}" if isinstance(v, float) else "—",
            "Units":        lambda v: f"{v:.6f}" if isinstance(v, float) else "—",
            "SL":           lambda v: f"{v:,.4f}" if isinstance(v, float) else "—",
            "TP":           lambda v: f"{v:,.4f}" if isinstance(v, float) else "—",
            "% to SL":      lambda v: f"{v:+.2f}%" if isinstance(v, float) else "—",
            "% to TP":      lambda v: f"{v:+.2f}%" if isinstance(v, float) else "—",
            "Live R:R":     lambda v: f"{v:+.2f}" if isinstance(v, float) else "—",
            "P&L (raw)":    lambda v: v if isinstance(v, str) else "—",
            "P&L £":        lambda v: f"£{v:+.2f}" if isinstance(v, float) else "—",
            "Margin P&L %": lambda v: f"{v:+.2f}%" if isinstance(v, float) else "—",
            "Swap":         lambda v: f"£{v:.2f}" if isinstance(v, float) else "—",
            "FX Fee £":     lambda v: f"£{v:.4f}" if isinstance(v, float) else "—",
            "Net P&L £":    lambda v: f"£{v:+.2f}" if isinstance(v, float) else "—",
        }

        _styler_cols_base = ["P&L (raw)", "P&L £", "Margin P&L %", "Net P&L £", "Live R:R"]
        _styler = (
            open_df.style
            .map(_style_acct_badge, subset=["Type"])
            .map(_style_signed,     subset=_styler_cols_base)
            .map(_style_swap,       subset=["Swap", "FX Fee £"])
        )
        if _has_sl_tp:
            _styler = (
                _styler
                .map(_style_pct_to_sl, subset=["% to SL"])
                .map(_style_pct_to_tp, subset=["% to TP"])
            )
        _styler = _styler.format(_fmt)

        _col_cfg = {}
        if _has_sl_tp:
            _col_cfg["Progress"] = st.column_config.ProgressColumn(
                "SL→TP",
                help="Position between Stop Loss (0%) and Take Profit (100%).",
                min_value=0,
                max_value=100,
                format="%.0f%%",
            )

        st.dataframe(_styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(rows),
                     column_config=_col_cfg if _col_cfg else None)
        st.caption(
            "**Live price is from the yfinance delayed feed — minor differences from "
            "Trading212's live price are expected.** "
            "P&L uses a spread-adjusted close price (SHORT: +spread/units, LONG: −spread/units). "
            "Margin P&L % = position price move % × leverage. "
            "Ccy = instrument currency; P&L (raw) in instrument currency, P&L £ / Net P&L £ in GBP. "
            "Swap = accrued overnight cost (basis × rate × days). "
            "FX Fee £ = estimated 0.50% of current unrealised P&L. "
            "SL/TP = stored from trade plan; % to SL (red = price moving toward stop). "
            "Live R:R = current gain / planned risk (positive = in profit vs risk taken). "
            "SL→TP progress bar: 0% = at stop loss, 100% = at take profit."
        )

    st.divider()

    # ---- Close a Trade -----------------------------------------------------
    if open_trades:
        st.markdown("### Close a Trade")
        trade_labels = {
            t["id"]: (
                f"#{t['id']}  {t['ticker']}  "
                f"[{t.get('account_type') or 'PRACTICE'}]  "
                f"{(t.get('signal_type') or '').replace('_', ' ')}  "
                f"@ {float(t.get('entry_price') or 0):,.4f}"
            )
            for t in open_trades
        }
        with st.form("close_trade_form"):
            selected_id = st.selectbox(
                "Select trade to close",
                options=list(trade_labels.keys()),
                format_func=lambda x: trade_labels[x],
            )
            exit_price = st.number_input("Exit Price", min_value=0.0, step=0.01, format="%.4f")
            close_btn  = st.form_submit_button("Close Trade", type="primary")

            if close_btn:
                if exit_price <= 0:
                    st.error("Exit price must be greater than 0.")
                else:
                    result = close_trade(selected_id, exit_price)
                    pnl    = result["pnl"]
                    icon   = "🟢" if pnl >= 0 else "🔴"
                    st.success(
                        f"{icon} Closed {result['ticker']} — "
                        f"P&L: {pnl:+.4f} pts  ({result['pnl_pct']:+.2f}%)  "
                        f"over {result['holding_days']} day(s)."
                    )
                    updated = maybe_update_weights(_load_config())
                    if updated:
                        st.info("System Learning: indicator weights recalculated from trade history.")
                    st.rerun()


# ---------------------------------------------------------------------------
# Performance page
# ---------------------------------------------------------------------------

_PNL_GREEN = "#16a34a"
_PNL_RED   = "#dc2626"


def _pnl_colour(val: float) -> str:
    return _PNL_GREEN if val >= 0 else _PNL_RED


def _render_performance_page(config: dict, watchlist: dict[str, str]) -> None:
    st.markdown("## 🏆 Performance")

    # ---- Account filter ----------------------------------------------------
    acct_filter = st.radio(
        "Account",
        ["All", "Practice", "Live"],
        horizontal=True,
        key="perf_acct_filter",
    )
    acct_type = None if acct_filter == "All" else acct_filter.upper()

    st.divider()

    # ---- Date range filter -------------------------------------------------
    with st.expander("📅 Date range filter", expanded=False):
        fc1, fc2 = st.columns(2)
        from_date: date | None = fc1.date_input("From (exit date)", value=None)
        to_date:   date | None = fc2.date_input("To (exit date)",   value=None)

    from_dt = datetime(from_date.year, from_date.month, from_date.day) if from_date else None
    to_dt   = datetime(to_date.year,  to_date.month,  to_date.day, 23, 59, 59) if to_date else None

    all_trades    = get_all_trades(account_type=acct_type)
    closed_trades = get_closed_trades(from_dt, to_dt, account_type=acct_type)

    # ---- Summary stats -----------------------------------------------------
    if acct_filter == "All":
        p_all     = get_all_trades(account_type="PRACTICE")
        l_all     = get_all_trades(account_type="LIVE")
        p_summary = accuracy_summary(p_all)
        l_summary = accuracy_summary(l_all)

        if not p_summary and not l_summary:
            st.info("No closed trades yet — close trades on the **Trade Log** page to see performance stats.")
        else:
            pc1, pc2 = st.columns(2)
            with pc1:
                st.markdown("#### Practice")
                if p_summary:
                    a1, a2, a3 = st.columns(3)
                    a1.metric("Trades",        p_summary["total_trades"])
                    a2.metric("Win Rate",      f"{p_summary['win_rate']:.0%}")
                    a3.metric("Profit Factor", f"{p_summary['profit_factor']:.2f}" if p_summary["profit_factor"] != float("inf") else "∞")
                    b1, b2 = st.columns(2)
                    b1.metric("Avg Win %",  f"{p_summary['avg_win_pct']:+.2f}%")
                    b2.metric("Avg Loss %", f"{p_summary['avg_loss_pct']:+.2f}%")
                else:
                    st.caption("No practice trades closed yet.")

            with pc2:
                st.markdown("#### Live")
                if l_summary:
                    a1, a2, a3 = st.columns(3)
                    a1.metric("Trades",        l_summary["total_trades"])
                    a2.metric("Win Rate",      f"{l_summary['win_rate']:.0%}")
                    a3.metric("Profit Factor", f"{l_summary['profit_factor']:.2f}" if l_summary["profit_factor"] != float("inf") else "∞")
                    b1, b2 = st.columns(2)
                    b1.metric("Avg Win %",  f"{l_summary['avg_win_pct']:+.2f}%")
                    b2.metric("Avg Loss %", f"{l_summary['avg_loss_pct']:+.2f}%")
                else:
                    st.caption("No live trades closed yet.")
    else:
        summary = accuracy_summary(all_trades)
        if not summary:
            st.info(
                f"No closed {acct_filter.lower()} trades yet — "
                "close trades on the **Trade Log** page to see performance stats."
            )
        else:
            s1, s2, s3, s4, s5 = st.columns(5)
            s1.metric("Total Trades",  summary["total_trades"])
            s2.metric("Win Rate",      f"{summary['win_rate']:.0%}")
            s3.metric("Profit Factor", f"{summary['profit_factor']:.2f}" if summary["profit_factor"] != float("inf") else "∞")
            s4.metric("Best Trade",    f"{summary['best_trade']:+.4f}",  delta=f"{summary['best_trade']:+.4f}")
            s5.metric("Worst Trade",   f"{summary['worst_trade']:+.4f}", delta=f"{summary['worst_trade']:+.4f}")
            col_l, col_r = st.columns(2)
            col_l.metric("Avg Win %",  f"{summary['avg_win_pct']:+.2f}%")
            col_r.metric("Avg Loss %", f"{summary['avg_loss_pct']:+.2f}%")

    st.divider()

    # ---- Equity curve ------------------------------------------------------
    st.markdown("### Equity Curve")
    if acct_filter == "All":
        p_closed = get_closed_trades(from_dt, to_dt, account_type="PRACTICE")
        l_closed = get_closed_trades(from_dt, to_dt, account_type="LIVE")
        p_curve  = equity_curve(p_closed)
        l_curve  = equity_curve(l_closed)

        if not p_curve.empty or not l_curve.empty:
            fig_curve = go.Figure()
            fig_curve.add_hline(y=0, line_dash="dot", line_color="grey", line_width=1)
            if not p_curve.empty:
                fig_curve.add_trace(go.Scatter(
                    x=p_curve["exit_date"],
                    y=p_curve["cumulative_pnl"],
                    name="Practice",
                    mode="lines+markers",
                    line=dict(color="#818cf8", width=2, dash="dash"),
                    hovertemplate=(
                        "<b>%{x|%Y-%m-%d}</b><br>"
                        "Practice P&L: %{y:+.4f}<extra></extra>"
                    ),
                ))
            if not l_curve.empty:
                fig_curve.add_trace(go.Scatter(
                    x=l_curve["exit_date"],
                    y=l_curve["cumulative_pnl"],
                    name="Live",
                    mode="lines+markers",
                    line=dict(color="#16a34a", width=2),
                    hovertemplate=(
                        "<b>%{x|%Y-%m-%d}</b><br>"
                        "Live P&L: %{y:+.4f}<extra></extra>"
                    ),
                ))
            fig_curve.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=40, r=20, t=20, b=40),
                xaxis_title="Exit Date",
                yaxis_title="Cumulative P&L (pts)",
                legend=dict(x=0.01, y=0.99),
            )
            st.plotly_chart(fig_curve, use_container_width=True)
        else:
            st.caption("No closed trades to plot yet.")
    else:
        curve_df = equity_curve(closed_trades if closed_trades else all_trades)
        if not curve_df.empty:
            final_pnl    = float(curve_df["cumulative_pnl"].iloc[-1])
            curve_colour = _pnl_colour(final_pnl)

            fig_curve = go.Figure()
            fig_curve.add_hline(y=0, line_dash="dot", line_color="grey", line_width=1)
            fig_curve.add_trace(go.Scatter(
                x=curve_df["exit_date"],
                y=curve_df["cumulative_pnl"],
                mode="lines+markers",
                line=dict(color=curve_colour, width=2),
                fill="tozeroy",
                fillcolor=f"rgba({'22,163,74' if final_pnl >= 0 else '220,38,38'},0.12)",
                hovertemplate=(
                    "<b>%{x|%Y-%m-%d}</b><br>"
                    "Cumulative P&L: %{y:+.4f}<extra></extra>"
                ),
            ))
            fig_curve.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=40, r=20, t=20, b=40),
                xaxis_title="Exit Date",
                yaxis_title="Cumulative P&L (pts)",
            )
            st.plotly_chart(fig_curve, use_container_width=True)

    st.divider()

    # ---- Signal Validation (practice trades) --------------------------------
    if acct_filter in ("All", "Practice"):
        st.markdown("### Signal Validation")
        st.caption(
            "Measures how often confluence signals correctly predicted outcomes "
            "in **practice** trades — use this to gauge readiness for live trading."
        )

        practice_for_val = get_all_trades(account_type="PRACTICE")
        val = signal_validation(practice_for_val)
        MIN = val["min_trades"]

        if val["confidence_level"] == "insufficient_data":
            needed = MIN - val["directional"]
            st.info(
                f"{val['directional']} / {MIN} directional practice trades completed — "
                f"need {needed} more for a confidence reading."
            )
            if val["directional"] > 0:
                st.progress(min(val["directional"] / MIN, 1.0))
        else:
            v1, v2, v3 = st.columns(3)
            v1.metric("Directional Signals", val["directional"])
            v2.metric("Correct Calls",        val["correct"])
            v3.metric("Signal Accuracy",      f"{val['accuracy']:.0%}")

            _conf_colours = {
                "strong":   ("#16a34a", "Strong"),
                "moderate": ("#d97706", "Moderate"),
                "weak":     ("#dc2626", "Weak"),
            }
            card_colour, card_label = _conf_colours.get(
                val["confidence_level"], ("#6b7280", "Unknown")
            )
            rec_text = html.escape(val["recommendation"] or "")
            st.markdown(
                f"<div style='border-left:4px solid {card_colour};padding:8px 16px;"
                f"background:rgba(0,0,0,0.2);border-radius:0 4px 4px 0;margin-top:8px'>"
                f"<strong style='color:{card_colour}'>Confidence: {card_label}</strong>"
                f"<br><span style='font-size:0.88rem'>{rec_text}</span></div>",
                unsafe_allow_html=True,
            )

        st.divider()

    # ---- Win rate by signal type -------------------------------------------
    sig_df = accuracy_by_signal_type(closed_trades)
    reg_df = accuracy_by_regime(closed_trades)

    if not sig_df.empty or not reg_df.empty:
        ch1, ch2 = st.columns(2)

        if not sig_df.empty:
            with ch1:
                st.markdown("### Win Rate by Signal Type")
                bar_colours = [_pnl_colour(r - 0.5) for r in sig_df["Win Rate"]]
                fig_sig = go.Figure(go.Bar(
                    x=sig_df["Signal"],
                    y=sig_df["Win Rate"] * 100,
                    marker_color=bar_colours,
                    text=[f"{r:.0%}" for r in sig_df["Win Rate"]],
                    textposition="outside",
                ))
                fig_sig.add_hline(y=50, line_dash="dot", line_color="grey", line_width=1)
                fig_sig.update_layout(
                    template="plotly_dark",
                    height=300,
                    margin=dict(l=20, r=20, t=20, b=60),
                    yaxis=dict(title="Win Rate (%)", range=[0, 110]),
                    showlegend=False,
                )
                st.plotly_chart(fig_sig, use_container_width=True)

        if not reg_df.empty:
            with ch2:
                st.markdown("### Win Rate by Regime")
                bar_colours = [_pnl_colour(r - 0.5) for r in reg_df["Win Rate"]]
                fig_reg = go.Figure(go.Bar(
                    x=reg_df["Regime"],
                    y=reg_df["Win Rate"] * 100,
                    marker_color=bar_colours,
                    text=[f"{r:.0%}" for r in reg_df["Win Rate"]],
                    textposition="outside",
                ))
                fig_reg.add_hline(y=50, line_dash="dot", line_color="grey", line_width=1)
                fig_reg.update_layout(
                    template="plotly_dark",
                    height=300,
                    margin=dict(l=20, r=20, t=20, b=60),
                    yaxis=dict(title="Win Rate (%)", range=[0, 110]),
                    showlegend=False,
                )
                st.plotly_chart(fig_reg, use_container_width=True)

        st.divider()

    # ---- Indicator accuracy ------------------------------------------------
    ind_df = accuracy_by_indicator(closed_trades)

    if not ind_df.empty:
        st.markdown("### Indicator Accuracy")
        st.caption(
            "Hit rate = (bullish calls that were profitable + "
            "bearish calls that lost) / total directional calls"
        )

        def _style_hit(val):
            if val is None or not isinstance(val, float):
                return ""
            if val >= 0.6:  return f"color:{_PNL_GREEN};font-weight:bold"
            if val <= 0.4:  return f"color:{_PNL_RED};font-weight:bold"
            return ""

        ind_styler = (
            ind_df.style
            .map(_style_hit, subset=["Hit Rate"])
            .format({"Hit Rate": lambda v: f"{v:.0%}" if isinstance(v, float) else "—"})
        )
        st.dataframe(ind_styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(ind_df))

    st.divider()

    # ---- Closed trades table -----------------------------------------------
    if closed_trades:
        st.markdown("### Closed Trades")
        show_acct_col = acct_filter == "All"
        ct_rows = []
        for t in reversed(closed_trades):     # most recent first
            row = {
                "Ticker":  t.get("ticker", ""),
                "Signal":  (t.get("signal_type") or "—").replace("_", " "),
                "Dir":     t.get("direction", "LONG"),
                "Entry":   float(t.get("entry_price") or 0),
                "Exit":    float(t.get("exit_price")  or 0),
                "P&L pts": float(t.get("pnl")         or 0),
                "P&L %":   float(t.get("pnl_pct")     or 0),
                "Days":    t.get("holding_days"),
                "Regime":  (t.get("regime") or "—").replace("_", " "),
            }
            if show_acct_col:
                row["Account"] = t.get("account_type") or "PRACTICE"
            ct_rows.append(row)

        ct_df = pd.DataFrame(ct_rows)

        def _style_pnl(val):
            if not isinstance(val, (int, float)):
                return ""
            return f"color:{_pnl_colour(val)};font-weight:bold"

        def _style_acct_col(val):
            if val == "LIVE":     return "color:#6ee7b7;font-weight:bold"
            if val == "PRACTICE": return "color:#a5b4fc"
            return ""

        ct_styler = (
            ct_df.style
            .map(_style_pnl, subset=["P&L pts", "P&L %"])
            .format({
                "Entry":   "{:,.4f}",
                "Exit":    "{:,.4f}",
                "P&L pts": "{:+.4f}",
                "P&L %":   "{:+.2f}%",
            })
        )
        if show_acct_col:
            ct_styler = ct_styler.map(_style_acct_col, subset=["Account"])
        st.dataframe(ct_styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(ct_rows))

    # ---- R:R Analysis -------------------------------------------------------
    _rr_trades = [
        t for t in closed_trades
        if t.get("planned_risk_gbp") and float(t["planned_risk_gbp"]) > 0
        and t.get("risk_reward_ratio") and t.get("pnl") is not None
    ]
    if _rr_trades:
        st.divider()
        st.markdown("### R:R Analysis")
        st.caption(
            "Planned R:R is the target set at entry. Actual R:R = realised P&L ÷ planned GBP risk. "
            "Outcome tells you whether the trade hit, missed, or beat the plan."
        )

        _rr_rows = []
        for _t in reversed(_rr_trades):
            _planned_rr   = float(_t["risk_reward_ratio"])
            _planned_risk = float(_t["planned_risk_gbp"])
            _pnl          = float(_t["pnl"])
            _actual_rr    = _pnl / _planned_risk

            if _pnl > 0 and _actual_rr >= _planned_rr:
                _outcome = "✅ Hit plan"
            elif _pnl > 0 and _actual_rr < _planned_rr:
                _outcome = "⚠ Won but short of plan"
            elif _pnl < 0:
                _outcome = "❌ Loss"
            else:
                _outcome = "— Flat"

            _rr_rows.append({
                "Ticker":       _t.get("ticker", ""),
                "Dir":          _t.get("direction", ""),
                "Planned R:R":  _planned_rr,
                "Planned Risk": _planned_risk,
                "P&L £":        _pnl,
                "Actual R:R":   _actual_rr,
                "Outcome":      _outcome,
            })

        _rr_df = pd.DataFrame(_rr_rows)

        def _style_rr_actual(val):
            if not isinstance(val, (int, float)): return ""
            return "color:#16a34a;font-weight:bold" if val >= 0 else "color:#dc2626;font-weight:bold"

        def _style_rr_outcome(val):
            if "Hit plan"  in str(val): return "color:#16a34a;font-weight:bold"
            if "short of"  in str(val): return "color:#f59e0b"
            if "Loss"      in str(val): return "color:#dc2626;font-weight:bold"
            return ""

        _rr_styler = (
            _rr_df.style
            .map(_style_rr_actual,  subset=["Actual R:R", "P&L £"])
            .map(_style_rr_outcome, subset=["Outcome"])
            .format({
                "Planned R:R":  "{:.1f}:1",
                "Planned Risk": "£{:.2f}",
                "P&L £":        lambda v: f"{v:+.2f}" if isinstance(v, float) else "—",
                "Actual R:R":   lambda v: f"{v:+.2f}:1" if isinstance(v, float) else "—",
            })
        )
        st.dataframe(_rr_styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(_rr_rows))

        # Summary stats
        _avg_planned = sum(float(t["risk_reward_ratio"]) for t in _rr_trades) / len(_rr_trades)
        _avg_actual  = sum(float(t["pnl"]) / float(t["planned_risk_gbp"]) for t in _rr_trades) / len(_rr_trades)
        _hit_count   = sum(
            1 for t in _rr_trades
            if float(t["pnl"]) > 0 and float(t["pnl"]) / float(t["planned_risk_gbp"]) >= float(t["risk_reward_ratio"])
        )
        _rr_s1, _rr_s2, _rr_s3 = st.columns(3)
        _rr_s1.metric("Avg Planned R:R",  f"{_avg_planned:.2f}:1")
        _rr_s2.metric("Avg Actual R:R",   f"{_avg_actual:+.2f}:1", delta=f"{_avg_actual - _avg_planned:+.2f}")
        _rr_s3.metric("Hit Plan Rate",     f"{_hit_count}/{len(_rr_trades)} ({_hit_count/len(_rr_trades):.0%})")

    # ---- System Learning ---------------------------------------------------
    _render_learning_dashboard(config)


def _render_learning_dashboard(config: dict) -> None:
    """Learning dashboard: indicator accuracy, weight evolution, and diagnostics."""
    if not config.get("learning", {}).get("enabled", True):
        return

    st.divider()

    with st.expander("🧠 System Learning", expanded=False):
        learning_cfg  = config.get("learning", {})
        min_trades    = learning_cfg.get("min_trades_before_adjustment", 20)
        freq          = learning_cfg.get("weight_update_frequency", 10)

        # Count all closed trades (unfiltered — learning uses the full history)
        from output.performance import get_closed_trades as _get_all_closed
        all_closed = _get_all_closed()
        n_closed   = len(all_closed)

        # Status banner
        learned = get_learned_weights()
        if learned is None:
            needed = min_trades - n_closed
            st.info(
                f"Collecting data — {n_closed} / {min_trades} closed trades needed "
                f"before the first weight adjustment. {max(0, needed)} more to go."
            )
            st.caption(
                "Until then, signals use the default regime-based weights. "
                "Close more practice trades to build the accuracy dataset."
            )
            return

        n_learned = learned["n_trades"]
        next_update_at = min_trades + (((n_closed - min_trades) // freq) + 1) * freq
        trades_until_next = max(0, next_update_at - n_closed)

        st.success(
            f"Active — weights last recalculated at {n_learned} closed trades. "
            f"Next recalculation in {trades_until_next} trade(s) "
            f"(at {next_update_at} total)."
        )

        # ── Weight comparison table ──────────────────────────────────────────
        st.markdown("#### Current Indicator Weights")
        st.caption(
            "Accuracy-derived weights blend 40 % with regime-specific weights at signal generation. "
            "Equal base = 1/6 per module."
        )

        lw   = learned["weights"]
        wr   = learned.get("win_rates", {})
        base = 1.0 / 6
        _MODULE_LABELS = {
            "trend":          "Trend",
            "mean_reversion": "Mean Reversion",
            "volatility":     "Volatility",
            "volume":         "Volume",
            "macro":          "Macro",
            "correlation":    "Correlation",
        }

        wt_rows = []
        for key, label in _MODULE_LABELS.items():
            learned_pct = lw.get(key, base)
            win_rate    = wr.get(key)
            wt_rows.append({
                "Indicator": label,
                "Equal Base": base,
                "Accuracy Weight": learned_pct,
                "Delta": learned_pct - base,
                "Win Rate": win_rate if win_rate is not None else float("nan"),
            })

        wt_df = pd.DataFrame(wt_rows)

        def _style_delta(val):
            if not isinstance(val, float) or pd.isna(val):
                return ""
            if val > 0.005:  return f"color:{_PNL_GREEN};font-weight:bold"
            if val < -0.005: return f"color:{_PNL_RED};font-weight:bold"
            return ""

        def _style_wr(val):
            if not isinstance(val, float) or pd.isna(val):
                return ""
            if val >= 0.6:  return f"color:{_PNL_GREEN};font-weight:bold"
            if val <= 0.4:  return f"color:{_PNL_RED};font-weight:bold"
            return ""

        wt_styler = (
            wt_df.style
            .map(_style_delta, subset=["Delta"])
            .map(_style_wr,    subset=["Win Rate"])
            .format({
                "Equal Base":       "{:.1%}",
                "Accuracy Weight":  "{:.1%}",
                "Delta":            "{:+.1%}",
                "Win Rate":         lambda v: f"{v:.0%}" if isinstance(v, float) and not pd.isna(v) else "—",
            })
        )
        st.dataframe(wt_styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(wt_df))

        st.divider()

        # ── Confluence band and asset class breakdowns ───────────────────────
        band_df  = accuracy_by_confluence_band(all_closed)
        ticker_to_group = {
            entry["ticker"]: group_key
            for group_key, group_entries in config.get("watchlist", {}).items()
            if isinstance(group_entries, list)
            for entry in group_entries
            if isinstance(entry, dict) and "ticker" in entry
        }
        class_df = accuracy_by_asset_class(all_closed, ticker_to_group)

        if not band_df.empty or not class_df.empty:
            bl1, bl2 = st.columns(2)

            if not band_df.empty:
                with bl1:
                    st.markdown("#### Win Rate by Signal Strength")
                    fig_band = go.Figure(go.Bar(
                        x=band_df["Band"],
                        y=band_df["Win Rate"] * 100,
                        marker_color=[_pnl_colour(r - 0.5) for r in band_df["Win Rate"]],
                        text=[f"{r:.0%}" for r in band_df["Win Rate"]],
                        textposition="outside",
                    ))
                    fig_band.add_hline(y=50, line_dash="dot", line_color="grey", line_width=1)
                    fig_band.update_layout(
                        template="plotly_dark", height=280,
                        margin=dict(l=10, r=10, t=10, b=50),
                        yaxis=dict(title="Win Rate (%)", range=[0, 115]),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_band, use_container_width=True)

            if not class_df.empty:
                with bl2:
                    st.markdown("#### Win Rate by Asset Class")
                    fig_cls = go.Figure(go.Bar(
                        x=class_df["Asset Class"],
                        y=class_df["Win Rate"] * 100,
                        marker_color=[_pnl_colour(r - 0.5) for r in class_df["Win Rate"]],
                        text=[f"{r:.0%}" for r in class_df["Win Rate"]],
                        textposition="outside",
                    ))
                    fig_cls.add_hline(y=50, line_dash="dot", line_color="grey", line_width=1)
                    fig_cls.update_layout(
                        template="plotly_dark", height=280,
                        margin=dict(l=10, r=10, t=10, b=80),
                        yaxis=dict(title="Win Rate (%)", range=[0, 115]),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_cls, use_container_width=True)

            st.divider()

        # ── Weight evolution timeline ────────────────────────────────────────
        history = get_weight_history()
        if len(history) >= 2:
            st.markdown("#### Weight Evolution")
            hist_rows = []
            for snap in history:
                row = {"Trades": snap["n_trades"]}
                for key, label in _MODULE_LABELS.items():
                    row[label] = snap["weights"].get(key, base)
                hist_rows.append(row)
            hist_df = pd.DataFrame(hist_rows)

            fig_hist = go.Figure()
            _HIST_COLOURS = ["#60a5fa", "#34d399", "#f59e0b", "#f87171", "#a78bfa", "#94a3b8"]
            for i, (key, label) in enumerate(_MODULE_LABELS.items()):
                if label in hist_df.columns:
                    fig_hist.add_trace(go.Scatter(
                        x=hist_df["Trades"], y=hist_df[label] * 100,
                        mode="lines+markers", name=label,
                        line=dict(color=_HIST_COLOURS[i % len(_HIST_COLOURS)]),
                    ))
            fig_hist.add_hline(y=base * 100, line_dash="dot", line_color="grey",
                               line_width=1, annotation_text="Equal base")
            fig_hist.update_layout(
                template="plotly_dark", height=300,
                margin=dict(l=10, r=10, t=10, b=40),
                xaxis=dict(title="Closed Trades"),
                yaxis=dict(title="Weight (%)"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_hist, use_container_width=True)
            st.divider()

        # ── Plain-English summary ────────────────────────────────────────────
        st.markdown("#### Summary")
        summary_lines = []
        sorted_mods = sorted(
            [(k, lw.get(k, base), wr.get(k)) for k in _MODULE_LABELS],
            key=lambda x: x[1], reverse=True
        )
        top_k, top_w, top_wr = sorted_mods[0]
        bot_k, bot_w, bot_wr = sorted_mods[-1]

        top_label = _MODULE_LABELS[top_k]
        bot_label = _MODULE_LABELS[bot_k]

        if top_wr is not None and top_wr != 0.5:
            summary_lines.append(
                f"**{top_label}** has the highest accuracy "
                f"({top_wr:.0%} win rate) and receives the most weight ({top_w:.1%})."
            )
        if bot_wr is not None and bot_wr != 0.5:
            summary_lines.append(
                f"**{bot_label}** has the lowest accuracy "
                f"({bot_wr:.0%} win rate) and has been downweighted to {bot_w:.1%}."
            )
        summary_lines.append(
            f"Weights blend 40% accuracy-derived with 60% regime-based at signal generation. "
            f"Next recalculation after {trades_until_next} more closed trade(s)."
        )
        for line in summary_lines:
            st.markdown(f"- {line}")


# ---------------------------------------------------------------------------
# Status page (also shown on Charts landing when no asset is selected)
# ---------------------------------------------------------------------------

def _render_home_page(config: dict, watchlist: dict[str, str]) -> None:
    """System status panel — rendered as the Status page and as the Charts landing."""
    # Clear all cached data so the status panel always reflects the latest DB
    # state — without this, cached metadata values would be served even after
    # a refresh or analysis run completed since the last render.
    st.cache_data.clear()

    st.markdown("## 🖥️ System Status")

    # ── Collect all status data ───────────────────────────────────────────
    deploy_ts, deploy_msg      = _git_log_info()
    uptime                     = _service_uptime()
    last_full_refresh          = _cached_last_full_refresh()
    last_analysis              = _cached_last_analysis()
    last_run_type              = _cached_last_run_type()
    db_size_str, db_level      = _db_size(config)

    refresh_val,  refresh_sub  = _age_display(last_full_refresh)
    analysis_val, analysis_sub = _age_display(last_analysis)
    # Full refresh: amber after 2 days, red after 4 (weekends break the daily cadence).
    refresh_level  = _age_level(last_full_refresh, warn_hours=48.0, error_hours=96.0)
    # Analysis: amber after 14h (should run 3× per weekday), red after 28h.
    analysis_level = _age_level(last_analysis,     warn_hours=14.0, error_hours=28.0)

    _RUN_TYPE_LABELS = {
        "full":          "Full Refresh",
        "analysis_only": "Analysis Only",
    }
    run_type_label = _RUN_TYPE_LABELS.get(last_run_type or "", "—")

    # Colour: green for full refresh, blue for analysis-only, amber if missing
    # or stale (last analysis more than 25 hours ago).
    _stale = last_analysis is None or (
        (datetime.utcnow() - last_analysis).total_seconds() > 25 * 3600
    )
    if not last_run_type or _stale:
        run_type_level = "warn"
    elif last_run_type == "full":
        run_type_level = "ok"
    else:
        run_type_level = "info"

    # ── Row 1: deployment / uptime / database ─────────────────────────────
    c1, c2, c3 = st.columns(3)
    _status_card(c1, "Last Deployment", deploy_ts, deploy_msg, "info")
    _status_card(c2, "Service Uptime",  uptime,    "Process running", "ok")
    _status_card(c3, "Database Size",   db_size_str, "", db_level)

    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Row 2: data freshness / analysis freshness / last run type ────────
    c1, c2, c3 = st.columns(3)
    _status_card(c1, "Last Full Data Refresh",  refresh_val,  refresh_sub,  refresh_level)
    _status_card(c2, "Last Analysis Update",    analysis_val, analysis_sub, analysis_level)
    _status_card(c3, "Last Run Type",           run_type_label,
                 "full = data pull + analysis" if last_run_type == "full"
                 else ("analysis only — no data pull" if last_run_type == "analysis_only" else ""),
                 run_type_level)

    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Row 3: upcoming scheduled runs ────────────────────────────────────
    sched_runs = config.get("scheduler", {}).get("runs", {})
    _RUN_DISPLAY = {
        "pre_market": ("Pre-Market",  "07:00 UTC", "Analysis only — Asian session closed, European opening"),
        "us_open":    ("US Open",     "13:30 UTC", "Analysis only — catches overnight moves"),
        "eod":        ("End of Day",  "21:30 UTC", "Full refresh + analysis after NYSE close"),
    }
    run_cols = st.columns(len(_RUN_DISPLAY))
    for col, (run_name, (label, time_str, desc)) in zip(run_cols, _RUN_DISPLAY.items()):
        run_cfg = sched_runs.get(run_name, {})
        h, m    = run_cfg.get("hour", 0), run_cfg.get("minute", 0)
        nxt     = _next_run_for_time(h, m)
        delta   = (nxt - datetime.utcnow()).total_seconds()
        if delta < 3600:
            until = f"in {int(delta / 60)}m"
        elif delta < 86400:
            until = f"in {delta / 3600:.1f}h"
        else:
            until = f"in {delta / 86400:.1f}d"
        _status_card(
            col,
            f"{label} · {time_str}",
            nxt.strftime("%a %H:%M UTC"),
            f"{until} — {desc}",
            "info",
        )

    st.divider()
    mc1, mc2 = st.columns(2)
    mc1.metric("Watchlist Assets", len(watchlist))
    mc2.metric("Asset Groups", len(config["watchlist"]))

    # ── Data coverage ─────────────────────────────────────────────────────────
    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
    st.markdown("### Data Coverage")
    fresh, stale, missing = _price_coverage(watchlist)
    total = len(watchlist)

    cc1, cc2, cc3 = st.columns(3)
    fresh_level  = "ok"    if len(fresh)   == total else ("warn" if len(fresh) >= total * 0.9 else "error")
    stale_level  = "ok"    if len(stale)   == 0     else "warn"
    missing_level = "ok"   if len(missing) == 0     else "error"

    _status_card(cc1, "Fresh (≤4 days)",   f"{len(fresh)} / {total}",
                 "up to date" if len(fresh) == total else f"{total - len(fresh)} not fresh", fresh_level)
    _status_card(cc2, "Stale (>4 days)",   f"{len(stale)} ticker{'s' if len(stale) != 1 else ''}",
                 ", ".join(stale[:5]) + ("…" if len(stale) > 5 else "") if stale else "none", stale_level)
    _status_card(cc3, "Missing entirely",  f"{len(missing)} ticker{'s' if len(missing) != 1 else ''}",
                 ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "") if missing else "none", missing_level)

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
    if st.button("📧 Send Test Email", key="status_send_test_email"):
        _regime_str = detect_regime().get("regime", "UNKNOWN")
        _open_count = len(get_open_trades())
        with st.spinner("Sending…"):
            _ok, _msg = send_test_email(config, _regime_str, last_analysis, _open_count)
        if _ok:
            st.success(_msg)
        else:
            st.error(_msg)
    st.caption("Sends a real email to confirm SMTP delivery is working. Check your inbox after clicking.")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _initialise() -> None:
    """Ensure the DB schema is current and start the background scheduler once.

    init_db() is called on every render because it is idempotent — create_all()
    is a no-op when tables exist and _apply_migrations() skips columns that are
    already present. This guarantees schema changes are picked up immediately
    after a code update without requiring a Streamlit session restart.

    The scheduler and startup analysis are only run once per session (guarded
    by session_state) so subsequent re-renders skip them.
    """
    init_db()
    if "app_initialised" not in st.session_state:
        try:
            start_scheduler()
        except Exception as exc:
            logger.warning("Scheduler could not start: %s", exc)

        # On first page load, run analysis only if data exists and the last run
        # was more than 60 minutes ago — avoids blocking page loads when the
        # scheduler has already kept things current.
        if _has_price_data():
            try:
                last_run = get_metadata("last_analysis_run")
                stale = last_run is None or (datetime.utcnow() - last_run).total_seconds() > 3600
                if stale:
                    _cfg = _load_config()
                    _tickers = get_all_tickers(_cfg)
                    run_analysis(tickers=_tickers, config=_cfg)
                    generate_signals(tickers=_tickers, config=_cfg)
                    ts = datetime.utcnow().strftime("%H:%M UTC")
                    st.session_state["_startup_msg"] = ("ok", f"Analysis updated on startup — {ts}")
                    logger.info("Startup analysis complete.")
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
    """Render the Relationships page: correlation heatmap, pair table, and divergence narrative."""
    st.markdown("## 🔗 Cross-Asset Relationships")
    st.caption(
        "Tracks 10 predefined cross-asset pairs and flags when current correlations "
        "diverge significantly from their historical norms."
    )

    # ── Refresh button ─────────────────────────────────────────────────────────
    col_btn, col_ts = st.columns([1, 4])
    if col_btn.button("↻ Recompute Now", type="primary"):
        with st.spinner("Computing correlations…"):
            try:
                compute_all_pairs(config)
                st.session_state["_corr_refresh_ts"] = datetime.utcnow()
                st.success("Correlations updated.")
            except Exception as exc:
                st.error(f"Failed: {exc}")
        st.rerun()

    refresh_ts = st.session_state.get("_corr_refresh_ts")
    if refresh_ts:
        col_ts.caption(f"Last recomputed: {refresh_ts.strftime('%H:%M UTC')}")

    st.divider()

    # ── Correlation heatmap ───────────────────────────────────────────────────
    st.markdown("### Correlation Matrix (30-day window)")
    st.caption("Current rolling 30-day pairwise correlation for all tracked assets.")

    with st.spinner("Building correlation matrix…"):
        corr_df, tickers_in_matrix = build_correlation_matrix(window=30)

    if corr_df.empty:
        st.info("Insufficient price data to build the correlation matrix. Run a data refresh first.")
    else:
        # Round for display and build the heatmap
        z_vals    = corr_df.values.tolist()
        fig_heat  = go.Figure(
            data=go.Heatmap(
                z=z_vals,
                x=tickers_in_matrix,
                y=tickers_in_matrix,
                colorscale=[
                    [0.0,  "#ef5350"],   # -1 → red
                    [0.5,  "#1f2937"],   # 0  → near-black (neutral)
                    [1.0,  "#16a34a"],   # +1 → green
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
        st.plotly_chart(fig_heat, use_container_width=True)

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

    stored = get_stored_correlations()

    if not any(p.get("corr_30d") is not None for p in stored):
        st.info("No correlation data stored yet — click **↻ Recompute Now** or run analysis.")
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
            use_container_width=True,
            hide_index=True,
            height=38 + 35 * len(records),
        )

    st.divider()

    # ── Plain-English divergence narratives ───────────────────────────────────
    st.markdown("### Notable Relationships")

    if not stored or not any(p.get("corr_30d") is not None for p in stored):
        st.info("No data yet. Run a data refresh and then recompute correlations.")
        return

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
}


def _render_charts_landing(config: dict) -> None:
    """Grouped asset cards shown on Charts before any ticker is selected."""
    st.markdown("## 📈 Charts")
    st.caption("Select an asset to view its price chart and indicators.")

    for group_key, group_label in _GROUP_LABELS.items():
        group = config["watchlist"].get(group_key, [])
        if not group:
            continue
        st.markdown(f"#### {group_label}")
        cols = st.columns(4)
        for i, entry in enumerate(group):
            ticker = entry["ticker"]
            name   = entry["name"]
            with cols[i % 4]:
                if st.button(
                    f"**{ticker}**  \n{name}",
                    key=f"cl_{ticker}",
                    use_container_width=True,
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

    ticker    = html.escape(str(e.get("ticker",    "")))
    direction = html.escape(str(e.get("direction", "LONG")))
    days_held = int(e.get("days_held") or 0)
    acct_name = html.escape(str(e.get("account_name") or e.get("account_type") or ""))

    unreal_pnl       = e.get("unreal_pnl")
    unreal_pct       = e.get("unreal_pct")
    price_unavailable = e.get("price_unavailable", e.get("live_price") is None)
    if price_unavailable:
        pnl_str = "⚠ Price unavailable"
        pnl_col = "#f59e0b"
    elif unreal_pnl is not None and unreal_pct is not None:
        _card_sym = _quote_ccy_sym(e.get("ticker") or "")
        pnl_str = f"{_card_sym}{unreal_pnl:+.2f} ({unreal_pct:+.2f}%)"
        pnl_col = "#16a34a" if unreal_pnl >= 0 else "#dc2626"
    else:
        pnl_str = "—"
        pnl_col = "#6b7280"

    entry_sig = html.escape((e.get("entry_signal")   or "—").replace("_", " "))
    curr_sig  = html.escape((e.get("current_signal") or "—").replace("_", " "))
    e_score   = float(e.get("entry_score")   or 0)
    c_score   = float(e.get("current_score") or 0)
    delta     = c_score - e_score
    delta_col = "#16a34a" if delta >= 0 else "#f59e0b"

    stop_loss = e.get("stop_loss")
    atr       = e.get("atr")
    stop_html = ""
    if stop_loss is not None:
        sl_str = f"Stop: {stop_loss:,.4f}"
        if atr is not None:
            sl_str += f" (2×ATR {atr:.4f})"
        stop_html = f"<span style='color:#64748b'>{html.escape(sl_str)}</span>"

    st.markdown(
        f"<div style='border-left:4px solid {colour};padding:12px 16px;"
        f"background:{bg};border-radius:0 6px 6px 0;margin-bottom:10px'>"
        # Header row
        f"<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px'>"
        f"<span style='background:{colour};color:#fff;padding:2px 9px;"
        f"border-radius:4px;font-size:0.72rem;font-weight:800;letter-spacing:0.05em'>"
        f"{icon} {status}</span>"
        f"<strong style='font-size:1rem'>{ticker}</strong>"
        f"<span style='color:#64748b;font-size:0.85rem'>{direction} · {days_held}d held</span>"
        f"<span style='color:{pnl_col};font-weight:700;font-size:0.9rem'>{pnl_str}</span>"
        f"<span style='color:#64748b;font-size:0.78rem'>{acct_name}</span>"
        f"</div>"
        # Detail row
        f"<div style='display:flex;gap:24px;flex-wrap:wrap;font-size:0.82rem'>"
        f"<span>Signal: {entry_sig} → <strong>{curr_sig}</strong></span>"
        f"<span>Score: {e_score:+.3f} → {c_score:+.3f} "
        f"<span style='color:{delta_col}'>(Δ {delta:+.3f})</span></span>"
        f"{stop_html}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_briefing_page(config: dict, watchlist: dict[str, str], tradeable_set: set[str], t212_names: dict[str, str]) -> None:
    """Render the Daily Briefing: market overview, open trade monitor, opportunities."""
    st.markdown("## 📅 Daily Briefing")

    last_analysis = get_metadata("last_analysis_run")
    if last_analysis:
        st.caption(
            f"As of: {last_analysis.strftime('%Y-%m-%d %H:%M')} UTC · "
            f"Updates run automatically at 07:00, 13:30 and 21:30 UTC"
        )
    else:
        st.caption("No analysis run yet — updates run automatically on weekdays.")

    # ── Section 1: Market Overview ──────────────────────────────────────────
    st.markdown("### 🌍 Market Overview")

    col_regime, col_macro = st.columns([3, 2])
    with col_regime:
        regime_result = detect_regime()
        regime  = regime_result["regime"]
        colour  = regime_result["colour"]
        details = regime_result["details"]
        price   = details.get("price")
        sma_200 = details.get("sma_200")
        adx     = details.get("adx")
        price_pct = f"^GSPC {(price/sma_200-1)*100:+.1f}% vs SMA200" if (price and sma_200) else ""
        adx_str   = f"ADX {adx:.1f}" if adx else ""
        st.markdown(
            f'<div style="display:inline-flex;align-items:center;gap:12px;flex-wrap:wrap;'
            f'background:{colour}18;border:1px solid {colour}44;'
            f'padding:6px 14px;border-radius:20px;margin-bottom:4px">'
            f'<span style="color:{colour};font-weight:700">⚡ {regime.replace("_", " ")}</span>'
            f'<span style="color:#6b7280;font-size:0.8rem">{html.escape(price_pct)}</span>'
            f'<span style="color:#6b7280;font-size:0.8rem">{adx_str}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(regime_result["description"])

    with col_macro:
        ctx    = get_macro_context()
        vix_v  = ctx.get("vix")
        vix_col = (
            "#dc2626" if vix_v and vix_v > 30 else
            "#f59e0b" if vix_v and vix_v > 20 else
            "#16a34a"
        )
        vix_str = f"VIX {vix_v:.1f}" if vix_v else "VIX —"
        ys  = ctx.get("yield_spread")
        yr  = (ctx.get("yield_regime") or "—").lower()
        ys_str = f"Yield {ys:+.2f}% ({yr})" if ys is not None else f"Yield: {yr}"
        dp  = ctx.get("dollar_pct")
        dt  = ctx.get("dollar_trend") or "—"
        usd_str = f"USD {dt} ({dp:+.1f}% vs SMA20)" if dp is not None else f"USD {dt}"
        st.markdown(
            f"<div style='font-size:0.85rem;margin-top:10px'>"
            f"<span style='color:{vix_col};font-weight:600'>{html.escape(vix_str)}</span>"
            f" &nbsp;·&nbsp; {html.escape(ys_str)} &nbsp;·&nbsp; {html.escape(usd_str)}"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Active overlays (compact)
    overlay_raw     = get_metadata_str("active_overlays") or ""
    active_overlays = {k for k in overlay_raw.split(",") if k and k != "none"}
    for overlay_key in ("MACRO_STRESS", "VOL_REGIME_SHIFT", "CREDIT_STRESS"):
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

    # ── Top Long / Short Opportunities ─────────────────────────────────────
    all_signals = get_latest_signals(list(watchlist.keys()))

    # Tickers in equity/bond groups trigger a confidence warning under MACRO_STRESS
    _EQ_BOND_GROUPS = {"indices", "sector_etfs", "bonds", "international"}
    _eq_bond_tickers: set[str] = {
        entry["ticker"]
        for gname, group in config.get("watchlist", {}).items()
        if gname in _EQ_BOND_GROUPS and isinstance(group, list)
        for entry in group
        if isinstance(entry, dict) and "ticker" in entry
    }
    macro_stress_active = "MACRO_STRESS" in active_overlays

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
        st.markdown(f"**🟢 Long Opportunities** ({n_long})" if n_long else "**🟢 Long Opportunities**")
        if long_picks:
            for sig in long_picks:
                lp = _opp_prices.get(sig.get("ticker", ""))
                st.markdown(_opp_card(sig, 1, "#26a69a", "#16a34a", live_price=lp), unsafe_allow_html=True)
        else:
            st.caption("No qualifying long signals today.")

    with rc:
        n_short = len(short_picks)
        st.markdown(f"**🔴 Short Opportunities** ({n_short})" if n_short else "**🔴 Short Opportunities**")
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
    st.markdown("### 📊 Macro Dashboard")

    macro_dash = get_macro_dashboard(config)

    if not macro_dash.get("has_data"):
        st.info(
            "No FRED macro data yet — the first fetch runs automatically at the next "
            "EOD scheduler run (21:30 UTC on weekdays)."
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
        ) -> str:
            bg = _hex_rgba(colour, bg_alpha)
            return (
                f"<div style='background:{bg};border:1px solid {colour}33;"
                f"border-radius:8px;padding:10px 14px;min-height:72px'>"
                f"<div style='font-size:0.72rem;font-weight:600;color:#6b7280;"
                f"text-transform:uppercase;letter-spacing:0.05em'>{html.escape(title)}</div>"
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
                _metric_card("10Y Real Yield", f"{rt_ar} {v_str}", f"Trend: {rt}", ry_c),
                unsafe_allow_html=True,
            )
        with c2:
            bk  = macro_dash["breakeven"]
            bt  = macro_dash["breakeven_trend"]
            bt_ar = _TREND_ARROW.get(bt, "→")
            bk_c  = "#16a34a" if 1.5 <= (bk or 0) <= 2.5 else "#f59e0b"
            v_str = f"{bk:.2f}%" if bk is not None else "—"
            st.markdown(
                _metric_card("10Y Breakeven Inflation", f"{bt_ar} {v_str}", f"Trend: {bt}", bk_c),
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
                _metric_card("HY Credit Spread (OAS)", v_str, sub, cs_c),
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
                _metric_card("Yield Curve 10Y-2Y", v_str, f"Shape: {sh2y}", yc2_c),
                unsafe_allow_html=True,
            )
        with c5:
            yc3m  = macro_dash["t10y3m"]
            sh3m  = macro_dash["t10y3m_shape"] or "—"
            yc3_c = _YC_COLOUR.get(sh3m, "#6b7280")
            v_str = f"{yc3m:+.2f}%" if yc3m is not None else "—"
            st.markdown(
                _metric_card("Yield Curve 10Y-3M", v_str, f"Shape: {sh3m}", yc3_c),
                unsafe_allow_html=True,
            )
        with c6:
            ig_bps = macro_dash["ig_oas_bps"]
            ig_c   = "#16a34a" if ig_bps is not None and ig_bps < 100 else (
                     "#ef5350" if ig_bps is not None and ig_bps > 150 else "#f59e0b"
            )
            v_str  = f"{ig_bps:.0f} bps" if ig_bps is not None else "—"
            st.markdown(
                _metric_card("IG Credit Spread (OAS)", v_str, "Investment Grade bonds", ig_c),
                unsafe_allow_html=True,
            )

        # Row 3: Leading indicators (USSLIND, PERMIT)
        usslind_val  = macro_dash.get("usslind")
        usslind_pct  = macro_dash.get("usslind_pct1m")
        usslind_sc   = macro_dash.get("usslind_score")
        permit_val   = macro_dash.get("permit")
        permit_pct   = macro_dash.get("permit_pct1m")
        permit_sc    = macro_dash.get("permit_score")

        if usslind_val is not None or permit_val is not None:
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            c7, c8, _ = st.columns(3)
            with c7:
                usslind_c = (
                    "#16a34a" if usslind_sc == 1 else
                    "#ef5350" if usslind_sc == -1 else "#f59e0b"
                )
                if usslind_val is not None:
                    ar  = _TREND_ARROW.get("rising" if (usslind_pct or 0) > 0 else "falling" if (usslind_pct or 0) < 0 else "flat", "→")
                    sub = f"MoM: {usslind_pct:+.2f}%" if usslind_pct is not None else "MoM: —"
                    st.markdown(
                        _metric_card(
                            "Leading Index (Philly Fed)",
                            f"{ar} {usslind_val:+.2f}%",
                            sub + " · predicted 6m growth",
                            usslind_c,
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(_metric_card("Leading Index (Philly Fed)", "—", "No data", "#6b7280"), unsafe_allow_html=True)
            with c8:
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
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(_metric_card("Housing Permits (SAAR)", "—", "No data", "#6b7280"), unsafe_allow_html=True)

    st.divider()

    # ── Section 3: Open Trade Monitor ──────────────────────────────────────
    st.markdown("### 👀 Open Trade Monitor")

    open_trades = get_open_trades()

    if not open_trades:
        st.info("No open trades. Log a trade on the **Trade Log** page.")
    else:
        tickers_needed = list({t["ticker"] for t in open_trades})
        live_prices    = {t: _cached_latest_price(t) for t in tickers_needed}

        enriched = [enrich_open_trade(t, live_prices.get(t["ticker"])) for t in open_trades]
        enriched.sort(key=lambda x: x.get("status_priority", 0), reverse=True)

        alerts = [e for e in enriched if e["status"] in ("EXIT SIGNAL", "STOP LOSS")]
        if alerts and should_send_alert(config):
            ok, msg = send_alert_email(alerts, config)
            if ok:
                st.toast(f"Alert email sent: {msg}", icon="📧")

        for e in enriched:
            _render_trade_card(e)

        if alerts:
            notif_enabled = config.get("notifications", {}).get("email", {}).get("enabled", False)
            if notif_enabled:
                if st.button("📧 Send Alert Email Now", key="brief_send_email", type="primary"):
                    ok, msg = send_alert_email(alerts, config)
                    st.success(msg) if ok else st.error(msg)
            else:
                n = len(alerts)
                st.caption(
                    f"💡 {n} position{'s require' if n > 1 else ' requires'} attention. "
                    f"Enable email alerts in config.yaml to receive notifications."
                )

    st.divider()

    # ── Section 4: Today's Opportunities ───────────────────────────────────
    st.markdown("### 🎯 Today's Opportunities")

    open_tickers = {t["ticker"] for t in open_trades} if open_trades else set()
    tradeable_signals = [s for s in all_signals if s.get("ticker") in tradeable_set]
    opps = build_opportunities(tradeable_signals, open_tickers, active_overlays, watchlist)

    if not opps:
        st.info("No strong signals outside current positions right now.")
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
        st.dataframe(styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(opp_rows))
        st.caption(
            "Sug. SL = entry ± 2×ATR based on yfinance reference price. "
            "TP1/TP2 = 1:1 and 2:1 R:R targets. "
            "Risk/lev = SL distance as % of price per unit of leverage — "
            "multiply by investment × leverage for GBP risk estimate."
        )


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
        st.title("📈 Investment Research")
        st.divider()

        # Page navigation
        _PAGES = ["📅 Daily Briefing", "📊 Signals", "📈 Charts", "🔗 Relationships", "📋 Trade Log", "🏆 Performance", "🖥️ Status"]
        _PAGE_KEYS = {
            "📅 Daily Briefing": "briefing",
            "📊 Signals":       "signals",
            "📈 Charts":        "charts",
            "🔗 Relationships": "relationships",
            "📋 Trade Log":     "trade_log",
            "🏆 Performance":   "performance",
            "🖥️ Status":        "status",
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

        # Last data pull timestamp — prefer last_full_refresh (set by scheduler
        # and manual refresh); fall back to last_refresh_run for older DBs.
        last_pull_ts = get_metadata("last_full_refresh") or get_metadata("last_refresh_run")
        if last_pull_ts:
            st.caption(f"Last data pull: {last_pull_ts.strftime('%Y-%m-%d %H:%M')} UTC")
        else:
            st.caption("No data yet — click Refresh Data to load.")

        # Refresh button — manual full refresh (data pull + analysis pipeline)
        if st.button("🔄 Refresh Data", use_container_width=True, type="primary"):
            _cfg     = _load_config()
            _tickers = get_all_tickers(_cfg)
            with st.spinner("Pulling latest prices…"):
                results  = pull_incremental(tickers=_tickers)
                new_rows = sum(results.values())
                try:
                    set_metadata("last_full_refresh", datetime.utcnow().isoformat())
                except Exception:
                    pass
            with st.spinner("Running analysis pipeline…"):
                try:
                    run_analysis(tickers=_tickers, config=_cfg)
                    generate_signals(tickers=_tickers, config=_cfg)
                except Exception as _exc:
                    st.warning(f"Analysis completed with errors: {_exc}")
            st.success(f"Fetched {new_rows} new rows · Analysis and signals updated.")
            st.rerun()


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

    if page_key == "trade_log":
        _render_trade_log_page(config, watchlist, tradeable_set, t212_names)
        return

    if page_key == "performance":
        _render_performance_page(config, watchlist)
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
            "Click **Refresh Data** in the sidebar to load history."
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
    st.plotly_chart(_candlestick_chart(df, selected, name), use_container_width=True)

    # Volume chart
    st.plotly_chart(_volume_bar_chart(df), use_container_width=True)

    # Indicator panel
    _render_indicator_panel(selected)

    # Raw data expander
    with st.expander("Raw OHLCV data"):
        display_df = df.copy()
        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(
            display_df.sort_values("date", ascending=False).reset_index(drop=True),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
