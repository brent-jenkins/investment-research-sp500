"""Tests for brokers/trading212.py's live order-placement functions —
maybe_place_orders()/maybe_exit_positions(), the momentum + crisis-override
strategy's execution path. The Trading212 HTTP client is mocked entirely
(pytest-mock); no real API call is ever made. All DB reads/writes go through
the db_engine fixture's in-memory SQLite database (tests/conftest.py).
"""
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text

from data.database import InvestOrder

# Captured at MODULE IMPORT time (pytest collection, before any fixture in
# this file has ever run) -- `time` is a single shared module object, so
# `brokers.trading212.time IS time` (not a separate copy); v2_mocks'
# monkeypatch.setattr("brokers.trading212.time.sleep", ...) therefore
# mutates the SAME time.sleep every other importer of `time` sees too,
# including this file's own `import time`. Grabbing the real function here,
# before any per-test fixture exists to patch it away, is the only way to
# get back the genuine, blocking time.sleep later inside a test whose own
# v2_mocks fixture has already (within that same test's execution, before
# monkeypatch's teardown-time revert) replaced it with a no-op -- see
# test_exit_check_live_pricing_completes_comfortably_within_the_hourly_
# window(), the one test in this file that needs a REAL, measurable sleep.
_REAL_TIME_SLEEP = time.sleep


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


def _frame_with_exact_momentum(target_momentum: float, n_days: int = 400) -> pd.DataFrame:
    """A flat-price frame whose trailing 12-month (252-trading-day) momentum
    on the final row is EXACTLY target_momentum -- not merely close to it.
    Sets only the single anchor point compute_momentum_series() actually
    reads (close[-253], i.e. 252 rows before the last), leaving every other
    row flat at 100.0, so close[-1]/close[-253] - 1 == target_momentum to
    floating-point precision. Used for the minimum-momentum-floor tests,
    where an approximate (e.g. compounding-daily-drift) momentum value
    would not reliably land on the correct side of an exact boundary."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = np.full(n_days, 100.0)
    closes[-253] = 100.0 / (1 + target_momentum)
    highs = closes * 1.01
    lows = closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _seed_resolutions_from_mapping(engine, mapping: dict) -> None:
    """Seed VERIFIED instrument_resolutions rows matching *mapping* --
    maybe_place_orders()/maybe_exit_positions() now resolve each ticker's
    T212 instrument mapping from this DB-backed cache
    (brokers/trading212.py:resolve_and_cache_instrument()/
    _load_all_verified_instrument_resolutions()) rather than reading a
    static config.yaml `instruments:` dict. See CLAUDE.md's "wire the
    momentum-only, full-S&P-500 selection into live execution" section."""
    with engine.begin() as conn:
        for ticker, raw in mapping.items():
            conn.execute(sa_text("""
                INSERT INTO instrument_resolutions
                    (ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at)
                VALUES (:t, :tt, :ccy, 'VERIFIED', 'seeded for test', :now, :now)
            """), {
                "t": ticker, "tt": raw["t212_instrument_id"],
                "ccy": raw.get("currency", "USD"), "now": datetime.utcnow().isoformat(),
            })


def _config(mapping=None, enabled=True, mocker=None, db_engine=None) -> dict:
    if mapping is None:
        mapping = {
            "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True},
            "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
        }
    # The live candidate pool (analysis.universe.get_live_stock_candidates())
    # and each ticker's T212 instrument mapping (instrument_resolutions) are
    # both DB/live-derived now, not read from config["instruments"] -- see
    # _seed_resolutions_from_mapping()'s own docstring. Mocked/seeded here,
    # from the same *mapping* every existing call site already passes, so
    # each test needs only to also pass mocker=mocker, db_engine=db_engine.
    if mocker is not None:
        mocker.patch("analysis.universe.get_live_stock_candidates", return_value=list(mapping.keys()))
    if db_engine is not None:
        _seed_resolutions_from_mapping(db_engine, mapping)
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
        "backtest": {"atr_stop_multiplier": 2.0},
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
    generic bullish frame (so ^GSPC always resolves unless overridden).

    Also mocks _live_current_price() (2026-08-02) to return the same
    frame's last close -- the submission-time sanity check's "current
    price" source moved from get_price_df() (mocked here) to a genuine
    live yfinance quote, and every caller of this shared helper that
    doesn't specifically care about the sanity check still needs a
    default "price hasn't moved" current-price value, or _live_current_
    price() would make a real, unmocked network call during the test.
    Matches this function's own prior behaviour exactly: before the fix,
    _latest_close() WAS get_price_df().iloc[-1]["close"] -- this keeps
    that same effective value, just from the new function name. A test
    that specifically wants to control the sanity check's live price
    independently of the stored frame (e.g. to prove the two are no
    longer the same thing) mocks brokers.trading212._live_current_price
    itself afterward, which overrides this default.
    """
    default = _bullish_frame()

    def _fake_get_price_df(ticker, days=8000):
        return frames.get(ticker, default).copy()

    def _fake_live_current_price(ticker):
        frame = frames.get(ticker, default)
        return float(frame["close"].iloc[-1])

    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("brokers.trading212._live_current_price", side_effect=_fake_live_current_price)


def _mock_live_price_for(mocker, ticker: str, price: float) -> None:
    """Override _live_current_price() for exactly ONE ticker, leaving
    every other ticker (notably ^GSPC, which the submission-time crisis
    re-check -- is_crisis_mode_active_now() -- also reads via this same
    function) at its own unchanged _bullish_frame() close.

    A bare `mocker.patch(..., return_value=X)` -- the pattern the
    original price-sanity tests used, since it predates the crisis
    re-check -- feeds X to EVERY ticker's _live_current_price() call,
    including ^GSPC's. Since _bullish_frame() is fully deterministic
    (confirmed directly), GSPC's own stored close and X can differ enough
    to look like a genuine >=7% single-day GSPC decline, spuriously
    triggering the crisis override and blocking submission for an
    unrelated reason before the price-sanity check is even reached --
    exactly what happened when this helper was added (2026-08-03), caught
    by a real test failure, not assumed."""
    default_close = float(_bullish_frame()["close"].iloc[-1])

    def _side_effect(t):
        return price if t == ticker else default_close

    mocker.patch("brokers.trading212._live_current_price", side_effect=_side_effect)


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


def _seed_t212_snapshot(summary: dict, checked_at: str = None) -> None:
    """Seed the stored T212 account snapshot project_pending_order_admission()
    now reads (2026-08-09 -- it no longer constructs a Trading212Client or
    calls get_account_summary() itself, see that function's own docstring).
    checked_at defaults to "now" -- pass an explicit ISO string to test
    staleness-adjacent behaviour."""
    import json as _json
    from data.database import set_metadata
    set_metadata("t212_account_snapshot", _json.dumps(summary))
    set_metadata("t212_snapshot_fetched_at", checked_at or datetime.utcnow().isoformat())


def _seed_pending_order(engine, *, ticker, t212_ticker, is_etf, decided_price=100.0,
                         position_gbp=500.0, status="PENDING", currency="USD", momentum_pct=0.05,
                         admission_rank_value=None):
    # admission_rank_value defaults to momentum_pct (2026-08-09) -- the real
    # decide phase always populates both, equal to each other in the
    # default admission_ranking_mode; a seeded row that leaves it NULL
    # would sort last in _load_pending_orders()'s real ORDER BY regardless
    # of momentum_pct, which is not what a real queued row ever looks like.
    if admission_rank_value is None:
        admission_rank_value = momentum_pct
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, admission_rank_value, stop_price, var_pct, status, created_at)
            VALUES (:ticker, :t212_ticker, :is_etf, :currency, :now, :decided_price,
                    :position_gbp, :momentum_pct, :admission_rank_value, :stop_price, 0.02, :status, :now)
        """), {
            "ticker": ticker, "t212_ticker": t212_ticker, "is_etf": is_etf,
            "currency": currency, "now": datetime.utcnow().isoformat(),
            "decided_price": decided_price, "position_gbp": position_gbp,
            "stop_price": decided_price * 0.9, "status": status, "momentum_pct": momentum_pct,
            "admission_rank_value": admission_rank_value,
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


def test_analysis_only_run_type_never_touches_pending_orders(db_engine, v2_mocks, mocker):
    """2026-08-03 urgent check — confirms directly (not just via
    place_market_order not being called) that a genuinely still-PENDING
    candidate queued at EOD, not yet due for its real exchange-timed
    submission window (e.g. a Friday decision whose Monday would-be
    submission window is pushed to Tuesday/Wednesday by a holiday — see
    _has_upcoming_trading_day()), survives an analysis_only run
    completely untouched. run_type != "full" returns immediately after
    maybe_exit_positions() (brokers/trading212.py:maybe_place_orders()),
    never reaching the BUY/decide-and-queue phase that is the only code
    in this function that ever writes to pending_orders at all — this is
    exactly what _eod_job() passes on a night with no upcoming trading
    day to queue candidates for (see _is_scheduled_trading_day()'s own
    docstring)."""
    from brokers.trading212 import maybe_place_orders

    _seed_pending_order(db_engine, ticker="STILLPENDING", t212_ticker="STILLPENDING_US_EQ",
                        is_etf=False, status="PENDING")
    before = _pending_rows(db_engine)
    assert len(before) == 1

    _mock_price_data(mocker, {})
    maybe_place_orders(_config(), run_type="analysis_only")

    after = _pending_rows(db_engine)
    assert after == before, "a still-PENDING candidate must survive an analysis_only run byte-for-byte unchanged"


def test_pause_new_entries_blocks_buy_but_still_exits(db_engine, v2_mocks, mocker):
    """trading212_invest.pause_new_entries is a temporary safety switch (see
    brokers/trading212.py) -- it must block the BUY phase entirely, even on
    a run_type='full' pass, while still running maybe_exit_positions()."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)
    v2_mocks.get_positions.return_value = []  # nothing open -- exit phase is a no-op but must run

    cfg = _config(mocker=mocker, db_engine=db_engine)
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
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
    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_entry_blocked_when_stock_not_index_member(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()


def test_etf_always_eligible_regardless_of_membership_check(db_engine, v2_mocks, mocker):
    """ETFs have no membership concept -- is_member() must never even be
    consulted for the is_etf branch (see _v2_ticker_state's is_member computation).
    Queues a decision (does not place directly) -- see the decide/submit split."""
    from brokers.trading212 import maybe_place_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    spy = mocker.patch("analysis.universe.is_member", return_value=False)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
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

    cfg = _config(mapping=_n_ticker_mapping(5), mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["max_total_deployed"] = 0.5  # already exceeded (60%)
    maybe_place_orders(cfg, run_type="full")

    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 5


# ---------------------------------------------------------------------------
# Asset-class trading switches (2026-08-01) -- etf_trading_enabled /
# stock_trading_enabled gate ENTRY CANDIDACY itself (checked before any
# signal is even computed), a strictly earlier/broader cut than
# etf_submission_enabled / stock_submission_enabled below, which only ever
# gate the final broker-submission step for a decision that's already been
# computed and queued. Both live here (brokers/trading212.py) and the
# backtest engine (analysis/validation.py, see tests/test_validation.py)
# read the identical shared function -- analysis.decision.is_asset_class_
# enabled() -- so neither can silently diverge on which asset classes are
# even considered. See CLAUDE.md's "Clean, independent ETF/stock trading
# switches" section.
# ---------------------------------------------------------------------------

def test_etf_trading_disabled_excludes_etf_candidacy_but_leaves_stocks_untouched(db_engine, v2_mocks, mocker):
    """Disabling ETF candidacy must mean an otherwise-qualifying ETF is
    never queued, while a stock candidate in the SAME run is queued exactly
    as if the ETF didn't exist in the universe at all."""
    from brokers.trading212 import maybe_place_orders

    # A real live candidate pool (analysis.universe.get_live_stock_candidates())
    # would never include an ETF ticker any more (ETFs were removed from
    # live trading 2026-08-02) -- VOO is used here anyway, mocked directly
    # into the candidate pool, purely to exercise maybe_place_orders()'s own
    # is_asset_class_enabled(is_etf=ticker in universe.ETF_TICKERS, ...)
    # gate defensively (VOO is still a real member of universe.ETF_TICKERS),
    # not because this scenario can occur in production today.
    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={
        "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True},
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
    }, mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["etf_trading_enabled"] = False
    maybe_place_orders(cfg, run_type="full")

    tickers_queued = {r["ticker"] for r in _pending_rows(db_engine, status="PENDING")}
    assert tickers_queued == {"AAPL"}  # ETF excluded entirely, stock unaffected


def test_stock_trading_disabled_excludes_stock_candidacy_but_leaves_etfs_untouched(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_place_orders

    # See the identical note in the sibling test above -- VOO is mocked
    # into the candidate pool purely to exercise the candidacy gate
    # defensively, not because a live-discovered ETF candidate can occur.
    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={
        "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True},
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
    }, mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["stock_trading_enabled"] = False
    maybe_place_orders(cfg, run_type="full")

    tickers_queued = {r["ticker"] for r in _pending_rows(db_engine, status="PENDING")}
    assert tickers_queued == {"VOO"}  # stock excluded entirely, ETF unaffected


def test_disabled_asset_class_never_reaches_signal_computation(db_engine, v2_mocks, mocker):
    """The candidacy gate must be checked BEFORE _ticker_state() -- no price
    data read, no signal computed at all -- for a disabled asset class, not
    merely filtered out afterward once the work has already been done."""
    from brokers.trading212 import maybe_place_orders

    frame = _bullish_frame()
    requested_tickers = []

    def _fake_get_price_df(ticker, days=8000):
        requested_tickers.append(ticker)
        return frame.copy()

    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["etf_trading_enabled"] = False
    maybe_place_orders(cfg, run_type="full")

    assert "VOO" not in requested_tickers  # never even read price data for it
    assert len(_pending_rows(db_engine, status="PENDING")) == 0


def test_decide_phase_calls_the_shared_asset_class_gate(db_engine, v2_mocks, mocker):
    """Proves maybe_place_orders() genuinely delegates to
    analysis.decision.is_asset_class_enabled() per ticker rather than
    re-deriving the same config lookup inline -- the same guarantee
    tests/test_validation.py checks on the backtest side, so both call
    sites are provably wired to one shared implementation, not two
    independently hand-written copies that could quietly drift apart."""
    import brokers.trading212 as t212mod
    from brokers.trading212 import maybe_place_orders

    spy = mocker.spy(t212mod, "is_asset_class_enabled")
    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mocker=mocker, db_engine=db_engine)  # default mapping: VOO (ETF) + AAPL (stock)
    maybe_place_orders(cfg, run_type="full")

    assert spy.call_count == 2  # once per ticker in the mapping
    seen_is_etf = {call.kwargs["is_etf"] for call in spy.call_args_list}
    assert seen_is_etf == {True, False}
    for call in spy.call_args_list:
        assert call.kwargs["config"] is cfg


def test_below_floor_candidate_excluded_from_candidacy(db_engine, v2_mocks, mocker):
    """The decisive live-side proof: a ticker with sig_on=True the entire
    window (positive momentum, so it would have qualified before this
    feature) but whose momentum sits below a configured 2% floor must
    never reach pending_orders at all."""
    from brokers.trading212 import maybe_place_orders

    weak = _frame_with_exact_momentum(0.01)  # 1% -- below floor
    _mock_price_data(mocker, {"AAPL": weak})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["min_momentum_pct"] = 0.02
    maybe_place_orders(cfg, run_type="full")

    assert len(_pending_rows(db_engine, status="PENDING")) == 0


def test_above_floor_candidate_admitted_unaffected_by_floor(db_engine, v2_mocks, mocker):
    """The same scenario, but with momentum comfortably above the
    configured floor -- must be queued exactly as before this feature."""
    from brokers.trading212 import maybe_place_orders

    strong = _frame_with_exact_momentum(0.05)  # 5% -- above floor
    _mock_price_data(mocker, {"AAPL": strong})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["min_momentum_pct"] = 0.02
    maybe_place_orders(cfg, run_type="full")

    tickers_queued = {r["ticker"] for r in _pending_rows(db_engine, status="PENDING")}
    assert tickers_queued == {"AAPL"}


def test_decide_phase_calls_the_shared_momentum_floor_functions(db_engine, v2_mocks, mocker):
    """Proves maybe_place_orders() genuinely delegates to
    analysis.decision.resolve_min_momentum_floor()/passes_momentum_floor()
    per ticker rather than re-deriving the same config lookup/comparison
    inline -- the same guarantee test_mtm_momentum_floor_excludes_
    candidate_below_floor_admits_one_above (tests/test_portfolio_backtest.py)
    checks on the backtest side, mirroring
    test_decide_phase_calls_the_shared_asset_class_gate's own pattern for
    is_asset_class_enabled()."""
    import brokers.trading212 as t212mod
    from brokers.trading212 import maybe_place_orders

    resolve_spy = mocker.spy(t212mod, "resolve_min_momentum_floor")
    floor_spy = mocker.spy(t212mod, "passes_momentum_floor")
    _mock_price_data(mocker, {"VOO": _bullish_frame(), "AAPL": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mocker=mocker, db_engine=db_engine)  # default mapping: VOO (ETF) + AAPL (stock)
    cfg["trading212_invest"]["min_momentum_pct"] = 0.02
    maybe_place_orders(cfg, run_type="full")

    assert resolve_spy.call_count == 2  # once per ticker in the mapping
    for call in resolve_spy.call_args_list:
        assert call.args[0] is cfg
    assert floor_spy.call_count == 2
    for call in floor_spy.call_args_list:
        assert call.args[1] == 0.02  # the resolved floor, not a hardcoded literal


def test_live_and_backtest_resolve_momentum_floor_from_the_identical_function(db_engine, v2_mocks, mocker):
    """Direct identity proof, not just behavioural agreement: both
    brokers.trading212 and analysis.backtest hold a reference to the
    EXACT SAME function objects (not two independently-written
    expressions of the same rule) -- the same standard already applied
    to the ATR multiplier and the re-entry cooldown's shared resolver."""
    import analysis.decision as decision_mod
    import brokers.trading212 as t212mod
    import analysis.backtest as backtest_mod

    assert t212mod.resolve_min_momentum_floor is decision_mod.resolve_min_momentum_floor
    assert t212mod.passes_momentum_floor is decision_mod.passes_momentum_floor
    assert backtest_mod.resolve_min_momentum_floor is decision_mod.resolve_min_momentum_floor
    assert backtest_mod.passes_momentum_floor is decision_mod.passes_momentum_floor


def test_trading_enabled_with_submission_disabled_is_a_coherent_shadow_state(db_engine, v2_mocks, mocker):
    """etf_trading_enabled=True (the default) alongside
    etf_submission_enabled=False (also the shipped default) is a
    deliberate, coherent state, not a contradiction: decisions are still
    computed, momentum-ranked, and queued for audit/research, but never
    reach the broker while submission stays off."""
    from brokers.trading212 import maybe_place_orders, submit_pending_etf_orders

    _mock_price_data(mocker, {"VOO": _bullish_frame()})
    mocker.patch("analysis.universe.is_member", return_value=True)

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
    # Neither etf_trading_enabled nor etf_submission_enabled set explicitly
    # -- defaults are True / False respectively.
    maybe_place_orders(cfg, run_type="full")
    assert len(_pending_rows(db_engine, status="PENDING")) == 1  # candidacy ran, decision queued

    n = submit_pending_etf_orders(cfg)
    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1  # still queued, untouched


def test_trading_disabled_overrides_submission_enabled_true(db_engine, v2_mocks, mocker):
    """etf_trading_enabled=False must win over an explicit
    etf_submission_enabled=True -- a decision that was never a real
    candidate must never be submitted regardless of the narrower flag. Any
    row queued before trading was disabled is left pending, not resolved."""
    from brokers.trading212 import submit_pending_etf_orders

    _seed_pending_order(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", is_etf=True)
    cfg = _submission_config(etf_enabled=True)  # would otherwise submit
    cfg["trading212_invest"]["etf_trading_enabled"] = False

    n = submit_pending_etf_orders(cfg)

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1  # left untouched, not resolved


def test_stock_trading_disabled_overrides_stock_submission_enabled(db_engine, v2_mocks, mocker):
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=float(frame["close"].iloc[-1]))
    _mock_price_data(mocker, {"AAPL": frame})
    cfg = _submission_config(stock_enabled=True)
    cfg["trading212_invest"]["stock_trading_enabled"] = False

    n = submit_pending_stock_orders(cfg)

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1


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

    cfg = _config(mapping={ticker: {"t212_instrument_id": t212_ticker, "currency": "USD", "is_etf": is_etf}},
                  mocker=mocker, db_engine=db_engine)
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


def test_lost_membership_no_longer_forces_an_exit_on_its_own(db_engine, v2_mocks, mocker):
    """Changed 2026-08-02 -- is_member=False alone (crisis/stop/signal-off/
    max-holding all otherwise quiet) must NOT close the position any more.
    A held ticker that loses S&P 500 membership is still tracked and
    priced normally; it simply isn't force-exited on that basis. Membership
    now only gates NEW entries (see maybe_place_orders() tests below)."""
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="AAPL", t212_ticker="AAPL_US_EQ",
        frame=_bullish_frame(),      # positive momentum -- SIGNAL_OFF would not fire
        stop_price=0.01,             # far below price -- STOP_LOSS would not fire
        days_ago=1, max_holding_days=30,  # nowhere near MAX_HOLDING
        is_etf=False, is_member=False,
        crisis_now=False,
    )
    v2_mocks.place_market_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row is None, "no SELL order should ever have been placed for this position"


def test_lost_membership_still_exits_normally_once_a_real_exit_condition_fires(db_engine, v2_mocks, mocker):
    """The flip side of the test above -- losing membership doesn't make a
    position immune to its OWN normal exit conditions either. STOP_LOSS
    (checked before SIGNAL_OFF in priority) must still fire exactly as if
    membership had never changed."""
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="AAPL", t212_ticker="AAPL_US_EQ",
        frame=_bearish_frame(),   # would also be SIGNAL_OFF
        stop_price=99999.0,       # guarantees price <= stop_price -- STOP_LOSS fires
        is_etf=False, is_member=False,
        crisis_now=False,
    )
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "STOP_LOSS"


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
# extendedHours for CRISIS_EXIT sells only (2026-08-04) -- see CLAUDE.md's
# "extendedHours for sell orders" investigation for the full trade-off
# reasoning. Only CRISIS_EXIT should pass extended_hours=True to
# place_market_order(); every other exit reason, and every BUY, must keep
# sending the exact same payload as before this feature existed.
# ---------------------------------------------------------------------------

def test_crisis_exit_sell_requests_extended_hours(db_engine, v2_mocks, mocker):
    """The core proof: a CRISIS_EXIT sell must call place_market_order()
    with extended_hours=True."""
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=_price_frame(400, 100.0, 0.0015, last_day_shock=-0.09),
        stop_price=99999.0,  # would also fire STOP_LOSS if crisis didn't win priority
        days_ago=999,        # would also fire MAX_HOLDING
        crisis_now=True,
    )
    v2_mocks.place_market_order.assert_called_once()
    _args, kwargs = v2_mocks.place_market_order.call_args
    assert kwargs.get("extended_hours") is True
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "CRISIS_EXIT"


