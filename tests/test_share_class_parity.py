"""Live/backtest parity for the share-class exclusion (2026-08-22).

Implements the task's own requirement: "Add a scenario pair that would FAIL
if live and backtest disagreed about sibling exclusion, and VERIFY it fails
against a deliberately broken version."

Modelled on tests/test_admission_ranking_parity.py, and for the same reason
tests/test_live_backtest_parity.py's module docstring gives: this protects
against a CALL SITE that quietly stops honouring the shared
blocking_share_class_siblings(), not against the function being wrong in
isolation (covered by tests/test_decision.py).

THE SCENARIO IS THE REAL INCIDENT: two share classes of one company, both
gate-passing on the SAME day, with capacity for both. That is what actually
happened live (GOOG + GOOGL, both entered 2026-08-12), and it is the case a
naive "is the sibling already held" check would miss entirely, since neither
is held when the other is evaluated.

Uses the REAL, SHIPPED map (analysis/universe.SHARE_CLASS_GROUPS) with the
real GOOGL/GOOG tickers rather than monkeypatching in synthetic pairs -- so a
mistake in the shipped map itself would surface here too.
"""
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text

from analysis import signals, universe
from analysis.volatility import _atr

SIB_A, SIB_B = "GOOGL", "GOOG"          # a real, shipped share-class group
UNRELATED = "AAPL"                       # deliberately NOT in any group

_N_DAYS_BACKTEST = 253                   # last row == first eligible day
_N_DAYS_LIVE = 305                       # clears _ticker_state()'s 300-row floor


def test_the_pair_this_test_relies_on_is_actually_in_the_shipped_map():
    """Non-vacuity guard for the whole module: every assertion below is
    meaningless if these two tickers are not really siblings in the shipped
    map, and the module would still 'pass' by admitting both everywhere."""
    assert universe.get_share_class_siblings(SIB_A) == frozenset({SIB_B})
    assert universe.get_share_class_siblings(SIB_B) == frozenset({SIB_A})
    assert universe.get_share_class_siblings(UNRELATED) == frozenset()


def _rising_frame(n_days: int, drift: float) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = 100.0 * np.cumprod(np.r_[1.0, np.full(n_days - 1, 1 + drift)])
    return pd.DataFrame({"date": dates, "open": closes, "high": closes * 1.01,
                         "low": closes * 0.99, "close": closes})


# SIB_A drifts slightly harder, so it outranks SIB_B on both absolute
# momentum and relative rank -- the winner is therefore the same under either
# admission_ranking_mode, keeping this test about EXCLUSION only.
def _frames(n_days: int) -> dict:
    # ^GSPC included because the live path loads it for the crisis check
    # (_load_gspc_close()); a perfectly flat series is never a crisis day.
    flat = _rising_frame(n_days, 0.0)
    return {SIB_A: _rising_frame(n_days, 0.0020),
            SIB_B: _rising_frame(n_days, 0.0018),
            UNRELATED: _rising_frame(n_days, 0.0016),
            "^GSPC": flat}


# ---------------------------------------------------------------------------
# Backtest side
# ---------------------------------------------------------------------------

def _cache_entry(ticker: str, frame: pd.DataFrame) -> dict:
    df = frame.set_index("date")
    df.index = pd.to_datetime(df.index)
    close, high, low, open_ = (df[c].astype(float) for c in ("close", "high", "low", "open"))
    return {"ticker": ticker, "frame": pd.DataFrame({
        "close": close, "atr": _atr(high, low, close, 14),
        "signal": signals.compute_signal_series(close),
        "momentum": signals.compute_momentum_series(close),
        "relative_rank": signals.compute_relative_rank_series(close),
        "open": open_, "low": low,
    })}


def _backtest_config(exclusion: bool) -> dict:
    return {
        "trading212_invest": {
            "var_confidence": 0.95, "var_lookback_days": 252, "max_risk_pct": 0.01,
            "min_position_gbp": 50, "absolute_max_position_gbp": 1_000_000,
            "per_trade_pct": 0.30, "max_total_deployed": 0.90,
            "admission_ranking_mode": "relative_rank",
            "share_class_exclusion": exclusion,
            "stock_max_holding_days": 0, "var_horizon_days": 30,
        },
        "backtest": {"atr_stop_multiplier": 3.0, "starting_capital_gbp": 10_000},
        "signals": {"atr_period": 14},
    }


def _run_backtest(exclusion: bool, mocker) -> set:
    """Real, unmodified simulate_portfolio_compounding_mtm(). Returns the set
    of tickers admitted on the shared first eligible day."""
    mocker.patch("analysis.universe.is_member", return_value=True)
    from analysis.backtest import simulate_portfolio_compounding_mtm

    frames = _frames(_N_DAYS_BACKTEST)
    caches = [_cache_entry(t, frames[t]) for t in (SIB_A, SIB_B, UNRELATED)]
    gspc = pd.Series(4000.0, index=caches[0]["frame"].index)   # never a crisis day
    cfg = _backtest_config(exclusion)
    trades = simulate_portfolio_compounding_mtm(
        caches, cfg, gspc,
        per_trade_pct=cfg["trading212_invest"]["per_trade_pct"],
        max_deployed_pct=cfg["trading212_invest"]["max_total_deployed"],
    )
    first_day = caches[0]["frame"].index[-1].date()
    return {t["ticker"] for t in trades if t["entry_date"] == first_day}


