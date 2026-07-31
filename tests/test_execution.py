"""Tests for brokers/trading212.py's live order-placement functions —
maybe_place_orders()/maybe_exit_positions(), the momentum + crisis-override
strategy's execution path. The Trading212 HTTP client is mocked entirely
(pytest-mock); no real API call is ever made. All DB reads/writes go through
the db_engine fixture's in-memory SQLite database (tests/conftest.py).
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text

from data.database import InvestOrder


def _price_frame(n_days: int, start_price: float, daily_drift: float, last_day_shock: float = 0.0) -> pd.DataFrame:
    """n_days of synthetic OHLC data. daily_drift is a per-day % return applied
    uniformly (e.g. 0.001 for a mild uptrend). last_day_shock, if non-zero,
    overrides the final day's return (e.g. -0.10 for a crisis day)."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = [start_price]
    for _ in range(n_days - 1):
        closes.append(closes[-1] * (1 + daily_drift))
    if last_day_shock:
        closes[-1] = closes[-2] * (1 + last_day_shock)
    closes = np.array(closes)
    highs = closes * 1.01
    lows = closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _bullish_frame() -> pd.DataFrame:
    """~400 days of mild uptrend -> trailing 12m momentum > 0, no crisis day anywhere."""
    return _price_frame(400, 100.0, 0.0015)


def _bearish_frame() -> pd.DataFrame:
    """~400 days of mild downtrend -> trailing 12m momentum < 0 (signal off)."""
    return _price_frame(400, 100.0, -0.0015)


def _bullish_with_crisis_today() -> pd.DataFrame:
    """Bullish trend, but today itself is a >=7% single-day decline."""
    return _price_frame(400, 100.0, 0.0015, last_day_shock=-0.09)


def _config(mapping=None, enabled=True) -> dict:
    if mapping is None:
        mapping = {
            "VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True},
            "AAPL": {"ticker": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
        }
    return {
        "trading212_invest": {
            "enabled": enabled,
            "live": False,
            "max_total_deployed": 0.5,
            "max_open_positions": 20,
            "instrument_mapping": mapping,
            "max_position_gbp": 500,
            "var_confidence": 0.95,
            "var_lookback_days": 252,
            "max_risk_pct": 0.01,
            "min_position_gbp": 50,
        },
        "backtest": {"atr_stop_multiplier": 2.0, "max_holding_days": 30},
        "signals": {"atr_period": 14},
    }


@pytest.fixture
def v2_mocks(db_engine, monkeypatch, mocker):
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
    mock_client.get_instruments.return_value = []
    mock_client.get_quantity_for_gbp.return_value = 10.0
    mock_client.get_instrument_price_gbp.return_value = 50.0
    mock_client.place_market_order.return_value = {"id": "mock-order-1"}
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)

    return mock_client


def _mock_price_data(mocker, frames: dict):
    """frames: {ticker: DataFrame}. Anything not listed falls back to a
    generic bullish frame (so ^GSPC always resolves unless overridden)."""
    default = _bullish_frame()

    def _fake_get_price_df(ticker, days=8000):
        return frames.get(ticker, default).copy()

    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)


# ---------------------------------------------------------------------------
# Disabled / missing-credentials early returns
# ---------------------------------------------------------------------------

def test_disabled_config_is_a_noop(db_engine, v2_mocks):
    from brokers.trading212 import maybe_place_orders

    maybe_place_orders(_config(enabled=False), run_type="full")
    v2_mocks.get_account_summary.assert_not_called()


def test_missing_credentials_is_a_noop(db_engine, v2_mocks, monkeypatch):
    from brokers.trading212 import maybe_place_orders

    monkeypatch.delenv("T212_API_KEY", raising=False)
    maybe_place_orders(_config(), run_type="full")
    v2_mocks.get_account_summary.assert_not_called()