def test_signal_off_sell_does_not_request_extended_hours(db_engine, v2_mocks, mocker):
    frame = _bearish_frame()
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 0.01,  # far below price -- stop does NOT fire
        days_ago=1, max_holding_days=90,
    )
    v2_mocks.place_market_order.assert_called_once()
    _args, kwargs = v2_mocks.place_market_order.call_args
    assert kwargs.get("extended_hours") is False
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "SIGNAL_OFF"


def test_max_holding_sell_does_not_request_extended_hours(db_engine, v2_mocks, mocker):
    frame = _bullish_frame()  # signal stays on -- MAX_HOLDING fires instead
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 0.01,
        days_ago=999,
        max_holding_days=30,
    )
    v2_mocks.place_market_order.assert_called_once()
    _args, kwargs = v2_mocks.place_market_order.call_args
    assert kwargs.get("extended_hours") is False
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "MAX_HOLDING"


def test_stop_loss_sell_does_not_request_extended_hours(db_engine, v2_mocks, mocker):
    """STOP_LOSS is deliberately out of scope for this feature (the
    broker-side stop has no extendedHours field at all, and local
    STOP_LOSS evaluation is slated for removal separately) -- confirms it
    stays regular-hours-only, same as SIGNAL_OFF/MAX_HOLDING."""
    frame = _bearish_frame()
    last_price = float(frame["close"].iloc[-1])
    _run_exit(
        db_engine, v2_mocks, mocker,
        ticker="VOO", t212_ticker="I50Dl_EQ",
        frame=frame,
        stop_price=last_price * 10,  # guarantees price <= stop_price
    )
    v2_mocks.place_market_order.assert_called_once()
    _args, kwargs = v2_mocks.place_market_order.call_args
    assert kwargs.get("extended_hours") is False
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "STOP_LOSS"


def test_place_market_order_payload_only_adds_extended_hours_key_when_true(monkeypatch, mocker):
    """Direct client-level proof, independent of the exit-check plumbing
    above: the JSON payload itself must be byte-for-byte identical to the
    pre-feature two-field shape ({"ticker", "quantity"}) whenever
    extended_hours is left at its default -- no new key added at all, not
    just a falsy one -- and must gain exactly one extra key, extendedHours:
    true, when explicitly requested."""
    from brokers.trading212 import Trading212Client

    monkeypatch.setenv("T212_API_KEY", "k")
    monkeypatch.setenv("T212_API_SECRET", "s")
    client = Trading212Client(live=False)

    class _FakeResponse:
        status_code = 200
        reason = "OK"
        url = "https://demo.trading212.com/api/v0/equity/orders/market"
        text = ""

        def json(self):
            return {"id": "order-1"}

    req = mocker.patch("brokers.trading212.requests.request", return_value=_FakeResponse())

    client.place_market_order("AAPL_US_EQ", -5.0)
    assert req.call_args.kwargs["json"] == {"ticker": "AAPL_US_EQ", "quantity": -5.0}

    req.reset_mock()
    client.place_market_order("AAPL_US_EQ", -5.0, extended_hours=True)
    assert req.call_args.kwargs["json"] == {
        "ticker": "AAPL_US_EQ", "quantity": -5.0, "extendedHours": True,
    }


def test_buy_order_payload_never_receives_extended_hours(db_engine, v2_mocks, mocker):
    """The BUY submission call site (_submit_pending_orders()) must never
    pass extended_hours at all -- confirms case 1 (new-entry buys) stays
    genuinely untouched by this feature, not just defaulted to False."""
    from brokers.trading212 import submit_pending_stock_orders

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    cfg = _config(mapping=mapping, mocker=mocker, db_engine=db_engine)
    _mock_price_data(mocker, {})
    v2_mocks.get_positions.return_value = []
    v2_mocks.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 5_000.0},
        "investments": {"currentValue": 0.0},
    }
    v2_mocks.get_instruments.return_value = [
        {"ticker": "AAPL_US_EQ", "minTradeQuantity": 1, "maxQuantityPrecision": 0},
    ]
    v2_mocks.get_quantity_for_gbp.return_value = 10.0
    v2_mocks.get_instrument_price_gbp.return_value = 50.0
    v2_mocks.place_market_order.return_value = {"id": "buy-order-eh"}
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False)

    submit_pending_stock_orders(cfg)

    v2_mocks.place_market_order.assert_called_once()
    _args, kwargs = v2_mocks.place_market_order.call_args
    assert "extended_hours" not in kwargs


# ---------------------------------------------------------------------------
# Live pricing for the exit check (2026-08-03) -- STOP_LOSS now compares
# against a genuinely live price, not the once-daily stored close
# _ticker_state() reads from price_data. See CLAUDE.md's "live pricing for
# the exit check" section for the full investigation, and
# brokers/trading212.py:_live_exit_check_price()'s own docstring for the
# two-tier mechanism (T212's own currentPrice, already fetched in the same
# get_positions() call -- zero extra network calls -- falling back to
# _live_current_price() / yfinance, paced, only when T212's own price is
# missing).
# ---------------------------------------------------------------------------

def _mock_yfinance_fast(mocker, price: float) -> None:
    """Patches yfinance.Ticker(...).fast_info.last_price to return *price*
    instantly, with no real network call. Used only by the two timing
    tests below, which specifically need _live_current_price()'s REAL
    code -- including its embedded _throttle_yfinance_request() call -- to
    actually run, unlike every other test in this file (which mocks
    _live_current_price() itself directly, via _mock_price_data() or a
    direct mocker.patch, bypassing the throttle entirely)."""
    mock_ticker = mocker.MagicMock()
    mock_ticker.fast_info.last_price = price
    mocker.patch("yfinance.Ticker", return_value=mock_ticker)


def test_exit_stop_loss_uses_live_t212_current_price_not_stale_stored_close(db_engine, v2_mocks, mocker):
    """The decisive proof this fix exists to provide: stop_price sits
    BETWEEN the stale stored close (well above it -- would NOT have
    triggered STOP_LOSS under the old code) and T212's own live
    currentPrice (well below it -- genuinely breaches the stop). Only a
    fix that is actually reading the live value, not the stored close,
    can produce STOP_LOSS here."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    stale_close = float(frame["close"].iloc[-1])
    stop_price = stale_close * 0.9        # stale close (100%) sits ABOVE this -- old code would NOT fire
    live_price = stale_close * 0.5        # live price sits BELOW this -- must fire if live is genuinely used

    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0, "currentPrice": live_price},
    ]
    _mock_price_data(mocker, {"AAPL": frame})
    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=stop_price, days_ago=1, is_etf=False)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_called_once()
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "STOP_LOSS", (
        "STOP_LOSS must fire from the live T212 currentPrice (below stop) -- "
        "the stale stored close (above stop) could never have produced this on its own"
    )


def test_exit_stop_loss_does_not_fire_on_a_stale_close_the_live_price_has_since_recovered_past(db_engine, v2_mocks, mocker):
    """The mirror of the test above: the STALE stored close sits at or
    below stop_price (the OLD, once-daily-close-based code would have
    wrongly force-sold this position), but the live T212 currentPrice has
    genuinely recovered back above the stop. The live price must win --
    no SELL should be placed at all."""
    from brokers.trading212 import maybe_exit_positions

    # Flat close at 100.0 with an exact, known-positive momentum anchor --
    # decouples the stop-loss price level from the momentum signal, so
    # SIGNAL_OFF can't also fire and confound the "no SELL at all" assertion.
    frame = _frame_with_exact_momentum(0.05)
    stale_close = float(frame["close"].iloc[-1])
    assert stale_close == 100.0
    stop_price = 150.0    # stale close (100) is BELOW this -- old code WOULD wrongly fire
    live_price = 200.0    # live price is ABOVE this -- must NOT fire

    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0, "currentPrice": live_price},
    ]
    _mock_price_data(mocker, {"AAPL": frame})
    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=stop_price, days_ago=1, is_etf=False)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_not_called()


def test_exit_falls_back_to_yfinance_live_price_when_t212_current_price_missing(db_engine, v2_mocks, mocker):
    """When T212's own currentPrice is absent from the position dict (tier
    1 unavailable), the exit check must fall back to _live_current_price()
    (yfinance) -- the identical mechanism the crisis check and the
    submission-time price-sanity check already use -- rather than silently
    reverting to the stale stored close."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    stale_close = float(frame["close"].iloc[-1])
    stop_price = stale_close * 0.5   # stale close does NOT breach this
    fallback_live_price = stop_price * 0.5  # the yfinance fallback value DOES breach it

    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0},  # deliberately no currentPrice key at all
    ]
    _mock_price_data(mocker, {"AAPL": frame})
    live_price_mock = mocker.patch("brokers.trading212._live_current_price", return_value=fallback_live_price)
    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=stop_price, days_ago=1, is_etf=False)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    live_price_mock.assert_called_with("AAPL")
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "STOP_LOSS"


def test_missing_live_price_skips_only_stop_loss_other_exit_conditions_still_fire(db_engine, v2_mocks, mocker):
    """A total live-price fetch failure (both tiers exhausted -- no T212
    currentPrice AND _live_current_price() returns None) must degrade
    gracefully, exactly as evaluate_exit_reason()'s own None-tolerance
    promises: STOP_LOSS simply doesn't fire for that ticker this cycle,
    but MAX_HOLDING (which never consumes price at all) still correctly
    fires for it -- proving the failure is isolated to the one condition
    that actually needs a price, not the whole ticker's exit evaluation."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0},  # no currentPrice -- forces the yfinance tier
    ]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("brokers.trading212._live_current_price", return_value=None)  # both tiers now exhausted
    # stop_price deliberately absurdly low -- would never fire even if a
    # price WERE available, isolating this test to "does MAX_HOLDING still
    # work when price is None", not "does a low stop coincidentally fire".
    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=0.01, days_ago=999, is_etf=False)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)  # must not raise despite price being unavailable

    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row is not None, "MAX_HOLDING must still fire even though no live price was ever obtained"
    assert row[0] == "MAX_HOLDING"


def test_missing_live_price_for_one_position_does_not_block_evaluation_of_another(db_engine, v2_mocks, mocker):
    """Per-ticker isolation, across two DIFFERENT positions in the same
    run: AAPL's live price is entirely unavailable (both tiers fail);
    VOO's is fine. Both are evaluated independently via MAX_HOLDING (which
    doesn't depend on price at all), confirming AAPL's failure doesn't
    prevent VOO -- or AAPL itself, for its own non-price-dependent
    conditions -- from being correctly evaluated and exited."""
    from brokers.trading212 import maybe_exit_positions

    frame_a = _bullish_frame()
    frame_b = _bullish_frame()

    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0},                     # no currentPrice -- yfinance tier
        {"ticker": "I50Dl_EQ", "quantity": 10.0, "currentPrice": 999999.0},  # T212 tier works fine
    ]
    _mock_price_data(mocker, {"AAPL": frame_a, "VOO": frame_b})

    def _live_price_side_effect(ticker):
        return None if ticker == "AAPL" else float(frame_b["close"].iloc[-1])

    mocker.patch("brokers.trading212._live_current_price", side_effect=_live_price_side_effect)

    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=0.01, days_ago=999, is_etf=False)
    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ",
                         stop_price=0.01, days_ago=999, is_etf=True)
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    cfg = _config(
        mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
                 "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
        mocker=mocker, db_engine=db_engine,
    )
    maybe_exit_positions(cfg)

    assert v2_mocks.place_market_order.call_count == 2
    rows = db_engine.connect().execute(sa_text(
        "SELECT ticker, exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchall()
    by_ticker = {r[0]: r[1] for r in rows}
    assert by_ticker == {"AAPL": "MAX_HOLDING", "VOO": "MAX_HOLDING"}


def test_live_price_fetch_exception_isolates_the_failing_position_from_the_rest(db_engine, v2_mocks, mocker):
    """A genuinely unexpected exception out of _live_exit_check_price()
    itself (not just a clean None return -- _live_current_price() already
    catches everything internally, so this simulates a failure mode
    outside that internal safety net) must still be caught by the
    EXISTING per-position try/except this call now sits inside
    (2026-07-31 -- 'one position's unhandled exception must never
    silently stop every other open position from being checked for
    exit'). AAPL's own exception must not prevent VOO, evaluated
    afterward in the same run, from exiting normally."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0},
        {"ticker": "I50Dl_EQ", "quantity": 10.0},
    ]
    _mock_price_data(mocker, {"AAPL": frame, "VOO": frame})
    mocker.patch(
        "brokers.trading212._live_exit_check_price",
        side_effect=[RuntimeError("simulated unexpected failure"), 10.0],
    )
    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=999999.0, days_ago=1, is_etf=False)
    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ",
                         stop_price=999999.0, days_ago=1, is_etf=True)  # 10.0 <= 999999.0 -- STOP_LOSS fires
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    cfg = _config(
        mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
                 "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
        mocker=mocker, db_engine=db_engine,
    )
    maybe_exit_positions(cfg)  # must not raise

    rows = db_engine.connect().execute(sa_text(
        "SELECT ticker FROM invest_orders WHERE direction='SELL'"
    )).fetchall()
    tickers_sold = {r[0] for r in rows}
    assert "AAPL" not in tickers_sold, "AAPL's own exception must not have produced a SELL for it"
    assert "VOO" in tickers_sold, "VOO must still be evaluated and exited normally despite AAPL's exception"


def test_exit_check_t212_current_price_tier_costs_zero_extra_calls_at_current_scale(db_engine, v2_mocks, mocker):
    """The realistic current-production case: T212 already returns
    currentPrice for every held position, in the SAME get_positions() call
    the exit loop already makes for its own quantity check. This proves
    -- via a call-count assertion, not inferred from timing alone, which
    could coincidentally look fast even if a slow path were silently
    taken -- that the exit check makes ZERO additional network calls
    (yfinance or otherwise) regardless of position count in this case,
    which is exactly why the "scale" concern this fix was built against
    doesn't bite in the common case."""
    from brokers.trading212 import maybe_exit_positions

    n = 16  # today's real open-position count
    frame = _bullish_frame()
    live_price = 999999.0  # never breaches any stop below -- isolates this to a pure fetch-path check

    positions, mapping = [], {}
    for i in range(n):
        ticker, t212_ticker = f"TICK{i}", f"TICK{i}_US_EQ"
        positions.append({"ticker": t212_ticker, "quantity": 10.0, "currentPrice": live_price})
        mapping[ticker] = {"t212_instrument_id": t212_ticker, "currency": "USD", "is_etf": False}
        _seed_open_position(db_engine, ticker=ticker, t212_ticker=t212_ticker,
                             stop_price=1.0, days_ago=1, is_etf=False)

    v2_mocks.get_positions.return_value = positions
    _mock_price_data(mocker, {f"TICK{i}": frame for i in range(n)})
    # Overrides _mock_price_data()'s own default _live_current_price mock
    # with one that FAILS the test loudly if ever reached -- the strongest
    # possible proof the yfinance fallback tier is never touched here.
    live_price_mock = mocker.patch(
        "brokers.trading212._live_current_price",
        side_effect=AssertionError(
            "the yfinance fallback tier must never be reached when every "
            "position already has a usable T212 currentPrice"
        ),
    )
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=999999)

    cfg = _config(mapping=mapping, mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)  # would raise AssertionError if the fallback tier were ever reached

    v2_mocks.place_market_order.assert_not_called()
    live_price_mock.assert_not_called()


