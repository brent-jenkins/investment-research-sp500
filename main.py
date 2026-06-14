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


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _initialise() -> None:
    """Run once per process: create DB tables and start background scheduler."""
    if "app_initialised" not in st.session_state:
        init_db()
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

        st.divider()

        # Grouped watchlist selector
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
