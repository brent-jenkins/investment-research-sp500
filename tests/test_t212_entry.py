"""Tests for the regime-aware entry gating in brokers/trading212.py:maybe_place_t212_orders().

The Trading212 HTTP client is mocked entirely (via pytest-mock) so no real
API call is ever made, and the T212_API_KEY/SECRET env vars are faked via
monkeypatch. All DB reads/writes go through the db_engine fixture's
in-memory SQLite database — see tests/conftest.py.
"""

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from data.database import Signal


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
        "trading212_invest": {
            "enabled": True,
            "live": False,
            "long_only": True,
            "max_position_gbp": 500,
            "max_total_deployed": 0.5,
            "entry_signal": "BUY",
            "entry_signal_overrides": {
                "MIXED": "STRONG_BUY",
                "RANGING_LOW_VOL": "STRONG_BUY",
                "HIGH_VOLATILITY": "STRONG_BUY",
                "TRENDING_BULL": "BUY",
                "TRENDING_BEAR": "BUY",
            },
            "exit_signal": "SELL",
            "exit_on_signal": True,
            "time_limit_as_backstop": True,
            "max_holding_days": 90,
            "ucits_mapping": {"AAPL": {"ticker": "AAPLUCITS_EQ", "currency": "USD"}},
        },
        "signals": {"reversal_confirmation_runs": 2},
    }


@pytest.fixture
def t212_mocks(db_engine, monkeypatch, mocker):
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
    mock_client.get_positions.return_value = []          # no existing positions
    mock_client.get_instruments.return_value = []         # instrument_meta stays empty -> min_qty defaults to 1.0
    mock_client.get_quantity_for_gbp.return_value = 10.0
    mock_client.place_market_order.return_value = {"id": "mock-order-1"}

    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)

    return mock_client


def test_mixed_regime_buy_signal_does_not_trigger_order(db_engine, t212_mocks):
    """MIXED overrides entry_signal to STRONG_BUY; a plain BUY signal is below
    that bar and must not place an order."""
    from brokers.trading212 import maybe_place_t212_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="BUY", regime="MIXED")

    maybe_place_t212_orders(_config(), run_type="full")

    t212_mocks.place_market_order.assert_not_called()


def test_mixed_regime_strong_buy_signal_triggers_order(db_engine, t212_mocks):
    """MIXED requires STRONG_BUY, and a STRONG_BUY signal meets that bar exactly
    — an order must be placed."""
    from brokers.trading212 import maybe_place_t212_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="MIXED")

    maybe_place_t212_orders(_config(), run_type="full")

    t212_mocks.place_market_order.assert_called_once()
    args, _ = t212_mocks.place_market_order.call_args
    assert args[0] == "AAPLUCITS_EQ"
    assert args[1] > 0  # positive quantity = BUY


def test_trending_bull_regime_buy_signal_triggers_order(db_engine, t212_mocks):
    """TRENDING_BULL overrides entry_signal to the looser BUY threshold — a
    plain BUY signal is sufficient to place an order in this regime, unlike
    in MIXED where the same signal is rejected."""
    from brokers.trading212 import maybe_place_t212_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="STRONG_BUY", regime="TRENDING_BULL")
    _seed_signal(db_engine, ticker="AAPL", signal="BUY", regime="TRENDING_BULL")

    maybe_place_t212_orders(_config(), run_type="full")

    t212_mocks.place_market_order.assert_called_once()
    args, _ = t212_mocks.place_market_order.call_args
    assert args[0] == "AAPLUCITS_EQ"
    assert args[1] > 0
