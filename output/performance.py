"""Live price/signal lookups and closed-trade queries.

Manual CFD trade logging (log_trade, close_trade, update_trade_plan,
get_open_trades, get_all_trades) and macro-theme classification
(classify_trade_theme) were removed along with the Trade Log page — see
"Removed: manual CFD trade logging" in CLAUDE.md. get_closed_trades() is
kept because the weekly summary email reads closed T212 Invest positions
(from t212_pnl) for accuracy stats.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yfinance as yf
import yaml
from sqlalchemy import text

from data.database import get_engine

logger = logging.getLogger(__name__)

# Quote currency for forex CFD instruments.
# "JPY" pairs need a two-step P&L conversion: price move → JPY → USD → GBP.
# All other entries have USD as quote currency: move → USD → GBP.
FOREX_QUOTE_CCY: dict[str, str] = {
    "GBPUSD=X": "USD",
    "EURUSD=X": "USD",
    "AUDUSD=X": "USD",
    "USDJPY=X": "JPY",
    "USDCHF=X": "CHF",
    "USDCAD=X": "CAD",
    "EURGBP=X": "GBP",
}


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Live prices
# ---------------------------------------------------------------------------

def get_latest_price(ticker: str) -> Optional[float]:
    """Fetch the most recent close price from yfinance (live, uncached).

    Caller should wrap with st.cache_data(ttl=300) in the UI layer.
    """
    import math
    try:
        data = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if data.empty:
            return None
        if hasattr(data.columns, "levels"):
            data.columns = [col[0] for col in data.columns]
        price = float(data["Close"].iloc[-1])
        return None if math.isnan(price) else price
    except Exception as exc:
        logger.warning("Live price fetch failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Signal lookup
# ---------------------------------------------------------------------------

def get_current_signal(ticker: str) -> dict:
    """Return the most recent signal row for *ticker* from the signals table."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT signal, confluence_score, notes AS regime, "
                "trend_score, mean_reversion_score, volatility_score, "
                "volume_score, macro_score "
                "FROM signals WHERE ticker = :t ORDER BY date DESC LIMIT 1"
            ),
            {"t": ticker},
        ).mappings().first()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_closed_trades(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> list[dict]:
    """Return closed T212 Invest positions, optionally filtered by exit date range.

    Reads t212_pnl — reconstructed realised P&L for closed T212 Invest positions
    (see brokers/t212_learning.py:_reconstruct_t212_pnl()) — the only closed-trade
    data source left in the app now that virtual trading and manual CFD trade
    logging have both been removed (see CLAUDE.md). Every row here is a real T212
    order fill, so callers no longer need an account_type filter to exclude
    synthetic/paper data the way the old VIRTUAL rows required.

    T212 Invest is a long-only cash account, so every row is implicitly a LONG
    position — a synthetic "direction": "LONG" key is added to each dict for
    callers that display trade direction.
    """
    engine = get_engine()
    sql    = "SELECT * FROM t212_pnl WHERE 1=1"
    params: dict = {}
    if from_date:
        sql += " AND exit_date >= :from_date"
        params["from_date"] = from_date
    if to_date:
        sql += " AND exit_date <= :to_date"
        params["to_date"] = to_date
    sql += " ORDER BY exit_date ASC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        d["pnl"] = d.get("pnl_gbp")
        d["direction"] = "LONG"
        result.append(d)
    return result


