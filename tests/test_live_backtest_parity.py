"""Structural parity test — Part B of the "one shared decision
implementation" invariant documented in CLAUDE.md's "Live/backtest
decision-logic parity" section.

Unlike the rest of the suite (which tests each entry point in isolation),
every test here drives BOTH real code paths — brokers/trading212.py's live
order-placement functions and analysis/backtest.py's simulate_trades()
engine — against byte-identical synthetic price history, and asserts they
reach the identical entry/exit decision. Only the Trading212 HTTP client and
the DB/price-data read layer are mocked; every decision-making function
itself (analysis.decision, analysis.signals, analysis.crisis,
analysis.universe, analysis.volatility) runs for real on both sides.

This is deliberately NOT the same thing as unit-testing
analysis/decision.py's two functions directly (see tests/test_decision.py
for that) — a shared function only protects against drift if every call
site is actually wired to it correctly. This test protects against the
next failure mode: a call site that quietly stops calling the shared
function, or calls it with the wrong arguments, while the shared function
itself stays perfectly correct in isolation.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text


# ---------------------------------------------------------------------------
# Shared synthetic-frame builders
# ---------------------------------------------------------------------------

def _flat_then_jump_frame(n_days: int = 310, base: float = 100.0, jump: float = 0.0) -> pd.DataFrame:
    """n_days of a perfectly flat price, except the FINAL day which jumps by
    *jump* (e.g. +0.05 -> trailing 12m momentum is exactly 0 (not > 0, so
    signal OFF) on every day except the last, where it is >0 (signal ON) --
    a jump of 0 keeps the signal OFF on every day including the last.
    Deliberately >= 300 rows so brokers/trading212.py:_ticker_state()'s own
    "enough history" gate (len(df) >= 300) never itself suppresses the
    comparison this test is trying to make."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = np.full(n_days, base, dtype=float)
    if jump:
        closes[-1] = base * (1 + jump)
    highs = closes * 1.01
    lows = closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _price_frame(n_days: int, start_price: float, daily_drift: float, last_day_shock: float = 0.0) -> pd.DataFrame:
    """n_days of synthetic OHLC data with a uniform per-day drift; the final
    day's return can be overridden (e.g. a crisis/stop-loss shock day)."""
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


def _decline_after_rise_frame(rise_days: int = 300, decline_days: int = 200,
                               rise_drift: float = 0.0015, decline_drift: float = -0.006) -> pd.DataFrame:
    """A long rise (long enough that trailing 12m momentum is comfortably
    positive) followed by a decline steep/long enough that momentum
    eventually turns negative -- used for the SIGNAL_OFF exit scenario."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=rise_days + decline_days)
    closes = [100.0]
    for _ in range(rise_days - 1):
        closes.append(closes[-1] * (1 + rise_drift))
    for _ in range(decline_days):
        closes.append(closes[-1] * (1 + decline_drift))
    closes = np.array(closes)
    highs = closes * 1.01
    lows = closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _calm_gspc_frame(n_days: int, crisis_on_last_day: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = [4000.0]
    for _ in range(n_days - 1):
        closes.append(closes[-1] * 1.0003)
    if crisis_on_last_day:
        closes[-1] = closes[-2] * (1 - 0.09)
    closes = np.array(closes)
    highs = closes * 1.005
    lows = closes * 0.995
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _config(ticker: str, t212_ticker: str, is_etf: bool, *, max_holding_days: int = 90,
            stock_max_holding_days: int = 30, atr_stop_multiplier: float = 2.0,
            mocker=None, db_engine=None) -> dict:
    # maybe_place_orders()/maybe_exit_positions() now source their candidate
    # pool from analysis.universe.get_live_stock_candidates() and resolve
    # each ticker's T212 instrument mapping from the DB-backed
    # instrument_resolutions cache, not config["instruments"] -- see
    # CLAUDE.md's "wire the momentum-only, full-S&P-500 selection into live
    # execution" section. Mocked/seeded here so every existing call site
    # needs only to also pass mocker=mocker, db_engine=db_engine.
    if mocker is not None:
        mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[ticker])
    if db_engine is not None:
        with db_engine.begin() as conn:
            conn.execute(sa_text("""
                INSERT INTO instrument_resolutions
                    (ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at)
                VALUES (:t, :tt, 'USD', 'VERIFIED', 'seeded for test', :now, :now)
                ON CONFLICT(ticker) DO UPDATE SET
                    t212_instrument_id = :tt, status = 'VERIFIED', attempted_at = :now
            """), {"t": ticker, "tt": t212_ticker, "now": datetime.utcnow().isoformat()})
    return {
        "instruments": {
            ticker: {"t212_instrument_id": t212_ticker, "currency": "USD", "is_etf": is_etf},
        },
        "trading212_invest": {
            "enabled": True, "live": False,
            "max_total_deployed": 0.5, "per_trade_pct": 0.05,
            "var_confidence": 0.95, "var_lookback_days": 252,
            "max_risk_pct": 0.01, "min_position_gbp": 50,
            "max_holding_days": max_holding_days,
            "stock_max_holding_days": stock_max_holding_days,
        },
        "backtest": {"atr_stop_multiplier": atr_stop_multiplier, "starting_capital_gbp": 10000},
        "signals": {"atr_period": 14},
    }


@pytest.fixture
def parity_mocks(db_engine, monkeypatch, mocker):
    """Same shape as tests/test_execution.py's v2_mocks fixture, kept local
    (not imported cross-file) so this test's independence from that file's
    own fixtures is explicit."""
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
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)
    return mock_client


def _mock_price_data(mocker, frames: dict):
    def _fake_get_price_df(ticker, days=8000):
        return frames[ticker].copy()
    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)


def _pending_rows(engine, ticker: str) -> list:
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text("SELECT * FROM pending_orders WHERE ticker = :t AND status = 'PENDING'"),
            {"t": ticker},
        ).mappings().all()
    return [dict(r) for r in rows]


def _sell_exit_reason(engine, ticker: str):
    with engine.connect() as conn:
        row = conn.execute(sa_text(
            "SELECT exit_reason FROM invest_orders WHERE ticker = :t AND direction = 'SELL' "
            "ORDER BY id DESC LIMIT 1"
        ), {"t": ticker}).fetchone()
    return row[0] if row else None


def _seed_buy_row(engine, *, ticker, t212_ticker, is_etf, entry_date, stop_price):
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (
                order_id, ticker, t212_ticker, direction, quantity, estimated_price,
                estimated_value_gbp, status, is_etf, account_type, currency_code,
                stop_price, filled_at, placed_at, created_at
            ) VALUES (
                'seed-buy', :ticker, :t212_ticker, 'BUY', 10.0, 100.0,
                1000.0, 'FILLED', :is_etf, 'DEMO', 'USD',
                :stop_price, :filled_at, :filled_at, :filled_at
            )
        """), {
            "ticker": ticker, "t212_ticker": t212_ticker, "is_etf": is_etf,
            "stop_price": stop_price, "filled_at": entry_date.isoformat(),
        })