def test_exit_check_live_pricing_completes_comfortably_within_the_hourly_window(db_engine, v2_mocks, mocker):
    """Real wall-clock timing at today's real open-position count (16),
    WORST CASE -- every single position falls through to the paced
    yfinance fallback tier (T212 currentPrice missing for all of them, the
    least favourable real scenario this mechanism has to handle).
    Confirms the throttle is genuinely wired in and running (a real,
    measured lower bound on elapsed time), and that even the worst case
    completes with enormous margin against the hourly market_hours_check_
    job() window.

    Restores the REAL, original time.sleep -- v2_mocks patches
    brokers.trading212.time.sleep (== the single shared `time` module's own
    sleep, not a private copy) to a no-op for every OTHER test in this file
    (correct for tests that don't measure timing; it would silently hide
    the throttle's real overhead here). By the point THIS test's body
    runs, that no-op is already installed (v2_mocks, a fixture dependency,
    has already executed) -- a fresh `import time` here would just hand
    back the already-patched module. _REAL_TIME_SLEEP (captured at this
    test FILE's own import time, before any fixture in it ever ran) is the
    only reliably-genuine reference left. Also mocks yfinance's own
    network call directly (not _live_current_price() itself), so
    _throttle_yfinance_request()'s real sleep still executes, with no real
    network latency or flakiness feeding into the measurement.
    """
    from brokers.trading212 import _YFINANCE_MIN_REQUEST_INTERVAL_SECONDS, maybe_exit_positions
    import brokers.trading212 as t212_module

    mocker.patch("brokers.trading212.time.sleep", _REAL_TIME_SLEEP)
    # Clean starting state for a reproducible measurement -- otherwise the
    # very first call in this test could inherit an already-elapsed
    # interval from whatever ran immediately before it in the same pytest
    # process, under- or over-stating the throttle's own contribution.
    t212_module._yfinance_last_request_at = 0.0

    n = 16  # today's real open-position count
    frame = _bullish_frame()

    positions, mapping = [], {}
    for i in range(n):
        ticker, t212_ticker = f"TICK{i}", f"TICK{i}_US_EQ"
        positions.append({"ticker": t212_ticker, "quantity": 10.0})  # no currentPrice -- forces the yfinance tier for everyone
        mapping[ticker] = {"t212_instrument_id": t212_ticker, "currency": "USD", "is_etf": False}
        _seed_open_position(db_engine, ticker=ticker, t212_ticker=t212_ticker,
                             stop_price=1.0, days_ago=1, is_etf=False)

    v2_mocks.get_positions.return_value = positions
    # _mock_price_data() itself ALSO patches _live_current_price() to a
    # mock (its own documented default -- see that helper's docstring) --
    # this is the ONE test in the file that specifically needs that
    # function's REAL body (throttle included) to run, not its mocked
    # replacement. Capturing the genuine function BEFORE _mock_price_data()
    # ever touches it, then explicitly restoring it as _mock_price_data()'s
    # side_effect afterward, is what makes _mock_yfinance_fast()'s patch of
    # the deeper yfinance.Ticker boundary actually reachable -- patching
    # yfinance.Ticker alone does nothing if _live_current_price() itself is
    # still a mock sitting in front of it (confirmed directly: without this
    # restore, a spy showed _live_current_price() being called 16 times but
    # _throttle_yfinance_request() 0 times -- the mocked function was still
    # the one running, never reaching its own real body at all).
    real_live_current_price = t212_module._live_current_price
    _mock_price_data(mocker, {f"TICK{i}": frame for i in range(n)})
    mocker.patch("brokers.trading212._live_current_price", side_effect=real_live_current_price)
    _mock_yfinance_fast(mocker, price=999999.0)  # never breaches any stop below
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=999999)

    cfg = _config(mapping=mapping, mocker=mocker, db_engine=db_engine)

    start = time.monotonic()
    maybe_exit_positions(cfg)
    elapsed = time.monotonic() - start

    # Confirms nothing else (order-placement's own separate 0.5s
    # inter-order sleep) contaminated this measurement -- stop_price=1.0
    # against a mocked live price of 999999.0 means no exit condition
    # should ever fire, isolating the timing to the price-fetch/throttle
    # path alone.
    v2_mocks.place_market_order.assert_not_called()

    min_expected = (n - 1) * _YFINANCE_MIN_REQUEST_INTERVAL_SECONDS
    hourly_window_seconds = 3600.0
    margin_x = hourly_window_seconds / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n[timing] {n} positions, worst case (every one via the paced "
        f"yfinance fallback tier): {elapsed:.3f}s total -- "
        f"{margin_x:,.0f}x margin against the {hourly_window_seconds:.0f}s "
        f"hourly market_hours_check_job() window."
    )

    assert elapsed >= min_expected * 0.9, (
        f"expected at least ~{min_expected:.2f}s from {n} paced calls, got {elapsed:.3f}s -- "
        "the throttle may not genuinely be running"
    )
    # Generous sanity ceiling -- catches an accidental huge/wrong sleep,
    # while leaving enormous headroom under the real 3600s hourly window.
    assert elapsed < 30.0


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

    cfg = _config(mapping={"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Broker-side stop order (2026-08-04) -- see CLAUDE.md's "Broker-side stop
# order" section. Two mechanisms tested here:
#   1. poll_placed_orders() places a real T212 stop order the instant a BUY
#      row genuinely transitions to FILLED (when the config flag is on).
#   2. maybe_exit_positions()'s orphaned-stop cancel-fix -- a standing
#      broker-side stop is cancelled before a non-STOP_LOSS exit SELL is
#      placed, so it can never later fire against an unrelated FUTURE
#      position in the same ticker.
# ---------------------------------------------------------------------------

class _FakeHTTPStatus(Exception):
    """Minimal stand-in for a requests.exceptions.HTTPError carrying a
    specific status code on its .response -- mirrors the shape real T212
    client-method exceptions carry (see Trading212Client._request())."""

    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

    def __init__(self, status_code, msg="http error"):
        super().__init__(msg)
        self.response = self._Resp(status_code)


def _seed_open_position_with_broker_stop(
    engine, *, ticker, t212_ticker, stop_price, days_ago, is_etf,
    stop_order_id=None, stop_order_status=None, momentum_pct=0.05,
):
    """Same shape as _seed_open_position(), plus the broker-side
    stop-order tracking columns the cancel-fix reads from
    _load_open_position_context()."""
    entry_dt = datetime.utcnow() - timedelta(days=days_ago)
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, momentum_pct, stop_price, account_type, currency_code,
                 stop_order_id, stop_order_status, placed_at, filled_at, created_at)
            VALUES (:t, :tt, 'BUY', 10, 100.0, 10, 'FILLED', :etf, :mom, :stop, 'DEMO', 'USD',
                    :soid, :sostatus, :at, :at, :at)
        """), {"t": ticker, "tt": t212_ticker, "etf": is_etf, "mom": momentum_pct,
               "stop": stop_price, "soid": stop_order_id, "sostatus": stop_order_status,
               "at": entry_dt.isoformat()})


def test_exit_cancels_standing_stop_before_a_non_stop_loss_exit(db_engine, v2_mocks, mocker):
    """The core cancel-fix proof: a MAX_HOLDING exit (not STOP_LOSS) with a
    standing PLACED broker-side stop must cancel that stop BEFORE placing
    the exit SELL -- otherwise it could later fire against an unrelated
    future position in the same ticker."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()  # signal stays on -- only MAX_HOLDING should fire
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)
    v2_mocks.cancel_order.return_value = {}

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 0.01,  # far below price -- STOP_LOSS does NOT fire
        days_ago=999,                  # MAX_HOLDING fires instead
        is_etf=False, stop_order_id="stop-order-1", stop_order_status="PLACED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.cancel_order.assert_called_once_with("stop-order-1")
    # The exit SELL must still be placed -- MAX_HOLDING is a real,
    # safety-critical exit condition, unaffected by the cancel-fix.
    v2_mocks.place_market_order.assert_called_once()
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "MAX_HOLDING"
    stop_row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status, stop_order_note FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert stop_row[0] == "CANCELLED"
    assert "MAX_HOLDING" in stop_row[1]


def test_exit_stop_loss_does_not_cancel_the_standing_stop(db_engine, v2_mocks, mocker):
    """The other half of the cancel-fix's own logic: when the exit reason
    IS STOP_LOSS, the standing stop and this exit are the SAME protection
    firing -- there is nothing to cancel and no orphan risk, so
    cancel_order() must NOT be called at all."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bearish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=999999)

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 10,  # guarantees price <= stop_price -- STOP_LOSS fires
        days_ago=1, is_etf=False,
        stop_order_id="stop-order-2", stop_order_status="PLACED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.cancel_order.assert_not_called()
    v2_mocks.place_market_order.assert_called_once()
    row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM invest_orders WHERE direction='SELL'"
    )).fetchone()
    assert row[0] == "STOP_LOSS"
    # The BUY row's own stop-order tracking must be left completely
    # untouched -- no cancel attempt means no status change.
    stop_row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert stop_row[0] == "PLACED"


def test_exit_cancel_fix_is_a_no_op_when_no_standing_stop_exists(db_engine, v2_mocks, mocker):
    """Regression guard: a position with no broker-side stop at all (the
    normal case for every pre-existing test in this file, and for every
    position opened before this mechanism shipped) must behave EXACTLY as
    before -- no cancel_order() call, no change to exit behaviour."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=last_price * 0.01, days_ago=999, is_etf=False)

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.cancel_order.assert_not_called()
    v2_mocks.place_market_order.assert_called_once()


def test_exit_cancel_404_and_position_already_closed_skips_redundant_sell(db_engine, v2_mocks, mocker):
    """The confirmed T212 race: cancel_order() 404s (the standing stop
    already resolved -- most likely it fired first), and a fresh
    get_position() call shows the position genuinely already closed. The
    correct handling is to skip placing a redundant exit SELL entirely,
    not to guess."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()  # signal on -- only MAX_HOLDING fires
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)
    v2_mocks.cancel_order.side_effect = _FakeHTTPStatus(404, "order not found")
    v2_mocks.get_position.return_value = {"ticker": "AAPL_US_EQ", "quantity": 0.0}  # already closed

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 0.01, days_ago=999, is_etf=False,
        stop_order_id="stop-order-3", stop_order_status="PLACED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.cancel_order.assert_called_once_with("stop-order-3")
    v2_mocks.get_position.assert_called_once_with("AAPL_US_EQ")
    # No redundant SELL against an already-closed position.
    v2_mocks.place_market_order.assert_not_called()
    stop_row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert stop_row[0] == "ALREADY_RESOLVED"


def test_exit_cancel_404_but_position_still_open_places_the_sell_anyway(db_engine, v2_mocks, mocker):
    """The other branch of the same race: cancel_order() 404s, but a
    fresh get_position() check shows the position is genuinely STILL
    open (the stop resolved some other way, e.g. cancelled manually) --
    the exit SELL must still be placed as normal, not skipped."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)
    v2_mocks.cancel_order.side_effect = _FakeHTTPStatus(404, "order not found")
    v2_mocks.get_position.return_value = {"ticker": "AAPL_US_EQ", "quantity": 10.0}  # still open

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 0.01, days_ago=999, is_etf=False,
        stop_order_id="stop-order-4", stop_order_status="PLACED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_called_once()
    stop_row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert stop_row[0] == "ALREADY_RESOLVED"


def test_exit_cancel_genuine_failure_still_places_sell_and_alerts_loudly(db_engine, v2_mocks, mocker):
    """A cancel failure that is NOT "already resolved" (a genuine API
    error) must not block the safety-critical exit SELL -- the exit
    condition is real, so it proceeds regardless -- but this is exactly
    the orphan risk the cancel-fix exists to close, so it must alert
    loudly rather than fail silently."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)
    v2_mocks.cancel_order.side_effect = RuntimeError("simulated T212 outage")
    spy = mocker.patch("brokers.trading212._send_t212_error_email")

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 0.01, days_ago=999, is_etf=False,
        stop_order_id="stop-order-5", stop_order_status="PLACED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    # The exit SELL still goes out despite the cancel failure.
    v2_mocks.place_market_order.assert_called_once()
    stop_row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status, stop_order_note FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert stop_row[0] == "CANCEL_FAILED"
    assert "simulated T212 outage" in stop_row[1]
    # An alert about the FAILED CANCEL specifically, distinct from any
    # exit-SELL-failure alert this same run might also produce.
    cancel_alert_calls = [
        c for c in spy.call_args_list if c.kwargs.get("direction") == "STOP-CANCEL"
    ]
    assert len(cancel_alert_calls) == 1
    _, kwargs = cancel_alert_calls[0]
    assert kwargs["ticker"] == "AAPL"
    assert "stop-order-5" in kwargs["next_steps"]


def test_exit_cancel_fix_only_considers_a_stop_order_in_placed_status(db_engine, v2_mocks, mocker):
    """A stop_order_status of anything other than PLACED (e.g. already
    FAILED, or already CANCELLED from a prior cycle) must not trigger a
    fresh cancel attempt -- there is nothing standing left to cancel."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bullish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 0.01, days_ago=999, is_etf=False,
        stop_order_id="stop-order-6", stop_order_status="FAILED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.cancel_order.assert_not_called()
    v2_mocks.place_market_order.assert_called_once()


# ---------------------------------------------------------------------------
# Both stop-loss layers kept permanently (2026-08-04) -- the race between
# the broker's own standing stop firing and the local hourly check
# independently detecting the same breach. See CLAUDE.md's "Both stop-loss
# layers kept permanently" section for the full reasoning (the local check
# is kept, not removed, specifically because it's the only thing that can
# act -- without waiting for a human -- if the broker-side stop placement
# itself silently failed).
# ---------------------------------------------------------------------------

def test_exit_race_fresh_check_skips_when_broker_stop_already_closed_position(db_engine, v2_mocks, mocker):
    """The exact race named in the task: the local check independently
    evaluates STOP_LOSS (bypassing the orphan-cancel block entirely, since
    that block only runs for a NON-STOP_LOSS exit_reason -- see
    test_exit_stop_loss_does_not_cancel_the_standing_stop above), but by
    the time it's about to fire, the broker's OWN standing stop has
    already closed the position. The final fresh-position re-check must
    catch this and skip cleanly -- no exit SELL attempt, no FAILED order
    row, no alert."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bearish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=999999)
    # The broker's own stop has already fired between the top-of-function
    # get_positions() snapshot and now -- a fresh, single-ticker read shows
    # the position genuinely closed.
    v2_mocks.get_position.return_value = {"ticker": "AAPL_US_EQ", "quantity": 0.0}
    spy = mocker.patch("brokers.trading212._send_t212_error_email")

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 10,  # guarantees price <= stop_price -- STOP_LOSS fires locally
        days_ago=1, is_etf=False,
        stop_order_id="stop-order-race-1", stop_order_status="PLACED",
    )

    cfg = _config(mapping={"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.get_position.assert_called_once_with("AAPL_US_EQ")
    # No redundant SELL against an already-closed position -- a clean,
    # silent skip, not a doomed attempt.
    v2_mocks.place_market_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT COUNT(*) FROM invest_orders WHERE direction='SELL'"
    )).scalar()
    assert row == 0
    # And no confusing "sell failed" alert for what is actually the
    # protection working correctly.
    spy.assert_not_called()


def test_exit_race_sell_rejected_cleanly_when_broker_closes_position_after_fresh_check(db_engine, v2_mocks, mocker):
    """The narrower residual race the fresh check cannot fully close: the
    position is still open at the moment of the fresh re-check, but the
    broker's own stop fires in the brief window between that check and
    this system's own place_market_order() call. T212 rejects a market
    SELL for a quantity the account no longer holds -- confirms directly
    (not just reasoned about) that place_market_order() raising here is
    caught cleanly by the existing per-position try/except: no crash, no
    duplicate order, no short position, and every OTHER open position in
    the same run is still processed."""
    from brokers.trading212 import maybe_exit_positions

    frame = _bearish_frame()
    last_price = float(frame["close"].iloc[-1])
    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0},
        {"ticker": "MSFT_US_EQ", "quantity": 10.0},
    ]
    _mock_price_data(mocker, {"AAPL": frame, "MSFT": frame})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=999999)
    # The fresh re-check still shows the position open (the broker's stop
    # hasn't fired YET at the moment of this read) -- T212 itself is what
    # rejects the SELL a moment later, simulating the broker's stop
    # winning the race in the gap between this check and the SELL call.
    v2_mocks.get_position.return_value = {"ticker": "AAPL_US_EQ", "quantity": 10.0}

    def _order_side_effect(t212_ticker, quantity, extended_hours=False):
        if t212_ticker == "AAPL_US_EQ":
            raise _FakeHTTPStatus(400, "insufficient shares")
        return {"id": "mock-order-msft"}

    v2_mocks.place_market_order.side_effect = _order_side_effect

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
        stop_price=last_price * 10, days_ago=1, is_etf=False,
        stop_order_id="stop-order-race-2", stop_order_status="PLACED",
    )
    _seed_open_position(db_engine, ticker="MSFT", t212_ticker="MSFT_US_EQ",
                         stop_price=last_price * 10, days_ago=1, is_etf=False)

    cfg = _config(
        mapping={
            "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
            "MSFT": {"t212_instrument_id": "MSFT_US_EQ", "currency": "USD", "is_etf": False},
        },
        mocker=mocker, db_engine=db_engine,
    )
    maybe_exit_positions(cfg)  # must not raise

    rows = db_engine.connect().execute(sa_text(
        "SELECT ticker, status FROM invest_orders WHERE direction='SELL' ORDER BY ticker"
    )).fetchall()
    by_ticker = {r[0]: r[1] for r in rows}
    # The raced position's SELL attempt is recorded FAILED -- a genuine,
    # cleanly-handled rejection, not a crash and not a duplicate order.
    assert by_ticker["AAPL"] == "FAILED"
    # No short position was created and no second attempt was made this
    # cycle -- exactly one SELL order row for AAPL.
    assert sum(1 for r in rows if r[0] == "AAPL") == 1
    # The OTHER open position in the same run is completely unaffected --
    # per-position exception isolation holds for this failure mode too.
    assert by_ticker["MSFT"] == "PLACED"


# ---------------------------------------------------------------------------
# poll_placed_orders() — broker-side stop placement on a genuine BUY fill
# ---------------------------------------------------------------------------

def _seed_placed_buy_order(engine, *, ticker, t212_ticker, order_id, quantity, stop_price, currency="USD"):
    """A PLACED BUY row (not yet FILLED) -- the state poll_placed_orders()
    reads and, on a genuine FILLED transition, may place a broker-side
    stop against."""
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (order_id, ticker, t212_ticker, direction, quantity, status,
                 is_etf, account_type, currency_code, stop_price, placed_at, created_at)
            VALUES (:oid, :t, :tt, 'BUY', :qty, 'PLACED', 0, 'DEMO', :ccy, :stop, :now, :now)
        """), {"oid": order_id, "t": ticker, "tt": t212_ticker, "qty": quantity,
               "ccy": currency, "stop": stop_price, "now": datetime.utcnow().isoformat()})


def _poll_config(enabled_stop=True) -> dict:
    return {
        "trading212_invest": {
            "enabled": True, "live": False,
            "broker_side_stop_enabled": enabled_stop,
        },
        "notifications": {"email": {"enabled": False}},
    }


def _mock_fx_conversion(mocker) -> None:
    """Mocks Trading212Client's two static FX-conversion helpers so the
    real _to_gbp() math runs deterministically instead of returning a
    MagicMock (which breaks the SQL UPDATE parameter binding the moment a
    test uses a real, non-None fill_price) -- every poll_placed_orders()
    test below that exercises a genuine FILLED transition with a real
    fillPrice needs this."""
    from brokers.trading212 import Trading212Client
    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch.object(
        Trading212Client, "_to_gbp",
        staticmethod(lambda price, currency, gbpusd, eurusd: price / gbpusd if gbpusd else None),
    )


def test_poll_places_broker_stop_when_buy_transitions_to_filled(db_engine, v2_mocks, mocker):
    """The core placement proof: a BUY row genuinely resolving to FILLED,
    with the flag enabled, must place a real stop order sized to the
    REAL filled quantity and at the row's own already-computed
    stop_price -- and record the resulting order id/status on that same
    BUY row."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-1", quantity=10.0, stop_price=140.0)

    v2_mocks.get_order.return_value = {
        "status": "FILLED", "fillPrice": 150.0, "filledQuantity": 10.0,
    }
    v2_mocks.place_stop_order.return_value = {"id": "stop-order-99"}

    updated = poll_placed_orders(_poll_config())
    assert updated == 1

    v2_mocks.place_stop_order.assert_called_once_with("AAPL_US_EQ", -10.0, 140.0)

    row = db_engine.connect().execute(sa_text(
        "SELECT status, stop_order_id, stop_order_status FROM invest_orders WHERE order_id='buy-order-1'"
    )).fetchone()
    assert row[0] == "FILLED"
    assert row[1] == "stop-order-99"
    assert row[2] == "PLACED"


def test_poll_stop_placement_sizes_to_the_real_filled_quantity_not_the_intended_one(db_engine, v2_mocks, mocker):
    """A partial fill (filledQuantity < the originally queued quantity)
    must size the stop order against the REAL filled amount -- otherwise
    the stop would protect a quantity the account doesn't actually
    hold."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-2", quantity=10.0, stop_price=140.0)

    v2_mocks.get_order.return_value = {
        "status": "FILLED", "fillPrice": 150.0, "filledQuantity": 6.5,  # partial fill
    }
    v2_mocks.place_stop_order.return_value = {"id": "stop-order-partial"}

    poll_placed_orders(_poll_config())

    v2_mocks.place_stop_order.assert_called_once_with("AAPL_US_EQ", -6.5, 140.0)


