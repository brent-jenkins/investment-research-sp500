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
# Trade lifecycle
# ---------------------------------------------------------------------------

def _swap_basis_amount(basis: str, investment: float, margin: float, position: float) -> float:
    """Return the amount to apply the overnight rate to based on the chosen basis."""
    if basis == "margin":
        return margin if margin > 0 else investment
    if basis == "position":
        return position if position > 0 else investment
    return investment  # default: "investment"


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
    # CFD / leverage fields
    currency: str = "GBP",
    account_currency: str = "GBP",
    fx_rate_at_entry: float = 1.0,
    fx_rate_pair: Optional[str] = None,
    leverage: int = 1,
    investment_amount: Optional[float] = None,
    margin_required: Optional[float] = None,
    overnight_rate: float = 0.0,
    swap_basis: str = "investment",
    position_value: Optional[float] = None,
    spread_cost: Optional[float] = None,
    fx_fee_pct: float = 0.0,
    platform_instrument: Optional[str] = None,
    # Trade plan fields
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    risk_reward_ratio: Optional[float] = None,
    planned_risk_gbp: Optional[float] = None,
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
            currency=currency,
            account_currency=account_currency,
            fx_rate_at_entry=fx_rate_at_entry,
            fx_rate_pair=fx_rate_pair,
            leverage=leverage,
            investment_amount=investment_amount,
            margin_required=margin_required,
            overnight_rate=overnight_rate,
            swap_basis=swap_basis,
            position_value=position_value,
            spread_cost=spread_cost,
            fx_fee_pct=fx_fee_pct,
            platform_instrument=platform_instrument,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_reward_ratio=risk_reward_ratio,
            planned_risk_gbp=planned_risk_gbp,
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

    ticker            = row.get("ticker", "")
    entry_price       = float(row["entry_price"] or 0)
    direction         = row.get("direction") or "LONG"
    quantity          = float(row["quantity"] or 1)
    leverage          = int(row.get("leverage") or 1)
    position_value    = float(row.get("position_value") or 0)
    investment_amount = float(row.get("investment_amount") or 0)
    margin_required   = float(row.get("margin_required") or 0)
    overnight_rate    = float(row.get("overnight_rate") or 0)
    swap_basis        = row.get("swap_basis") or "investment"
    fx_rate_at_entry  = float(row.get("fx_rate_at_entry") or 1.0)
    fx_fee_pct        = float(row.get("fx_fee_pct") or 0.0)
    entry_date_raw    = row["entry_date"]

    if isinstance(entry_date_raw, str):
        entry_dt = datetime.fromisoformat(entry_date_raw)
    elif isinstance(entry_date_raw, datetime):
        entry_dt = entry_date_raw
    else:
        entry_dt = datetime.utcnow()

    multiplier   = 1 if direction == "LONG" else -1
    holding_days = max(0, (exit_date - entry_dt).days)

    if leverage > 1 and quantity > 0 and entry_price > 0:
        quote_ccy = FOREX_QUOTE_CCY.get(ticker)
        pnl_instr = (exit_price - entry_price) * quantity * multiplier

        if quote_ccy == "JPY":
            # USDJPY and similar: move is in JPY.  exit_price IS the USDJPY rate,
            # so dividing by it converts JPY → USD; then divide by GBPUSD → GBP.
            live_gbpusd = get_latest_price("GBPUSD=X") or 1.0
            pnl_usd   = pnl_instr / exit_price if exit_price > 0 else 0.0
            gross_pnl = round(pnl_usd / live_gbpusd, 4)
        else:
            # Standard CFD: move is already in instrument currency; convert via entry FX rate.
            gross_pnl = round(pnl_instr / fx_rate_at_entry, 4)

        swap_amt       = _swap_basis_amount(swap_basis, investment_amount, margin_required, position_value)
        swap_total     = round(holding_days * swap_amt * overnight_rate / 100, 4) if swap_amt > 0 else 0.0
        pnl_before_fee = round(gross_pnl - swap_total, 4)
        # FX conversion fee is applied to the actual realised P&L (not the trade value at open)
        fx_fee_cost    = round(abs(pnl_before_fee) * fx_fee_pct / 100, 4) if fx_fee_pct > 0 else 0.0
        pnl            = round(pnl_before_fee - fx_fee_cost, 4)
        base           = margin_required if margin_required > 0 else (investment_amount if investment_amount > 0 else position_value)
        pnl_pct        = round(pnl / base * 100, 4) if base else 0.0
    else:
        pnl_before_fee = round((exit_price - entry_price) * multiplier * quantity, 4)
        fx_fee_cost    = round(abs(pnl_before_fee) * fx_fee_pct / 100, 4) if fx_fee_pct > 0 else 0.0
        pnl            = round(pnl_before_fee - fx_fee_cost, 4)
        pnl_pct        = round((exit_price - entry_price) / entry_price * multiplier * 100, 4) if entry_price else 0.0

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
