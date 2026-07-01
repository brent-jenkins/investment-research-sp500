"""Regression test for _process_virtual_trades() (data/scheduler.py).

Covers a churn bug: when a virtual position hits its MAX HOLDING backstop
(e.g. the 5-day forex limit) while its signal is still favourable, the close
phase used to remove the ticker from open_by_ticker and the open phase — run
immediately after, in the same call, against the same price snapshot — would
then reopen it at the very same price. The position never actually left the
market, but the new trade row's entry_date reset to "now", making the Trade
Log's Days column read 0 for what was, in substance, a several-day-old
holding. The fix defers re-entry to the next scheduled run by tracking
tickers closed earlier in the same call and excluding them from the open
phase.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from data.database import Signal, Trade, PriceData


def _config() -> dict:
    return {
        "watchlist": {
            "forex": [
                {"ticker": "AUDUSD=X", "name": "AUD/USD", "exchange": "FX", "tradeable": True},
            ],
        },
        "virtual_trading": {
            "enabled": True,
            "entry_signal": "STRONG_BUY",
            "max_open_positions": 5,
            "position_pct": 0.05,
            "portfolio_size_gbp": 10000,
        },
        "backtest": {
            "atr_stop_multiplier": 2.0,
            "max_holding_days_forex": 5,
        },
        "signals": {"reversal_confirmation_runs": 2},
    }


def test_max_holding_close_does_not_reopen_in_the_same_run(db_engine):
    """A forex position past its 5-day max hold must close and stay closed
    for the rest of this run, even though its STRONG_BUY signal — which
    would otherwise qualify it for a fresh entry — is still active."""
    from data import scheduler

    now = datetime.utcnow()

    with Session(db_engine) as session:
        session.add(Signal(
            ticker="^GSPC", date=now, signal="NEUTRAL",
            confluence_score=0.1, regime="TRENDING_BULL",
        ))
        session.add(Signal(
            ticker="AUDUSD=X", date=now, signal="STRONG_BUY",
            confluence_score=0.8, regime="TRENDING_BULL",
        ))
        session.add(PriceData(ticker="AUDUSD=X", date=now, close=0.6888))
        session.add(Trade(
            ticker="AUDUSD=X", account_type="VIRTUAL", direction="LONG",
            entry_date=now - timedelta(days=6), entry_price=0.6903,
            quantity=192.45, stop_loss_price=0.60,  # far below current price — no stop hit
            is_open=True,
        ))
        session.commit()

    scheduler._process_virtual_trades(_config(), run_type="full", eligible_exchanges=None)

    with Session(db_engine) as session:
        rows = session.query(Trade).filter(Trade.ticker == "AUDUSD=X").all()

    assert len(rows) == 1, (
        "A new trade was opened in the same run the old one closed in — "
        "the MAX HOLDING close must not be immediately reopened."
    )
    assert rows[0].is_open is False
    assert rows[0].holding_days == 6


def test_closed_trade_pnl_is_cost_adjusted_for_learning(db_engine):
    """pnl_pct must stay the raw, cost-free price move (for display), while
    pnl (GBP) — the column _fetch_trades_for_learning() actually reads — must
    have virtual_trading.cost_adjustment_pct deducted first. Before this fix,
    virtual trade closes never wrote pnl at all, so they were silently
    excluded from feedback-learning's `pnl IS NOT NULL` filter."""
    from data import scheduler

    now = datetime.utcnow()
    cfg = _config()
    cfg["virtual_trading"]["cost_adjustment_pct"] = 1.0  # 1% round-trip cost

    with Session(db_engine) as session:
        session.add(Signal(
            ticker="^GSPC", date=now, signal="NEUTRAL",
            confluence_score=0.1, regime="TRENDING_BULL",
        ))
        session.add(Signal(
            ticker="AUDUSD=X", date=now, signal="STRONG_BUY",
            confluence_score=0.8, regime="TRENDING_BULL",
        ))
        session.add(PriceData(ticker="AUDUSD=X", date=now, close=0.6936))
        session.add(Trade(
            ticker="AUDUSD=X", account_type="VIRTUAL", direction="LONG",
            entry_date=now - timedelta(days=6), entry_price=0.6800,
            quantity=192.45, stop_loss_price=0.60, investment_amount=500.0,
            is_open=True,
        ))
        session.commit()

    scheduler._process_virtual_trades(cfg, run_type="full", eligible_exchanges=None)

    with Session(db_engine) as session:
        row = session.query(Trade).filter(Trade.ticker == "AUDUSD=X").one()

    assert row.pnl_pct == pytest.approx(0.02, abs=1e-6)   # raw 2% gain, untouched
    assert row.pnl == pytest.approx(5.0, abs=1e-4)         # (2% - 1% cost) * £500