def test_poll_does_not_place_a_stop_when_the_flag_is_disabled(db_engine, v2_mocks, mocker):
    """The mechanism's own config gate -- with broker_side_stop_enabled
    False (the shipped default), a genuine BUY fill must NOT place any
    stop order at all, even though every other condition is met."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-3", quantity=10.0, stop_price=140.0)

    v2_mocks.get_order.return_value = {
        "status": "FILLED", "fillPrice": 150.0, "filledQuantity": 10.0,
    }

    updated = poll_placed_orders(_poll_config(enabled_stop=False))
    assert updated == 1  # the BUY fill itself is still recorded normally

    v2_mocks.place_stop_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_id, stop_order_status FROM invest_orders WHERE order_id='buy-order-3'"
    )).fetchone()
    assert row[0] is None
    assert row[1] is None


def test_poll_does_not_place_a_stop_for_a_sell_transitioning_to_filled(db_engine, v2_mocks, mocker):
    """Only a BUY's own fill is a candidate for a NEW protective stop --
    a SELL resolving to FILLED is a position CLOSING, not opening, and
    must never trigger a stop-placement attempt."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (order_id, ticker, t212_ticker, direction, quantity, status,
                 is_etf, account_type, currency_code, stop_price, placed_at, created_at)
            VALUES ('sell-order-1', 'AAPL', 'AAPL_US_EQ', 'SELL', 10, 'PLACED',
                    0, 'DEMO', 'USD', 140.0, :now, :now)
        """), {"now": datetime.utcnow().isoformat()})

    v2_mocks.get_order.return_value = {
        "status": "FILLED", "fillPrice": 155.0, "filledQuantity": 10.0,
    }

    poll_placed_orders(_poll_config())

    v2_mocks.place_stop_order.assert_not_called()


def test_poll_stop_placement_failure_is_recorded_and_alerts_but_never_blocks_the_fill(db_engine, v2_mocks, mocker):
    """A stop-placement failure must never be silent: the BUY row's own
    fill is still correctly recorded (the position genuinely opened --
    that fact must not be lost), stop_order_status is set to FAILED with
    the real error message, and a loud alert fires naming the ticker and
    stating the position currently has no broker-side stop."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-4", quantity=10.0, stop_price=140.0)

    v2_mocks.get_order.return_value = {
        "status": "FILLED", "fillPrice": 150.0, "filledQuantity": 10.0,
    }
    v2_mocks.place_stop_order.side_effect = RuntimeError("simulated T212 rejection")
    spy = mocker.patch("brokers.trading212._send_t212_error_email")

    updated = poll_placed_orders(_poll_config())
    assert updated == 1  # the BUY fill itself is still recorded

    row = db_engine.connect().execute(sa_text(
        "SELECT status, stop_order_id, stop_order_status, stop_order_note "
        "FROM invest_orders WHERE order_id='buy-order-4'"
    )).fetchone()
    assert row[0] == "FILLED"       # the fill itself is unaffected
    assert row[1] is None           # no order id -- placement never succeeded
    assert row[2] == "FAILED"
    assert "simulated T212 rejection" in row[3]

    spy.assert_called_once()
    _, kwargs = spy.call_args
    assert kwargs["ticker"] == "AAPL"
    assert kwargs["direction"] == "STOP"
    assert "NO broker-side stop" in kwargs["next_steps"]


def test_poll_does_not_attempt_stop_placement_when_row_has_no_stop_price(db_engine, v2_mocks, mocker):
    """Defensive guard: a BUY row with no stop_price at all (should not
    occur under the current pipeline, but checked anyway) must not
    attempt a stop placement with a missing/invalid price."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-5", quantity=10.0, stop_price=None)

    v2_mocks.get_order.return_value = {
        "status": "FILLED", "fillPrice": 150.0, "filledQuantity": 10.0,
    }

    poll_placed_orders(_poll_config())

    v2_mocks.place_stop_order.assert_not_called()


def test_poll_does_not_place_a_stop_when_buy_resolves_to_cancelled(db_engine, v2_mocks, mocker):
    """A BUY that never actually fills -- resolves to CANCELLED instead --
    must never trigger a stop-placement attempt. True by construction
    (the placement condition explicitly requires new_status == 'FILLED'),
    confirmed here with a real test rather than assumed from the logic's
    shape."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-cancelled", quantity=10.0, stop_price=140.0)

    v2_mocks.get_order.return_value = {"status": "CANCELLED"}

    updated = poll_placed_orders(_poll_config())
    assert updated == 1  # the CANCELLED status itself is still recorded

    v2_mocks.place_stop_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT status, stop_order_id, stop_order_status FROM invest_orders WHERE order_id='buy-order-cancelled'"
    )).fetchone()
    assert row[0] == "CANCELLED"
    assert row[1] is None
    assert row[2] is None


def test_poll_does_not_place_a_stop_when_buy_resolves_to_rejected(db_engine, v2_mocks, mocker):
    """Same guarantee as the CANCELLED case above, for a BUY T212 rejects
    outright (e.g. no real market to fill into) -- must never trigger a
    stop-placement attempt."""
    from brokers.trading212 import poll_placed_orders

    _mock_fx_conversion(mocker)
    _seed_placed_buy_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                            order_id="buy-order-rejected", quantity=10.0, stop_price=140.0)

    v2_mocks.get_order.return_value = {"status": "REJECTED"}

    updated = poll_placed_orders(_poll_config())
    assert updated == 1  # the REJECTED status itself is still recorded

    v2_mocks.place_stop_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT status, stop_order_id, stop_order_status FROM invest_orders WHERE order_id='buy-order-rejected'"
    )).fetchone()
    assert row[0] == "REJECTED"
    assert row[1] is None
    assert row[2] is None


# ---------------------------------------------------------------------------
# retry_missing_broker_stops() (2026-08-04) -- catches a FILLED BUY whose
# stop-placement attempt never even ran (e.g. a crash/restart between
# poll_placed_orders()'s own DB commit and its stop-placement call). See
# CLAUDE.md's "buy-then-stop sequencing" section for the full gap this
# closes -- poll_placed_orders() itself can never retry this, since its
# own query only ever selects status='PLACED' rows, and this row has
# already moved past that.
# ---------------------------------------------------------------------------

def _retry_config(enabled=True, enabled_stop=True) -> dict:
    return {
        "trading212_invest": {
            "enabled": enabled, "live": False,
            "broker_side_stop_enabled": enabled_stop,
        },
        "notifications": {"email": {"enabled": False}},
    }


def test_retry_missing_broker_stops_places_stop_for_a_never_attempted_filled_buy(db_engine, v2_mocks, mocker):
    """The core proof: a FILLED BUY row with stop_order_status genuinely
    NULL (never attempted -- simulating the crash/restart window between
    poll_placed_orders()'s DB commit and its own stop-placement call)
    must have its stop placed on the next retry pass, sized to a FRESH
    live quantity read (not the possibly-stale filled_quantity column)."""
    from brokers.trading212 import retry_missing_broker_stops

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=1, is_etf=False, stop_order_id=None, stop_order_status=None,
    )
    v2_mocks.get_position.return_value = {"ticker": "AAPL_US_EQ", "quantity": 10.0}
    v2_mocks.place_stop_order.return_value = {"id": "stop-order-retry-1"}

    attempted = retry_missing_broker_stops(_retry_config())
    assert attempted == 1

    v2_mocks.place_stop_order.assert_called_once_with("AAPL_US_EQ", -10.0, 140.0)
    row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_id, stop_order_status FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert row[0] == "stop-order-retry-1"
    assert row[1] == "PLACED"


def test_retry_missing_broker_stops_skips_a_position_no_longer_held(db_engine, v2_mocks, mocker):
    """A never-attempted stop for a position that has since closed through
    some other path must not attempt a placement against a position that
    no longer exists -- skip silently; reconcile_positions_with_broker()
    is responsible for correctly labelling that closure."""
    from brokers.trading212 import retry_missing_broker_stops

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=1, is_etf=False, stop_order_id=None, stop_order_status=None,
    )
    v2_mocks.get_position.return_value = {"ticker": "AAPL_US_EQ", "quantity": 0.0}

    attempted = retry_missing_broker_stops(_retry_config())
    assert attempted == 0

    v2_mocks.place_stop_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status FROM invest_orders WHERE direction='BUY'"
    )).fetchone()
    assert row[0] is None  # left untouched, not falsely marked


def test_retry_missing_broker_stops_does_not_touch_a_row_already_attempted(db_engine, v2_mocks, mocker):
    """A row with ANY non-NULL stop_order_status (PLACED, FAILED, ...) has
    already had a genuine attempt made -- and, for FAILED specifically,
    already been alerted at the time of that failure. This retry pass
    must only ever act on a row that was never attempted at all, not
    re-attempt an already-attempted-and-failed one automatically."""
    from brokers.trading212 import retry_missing_broker_stops

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=1, is_etf=False, stop_order_id="stop-order-existing", stop_order_status="PLACED",
    )
    _seed_open_position_with_broker_stop(
        db_engine, ticker="MSFT", t212_ticker="MSFT_US_EQ", stop_price=200.0,
        days_ago=1, is_etf=False, stop_order_id=None, stop_order_status="FAILED",
    )

    attempted = retry_missing_broker_stops(_retry_config())
    assert attempted == 0

    v2_mocks.get_position.assert_not_called()
    v2_mocks.place_stop_order.assert_not_called()


def test_retry_missing_broker_stops_is_a_noop_when_flag_disabled(db_engine, v2_mocks, mocker):
    from brokers.trading212 import retry_missing_broker_stops

    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=1, is_etf=False, stop_order_id=None, stop_order_status=None,
    )

    attempted = retry_missing_broker_stops(_retry_config(enabled_stop=False))
    assert attempted == 0
    v2_mocks.place_stop_order.assert_not_called()


def test_retry_missing_broker_stops_disabled_or_missing_creds_is_noop(db_engine, v2_mocks, monkeypatch):
    from brokers.trading212 import retry_missing_broker_stops

    assert retry_missing_broker_stops(_retry_config(enabled=True, enabled_stop=False)) == 0

    monkeypatch.delenv("T212_API_KEY", raising=False)
    assert retry_missing_broker_stops(_retry_config()) == 0
    v2_mocks.get_positions.assert_not_called()


def _n_ticker_mapping(n: int) -> dict:
    return {
        f"T{i}": {"t212_instrument_id": f"T{i}_EQ", "currency": "USD", "is_etf": True}
        for i in range(n)
    }


# ---------------------------------------------------------------------------
# check_stop_health() (2026-08-04) -- catches a broker-side stop that was
# believed active (invest_orders.stop_order_status = 'PLACED') but has
# silently stopped being genuinely active at T212 -- the exact shape
# tonight's incident took (all 16 stops silently rejected hours after
# placement), only caught because a T212 notification happened to be seen
# externally. Reuses _seed_open_position_with_broker_stop() and v2_mocks
# from the cancel-fix section above. Efficiency is verified directly (real
# call counts), not assumed: the whole point of get_orders() (a single
# list call) over a naive per-position get_order(id) loop is that total
# new API load stays fixed at 2 calls/hour (get_positions + get_orders)
# regardless of how many positions are open.
# ---------------------------------------------------------------------------

def _stop_health_config(mapping, mocker, db_engine, broker_stop_enabled=True) -> dict:
    cfg = _config(mapping=mapping, mocker=mocker, db_engine=db_engine)
    cfg["trading212_invest"]["broker_side_stop_enabled"] = broker_stop_enabled
    cfg["notifications"] = {"email": {"enabled": False}}
    return cfg


def test_stop_health_healthy_stop_produces_no_alert(db_engine, v2_mocks, mocker):
    from brokers.trading212 import check_stop_health

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-1", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    v2_mocks.get_orders.return_value = [
        {"id": "stop-order-health-1", "status": "NEW", "ticker": "AAPL_US_EQ"},
    ]
    alert_mock = mocker.patch("output.briefing.send_stop_health_alert")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert unhealthy == []
    # Still called with the empty list -- this is what clears any
    # previously-recorded unhealthy set on genuine recovery (see
    # send_stop_health_alert()'s own docstring for why the caller must
    # always pass the current list, even when empty).
    alert_mock.assert_called_once_with([], mocker.ANY)


def test_stop_health_missing_stop_triggers_a_correctly_targeted_alert(db_engine, v2_mocks, mocker):
    """The core proof: a locally-PLACED stop that T212's real order list
    no longer shows must be flagged, naming the specific ticker and
    stop_order_id -- not a generic 'something's wrong'."""
    from brokers.trading212 import check_stop_health

    mapping = {"KLAC": {"t212_instrument_id": "KLAC_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_open_position_with_broker_stop(
        db_engine, ticker="KLAC", t212_ticker="KLAC_US_EQ", stop_price=150.0,
        days_ago=1, is_etf=False, stop_order_id="stop-order-health-2", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.return_value = [{"ticker": "KLAC_US_EQ", "quantity": 10.0}]
    # The stop is simply absent from T212's live order list -- exactly
    # tonight's real shape (silently rejected, no longer resting).
    v2_mocks.get_orders.return_value = []
    alert_mock = mocker.patch("output.briefing.send_stop_health_alert")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert len(unhealthy) == 1
    assert unhealthy[0]["ticker"] == "KLAC"
    assert unhealthy[0]["t212_ticker"] == "KLAC_US_EQ"
    assert unhealthy[0]["stop_order_id"] == "stop-order-health-2"
    alert_mock.assert_called_once_with(unhealthy, mocker.ANY)


def test_stop_health_a_healthy_and_an_unhealthy_position_together_only_flags_the_unhealthy_one(
    db_engine, v2_mocks, mocker,
):
    """Two open positions, only one with a real gap -- proves the check
    doesn't over-flag a healthy neighbour just because a different
    ticker's stop went bad."""
    from brokers.trading212 import check_stop_health

    mapping = {
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
        "KLAC": {"t212_instrument_id": "KLAC_US_EQ", "currency": "USD", "is_etf": False},
    }
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-3a", stop_order_status="PLACED",
    )
    _seed_open_position_with_broker_stop(
        db_engine, ticker="KLAC", t212_ticker="KLAC_US_EQ", stop_price=150.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-3b", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.return_value = [
        {"ticker": "AAPL_US_EQ", "quantity": 10.0},
        {"ticker": "KLAC_US_EQ", "quantity": 10.0},
    ]
    # Only AAPL's stop is genuinely still active; KLAC's is missing.
    v2_mocks.get_orders.return_value = [{"id": "stop-order-health-3a", "status": "NEW"}]
    mocker.patch("output.briefing.send_stop_health_alert")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert len(unhealthy) == 1
    assert unhealthy[0]["ticker"] == "KLAC"


def test_stop_health_position_already_closed_is_not_flagged(db_engine, v2_mocks, mocker):
    """A local row still shows stop_order_status='PLACED', but T212 no
    longer shows the position held at all (closed via some other real
    path already -- reconciliation's own job, not this check's). Must
    not be treated as a missing-stop gap."""
    from brokers.trading212 import check_stop_health

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-4", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.return_value = []  # nothing genuinely held any more
    v2_mocks.get_orders.return_value = []
    mocker.patch("output.briefing.send_stop_health_alert")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert unhealthy == []


def test_stop_health_non_placed_local_status_is_not_checked(db_engine, v2_mocks, mocker):
    """A row with stop_order_status other than 'PLACED' (FAILED,
    CANCELLED, ALREADY_RESOLVED, CANCEL_FAILED, or never attempted at
    all) is a different, already-alerted-elsewhere state -- this check's
    scope is specifically 'believed active, silently isn't', not every
    non-PLACED status."""
    from brokers.trading212 import check_stop_health

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-5", stop_order_status="FAILED",
    )
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 10.0}]
    v2_mocks.get_orders.return_value = []
    mocker.patch("output.briefing.send_stop_health_alert")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert unhealthy == []


def test_stop_health_disabled_flag_is_a_complete_noop_no_api_calls_at_all(db_engine, v2_mocks, mocker):
    from brokers.trading212 import check_stop_health

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-6", stop_order_status="PLACED",
    )

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine, broker_stop_enabled=False))

    assert unhealthy == []
    v2_mocks.get_positions.assert_not_called()
    v2_mocks.get_orders.assert_not_called()


def test_stop_health_never_raises_on_a_broker_read_failure(db_engine, v2_mocks, mocker):
    from brokers.trading212 import check_stop_health

    mapping = {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False}}
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=140.0,
        days_ago=5, is_etf=False, stop_order_id="stop-order-health-7", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.side_effect = RuntimeError("T212 unreachable")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert unhealthy == []  # never raises -- a check-the-checker failure isn't a new incident


def test_stop_health_efficiency_exactly_two_calls_regardless_of_position_count(db_engine, v2_mocks, mocker):
    """The core efficiency proof this task exists to satisfy: real,
    counted API calls -- not assumed -- stay fixed at exactly ONE
    get_positions() call and ONE get_orders() call for the whole hourly
    check, whether 1 or many positions are open. Confirms the naive
    per-position approach (one get_order(id) call per position) was
    NOT used -- get_order() must never be called by this check at all."""
    from brokers.trading212 import check_stop_health

    n = 5
    mapping = {
        f"T{i}": {"t212_instrument_id": f"T{i}_US_EQ", "currency": "USD", "is_etf": False}
        for i in range(n)
    }
    for i in range(n):
        _seed_open_position_with_broker_stop(
            db_engine, ticker=f"T{i}", t212_ticker=f"T{i}_US_EQ", stop_price=100.0,
            days_ago=5, is_etf=False, stop_order_id=f"stop-order-eff-{i}", stop_order_status="PLACED",
        )
    v2_mocks.get_positions.return_value = [
        {"ticker": f"T{i}_US_EQ", "quantity": 10.0} for i in range(n)
    ]
    v2_mocks.get_orders.return_value = [
        {"id": f"stop-order-eff-{i}", "status": "NEW"} for i in range(n)
    ]
    mocker.patch("output.briefing.send_stop_health_alert")

    unhealthy = check_stop_health(_stop_health_config(mapping, mocker, db_engine))

    assert unhealthy == []
    assert v2_mocks.get_positions.call_count == 1
    assert v2_mocks.get_orders.call_count == 1
    v2_mocks.get_order.assert_not_called()


def test_stop_health_check_wired_into_market_hours_check_job(mocker):
    """Confirms this actually runs from Open Trade Monitor's own hourly
    job -- folded into the existing cadence, not a new scheduled job --
    per this task's own explicit instruction, and that it runs AFTER
    both poll_placed_orders() and maybe_exit_positions() (so a same-cycle
    fresh stop placement or a same-cycle exit isn't mistaken for a
    pre-existing gap)."""
    from data import scheduler as sched

    mocker.patch("data.scheduler._is_nyse_extended_hours_open", return_value=True)
    mocker.patch("data.scheduler._load_config", return_value={})
    poll_mock = mocker.patch("brokers.trading212.poll_placed_orders")
    exit_mock = mocker.patch("brokers.trading212.maybe_exit_positions")
    health_mock = mocker.patch("brokers.trading212.check_stop_health")

    sched._market_hours_check_job()

    poll_mock.assert_called_once()
    exit_mock.assert_called_once()
    health_mock.assert_called_once()


def test_stop_health_check_failure_never_blocks_the_rest_of_the_job(mocker):
    from data import scheduler as sched

    mocker.patch("data.scheduler._is_nyse_extended_hours_open", return_value=True)
    mocker.patch("data.scheduler._load_config", return_value={})
    mocker.patch("brokers.trading212.poll_placed_orders")
    mocker.patch("brokers.trading212.maybe_exit_positions")
    mocker.patch("brokers.trading212.check_stop_health", side_effect=RuntimeError("boom"))

    sched._market_hours_check_job()  # must not raise


# ---------------------------------------------------------------------------
# 30-day re-entry cooldown, live side (2026-08-03) -- _load_open_order_
# tickers() is live's own half of the shared analysis.decision.
# REENTRY_COOLDOWN_DAYS boundary; see tests/test_decision.py's pure
# is_reentry_blocked() tests and tests/test_portfolio_backtest.py's
# MTM-engine-level proof for the other two legs of the same rule.
# ---------------------------------------------------------------------------

def test_load_open_order_tickers_default_matches_shared_reentry_constant():
    from brokers.trading212 import _load_open_order_tickers
    from analysis.decision import REENTRY_COOLDOWN_DAYS
    import inspect
    default = inspect.signature(_load_open_order_tickers).parameters["days"].default
    assert default == REENTRY_COOLDOWN_DAYS == 30


def test_load_open_order_tickers_boundary_matches_backtests_own_boundary(db_engine):
    """A real DB-backed confirmation that live's SQL-day-count and the
    backtest's Python day-count agree at the identical 30-day boundary --
    an order 29 days old is still within the cooldown (ticker returned,
    entry blocked); an order 31 days old is not (ticker not returned,
    entry allowed) -- exactly matching is_reentry_blocked(29)=True /
    is_reentry_blocked(31)=False proven in isolation in test_decision.py."""
    from brokers.trading212 import _load_open_order_tickers

    within_window = (datetime.utcnow() - timedelta(days=29)).isoformat()
    past_window = (datetime.utcnow() - timedelta(days=31)).isoformat()
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('RECENT', 'RECENT_EQ', 'SELL', 'FILLED', :ts, :ts)
        """), {"ts": within_window})
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('OLD', 'OLD_EQ', 'SELL', 'FILLED', :ts, :ts)
        """), {"ts": past_window})

    blocked = _load_open_order_tickers()

    assert "RECENT" in blocked   # 29 days -- still within cooldown
    assert "OLD" not in blocked  # 31 days -- cooldown cleared


