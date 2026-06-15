"""Trade logging, P&L calculation, and open-position tracking."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yfinance as yf
import yaml
from sqlalchemy import text

from data.database import Trade, get_engine, get_session

logger = logging.getLogger(__name__)


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
    try:
        data = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if data.empty:
            return None
        if hasattr(data.columns, "levels"):
            data.columns = [col[0] for col in data.columns]
        return float(data["Close"].iloc[-1])
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
# Trade lifecycle
# ---------------------------------------------------------------------------

def log_trade(
    ticker: str,
    direction: str,
    signal_type: str,
    confluence_score: float,
    regime: str,
    trend_score: int,
    mean_reversion_score: int,
    volatility_score: int,
    volume_score: int,
    macro_score: int,
    entry_price: float,
    quantity: float,
    account_id: str = "t212_practice",
    account_name: str = "Trading212 Practice",
    account_type: str = "PRACTICE",
    entry_date: Optional[datetime] = None,
    notes: str = "",
) -> int:
    """Insert a new open trade and return its database id."""
    if entry_date is None:
        entry_date = datetime.utcnow()

    session = get_session()
    try:
        trade = Trade(
            ticker=ticker,
            direction=direction,
            signal_type=signal_type,
            confluence_score=confluence_score,
            regime=regime,
            trend_score=trend_score,
            mean_reversion_score=mean_reversion_score,
            volatility_score=volatility_score,
            volume_score=volume_score,
            macro_score=macro_score,
            entry_price=entry_price,
            quantity=quantity,
            account_id=account_id,
            account_name=account_name,
            account_type=account_type,
            entry_date=entry_date,
            is_open=True,
            notes=notes,
        )
        session.add(trade)
        session.flush()
        trade_id = trade.id
        session.commit()
        return trade_id
    finally:
        session.close()


def close_trade(
    trade_id: int,
    exit_price: float,
    exit_date: Optional[datetime] = None,
) -> dict:
    """Close a trade: write exit data, compute P&L, return a summary dict."""
    if exit_date is None:
        exit_date = datetime.utcnow()

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM trades WHERE id = :id"),
            {"id": trade_id},
        ).mappings().first()

    if row is None:
        raise ValueError(f"Trade {trade_id} not found")

    entry_price    = float(row["entry_price"] or 0)
    direction      = row.get("direction") or "LONG"
    quantity       = float(row["quantity"] or 1)
    entry_date_raw = row["entry_date"]

    if isinstance(entry_date_raw, str):
        entry_dt = datetime.fromisoformat(entry_date_raw)
    elif isinstance(entry_date_raw, datetime):
        entry_dt = entry_date_raw
    else:
        entry_dt = datetime.utcnow()

    multiplier   = 1 if direction == "LONG" else -1
    pnl          = round((exit_price - entry_price) * multiplier * quantity, 4)
    pnl_pct      = round((exit_price - entry_price) / entry_price * multiplier * 100, 4) if entry_price else 0.0
    holding_days = max(0, (exit_date - entry_dt).days)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE trades SET exit_price=:ep, exit_date=:ed, pnl=:pnl, "
                "pnl_pct=:ppct, holding_days=:hd, is_open=0 WHERE id=:id"
            ),
            {"ep": exit_price, "ed": exit_date, "pnl": pnl,
             "ppct": pnl_pct, "hd": holding_days, "id": trade_id},
        )

    return {
        "id":           trade_id,
        "ticker":       row["ticker"],
        "pnl":          pnl,
        "pnl_pct":      pnl_pct,
        "holding_days": holding_days,
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_open_trades(account_type: Optional[str] = None) -> list[dict]:
    """Return open trades ordered by entry date descending, optionally filtered by account type."""
    engine = get_engine()
    sql    = "SELECT * FROM trades WHERE is_open = 1"
    params: dict = {}
    if account_type:
        sql += " AND account_type = :account_type"
        params["account_type"] = account_type
    sql += " ORDER BY entry_date DESC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_closed_trades(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    account_type: Optional[str] = None,
) -> list[dict]:
    """Return closed trades, optionally filtered by exit date range and/or account type."""
    engine = get_engine()
    sql    = "SELECT * FROM trades WHERE is_open = 0"
    params: dict = {}
    if from_date:
        sql += " AND exit_date >= :from_date"
        params["from_date"] = from_date
    if to_date:
        sql += " AND exit_date <= :to_date"
        params["to_date"] = to_date
    if account_type:
        sql += " AND account_type = :account_type"
        params["account_type"] = account_type
    sql += " ORDER BY exit_date ASC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_all_trades(account_type: Optional[str] = None) -> list[dict]:
    """Return every trade (open and closed) ordered by entry date descending, optionally filtered by account type."""
    engine = get_engine()
    sql    = "SELECT * FROM trades"
    params: dict = {}
    if account_type:
        sql += " WHERE account_type = :account_type"
        params["account_type"] = account_type
    sql += " ORDER BY entry_date DESC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]