# ---------------------------------------------------------------------------
# Entry parity — does live queue a PENDING order iff the backtest engine
# would have opened a trade on the same final day?
# ---------------------------------------------------------------------------

def _entry_fires_in_backtest(frame: pd.DataFrame, gspc_close: pd.Series, ticker: str, cfg: dict, is_etf: bool) -> bool:
    from analysis.backtest import simulate_trades
    df = frame.set_index("date")
    df.index = pd.to_datetime(df.index)
    close, high, low = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
    from analysis import signals
    from analysis.volatility import _atr
    atr_s = _atr(high, low, close, cfg["signals"]["atr_period"])
    sig = signals.compute_signal_series(close)
    working = pd.DataFrame({"close": close, "atr": atr_s, "signal": sig})
    trades = simulate_trades(working, ticker, cfg, gspc_close, is_etf)
    last_date = working.index[-1].date()
    return any(t["entry_date"] == last_date for t in trades)


def test_entry_parity_fires_when_all_three_conditions_hold(db_engine, parity_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    frame = _flat_then_jump_frame(jump=0.05)  # signal ON only on the final day
    gspc = _calm_gspc_frame(310)
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    _mock_price_data(mocker, {ticker: frame, "^GSPC": gspc})

    cfg = _config(ticker, t212_ticker, is_etf=True, mocker=mocker, db_engine=db_engine)
    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    backtest_entered = _entry_fires_in_backtest(frame, gspc_close, ticker, cfg, is_etf=True)
    assert backtest_entered is True  # sanity: scenario actually exercises entry

    maybe_place_orders(cfg, run_type="full")
    live_entered = len(_pending_rows(db_engine, ticker)) == 1

    assert live_entered == backtest_entered == True


def test_entry_parity_blocked_when_signal_is_off(db_engine, parity_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    frame = _flat_then_jump_frame(jump=0.0)  # never turns on
    gspc = _calm_gspc_frame(310)
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    _mock_price_data(mocker, {ticker: frame, "^GSPC": gspc})

    cfg = _config(ticker, t212_ticker, is_etf=True, mocker=mocker, db_engine=db_engine)
    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    backtest_entered = _entry_fires_in_backtest(frame, gspc_close, ticker, cfg, is_etf=True)
    maybe_place_orders(cfg, run_type="full")
    live_entered = len(_pending_rows(db_engine, ticker)) == 1

    assert live_entered == backtest_entered == False


def test_entry_parity_blocked_during_crisis_despite_positive_signal(db_engine, parity_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    frame = _flat_then_jump_frame(jump=0.05)  # signal ON on the final day
    gspc = _calm_gspc_frame(310, crisis_on_last_day=True)  # but that day is also a crisis day
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    _mock_price_data(mocker, {ticker: frame, "^GSPC": gspc})

    cfg = _config(ticker, t212_ticker, is_etf=True, mocker=mocker, db_engine=db_engine)
    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    backtest_entered = _entry_fires_in_backtest(frame, gspc_close, ticker, cfg, is_etf=True)
    maybe_place_orders(cfg, run_type="full")
    live_entered = len(_pending_rows(db_engine, ticker)) == 1

    assert live_entered == backtest_entered == False


def test_entry_parity_blocked_when_not_an_index_member(db_engine, parity_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    # A fictitious "stock" ticker: not in ETF_TICKERS and not in
    # STOCK_MEMBERSHIP -> universe.is_member() returns False with no mocking
    # needed at all, for both engines, since both read the same table.
    ticker, t212_ticker = "PXSTOCK", "PXSTOCK_EQ"
    frame = _flat_then_jump_frame(jump=0.05)
    gspc = _calm_gspc_frame(310)
    _mock_price_data(mocker, {ticker: frame, "^GSPC": gspc})

    cfg = _config(ticker, t212_ticker, is_etf=False, mocker=mocker, db_engine=db_engine)
    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    backtest_entered = _entry_fires_in_backtest(frame, gspc_close, ticker, cfg, is_etf=False)
    maybe_place_orders(cfg, run_type="full")
    live_entered = len(_pending_rows(db_engine, ticker)) == 1

    assert live_entered == backtest_entered == False


# ---------------------------------------------------------------------------
# Exit parity — given the SAME already-open position (entry context derived
# from a real backtest run, not hand-computed), does live's exit check agree
# with the backtest's own exit_reason for the same day?
# ---------------------------------------------------------------------------

def _run_backtest_and_get_first_trade(frame: pd.DataFrame, gspc_close: pd.Series, ticker: str,
                                       cfg: dict, is_etf: bool) -> dict:
    from analysis.backtest import simulate_trades
    from analysis import signals
    from analysis.volatility import _atr

    df = frame.set_index("date")
    df.index = pd.to_datetime(df.index)
    close, high, low = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
    atr_s = _atr(high, low, close, cfg["signals"]["atr_period"])
    sig = signals.compute_signal_series(close)
    working = pd.DataFrame({"close": close, "atr": atr_s, "signal": sig})
    trades = simulate_trades(working, ticker, cfg, gspc_close, is_etf)
    assert trades, "scenario construction error: backtest opened no trade at all"
    return trades[0]


def _assert_exit_parity(db_engine, mocker, monkeypatch, *, ticker, t212_ticker, is_etf, frame, gspc, cfg, expected_reason):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    trade = _run_backtest_and_get_first_trade(frame, gspc_close, ticker, cfg, is_etf)
    assert trade["exit_reason"] == expected_reason, (
        f"scenario construction error: backtest closed with {trade['exit_reason']!r}, "
        f"expected {expected_reason!r}"
    )

    entry_date = pd.Timestamp(trade["entry_date"])
    exit_date = pd.Timestamp(trade["exit_date"])
    entry_price = trade["entry_price"]
    stop_price = entry_price * (1 - trade["stop_distance_pct"] / 100.0)
    holding_days_calendar = (exit_date - entry_date).days

    # Truncate both series at the exit date -- this is "today" for
    # _ticker_state()/_load_gspc_close() on the live side, exactly matching
    # the day the backtest engine itself resolved this exit on.
    truncated_frame = frame[pd.to_datetime(frame["date"]) <= exit_date]
    truncated_gspc = gspc[pd.to_datetime(gspc["date"]) <= exit_date]
    exit_close = float(truncated_frame["close"].iloc[-1])

    # brokers/trading212.py:maybe_exit_positions() computes holding_days
    # from datetime.utcnow() minus the stored BUY fill timestamp -- unlike
    # every other input here, it is NOT derived from "today" as defined by
    # the price frame's own last date (a real production run has no
    # distinction between the two, since price data is always fetched up
    # to the genuine current date; this synthetic frame's dates are
    # arbitrary). Anchor the seeded entry_date to the real wall clock, at
    # the SAME calendar-day gap the backtest engine used, so both engines
    # are asking "how many days has this position been held" about the
    # identical gap -- not so the test depends on which real date it
    # happens to run on.
    entry_date_wallclock = datetime.utcnow() - timedelta(days=holding_days_calendar)

    mock_client = mocker.MagicMock()
    mock_client.get_positions.return_value = [
        {"ticker": t212_ticker, "quantity": 10.0, "currentPrice": exit_close},
    ]
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)
    mocker.patch("brokers.trading212.time.sleep", lambda *_a, **_k: None)

    def _fake_get_price_df(t, days=8000):
        return {ticker: truncated_frame, "^GSPC": truncated_gspc}[t].copy()
    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)

    _seed_buy_row(db_engine, ticker=ticker, t212_ticker=t212_ticker, is_etf=is_etf,
                  entry_date=entry_date_wallclock, stop_price=stop_price)

    from brokers.trading212 import maybe_exit_positions
    maybe_exit_positions(cfg)

    live_reason = _sell_exit_reason(db_engine, ticker)
    assert live_reason == trade["exit_reason"] == expected_reason


def test_exit_parity_crisis(db_engine, mocker, monkeypatch):
    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    frame = _price_frame(400, 100.0, 0.0015)                 # ticker itself stays bullish throughout
    gspc = _price_frame(400, 4000.0, 0.0003, last_day_shock=-0.09)  # crisis on the S&P, last day
    # A generous max-holding so MAX_HOLDING can never preempt the crisis
    # trigger this scenario is actually testing.
    cfg = _config(ticker, t212_ticker, is_etf=True, max_holding_days=500, mocker=mocker, db_engine=db_engine)
    _assert_exit_parity(db_engine, mocker, monkeypatch, ticker=ticker, t212_ticker=t212_ticker, is_etf=True,
                         frame=frame, gspc=gspc, cfg=cfg, expected_reason="CRISIS_EXIT")


def test_exit_parity_stop_loss(db_engine, mocker, monkeypatch):
    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    # Bullish for 309 days (entry at day 252), then a sharp single-day drop
    # on day 310 well beyond the fixed-at-entry 2xATR stop -- comfortably
    # >=300 total rows so _ticker_state()'s history-length gate never itself
    # suppresses the live side of the comparison.
    frame = _price_frame(310, 100.0, 0.0015, last_day_shock=-0.20)
    gspc = _calm_gspc_frame(310)
    cfg = _config(ticker, t212_ticker, is_etf=True, max_holding_days=500, mocker=mocker, db_engine=db_engine)
    _assert_exit_parity(db_engine, mocker, monkeypatch, ticker=ticker, t212_ticker=t212_ticker, is_etf=True,
                         frame=frame, gspc=gspc, cfg=cfg, expected_reason="STOP_LOSS")


def test_exit_parity_signal_off(db_engine, mocker, monkeypatch):
    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    frame = _decline_after_rise_frame(rise_days=300, decline_days=220)
    gspc = _calm_gspc_frame(520)
    # Wide ATR stop and a generous max-holding so the long decline resolves
    # via SIGNAL_OFF rather than STOP_LOSS or MAX_HOLDING firing first.
    cfg = _config(ticker, t212_ticker, is_etf=True, max_holding_days=999, atr_stop_multiplier=50.0, mocker=mocker, db_engine=db_engine)
    _assert_exit_parity(db_engine, mocker, monkeypatch, ticker=ticker, t212_ticker=t212_ticker, is_etf=True,
                         frame=frame, gspc=gspc, cfg=cfg, expected_reason="SIGNAL_OFF")


def test_exit_parity_max_holding(db_engine, mocker, monkeypatch):
    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    # entry lands at row 252 (the first day with a full 252-day momentum
    # lookback); max_holding_days=100 (calendar days) puts the exit
    # comfortably past row 300, so the frame truncated AT the exit date --
    # not just the full frame -- still satisfies _ticker_state()'s own
    # >=300-rows "enough history" gate on the live side.
    frame = _price_frame(400, 100.0, 0.0015)  # pure uptrend -- nothing else ever fires
    gspc = _calm_gspc_frame(400)
    cfg = _config(ticker, t212_ticker, is_etf=True, max_holding_days=100, mocker=mocker, db_engine=db_engine)
    _assert_exit_parity(db_engine, mocker, monkeypatch, ticker=ticker, t212_ticker=t212_ticker, is_etf=True,
                         frame=frame, gspc=gspc, cfg=cfg, expected_reason="MAX_HOLDING")


def test_exit_parity_lost_membership_no_longer_forces_exit_rides_to_max_holding(db_engine, mocker, monkeypatch):
    """Changed 2026-08-02 (see analysis/decision.py:evaluate_exit_reason()'s
    own docstring, CLAUDE.md's "let momentum, not index removal, govern
    already-open positions" section) -- index membership no longer
    participates in an already-open position's exit decision at all. This
    replaces the old test_exit_parity_index_removal, which parity-tested a
    trigger (INDEX_REMOVAL) that no longer exists. Confirms BOTH the
    backtest engine and live order placement agree on the new behaviour: a
    stock that loses S&P 500 membership soon after entry, with momentum
    staying positive the whole time, is NOT force-closed on that basis --
    it rides all the way to its own MAX_HOLDING backstop instead, exactly
    as if membership had never changed."""
    ticker, t212_ticker = "PXSTOCK", "PXSTOCK_EQ"
    # entry lands at row 252 (the first day with a full 252-day momentum
    # lookback); stock_max_holding_days=100 (calendar days) puts the exit
    # comfortably past row 300, so the frame truncated AT the exit date --
    # not just the full frame -- still satisfies _ticker_state()'s own
    # >=300-rows "enough history" gate on the live side. Mirrors
    # test_exit_parity_max_holding's own proven parameters exactly.
    n_days = 400
    frame = _price_frame(n_days, 100.0, 0.0015)  # pure uptrend throughout
    gspc = _calm_gspc_frame(n_days)
    # Membership is lost soon after entry -- well before the 100-day
    # MAX_HOLDING backstop this scenario expects to fire instead.
    removal_date = pd.Timestamp(frame["date"].iloc[260])

    def _is_member_side_effect(t, check_date):
        return pd.Timestamp(check_date) < removal_date

    mocker.patch("analysis.universe.is_member", side_effect=_is_member_side_effect)
    # get_max_holding_days() dispatches on ETF_TICKERS membership only
    # (post-2026-08-02 consolidation, every non-ETF ticker shares one
    # stock_max_holding_days value regardless of STOCK_MEMBERSHIP status) --
    # this STOCK_MEMBERSHIP entry isn't load-bearing for the holding-days
    # dispatch itself any more, only for is_member()'s own separate check
    # (mocked above), but is kept so PXSTOCK reads as a genuine tracked
    # stock rather than an untracked research-only ticker.
    mocker.patch("analysis.universe.STOCK_MEMBERSHIP", {"PXSTOCK": []})
    cfg = _config(ticker, t212_ticker, is_etf=False, stock_max_holding_days=100, mocker=mocker, db_engine=db_engine)
    _assert_exit_parity(db_engine, mocker, monkeypatch, ticker=ticker, t212_ticker=t212_ticker, is_etf=False,
                         frame=frame, gspc=gspc, cfg=cfg, expected_reason="MAX_HOLDING")


# ---------------------------------------------------------------------------
# ATR stop-multiplier parity -- config.yaml's backtest.atr_stop_multiplier
# comment claims live order placement reads this exact key ("matches live
# system"). Verified structurally rather than trusted: both call sites
# import and call the SAME analysis.volatility._resolve_entry_stop()
# function object, so there is no separate live-side copy of this formula
# that could silently diverge.
# ---------------------------------------------------------------------------

def test_resolve_entry_stop_is_the_single_shared_implementation():
    """Live (brokers/trading212.py) and the backtest engine
    (analysis/backtest.py) must both import the identical function object
    from analysis/volatility.py -- not two independently-written
    expressions of the same formula that happen to agree today but could
    quietly drift apart."""
    import analysis.volatility as vol
    import analysis.backtest as bt
    import brokers.trading212 as t212

    assert t212._resolve_entry_stop is vol._resolve_entry_stop
    assert bt._resolve_entry_stop is vol._resolve_entry_stop


def test_entry_parity_stop_price_uses_configured_atr_multiplier_not_a_hardcoded_default(
    db_engine, parity_mocks, mocker,
):
    """The PENDING row's own stop_price (queued by live's decide phase) must
    equal entry_price * (1 - stop_distance_pct/100) using the SAME
    stop_distance_pct the backtest engine independently computes for the
    identical entry -- checked at a non-default multiplier too, so a
    hardcoded 2.0 in either engine (rather than a genuine read of
    backtest.atr_stop_multiplier) would make this fail."""
    from brokers.trading212 import maybe_place_orders

    ticker, t212_ticker = "PXETF", "PXETF_EQ"
    frame = _flat_then_jump_frame(jump=0.05)
    gspc = _calm_gspc_frame(310)
    mocker.patch("analysis.universe.ETF_TICKERS", ("PXETF",))
    _mock_price_data(mocker, {ticker: frame, "^GSPC": gspc})

    gspc_close = gspc.set_index("date")["close"].astype(float)
    gspc_close.index = pd.to_datetime(gspc_close.index)

    seen_stops = {}
    for multiplier in (2.0, 4.5):
        with db_engine.begin() as conn:
            conn.execute(sa_text("DELETE FROM pending_orders"))

        cfg = _config(ticker, t212_ticker, is_etf=True, atr_stop_multiplier=multiplier, mocker=mocker, db_engine=db_engine)

        backtest_trade = _run_backtest_and_get_first_trade(frame, gspc_close, ticker, cfg, is_etf=True)
        expected_stop = backtest_trade["entry_price"] * (1 - backtest_trade["stop_distance_pct"] / 100.0)

        maybe_place_orders(cfg, run_type="full")
        rows = _pending_rows(db_engine, ticker)
        assert len(rows) == 1
        live_stop = rows[0]["stop_price"]

        assert live_stop == pytest.approx(expected_stop, rel=1e-6), (
            f"multiplier={multiplier}: live stop_price {live_stop} != "
            f"backtest-derived {expected_stop}"
        )
        seen_stops[multiplier] = live_stop

    # And the two multipliers must genuinely produce different stop prices --
    # otherwise this whole comparison would pass even against a hardcoded
    # constant that ignored config entirely.
    assert seen_stops[2.0] != pytest.approx(seen_stops[4.5])