def _config_with_ucits() -> dict:
    cfg = _config()
    cfg["watchlist"]["sector_etfs"] = [
        {"ticker": "XLK", "name": "Tech Sector ETF", "exchange": "NYSE", "tradeable": True},
    ]
    cfg["trading212_invest"] = {"ucits_mapping": {"XLK": "XLKD"}}
    return cfg


def test_ucits_mapped_ticker_does_not_open_a_new_virtual_position(db_engine):
    """A ticker in trading212_invest.ucits_mapping must never get a new
    virtual position — T212 Invest already automates it. A non-UCITS ticker
    with the identical signal in the same run must still open normally, so
    this proves the exclusion is scoped to UCITS tickers, not a global stop."""
    from data import scheduler

    now = datetime.utcnow()
    cfg = _config_with_ucits()

    with Session(db_engine) as session:
        session.add(Signal(
            ticker="^GSPC", date=now, signal="NEUTRAL",
            confluence_score=0.1, regime="TRENDING_BULL",
        ))
        session.add(Signal(
            ticker="XLK", date=now, signal="STRONG_BUY",
            confluence_score=0.8, regime="TRENDING_BULL",
        ))
        session.add(Signal(
            ticker="AUDUSD=X", date=now, signal="STRONG_BUY",
            confluence_score=0.8, regime="TRENDING_BULL",
        ))
        session.add(PriceData(ticker="XLK", date=now, close=200.0))
        session.add(PriceData(ticker="AUDUSD=X", date=now, close=0.6888))
        session.commit()

    scheduler._process_virtual_trades(cfg, run_type="full", eligible_exchanges=None)

    with Session(db_engine) as session:
        xlk_trades   = session.query(Trade).filter(Trade.ticker == "XLK").all()
        audusd_trades = session.query(Trade).filter(Trade.ticker == "AUDUSD=X").all()

    assert xlk_trades == [], "XLK is UCITS-mapped — virtual trading must not open a position for it."
    assert len(audusd_trades) == 1, "AUDUSD=X is not UCITS-mapped — it must still open normally."


def test_ucits_mapped_ticker_existing_position_still_closes_naturally(db_engine):
    """The UCITS exclusion only gates the open phase — an existing open
    virtual position on a UCITS-mapped ticker must still close normally
    (e.g. on hitting its max-holding backstop), not be force-closed early
    or left stuck open."""
    from data import scheduler

    now = datetime.utcnow()
    cfg = _config_with_ucits()
    cfg["backtest"]["max_holding_days"] = 5  # XLK falls under the generic default bucket

    with Session(db_engine) as session:
        session.add(Signal(
            ticker="^GSPC", date=now, signal="NEUTRAL",
            confluence_score=0.1, regime="TRENDING_BULL",
        ))
        session.add(Signal(
            ticker="XLK", date=now, signal="STRONG_BUY",
            confluence_score=0.8, regime="TRENDING_BULL",
        ))
        session.add(PriceData(ticker="XLK", date=now, close=205.0))
        session.add(Trade(
            ticker="XLK", account_type="VIRTUAL", direction="LONG",
            entry_date=now - timedelta(days=6), entry_price=200.0,
            quantity=2.5, stop_loss_price=150.0, investment_amount=500.0,
            is_open=True,
        ))
        session.commit()

    scheduler._process_virtual_trades(cfg, run_type="full", eligible_exchanges=None)

    with Session(db_engine) as session:
        row = session.query(Trade).filter(Trade.ticker == "XLK").one()

    assert row.is_open is False, "Existing UCITS-ticker position must still close on its own exit conditions."