def test_backtest_admits_both_siblings_when_exclusion_is_off(mocker):
    """The CURRENT, shipped default -- and the non-vacuity anchor for the
    test below: it proves the scenario really does admit both siblings, so
    the exclusion result is a genuine change and not an empty run."""
    admitted = _run_backtest(exclusion=False, mocker=mocker)
    assert admitted == {SIB_A, SIB_B, UNRELATED}, admitted


def test_backtest_admits_only_one_sibling_when_exclusion_is_on(mocker):
    admitted = _run_backtest(exclusion=True, mocker=mocker)
    assert SIB_A in admitted, admitted            # the higher-ranked class wins
    assert SIB_B not in admitted, admitted
    assert UNRELATED in admitted, "an unmapped ticker must be unaffected"


# ---------------------------------------------------------------------------
# Live side
# ---------------------------------------------------------------------------

def _live_config(exclusion: bool) -> dict:
    return {
        "trading212_invest": {
            "enabled": True, "live": False,
            "admission_ranking_mode": "relative_rank",
            "per_trade_pct": 0.30, "max_total_deployed": 0.90,
            "var_confidence": 0.95, "var_lookback_days": 252,
            "max_risk_pct": 0.01, "min_position_gbp": 50,
            "price_sanity_threshold_pct": 0.03,
            "stock_submission_enabled": True,
            "share_class_exclusion": exclusion,
            "stock_max_holding_days": 0, "var_horizon_days": 30,
        },
        "backtest": {"atr_stop_multiplier": 3.0},
        "signals": {"atr_period": 14},
    }


@pytest.fixture
def _live_mocks(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    monkeypatch.setattr("brokers.trading212.time.sleep", lambda *_a, **_k: None)

    client = mocker.MagicMock()
    client.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    }
    client.get_positions.return_value = []
    client.get_instruments.return_value = []
    client.get_quantity_for_gbp.side_effect = lambda t212_ticker, gbp, **kw: gbp / 100.0
    client.get_instrument_price_gbp.return_value = 100.0
    client.place_market_order.return_value = {"id": "mock-order-1"}
    mocker.patch("brokers.trading212.Trading212Client", return_value=client)

    mocker.patch("analysis.universe.get_live_stock_candidates",
                 return_value=[SIB_A, SIB_B, UNRELATED])
    mocker.patch("analysis.universe.is_member", return_value=True)
    with db_engine.begin() as conn:
        for ticker in (SIB_A, SIB_B, UNRELATED):
            conn.execute(sa_text("""
                INSERT INTO instrument_resolutions
                    (ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at)
                VALUES (:t, :tt, 'USD', 'VERIFIED', 'seeded for test', :now, :now)
            """), {"t": ticker, "tt": f"{ticker}_EQ", "now": "2026-07-30T00:00:00"})
    return client


def _run_live(exclusion: bool, db_engine, mocker) -> set:
    from brokers.trading212 import maybe_place_orders, submit_pending_stock_orders
    frames = _frames(_N_DAYS_LIVE)
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: frames[t].copy())
    mocker.patch("brokers.trading212._live_current_price",
                 side_effect=lambda t: frames[t]["close"].iloc[-1])
    cfg = _live_config(exclusion)
    maybe_place_orders(cfg)
    submit_pending_stock_orders(cfg)
    with db_engine.connect() as conn:
        return {r[0] for r in conn.execute(sa_text(
            "SELECT ticker FROM invest_orders WHERE direction='BUY'"))}


def test_live_submits_both_siblings_when_exclusion_is_off(db_engine, _live_mocks, mocker):
    """Non-vacuity anchor for the live half, exactly as its backtest twin is."""
    submitted = _run_live(exclusion=False, db_engine=db_engine, mocker=mocker)
    assert submitted == {SIB_A, SIB_B, UNRELATED}, submitted


def test_live_submits_only_one_sibling_when_exclusion_is_on(db_engine, _live_mocks, mocker):
    submitted = _run_live(exclusion=True, db_engine=db_engine, mocker=mocker)
    assert SIB_A in submitted, submitted
    assert SIB_B not in submitted, submitted
    assert UNRELATED in submitted, "an unmapped ticker must be unaffected"


def test_live_records_why_the_sibling_was_skipped(db_engine, _live_mocks, mocker):
    _run_live(exclusion=True, db_engine=db_engine, mocker=mocker)
    with db_engine.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT ticker, status, resolution_note FROM pending_orders WHERE ticker = :t"),
            {"t": SIB_B}).mappings().all()
    assert rows, "no pending_orders row for the excluded sibling — test is vacuous"
    assert rows[0]["status"] == "SKIPPED_SHARE_CLASS", dict(rows[0])
    assert SIB_A in (rows[0]["resolution_note"] or ""), dict(rows[0])


