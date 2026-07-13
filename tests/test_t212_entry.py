"""Tests for the regime-aware entry gating in brokers/trading212.py:maybe_place_t212_orders().

The Trading212 HTTP client is mocked entirely (via pytest-mock) so no real
API call is ever made, and the T212_API_KEY/SECRET env vars are faked via
monkeypatch. All DB reads/writes go through the db_engine fixture's
in-memory SQLite database — see tests/conftest.py.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from data.database import Signal, T212InvestOrder


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
    mock_client.get_instrument_price_gbp.return_value = 50.0
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


def test_buy_order_has_no_exit_reason(db_engine, t212_mocks):
    """BUY orders are not exits — exit_reason must stay None (not just unset,
    since Stage 3's P&L reconstruction distinguishes BUY/SELL by direction,
    but exit_reason=None makes the intent explicit at the DB row level)."""
    from brokers.trading212 import maybe_place_t212_orders

    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="MIXED")

    maybe_place_t212_orders(_config(), run_type="full")

    with Session(db_engine) as session:
        order = session.query(T212InvestOrder).filter_by(direction="BUY").one()
    assert order.exit_reason is None


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


# ---------------------------------------------------------------------------
# max_open_positions — caps total simultaneous positions regardless of
# max_total_deployed, independent of which ticker occupies each slot.
# ---------------------------------------------------------------------------

def test_max_open_positions_blocks_new_buy_when_at_limit(db_engine, t212_mocks):
    """A held position on any ticker counts toward the cap — once open
    positions equal max_open_positions, a new BUY signal is skipped even
    though the signal itself would otherwise qualify."""
    from brokers.trading212 import maybe_place_t212_orders

    cfg = _config()
    cfg["trading212_invest"]["max_open_positions"] = 1
    t212_mocks.get_positions.return_value = [{"ticker": "OTHERUCITS_EQ", "quantity": 5}]

    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="MIXED")

    maybe_place_t212_orders(cfg, run_type="full")

    t212_mocks.place_market_order.assert_not_called()


def test_max_open_positions_allows_buy_when_below_limit(db_engine, t212_mocks):
    """Below max_open_positions, BUY orders proceed exactly as before."""
    from brokers.trading212 import maybe_place_t212_orders

    cfg = _config()
    cfg["trading212_invest"]["max_open_positions"] = 2
    t212_mocks.get_positions.return_value = [{"ticker": "OTHERUCITS_EQ", "quantity": 5}]

    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="MIXED")

    maybe_place_t212_orders(cfg, run_type="full")

    t212_mocks.place_market_order.assert_called_once()


def test_max_open_positions_defaults_to_999_when_absent(db_engine, t212_mocks):
    """No max_open_positions key in config falls back to 999 (unlimited in
    practice) — a config predating this feature sees unchanged behaviour."""
    from brokers.trading212 import maybe_place_t212_orders

    cfg = _config()
    assert "max_open_positions" not in cfg["trading212_invest"]
    t212_mocks.get_positions.return_value = [{"ticker": "OTHERUCITS_EQ", "quantity": 5}]

    _seed_signal(db_engine, ticker="^GSPC", signal="NEUTRAL", regime="MIXED")
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_BUY", regime="MIXED")

    maybe_place_t212_orders(cfg, run_type="full")

    t212_mocks.place_market_order.assert_called_once()


# ---------------------------------------------------------------------------
# maybe_exit_t212_positions() — hourly exit check, independent of run_type.
# ---------------------------------------------------------------------------

def test_exit_sell_signal_triggers_immediate_sell_order(db_engine, t212_mocks):
    """A SELL/STRONG_SELL signal on a held position triggers an immediate SELL."""
    from brokers.trading212 import maybe_exit_t212_positions

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_SELL", regime="MIXED")

    maybe_exit_t212_positions(_config())

    t212_mocks.place_market_order.assert_called_once()
    args, _ = t212_mocks.place_market_order.call_args
    assert args[0] == "AAPLUCITS_EQ"
    assert args[1] < 0  # negative quantity = SELL (position close)


def test_exit_neutral_within_confirmation_window_does_not_sell(db_engine, t212_mocks):
    """A NEUTRAL signal inside the reversal-confirmation window must not exit yet.

    reversal_confirmation_runs=2 -> a 6h confirmation window; 1h ago is inside it.
    """
    from brokers.trading212 import maybe_exit_t212_positions
    from data.database import set_metadata

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="NEUTRAL", regime="MIXED")
    set_metadata(
        "t212_neutral_since:AAPLUCITS_EQ",
        (datetime.utcnow() - timedelta(hours=1)).isoformat(),
    )

    maybe_exit_t212_positions(_config())

    t212_mocks.place_market_order.assert_not_called()


def test_exit_neutral_past_confirmation_window_sells(db_engine, t212_mocks):
    """A NEUTRAL signal that has persisted past the confirmation window triggers an exit."""
    from brokers.trading212 import maybe_exit_t212_positions
    from data.database import set_metadata

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="NEUTRAL", regime="MIXED")
    set_metadata(
        "t212_neutral_since:AAPLUCITS_EQ",
        (datetime.utcnow() - timedelta(hours=7)).isoformat(),
    )

    maybe_exit_t212_positions(_config())

    t212_mocks.place_market_order.assert_called_once()
    args, _ = t212_mocks.place_market_order.call_args
    assert args[0] == "AAPLUCITS_EQ"
    assert args[1] < 0


def test_exit_skips_duplicate_when_recent_sell_order_exists(db_engine, t212_mocks):
    """An existing PLACED SELL order for the ticker blocks a duplicate SELL.

    This is what keeps the hourly exit check and the EOD run's delegated
    call to the same function from both selling the same position.
    """
    from brokers.trading212 import maybe_exit_t212_positions

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_SELL", regime="MIXED")

    with Session(db_engine) as session:
        session.add(T212InvestOrder(
            order_id="existing-1",
            ticker="AAPL",
            t212_ticker="AAPLUCITS_EQ",
            direction="SELL",
            quantity=5.0,
            status="PLACED",
            account_type="DEMO",
            placed_at=datetime.utcnow(),
        ))
        session.commit()

    maybe_exit_t212_positions(_config())

    t212_mocks.place_market_order.assert_not_called()


def test_exit_skips_duplicate_when_placed_sell_order_is_older_than_24h(db_engine, t212_mocks):
    """A still-PLACED (unresolved) SELL blocks a duplicate no matter its age.

    Regression test for a production incident: T212's demo account left SELL
    orders sitting in PLACED for 60+ hours before poll_placed_orders() ever
    saw them resolve. The original 24h-only dedup window let those orders
    "age out," so the next hourly exit check placed a second, duplicate SELL
    on top of the still-pending first one — which T212 rejected with
    'selling-equity-not-owned' since the shares were already committed to
    the first order. A PLACED order must block regardless of placed_at age;
    only resolved (FILLED/FAILED) orders should be subject to the 24h window.
    """
    from brokers.trading212 import maybe_exit_t212_positions

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_SELL", regime="MIXED")

    with Session(db_engine) as session:
        session.add(T212InvestOrder(
            order_id="existing-old-placed",
            ticker="AAPL",
            t212_ticker="AAPLUCITS_EQ",
            direction="SELL",
            quantity=5.0,
            status="PLACED",
            account_type="DEMO",
            placed_at=datetime.utcnow() - timedelta(hours=63),
        ))
        session.commit()

    maybe_exit_t212_positions(_config())

    t212_mocks.place_market_order.assert_not_called()


def test_exit_retries_sell_when_prior_failed_attempt_is_older_than_24h(db_engine, t212_mocks):
    """A FAILED SELL older than 24h no longer blocks a fresh attempt.

    Unlike a still-open PLACED order, a resolved FAILED order is only meant
    to suppress retries for the cooldown window — once that window passes,
    the position may still genuinely need exiting, so a new attempt must be
    allowed through.
    """
    from brokers.trading212 import maybe_exit_t212_positions

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_SELL", regime="MIXED")

    with Session(db_engine) as session:
        session.add(T212InvestOrder(
            order_id="existing-old-failed",
            ticker="AAPL",
            t212_ticker="AAPLUCITS_EQ",
            direction="SELL",
            quantity=5.0,
            status="FAILED",
            account_type="DEMO",
            placed_at=datetime.utcnow() - timedelta(hours=25),
        ))
        session.commit()

    maybe_exit_t212_positions(_config())

    t212_mocks.place_market_order.assert_called_once()


def test_exit_immediate_sell_signal_stores_exit_reason(db_engine, t212_mocks):
    """A SELL order placed via the immediate-signal branch stores a structured
    exit_reason (SELL_SIGNAL or STRONG_SELL_SIGNAL, derived from sig_type) on
    the t212_invest_orders row, not just a free-text log line."""
    from brokers.trading212 import maybe_exit_t212_positions

    t212_mocks.get_positions.return_value = [{"ticker": "AAPLUCITS_EQ", "quantity": 5.0}]
    _seed_signal(db_engine, ticker="AAPL", signal="STRONG_SELL", regime="MIXED")

    maybe_exit_t212_positions(_config())

    with Session(db_engine) as session:
        order = session.query(T212InvestOrder).filter_by(direction="SELL").one()
    assert order.exit_reason == "STRONG_SELL_SIGNAL"
