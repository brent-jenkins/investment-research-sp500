"""Tests for the direct US stock automation path in brokers/trading212.py:
maybe_place_stock_orders().

The Trading212 HTTP client is mocked entirely (via pytest-mock) so no real
API call is ever made, and the T212_API_KEY/SECRET env vars are faked via
monkeypatch. All DB reads/writes go through the db_engine fixture's
in-memory SQLite database — see tests/conftest.py.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from data.database import PriceData, Signal, T212InvestOrder


def _seed_signal(engine, *, ticker: str, signal: str, regime: str, confluence_score: float = 0.5) -> None:
    with Session(engine) as session:
        session.add(Signal(
            ticker=ticker,
            date=datetime.utcnow(),
            signal=signal,
            confluence_score=confluence_score,
            regime=regime,
        ))
        session.commit()


def _config() -> dict:
    return {
        "watchlist": {
            "us_large_cap": [
                {"ticker": "AAPL", "name": "Apple", "exchange": "NYSE", "tradeable": True},
            ],
        },
        "trading212_invest": {"ucits_mapping": {}},
        "trading212_stocks": {
            "enabled": True,
            "live": False,
            "long_only": True,
            "max_position_gbp": 200,
            "max_total_deployed": 0.3,
            "entry_signal": "STRONG_BUY",
            "exit_signal": "SELL",
            "exit_on_signal": True,
            "time_limit_as_backstop": True,
            "max_holding_days": 30,
            "allowed_entry_regimes": ["TRENDING_BULL"],
            "entry_signal_overrides": {"TRENDING_BULL": "STRONG_BUY"},
            "fractional_shares": True,
        },
        "signals": {"reversal_confirmation_runs": 2},
    }


@pytest.fixture
def stock_mocks(db_engine, monkeypatch, mocker):
    """In-memory DB (from db_engine) plus a mocked Trading212Client and fake creds.

    Returns the mock client instance so tests can assert on place_market_order().
    """
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    monkeypatch.setattr("brokers.trading212.time.sleep", lambda *_a, **_k: None)

    mock_client = mocker.MagicMock()
    mock_client.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 5_000.0},
        "investments": {"currentValue": 0.0},
    }
    mock_client.get_positions.return_value = []
    mock_client.get_instruments.return_value = [
        {"ticker": "AAPL", "currencyCode": "USD", "maxQuantityPrecision": 2, "minTradeQuantity": 0.01},
    ]
    mock_client.get_quantity_for_gbp.return_value = 1.23456
    mock_client.place_market_order.return_value = {"id": "mock-stock-order-1"}

    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)

    return mock_client


def test_trending_bear_regime_skips_buy_phase(db_engine, stock_mocks):
    """Stocks only trade in TRENDING_BULL (allowed_entry_regimes); a
    TRENDING_BEAR regime must skip the BUY phase entirely, regardless of
    how strong the signal is."""
    from brokers.trading212 import maybe_place_stock_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="STRONG_SELL", regime="TRENDING_BEAR")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="TRENDING_BEAR")

    maybe_place_stock_orders(_config(), run_type="full")

    stock_mocks.place_market_order.assert_not_called()


def test_get_quantity_for_gbp_converts_using_live_fx_rate(db_engine, monkeypatch):
    """get_quantity_for_gbp() must convert the GBP order size to USD using the
    live GBPUSD=X close price from price_data, then divide by the ticker's
    own close price — the core of the fractional-share sizing calculation."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    with Session(db_engine) as session:
        session.add(PriceData(ticker="AAPL", date=datetime.utcnow(), close=200.0))
        session.add(PriceData(ticker="GBPUSD=X", date=datetime.utcnow(), close=1.25))
        session.commit()

    from brokers.trading212 import Trading212Client
    client = Trading212Client(live=False)
    quantity = client.get_quantity_for_gbp("AAPL", 100.0)

    # £100 * 1.25 USD/GBP = $125; $125 / $200 per share = 0.625 shares
    assert quantity == pytest.approx(0.625, abs=1e-4)


def test_fractional_order_rounds_to_instrument_precision_not_floored(db_engine, stock_mocks):
    """When the instrument reports maxQuantityPrecision > 0, the order
    quantity actually sent to place_market_order() must be the fractional
    value rounded to that precision — not floored to a whole share the way
    UCITS ETF orders are."""
    from brokers.trading212 import maybe_place_stock_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="STRONG_BUY", regime="TRENDING_BULL")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="TRENDING_BULL")

    maybe_place_stock_orders(_config(), run_type="full")

    stock_mocks.place_market_order.assert_called_once()
    args, _ = stock_mocks.place_market_order.call_args
    assert args[0] == "AAPL"
    assert args[1] == pytest.approx(1.23)  # round(1.23456, precision=2), not floor() -> 1


def test_recent_order_blocks_reentry_within_30_days(db_engine, stock_mocks):
    """A ticker with a PLACED/FILLED order in the last 30 days must not
    receive a second BUY order, even when the signal and regime otherwise
    qualify for a fresh entry."""
    from brokers.trading212 import maybe_place_stock_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="STRONG_BUY", regime="TRENDING_BULL")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="TRENDING_BULL")

    with Session(db_engine) as session:
        session.add(T212InvestOrder(
            order_id="prior-order",
            ticker="AAPL",
            t212_ticker="AAPL",
            direction="BUY",
            quantity=1.0,
            status="FILLED",
            account_type="DEMO",
            placed_at=datetime.utcnow() - timedelta(days=5),
        ))
        session.commit()

    maybe_place_stock_orders(_config(), run_type="full")

    stock_mocks.place_market_order.assert_not_called()


def test_exit_phase_fires_regardless_of_regime(db_engine, stock_mocks):
    """The regime gate only affects new entries. An existing stock position
    must still be closed on a STRONG_SELL signal even while the current
    regime (MIXED) is not in allowed_entry_regimes for stocks."""
    from brokers.trading212 import maybe_place_stock_orders

    stock_mocks.get_positions.return_value = [
        {"ticker": "AAPL", "quantity": 2.0, "averagePrice": 150.0, "currentPrice": 140.0, "ppl": -20.0},
    ]
    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_SELL", regime="MIXED")

    maybe_place_stock_orders(_config(), run_type="full")

    stock_mocks.place_market_order.assert_called_once()
    args, _ = stock_mocks.place_market_order.call_args
    assert args[0] == "AAPL"
    assert args[1] < 0  # negative quantity = closing sell