def test_load_open_order_tickers_config_driven_zero_permits_recent_reentry(db_engine):
    """2026-08-03: production sets trading212_invest.reentry_cooldown_days
    to 0 -- a ticker with an order placed moments ago must no longer be
    treated as cooling down once that config value is resolved and
    passed through as days=0, even though the same row would still be
    blocked under the 30-day fallback default proven above."""
    from brokers.trading212 import _load_open_order_tickers
    from analysis.decision import resolve_reentry_cooldown_days

    # Space-separated (matching SQLAlchemy's real placed_at storage format,
    # "YYYY-MM-DD HH:MM:SS.ffffff") -- an isoformat() T-separated string
    # would sort *after* SQLite's own datetime('now') at the same second
    # (see CLAUDE.md's "T212 P&L reconstruction" section on this exact
    # same-timestamp string-comparison pitfall), which would make even a
    # days=0 window spuriously still "contain" a just-inserted row.
    just_now = (datetime.utcnow() - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S.%f")
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('JUSTNOW', 'JUSTNOW_EQ', 'SELL', 'FILLED', :ts, :ts)
        """), {"ts": just_now})

    cooldown_days = resolve_reentry_cooldown_days({"trading212_invest": {"reentry_cooldown_days": 0}})
    assert cooldown_days == 0

    blocked_default = _load_open_order_tickers()
    assert "JUSTNOW" in blocked_default  # unchanged 30-day fallback still blocks it

    blocked_zero = _load_open_order_tickers(days=cooldown_days)
    assert "JUSTNOW" not in blocked_zero  # config-resolved 0-day cooldown does not


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
# at decide time against a genuinely live price at submission time.
#
# 2026-08-02: the live price read moved from get_price_df() (the stored,
# EOD-only price_data table -- structurally unable to reflect any move
# within the same trading day, since it's never updated intraday) to
# _live_current_price() (a real yfinance quote). Tests below mock
# _live_current_price() directly, the correct boundary now -- mocking
# get_price_df() as these tests originally did would no longer control
# what the sanity check actually sees.
#
# 2026-08-03: ASYMMETRIC -- blocks only on a DROP past the threshold, never
# on a rise of any size (see CLAUDE.md's "Price-sanity threshold --
# momentum-skew investigation" section for the 176-run block-based evidence
# this is based on). The two tests below that used to construct their
# "moved beyond threshold" scenario with a RISE (decided_price below the
# current price) were updated to use a genuine DROP instead, to keep
# actually testing "does an over-threshold move still block" now that only
# one direction can trigger it -- their own original intent (prove the
# check fires / prove it reads the live quote, not stale data) is
# unaffected by which direction is used, so this is a like-for-like
# substitution, not a behavioural relaxation of either test.
# ---------------------------------------------------------------------------

def test_sanity_check_skips_submission_when_price_dropped_beyond_threshold(db_engine, v2_mocks, mocker):
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    decided_price = last_close * 1.10  # decided price is 10% above "current" -- a genuine drop, well past 3%
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})
    _mock_live_price_for(mocker, "AAPL", last_close)

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
    decided_price = last_close / 1.01  # ~1% rise -- comfortably inside the 3% threshold either way
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})
    _mock_live_price_for(mocker, "AAPL", last_close)

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()
    assert len(_pending_rows(db_engine, status="SUBMITTED")) == 1


def test_sanity_check_uses_live_price_not_stale_stored_close(db_engine, v2_mocks, mocker):
    """The decisive regression test for the 2026-08-02 fix: price_data's
    stored close is set to a value that would NOT trip the sanity check if
    (incorrectly) still used as "current price", while the genuinely live
    quote is set to a value that WOULD trip it. The check must follow the
    live quote, proving it no longer reads the stale stored table at all.
    Uses a genuine drop (not the original rise) so this still actually
    trips the now-asymmetric check -- the point being tested (live vs.
    stale data source) is orthogonal to direction."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    stale_stored_close = float(frame["close"].iloc[-1])
    decided_price = stale_stored_close  # decided price matches the stale stored close exactly
    live_price = stale_stored_close * 0.90  # but the real live quote has DROPPED 10% -- past the 3% threshold
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})
    _mock_live_price_for(mocker, "AAPL", live_price)

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="SKIPPED_PRICE_MOVED")
    assert len(rows) == 1


def test_sanity_check_asymmetric_a_rise_never_blocks_regardless_of_size(db_engine, v2_mocks, mocker):
    """The core 2026-08-03 behavioural change: a price RISE since the
    decision -- even a large one, well past the 3% threshold -- must never
    block submission. A rise confirms the momentum thesis rather than
    invalidating it (see CLAUDE.md's momentum-skew investigation)."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    decided_price = last_close / 1.20  # a 20% RISE since decision -- far past 3%, but a rise, not a drop
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})
    _mock_live_price_for(mocker, "AAPL", last_close)

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 1, "a price rise, regardless of size, must never block submission"
    v2_mocks.place_market_order.assert_called_once()
    assert len(_pending_rows(db_engine, status="SUBMITTED")) == 1
    assert len(_pending_rows(db_engine, status="SKIPPED_PRICE_MOVED")) == 0


def test_sanity_check_drop_just_under_threshold_does_not_block(db_engine, v2_mocks, mocker):
    """Boundary case: a drop of 2.9% (just under the 3% threshold) must
    still allow submission -- confirms the boundary is a genuine threshold
    comparison, not an off-by-something that blocks too eagerly. (The
    EXACT boundary -- a drop of precisely 3.00% -- is covered robustly at
    the pure-function level in tests/test_decision.py instead of here:
    constructing an exact-percentage price via division in this
    integration test hit real floating-point rounding that made a
    "precisely -3.00%" move compute as -3.0000000000000016%, an artifact
    of the test's own arithmetic, not the production code -- confirmed
    directly before removing that integration-level variant.)"""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    decided_price = last_close / 0.971  # current price is ~2.9% below decided_price -- just under 3%
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})
    _mock_live_price_for(mocker, "AAPL", last_close)

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()
    assert len(_pending_rows(db_engine, status="SKIPPED_PRICE_MOVED")) == 0


def test_sanity_check_drop_just_over_threshold_blocks(db_engine, v2_mocks, mocker):
    """The other side of the same boundary: a drop of 3.1% (just over the
    3% threshold) must block."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    last_close = float(frame["close"].iloc[-1])
    decided_price = last_close / 0.969  # current price is ~3.1% below decided_price -- just over 3%
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=decided_price)
    _mock_price_data(mocker, {"AAPL": frame})
    _mock_live_price_for(mocker, "AAPL", last_close)

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="SKIPPED_PRICE_MOVED")) == 1


def test_live_current_price_returns_none_on_failure_leaves_pending(db_engine, v2_mocks, mocker):
    """A live-quote failure must fail loud -- leave the row PENDING for
    retry, never fall back to a stale value that would silently reproduce
    the exact bug this fix closes."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=float(frame["close"].iloc[-1]))
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("brokers.trading212._live_current_price", return_value=None)

    n = submit_pending_stock_orders(_submission_config(threshold=0.03))

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1


def test_live_current_price_uses_yfinance_fast_info_and_handles_failure(mocker):
    """Direct unit test of _live_current_price() itself -- confirms it
    reads yfinance's fast_info.last_price (not get_price_df()), returns
    None on a non-positive/missing price, and never raises on a genuine
    fetch failure."""
    from brokers.trading212 import _live_current_price

    mock_ticker = mocker.MagicMock()
    mock_ticker.fast_info.last_price = 123.45
    mocker.patch("yfinance.Ticker", return_value=mock_ticker)
    assert _live_current_price("AAPL") == 123.45

    mock_ticker.fast_info.last_price = None
    assert _live_current_price("AAPL") is None

    mock_ticker.fast_info.last_price = 0.0
    assert _live_current_price("AAPL") is None

    mocker.patch("yfinance.Ticker", side_effect=RuntimeError("network down"))
    assert _live_current_price("AAPL") is None  # must not raise


def test_live_current_price_converts_dot_ticker_to_hyphen_for_yfinance(mocker):
    """BRK.B / BF.B (real S&P 500 constituents, dot share-class notation)
    must be converted to yfinance's hyphen notation (BRK-B) before the
    yfinance call -- confirmed live (2026-08-05) that yf.Ticker("BRK.B")
    returns no usable price at all, silently, while yf.Ticker("BRK-B")
    works. This is the same conversion data/ingestion.py:_yf_symbol()
    applies for price history; this call site needs it independently
    since it calls yfinance directly."""
    from brokers.trading212 import _live_current_price

    mock_ticker = mocker.MagicMock()
    mock_ticker.fast_info.last_price = 555.0
    ticker_ctor = mocker.patch("yfinance.Ticker", return_value=mock_ticker)

    assert _live_current_price("BRK.B") == 555.0
    ticker_ctor.assert_called_once_with("BRK-B")


# ---------------------------------------------------------------------------
# Submission-time crisis re-check (2026-08-03) -- passes_entry_gate()'s
# crisis block is only ever evaluated once, at EOD decide time. Stock
# submission fires up to ~17 hours later; without a fresh re-check, a
# genuine crisis developing between decide and submission would never
# stop yesterday's now-stale BUY decisions from still going out.
# ---------------------------------------------------------------------------

def test_is_crisis_mode_active_now_direct_true_false_and_none(db_engine, mocker):
    """Direct unit test of the shared authoritative function itself --
    both _submit_pending_orders() and the Dashboard's crisis banner call
    this exact function, so its own True/False/None behaviour is worth
    proving in isolation from either caller."""
    from brokers.trading212 import is_crisis_mode_active_now

    gspc_frame = _bullish_frame()
    gspc_last_close = float(gspc_frame["close"].iloc[-1])
    _mock_price_data(mocker, {"^GSPC": gspc_frame})

    # No crisis anywhere -- False.
    assert is_crisis_mode_active_now() is False

    # A live quote down >=7% from the stored close, not yet a daily bar -- True.
    mocker.patch("brokers.trading212._live_current_price", return_value=gspc_last_close * 0.90)
    assert is_crisis_mode_active_now() is True

    # A read failure -- None, never guessed as either True or False.
    mocker.patch("brokers.trading212._load_gspc_close", side_effect=RuntimeError("DB down"))
    assert is_crisis_mode_active_now() is None


def test_submission_blocked_by_fresh_intraday_crisis_not_yet_in_stored_data(db_engine, v2_mocks, mocker):
    """The decisive test for the actual gap this closes: ^GSPC's STORED
    daily-bar data (price_data) shows no crisis at all -- is_entry_blocked()
    alone would see nothing wrong, identically to what decide time already
    saw. Only a genuinely live ^GSPC quote, down >=7% from that stored
    close, reveals the crisis. Submission must still block on this."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    gspc_frame = _bullish_frame()  # no crisis anywhere in stored ^GSPC data
    gspc_last_close = float(gspc_frame["close"].iloc[-1])
    stock_last_close = float(frame["close"].iloc[-1])
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=stock_last_close)
    _mock_price_data(mocker, {"AAPL": frame, "^GSPC": gspc_frame})

    def _fake_live(ticker):
        if ticker == "^GSPC":
            return gspc_last_close * 0.90  # -10% intraday -- past -7%, invisible to stored data
        return stock_last_close
    mocker.patch("brokers.trading212._live_current_price", side_effect=_fake_live)

    n = submit_pending_stock_orders(_submission_config())

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="SKIPPED_CRISIS_BLOCKED")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert "crisis" in rows[0]["resolution_note"].lower()


def test_submission_blocked_by_daily_bar_crisis_still_in_cooldown(db_engine, v2_mocks, mocker):
    """A crisis already reflected in stored data (a trigger day within its
    5-day cooldown window) must still block submission via
    crisis.is_entry_blocked() -- re-confirmed fresh, not merely inherited
    from decide time, even though this specific case doesn't need the new
    intraday path to catch it."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    gspc_crisis_frame = _bullish_with_crisis_today()  # crisis already IN stored data
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=float(frame["close"].iloc[-1]))
    _mock_price_data(mocker, {"AAPL": frame, "^GSPC": gspc_crisis_frame})

    n = submit_pending_stock_orders(_submission_config())

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    rows = _pending_rows(db_engine, status="SKIPPED_CRISIS_BLOCKED")
    assert len(rows) == 1


def test_submission_proceeds_normally_when_no_crisis_present(db_engine, v2_mocks, mocker):
    """Control/regression -- no crisis anywhere (stored or live), the
    fresh re-check must not block a genuinely normal submission run."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=float(frame["close"].iloc[-1]))
    _mock_price_data(mocker, {"AAPL": frame})  # ^GSPC defaults to _bullish_frame() too

    n = submit_pending_stock_orders(_submission_config())

    assert n == 1
    v2_mocks.place_market_order.assert_called_once()
    assert len(_pending_rows(db_engine, status="SUBMITTED")) == 1
    assert len(_pending_rows(db_engine, status="SKIPPED_CRISIS_BLOCKED")) == 0


def test_submission_crisis_check_failure_leaves_rows_pending_not_blocked_or_submitted(db_engine, v2_mocks, mocker):
    """A failure reading/computing the crisis check itself (e.g. a DB
    read error) must fail safe: no order is placed (never guess it's
    safe), but the row is also left PENDING for the next window to
    retry, not permanently resolved to SKIPPED_CRISIS_BLOCKED on a mere
    check failure -- mirroring how a single ticker's own missing live
    price already leaves that row pending rather than assuming either
    outcome."""
    from brokers.trading212 import submit_pending_stock_orders

    frame = _bullish_frame()
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", is_etf=False,
                        decided_price=float(frame["close"].iloc[-1]))
    _mock_price_data(mocker, {"AAPL": frame})
    mocker.patch("brokers.trading212._load_gspc_close", side_effect=RuntimeError("DB read failed"))

    n = submit_pending_stock_orders(_submission_config())

    assert n == 0
    v2_mocks.place_market_order.assert_not_called()
    assert len(_pending_rows(db_engine, status="PENDING")) == 1
    assert len(_pending_rows(db_engine, status="SKIPPED_CRISIS_BLOCKED")) == 0


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

    maybe_place_orders(_config(mocker=mocker, db_engine=db_engine), run_type="full")  # must not raise


def test_exit_api_error_never_raises(db_engine, v2_mocks, mocker):
    from brokers.trading212 import maybe_exit_positions

    v2_mocks.get_positions.side_effect = RuntimeError("T212 is down")
    mocker.patch("brokers.trading212._send_t212_error_email")

    maybe_exit_positions(_config(mocker=mocker, db_engine=db_engine))  # must not raise


# ---------------------------------------------------------------------------
# Exit-check positions-fetch status (2026-08-03) — a distinct, always-
# current signal (get_exit_check_fetch_status()) separate from the
# existing generic invest_error key, closing the "only discoverable by
# manually reconstructing the timeline" gap a real get_positions()
# failure during the exit check previously had. See CLAUDE.md's
# "structural safeguards against future T212 API scheduling collisions"
# section, Part C.
# ---------------------------------------------------------------------------

def test_record_and_get_exit_check_fetch_status_round_trip(db_engine):
    """The pure record/read mechanism itself, independent of
    maybe_exit_positions() — confirms the JSON payload shape (status,
    recorded_at, and any extra detail kwargs) round-trips correctly."""
    from brokers.trading212 import _record_exit_check_fetch_status, get_exit_check_fetch_status

    assert get_exit_check_fetch_status() is None, "must be None before anything has ever recorded"

    _record_exit_check_fetch_status("FETCH_FAILED", error="boom")
    status = get_exit_check_fetch_status()
    assert status["status"] == "FETCH_FAILED"
    assert status["error"] == "boom"
    assert "recorded_at" in status

    _record_exit_check_fetch_status("OK")
    status = get_exit_check_fetch_status()
    assert status["status"] == "OK"
    assert "error" not in status, "a fresh OK record must not carry over the prior failure's detail"


def test_exit_check_records_ok_on_a_successful_positions_fetch(db_engine, v2_mocks, mocker):
    """A genuinely successful get_positions() call (even one returning no
    open positions at all, the v2_mocks default) must record "OK" —
    confirms the signal reflects "did the fetch itself succeed", not "did
    any position actually need evaluating"."""
    from brokers.trading212 import get_exit_check_fetch_status, maybe_exit_positions

    maybe_exit_positions(_config(mocker=mocker, db_engine=db_engine))

    status = get_exit_check_fetch_status()
    assert status is not None
    assert status["status"] == "OK"


def test_exit_check_records_fetch_failed_with_the_real_error_message(db_engine, v2_mocks, mocker):
    """The exact real gap this closes: a get_positions() failure during
    the exit check must leave a distinct, directly-checkable
    FETCH_FAILED record naming the real error — not just the generic,
    UI-invisible invest_error key this same call site also writes."""
    from brokers.trading212 import get_exit_check_fetch_status, maybe_exit_positions

    v2_mocks.get_positions.side_effect = RuntimeError("T212 rate limited")
    mocker.patch("brokers.trading212._send_t212_error_email")

    maybe_exit_positions(_config(mocker=mocker, db_engine=db_engine))

    status = get_exit_check_fetch_status()
    assert status is not None
    assert status["status"] == "FETCH_FAILED"
    assert "T212 rate limited" in status["error"]


def test_exit_check_fetch_failure_still_calls_the_existing_error_alert_path(db_engine, v2_mocks, mocker):
    """The new FETCH_FAILED record is ADDITIVE — the pre-existing
    _set_t212_error()/_send_t212_error_email() calls at this exact call
    site must be completely unaffected, not replaced."""
    from brokers.trading212 import maybe_exit_positions

    v2_mocks.get_positions.side_effect = RuntimeError("T212 is down")
    mock_email = mocker.patch("brokers.trading212._send_t212_error_email")
    mock_set_error = mocker.patch("brokers.trading212._set_t212_error")

    maybe_exit_positions(_config(mocker=mocker, db_engine=db_engine))

    mock_set_error.assert_called_once_with("T212 is down")
    mock_email.assert_called_once()
    assert mock_email.call_args.kwargs["context"] == "Exit check setup — could not fetch open positions from T212"


def test_exit_check_a_later_success_overwrites_an_earlier_failure(db_engine, v2_mocks, mocker):
    """Self-healing, by design: the record always reflects the MOST
    RECENT attempt only — a prior hour's real failure must not keep
    showing as the current state once a later check succeeds (this is
    what lets the Dashboard banner disappear on its own once the
    underlying issue clears, with no separate "resolved" tracking
    needed)."""
    from brokers.trading212 import get_exit_check_fetch_status, maybe_exit_positions

    # Config built once and reused for both calls -- _config() seeds
    # instrument_resolutions on every call, so calling it twice against
    # the same db_engine would hit a UNIQUE-constraint violation on the
    # second insert.
    cfg = _config(mocker=mocker, db_engine=db_engine)

    v2_mocks.get_positions.side_effect = RuntimeError("T212 is down")
    mocker.patch("brokers.trading212._send_t212_error_email")
    maybe_exit_positions(cfg)
    assert get_exit_check_fetch_status()["status"] == "FETCH_FAILED"

    v2_mocks.get_positions.side_effect = None
    v2_mocks.get_positions.return_value = []
    maybe_exit_positions(cfg)
    assert get_exit_check_fetch_status()["status"] == "OK"


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

    # (3) The skip is self-documenting: names the ticker and confirms it's
    # entry-eligible (not just "no data"). No manual-seed mechanism exists
    # any more (removed alongside the ETF sleeve, 2026-08-02) -- this
    # branch is structurally unreachable in production now, kept only as
    # a defensive fallback inside the shared submission logic.
    warning_text = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
    assert "XLK" in warning_text and "SXLKl_EQ" in warning_text
    assert "entry-eligible" in warning_text
    assert "never occur in production" in warning_text


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
    }, mocker=mocker, db_engine=db_engine)
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
    }, mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    # VOO's exception must not have stopped AAPL from still being exited.
    v2_mocks.place_market_order.assert_called_once()
    args, _ = v2_mocks.place_market_order.call_args
    assert args[0] == "AAPL_US_EQ"


def test_submission_one_row_exception_does_not_stop_the_rest(db_engine, v2_mocks, mocker):
    """A failure evaluating one pending row (e.g. the price-sanity lookup
    blowing up) must not stop the rest of the pending queue from being
    submitted. The simulated failure targets _live_current_price()
    (2026-08-02) -- the sanity check's real current-price source now,
    since get_price_df() is no longer part of that check's own code path
    at all."""
    from brokers.trading212 import submit_pending_etf_orders

    good_frame = _bullish_frame()
    good_close = float(good_frame["close"].iloc[-1])

    def _fake_live_current_price(ticker):
        if ticker == "VOO":
            raise RuntimeError("simulated transient failure")
        return good_close

    mocker.patch("brokers.trading212.get_price_df", return_value=good_frame.copy())
    mocker.patch("brokers.trading212._live_current_price", side_effect=_fake_live_current_price)
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
# Actionable T212 error alerts (2026-08-03) -- a real production alert
# ("T212 request failed after 3 attempts") gave no ticker, no direction,
# and no indication of what would happen next -- found investigating a
# genuine live incident (see CLAUDE.md). _send_t212_error_email() gained
# optional context/ticker/direction/next_steps kwargs so every call site
# can say what actually failed and what happens now, without breaking any
# existing positional-arg caller (all four kwargs default to empty/None).
# ---------------------------------------------------------------------------

