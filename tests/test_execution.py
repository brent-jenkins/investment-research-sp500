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
            # 5% of v2_mocks' mocked totalValue (£10,000) = £500 -- deliberately
            # matches the old fixed max_position_gbp: 500 this fixture used
            # before per_trade_pct replaced it (2026-08-01), so every existing
            # test's implicit "£500 ceiling" expectation still holds unchanged.
            "per_trade_pct": 0.05,
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


def _pending_rows(engine, status=None) -> list:
    """Read pending_orders rows for assertions -- exchange-aware submission
    split (2026-07-31, see CLAUDE.md)."""
    with engine.connect() as conn:
        if status is None:
            rows = conn.execute(sa_text("SELECT * FROM pending_orders")).mappings().all()
        else:
            rows = conn.execute(
                sa_text("SELECT * FROM pending_orders WHERE status = :s"), {"s": status},
            ).mappings().all()
    return [dict(r) for r in rows]


def _seed_pending_order(engine, *, ticker, t212_ticker, is_etf, decided_price=100.0,
                         position_gbp=500.0, status="PENDING", currency="USD", momentum_pct=0.05):
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status, created_at)
            VALUES (:ticker, :t212_ticker, :is_etf, :currency, :now, :decided_price,
                    :position_gbp, :momentum_pct, :stop_price, 0.02, :status, :now)
        """), {
            "ticker": ticker, "t212_ticker": t212_ticker, "is_etf": is_etf,
            "currency": currency, "now": datetime.utcnow().isoformat(),
            "decided_price": decided_price, "position_gbp": position_gbp,
            "stop_price": decided_price * 0.9, "status": status, "momentum_pct": momentum_pct,
        })


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
    """maybe_place_orders() no longer submits directly -- a qualifying entry
    is QUEUED into pending_orders (exchange-aware submission split,
    2026-07-31, see CLAUDE.md); a separate, exchange-timed job submits it
    later. Decide-phase itself must never call place_market_order()."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="PENDING")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "VOO"
    assert rows[0]["t212_ticker"] == "I50Dl_EQ"
    assert rows[0]["position_gbp"] > 0
    assert rows[0]["decided_price"] > 0


def test_entry_sizing_ceiling_scales_with_live_account_value(db_engine, v2_mocks, mocker):
    """position_gbp's ceiling is per_trade_pct * the account's CURRENT
    total value (client.get_account_summary()['totalValue']), not a fixed
    GBP figure (2026-08-01) -- confirmed via the deterministic <60-day-
    history fallback formula (round(dynamic_max_position_gbp * 0.5, 2)),
    which isolates the ceiling itself from any real VaR computation.
    var_lookback_days is set well below _MIN_VAR_RETURNS (60) so even
    _bullish_frame()'s 400 days of history yields too few usable log
    returns for the ticker's slice, forcing the fallback path
    deterministically. Doubling total_val must exactly double the
    resulting queued position_gbp -- the old flat max_position_gbp cap
    could never have done this, by construction."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    cfg["trading212_invest"]["var_lookback_days"] = 30
    cfg["trading212_invest"]["per_trade_pct"] = 0.05

    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        # generous free cash so the unrelated `free_cash * 0.25` cap never
        # binds and this test isolates the per_trade_pct ceiling alone.
        "cash": {"availableToTrade": 50_000.0},
        "investments": {"currentValue": 0.0},
    }
    maybe_place_orders(cfg, run_type="full")
    rows_small = _pending_rows(db_engine, status="PENDING")
    assert len(rows_small) == 1
    position_small = rows_small[0]["position_gbp"]
    assert position_small == pytest.approx(10_000.0 * 0.05 * 0.5)  # £250

    with db_engine.begin() as conn:
        conn.execute(sa_text("DELETE FROM pending_orders"))

    v2_mocks.get_account_summary.return_value = {
        "totalValue": 100_000.0,
        "cash": {"availableToTrade": 500_000.0},
        "investments": {"currentValue": 0.0},
    }
    maybe_place_orders(cfg, run_type="full")
    rows_large = _pending_rows(db_engine, status="PENDING")
    assert len(rows_large) == 1
    position_large = rows_large[0]["position_gbp"]
    assert position_large == pytest.approx(100_000.0 * 0.05 * 0.5)  # £5,000

    assert position_large == pytest.approx(position_small * 10)


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
    consulted for the is_etf branch (see _v2_ticker_state's is_member computation).
    Queues a decision (does not place directly) -- see the decide/submit split."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    spy = mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1
    spy.assert_not_called()


def test_decide_phase_skips_ticker_with_existing_unresolved_pending_order(db_engine, v2_mocks, mocker):
    """Dedup guard -- a ticker whose signal stays on across multiple EOD runs
    while submission is paused/behind must not accumulate duplicate PENDING
    rows."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)
    _seed_pending_order(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", is_etf=True)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}})
    maybe_place_orders(cfg, run_type="full")

    assert len(_pending_rows(db_engine, status="PENDING")) == 1  # still just the seeded one


def test_decide_phase_queues_regardless_of_deployed_cap(db_engine, v2_mocks, mocker):
    """Decide-phase no longer enforces max_total_deployed -- that's a
    submission-time concern now (account state can genuinely differ by the
    time submission runs). A cold-start run with 5 qualifiers and an
    already-exceeded deployment cap must still queue all 5 -- nothing is
    lost, just deferred. (max_open_positions was retired entirely
    2026-08-01 -- position count is now emergent from per_trade_pct/
    max_total_deployed together, so there is no separate cap left to test
    here -- see CLAUDE.md's "Live execution switched to the compounding
    two-parameter scheme" section.)"""
    from brokers.trading212 import maybe_place_orders

    v2_mocks.get_positions.return_value = [
        {"ticker": "X_US_EQ", "quantity": 1.0}, {"ticker": "Y_US_EQ", "quantity": 1.0},
    ]
    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 1_000.0},
        "investments": {"currentValue": 6_000.0},  # 60% deployed
    }
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping=_n_ticker_mapping(5))
    cfg["trading212_invest"]["max_total_deployed"] = 0.5  # already exceeded (60%)
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 5


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


