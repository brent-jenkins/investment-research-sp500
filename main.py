"""Investment Research Dashboard — Streamlit entry point."""

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

from analysis.confluence import generate_signals, get_last_signal_timestamp, get_latest_signals
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
    get_macro_context,
    send_alert_email,
    should_send_alert,
)
from output.signals import (
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
    """Load config.yaml (cached for the session, invalidated on refresh)."""
    config_path = _ROOT / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _build_watchlist(config: dict) -> dict[str, str]:
    """Return an ordered dict of {ticker: display_name} for all watchlist assets."""
    items: dict[str, str] = {}
    for group in config["watchlist"].values():
        for entry in group:
            items[entry["ticker"]] = entry["name"]
    return items


@st.cache_data(ttl=300)
def _cached_latest_price(ticker: str) -> float | None:
    """Live price from yfinance, cached for 5 minutes to limit API calls."""
    return get_latest_price(ticker)


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
    return get_metadata("last_run_type")  # type: ignore[return-value]


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
            st.info("No indicator data yet — analysis runs automatically after each data refresh.")
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


def _render_signals_page(config: dict, watchlist: dict[str, str]) -> None:
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
    for r in rows:
        lbl = (r.get("signal") or "NEUTRAL").replace("_", " ")
        if lbl in counts:
            counts[lbl] += 1

    dist_cols = st.columns(5)
    for col, (lbl, fg) in zip(dist_cols, _DIST_CFG):
        col.markdown(
            f'<div style="text-align:center;padding:10px 4px;border-radius:6px;'
            f'background:{fg}22;border:1px solid {fg}55">'
            f'<div style="color:{fg};font-weight:700;font-size:1.5rem">{counts[lbl]}</div>'
            f'<div style="color:#64748b;font-size:0.72rem;margin-top:2px">{lbl}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ---- Market intelligence -----------------------------------------------
    bullish_n = counts["STRONG BUY"] + counts["BUY"]
    bearish_n = counts["SELL"] + counts["STRONG SELL"]
    total_n   = sum(counts.values())
    _intel: list[str] = []

    if total_n > 0:
        bull_pct = bullish_n / total_n
        bear_pct = bearish_n / total_n
        if bull_pct >= 0.6:
            _intel.append(
                f"{bullish_n} of {total_n} assets show buy signals — broad bullish breadth "
                f"supports adding long exposure on short-term pullbacks."
            )
        elif bear_pct >= 0.6:
            _intel.append(
                f"{bearish_n} of {total_n} assets show sell signals — broad bearish breadth "
                f"suggests reducing longs and favouring cash or short positions."
            )
        elif bull_pct > bear_pct:
            _intel.append(
                f"Signals lean bullish ({bullish_n} buys vs {bearish_n} sells across {total_n} assets) "
                f"— selective long exposure is favoured, though broad momentum is not overwhelming."
            )
        elif bear_pct > bull_pct:
            _intel.append(
                f"Signals lean bearish ({bearish_n} sells vs {bullish_n} buys from {total_n} assets) "
                f"— consider lightening longs and waiting for clearer confirmation before new entries."
            )
        else:
            _intel.append(
                f"Signals are evenly split ({bullish_n} buys, {bearish_n} sells across {total_n} assets) "
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

    long_picks  = [r for r in rows           if _qualifies(r,  1)][:3]
    short_picks = [r for r in reversed(rows) if _qualifies(r, -1)][:3]

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
    st.caption("▲ bullish  ●  neutral  ▼ bearish")

    def _sym(val) -> str:
        return _SCORE_SYMBOL.get(int(val) if val is not None else 0, "●")

    records = []
    for r in rows:
        ticker = r.get("ticker", "")
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


# ---------------------------------------------------------------------------
# Trade Log page
# ---------------------------------------------------------------------------

def _render_trade_log_page(config: dict, watchlist: dict[str, str]) -> None:
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

        ticker_keys = list(watchlist.keys())
        sel_ticker  = st.selectbox(
            "Asset",
            ticker_keys,
            format_func=lambda t: f"{t} — {watchlist[t]}",
            key="log_ticker",
        )

        sig = get_current_signal(sel_ticker)
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Signal",           (sig.get("signal") or "—").replace("_", " "))
        sc2.metric("Confluence Score", f"{sig.get('confluence_score', 0):+.3f}" if sig else "—")
        sc3.metric("Regime",           (sig.get("regime") or "—").replace("_", " "))

        with st.form("log_trade_form", clear_on_submit=True):
            fc1, fc2, fc3 = st.columns(3)
            direction   = fc1.radio("Direction", ["LONG", "SHORT"], horizontal=True)
            entry_price = fc2.number_input("Entry Price", min_value=0.0, step=0.01, format="%.4f")
            quantity    = fc3.number_input("Quantity (units)", min_value=0.001, value=1.0, step=1.0, format="%.3f")
            notes_text  = st.text_input("Notes (optional)")
            submitted   = st.form_submit_button("Log Trade", type="primary")

            if submitted:
                if entry_price <= 0:
                    st.error("Entry price must be greater than 0.")
                else:
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
                        quantity=quantity,
                        account_id=sel_acct["id"],
                        account_name=sel_acct["name"],
                        account_type=sel_acct.get("type", "PRACTICE"),
                        notes=notes_text,
                    )
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

        rows = []
        for t in open_trades:
            ticker    = t["ticker"]
            live      = live_prices.get(ticker)
            entry     = float(t.get("entry_price") or 0)
            direction = t.get("direction", "LONG")
            entry_dt  = pd.Timestamp(t["entry_date"]) if t.get("entry_date") else None
            days_held = int((pd.Timestamp.utcnow() - entry_dt).days) if entry_dt else None

            if live and entry:
                mult       = 1 if direction == "LONG" else -1
                unreal_pct = (live - entry) / entry * mult * 100
            else:
                unreal_pct = None

            rows.append({
                "ID":           t["id"],
                "Type":         t.get("account_type") or "PRACTICE",
                "Ticker":       ticker,
                "Signal":       (t.get("signal_type") or "—").replace("_", " "),
                "Dir":          direction,
                "Entry Date":   entry_dt.strftime("%Y-%m-%d") if entry_dt else "—",
                "Entry Price":  entry,
                "Live Price":   live,
                "Unreal P&L %": unreal_pct,
                "Days Held":    days_held,
            })

        open_df = pd.DataFrame(rows)

        def _style_unreal(val):
            if not isinstance(val, float):
                return ""
            if val > 0:  return "color:#16a34a;font-weight:bold"
            if val < 0:  return "color:#dc2626;font-weight:bold"
            return ""

        def _style_acct_badge(val):
            if val == "LIVE":     return "color:#6ee7b7;font-weight:bold"
            if val == "PRACTICE": return "color:#a5b4fc"
            return ""

        styler = (
            open_df.style
            .map(_style_unreal,    subset=["Unreal P&L %"])
            .map(_style_acct_badge, subset=["Type"])
            .format({
                "Entry Price":  "{:,.4f}",
                "Live Price":   lambda v: f"{v:,.4f}" if isinstance(v, float) else "—",
                "Unreal P&L %": lambda v: f"{v:+.2f}%" if isinstance(v, float) else "—",
            })
        )
        st.dataframe(styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(rows))

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
            st.info("No closed trades yet. Log and close trades on the **Trade Log** page.")
            return

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
                f"No closed {acct_filter.lower()} trades yet. "
                "Log and close trades on the **Trade Log** page."
            )
            return

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
                 "info" if last_run_type else "warn")

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

        # On first page load, if price data exists, automatically run analysis
        # and generate signals so the dashboard is immediately ready after a
        # deployment or service restart without any manual steps.
        if _has_price_data():
            try:
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
    "indices":       "Indices",
    "sector_etfs":   "Sector ETFs",
    "energy":        "Energy",
    "commodities":   "Commodities",
    "bonds":         "Bonds",
    "volatility":    "Volatility",
    "international": "International",
    "forex":         "Forex",
    "crypto":        "Crypto",
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

    unreal_pnl = e.get("unreal_pnl")
    unreal_pct = e.get("unreal_pct")
    pnl_str    = (
        f"{unreal_pnl:+.2f} ({unreal_pct:+.2f}%)"
        if unreal_pnl is not None and unreal_pct is not None
        else "—"
    )
    pnl_col = "#16a34a" if (unreal_pnl or 0) >= 0 else "#dc2626"

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


