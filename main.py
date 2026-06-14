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
from analysis.engine import run_analysis
from analysis.regime import REGIME_COLOURS, REGIME_DESCRIPTIONS, Regime, detect_regime
from data.database import init_db
from data.ingestion import (
    get_all_tickers,
    get_last_update_timestamp,
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
from output.signals import (
    accuracy_by_indicator,
    accuracy_by_regime,
    accuracy_by_signal_type,
    accuracy_summary,
    equity_curve,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        f'<div style="font-size:0.78rem;color:#9ca3af;margin-top:3px">'
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
        primary = f"{int(hours * 60)}m ago"
    elif hours < 24:
        primary = f"{int(hours)}h ago"
    else:
        primary = f"{hours / 24:.1f}d ago"
    return primary, _ts.strftime("%Y-%m-%d %H:%M UTC")


def _next_scheduled_run() -> datetime:
    """Return the next 21:30 UTC weekday datetime (Mon–Fri)."""
    now = datetime.utcnow()
    candidate = now.replace(hour=21, minute=30, second=0, microsecond=0)
    if now >= candidate or now.weekday() >= 5:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
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
            st.info("No indicator data yet — click **Run Analysis** in the sidebar.")
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

    # ---- Regime card -------------------------------------------------------
    regime_result = detect_regime()
    regime        = regime_result["regime"]
    colour        = regime_result["colour"]
    description   = regime_result["description"]
    details       = regime_result["details"]
    weights       = regime_result["weights"]

    st.markdown(
        f'<div style="background:{colour}22;border-left:4px solid {colour};'
        f'padding:12px 16px;border-radius:6px;margin-bottom:16px">'
        f'<span style="color:{colour};font-size:1.2rem;font-weight:700">'
        f'⚡ {regime.replace("_", " ")}</span><br>'
        f'<span style="color:#d1d5db;font-size:0.9rem">{description}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    # Regime detail metrics
    d1, d2, d3, d4 = st.columns(4)
    price   = details.get("price")
    sma_200 = details.get("sma_200")
    adx     = details.get("adx")
    vix     = details.get("vix")

    d1.metric("^GSPC Price",   f"{price:,.0f}"   if price   else "—")
    d2.metric("vs SMA 200",
              f"{(price/sma_200 - 1)*100:+.1f}%" if (price and sma_200) else "—")
    d3.metric("ADX",           f"{adx:.1f}"      if adx     else "—")
    d4.metric("VIX",           f"{vix:.1f}"      if vix     else "—")

    # Weight profile for current regime
    with st.expander("Regime weight profile", expanded=False):
        cols = st.columns(5)
        labels = ["Trend", "Mean Rev", "Volatility", "Volume", "Macro"]
        keys   = ["trend", "mean_reversion", "volatility", "volume", "macro"]
        for col, lbl, key in zip(cols, labels, keys):
            col.metric(lbl, f"{weights[key]:.0%}")

    st.divider()

    # ---- Generate Signals button -------------------------------------------
    last_ts = get_last_signal_timestamp()
    if last_ts:
        st.caption(f"Signals last generated: {last_ts.strftime('%Y-%m-%d %H:%M')} UTC")
    else:
        st.caption("No signals generated yet.")

    if st.button("⚡ Generate Signals", type="primary", use_container_width=False):
        tickers = get_all_tickers(config)
        bar     = st.progress(0, text="Detecting regime…")
        status  = st.empty()

        def _on_progress(current: int, total: int, ticker: str) -> None:
            bar.progress(current / total, text=f"Scoring {ticker} ({current}/{total})")
            status.caption(f"Last completed: **{ticker}**")

        outcome = generate_signals(
            tickers=tickers, config=config, progress_callback=_on_progress
        )
        bar.progress(1.0, text="Done")

        n_errors = len(outcome["errors"])
        n_ok     = len(outcome["signals"])
        if n_errors:
            st.warning(f"Generated {n_ok} signals with {n_errors} error(s). Check logs.")
        else:
            st.success(f"Generated signals for {n_ok} assets.")
        st.rerun()

    st.divider()

    # ---- Ranked signals table ----------------------------------------------
    rows = get_latest_signals(list(watchlist.keys()))

    if not rows:
        st.info("No signals yet — click **Generate Signals** above.")
        return

    st.markdown("### Asset Rankings")
    st.caption("▲ bullish  ●  neutral  ▼ bearish")

    def _sym(val) -> str:
        return _SCORE_SYMBOL.get(int(val) if val is not None else 0, "●")

    records = []
    for r in rows:
        ticker = r.get("ticker", "")
        records.append({
            "Ticker":    ticker,
            "Name":      watchlist.get(ticker, ticker),
            "Signal":    (r.get("signal") or "NEUTRAL").replace("_", " "),
            "Score":     r.get("confluence_score") or 0.0,
            "Trend":     _sym(r.get("trend_score")),
            "Mean Rev":  _sym(r.get("mean_reversion_score")),
            "Volatility": _sym(r.get("volatility_score")),
            "Volume":    _sym(r.get("volume_score")),
            "Macro":     _sym(r.get("macro_score")),
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

    # ---- Log New Trade -----------------------------------------------------
    with st.expander("➕ Log New Trade", expanded=True):
        ticker_keys = list(watchlist.keys())
        sel_ticker  = st.selectbox(
            "Asset",
            ticker_keys,
            format_func=lambda t: f"{t} — {watchlist[t]}",
            key="log_ticker",
        )

        sig = get_current_signal(sel_ticker)
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Signal",          (sig.get("signal") or "—").replace("_", " "))
        sc2.metric("Confluence Score", f"{sig.get('confluence_score', 0):+.3f}" if sig else "—")
        sc3.metric("Regime",          (sig.get("regime") or "—").replace("_", " "))

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
                        notes=notes_text,
                    )
                    st.success(f"Trade #{trade_id} logged for {sel_ticker}.")
                    st.rerun()

    st.divider()

    # ---- Open Trades -------------------------------------------------------
    st.markdown("### Open Trades")
    open_trades = get_open_trades()

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
            qty       = float(t.get("quantity") or 1)
            direction = t.get("direction", "LONG")
            entry_dt  = pd.Timestamp(t["entry_date"]) if t.get("entry_date") else None
            days_held = int((pd.Timestamp.utcnow() - entry_dt).days) if entry_dt else None

            if live and entry:
                mult      = 1 if direction == "LONG" else -1
                unreal_pct = (live - entry) / entry * mult * 100
            else:
                unreal_pct = None

            rows.append({
                "ID":          t["id"],
                "Ticker":      ticker,
                "Signal":      (t.get("signal_type") or "—").replace("_", " "),
                "Dir":         direction,
                "Entry Date":  entry_dt.strftime("%Y-%m-%d") if entry_dt else "—",
                "Entry Price": entry,
                "Live Price":  live,
                "Unreal P&L %": unreal_pct,
                "Days Held":   days_held,
            })

        open_df = pd.DataFrame(rows)

        def _style_unreal(val):
            if not isinstance(val, float):
                return ""
            if val > 0:  return "color:#16a34a;font-weight:bold"
            if val < 0:  return "color:#dc2626;font-weight:bold"
            return ""

        styler = (
            open_df.style
            .map(_style_unreal, subset=["Unreal P&L %"])
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

    # ---- Date range filter -------------------------------------------------
    with st.expander("📅 Date range filter", expanded=False):
        fc1, fc2 = st.columns(2)
        from_date: date | None = fc1.date_input("From (exit date)", value=None)
        to_date:   date | None = fc2.date_input("To (exit date)",   value=None)

    from_dt = datetime(from_date.year, from_date.month, from_date.day) if from_date else None
    to_dt   = datetime(to_date.year,  to_date.month,  to_date.day, 23, 59, 59) if to_date else None

    all_trades    = get_all_trades()
    closed_trades = get_closed_trades(from_dt, to_dt)

    # ---- Summary stats -----------------------------------------------------
    summary = accuracy_summary(all_trades)

    if not summary:
        st.info("No closed trades yet. Log and close trades on the **Trade Log** page.")
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
    curve_df = equity_curve(closed_trades if closed_trades else all_trades)

    if not curve_df.empty:
        st.markdown("### Equity Curve")
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

    # ---- Win rate by signal type -------------------------------------------
    sig_df  = accuracy_by_signal_type(closed_trades)
    reg_df  = accuracy_by_regime(closed_trades)

    if not sig_df.empty or not reg_df.empty:
        ch1, ch2 = st.columns(2)

        if not sig_df.empty:
            with ch1:
                st.markdown("### Win Rate by Signal Type")
                bar_colours = [
                    _pnl_colour(r - 0.5) for r in sig_df["Win Rate"]
                ]
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
        st.caption("Hit rate = (bullish calls that were profitable + bearish calls that lost) / total directional calls")

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
        ct_rows = []
        for t in reversed(closed_trades):     # most recent first
            ct_rows.append({
                "Ticker":    t.get("ticker", ""),
                "Signal":    (t.get("signal_type") or "—").replace("_", " "),
                "Dir":       t.get("direction", "LONG"),
                "Entry":     float(t.get("entry_price") or 0),
                "Exit":      float(t.get("exit_price")  or 0),
                "P&L pts":   float(t.get("pnl")         or 0),
                "P&L %":     float(t.get("pnl_pct")     or 0),
                "Days":      t.get("holding_days"),
                "Regime":    (t.get("regime") or "—").replace("_", " "),
            })

        ct_df = pd.DataFrame(ct_rows)

        def _style_pnl(val):
            if not isinstance(val, (int, float)):
                return ""
            return f"color:{_pnl_colour(val)};font-weight:bold"

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
        st.dataframe(ct_styler, use_container_width=True, hide_index=True,
                     height=38 + 35 * len(ct_rows))


# ---------------------------------------------------------------------------
# Home page (Charts landing — no asset selected)
# ---------------------------------------------------------------------------

def _render_home_page(config: dict, watchlist: dict[str, str]) -> None:
    """System status panel shown when no asset is selected on the Charts page."""
    st.markdown("## 📈 Investment Research")
    st.caption("Select an asset from the sidebar to view its price chart and indicators.")
    st.divider()
    st.markdown("### System Status")

    # ── Collect all status data ───────────────────────────────────────────
    deploy_ts, deploy_msg      = _git_log_info()
    uptime                     = _service_uptime()
    last_refresh               = get_last_update_timestamp()
    last_analysis              = get_last_signal_timestamp()
    next_run                   = _next_scheduled_run()
    db_size_str, db_level      = _db_size(config)

    refresh_val,  refresh_sub  = _age_display(last_refresh)
    analysis_val, analysis_sub = _age_display(last_analysis)
    refresh_level              = _age_level(last_refresh)
    analysis_level             = _age_level(last_analysis)

    delta_secs = (next_run - datetime.utcnow()).total_seconds()
    if delta_secs < 3600:
        next_sub = f"in {int(delta_secs / 60)}m"
    elif delta_secs < 86400:
        next_sub = f"in {delta_secs / 3600:.1f}h"
    else:
        next_sub = f"in {delta_secs / 86400:.1f}d"

    # ── Row 1: deployment / uptime / database ─────────────────────────────
    c1, c2, c3 = st.columns(3)
    _status_card(c1, "Last Deployment", deploy_ts, deploy_msg, "info")
    _status_card(c2, "Service Uptime",  uptime,    "Process running", "ok")
    _status_card(c3, "Database Size",   db_size_str, "", db_level)

    st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)

    # ── Row 2: data freshness / next run ──────────────────────────────────
    c1, c2, c3 = st.columns(3)
    _status_card(c1, "Last Data Refresh",  refresh_val,  refresh_sub,  refresh_level)
    _status_card(c2, "Last Analysis Run",  analysis_val, analysis_sub, analysis_level)
    _status_card(c3, "Next Scheduled Run",
                 next_run.strftime("%a %Y-%m-%d %H:%M UTC"), next_sub, "info")

    st.divider()
    mc1, mc2 = st.columns(2)
    mc1.metric("Watchlist Assets", len(watchlist))
    mc2.metric("Asset Groups", len(config["watchlist"]))


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
        _PAGES = ["📈 Charts", "📊 Signals", "📋 Trade Log", "🏆 Performance"]
        _PAGE_KEYS = {
            "📈 Charts":      "charts",
            "📊 Signals":     "signals",
            "📋 Trade Log":   "trade_log",
            "🏆 Performance": "performance",
        }
        current_key = st.session_state.get("page", "charts")
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

        # Last update timestamp
        last_ts = get_last_update_timestamp()
        if last_ts:
            st.caption(f"Last data pull: {last_ts.strftime('%Y-%m-%d %H:%M')} UTC")
        else:
            st.caption("No data yet — click Refresh Data to load.")

        # Refresh button
        if st.button("🔄 Refresh Data", use_container_width=True, type="primary"):
            with st.spinner("Pulling latest prices…"):
                results = pull_incremental()
                new_rows = sum(results.values())
            st.success(f"Fetched {new_rows} new rows across {len(results)} tickers.")
            st.rerun()

        # Run Analysis button
        if st.button("🧮 Run Analysis", use_container_width=True):
            tickers = get_all_tickers(config)
            progress_bar = st.progress(0, text="Starting analysis…")
            status_text  = st.empty()

            def _on_progress(current: int, total: int, ticker: str) -> None:
                pct = current / total
                progress_bar.progress(pct, text=f"Analysed {ticker} ({current}/{total})")
                status_text.caption(f"Last completed: **{ticker}**")

            analysis_results = run_analysis(
                tickers=tickers, config=config, progress_callback=_on_progress
            )
            progress_bar.progress(1.0, text="Analysis complete")
            errors = [r for r in analysis_results if "error" in r]
            if errors:
                st.warning(f"Completed with {len(errors)} error(s). Check logs.")
            else:
                st.success(f"Analysis complete for {len(analysis_results)} assets.")
            st.rerun()

        # Watchlist selector — only shown on Charts page
        if current_page == "charts":
            st.divider()
            st.subheader("Watchlist")
            group_labels = {
                "indices": "Indices",
                "sector_etfs": "Sector ETFs",
                "energy": "Energy",
                "commodities": "Commodities",
                "bonds": "Bonds",
                "volatility": "Volatility",
            }

            selected_ticker: str | None = st.session_state.get("selected_ticker")

            for group_key, group_label in group_labels.items():
                group = config["watchlist"].get(group_key, [])
                if not group:
                    continue
                st.markdown(f"**{group_label}**")
                for entry in group:
                    ticker = entry["ticker"]
                    name = entry["name"]
                    label = f"{ticker}  —  {name}"
                    is_selected = ticker == selected_ticker
                    btn_type = "primary" if is_selected else "secondary"
                    if st.button(label, key=f"wb_{ticker}", use_container_width=True, type=btn_type):
                        st.session_state["selected_ticker"] = ticker
                        st.rerun()

    # ---- Main content --------------------------------------------------
    page_key = st.session_state.get("page", "charts")

    if page_key == "signals":
        _render_signals_page(config, watchlist)
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
        _render_home_page(config, watchlist)
        return

    name = watchlist.get(selected, selected)
    df = get_price_df(selected, days=config["data"]["history_days"])

    st.markdown(f"## {name}")
    st.caption(f"Ticker: **{selected}**")

    if df.empty:
        st.warning(
            f"No price data found for **{selected}**. "
            "Click **Refresh Data** in the sidebar to load history."
        )
        return

    # Key metrics row
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    daily_chg = (latest["close"] - prev["close"]) / prev["close"] * 100
    ytd_start = df[df["date"].dt.year == latest["date"].year].iloc[0]
    ytd_chg = (latest["close"] - ytd_start["close"]) / ytd_start["close"] * 100

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
