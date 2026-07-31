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
            "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True},
            "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
        }
    return {
        "instruments": mapping,
        "trading212_invest": {
            "enabled": enabled,
            "live": False,
            "max_total_deployed": 0.5,
            "max_open_positions": 20,
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


def test_pause_new_entries_blocks_buy_but_still_exits(db_engine, v2_mocks, mocker):
    """trading212_invest.pause_new_entries is a temporary safety switch (see
    brokers/trading212.py) -- it must block the BUY phase entirely, even on
    a run_type='full' pass, while still running maybe_exit_positions()."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)
    v2_mocks.get_positions.return_value = []  # nothing open -- exit phase is a no-op but must run

    cfg = _config()
    cfg["trading212_invest"]["pause_new_entries"] = True
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()
    v2_mocks.get_positions.assert_called()  # proves maybe_exit_positions() ran


# ---------------------------------------------------------------------------
# Entry gate — sig_on AND is_member AND not entry_blocked, all required
# ---------------------------------------------------------------------------

def test_entry_fires_when_all_three_conditions_hold(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "I50Dl_EQ"
    assert args[1] > 0  # BUY = positive quantity


def test_entry_blocked_when_momentum_negative(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bearish_frame()})
    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_entry_blocked_when_stock_not_index_member(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}})
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_etf_always_eligible_regardless_of_membership_check(db_engine, v2_mocks, mocker):
    """ETFs have no membership concept -- is_member() must never even be
    consulted for the is_etf branch (see _v2_ticker_state's is_member computation)."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    spy = mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
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

    cfg = _config(mapping={ticker: {"t212_instrument_id": t212_ticker, "currency": "USD", "is_etf": is_etf}})
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
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
        f"T{i}": {"t212_instrument_id": f"T{i}_EQ", "currency": "USD", "is_etf": True}
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


# ---------------------------------------------------------------------------
# get_instrument_price_gbp() -- 2026-07-31 pricing fix
#
# T212's public API has no endpoint that returns a live quote for an
# instrument that isn't currently held (confirmed against T212's own docs --
# see brokers/trading212.py:get_instrument_price_gbp()'s docstring). The old
# "primary path" (reading currentPrice from get_instruments()) never worked
# for any instrument; these tests cover the real primary path (a live
# position's currentPrice), the persisted last-known-price cache that
# survives a full exit, and the is_etf-gated fallback/refusal behaviour.
# ---------------------------------------------------------------------------

def test_price_uses_live_position_when_held(db_engine, monkeypatch, mocker):
    from brokers.trading212 import Trading212Client

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(
        Trading212Client, "get_positions",
        lambda self: [{"ticker": "I50Dl_EQ", "currentPrice": 6.26, "quantity": 4.0}],
    )
    mocker.patch.object(
        Trading212Client, "get_instruments",
        lambda self: [{"ticker": "I50Dl_EQ", "currencyCode": "GBP"}],
    )

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("I50Dl_EQ", research_ticker="VOO", is_etf=True)

    assert price_gbp == pytest.approx(6.26)


def test_price_refuses_unheld_etf_with_no_cache(db_engine, monkeypatch, mocker):
    """An ETF/UCITS wrapper never held and never cached must refuse (0.0) --
    the research-ticker fallback is unsafe for wrapper instruments (arbitrary
    NAV-per-share convention, confirmed via XLF's real mis-sizing)."""
    from brokers.trading212 import Trading212Client

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(Trading212Client, "get_positions", lambda self: [])
    mocker.patch.object(Trading212Client, "get_instruments", lambda self: [])

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("SXLFl_EQ", research_ticker="XLF", is_etf=True)

    assert price_gbp == 0.0


def test_price_falls_back_to_research_ticker_for_unheld_stock(db_engine, monkeypatch, mocker):
    """A direct single-stock mapping (is_etf=False) is safe to size off the
    research ticker's own close -- same underlying share, same convention."""
    from brokers.trading212 import Trading212Client
    from sqlalchemy import text as sa_text

    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO price_data (ticker, date, open, high, low, close, volume)
            VALUES ('AAPL', :d, 200.0, 200.0, 200.0, 200.0, 1000000)
        """), {"d": datetime.utcnow()})

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.25, 1.08)))
    mocker.patch.object(Trading212Client, "get_positions", lambda self: [])
    mocker.patch.object(Trading212Client, "get_instruments", lambda self: [])

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("AAPL_US_EQ", research_ticker="AAPL", is_etf=False)

    assert price_gbp == pytest.approx(200.0 / 1.25)


def test_price_uses_cache_for_unheld_etf_previously_traded(db_engine, monkeypatch, mocker):
    """An ETF fully exited (no longer in get_positions()) but priced at some
    point in the past must use that cached price rather than refusing --
    otherwise every ETF sleeve position would become permanently unpriceable
    for a future re-entry after its first exit."""
    from brokers.trading212 import Trading212Client, _cache_last_known_price_gbp

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    _cache_last_known_price_gbp("I50Dl_EQ", 6.30)

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(Trading212Client, "get_positions", lambda self: [])
    mocker.patch.object(Trading212Client, "get_instruments", lambda self: [])

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("I50Dl_EQ", research_ticker="VOO", is_etf=True)

    assert price_gbp == pytest.approx(6.30)


def test_price_cache_ignored_when_stale(db_engine, monkeypatch, mocker):
    from brokers.trading212 import Trading212Client, _LAST_PRICE_CACHE_MAX_AGE_DAYS
    from data.database import set_metadata
    import json

    stale_ts = (datetime.utcnow() - timedelta(days=_LAST_PRICE_CACHE_MAX_AGE_DAYS + 1)).isoformat()
    set_metadata(
        "last_known_price_gbp:I50Dl_EQ",
        json.dumps({"price_gbp": 6.30, "as_of": stale_ts}),
    )

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(Trading212Client, "get_positions", lambda self: [])
    mocker.patch.object(Trading212Client, "get_instruments", lambda self: [])

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("I50Dl_EQ", research_ticker="VOO", is_etf=True)

    assert price_gbp == 0.0  # is_etf=True, stale cache must not be used


def test_live_position_price_refreshes_the_cache(db_engine, monkeypatch, mocker):
    from brokers.trading212 import Trading212Client, _get_cached_price_gbp

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(
        Trading212Client, "get_positions",
        lambda self: [{"ticker": "I50Dl_EQ", "currentPrice": 6.40, "quantity": 4.0}],
    )
    mocker.patch.object(
        Trading212Client, "get_instruments",
        lambda self: [{"ticker": "I50Dl_EQ", "currencyCode": "GBP"}],
    )

    client = Trading212Client(live=False)
    client.get_instrument_price_gbp("I50Dl_EQ", research_ticker="VOO", is_etf=True)

    assert _get_cached_price_gbp("I50Dl_EQ") == pytest.approx(6.40)


def test_locked_out_etf_with_positive_signal_is_a_clean_logged_skip_not_a_silent_failure(
    db_engine, v2_mocks, mocker, caplog,
):
    """A locked-out ETF (no held position, no cached price) whose momentum
    signal is genuinely positive must: (1) not place an order, (2) not raise
    or halt the run -- the next ticker in dict order must still be
    processed, and (3) log a specific, actionable warning naming the ticker
    and pointing at the seed mechanism, not a generic message a reader would
    have to correlate with a signal state themselves."""
    import logging
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"XLK": _bullish_frame(), "VOO": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    # XLK: entry-eligible but unpriceable (locked out). VOO: entry-eligible
    # and priceable -- proves the loop keeps going past XLK, in dict order.
    def _qty_side_effect(t212_ticker, *args, **kwargs):
        return 0.0 if t212_ticker == "SXLKl_EQ" else 10.0
    v2_mocks.get_quantity_for_gbp.side_effect = _qty_side_effect

    cfg = _config(mapping={
        "XLK": {"t212_instrument_id": "SXLKl_EQ", "currency": "USD", "is_etf": True},
        "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True},
    })

    with caplog.at_level(logging.WARNING, logger="brokers.trading212"):
        maybe_place_orders(cfg, run_type="full")

    # (1) No order for the locked-out ticker; (2) VOO still went through --
    # the run didn't halt or silently drop everything after XLK.
    placed_tickers = [c.args[0] for c in v2_mocks.place_market_order.call_args_list]
    assert "SXLKl_EQ" not in placed_tickers
    assert "I50Dl_EQ" in placed_tickers

    # (3) The skip is self-documenting: names the ticker, confirms the
    # signal was positive/eligible (not just "no data"), and points at the
    # seed mechanism as the fix.
    warning_text = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
    assert "XLK" in warning_text and "SXLKl_EQ" in warning_text
    assert "momentum signal is positive" in warning_text
    assert "manual price seed" in warning_text
    assert "Seed a manual price" in warning_text


# ---------------------------------------------------------------------------
# Manual price seed mechanism -- 2026-07-31
#
# get_instrument_price_gbp()'s refusal to price an unheld ETF with no cache
# entry (see test_price_refuses_unheld_etf_with_no_cache above) is correct,
# but has a side effect: a brand-new ETF mapping has NO automated path to a
# first price at all, ever -- T212's API has no quote endpoint for an
# instrument not currently held. seed_instrument_price_gbp() is the one
# sanctioned way to bootstrap that cache, from an operator-supplied price
# that must have been read directly off T212's own displayed price -- never
# from get_price_df() or any other automated proxy (that proxy is exactly
# what caused the XLF mis-sizing incident this whole 3-tier system exists to
# prevent).
# ---------------------------------------------------------------------------

def test_seed_instrument_price_gbp_populates_the_cache(db_engine, mocker):
    from brokers.trading212 import Trading212Client, seed_instrument_price_gbp, _get_cached_price_gbp

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))

    price_gbp = seed_instrument_price_gbp("SXLKl_EQ", 45.00, "USD")

    assert price_gbp == pytest.approx(45.00 / 1.27)
    assert _get_cached_price_gbp("SXLKl_EQ") == pytest.approx(45.00 / 1.27)


def test_seed_instrument_price_gbp_records_manual_seed_source(db_engine, mocker):
    from brokers.trading212 import Trading212Client, seed_instrument_price_gbp, get_cached_price_entry

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))

    seed_instrument_price_gbp("SXLKl_EQ", 45.00, "USD")
    entry = get_cached_price_entry("SXLKl_EQ")

    assert entry is not None
    assert entry["source"] == "manual_seed"
    assert entry["price_gbp"] == pytest.approx(45.00 / 1.27)


def test_seed_instrument_price_gbp_handles_gbp_and_gbx_directly(db_engine, mocker):
    from brokers.trading212 import Trading212Client, seed_instrument_price_gbp

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))

    assert seed_instrument_price_gbp("FOO_EQ", 6.30, "GBP") == pytest.approx(6.30)
    assert seed_instrument_price_gbp("BAR_EQ", 630.0, "GBX") == pytest.approx(6.30)


def test_seed_instrument_price_gbp_rejects_non_positive_price(db_engine, mocker):
    from brokers.trading212 import Trading212Client, seed_instrument_price_gbp

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))

    with pytest.raises(ValueError):
        seed_instrument_price_gbp("SXLKl_EQ", 0.0, "USD")
    with pytest.raises(ValueError):
        seed_instrument_price_gbp("SXLKl_EQ", -5.0, "USD")


def test_seed_instrument_price_gbp_rejects_unconvertible_currency(db_engine, mocker):
    from brokers.trading212 import Trading212Client, seed_instrument_price_gbp

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))

    with pytest.raises(ValueError):
        seed_instrument_price_gbp("SXLKl_EQ", 45.00, "CHF")  # unrecognised by _to_gbp()


def test_seeded_price_prices_an_unheld_etf_via_the_normal_cache_tier(db_engine, monkeypatch, mocker):
    """A seeded ETF must become priceable through get_instrument_price_gbp()'s
    existing cache tier -- no special-casing anywhere else in the pricing
    chain for a manually-seeded value vs. one cached from a real fill."""
    from brokers.trading212 import Trading212Client, seed_instrument_price_gbp

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(Trading212Client, "get_positions", lambda self: [])
    mocker.patch.object(Trading212Client, "get_instruments", lambda self: [])

    seed_instrument_price_gbp("SXLKl_EQ", 45.00, "USD")

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("SXLKl_EQ", research_ticker="XLK", is_etf=True)

    assert price_gbp == pytest.approx(45.00 / 1.27)


def test_held_position_live_quote_supersedes_a_manual_seed(db_engine, monkeypatch, mocker):
    """Once a seeded ETF is actually bought and held, the next live-position
    read must overwrite the manual-seed cache entry -- a seed is a one-time
    bootstrap, never a standing override."""
    from brokers.trading212 import (
        Trading212Client, seed_instrument_price_gbp, get_cached_price_entry,
    )

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))

    seed_instrument_price_gbp("SXLKl_EQ", 45.00, "USD")
    assert get_cached_price_entry("SXLKl_EQ")["source"] == "manual_seed"

    # Now the instrument is actually held, with a genuinely different price.
    mocker.patch.object(
        Trading212Client, "get_positions",
        lambda self: [{"ticker": "SXLKl_EQ", "currentPrice": 47.50, "quantity": 10.0}],
    )
    mocker.patch.object(
        Trading212Client, "get_instruments",
        lambda self: [{"ticker": "SXLKl_EQ", "currencyCode": "USD"}],
    )

    client = Trading212Client(live=False)
    price_gbp = client.get_instrument_price_gbp("SXLKl_EQ", research_ticker="XLK", is_etf=True)

    assert price_gbp == pytest.approx(47.50 / 1.27)
    refreshed = get_cached_price_entry("SXLKl_EQ")
    assert refreshed["price_gbp"] == pytest.approx(47.50 / 1.27)
    assert refreshed.get("source") != "manual_seed"


# ---------------------------------------------------------------------------
# ETF pricing-path audit -- 2026-07-31
# ---------------------------------------------------------------------------

def _etf_mapping():
    return {
        "instruments": {
            "XLK": {"t212_instrument_id": "SXLKl_EQ", "currency": "USD", "is_etf": True},
            "XLF": {"t212_instrument_id": "SXLFl_EQ", "currency": "USD", "is_etf": True},
            "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "GBP", "is_etf": True},
            "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
        },
        "trading212_invest": {
            "live": False,
        },
    }


def test_audit_etf_pricing_paths_classifies_held_cached_and_locked_out(db_engine, monkeypatch, mocker):
    from brokers.trading212 import Trading212Client, audit_etf_pricing_paths, seed_instrument_price_gbp

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(
        Trading212Client, "get_positions",
        lambda self: [{"ticker": "I50Dl_EQ", "currentPrice": 6.26, "quantity": 4.0}],
    )
    seed_instrument_price_gbp("SXLFl_EQ", 40.0, "USD")

    results = audit_etf_pricing_paths(_etf_mapping(), client=Trading212Client(live=False))
    by_ticker = {r["ticker"]: r for r in results}

    assert set(by_ticker.keys()) == {"XLK", "XLF", "VOO"}  # AAPL excluded (not an ETF)
    assert by_ticker["VOO"]["held"] is True
    assert by_ticker["VOO"]["locked_out"] is False
    assert by_ticker["XLF"]["held"] is False
    assert by_ticker["XLF"]["cached"] is True
    assert by_ticker["XLF"]["cache_source"] == "manual_seed"
    assert by_ticker["XLF"]["locked_out"] is False
    assert by_ticker["XLK"]["held"] is False
    assert by_ticker["XLK"]["cached"] is False
    assert by_ticker["XLK"]["locked_out"] is True


def test_audit_etf_pricing_paths_handles_live_position_fetch_failure(db_engine, monkeypatch, mocker):
    from brokers.trading212 import Trading212Client, audit_etf_pricing_paths

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    mocker.patch.object(
        Trading212Client, "get_positions",
        side_effect=RuntimeError("boom"),
    )

    results = audit_etf_pricing_paths(_etf_mapping(), client=Trading212Client(live=False))
    # Must not raise -- everything falls through to "not held" and is
    # correctly reported as locked out (no cache in this test either).
    assert all(r["held"] is False for r in results)
    assert all(r["locked_out"] is True for r in results)


# ---------------------------------------------------------------------------
# Per-ticker/per-position exception isolation -- 2026-07-31
#
# Confirmed live: a real cycle placed 13 BUY orders through position 19/73 in
# instrument_mapping, then produced zero orders for the remaining 54 tickers
# with no trace in any log -- consistent with an unhandled exception on one
# ticker silently killing every ticker after it in dict order. Neither loop
# had per-item exception isolation around its sizing/state computation.
# ---------------------------------------------------------------------------

def test_buy_loop_one_ticker_exception_does_not_stop_the_rest(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    good_frame = _bullish_frame()

    def _fake_get_price_df(ticker, days=8000):
        if ticker == "VOO":
            raise RuntimeError("simulated transient failure")
        return good_frame.copy()

    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={
        "VOO":  {"t212_instrument_id": "I50Dl_EQ",  "currency": "USD", "is_etf": True},
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
    })
    maybe_place_orders(cfg, run_type="full")

    # VOO's exception must not have stopped AAPL from still being ordered.
    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "AAPL_US_EQ"


def test_exit_loop_one_position_exception_does_not_stop_the_rest(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_exit_positions

    bearish = _bearish_frame()  # signal off -> both should exit

    v2_mocks.get_positions.return_value = [
        {"ticker": "I50Dl_EQ", "quantity": 4.0},
        {"ticker": "AAPL_US_EQ", "quantity": 2.0},
    ]

    def _fake_get_price_df(ticker, days=8000):
        if ticker == "VOO":
            raise RuntimeError("simulated transient failure")
        return bearish.copy()

    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)

    cfg = _config(mapping={
        "VOO":  {"t212_instrument_id": "I50Dl_EQ",  "currency": "USD", "is_etf": True},
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
    })
    maybe_exit_positions(cfg)

    # VOO's exception must not have stopped AAPL from still being exited.
    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "AAPL_US_EQ"


# ---------------------------------------------------------------------------
# _send_t212_error_email — enabled gate + cooldown -- 2026-07-31
#
# notifications.email.enabled and alert_cooldown_hours were both fully
# configured and documented but never actually read anywhere -- found
# during the config-consolidation task. _send_t212_error_email() is the
# single function all four call sites (BUY-loop setup error, failed BUY,
# EXIT-loop setup error, failed SELL) funnel through, so it's gated once,
# here, rather than at each of the four sites individually.
# ---------------------------------------------------------------------------

def _email_config(enabled=True, cooldown_hours=8):
    return {
        "notifications": {
            "email": {
                "enabled": enabled,
                "alert_cooldown_hours": cooldown_hours,
                "smtp_host": "smtp.example.com", "smtp_port": 587,
                "smtp_user": "user@example.com", "smtp_password": "secret",
                "from_address": "hello@example.com",
                "to_addresses": ["alerts@example.com"],
            },
        },
    }


def test_t212_error_email_disabled_skips_cleanly_without_error(db_engine, mocker):
    """enabled=false must be a clean, silent skip -- no exception, no SMTP
    connection attempt -- not just an eventually-swallowed failure."""
    from brokers.trading212 import _send_t212_error_email
    smtp_cls = mocker.patch("output.briefing.smtplib.SMTP")

    _send_t212_error_email("boom", _email_config(enabled=False))

    smtp_cls.assert_not_called()


def test_t212_error_email_sends_on_first_occurrence(db_engine, mocker):
    """No prior send recorded -- must send immediately, cooldown or not."""
    from brokers.trading212 import _send_t212_error_email
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    _send_t212_error_email("boom", _email_config())

    mock_server.sendmail.assert_called_once()


def test_t212_error_email_second_error_within_cooldown_is_skipped(db_engine, mocker):
    from brokers.trading212 import _send_t212_error_email
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    cfg = _email_config(cooldown_hours=8)
    _send_t212_error_email("first error", cfg)
    mock_server.sendmail.reset_mock()

    _send_t212_error_email("second error, 1h later", cfg)

    mock_server.sendmail.assert_not_called()


def test_t212_error_email_sends_again_once_cooldown_has_elapsed(db_engine, mocker):
    from data.database import set_metadata
    from brokers.trading212 import _send_t212_error_email, _T212_ERROR_EMAIL_LAST_SENT_KEY

    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    stale_last_sent = (datetime.utcnow() - timedelta(hours=9)).isoformat()
    set_metadata(_T212_ERROR_EMAIL_LAST_SENT_KEY, stale_last_sent)

    _send_t212_error_email("error after cooldown window", _email_config(cooldown_hours=8))

    mock_server.sendmail.assert_called_once()


def test_t212_error_email_disabled_does_not_consume_the_cooldown(db_engine, mocker):
    """A disabled config must never touch the cooldown timestamp -- a
    genuine error while disabled must not suppress the first real alert
    once notifications are turned back on."""
    from data.database import get_metadata_str
    from brokers.trading212 import _send_t212_error_email, _T212_ERROR_EMAIL_LAST_SENT_KEY
    mocker.patch("output.briefing.smtplib.SMTP")

    _send_t212_error_email("error while disabled", _email_config(enabled=False))

    assert get_metadata_str(_T212_ERROR_EMAIL_LAST_SENT_KEY) is None
