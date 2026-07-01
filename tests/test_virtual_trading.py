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