def _sent_message_parts(mock_server) -> tuple[str, str]:
    """Parses the MIME message sendmail() was called with and returns
    (decoded subject, decoded plain-text body) -- properly decoded rather
    than raw substring-matched against msg.as_string(), since utf-8
    MIMEText bodies (and any non-ASCII subject, e.g. an em-dash) are
    base64/quoted-printable encoded on the wire and would not contain a
    literal, searchable substring otherwise."""
    from email import message_from_string
    from email.header import decode_header

    raw = mock_server.sendmail.call_args[0][2]
    msg = message_from_string(raw)

    subject_parts = decode_header(msg["Subject"])
    subject = "".join(
        part.decode(enc or "utf-8") if isinstance(part, bytes) else part
        for part, enc in subject_parts
    )

    text_body = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            text_body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
            break

    return subject, text_body


def test_t212_error_email_includes_ticker_direction_context_and_next_steps(db_engine, mocker):
    """A per-candidate failure (e.g. a failed SELL) must carry the real
    ticker, direction, what step failed, and what happens next -- not just
    the bare underlying error string."""
    from brokers.trading212 import _send_t212_error_email
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    _send_t212_error_email(
        "T212 request failed after 3 attempts", _email_config(),
        context="SELL order failed for WBD (exit reason: STOP_LOSS)",
        ticker="WBD", direction="SELL",
        next_steps="The position in WBD remains open and will be retried on the next hourly check.",
    )

    subject, body = _sent_message_parts(mock_server)
    assert "WBD" in body
    assert "SELL" in body
    assert "STOP_LOSS" in body
    assert "T212 request failed after 3 attempts" in body
    assert "remains open" in body
    assert "retried on the next hourly check" in body

    assert "WBD" in subject
    assert "SELL" in subject


def test_t212_error_email_setup_failure_has_no_ticker_but_still_has_context_and_next_steps(db_engine, mocker):
    """A whole-run setup failure (no single ticker/direction involved) must
    still explain what step failed and what happens next -- confirms the
    optional fields degrade gracefully rather than producing a broken or
    still-generic email when ticker/direction are genuinely not applicable."""
    from brokers.trading212 import _send_t212_error_email
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    _send_t212_error_email(
        "T212 request failed after 3 attempts", _email_config(),
        context="Exit check setup — could not fetch open positions from T212",
        next_steps="No positions were evaluated for exit this cycle. This will retry automatically.",
    )

    subject, body = _sent_message_parts(mock_server)
    assert "Exit check setup" in body
    assert "could not fetch open positions" in body
    assert "No positions were evaluated for exit this cycle" in body
    assert "retry automatically" in body

    assert "Exit check setup" in subject


def test_t212_error_email_still_works_with_only_positional_args(db_engine, mocker):
    """Backward compatibility: every pre-existing call shape (error_msg,
    config) with no keyword args must still send a well-formed email
    containing at least the underlying error, not raise a TypeError."""
    from brokers.trading212 import _send_t212_error_email
    mock_server = mocker.MagicMock()
    mock_server.__enter__.return_value = mock_server
    mocker.patch("output.briefing.smtplib.SMTP", return_value=mock_server)

    _send_t212_error_email("bare legacy-shaped error", _email_config())

    mock_server.sendmail.assert_called_once()
    _subject, body = _sent_message_parts(mock_server)
    assert "bare legacy-shaped error" in body


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


# ---------------------------------------------------------------------------
# Live position reconciliation (2026-08-02, see CLAUDE.md's "Live position
# reconciliation" section) -- the live candidate pool is now the full,
# dynamically-tracked ~500-ticker S&P 500, where a held position's ticker
# becoming unavailable (delisting/acquisition) is a routine, ongoing
# occurrence, not a rare edge case. Covers:
#   - a synchronous sell-order failure no longer halts the whole exit loop
#     for every OTHER open position (was: `return`, now: `continue`)
#   - REJECTED added to _has_recent_sell_order()'s cooldown, so a sell that
#     T212 accepts then later rejects doesn't retry every single hour
#   - giving up (and flagging) after repeated sell failures, rather than
#     retrying forever against a genuinely unsellable (delisted/bankrupt)
#     position
#   - reconcile_positions_with_broker(): a broker-side closure with no SELL
#     from us (cash-out acquisition), and an unrecognized held instrument
#     (stock-swap acquisition) -- neither of which maybe_exit_positions()'s
#     own loop (which only iterates what T212 currently reports as held,
#     matched through a VERIFIED mapping) has any way to notice on its own
#   - stale VERIFIED instrument_resolutions invalidation once a ticker is
#     no longer a candidate AND no longer held, so a future ticker-symbol
#     reuse forces a fresh, re-verified resolution instead of trusting a
#     years-old cached mapping for what could by then be a different company
# ---------------------------------------------------------------------------

def test_exit_sell_failure_does_not_halt_remaining_positions(db_engine, v2_mocks, mocker):
    """Regression for the `if status == 'FAILED': return` bug -- a
    synchronous sell-order failure for one position (e.g. a genuinely
    delisted/bankrupt ticker with no market to sell into) used to halt the
    ENTIRE exit phase, silently leaving every OTHER open position unchecked
    for the rest of that run. Must now continue to the next position."""
    from brokers.trading212 import maybe_exit_positions

    bearish = _bearish_frame()  # signal off -> both should attempt exit
    v2_mocks.get_positions.return_value = [
        {"ticker": "I50Dl_EQ", "quantity": 4.0},
        {"ticker": "AAPL_US_EQ", "quantity": 2.0},
    ]

    def _fake_place_order(t212_ticker, qty, extended_hours=False):
        if t212_ticker == "I50Dl_EQ":
            raise RuntimeError("simulated T212 rejection -- no market")
        return {"id": "mock-order-ok"}

    v2_mocks.place_market_order.side_effect = _fake_place_order
    _mock_price_data(mocker, {"VOO": bearish, "AAPL": bearish})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)
    mocker.patch("brokers.trading212._send_t212_error_email")

    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0, days_ago=5, is_etf=True)
    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=1.0, days_ago=5, is_etf=False)

    cfg = _config(mapping={
        "VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD", "is_etf": True},
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
    }, mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    # Both were attempted (VOO's failure did not stop AAPL from being tried).
    assert v2_mocks.place_market_order.call_count == 2
    voo_row = db_engine.connect().execute(sa_text(
        "SELECT status FROM invest_orders WHERE ticker='VOO' AND direction='SELL'"
    )).fetchone()
    aapl_row = db_engine.connect().execute(sa_text(
        "SELECT status FROM invest_orders WHERE ticker='AAPL' AND direction='SELL'"
    )).fetchone()
    assert voo_row[0] == "FAILED"
    assert aapl_row[0] == "PLACED"


def test_exit_sell_failure_alert_carries_real_ticker_direction_and_next_steps(db_engine, v2_mocks, mocker):
    """Real-call-site regression for the 2026-08-03 alert-content fix: the
    actual maybe_exit_positions() SELL-failure path must call
    _send_t212_error_email() with ticker/direction/context/next_steps
    populated from the real failing position -- not the bare error string
    a real production alert used to be limited to."""
    from brokers.trading212 import maybe_exit_positions

    bearish = _bearish_frame()
    v2_mocks.get_positions.return_value = [{"ticker": "AAPL_US_EQ", "quantity": 2.0}]
    v2_mocks.place_market_order.side_effect = RuntimeError("simulated T212 rejection -- no market")
    _mock_price_data(mocker, {"AAPL": bearish})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)
    spy = mocker.patch("brokers.trading212._send_t212_error_email")

    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=1.0, days_ago=5, is_etf=False)

    cfg = _config(mapping={
        "AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD", "is_etf": False},
    }, mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    spy.assert_called_once()
    _, kwargs = spy.call_args
    assert kwargs["ticker"] == "AAPL"
    assert kwargs["direction"] == "SELL"
    assert "AAPL" in kwargs["context"]
    assert kwargs["next_steps"]  # non-empty -- real guidance, not left blank
    assert "remains open" in kwargs["next_steps"]


def test_has_recent_sell_order_blocks_on_rejected_within_cooldown(db_engine):
    """REJECTED (a sell T212 accepted then later rejected, via
    poll_placed_orders()) must be covered by the same short cooldown as
    FILLED/FAILED -- it was missing entirely before 2026-08-02, meaning a
    rejected sell for a genuinely unsellable ticker would retry on every
    single exit check with nothing blocking it."""
    from brokers.trading212 import _has_recent_sell_order

    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('DELISTED', 'DELISTED_US_EQ', 'SELL', 'REJECTED', :now, :now)
        """), {"now": datetime.utcnow().isoformat()})

    assert _has_recent_sell_order("DELISTED", hours=24) is True


def test_has_recent_sell_order_rejected_expires_after_cooldown(db_engine):
    from brokers.trading212 import _has_recent_sell_order

    # Days-old, not just-past-24h -- avoids the same T-vs-space-separated
    # same-calendar-date string-comparison ambiguity documented elsewhere
    # in this codebase (see the multi-day-offset test just below for the
    # full explanation).
    old = (datetime.utcnow() - timedelta(days=5)).isoformat()
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('DELISTED', 'DELISTED_US_EQ', 'SELL', 'REJECTED', :at, :at)
        """), {"at": old})

    assert _has_recent_sell_order("DELISTED", hours=24) is False


def test_count_recent_sell_failures_counts_since_last_buy(db_engine):
    from brokers.trading212 import _count_recent_sell_failures

    buy_dt = datetime.utcnow() - timedelta(days=10)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, filled_at, created_at)
            VALUES ('ZZZ', 'ZZZ_US_EQ', 'BUY', 'FILLED', :at, :at, :at)
        """), {"at": buy_dt.isoformat()})
        for hours_ago in (3, 2, 1):
            at = (datetime.utcnow() - timedelta(hours=hours_ago)).isoformat()
            conn.execute(sa_text("""
                INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
                VALUES ('ZZZ', 'ZZZ_US_EQ', 'SELL', 'REJECTED', :at, :at)
            """), {"at": at})

    assert _count_recent_sell_failures("ZZZ") == 3


def test_count_recent_sell_failures_zero_with_no_buy(db_engine):
    from brokers.trading212 import _count_recent_sell_failures

    assert _count_recent_sell_failures("NEVERBOUGHT") == 0


def test_repeated_sell_rejection_gives_up_and_flags_after_threshold(db_engine, v2_mocks, mocker):
    """After _SELL_REJECTION_GIVE_UP_THRESHOLD consecutive sell failures
    since the last FILLED BUY, maybe_exit_positions() must stop attempting
    a fresh sell (recognizing the position likely cannot be sold at all --
    delisted/bankrupt, no real market) and record a REPEATED_SELL_REJECTION
    reconciliation event instead of hammering T212 again."""
    from brokers.trading212 import maybe_exit_positions

    bearish = _bearish_frame()
    v2_mocks.get_positions.return_value = [{"ticker": "ZZZ_US_EQ", "quantity": 5.0}]
    _mock_price_data(mocker, {"ZZZ": bearish})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    buy_dt = datetime.utcnow() - timedelta(days=10)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, momentum_pct, stop_price, account_type, currency_code,
                 placed_at, filled_at, created_at)
            VALUES ('ZZZ', 'ZZZ_US_EQ', 'BUY', 5, 100.0, 5, 'FILLED', 0, 0.05, 1.0, 'DEMO', 'USD', :at, :at, :at)
        """), {"at": buy_dt.isoformat()})
        # Multi-day offsets, not just-past-24h ones -- deliberately avoids
        # the same T-vs-space-separated-timestamp string-comparison trap
        # already documented elsewhere in this codebase (see CLAUDE.md's
        # "Monday pre-market job" cutoff-comparison note and "T212 P&L
        # reconstruction" section): a row whose placed_at shares today's
        # calendar date with the query's own datetime('now', '-24 hours')
        # cutoff can compare incorrectly. Days-old offsets side-step that
        # ambiguity entirely rather than relying on getting a same-day
        # boundary right.
        for days_ago in (5, 4, 3):
            at = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
            conn.execute(sa_text("""
                INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
                VALUES ('ZZZ', 'ZZZ_US_EQ', 'SELL', 'REJECTED', :at, :at)
            """), {"at": at})

    cfg = _config(mapping={"ZZZ": {"t212_instrument_id": "ZZZ_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_not_called()
    row = db_engine.connect().execute(sa_text(
        "SELECT event_type, ticker, status FROM position_reconciliations WHERE t212_ticker='ZZZ_US_EQ'"
    )).fetchone()
    assert row is not None
    assert row[0] == "REPEATED_SELL_REJECTION"
    assert row[1] == "ZZZ"
    assert row[2] == "OPEN"


def test_repeated_sell_rejection_below_threshold_still_attempts(db_engine, v2_mocks, mocker):
    """Fewer than the threshold's worth of failures must still attempt a
    fresh sell -- the give-up behaviour is specifically for REPEATED
    failure, not a single one."""
    from brokers.trading212 import maybe_exit_positions

    bearish = _bearish_frame()
    v2_mocks.get_positions.return_value = [{"ticker": "ZZZ_US_EQ", "quantity": 5.0}]
    _mock_price_data(mocker, {"ZZZ": bearish})
    mocker.patch("analysis.universe.is_member", return_value=True)
    mocker.patch("analysis.crisis.is_crisis_day", return_value=False)
    mocker.patch("analysis.crisis.is_entry_blocked", return_value=False)
    mocker.patch("analysis.universe.get_max_holding_days", return_value=30)

    buy_dt = datetime.utcnow() - timedelta(days=10)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, momentum_pct, stop_price, account_type, currency_code,
                 placed_at, filled_at, created_at)
            VALUES ('ZZZ', 'ZZZ_US_EQ', 'BUY', 5, 100.0, 5, 'FILLED', 0, 0.05, 1.0, 'DEMO', 'USD', :at, :at, :at)
        """), {"at": buy_dt.isoformat()})
        at = (datetime.utcnow() - timedelta(days=5)).isoformat()  # days-old, see the note above
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('ZZZ', 'ZZZ_US_EQ', 'SELL', 'REJECTED', :at, :at)
        """), {"at": at})

    cfg = _config(mapping={"ZZZ": {"t212_instrument_id": "ZZZ_US_EQ", "currency": "USD", "is_etf": False}},
                  mocker=mocker, db_engine=db_engine)
    maybe_exit_positions(cfg)

    v2_mocks.place_market_order.assert_called_once()


# ---------------------------------------------------------------------------
# reconcile_positions_with_broker()
# ---------------------------------------------------------------------------

def _reconcile_config(enabled=True) -> dict:
    return {
        "trading212_invest": {"enabled": enabled, "live": False},
        "notifications": {"email": {"enabled": False}},
    }


# ---------------------------------------------------------------------------
# get_held_research_tickers() (2026-08-02) -- the public wrapper around
# _locally_believed_open_positions() that data/ingestion.py's price-pull
# ticker list now merges in, so a held ticker's price data keeps updating
# even after it loses S&P 500 membership. See CLAUDE.md's "let momentum,
# not index removal, govern already-open positions" section.
# ---------------------------------------------------------------------------

def test_get_held_research_tickers_returns_ticker_for_open_position(db_engine):
    from brokers.trading212 import get_held_research_tickers

    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=1.0, days_ago=5, is_etf=False)

    assert get_held_research_tickers() == {"AAPL"}


def test_get_held_research_tickers_empty_when_nothing_open(db_engine):
    from brokers.trading212 import get_held_research_tickers

    assert get_held_research_tickers() == set()


def test_get_held_research_tickers_excludes_a_closed_position(db_engine):
    """A BUY followed by a later FILLED SELL for the same t212_ticker is no
    longer believed open -- matches _locally_believed_open_positions()'s
    own semantics exactly, since this is a thin wrapper around it."""
    from brokers.trading212 import get_held_research_tickers

    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=1.0, days_ago=10, is_etf=False)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, account_type, currency_code, placed_at, filled_at, created_at)
            VALUES ('AAPL', 'AAPL_US_EQ', 'SELL', 10, 100.0, 10, 'FILLED', 0, 'DEMO', 'USD',
                    :now, :now, :now)
        """), {"now": (datetime.utcnow() - timedelta(days=1)).isoformat()})

    assert get_held_research_tickers() == set()


def test_get_all_watchlist_entries_includes_held_ticker_no_longer_a_live_candidate(db_engine, mocker):
    """The critical dependency check (2026-08-02, widened 2026-08-06): a
    ticker with a currently-open position must appear in
    get_all_watchlist_entries() -- and therefore in the daily price-pull
    ticker list -- even when it's absent from
    get_all_known_stock_tickers() entirely (e.g. a held ETF position;
    ETF_TICKERS is deliberately excluded from that function). Without
    this, a held ticker's price data (and therefore its momentum signal)
    would silently stop updating, making "let momentum, not index
    removal, govern an already-open position" unimplementable."""
    from data.ingestion import get_all_watchlist_entries

    _seed_open_position(db_engine, ticker="REMOVEDCO", t212_ticker="REMOVEDCO_US_EQ",
                         stop_price=1.0, days_ago=5, is_etf=False)
    # REMOVEDCO deliberately absent from the known-tickers set -- it's an
    # ETF-style edge case (or a ticker discovery gap) the held-position
    # merge alone must cover.
    mocker.patch("analysis.universe.get_all_known_stock_tickers", return_value={"AAPL", "MSFT"})

    entries = get_all_watchlist_entries({"reference_tickers": []})
    tickers = [e["ticker"] for e in entries]

    assert "REMOVEDCO" in tickers
    assert "AAPL" in tickers and "MSFT" in tickers
    assert len(tickers) == len(set(tickers))


def test_get_all_watchlist_entries_held_ticker_never_duplicated_if_also_a_known_ticker(db_engine, mocker):
    from data.ingestion import get_all_watchlist_entries

    _seed_open_position(db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ",
                         stop_price=1.0, days_ago=5, is_etf=False)
    mocker.patch("analysis.universe.get_all_known_stock_tickers", return_value={"AAPL"})

    entries = get_all_watchlist_entries({"reference_tickers": []})
    tickers = [e["ticker"] for e in entries]

    assert tickers.count("AAPL") == 1


def test_get_all_watchlist_entries_retains_ticker_after_membership_ends(db_engine, mocker):
    """The Task A fix itself: a ticker whose only membership window has
    already ended must still appear in get_all_watchlist_entries() (so
    pull_incremental() keeps fetching it), even though it is no longer a
    live trading candidate. Uses the real analysis.universe functions
    against a seeded universe_membership row rather than mocking
    get_all_known_stock_tickers(), so this actually exercises the DB
    query the production code runs."""
    from data.ingestion import get_all_watchlist_entries
    from analysis.universe import is_member
    from sqlalchemy import text
    from datetime import date

    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO universe_membership (ticker, start_date, end_date, source, fetched_at) "
                "VALUES ('DEPARTEDCO', '2010-01-01', '2015-01-01', 'test', :now)"
            ),
            {"now": datetime.utcnow()},
        )

    entries = get_all_watchlist_entries({"reference_tickers": []})
    tickers = [e["ticker"] for e in entries]

    assert "DEPARTEDCO" in tickers, "a ticker whose membership already ended must still be ingested"
    # Point-in-time tradeability is unaffected by ingestion scope: still
    # inadmissible today, still admissible inside its real window.
    assert is_member("DEPARTEDCO", date(2026, 1, 1)) is False
    assert is_member("DEPARTEDCO", date(2012, 6, 1)) is True


def test_reconcile_disabled_or_missing_creds_is_noop(db_engine, v2_mocks, monkeypatch):
    from brokers.trading212 import reconcile_positions_with_broker

    summary = reconcile_positions_with_broker(_reconcile_config(enabled=False))
    assert summary == {
        "broker_closed_no_sell": [], "unrecognized_position": [],
        "stop_loss_closed": [], "invalidated_resolutions": 0,
    }
    v2_mocks.get_positions.assert_not_called()

    monkeypatch.delenv("T212_API_KEY", raising=False)
    summary = reconcile_positions_with_broker(_reconcile_config())
    assert summary["broker_closed_no_sell"] == []


