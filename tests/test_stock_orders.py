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
    mock_client.get_instrument_price_gbp.return_value = 150.0
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


def test_get_quantity_for_gbp_uses_ucits_instrument_price_iwm(db_engine, monkeypatch, mocker):
    """Regression test for a live mis-sizing: the IWM UCITS instrument
    (RU2Kl_EQ) trades around £5.25/share on T212, completely unrelated to
    the US IWM close price (~$220) that get_quantity_for_gbp() used to read
    instead. A £500 target must size off the instrument's own price."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    with Session(db_engine) as session:
        session.add(PriceData(ticker="IWM", date=datetime.utcnow(), close=220.0))
        session.commit()

    from brokers.trading212 import Trading212Client
    client = Trading212Client(live=False)
    mocker.patch.object(client, "get_instruments", return_value=[
        {"ticker": "RU2Kl_EQ", "currencyCode": "GBP", "currentPrice": 5.25},
    ])

    quantity = client.get_quantity_for_gbp("RU2Kl_EQ", 500.0, research_ticker="IWM")

    assert quantity == 95  # floor(500 / 5.25) = 95, not floor(500/220)=2


def test_get_quantity_for_gbp_uses_ucits_instrument_price_xlf(db_engine, monkeypatch, mocker):
    """Regression test for a second live mis-sizing: the XLF UCITS instrument
    (STZXs_EQ) trades around £130/share, far above the US XLF close price
    (~$50) that used to be read instead."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    with Session(db_engine) as session:
        session.add(PriceData(ticker="XLF", date=datetime.utcnow(), close=50.0))
        session.commit()

    from brokers.trading212 import Trading212Client
    client = Trading212Client(live=False)
    mocker.patch.object(client, "get_instruments", return_value=[
        {"ticker": "STZXs_EQ", "currencyCode": "GBP", "currentPrice": 130.0},
    ])

    quantity = client.get_quantity_for_gbp("STZXs_EQ", 500.0, research_ticker="XLF")

    assert quantity == 3  # floor(500 / 130) = 3, not floor(500/50)=12 or similar


def test_get_quantity_for_gbp_falls_back_when_instrument_price_missing(db_engine, monkeypatch, mocker, caplog):
    """When the T212 instruments API has no price for the instrument,
    get_quantity_for_gbp() must fall back to the research ticker's close
    price from price_data, convert via live GBPUSD=X, and log a clear
    warning that the fallback (potentially inaccurate) path was used."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    with Session(db_engine) as session:
        session.add(PriceData(ticker="AAPL", date=datetime.utcnow(), close=200.0))
        session.add(PriceData(ticker="GBPUSD=X", date=datetime.utcnow(), close=1.25))
        session.commit()

    from brokers.trading212 import Trading212Client
    client = Trading212Client(live=False)
    mocker.patch.object(client, "get_instruments", return_value=[])  # no instrument price available

    with caplog.at_level("WARNING"):
        quantity = client.get_quantity_for_gbp(
            "AAPL_UCITS", 100.0, research_ticker="AAPL", floor_to_whole_share=False,
        )

    # £100 / (200/1.25 GBP-per-share) = 0.625 shares
    assert quantity == pytest.approx(0.625, abs=1e-4)
    assert "instrument price unavailable" in caplog.text.lower()


def test_get_quantity_for_gbp_logs_sizing_calculation_detail(db_engine, monkeypatch, mocker, caplog):
    """Every sizing call must log price/target/quantity/estimated-value at
    INFO level so a future sizing discrepancy is diagnosable from server
    logs without needing to reproduce the bug."""
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")

    from brokers.trading212 import Trading212Client
    client = Trading212Client(live=False)
    mocker.patch.object(client, "get_instruments", return_value=[
        {"ticker": "RU2Kl_EQ", "currencyCode": "GBP", "currentPrice": 5.25},
    ])

    with caplog.at_level("INFO"):
        client.get_quantity_for_gbp("RU2Kl_EQ", 500.0, research_ticker="IWM")

    assert "Sizing" in caplog.text
    assert "RU2Kl_EQ" in caplog.text
    assert "quantity" in caplog.text.lower()
    assert "estimated_value" in caplog.text.lower()


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