def test_analysis_only_run_type_skips_buy_phase(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {})
    maybe_place_orders(_config(), run_type="analysis_only")
    v2_mocks.place_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Entry gate — sig_on AND is_member AND not entry_blocked, all required
# ---------------------------------------------------------------------------

def test_entry_fires_when_all_three_conditions_hold(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "I50Dl_EQ"
    assert args[1] > 0  # BUY = positive quantity


def test_entry_blocked_when_momentum_negative(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bearish_frame()})
    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_entry_blocked_when_stock_not_index_member(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"AAPL": {"ticker": "AAPL_US_EQ", "currency": "USD", "is_etf": False}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_entry_blocked_during_crisis_cooldown_window(db_engine, v2_mocks, mocker):
    """A ticker with genuinely positive momentum must still be blocked from
    entering while the crisis entry-block window (extend_trigger) is active,
    even on a day that isn't itself a raw >=7% decline."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)

    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_etf_always_eligible_regardless_of_membership_check(db_engine, v2_mocks, mocker):
    """ETFs have no membership concept -- is_member() must never even be
    consulted for the is_etf branch (see _v2_ticker_state's is_member computation)."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    spy = mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_called_once()
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# Exit priority -- must match analysis/v2/backtest.py:simulate_trades() exactly:
# crisis > index-removal > stop-loss > signal-off > max-holding
# ---------------------------------------------------------------------------

def _seed_open_position(engine, *, ticker, t212_ticker, stop_price, days_ago, is_etf, momentum_pct=0.05):
    entry_dt = datetime.utcnow() - timedelta(days=days_ago)
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, momentum_pct, stop_price, account_type, currency_code,
                 placed_at, filled_at, created_at)
            VALUES (:t, :tt, 'BUY', 10, 100.0, 10, 'FILLED', :etf, :mom, :stop, 'DEMO', 'USD',
                    :at, :at, :at)
        """), {"t": ticker, "tt": t212_ticker, "etf": is_etf, "mom": momentum_pct,
               "stop": stop_price, "at": entry_dt.isoformat()})


def _run_exit(db_engine, v2_mocks, mocker, *, ticker, t212_ticker, frame, stop_price,
              days_ago=10, is_etf=True, is_member=True, crisis_now=False, entry_blocked=False,
              max_holding_days=30):
    from brokers.trading212 import maybe_exit_positions

    v2_mocks.get_positions.return_value = [{"ticker": t212_ticker, "quantity": 10.0}]
    _mock_price_data(mocker, {ticker: frame})
    mocker.patch("analysis.universe.is_member", return_value=is_member)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=crisis_now)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=entry_blocked)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=max_holding_days)

    _seed_open_position(db_engine, ticker=ticker, t212_ticker=t212_ticker,
                        stop_price=stop_price, days_ago=days_ago, is_etf=is_etf)

    cfg = _config(mapping={ticker: {"ticker": t212_ticker, "currency": "USD", "is_etf": is_etf}})
    maybe_exit_positions(cfg)


def test_exit_priority_crisis_wins_over_everything(db_engine, v2_mocks, mocker):
    """Crisis fires even though the position is also over its stop and past
    max holding -- crisis must still be the exit_reason recorded."""
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=_price_frame(400, 100.0, 0.0015, last_day_shock=-0.09),
        stop_price=99999.0,  # guarantees price <= stop_price would also fire if checked
        days_ago=999,        # guarantees max-holding would also fire if checked
        crisis_now=True,
    )
    v2_mocks.place_market_order.assert_called_once()
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "CRISIS_EXIT"


def test_exit_priority_index_removal_before_stop_and_signal(db_engine, v2_mocks, mocker):
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="AAPL", t212_ticker="AAPL_US_EQ",
        frame=_bearish_frame(),   # would also be SIGNAL_OFF
        stop_price=99999.0,       # would also be STOP_LOSS
        is_etf=False, is_member=False,
        crisis_now=False,
    )
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "INDEX_REMOVAL"


def test_exit_priority_stop_loss_before_signal_off(db_engine, v2_mocks, mocker):
    frame = _bearish_frame()  # signal is off too, but stop must take priority
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 10,  # guarantees price <= stop_price
        is_member=True,
    )
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "STOP_LOSS"


def test_exit_priority_signal_off_before_max_holding(db_engine, v2_mocks, mocker):
    frame = _bearish_frame()
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 0.01,  # far below price -- stop does NOT fire
        days_ago=999,                   # would also trigger MAX_HOLDING
        max_holding_days=30,
    )
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "SIGNAL_OFF"


def test_exit_priority_max_holding_when_nothing_else_fires(db_engine, v2_mocks, mocker):
    frame = _bullish_frame()  # signal stays on
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 0.01,
        days_ago=999,
        max_holding_days=30,
    )
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "MAX_HOLDING"


def test_no_exit_when_nothing_fires(db_engine, v2_mocks, mocker):
    frame = _bullish_frame()
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 0.01,
        days_ago=1,
        max_holding_days=90,
    )
    v2_mocks.place_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Duplicate-order guard
# ---------------------------------------------------------------------------

def test_exit_skips_duplicate_when_placed_sell_order_exists(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_exit_positions

    v2_mocks.get_positions.return_value = [{"ticker": "I50Dl_EQ", "quantity": 10.0}]
    frame = _bearish_frame()
    _mock_price_data(mocker, {"VOO": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ",
                        stop_price=1.0, days_ago=5, is_etf=True)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'SELL', 'PLACED', :now, :now)
        """), {"now": datetime.utcnow().isoformat()})

    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# max_open_positions / max_total_deployed gates