def test_reconcile_no_discrepancy_when_positions_match(db_engine, v2_mocks, mocker):
    """A position we believe is open, that T212 also genuinely still shows
    held via a recognized mapping, must produce zero reconciliation events."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    _seed_resolutions_from_mapping(db_engine, {"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD"}})
    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0, days_ago=5, is_etf=True)
    v2_mocks.get_positions.return_value = [{"ticker": "I50Dl_EQ", "quantity": 10.0}]

    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["broker_closed_no_sell"] == []
    assert summary["unrecognized_position"] == []
    count = db_engine.connect().execute(sa_text("SELECT COUNT(*) FROM position_reconciliations")).scalar()
    assert count == 0


def test_reconcile_broker_closed_no_sell(db_engine, v2_mocks, mocker):
    """We believe VOO is still open (FILLED BUY, no FILLED SELL), but T212's
    real positions list does not show it held, and we never placed a SELL
    -- a cash-out acquisition the broker processed automatically is exactly
    this shape. Must be recorded, never silently missed."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    _seed_resolutions_from_mapping(db_engine, {"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD"}})
    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0, days_ago=5, is_etf=True)
    v2_mocks.get_positions.return_value = []  # T212 shows nothing held

    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["broker_closed_no_sell"] == ["VOO"]
    row = db_engine.connect().execute(sa_text(
        "SELECT event_type, ticker, status, note FROM position_reconciliations WHERE t212_ticker='I50Dl_EQ'"
    )).fetchone()
    assert row is not None
    assert row[0] == "BROKER_CLOSED_NO_SELL"
    assert row[1] == "VOO"
    assert row[2] == "OPEN"
    assert "cash-out acquisition" in row[3]
    mock_alert.assert_called_once()


def test_reconcile_stop_loss_closure_labeled_precisely_not_broker_closed_no_sell(db_engine, v2_mocks, mocker):
    """The core fix (2026-08-04): a locally-believed-open position that's
    gone from T212's live positions list, with no SELL order of our own
    on record, but whose BUY row has a broker-side stop recorded PLACED
    -- and T212's own order history confirms THAT stop reached FILLED --
    must be recorded precisely as STOP_LOSS, not folded into the generic
    BROKER_CLOSED_NO_SELL bucket, and must NOT trigger the reconciliation
    alert (this is expected, healthy behaviour, not a discrepancy needing
    review). A synthetic FILLED SELL row must also be written so realised
    P&L and the Dashboard's "Performance by Exit Reason" section reflect
    the trade correctly -- without it the closure would never produce a
    pnl row at all, not merely be mislabeled."""
    from brokers.trading212 import Trading212Client, reconcile_positions_with_broker

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAPL"])
    _seed_resolutions_from_mapping(db_engine, {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD"}})
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=90.0,
        days_ago=3, is_etf=False, stop_order_id="stop-order-recon-1", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.return_value = []  # T212 no longer shows it held
    # filledAt must sort after the seeded entry's filled_at (utcnow() - 3
    # days) in _reconstruct_pnl()'s `ORDER BY filled_at ASC` string
    # comparison -- a hardcoded past date here previously collided with
    # the entry's time-of-day depending on when the suite happened to run
    # (e.g. entry at 13:34 vs. a hardcoded "10:00:00" exit sorted the SELL
    # BEFORE the BUY, silently breaking FIFO pairing and producing no pnl
    # row). Anchoring to utcnow() guarantees it's always after the entry.
    exit_filled_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    v2_mocks.get_order_history.return_value = [{
        "order": {"id": "stop-order-recon-1", "status": "FILLED"},
        "fill": {"price": 89.5, "quantity": -10.0, "filledAt": exit_filled_at},
    }]
    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")

    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["stop_loss_closed"] == ["AAPL"]
    assert summary["broker_closed_no_sell"] == []
    row = db_engine.connect().execute(sa_text(
        "SELECT event_type, ticker, status, note FROM position_reconciliations WHERE t212_ticker='AAPL_US_EQ'"
    )).fetchone()
    assert row is not None
    assert row[0] == "STOP_LOSS"
    assert row[1] == "AAPL"
    assert row[2] == "ACKNOWLEDGED"
    assert "confirmed FILLED" in row[3]
    mock_alert.assert_not_called()  # expected, healthy -- no alert

    sell_row = db_engine.connect().execute(sa_text(
        "SELECT status, exit_reason, fill_price, filled_quantity, order_id FROM invest_orders "
        "WHERE t212_ticker='AAPL_US_EQ' AND direction='SELL'"
    )).fetchone()
    assert sell_row is not None
    assert sell_row[0] == "FILLED"
    assert sell_row[1] == "STOP_LOSS"
    assert sell_row[2] == 89.5
    assert sell_row[3] == 10.0
    assert sell_row[4] == "stop-order-recon-1"

    buy_row = db_engine.connect().execute(sa_text(
        "SELECT stop_order_status FROM invest_orders WHERE t212_ticker='AAPL_US_EQ' AND direction='BUY'"
    )).fetchone()
    assert buy_row[0] == "FILLED"

    pnl_row = db_engine.connect().execute(sa_text(
        "SELECT exit_reason FROM pnl WHERE t212_ticker='AAPL_US_EQ'"
    )).fetchone()
    assert pnl_row is not None
    assert pnl_row[0] == "STOP_LOSS"


def test_reconcile_stop_order_not_confirmed_filled_falls_through_to_broker_closed_no_sell(db_engine, v2_mocks, mocker):
    """A standing stop_order_id recorded PLACED locally, but T212's own
    order history does NOT confirm it reached FILLED (not found in the
    most recent history page) -- must NOT be guessed as a STOP_LOSS
    closure. Falls through to the existing, alert-worthy
    BROKER_CLOSED_NO_SELL handling instead, exactly as before this
    mechanism existed."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAPL"])
    _seed_resolutions_from_mapping(db_engine, {"AAPL": {"t212_instrument_id": "AAPL_US_EQ", "currency": "USD"}})
    _seed_open_position_with_broker_stop(
        db_engine, ticker="AAPL", t212_ticker="AAPL_US_EQ", stop_price=90.0,
        days_ago=3, is_etf=False, stop_order_id="stop-order-recon-2", stop_order_status="PLACED",
    )
    v2_mocks.get_positions.return_value = []
    v2_mocks.get_order_history.return_value = []  # not found -- cannot confirm

    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["stop_loss_closed"] == []
    assert summary["broker_closed_no_sell"] == ["AAPL"]
    row = db_engine.connect().execute(sa_text(
        "SELECT event_type, status FROM position_reconciliations WHERE t212_ticker='AAPL_US_EQ'"
    )).fetchone()
    assert row[0] == "BROKER_CLOSED_NO_SELL"
    assert row[1] == "OPEN"
    mock_alert.assert_called_once()
    sell_count = db_engine.connect().execute(sa_text(
        "SELECT COUNT(*) FROM invest_orders WHERE t212_ticker='AAPL_US_EQ' AND direction='SELL'"
    )).scalar()
    assert sell_count == 0


def test_reconcile_broker_closed_skips_when_placed_sell_order_pending(db_engine, v2_mocks, mocker):
    """2026-08-02 order-ID-aware refinement: if a real T212 order_id
    already exists for a SELL on this position (status still PLACED --
    not yet resolved via poll_placed_orders(), which has a documented lag
    on this account), the position being gone from T212's live positions
    is consistent with that order having actually filled -- must NOT be
    flagged as a broker-side mystery, and must NOT alert."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    _seed_resolutions_from_mapping(db_engine, {"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD"}})
    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0, days_ago=5, is_etf=True)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, order_id, direction, status, placed_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'real-t212-order-id-123', 'SELL', 'PLACED', :now, :now)
        """), {"now": datetime.utcnow().isoformat()})
    v2_mocks.get_positions.return_value = []  # T212 shows nothing held -- consistent with that SELL having filled

    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["broker_closed_no_sell"] == []
    count = db_engine.connect().execute(sa_text(
        "SELECT COUNT(*) FROM position_reconciliations WHERE t212_ticker='I50Dl_EQ'"
    )).scalar()
    assert count == 0
    mock_alert.assert_not_called()


def test_reconcile_broker_closed_notes_order_id_when_sell_attempted_but_not_filled(db_engine, v2_mocks, mocker):
    """A SELL order_id exists but resolved to REJECTED (not FILLED) in our
    records, yet the position is gone from T212 anyway -- still flagged
    (this is genuinely unresolved), but the note must name the real
    order_id and its recorded status explicitly rather than reading as a
    clean, order-free broker-side closure -- a materially different,
    more specific situation."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    _seed_resolutions_from_mapping(db_engine, {"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD"}})
    _seed_open_position(db_engine, ticker="VOO", t212_ticker="I50Dl_EQ", stop_price=1.0, days_ago=5, is_etf=True)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders (ticker, t212_ticker, order_id, direction, status, placed_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'real-t212-order-id-456', 'SELL', 'REJECTED', :now, :now)
        """), {"now": datetime.utcnow().isoformat()})
    v2_mocks.get_positions.return_value = []

    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["broker_closed_no_sell"] == ["VOO"]
    row = db_engine.connect().execute(sa_text(
        "SELECT note FROM position_reconciliations WHERE t212_ticker='I50Dl_EQ'"
    )).fetchone()
    assert "real-t212-order-id-456" in row[0]
    assert "REJECTED" in row[0]
    mock_alert.assert_called_once()


def test_reconcile_unrecognized_position(db_engine, v2_mocks, mocker):
    """T212 shows an instrument held that has no VERIFIED mapping at all --
    a stock-swap acquisition converting a previously-tracked position into
    a different instrument is exactly this shape."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])
    v2_mocks.get_positions.return_value = [{"ticker": "SWAPPEDCO_US_EQ", "quantity": 12.0}]

    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["unrecognized_position"] == ["SWAPPEDCO_US_EQ"]
    row = db_engine.connect().execute(sa_text(
        "SELECT event_type, quantity, status FROM position_reconciliations WHERE t212_ticker='SWAPPEDCO_US_EQ'"
    )).fetchone()
    assert row is not None
    assert row[0] == "UNRECOGNIZED_POSITION"
    assert row[1] == 12.0
    assert row[2] == "OPEN"
    mock_alert.assert_called_once()


def test_reconcile_no_alert_when_nothing_found(db_engine, v2_mocks, mocker):
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])
    v2_mocks.get_positions.return_value = []
    mock_alert = mocker.patch("output.briefing.send_position_reconciliation_alert")

    reconcile_positions_with_broker(_reconcile_config())

    mock_alert.assert_not_called()


def test_reconcile_invalidates_stale_resolution_when_not_held(db_engine, v2_mocks, mocker):
    """A VERIFIED resolution for a ticker that's no longer a live candidate
    AND has no currently-open position must be invalidated -- otherwise a
    future reuse of the same ticker symbol by an unrelated company would
    silently trade under the OLD company's cached T212 mapping, since
    VERIFIED never re-checks itself."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])  # DELISTED no longer a candidate
    _seed_resolutions_from_mapping(db_engine, {"DELISTED": {"t212_instrument_id": "DELISTED_US_EQ", "currency": "USD"}})
    v2_mocks.get_positions.return_value = []  # and not currently held either

    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["invalidated_resolutions"] == 1
    row = db_engine.connect().execute(sa_text(
        "SELECT 1 FROM instrument_resolutions WHERE ticker='DELISTED'"
    )).fetchone()
    assert row is None


def test_reconcile_preserves_resolution_when_still_held(db_engine, v2_mocks, mocker):
    """The same ticker as above, but STILL genuinely held right now -- must
    NOT be invalidated, since maybe_exit_positions()'s own reverse_map
    lookup needs this exact mapping on its very next run to correctly
    match the position and fire INDEX_REMOVAL."""
    from brokers.trading212 import reconcile_positions_with_broker

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])  # no longer a candidate...
    _seed_resolutions_from_mapping(db_engine, {"REMOVED": {"t212_instrument_id": "REMOVED_US_EQ", "currency": "USD"}})
    v2_mocks.get_positions.return_value = [{"ticker": "REMOVED_US_EQ", "quantity": 3.0}]  # ...but still held

    summary = reconcile_positions_with_broker(_reconcile_config())

    assert summary["invalidated_resolutions"] == 0
    row = db_engine.connect().execute(sa_text(
        "SELECT status FROM instrument_resolutions WHERE ticker='REMOVED'"
    )).fetchone()
    assert row is not None and row[0] == "VERIFIED"


def test_record_reconciliation_event_upsert_preserves_acknowledged_status(db_engine):
    """Once a human has set an event's status to ACKNOWLEDGED, re-detecting
    the identical discrepancy on a later reconciliation run must refresh
    note/last_seen_at but must NOT silently flip status back to OPEN --
    otherwise ACKNOWLEDGED would be meaningless for any discrepancy that
    doesn't resolve itself (which BROKER_CLOSED_NO_SELL never does)."""
    from brokers.trading212 import _record_reconciliation_event

    _record_reconciliation_event(
        event_type="BROKER_CLOSED_NO_SELL", t212_ticker="X_US_EQ", ticker="X",
        quantity=5.0, note="first detection",
    )
    with db_engine.begin() as conn:
        conn.execute(sa_text(
            "UPDATE position_reconciliations SET status='ACKNOWLEDGED' WHERE t212_ticker='X_US_EQ'"
        ))

    _record_reconciliation_event(
        event_type="BROKER_CLOSED_NO_SELL", t212_ticker="X_US_EQ", ticker="X",
        quantity=5.0, note="re-detected on a later run",
    )

    row = db_engine.connect().execute(sa_text(
        "SELECT status, note FROM position_reconciliations WHERE t212_ticker='X_US_EQ'"
    )).fetchone()
    assert row[0] == "ACKNOWLEDGED"
    assert row[1] == "re-detected on a later run"


# ---------------------------------------------------------------------------
# Cross-mechanism check (2026-08-02): does get_unconfirmed_exits() (the
# Dashboard page's "placed-but-unconfirmed exits" list, output/performance.py)
# correctly stop showing a SELL once it's genuinely resolved, and does
# reconcile_positions_with_broker() correctly defer to poll_placed_orders()
# rather than double-flagging the same order? These are two independent
# reads over invest_orders that never talk to each other directly -- the
# only thing that actually clears a PLACED status is poll_placed_orders()
# itself, so this test drives the real chain end to end rather than
# asserting on either function in isolation.
# ---------------------------------------------------------------------------

class _Fake404(Exception):
    class _Resp:
        status_code = 404
    response = _Resp()


def test_unconfirmed_exits_clears_once_poll_resolves_it_and_reconciliation_agrees(db_engine, v2_mocks, mocker):
    """A genuine broker-side closure (e.g. a cash-out acquisition) that
    happens to coincide with our own outstanding PLACED SELL for the same
    ticker: poll_placed_orders() (which runs first, every hour, ahead of
    reconcile_positions_with_broker()) must resolve the order via its
    direction-aware SELL fallback (no order-history match, no live
    position -> FILLED) BEFORE reconciliation ever looks at it. Once that
    happens, both get_unconfirmed_exits() (nothing PLACED left to show)
    and reconcile_positions_with_broker() (the position no longer counts
    as "believed open" once its SELL is FILLED, so no BROKER_CLOSED_NO_SELL
    is raised either) must agree the situation is resolved -- not one
    clearing while the other still flags it, and not both staying silent
    forever."""
    from brokers.trading212 import Trading212Client, poll_placed_orders, reconcile_positions_with_broker
    from output.performance import get_unconfirmed_exits

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    _seed_resolutions_from_mapping(db_engine, {"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD"}})

    buy_dt = datetime.utcnow() - timedelta(days=5)
    sell_dt = datetime.utcnow() - timedelta(hours=3)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, account_type, currency_code, placed_at, filled_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'BUY', 10, 100.0, 10, 'FILLED', 1, 'DEMO', 'USD', :at, :at, :at)
        """), {"at": buy_dt.isoformat()})
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, status, order_id, account_type,
                 currency_code, placed_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'SELL', 10, 'PLACED', 'order-abc', 'DEMO', 'USD', :at, :at)
        """), {"at": sell_dt.isoformat()})

    # Sanity: while still PLACED, both mechanisms must reflect that --
    # unconfirmed, and reconciliation deliberately silent (deferring to
    # poll, not double-flagging).
    v2_mocks.get_positions.return_value = []  # T212 no longer shows it held
    assert [r["ticker"] for r in get_unconfirmed_exits()] == ["VOO"]
    summary_before = reconcile_positions_with_broker(_reconcile_config())
    assert summary_before["broker_closed_no_sell"] == []

    # Now the broker-side event resolves: get_order() 404s, order-history
    # has no match, and no live position remains -- poll_placed_orders()'s
    # SELL fallback marks it FILLED.
    v2_mocks.get_order.side_effect = _Fake404()
    v2_mocks.get_order_history.return_value = []
    v2_mocks.get_position.return_value = None

    updated = poll_placed_orders({"trading212_invest": {"enabled": True, "live": False}})
    assert updated == 1

    row = db_engine.connect().execute(sa_text(
        "SELECT status FROM invest_orders WHERE order_id='order-abc'"
    )).fetchone()
    assert row[0] == "FILLED"

    # Both consumers must now agree it's resolved.
    assert get_unconfirmed_exits() == []
    mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary_after = reconcile_positions_with_broker(_reconcile_config())
    assert summary_after["broker_closed_no_sell"] == []
    count = db_engine.connect().execute(sa_text("SELECT COUNT(*) FROM position_reconciliations")).scalar()
    assert count == 0


def test_unconfirmed_exits_can_linger_if_poll_never_terminal_resolves_the_order(db_engine, v2_mocks, mocker):
    """The converse of the test above, stated as a genuine, confirmed gap
    rather than assumed away: if T212's own GET /equity/orders/{id} never
    404s for a stuck order (any other exception, or a non-terminal status
    every time), poll_placed_orders()'s `except ... if http_status != 404:
    continue` never reaches its FILLED/CANCELLED fallback -- the row stays
    PLACED indefinitely. reconcile_positions_with_broker() deliberately
    treats an outstanding PLACED order_id as "not a discrepancy, defer to
    polling" (see _most_recent_sell_order_since()'s own docstring) and has
    no independent give-up/timeout for this shape the way
    _SELL_REJECTION_GIVE_UP_THRESHOLD does for repeated FAILED/REJECTED
    attempts -- so neither mechanism provides a backstop here. This is not
    a bug this task fixes; it documents the real boundary of what "both
    mechanisms agree" actually guarantees."""
    from brokers.trading212 import Trading212Client, poll_placed_orders, reconcile_positions_with_broker
    from output.performance import get_unconfirmed_exits

    mocker.patch.object(Trading212Client, "_fx_rates", staticmethod(lambda: (1.27, 1.08)))
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["VOO"])
    _seed_resolutions_from_mapping(db_engine, {"VOO": {"t212_instrument_id": "I50Dl_EQ", "currency": "USD"}})

    buy_dt = datetime.utcnow() - timedelta(days=5)
    sell_dt = datetime.utcnow() - timedelta(hours=3)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, fill_price, filled_quantity,
                 status, is_etf, account_type, currency_code, placed_at, filled_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'BUY', 10, 100.0, 10, 'FILLED', 1, 'DEMO', 'USD', :at, :at, :at)
        """), {"at": buy_dt.isoformat()})
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, status, order_id, account_type,
                 currency_code, placed_at, created_at)
            VALUES ('VOO', 'I50Dl_EQ', 'SELL', 10, 'PLACED', 'order-stuck', 'DEMO', 'USD', :at, :at)
        """), {"at": sell_dt.isoformat()})

    v2_mocks.get_positions.return_value = []  # T212 no longer shows it held
    # get_order() never 404s cleanly -- some other transient error every time.
    v2_mocks.get_order.side_effect = RuntimeError("connection reset")

    updated = poll_placed_orders({"trading212_invest": {"enabled": True, "live": False}})
    assert updated == 0  # never resolved

    row = db_engine.connect().execute(sa_text(
        "SELECT status FROM invest_orders WHERE order_id='order-stuck'"
    )).fetchone()
    assert row[0] == "PLACED"  # still stuck

    # Confirmed gap: it lingers on the dashboard...
    assert [r["ticker"] for r in get_unconfirmed_exits()] == ["VOO"]
    # ...and reconciliation offers no backstop either -- it defers to
    # polling by design and never flags this as a discrepancy on its own.
    mocker.patch("output.briefing.send_position_reconciliation_alert")
    summary = reconcile_positions_with_broker(_reconcile_config())
    assert summary["broker_closed_no_sell"] == []