def _n_ticker_mapping(n: int) -> dict:
    return {
        f"T{i}": {"t212_instrument_id": f"T{i}_EQ", "currency": "USD", "is_etf": True}
        for i in range(n)
    }


# ---------------------------------------------------------------------------
# Exchange-aware order submission (2026-07-31, see CLAUDE.md) --
# _submit_pending_orders() / submit_pending_etf_orders() /
# submit_pending_stock_orders(). max_total_deployed / the price-sanity
# check all now live here, applied against the account's live state *at
# submission time* rather than at EOD decide time. No position-count cap
# (max_open_positions retired 2026-08-01 -- see CLAUDE.md's "Live execution
# switched to the compounding two-parameter scheme" section).
# ---------------------------------------------------------------------------

def _submission_config(max_deployed=1.0, threshold=0.03,
                        etf_enabled=None, stock_enabled=None) -> dict:
    cfg = _config()
    cfg["trading212_invest"]["max_total_deployed"] = max_deployed
    cfg["trading212_invest"]["price_sanity_threshold_pct"] = threshold
    if etf_enabled is not None:
        cfg["trading212_invest"]["etf_submission_enabled"] = etf_enabled
    if stock_enabled is not None:
        cfg["trading212_invest"]["stock_submission_enabled"] = stock_enabled
    return cfg


