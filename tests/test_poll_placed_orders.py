"""Tests for brokers/trading212.py:poll_placed_orders() — specifically the
404 handling that decides whether an order T212 no longer lists is FILLED or
CANCELLED.

T212 removes a market order from GET /equity/orders/{id} once it settles,
whether that settlement was a BUY opening a position or a SELL closing one.
The heuristic used to tell these apart from a 404 alone must be direction-
aware: "position now exists" means FILLED for a BUY, but "no position now
exists" means FILLED for a SELL that fully closed it — the exact opposite
signal. A regression here previously mislabelled a fully-closed SELL as
CANCELLED, which silently excluded it from t212_pnl reconstruction (see
brokers/t212_learning.py) since that only reads FILLED rows.
"""

from datetime import datetime

import pytest
import requests
from sqlalchemy.orm import Session

from data.database import T212InvestOrder


def _config() -> dict:
    return {"trading212_invest": {"enabled": True, "live": False}}


def _seed_placed_order(engine, *, direction: str, ticker: str = "XLF",
                        t212_ticker: str = "STZXs_EQ", quantity: float = 12.0) -> None:
    with Session(engine) as session:
        session.add(T212InvestOrder(
            order_id="order-404-test",
            ticker=ticker,
            t212_ticker=t212_ticker,
            direction=direction,
            quantity=quantity,
            status="PLACED",
            currency_code="USD",
            placed_at=datetime.utcnow(),
        ))
        session.commit()


def _http_404():
    resp = requests.Response()
    resp.status_code = 404
    return requests.exceptions.HTTPError("404 Not Found", response=resp)


@pytest.fixture
def poll_mocks(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    monkeypatch.setattr("brokers.trading212.time.sleep", lambda *_a, **_k: None)

    mock_client = mocker.MagicMock()
    mock_client.get_order.side_effect = _http_404()
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)
    return mock_client


def _order_status(engine) -> str:
    with Session(engine) as session:
        row = session.query(T212InvestOrder).filter_by(order_id="order-404-test").one()
        return row.status


def test_sell_404_no_position_marks_filled_not_cancelled(db_engine, poll_mocks):
    """A SELL that fully closed a position leaves no position behind — that's
    expected, not evidence of cancellation."""
    from brokers.trading212 import poll_placed_orders

    _seed_placed_order(db_engine, direction="SELL")
    poll_mocks.get_position.return_value = None
    poll_mocks.get_instruments.return_value = [
        {"ticker": "STZXs_EQ", "currentPrice": 130.5, "currencyCode": "GBP"},
    ]

    poll_placed_orders(_config())

    assert _order_status(db_engine) == "FILLED"


def test_sell_404_position_still_open_marks_cancelled(db_engine, poll_mocks):
    """If the position is still there after a SELL 404s, the sale never
    actually took effect — that one genuinely is a cancellation."""
    from brokers.trading212 import poll_placed_orders

    _seed_placed_order(db_engine, direction="SELL")
    poll_mocks.get_position.return_value = {
        "ticker": "STZXs_EQ", "quantity": 12.0, "currentPrice": 130.5,
    }

    poll_placed_orders(_config())

    assert _order_status(db_engine) == "CANCELLED"


def test_buy_404_position_exists_marks_filled(db_engine, poll_mocks):
    """Unchanged behaviour: a BUY 404 with an open position means it filled."""
    from brokers.trading212 import poll_placed_orders

    _seed_placed_order(db_engine, direction="BUY")
    poll_mocks.get_position.return_value = {
        "ticker": "STZXs_EQ", "quantity": 12.0, "averagePrice": 125.0,
    }

    poll_placed_orders(_config())

    assert _order_status(db_engine) == "FILLED"


def test_buy_404_no_position_marks_cancelled(db_engine, poll_mocks):
    """Unchanged behaviour: a BUY 404 with no position means it never filled."""
    from brokers.trading212 import poll_placed_orders

    _seed_placed_order(db_engine, direction="BUY")
    poll_mocks.get_position.return_value = None

    poll_placed_orders(_config())

    assert _order_status(db_engine) == "CANCELLED"


def test_sell_404_resolved_via_order_history_gets_real_fill_price(db_engine, poll_mocks):
    """A SELL 404 resolves via get_order_history() with the *real* fill price,
    not the instrument-currentPrice guess.

    Regression test for a production incident: T212's instruments metadata
    returned a null currentPrice/price for several UCITS instruments, so the
    old estimate-only fallback permanently wrote fill_price=NULL on an
    otherwise-FILLED SELL — which then silently excluded that closed position
    from _reconstruct_t212_pnl()'s `fill_price IS NOT NULL` filter, so it
    never appeared on the Performance page despite being a real closed trade.
    get_order_history() retains the true fill data indefinitely and must be
    tried first, before any position/instrument-based guessing.
    """
    from brokers.trading212 import poll_placed_orders

    _seed_placed_order(db_engine, direction="SELL")
    poll_mocks.get_position.return_value = None
    # If the history lookup didn't take priority, this would be used instead
    # and produce a different (wrong) fill price — or None, as observed live.
    poll_mocks.get_instruments.return_value = [
        {"ticker": "STZXs_EQ", "currentPrice": None, "price": None},
    ]
    poll_mocks.get_order_history.return_value = [
        {
            "order": {"id": "order-404-test", "status": "FILLED"},
            "fill": {"price": 142.12, "quantity": -12.0, "filledAt": "2026-07-03T10:00:05.000Z"},
        },
    ]

    poll_placed_orders(_config())

    with Session(db_engine) as session:
        row = session.query(T212InvestOrder).filter_by(order_id="order-404-test").one()
        assert row.status == "FILLED"
        assert row.fill_price == 142.12
        assert row.filled_quantity == 12.0
