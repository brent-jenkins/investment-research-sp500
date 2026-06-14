"""Investment Research Dashboard — Streamlit entry point."""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so sibling packages resolve correctly
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging
from datetime import datetime

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

# Colour map for signal classifications
_SIGNAL_COLOURS: dict[str, str] = {
    "STRONG_BUY":  "#1a5c38",   # dark green
    "BUY":         "#26a69a",   # teal / light green
    "NEUTRAL":     "#6b7280",   # grey
    "SELL":        "#f59e0b",   # amber / orange
    "STRONG_SELL": "#ef5350",   # red
}

_SCORE_COLOURS: dict[int, str] = {
    1:  "#26a69a",   # green
    0:  "#374151",   # dark grey
    -1: "#ef5350",   # red
}


def _signal_badge(label: str) -> str:
    """Return an HTML span badge for a signal classification."""
    colour = _SIGNAL_COLOURS.get(label, "#6b7280")
    display = label.replace("_", " ")
    return (
        f'<span style="background:{colour};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8rem;font-weight:600;">{display}</span>'
    )


def _score_cell(score: int | None) -> str:
    """Return an HTML span for a single module score cell."""
    if score is None:
        return '<span style="color:#6b7280">—</span>'
    colour = _SCORE_COLOURS.get(int(score), "#374151")
    symbol = {1: "▲", 0: "●", -1: "▼"}.get(int(score), "●")
    return (
        f'<span style="color:{colour};font-weight:700;font-size:1rem">{symbol}</span>'
    )


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
    st.caption(
        "▲ bullish &nbsp;&nbsp; ● neutral &nbsp;&nbsp; ▼ bearish &nbsp;&nbsp;"
        "(colour-coded per module)"
    )

    # Build display DataFrame
    table_rows = []
    for r in rows:
        ticker = r.get("ticker", "")
        table_rows.append(
            {
                "Ticker":    ticker,
                "Name":      watchlist.get(ticker, ticker),
                "_signal":   r.get("signal", "NEUTRAL"),
                "Score":     r.get("confluence_score") or 0.0,
                "_trend":    r.get("trend_score") or 0,
                "_mr":       r.get("mean_reversion_score") or 0,
                "_vol":      r.get("volatility_score") or 0,
                "_volume":   r.get("volume_score") or 0,
                "_macro":    r.get("macro_score") or 0,
            }
        )

    # Render as an HTML table for full colour control
    header_html = (
        "<table style='width:100%;border-collapse:collapse;font-size:0.88rem'>"
        "<thead><tr style='border-bottom:1px solid #374151'>"
        "<th style='text-align:left;padding:6px'>Ticker</th>"
        "<th style='text-align:left;padding:6px'>Name</th>"
        "<th style='text-align:center;padding:6px'>Signal</th>"
        "<th style='text-align:right;padding:6px'>Score</th>"
        "<th style='text-align:center;padding:6px'>Trend</th>"
        "<th style='text-align:center;padding:6px'>Mean Rev</th>"
        "<th style='text-align:center;padding:6px'>Volatility</th>"
        "<th style='text-align:center;padding:6px'>Volume</th>"
        "<th style='text-align:center;padding:6px'>Macro</th>"
        "</tr></thead><tbody>"
    )

    body_parts = []
    for i, r in enumerate(table_rows):
        bg = "#1f2937" if i % 2 == 0 else "#111827"
        score = r["Score"]
        score_colour = (
            "#26a69a" if score > 0.1
            else "#ef5350" if score < -0.1
            else "#6b7280"
        )
        body_parts.append(
            f"<tr style='background:{bg}'>"
            f"<td style='padding:6px;font-weight:600'>{r['Ticker']}</td>"
            f"<td style='padding:6px;color:#9ca3af'>{r['Name']}</td>"
            f"<td style='padding:6px;text-align:center'>{_signal_badge(r['_signal'])}</td>"
            f"<td style='padding:6px;text-align:right;color:{score_colour};font-weight:700'>"
            f"{score:+.3f}</td>"
            f"<td style='padding:6px;text-align:center'>{_score_cell(r['_trend'])}</td>"
            f"<td style='padding:6px;text-align:center'>{_score_cell(r['_mr'])}</td>"
            f"<td style='padding:6px;text-align:center'>{_score_cell(r['_vol'])}</td>"
            f"<td style='padding:6px;text-align:center'>{_score_cell(r['_volume'])}</td>"
            f"<td style='padding:6px;text-align:center'>{_score_cell(r['_macro'])}</td>"
            "</tr>"
        )

    st.markdown(
        header_html + "".join(body_parts) + "</tbody></table>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _initialise() -> None:
    """Ensure the DB schema is current and start the background scheduler once.

    init_db() is called on every render because it is idempotent — create_all()
    is a no-op when tables exist and _apply_migrations() skips columns that are
    already present. This guarantees schema changes are picked up immediately
    after a code update without requiring a Streamlit session restart.

    The scheduler is only started once per session (tracked in session_state).
    """
    init_db()
    if "app_initialised" not in st.session_state:
        try:
            start_scheduler()
        except Exception as exc:
            logger.warning("Scheduler could not start: %s", exc)
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
    config = _load_config()
    watchlist = _build_watchlist(config)

    # ---- Sidebar -------------------------------------------------------
    with st.sidebar:
        st.title("📈 Investment Research")
        st.divider()

        # Page navigation
        page = st.radio(
            "Navigation",
            options=["📈 Charts", "📊 Signals"],
            index=0 if st.session_state.get("page", "charts") == "charts" else 1,
            label_visibility="collapsed",
        )
        current_page = "signals" if page == "📊 Signals" else "charts"
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
    if st.session_state.get("page") == "signals":
        _render_signals_page(config, watchlist)
        return

    # Charts page
    selected = st.session_state.get("selected_ticker")

    if selected is None:
        st.markdown("## Welcome to Investment Research")
        st.info("Select an asset from the sidebar to view its price chart.")

        col1, col2, col3 = st.columns(3)
        col1.metric("Watchlist size", len(watchlist))
        last = get_last_update_timestamp()
        col2.metric("Last update", last.strftime("%Y-%m-%d") if last else "None")
        col3.metric("Database", "Ready")
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