def test_etf_submission_disabled_by_default_leaves_rows_pending(db_engine, v2_mocks, mocker):
    """etf_submission_enabled defaults False when the key is absent -- ETF
    submission ships paused until explicitly enabled and verified (see
    CLAUDE.md)."""
    from brokers.trading212 import submit_pending_etf_orders

    _seed_pending_order(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", is_etf=True)
    cfg = _config()  # no etf_submission_enabled key at all

    n = submit_pending_etf_orders(cfg)

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1


def test_stock_submission_enabled_by_default(db_engine, v2_mocks, mocker):
    """stock_submission_enabled defaults True when the key is absent --
    stocks continue submitting as before this change."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=last_close)
    _mock_price_data(mocker, {"AAPL": frame})
    cfg = _config()  # no stock_submission_enabled key at all

    n = submit_pending_stock_orders(cfg)

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()


def test_submission_only_processes_its_own_asset_class(db_engine, v2_mocks, mocker):
    """submit_pending_etf_orders() must only ever act on is_etf=True rows;
    submit_pending_stock_orders() only on is_etf=False rows."""
    from brokers.trading212 import submit_pending_etf_orders

    etf_frame = _bullish_frame()
    stock_frame = _bullish_frame()
    etf_close = float(etf_frame["close"].iloc[-1])
    _seed_pending_order(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", is_etf=True, decided_price=etf_close)
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=float(stock_frame["close"].iloc[-1]))
    _mock_price_data(mocker, {"VOO": etf_frame, "AAPL": stock_frame})

    cfg = _submission_config(etf_enabled=True)
    n = submit_pending_etf_orders(cfg)

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "I50Dl_EQ"  # only the ETF row submitted

    remaining_pending = {r["ticker"] for r in _pending_rows(db_engine, status="PENDING")}
    assert remaining_pending == {"AAPL"}  # stock row untouched by the ETF submission job


# ---------------------------------------------------------------------------
# Submission-time price-sanity check -- fail loud, don't guess (same
# principle as the 2026-07-31 pricing-bug fix). Compares the price recorded
# at decide time against the latest available price at submission time.
# ---------------------------------------------------------------------------

def test_sanity_check_skips_submission_when_price_moved_beyond_threshold(db_engine, v2_mocks, mocker):
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    decided_price = last_close / 1.10  # decided price is 10% below "current" -- well past the 3% threshold
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="SKIPPED_PRICE_MOVED")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["resolution_note"] is not None and "threshold" in rows[0]["resolution_note"]


def test_sanity_check_allows_submission_when_price_within_threshold(db_engine, v2_mocks, mocker):
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    decided_price = last_close / 1.01  # ~1% move -- comfortably inside the 3% threshold
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()
    assert len(_pending_rows(db_engine, status="SUBMITTED")) == 1


# ---------------------------------------------------------------------------
# max_total_deployed gate, applied at submission time -- and confirmation
# that NO position-count gate exists any more (retired 2026-08-01).
# ---------------------------------------------------------------------------

def _seed_n_pending(engine, n: int, frames: dict) -> None:
    for i in range(n):
        ticker = f"T{i}"
        close = float(frames[ticker]["close"].iloc[-1])
        _seed_pending_order(engine, ticker=ticker, t212_ticker=f"T{i}_EQ", is_etf=True, decided_price=close)


def test_no_position_count_cap_many_open_positions_do_not_block_submission(db_engine, v2_mocks, mocker):
    """max_open_positions was retired 2026-08-01 -- position count is now an
    emergent consequence of per_trade_pct/max_total_deployed together, not a
    separately-enforced rule. 50 already-open positions (far more than the
    old max_open_positions=20 default would ever have allowed) must NOT
    block 5 new pending ETF decisions from submitting, as long as the
    deployment cap itself isn't binding."""
    from brokers.trading212 import submit_pending_etf_orders

    v2_mocks.get_positions.return_value = [{"ticker": f"EXISTING{i}_EQ", "quantity": 1.0} for i in range(50)]
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    _seed_n_pending(db_engine, 5, frames)

    n = submit_pending_etf_orders(_submission_config(max_deployed=1.0, etf_enabled=True))

    assert n == 5
    assert len(_pending_rows(db_engine, status="PENDING")) == 0


def test_max_total_deployed_stops_submission_partway(db_engine, v2_mocks, mocker):
    """5 pending ETF decisions, cold start (0% deployed), max_total_deployed
    =0.15 with each order sizing to £500 (10 qty x £50 mocked price) against
    a £10,000 account (5% each) -- 3 fit, 2 do not. Skip-and-continue
    (2026-08-01) means the loop still walks every candidate rather than
    stopping the run the instant the cap binds -- the 2 that don't fit are
    marked REJECTED_CAPACITY, not left PENDING (see CLAUDE.md's "Trade
    prioritization and capacity-allocation engine" section)."""
    from brokers.trading212 import submit_pending_etf_orders

    v2_mocks.get_positions.return_value = []
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    _seed_n_pending(db_engine, 5, frames)

    n = submit_pending_etf_orders(_submission_config(max_deployed=0.15, etf_enabled=True))

    assert n == 3
    assert len(_pending_rows(db_engine, status="PENDING")) == 0
    rejected = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rejected) == 2
    for row in rejected:
        assert row["resolution_note"] is not None and "deploy" in row["resolution_note"].lower()