def _render_briefing_page(config: dict, watchlist: dict[str, str]) -> None:
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

    # Top 3 signals
    all_signals = get_latest_signals(list(watchlist.keys()))
    if all_signals:
        st.markdown(
            "<div style='margin-top:12px;margin-bottom:6px'><strong>Top 3 Signals Today</strong></div>",
            unsafe_allow_html=True,
        )
        _SIG_C = {
            "STRONG_BUY":  "#16a34a",
            "BUY":         "#26a69a",
            "NEUTRAL":     "#6b7280",
            "SELL":        "#f59e0b",
            "STRONG_SELL": "#ef5350",
        }
        top3 = all_signals[:3]
        t3_cols = st.columns(3)
        for i, sig in enumerate(top3):
            s_key  = sig.get("signal", "NEUTRAL")
            score  = sig.get("confluence_score", 0) or 0
            sc     = _SIG_C.get(s_key, "#6b7280")
            t3_cols[i].markdown(
                f"<div style='background:{sc}22;border:1px solid {sc}44;"
                f"padding:8px 12px;border-radius:6px;text-align:center'>"
                f"<div style='font-weight:700;color:{sc}'>{html.escape(sig['ticker'])}</div>"
                f"<div style='color:{sc};font-size:0.78rem'>{s_key.replace('_',' ')}</div>"
                f"<div style='color:#64748b;font-size:0.75rem'>{score:+.3f}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

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
    opps = build_opportunities(all_signals, open_tickers, active_overlays, watchlist)

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

        opp_rows = [
            {
                "Ticker":  o["ticker"],
                "Name":    o["name"],
                "Signal":  o["signal"].replace("_", " "),
                "Score":   o["score"],
                "Modules": _mod_str(o),
                "Note":    o["overlay_note"] or "",
            }
            for o in opps
        ]
        opp_df = pd.DataFrame(opp_rows)

        def _style_opp_sig(val: str) -> str:
            if "STRONG BUY"  in val: return "color:#16a34a;font-weight:bold"
            if "STRONG SELL" in val: return "color:#ef5350;font-weight:bold"
            return ""

        styler = opp_df.style.map(_style_opp_sig, subset=["Signal"]).format({"Score": "{:+.3f}"})
        st.dataframe(styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(opp_rows))


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

        # Refresh button — manual full refresh (data pull + metadata stamp)
        if st.button("🔄 Refresh Data", use_container_width=True, type="primary"):
            with st.spinner("Pulling latest prices…"):
                results = pull_incremental()
                new_rows = sum(results.values())
                try:
                    set_metadata("last_full_refresh", datetime.utcnow().isoformat())
                except Exception:
                    pass
            st.success(f"Fetched {new_rows} new rows across {len(results)} tickers.")
            st.rerun()


    # ---- Main content --------------------------------------------------
    page_key = st.session_state.get("page", "briefing")

    if page_key == "briefing":
        _render_briefing_page(config, watchlist)
        return

    if page_key == "signals":
        _render_signals_page(config, watchlist)
        return

    if page_key == "status":
        _render_home_page(config, watchlist)
        return

    if page_key == "relationships":
        _render_relationships_page(config)
        return

    if page_key == "trade_log":
        _render_trade_log_page(config, watchlist)
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