# ---------------------------------------------------------------------------
# purge_old_pending_orders() (2026-08-02) -- bounded retention for
# pending_orders' resolved rows, a deliberate, scoped exception to
# "flag, don't delete" for this specific high-volume, mostly-routine-noise
# table. invest_orders (real trade history) is never touched by this.
# ---------------------------------------------------------------------------

def _insert_pending_row(engine, *, ticker, status, resolved_at, t212_ticker=None):
    """Direct insert with an explicit resolved_at timestamp -- the existing
    _seed_pending_order() helper always leaves resolved_at NULL, which
    can't exercise age-based retention at all."""
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status,
                 resolution_note, resolved_at, created_at)
            VALUES (:ticker, :t212_ticker, 0, 'USD', :now, 100.0,
                    500.0, 0.05, 90.0, 0.02, :status,
                    'test row', :resolved_at, :now)
        """), {
            "ticker": ticker, "t212_ticker": t212_ticker or f"{ticker}_US_EQ",
            "now": datetime.utcnow().isoformat(), "status": status,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
        })


def test_purge_deletes_resolved_rows_past_retention_window(db_engine):
    from brokers.trading212 import purge_old_pending_orders

    old_cutoff = datetime.utcnow() - timedelta(days=45)
    _insert_pending_row(db_engine, ticker="OLDREJECT", status="REJECTED_CAPACITY", resolved_at=old_cutoff)

    deleted = purge_old_pending_orders(retention_days=30)

    assert deleted == 1
    row = db_engine.connect().execute(sa_text(
        "SELECT 1 FROM pending_orders WHERE ticker='OLDREJECT'"
    )).fetchone()
    assert row is None


def test_purge_preserves_resolved_rows_within_retention_window(db_engine):
    from brokers.trading212 import purge_old_pending_orders

    recent = datetime.utcnow() - timedelta(days=5)
    _insert_pending_row(db_engine, ticker="RECENTREJECT", status="REJECTED_CAPACITY", resolved_at=recent)

    deleted = purge_old_pending_orders(retention_days=30)

    assert deleted == 0
    row = db_engine.connect().execute(sa_text(
        "SELECT 1 FROM pending_orders WHERE ticker='RECENTREJECT'"
    )).fetchone()
    assert row is not None


def test_purge_never_deletes_a_still_pending_row_regardless_of_age(db_engine):
    """A PENDING row is a live, unresolved decision awaiting its
    submission window -- must never be purged just because it's old,
    even if it's been sitting unresolved for far longer than the
    retention window (e.g. submission paused)."""
    from brokers.trading212 import purge_old_pending_orders

    very_old = datetime.utcnow() - timedelta(days=200)
    # PENDING rows have no resolved_at in real use (only set by
    # _resolve_pending()) -- seed one with an old decided_at/created_at
    # but resolved_at left NULL, the genuine shape of a real stuck-PENDING row.
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, stop_price, var_pct, status, created_at)
            VALUES ('STUCKPENDING', 'STUCKPENDING_US_EQ', 0, 'USD', :old, 100.0,
                    500.0, 0.05, 90.0, 0.02, 'PENDING', :old)
        """), {"old": very_old.isoformat()})

    deleted = purge_old_pending_orders(retention_days=30)

    assert deleted == 0
    row = db_engine.connect().execute(sa_text(
        "SELECT status FROM pending_orders WHERE ticker='STUCKPENDING'"
    )).fetchone()
    assert row is not None and row[0] == "PENDING"


def test_purge_handles_mixed_statuses_correctly_in_one_pass(db_engine):
    from brokers.trading212 import purge_old_pending_orders

    old = datetime.utcnow() - timedelta(days=45)
    recent = datetime.utcnow() - timedelta(days=5)
    _insert_pending_row(db_engine, ticker="OLD_SUPERSEDED", status="SUPERSEDED", resolved_at=old)
    _insert_pending_row(db_engine, ticker="OLD_SKIPPED", status="SKIPPED_PRICE_MOVED", resolved_at=old)
    _insert_pending_row(db_engine, ticker="RECENT_FAILED", status="FAILED", resolved_at=recent)

    deleted = purge_old_pending_orders(retention_days=30)

    assert deleted == 2
    remaining = {r[0] for r in db_engine.connect().execute(sa_text(
        "SELECT ticker FROM pending_orders"
    )).fetchall()}
    assert remaining == {"RECENT_FAILED"}


def test_purge_reads_retention_days_from_config(db_engine):
    """config.yaml's trading212_invest.pending_orders_retention_days must
    genuinely drive the cutoff, not just be documented and ignored."""
    from brokers.trading212 import purge_old_pending_orders

    ten_days_ago = datetime.utcnow() - timedelta(days=10)
    _insert_pending_row(db_engine, ticker="TENDAYSOLD", status="REJECTED_CAPACITY", resolved_at=ten_days_ago)

    # A tight 7-day retention must purge a 10-day-old row.
    deleted = purge_old_pending_orders(config={"trading212_invest": {"pending_orders_retention_days": 7}})
    assert deleted == 1


def test_purge_defaults_to_30_days_when_config_key_absent(db_engine):
    from brokers.trading212 import purge_old_pending_orders

    fifty_days_ago = datetime.utcnow() - timedelta(days=50)
    twenty_days_ago = datetime.utcnow() - timedelta(days=20)
    _insert_pending_row(db_engine, ticker="FIFTYDAYSOLD", status="REJECTED_CAPACITY", resolved_at=fifty_days_ago)
    _insert_pending_row(db_engine, ticker="TWENTYDAYSOLD", status="REJECTED_CAPACITY", resolved_at=twenty_days_ago)

    deleted = purge_old_pending_orders(config={"trading212_invest": {}})

    assert deleted == 1
    remaining = {r[0] for r in db_engine.connect().execute(sa_text(
        "SELECT ticker FROM pending_orders"
    )).fetchall()}
    assert remaining == {"TWENTYDAYSOLD"}


def test_purge_never_raises_on_db_failure(db_engine, mocker):
    from brokers.trading212 import purge_old_pending_orders
    mocker.patch("data.database.get_engine", side_effect=RuntimeError("db down"))
    assert purge_old_pending_orders() == 0  # must not raise


def test_purge_never_touches_invest_orders(db_engine):
    """invest_orders (real trade/order history) must remain completely
    untouched under any condition -- confirmed by seeding a resolved
    invest_orders row old enough to be purged if this function were ever
    accidentally scoped too broadly, and confirming it survives."""
    from brokers.trading212 import purge_old_pending_orders

    old = datetime.utcnow() - timedelta(days=400)
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, status, placed_at, created_at)
            VALUES ('ANCIENT', 'ANCIENT_US_EQ', 'SELL', 'FAILED', :old, :old)
        """), {"old": old.isoformat()})
        _insert_pending_row(db_engine, ticker="ALSOOLD", status="REJECTED_CAPACITY",
                             resolved_at=old)

    before_count = db_engine.connect().execute(sa_text("SELECT COUNT(*) FROM invest_orders")).scalar()
    deleted = purge_old_pending_orders(retention_days=30)
    after_count = db_engine.connect().execute(sa_text("SELECT COUNT(*) FROM invest_orders")).scalar()

    assert deleted == 1  # only the pending_orders row
    assert before_count == after_count == 1
    row = db_engine.connect().execute(sa_text(
        "SELECT status FROM invest_orders WHERE ticker='ANCIENT'"
    )).fetchone()
    assert row is not None and row[0] == "FAILED"  # completely untouched


# ---------------------------------------------------------------------------
# project_pending_order_admission() (2026-08-02) -- read-only projection of
# tomorrow's capacity admission, for the Dashboard's candidate-queue Status
# column. Shares _capacity_fit_reason() with the real _submit_pending_orders()
# walk -- never a second, independently-derived rule.
#
# Reads a STORED account snapshot, not a live one (2026-08-09) -- see the
# function's own docstring for why. Tests seed system_metadata directly via
# _seed_t212_snapshot() rather than mocking Trading212Client.get_account_
# summary() (v2_mocks is still needed in most of these for T212_API_KEY/
# SECRET env vars and to keep the client itself constructible, but its
# get_account_summary() return value is no longer what this function reads).
# ---------------------------------------------------------------------------

def test_project_admission_ranks_by_momentum_and_admits_until_capacity(db_engine, v2_mocks):
    """Highest momentum first, skip-and-continue against real account
    state -- must reproduce the exact 2026-08-02 verification shape."""
    from brokers.trading212 import project_pending_order_admission

    _seed_t212_snapshot({
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    })
    # £2,500 each -> cap (30%% = £3,000) fits exactly 1; the rest reject.
    _seed_pending_order(db_engine, ticker="HIGH", t212_ticker="HIGH_EQ", is_etf=False,
                         momentum_pct=2.0, position_gbp=2500.0)
    _seed_pending_order(db_engine, ticker="MID", t212_ticker="MID_EQ", is_etf=False,
                         momentum_pct=1.0, position_gbp=2500.0)
    _seed_pending_order(db_engine, ticker="LOW", t212_ticker="LOW_EQ", is_etf=False,
                         momentum_pct=0.1, position_gbp=2500.0)

    projected = project_pending_order_admission(_submission_config(max_deployed=0.30))

    by_ticker = {p["ticker"]: p["projected_status"] for p in projected}
    assert by_ticker["HIGH"] == "PROJECTED_ADMIT"
    assert by_ticker["MID"] == "PROJECTED_REJECT_CAPACITY"
    assert by_ticker["LOW"] == "PROJECTED_REJECT_CAPACITY"
    # Order returned must itself be momentum-descending, matching the real walk.
    assert [p["ticker"] for p in projected] == ["HIGH", "MID", "LOW"]
    # Every row carries the same stored snapshot's timestamp (2026-08-09) --
    # the whole reason a caller can show "as of" wherever the projection appears.
    assert all(p["snapshot_checked_at"] for p in projected)
    assert len({p["snapshot_checked_at"] for p in projected}) == 1


def test_project_admission_never_places_an_order_or_resolves_a_row(db_engine, v2_mocks):
    """Purely read-only -- confirmed directly, not just by absence of a
    call in this one test's own assertions."""
    from brokers.trading212 import project_pending_order_admission

    _seed_t212_snapshot({
        "totalValue": 10_000.0, "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    })
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_EQ", is_etf=False,
                         momentum_pct=0.5, position_gbp=500.0)

    project_pending_order_admission(_submission_config(max_deployed=0.5))

    v2_mocks.place_market_order.assert_not_called()
    v2_mocks.get_account_summary.assert_not_called()  # stored-state read, no live call at all
    still_pending = _pending_rows(db_engine, status="PENDING")
    assert len(still_pending) == 1  # unresolved -- projection never wrote anything


def test_project_admission_returns_empty_when_disabled(db_engine, v2_mocks):
    from brokers.trading212 import project_pending_order_admission
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_EQ", is_etf=False, position_gbp=500.0)
    assert project_pending_order_admission(_config(enabled=False)) == []


def test_project_admission_returns_empty_when_no_snapshot_stored(db_engine, v2_mocks):
    """No honest projection can be made without a stored account state --
    returns [] rather than guessing, matching this project's own
    "fail loud, don't guess" convention. (2026-08-09 -- this used to be a
    live client failure; the function no longer calls the client at all,
    so the equivalent failure mode is now "nothing in system_metadata yet",
    e.g. immediately after a fresh deploy before the first hourly snapshot.)"""
    from brokers.trading212 import project_pending_order_admission
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_EQ", is_etf=False, position_gbp=500.0)

    assert project_pending_order_admission(_submission_config()) == []
    v2_mocks.get_account_summary.assert_not_called()


def test_project_admission_returns_empty_on_malformed_stored_snapshot(db_engine, v2_mocks):
    """A corrupted/unparseable stored snapshot is the same "can't honestly
    project" case as no snapshot at all -- [] , not a crash."""
    from data.database import set_metadata
    from brokers.trading212 import project_pending_order_admission
    set_metadata("t212_account_snapshot", "not-valid-json{{{")
    set_metadata("t212_snapshot_fetched_at", datetime.utcnow().isoformat())
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_EQ", is_etf=False, position_gbp=500.0)

    assert project_pending_order_admission(_submission_config()) == []


def test_project_admission_empty_when_no_pending_rows(db_engine, v2_mocks):
    from brokers.trading212 import project_pending_order_admission
    assert project_pending_order_admission(_submission_config()) == []


def test_project_admission_render_path_never_calls_the_live_client(db_engine, monkeypatch):
    """Direct proof, mirroring the crisis-status/sector-breakdown fixes'
    own no-live-call tests (2026-08-09): Trading212Client itself is
    patched to raise if constructed at all -- project_pending_order_
    admission() must never reach it, regardless of what's stored. No
    v2_mocks fixture on purpose -- that fixture's whole job is providing a
    working mock client, which would silently mask this function ever
    trying to construct one."""
    from brokers.trading212 import project_pending_order_admission
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")

    def _raise(*_a, **_k):
        raise AssertionError("project_pending_order_admission() must never construct a live Trading212Client")
    monkeypatch.setattr("brokers.trading212.Trading212Client", _raise)

    _seed_t212_snapshot({
        "totalValue": 10_000.0, "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    })
    _seed_pending_order(db_engine, ticker="AAPL", t212_ticker="AAPL_EQ", is_etf=False,
                         momentum_pct=0.5, position_gbp=500.0)

    projected = project_pending_order_admission(_submission_config(max_deployed=0.5))
    assert projected and projected[0]["projected_status"] == "PROJECTED_ADMIT"


def test_capacity_fit_reason_matches_real_submission_rejection_for_the_same_inputs(db_engine, v2_mocks, mocker):
    """The shared helper genuinely drives both call sites identically --
    proven by running the same scenario through both
    project_pending_order_admission() and the real submit_pending_stock_
    orders() and confirming they agree on which ticker is rejected."""
    from brokers.trading212 import project_pending_order_admission, submit_pending_stock_orders

    account = {
        "totalValue": 10_000.0, "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    }
    v2_mocks.get_account_summary.return_value = account
    v2_mocks.get_positions.return_value = []
    v2_mocks.get_quantity_for_gbp.return_value = 10.0
    v2_mocks.get_instrument_price_gbp.return_value = 250.0
    v2_mocks.get_instruments.return_value = [{"ticker": "OVERCAP_EQ", "minTradeQuantity": 1.0}]
    # The projection now reads a STORED snapshot (2026-08-09), not the live
    # client -- seeded here with the identical account dict so this remains
    # a genuine apples-to-apples parity check against the real submission
    # walk's own (still-live, unchanged) account read.
    _seed_t212_snapshot(account)
    # ^GSPC needs real (non-crashed) price data too -- submission now runs
    # a fresh crisis re-check (2026-08-03) before anything else, and an
    # unmocked, empty ^GSPC series would fail that check and block
    # submission before ever reaching the capacity logic this test is
    # actually proving. _mock_price_data(mocker, {}) gives every ticker,
    # including ^GSPC, a normal bullish default (no crisis anywhere).
    _mock_price_data(mocker, {})
    # Real submission also runs the price-sanity check, which needs a live
    # current price -- irrelevant to what THIS test is proving (that the
    # two paths agree on capacity), so OVERCAP's is held equal to its
    # decided_price (100.0, _seed_pending_order's default) to guarantee it
    # never blocks first; ^GSPC keeps its own (unmoved) default so the new
    # crisis re-check above stays a no-op for this test.
    def _fake_live(ticker):
        return 100.0 if ticker == "OVERCAP" else float(_bullish_frame()["close"].iloc[-1])
    mocker.patch("brokers.trading212._live_current_price", side_effect=_fake_live)

    _seed_pending_order(db_engine, ticker="OVERCAP", t212_ticker="OVERCAP_EQ", is_etf=False,
                         momentum_pct=1.0, position_gbp=6000.0)  # alone exceeds a 50% cap

    projected = project_pending_order_admission(_submission_config(max_deployed=0.5, stock_enabled=True))
    assert projected[0]["projected_status"] == "PROJECTED_REJECT_CAPACITY"

    submitted = submit_pending_stock_orders(_submission_config(max_deployed=0.5, stock_enabled=True))
    assert submitted == 0
    rejected = _pending_rows(db_engine, status="REJECTED_CAPACITY")
    assert len(rejected) == 1 and rejected[0]["ticker"] == "OVERCAP"


# ---------------------------------------------------------------------------
# admission_rank_value backfill -- regression for the 2026-08-10 incident
# where an ALTER-only column addition silently reordered the live
# submission walk. See data/database.py:_apply_data_fixes()'s
# backfill_admission_rank_value comment and _load_pending_orders()'s own
# corrected docstring for the full mechanism.
# ---------------------------------------------------------------------------

def _seed_premigration_pending_row(engine, *, ticker, momentum_pct, decided_at):
    """A pending_orders row exactly as ALTER TABLE ADD COLUMN leaves it:
    admission_rank_value NULL, momentum_pct populated. Takes an explicit
    decided_at so insertion order can be made to disagree with momentum
    order -- the real incident's rows were inserted alphabetically by the
    EOD candidate loop, which is what the collapsed sort fell back to."""
    with engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO pending_orders
                (ticker, t212_ticker, is_etf, currency, decided_at, decided_price,
                 position_gbp, momentum_pct, admission_rank_value, stop_price,
                 var_pct, status, created_at)
            VALUES (:ticker, :t212, 0, 'USD', :decided_at, 100.0,
                    500.0, :momentum_pct, NULL, 90.0, 0.02, 'PENDING', :decided_at)
        """), {
            "ticker": ticker, "t212": f"{ticker}_US_EQ",
            "decided_at": decided_at, "momentum_pct": momentum_pct,
        })


def test_backfill_populates_admission_rank_value_from_momentum(db_engine):
    """The paired backfill for the ALTER-only column addition: every
    pre-migration row ends up carrying its own momentum_pct, which is what
    resolve_admission_rank_value() would have written in the default
    absolute_momentum mode."""
    from data.database import _apply_data_fixes

    _seed_premigration_pending_row(db_engine, ticker="AAA", momentum_pct=0.10,
                                   decided_at="2026-08-07T22:09:00")
    _seed_premigration_pending_row(db_engine, ticker="ZZZ", momentum_pct=0.90,
                                   decided_at="2026-08-07T22:10:00")

    _apply_data_fixes(db_engine)

    rows = {r["ticker"]: r["admission_rank_value"] for r in _pending_rows(db_engine)}
    assert rows == {"AAA": 0.10, "ZZZ": 0.90}


def test_submission_walk_orders_by_momentum_not_insertion_order_after_backfill(db_engine):
    """The actual defect this backfill exists to prevent, asserted against
    the REAL submission-priority loader rather than a re-derived query.

    Rows are seeded alphabetically-ascending by decided_at while momentum
    runs the opposite way, so insertion order and momentum order fully
    disagree. Without the backfill every admission_rank_value is NULL, the
    primary sort key contributes nothing, and the walk collapses onto the
    unique decided_at tiebreak -- i.e. alphabetical order.
    """
    from brokers.trading212 import _load_pending_orders
    from data.database import _apply_data_fixes

    # Alphabetical insertion order, deliberately inverse to momentum.
    for i, (ticker, momentum) in enumerate([
        ("AAPL", 0.10), ("ADI", 0.40), ("ALB", 0.60), ("SNDK", 0.95),
    ]):
        _seed_premigration_pending_row(
            db_engine, ticker=ticker, momentum_pct=momentum,
            decided_at=f"2026-08-07T22:09:{i:02d}",
        )

    # Pre-backfill: confirm the degenerate collapse is real, so this test
    # genuinely fails without the fix rather than passing for other reasons.
    assert [r["ticker"] for r in _load_pending_orders(is_etf=False)] == [
        "AAPL", "ADI", "ALB", "SNDK",
    ], "expected the all-NULL sort key to collapse onto insertion order"

    _apply_data_fixes(db_engine)

    assert [r["ticker"] for r in _load_pending_orders(is_etf=False)] == [
        "SNDK", "ALB", "ADI", "AAPL",
    ], "after backfill the walk must run in momentum-descending order"


def test_backfill_leaves_an_already_populated_rank_value_untouched(db_engine):
    """The backfill must never overwrite a real decide-time value -- that
    would silently rewrite a relative_rank-mode row's rank as its absolute
    momentum, the one case where the two genuinely differ."""
    from data.database import _apply_data_fixes

    _seed_pending_order(db_engine, ticker="REL", t212_ticker="REL_US_EQ", is_etf=False,
                        momentum_pct=0.10, admission_rank_value=0.99)

    _apply_data_fixes(db_engine)

    rows = _pending_rows(db_engine)
    assert len(rows) == 1
    assert rows[0]["admission_rank_value"] == 0.99
    assert rows[0]["momentum_pct"] == 0.10