def test_uncapped_submission_still_places_every_pending_row(db_engine, v2_mocks, mocker):
    """Sanity check the deployment cap doesn't over-suppress: with it
    generous, all 5 pending ETF decisions still submit."""
    from brokers.trading212 import submit_pending_etf_orders

    v2_mocks.get_positions.return_value = []
    frames = {f"T{i}": _bullish_frame() for i in range(5)}
    _mock_price_data(mocker, frames)
    _seed_n_pending(db_engine, 5, frames)

    n = submit_pending_etf_orders(_submission_config(max_deployed=1.0, etf_enabled=True))

    assert n == 5
    assert len(_pending_rows(db_engine, status="PENDING")) == 0


def test_organic_over_deployment_rejects_new_entries_cleanly_no_negative_capacity_bug(db_engine, v2_mocks, mocker):
    """Deployment can drift ABOVE max_total_deployed organically (held
    positions appreciating in value -- investments.currentValue is a live
    mark-to-market read, not cost-basis) without any new order ever having
    caused it. This test starts the mocked account already at 70% invested
    against a 50% cap and confirms the capacity check
    (deployed_after_pct = (invested + position_gbp) / total_val, compared
    directly against max_deployed_pct) rejects the new candidate cleanly --
    no exception, no negative-number arithmetic, no accidental admission --
    because the check is expressed as "would-be deployed % after adding
    this candidate", never as a "remaining capacity = cap - invested"
    subtraction that could go negative and be misread as falsy/zero."""
    from brokers.trading212 import submit_pending_etf_orders

    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 3_000.0},
        "investments": {"currentValue": 7_000.0},  # 70% already deployed, organically
    }
    v2_mocks.get_positions.return_value = []  # the 70% is in tickers NOT in this pending batch
    frames = {"T0": _bullish_frame()}
    _mock_price_data(mocker, frames)
    _seed_n_pending(db_engine, 1, frames)  # position_gbp=500 (10 qty x £50 mocked price)

    n = submit_pending_etf_orders(_submission_config(max_deployed=0.5, etf_enabled=True))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    rejected = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rejected) == 1
    note = rejected[0]["resolution_note"]
    assert "deploy" in note.lower()
    assert "75.0%" in note  # (7000 + 500) / 10000 -- correctly computed, not negative/garbled