# ---------------------------------------------------------------------------

def test_max_open_positions_blocks_new_entries(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_positions.return_value = [
        {"ticker": "X_US_EQ", "quantity": 1.0}, {"ticker": "Y_US_EQ", "quantity": 1.0},
    ]
    _mock_price_data(mocker, {"VOO": _bullish_frame()})

    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    cfg["trading212_invest"]["max_open_positions"] = 2
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_max_total_deployed_blocks_new_entries(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 1_000.0},
        "investments": {"currentValue": 6_000.0},  # 60% deployed > 50% cap
    }
    _mock_price_data(mocker, {"VOO": _bullish_frame()})

    cfg = _config(mapping={"VOO": {"ticker": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Per-iteration cap enforcement — the 2026-07-31 first-cycle overshoot fix.
# The pre-loop checks above only ever gate entry into the loop from the
# account's state *before* the run started; a cold start (0 positions, 0%
# deployed) must still stop mid-run once either cap is crossed by orders
# placed earlier in the *same* run, not walk through every qualifier.
# ---------------------------------------------------------------------------

def _n_ticker_mapping(n: int) -> dict:
    return {
        f"T{i}": {"ticker": f"T{i}_EQ", "currency": "USD", "is_etf": True}
        for i in range(n)
    }


def test_max_open_positions_stops_mid_run_from_cold_start(db_engine, v2_mocks, mocker):
    """5 simultaneously-qualifying tickers, cold start (0 open positions),
    max_open_positions=2 -- must place exactly 2 orders, not 5."""
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_positions.return_value = []  # cold start
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping=_n_ticker_mapping(5))
    cfg["trading212_invest"]["max_open_positions"] = 2
    cfg["trading212_invest"]["max_total_deployed"] = 1.0  # deployed cap not the limiting factor here
    maybe_place_orders(cfg, run_type="full")

    assert v2_mocks.place_market_order.call_count == 2


def test_max_total_deployed_stops_mid_run_from_cold_start(db_engine, v2_mocks, mocker):
    """5 simultaneously-qualifying tickers, cold start (0% deployed),
    max_total_deployed=0.15 with each order sizing to a fixed £500 (10 qty x
    £50 mocked price) against a £10,000 account (5% each) -- must stop after
    the 3rd order (15% reached), not place all 5."""
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_positions.return_value = []
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping=_n_ticker_mapping(5))
    cfg["trading212_invest"]["max_open_positions"] = 999  # position count not the limiting factor here
    cfg["trading212_invest"]["max_total_deployed"] = 0.15
    maybe_place_orders(cfg, run_type="full")

    assert v2_mocks.place_market_order.call_count == 3


def test_uncapped_run_still_places_every_qualifier(db_engine, v2_mocks, mocker):
    """Sanity check the fix doesn't over-suppress: with both caps generous,
    all 5 qualifying tickers from a cold start still get an order."""
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_positions.return_value = []
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping=_n_ticker_mapping(5))
    cfg["trading212_invest"]["max_open_positions"] = 999
    cfg["trading212_invest"]["max_total_deployed"] = 1.0
    maybe_place_orders(cfg, run_type="full")

    assert v2_mocks.place_market_order.call_count == 5


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------

