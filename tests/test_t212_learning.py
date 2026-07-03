"""Tests for brokers/t212_learning.py:_reconstruct_t212_pnl().

All DB reads/writes go through the db_engine fixture's in-memory SQLite
database — see tests/conftest.py. No T212 API calls are made; this module
only reads t212_invest_orders, price_data, and signals, and writes t212_pnl.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from data.database import PriceData, Signal, T212InvestOrder, T212Pnl


def _buy(session, **overrides):
    row = {
        "order_id": "buy-1",
        "ticker": "AAPL",
        "t212_ticker": "AAPLUCITS_EQ",
        "direction": "BUY",
        "quantity": 10.0,
        "filled_quantity": 10.0,
        "fill_price": 100.0,
        "currency_code": "USD",
        "status": "FILLED",
        "signal_type": "STRONG_BUY",
        "confluence_score": 0.6,
        "regime": "TRENDING_BULL",
        "account_type": "DEMO",
        "filled_at": datetime(2026, 6, 10),
    }
    row.update(overrides)
    session.add(T212InvestOrder(**row))


def _sell(session, **overrides):
    row = {
        "order_id": "sell-1",
        "ticker": "AAPL",
        "t212_ticker": "AAPLUCITS_EQ",
        "direction": "SELL",
        "quantity": 10.0,
        "filled_quantity": 10.0,
        "fill_price": 110.0,
        "currency_code": "USD",
        "status": "FILLED",
        "signal_type": "STRONG_SELL",
        "regime": "MIXED",
        "exit_reason": "STRONG_SELL_SIGNAL",
        "account_type": "DEMO",
        "filled_at": datetime(2026, 6, 20),
    }
    row.update(overrides)
    session.add(T212InvestOrder(**row))


def test_reconstructs_gbp_pnl_and_context_from_buy_sell_pair(db_engine):
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        _buy(session)
        _sell(session)
        session.commit()

    inserted = _reconstruct_t212_pnl({})
    assert inserted == 1

    with Session(db_engine) as session:
        row = session.query(T212Pnl).one()
        assert row.ticker == "AAPL"
        assert row.entry_price == 100.0
        assert row.exit_price == 110.0
        assert row.pnl_pct == 0.1
        assert row.pnl_gbp == round((110.0 - 100.0) * 10.0 / 1.27, 4)
        assert row.holding_days == 10
        assert row.exit_reason == "STRONG_SELL_SIGNAL"
        assert row.signal_type == "STRONG_BUY"
        assert row.confluence_score == 0.6
        assert row.regime == "TRENDING_BULL"
        assert row.currency == "USD"


def test_idempotent_on_repeated_calls(db_engine):
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        _buy(session)
        _sell(session)
        session.commit()

    first = _reconstruct_t212_pnl({})
    second = _reconstruct_t212_pnl({})
    assert first == 1
    assert second == 0

    with Session(db_engine) as session:
        assert session.query(T212Pnl).count() == 1


def test_module_scores_looked_up_from_nearest_prior_signal(db_engine):
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        session.add(Signal(
            ticker="AAPL", date=datetime(2026, 6, 9), signal="STRONG_BUY", confluence_score=0.6,
            trend_score=1, mean_reversion_score=0, volatility_score=1, volume_score=1, macro_score=0,
        ))
        _buy(session)
        _sell(session)
        session.commit()

    _reconstruct_t212_pnl({})

    with Session(db_engine) as session:
        row = session.query(T212Pnl).one()
        assert row.trend_score == 1
        assert row.mean_reversion_score == 0
        assert row.volatility_score == 1
        assert row.volume_score == 1
        assert row.macro_score == 0


def test_module_score_lookup_matches_signal_on_the_same_day_as_the_buy_fill(db_engine):
    """Regression test: date(date) <= date(:d) must match a signals row dated
    the exact same calendar day as the BUY fill, not just strictly earlier
    days — a raw string `date <= :d` comparison silently excludes same-day
    rows because a Python datetime with microsecond=0 stringifies without a
    fractional-seconds component while the stored column always has one."""
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        session.add(Signal(
            ticker="AAPL", date=datetime(2026, 6, 10), signal="STRONG_BUY", confluence_score=0.6,
            trend_score=1, mean_reversion_score=-1, volatility_score=0, volume_score=1, macro_score=-1,
        ))
        _buy(session, filled_at=datetime(2026, 6, 10))  # same calendar day as the signal
        _sell(session)
        session.commit()

    _reconstruct_t212_pnl({})

    with Session(db_engine) as session:
        row = session.query(T212Pnl).one()
        assert row.trend_score == 1
        assert row.mean_reversion_score == -1
        assert row.volatility_score == 0
        assert row.volume_score == 1
        assert row.macro_score == -1


def test_missing_signal_history_leaves_module_scores_null(db_engine):
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        _buy(session)
        _sell(session)
        session.commit()

    _reconstruct_t212_pnl({})

    with Session(db_engine) as session:
        row = session.query(T212Pnl).one()
        assert row.trend_score is None
        assert row.macro_score is None


def test_sell_with_no_prior_buy_is_skipped_not_errored(db_engine):
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        _sell(session)  # no matching BUY at all
        session.commit()

    inserted = _reconstruct_t212_pnl({})
    assert inserted == 0

    with Session(db_engine) as session:
        assert session.query(T212Pnl).count() == 0


def test_fifo_matches_two_separate_round_trips_on_the_same_ticker(db_engine):
    """Two full buy->sell cycles on AAPL must pair BUY1<->SELL1 and BUY2<->SELL2,
    not cross-match — this codebase never holds overlapping positions on one
    ticker, but the FIFO queue must still get the pairing right regardless."""
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.27))
        _buy(session, order_id="buy-1", fill_price=100.0, filled_at=datetime(2026, 6, 1))
        _sell(session, order_id="sell-1", fill_price=110.0, filled_at=datetime(2026, 6, 5))
        _buy(session, order_id="buy-2", fill_price=120.0, filled_at=datetime(2026, 6, 10))
        _sell(session, order_id="sell-2", fill_price=90.0, filled_at=datetime(2026, 6, 15))
        session.commit()

    inserted = _reconstruct_t212_pnl({})
    assert inserted == 2

    with Session(db_engine) as session:
        rows = session.query(T212Pnl).order_by(T212Pnl.entry_date).all()
        assert len(rows) == 2
        assert rows[0].entry_price == 100.0 and rows[0].exit_price == 110.0
        assert rows[1].entry_price == 120.0 and rows[1].exit_price == 90.0


def test_eur_instrument_converts_via_eurusd_and_gbpusd(db_engine):
    from brokers.t212_learning import _reconstruct_t212_pnl

    with Session(db_engine) as session:
        session.add(PriceData(ticker="GBPUSD=X", date=datetime(2026, 6, 20), close=1.25))
        session.add(PriceData(ticker="EURUSD=X", date=datetime(2026, 6, 20), close=1.10))
        _buy(session, currency_code="EUR", fill_price=50.0)
        _sell(session, currency_code="EUR", fill_price=55.0)
        session.commit()

    _reconstruct_t212_pnl({})

    with Session(db_engine) as session:
        row = session.query(T212Pnl).one()
        pnl_eur = (55.0 - 50.0) * 10.0
        expected_gbp = round(pnl_eur * 1.10 / 1.25, 4)
        assert row.pnl_gbp == expected_gbp


def test_exit_reconstruction_runs_even_with_zero_open_positions(db_engine, mocker):
    """Regression test for the ordering bug: maybe_exit_t212_positions() must
    call _reconstruct_t212_pnl() even when positions_by_ucits is empty, since
    that's exactly the state right after a position has just closed."""
    import os
    os.environ["T212_API_KEY"] = "test-key"
    os.environ["T212_API_SECRET"] = "test-secret"

    mock_client = mocker.MagicMock()
    mock_client.get_positions.return_value = []  # no open positions
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)
    mock_reconstruct = mocker.patch("brokers.t212_learning._reconstruct_t212_pnl")

    from brokers.trading212 import maybe_exit_t212_positions

    config = {
        "trading212_invest": {
            "enabled": True,
            "live": False,
            "ucits_mapping": {"AAPL": {"ticker": "AAPLUCITS_EQ", "currency": "USD"}},
        },
        "signals": {"reversal_confirmation_runs": 2},
    }
    maybe_exit_t212_positions(config)

    mock_reconstruct.assert_called_once_with(config)