def test_extreme_over_deployment_still_rejects_cleanly(db_engine, v2_mocks, mocker):
    """Pathological case: invested value has run up to 3x total_val (e.g. a
    stale/lagging totalValue read, or an extreme rally) -- the deployed
    fraction is already >>100%. Confirms the same clean-rejection path holds
    at the extreme, not just near the boundary, and that nothing divides by
    zero or produces a negative quantity/position size downstream."""
    from brokers.trading212 import submit_pending_etf_orders

    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 500.0},
        "investments": {"currentValue": 30_000.0},
    }
    v2_mocks.get_positions.return_value = []
    frames = {"T0": _bullish_frame()}
    _mock_price_data(mocker, frames)
    _seed_n_pending(db_engine, 1, frames)

    n = submit_pending_etf_orders(_submission_config(max_deployed=0.5, etf_enabled=True))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="REJECTED_CAPACITY")) == 1


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
    """A locked-out ETF (no held position, no cached price) whose entry
    decision was queued must: (1) not have an order placed for it at
    submission time, (2) not raise or halt the submission run -- the next
    pending row must still be processed, and (3) log a specific, actionable
    warning naming the ticker and pointing at the seed mechanism, not a
    generic message a reader would have to correlate with a signal state
    themselves. (Quantity resolution -- and therefore this lockout -- now
    happens at submission time, not decide time; see the decide/submit
    split.)"""
    import logging
    from brokers.trading212 import submit_pending_etf_orders

    xlk_frame, voo_frame = _bullish_frame(), _bullish_frame()
    _mock_price_data(mocker, {"XLK": xlk_frame, "VOO": voo_frame})
    _seed_pending_order(db_engine, ticker="XLK", t212_ticker="SXLKl_EQ", is_etf=True,
                        decided_price=float(xlk_frame["close"].iloc[-1]))
    _seed_pending_order(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", is_etf=True,
                        decided_price=float(voo_frame["close"].iloc[-1]))

    # XLK: entry-eligible but unpriceable (locked out). VOO: entry-eligible
    # and priceable -- proves the loop keeps going past XLK, in row order.
    def _qty_side_effect(t212_ticker, *args, **kwargs):
        return 0.0 if t212_ticker == "SXLKl_EQ" else 10.0
    v2_mocks.get_quantity_for_gbp.side_effect = _qty_side_effect

    with caplog.at_level(logging.WARNING, logger="brokers.trading212"):
        submit_pending_etf_orders(_submission_config(etf_enabled=True))

    # (1) No order for the locked-out ticker; (2) VOO still went through --
    # the run didn't halt or silently drop everything after XLK.
    placed_tickers = [c.args[0] for c in v2_mocks.place_market_order.call_args_list]
    assert "SXLKl_EQ" not in placed_tickers
    assert "I50Dl_EQ" in placed_tickers

    # XLK's row stays PENDING (retryable once seeded) -- it is not FAILED or
    # silently dropped.
    still_pending = {r["ticker"] for r in _pending_rows(db_engine, status="PENDING")}
    assert "XLK" in still_pending

    # (3) The skip is self-documenting: names the ticker, confirms it's
    # entry-eligible (not just "no data"), and points at the seed mechanism.
    warning_text = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
    assert "XLK" in warning_text and "SXLKl_EQ" in warning_text
    assert "entry-eligible" in warning_text
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
    """Decide-phase exception isolation -- a failure evaluating one ticker
    must not stop the rest of the mapping from being queued. Decide-phase
    never calls place_market_order() at all now (see the decide/submit
    split), so the assertion is against the pending_orders queue instead."""
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

    # VOO's exception must not have stopped AAPL from still being queued.
    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="PENDING")
    queued_tickers = {r["ticker"] for r in rows}
    assert queued_tickers == {"AAPL"}


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