def test_live_blocks_a_candidate_whose_sibling_was_held_from_an_earlier_day(
    db_engine, _live_mocks, mocker,
):
    """The other half of "already held": a sibling opened on a PREVIOUS day,
    present only in invest_orders, with nothing in this batch to reveal it."""
    with db_engine.begin() as conn:
        conn.execute(sa_text("""
            INSERT INTO invest_orders
                (ticker, t212_ticker, direction, quantity, status, order_id,
                 fill_price, filled_quantity, filled_at, created_at)
            VALUES (:t, :tt, 'BUY', 10, 'FILLED', 'seed-1', 100.0, 10,
                    '2026-07-01T14:30:00', '2026-07-01T14:30:00')
        """), {"t": SIB_A, "tt": f"{SIB_A}_EQ"})

    from brokers.trading212 import get_held_research_tickers
    assert SIB_A in get_held_research_tickers(), "seed did not register as held — vacuous"

    frames = _frames(_N_DAYS_LIVE)
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: frames[t].copy())
    mocker.patch("brokers.trading212._live_current_price",
                 side_effect=lambda t: frames[t]["close"].iloc[-1])
    from brokers.trading212 import maybe_place_orders, submit_pending_stock_orders
    cfg = _live_config(exclusion=True)
    maybe_place_orders(cfg)
    submit_pending_stock_orders(cfg)

    with db_engine.connect() as conn:
        new_buys = {r[0] for r in conn.execute(sa_text(
            "SELECT ticker FROM invest_orders WHERE direction='BUY' AND order_id != 'seed-1'"))}
    assert SIB_B not in new_buys, new_buys
    assert UNRELATED in new_buys, "an unmapped ticker must still be admitted"


def test_exits_are_untouched_by_the_exclusion(db_engine, _live_mocks, mocker):
    """A held position is NEVER force-exited because a sibling exists. Drives
    the real exit path with both siblings held and exclusion on."""
    with db_engine.begin() as conn:
        for i, t in enumerate((SIB_A, SIB_B)):
            conn.execute(sa_text("""
                INSERT INTO invest_orders
                    (ticker, t212_ticker, direction, quantity, status, order_id,
                     fill_price, filled_quantity, filled_at, created_at, stop_price)
                VALUES (:t, :tt, 'BUY', 10, 'FILLED', :oid, 100.0, 10,
                        '2026-07-01T14:30:00', '2026-07-01T14:30:00', 1.0)
            """), {"t": t, "tt": f"{t}_EQ", "oid": f"seed-{i}"})

    frames = _frames(_N_DAYS_LIVE)
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: frames[t].copy())
    mocker.patch("brokers.trading212._live_current_price",
                 side_effect=lambda t: frames[t]["close"].iloc[-1])
    _live_mocks.get_positions.return_value = [
        {"ticker": f"{SIB_A}_EQ", "quantity": 10}, {"ticker": f"{SIB_B}_EQ", "quantity": 10},
    ]
    from brokers.trading212 import maybe_exit_positions, get_held_research_tickers
    assert {SIB_A, SIB_B} <= get_held_research_tickers(), "seed failed — vacuous"

    maybe_exit_positions(_live_config(exclusion=True))

    with db_engine.connect() as conn:
        sells = {r[0] for r in conn.execute(sa_text(
            "SELECT ticker FROM invest_orders WHERE direction='SELL'"))}
    assert sells == set(), f"exclusion must never force an exit, got SELLs for {sells}"


# ---------------------------------------------------------------------------
# The parity assertion, and proof it actually fails when the two disagree
# ---------------------------------------------------------------------------

def test_live_and_backtest_agree_on_which_sibling_is_admitted(db_engine, _live_mocks, mocker):
    live = _run_live(exclusion=True, db_engine=db_engine, mocker=mocker)
    backtest = _run_backtest(exclusion=True, mocker=mocker)
    assert live, "live admitted nothing — parity assertion would be vacuous"
    assert backtest, "backtest admitted nothing — parity assertion would be vacuous"
    assert live == backtest, f"live {live} != backtest {backtest}"


def test_parity_scenario_fails_if_the_backtest_stops_honouring_the_shared_check(
    db_engine, _live_mocks, mocker,
):
    """VERIFIED-TO-FAIL proof. Simulates exactly the regression this file
    exists to catch -- a call site that keeps CALLING the shared function but
    stops acting on it -- by patching the backtest's own reference to return
    "nothing blocks this" unconditionally. Live (unbroken) then excludes the
    sibling and the backtest does not, so the two must disagree.

    Without this test, the parity assertion above could pass simply because
    neither side excluded anything.
    """
    mocker.patch("analysis.backtest.blocking_share_class_siblings", return_value=())
    live = _run_live(exclusion=True, db_engine=db_engine, mocker=mocker)
    backtest = _run_backtest(exclusion=True, mocker=mocker)
    assert SIB_B not in live, "live half of the broken-version proof did not hold"
    assert SIB_B in backtest, "backtest was not actually broken — proof is vacuous"
    assert live != backtest, "a real divergence went undetected"