def test_api_error_never_raises(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_account_summary.side_effect = RuntimeError("T212 is down")
    mocker.patch("brokers.trading212._send_t212_error_email")  # avoid real email attempt

    maybe_place_orders(_config(), run_type="full")  # must not raise


def test_exit_api_error_never_raises(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_exit_positions

    v2_mocks.get_positions.side_effect = RuntimeError("T212 is down")
    mocker.patch("brokers.trading212._send_t212_error_email")

    maybe_exit_positions(_config())  # must not raise


# ---------------------------------------------------------------------------
# P&L reconstruction
# ---------------------------------------------------------------------------

def test_pnl_reconstruction_fifo_pairs_buy_and_sell(db_engine, mocker):
    from brokers.trading212 import _reconstruct_pnl

    mocker.patch(
        "brokers.trading212.Trading212Client._fx_rates",
        staticmethod(lambda: (1.27, 1.08)),
    )

    entry_dt = datetime.utcnow() - timedelta(days=10)
    exit_dt = datetime.utcnow()
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, momentum_pct, currency_code, account_type, placed_at, filled_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'BUY', 10, 100.0, 10, 'FILLED', 1, 0.05, 'USD', 'DEMO', :at, :at, :at)
        """), {"at": entry_dt.isoformat()})
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, exit_reason, currency_code, account_type, placed_at, filled_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'SELL', 10, 110.0, 10, 'FILLED', 1, 'SIGNAL_OFF', 'USD', 'DEMO', :at, :at, :at)
        """), {"at": exit_dt.isoformat()})

    _reconstruct_pnl(_config())

    row = db_engine.connect().execute(sa_text(
        "SELECT ticker, pnl_pct, holding_days, exit_reason FROM pnl"
    )).fetchone()
    assert row is not None
    assert row[0] == "VOO"
    assert row[1] == pytest.approx(0.10)
    assert row[3] == "SIGNAL_OFF"


def test_pnl_reconstruction_is_idempotent(db_engine, mocker):
    from brokers.trading212 import _reconstruct_pnl

    mocker.patch(
        "brokers.trading212.Trading212Client._fx_rates",
        staticmethod(lambda: (1.27, 1.08)),
    )

    entry_dt = datetime.utcnow() - timedelta(days=10)
    exit_dt = datetime.utcnow()
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, currency_code, account_type, placed_at, filled_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'BUY', 10, 100.0, 10, 'FILLED', 1, 'USD', 'DEMO', :at, :at, :at)
        """), {"at": entry_dt.isoformat()})
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, currency_code, account_type, placed_at, filled_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'SELL', 10, 110.0, 10, 'FILLED', 1, 'USD', 'DEMO', :at, :at, :at)
        """), {"at": exit_dt.isoformat()})

    _reconstruct_pnl(_config())
    _reconstruct_pnl(_config())  # second call must not duplicate

    count = db_engine.connect().execute(sa_text("SELECT COUNT(*) FROM pnl")).fetchone()[0]
    assert count == 1