def test_submission_one_row_exception_does_not_stop_the_rest(db_engine, v2_mocks, mocker):
    """A failure evaluating one pending row (e.g. the price-sanity lookup
    blowing up) must not stop the rest of the pending queue from being
    submitted."""
    from brokers.trading212 import submit_pending_etf_orders

    good_frame = _bullish_frame()
    good_close = float(good_frame["close"].iloc[-1])

    def _fake_get_price_df(ticker, days=8000):
        if ticker == "VOO":
            raise RuntimeError("simulated transient failure")
        return good_frame.copy()

    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    _seed_pending_order(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", is_etf=True, decided_price=good_close)
    _seed_pending_order(db_engine, ticker="XLK", t212_ticker="SXLKl_EQ", is_etf=True, decided_price=good_close)

    n = submit_pending_etf_orders(_submission_config(etf_enabled=True))

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "SXLKl_EQ"
    # VOO's row is untouched (still PENDING, not marked FAILED) -- the
    # exception happened before any resolution decision could be made.
    still_pending = {r["ticker"] for r in _pending_rows(db_engine, status="PENDING")}
    assert still_pending == {"VOO"}


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


# ---------------------------------------------------------------------------
# Trade prioritization and capacity-allocation engine (2026-08-01) -- see
# CLAUDE.md's "Trade prioritization and capacity-allocation engine" section
# for the full design writeup and the analysis/backtest.py mirror.
# ---------------------------------------------------------------------------

def test_submission_ranks_by_momentum_descending_not_decided_at(db_engine, v2_mocks, mocker):
    """Priority is signal strength (momentum_pct, descending), not decide
    order -- seed three rows with momentum deliberately out of decided_at
    order and confirm orders are actually placed strongest-first."""
    from brokers.trading212 import submit_pending_etf_orders

    frame = _bullish_frame()
    close = float(frame["close"].iloc[-1])
    _mock_price_data(mocker, {"WEAK": frame, "MEDIUM": frame, "STRONG": frame})
    # Decided in WEAK, STRONG, MEDIUM order -- momentum ranking must
    # override this entirely.
    _seed_pending_order(db_engine, ticker="WEAK", t212_ticker="WEAK_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.02)
    _seed_pending_order(db_engine, ticker="STRONG", t212_ticker="STRONG_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.30)
    _seed_pending_order(db_engine, ticker="MEDIUM", t212_ticker="MEDIUM_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.15)

    n = submit_pending_etf_orders(_submission_config(max_deployed=1.0, etf_enabled=True))

    assert n == 3
    placed_order = [c.args[0] for c in v2_mocks.place_market_order.call_args_list]
    assert placed_order == ["STRONG_EQ", "MEDIUM_EQ", "WEAK_EQ"]


def test_skip_and_continue_admits_smaller_lower_priority_candidate_past_a_rejected_larger_higher_priority_one(
    db_engine, v2_mocks, mocker,
):
    """A large, HIGH-momentum candidate that doesn't fit remaining capacity
    is rejected, and the walk continues to a smaller, LOWER-momentum
    candidate that fits -- proving admission genuinely continues past a
    miss rather than stopping (the old break-on-first-miss behaviour)."""
    from brokers.trading212 import submit_pending_etf_orders

    frame = _bullish_frame()
    close = float(frame["close"].iloc[-1])
    _mock_price_data(mocker, {"BIG_STRONG": frame, "SMALL_WEAK": frame})
    _seed_pending_order(db_engine, ticker="BIG_STRONG", t212_ticker="BIG_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.30, position_gbp=9000.0)
    _seed_pending_order(db_engine, ticker="SMALL_WEAK", t212_ticker="SMALL_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.02, position_gbp=200.0)

    # v2_mocks' account: totalValue=10,000, investments.currentValue=0.0 --
    # BIG_STRONG (£9,000) alone would deploy 90%, comfortably over a 50% cap;
    # SMALL_WEAK (£200) alone deploys only 2%, comfortably under it.
    n = submit_pending_etf_orders(_submission_config(max_deployed=0.5, etf_enabled=True))

    assert n == 1
    placed_tickers = [c.args[0] for c in v2_mocks.place_market_order.call_args_list]
    assert placed_tickers == ["SMALL_EQ"], (
        "the smaller, lower-priority candidate must still be admitted after "
        "the larger, higher-priority one is rejected for not fitting"
    )
    rejected = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rejected) == 1 and rejected[0]["ticker"] == "BIG_STRONG"
    assert len(_pending_rows(db_engine, status="SUBMITTED")) == 1


def test_rejected_capacity_note_has_reconstructable_detail(db_engine, v2_mocks, mocker):
    """A REJECTED_CAPACITY row's resolution_note must state the actual
    numbers (would-be deployed %, cap, position size, already-invested,
    total account value) needed to independently verify the rejection was
    correct after the fact -- not just a bare status flip."""
    from brokers.trading212 import submit_pending_etf_orders

    frame = _bullish_frame()
    close = float(frame["close"].iloc[-1])
    _mock_price_data(mocker, {"TOO_BIG": frame})
    _seed_pending_order(db_engine, ticker="TOO_BIG", t212_ticker="TOOBIG_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.10, position_gbp=6000.0)

    submit_pending_etf_orders(_submission_config(max_deployed=0.5, etf_enabled=True))

    rows = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rows) == 1
    note = rows[0]["resolution_note"]
    assert note is not None
    for expected_fragment in ("60.0%", "50%", "6000.00", "10000.00"):
        assert expected_fragment in note, f"expected {expected_fragment!r} in note: {note!r}"


def test_full_position_size_never_shrunk_to_fit_free_cash(db_engine, v2_mocks, mocker):
    """The full, VaR-calibrated position_gbp is used for sizing the order --
    never resized down to a fraction of free cash (2026-08-01; the old
    `min(position_gbp, free_cash * 0.25)` line this replaces was itself a
    resize-to-fit, the exact anti-pattern this design explicitly rejects).
    A candidate whose full size exceeds free cash is REJECTED_CAPACITY
    instead, never silently shrunk and submitted anyway."""
    from brokers.trading212 import submit_pending_etf_orders

    frame = _bullish_frame()
    close = float(frame["close"].iloc[-1])
    _mock_price_data(mocker, {"AFFORDABLE": frame})
    _seed_pending_order(db_engine, ticker="AFFORDABLE", t212_ticker="AFFORD_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.10, position_gbp=800.0)

    # v2_mocks' free cash is £5,000 (see the v2_mocks fixture) -- £800 fits
    # comfortably and must be sized at its FULL £800, not shrunk to
    # free_cash * 0.25 (£1,250, which would coincidentally still be >= 800
    # here -- the real proof is in the get_quantity_for_gbp call args below,
    # not just that submission succeeded).
    n = submit_pending_etf_orders(_submission_config(max_deployed=1.0, etf_enabled=True))

    assert n == 1
    call = v2_mocks.get_quantity_for_gbp.call_args
    assert call.args[1] == pytest.approx(800.0), (
        f"expected the full £800 position size to be passed to sizing, got {call.args[1]}"
    )


def test_oversized_position_exceeding_free_cash_is_rejected_capacity_not_shrunk(db_engine, v2_mocks, mocker):
    """A candidate whose full position_gbp exceeds available free cash is
    marked REJECTED_CAPACITY and skipped -- never silently shrunk to fit."""
    from brokers.trading212 import submit_pending_etf_orders

    frame = _bullish_frame()
    close = float(frame["close"].iloc[-1])
    _mock_price_data(mocker, {"TOO_MUCH_CASH": frame})
    # v2_mocks' free cash is £5,000 -- this position exceeds it outright.
    _seed_pending_order(db_engine, ticker="TOO_MUCH_CASH", t212_ticker="TOOMUCH_EQ", is_etf=True,
                        decided_price=close, momentum_pct=0.10, position_gbp=8000.0)

    n = submit_pending_etf_orders(_submission_config(max_deployed=1.0, etf_enabled=True))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rows) == 1
    assert "free cash" in rows[0]["resolution_note"].lower()


def test_stock_job_sees_capacity_already_consumed_by_etf_job_via_live_state(db_engine, v2_mocks, mocker):
    """Capacity checks read the real, live, current T212 account state at
    the moment EACH job runs -- never a tracked/projected running total
    (design point 4). This is what naturally resolves the ETF/stock
    cross-job timing question: simulate the ETF job (07:00 UTC) running
    first and consuming capacity, then update the MOCKED account state to
    reflect what T212 would now genuinely report (as a real API would),
    and confirm the stock job (13:30 UTC), reading that fresh state, sees
    the ETF job's consumption -- not a stale figure from before it ran."""
    from brokers.trading212 import submit_pending_etf_orders, submit_pending_stock_orders

    etf_frame = _bullish_frame()
    stock_frame = _bullish_frame()
    etf_close = float(etf_frame["close"].iloc[-1])
    stock_close = float(stock_frame["close"].iloc[-1])
    _mock_price_data(mocker, {"BIG_ETF": etf_frame, "STOCK_CAND": stock_frame})

    _seed_pending_order(db_engine, ticker="BIG_ETF", t212_ticker="BIGETF_EQ", is_etf=True,
                        decided_price=etf_close, momentum_pct=0.10, position_gbp=4000.0)
    _seed_pending_order(db_engine, ticker="STOCK_CAND", t212_ticker="STOCKCAND_US_EQ", is_etf=False,
                        decided_price=stock_close, momentum_pct=0.10, position_gbp=2000.0)

    cfg = _submission_config(max_deployed=0.5, etf_enabled=True, stock_enabled=True)

    # ETF job: cold start, £10,000 total, 0 invested -- £4,000 (40%) fits
    # under the 50% cap.
    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0, "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    }
    n_etf = submit_pending_etf_orders(cfg)
    assert n_etf == 1
    v2_mocks.place_market_order.assert_called_once()

    # Stock job runs later -- the mock now reflects what T212 would
    # genuinely report AFTER the ETF job's fill (invested rose to £4,000).
    # A stale/tracked total from before the ETF job ran would incorrectly
    # still show 0% deployed and admit the stock candidate; the real,
    # freshly-read 40%-deployed state leaves only 10% (£1,000) of headroom
    # under the 50% cap -- not enough for the £2,000 stock candidate.
    v2_mocks.place_market_order.reset_mock()
    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0, "cash": {"availableToTrade": 6_000.0},
        "investments": {"currentValue": 4_000.0},
    }
    n_stock = submit_pending_stock_orders(cfg)

    assert n_stock == 0
    v2_mocks.place_market_order.assert_not_called()
    rejected = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rejected) == 1 and rejected[0]["ticker"] == "STOCK_CAND"
