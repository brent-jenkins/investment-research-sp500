"""Live/backtest parity for the admission-ranking promotion (2026-08-09) --
implements the promotion task's own requirement: 'Add a live/backtest parity
scenario pair that would FAIL if the two paths ordered candidates
differently, and verify it fails against a deliberately broken version.'

Unlike tests/test_live_backtest_parity.py's own single-ticker entry/exit
scenarios, this drives a genuine TWO-CANDIDATE, CAPACITY-CONSTRAINED
admission race, since that is the one place admission_ranking_mode actually
has an observable effect: which of two gate-passing candidates wins a
scarce capacity slot. Two tickers are constructed so absolute momentum and
12-minus-1 relative rank DISAGREE on which one is stronger -- under
"relative_rank" mode, both live order submission and the backtest engine
must agree on the same winner (B), for the same reason
tests/test_live_backtest_parity.py's own module docstring gives: this
protects against a call site that quietly stops calling the shared
resolve_admission_rank_value(), not against the function itself being wrong
in isolation (already covered by tests/test_decision.py).
"""
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text as sa_text

from analysis import signals
from analysis.volatility import _atr

# ---------------------------------------------------------------------------
# The crossover price series -- momentum(A) > momentum(B), but
# relative_rank(B) > relative_rank(A). Verified numerically, not just
# argued: A is a steady, moderate rise; B rises more steeply for most of
# the window, then declines sharply for the final 21 days (the window
# relative rank's own 1-month skip excludes but absolute momentum does
# not) -- both stay comfortably above the 2% floor.
#
# TWO frame lengths, for two different evaluation points -- this is the
# fix for a real bug found while building this fixture, not an arbitrary
# choice, so it's recorded here rather than left implicit:
#
# - _N_DAYS_BACKTEST = MOMENTUM_LOOKBACK_DAYS + 1 (253) exactly.
#   compute_momentum_series()/compute_relative_rank_series() both need a
#   full 252-trading-day lookback, so the FIRST day either metric is
#   non-NaN is row 252 (0-indexed) -- and simulate_portfolio_compounding_
#   mtm() evaluates admission on a ticker's very first eligible day (the
#   first day signal/gate pass), not on the LAST day of however much
#   history the cache happens to contain. Sizing the backtest frame so
#   the last row IS row 252 makes "first eligible day" and "the day the
#   kink lands" the same day, which is what the backtest race actually
#   needs. An earlier version used one longer (310-row) frame for both
#   backtest and live, with the kink placed 21 days before THAT frame's
#   end (day 309) -- but the backtest admission race actually happened
#   on day 252 (the first eligible day), 57 days before the kink, where
#   B still led on BOTH momentum and relative rank (no crossover existed
#   at the moment that actually mattered) -- caught because both
#   backtest parity assertions failed with an unconstrained "both
#   admitted" result instead of the expected single winner.
# - _N_DAYS_LIVE = 305, well past _ticker_state()'s own hard `len(df) <
#   300: return None` minimum-history floor -- live only ever evaluates
#   the LATEST row (`.iloc[-1]`), never "the first eligible day", so the
#   kink just needs to sit in the final 21 rows of WHATEVER row count the
#   live frame has, same shape as the original single-frame version, just
#   under a different name now that backtest has its own, shorter frame.
#   (Discovered the same way as the bug above -- a first pass reused the
#   253-row backtest frame for the live tests too, which is 47 rows
#   short of _ticker_state()'s floor, so live silently treated both
#   tickers as having insufficient history and queued nothing at all.)
# ---------------------------------------------------------------------------

_N_DAYS_BACKTEST = 253
_N_DAYS_LIVE = 305


def _crossover_frame(
    *, n_days: int, up_drift: float, down_drift: float = 0.0, kink_days_from_end: int = 0,
) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2026-07-30"), periods=n_days)
    closes = [100.0]
    for i in range(n_days - 1):
        d = down_drift if kink_days_from_end and i >= (n_days - 1 - kink_days_from_end) else up_drift
        closes.append(closes[-1] * (1 + d))
    closes = np.array(closes)
    highs, lows = closes * 1.01, closes * 0.99
    return pd.DataFrame({"date": dates, "open": closes, "high": highs, "low": lows, "close": closes})


def _ticker_a_frame_backtest() -> pd.DataFrame:
    """Steady moderate rise for the full window -- higher absolute momentum.
    Sized (253 rows) so the frame's last row is the first day the backtest
    engine can admit anything at all -- see the module-level comment above."""
    return _crossover_frame(n_days=_N_DAYS_BACKTEST, up_drift=0.0011)


def _ticker_b_frame_backtest() -> pd.DataFrame:
    """Strong rise for the first 232 days, sharp decline for the final 21
    -- higher relative rank (the decline sits entirely inside the
    excluded final month) despite LOWER absolute momentum than A."""
    return _crossover_frame(
        n_days=_N_DAYS_BACKTEST, up_drift=0.0022, down_drift=-0.0210, kink_days_from_end=21,
    )


def _ticker_a_frame_live() -> pd.DataFrame:
    """Same shape as _ticker_a_frame_backtest(), just padded to clear
    _ticker_state()'s own 300-row minimum-history floor -- live evaluates
    only the frame's LAST row, so padding earlier rows changes nothing
    about the crossover itself, only how much history sits before it."""
    return _crossover_frame(n_days=_N_DAYS_LIVE, up_drift=0.0011)


def _ticker_b_frame_live() -> pd.DataFrame:
    return _crossover_frame(
        n_days=_N_DAYS_LIVE, up_drift=0.0022, down_drift=-0.0210, kink_days_from_end=21,
    )


def _assert_crossover_holds():
    """Sanity-checks both fixture variants, once, so a future edit to the
    drift constants that accidentally breaks the intended crossover fails
    loudly here rather than producing a silently-vacuous parity test
    below."""
    for close_a, close_b in (
        (
            _ticker_a_frame_backtest().set_index("date")["close"].astype(float),
            _ticker_b_frame_backtest().set_index("date")["close"].astype(float),
        ),
        (
            _ticker_a_frame_live().set_index("date")["close"].astype(float),
            _ticker_b_frame_live().set_index("date")["close"].astype(float),
        ),
    ):
        mom_a = signals.compute_momentum_series(close_a).iloc[-1]
        mom_b = signals.compute_momentum_series(close_b).iloc[-1]
        rank_a = signals.compute_relative_rank_series(close_a).iloc[-1]
        rank_b = signals.compute_relative_rank_series(close_b).iloc[-1]
        assert mom_a > mom_b > 0.02, (mom_a, mom_b)
        assert rank_b > rank_a > 0.02, (rank_a, rank_b)


_assert_crossover_holds()


_RELATIVE_RANK_CONFIG_EXTRAS = {
    "admission_ranking_mode": "relative_rank",
    # per_trade_pct=1.0 sets the per_trade_pct CEILING far above what
    # either candidate's VaR-based sizing (analysis/volatility.py:
    # _var_position_size_core(), the shared risk-based sizing formula)
    # actually produces here, so it never binds -- position size is
    # whatever the real VaR formula gives each ticker's own realised
    # volatility, empirically ~2,500 for A (steadier drift, capped by
    # the max_deployed_pct ceiling below, which happens to bind first)
    # and ~860 for B (the kink adds realised volatility, so its
    # risk-based size is smaller) against a 10,000 starting balance --
    # confirmed directly by running the engine, not assumed equal. Each
    # candidate ALONE comfortably fits under max_total_deployed=0.25
    # (25%, i.e. 2,500), but the two TOGETHER never do (2,500 + 860 =
    # 3,360 > 2,500) -- capacity is genuinely binding, so exactly one of
    # the two wins the slot and admission order (the thing under test)
    # is what decides which one.
    "per_trade_pct": 1.0,
    "max_total_deployed": 0.25,
}


# ---------------------------------------------------------------------------
# Backtest side
# ---------------------------------------------------------------------------

def _build_cache_entry(ticker: str, frame: pd.DataFrame, atr_period: int = 14) -> dict:
    """Matches analysis.backtest.precompute_ticker()'s own frame shape
    exactly (close/atr/signal/momentum/relative_rank/open/low) -- built
    directly here instead of going through the DB-reading precompute_
    ticker() itself, since this test supplies synthetic prices, not real
    ingested ones."""
    df = frame.set_index("date")
    df.index = pd.to_datetime(df.index)
    close, high, low, open_ = (df[c].astype(float) for c in ("close", "high", "low", "open"))
    atr_s = _atr(high, low, close, atr_period)
    out = pd.DataFrame({
        "close": close, "atr": atr_s,
        "signal": signals.compute_signal_series(close),
        "momentum": signals.compute_momentum_series(close),
        "relative_rank": signals.compute_relative_rank_series(close),
        "open": open_, "low": low,
    })
    return {"ticker": ticker, "frame": out}


def _backtest_config(**extra_t212) -> dict:
    return {
        "trading212_invest": {
            "var_confidence": 0.95, "var_lookback_days": 252, "max_risk_pct": 0.01,
            "min_position_gbp": 50, "absolute_max_position_gbp": 1_000_000,
            **extra_t212,
        },
        "backtest": {"atr_stop_multiplier": 2.0, "starting_capital_gbp": 10_000},
        "signals": {"atr_period": 14},
    }


def _run_backtest_admission_race(mode: str, mocker) -> set:
    """Runs the real, unmodified simulate_portfolio_compounding_mtm() on
    tickers A and B together, under a capacity cap tight enough that only
    one can be admitted on the shared first eligible day. Returns the set
    of tickers actually admitted (by entry_date == the two frames' common
    first trading day).

    analysis.universe.is_member() is mocked True unconditionally, purely to
    bypass a real DB-backed membership lookup for these synthetic tickers --
    this test is about admission ORDER, not index-membership gating, which
    is already covered elsewhere. Deliberately NOT done by mocking
    ETF_TICKERS instead (an earlier version of this fixture did that) --
    ETF_TICKERS membership also drives is_asset_class_enabled()'s ETF-vs-
    stock candidacy switch, _queue_pending_order()'s is_etf flag, and which
    of submit_pending_etf_orders()/submit_pending_stock_orders() a decision
    actually resolves through, so mocking it made both synthetic tickers
    silently queue as ETF decisions -- caught only because the live-side
    tests below kept submitting nothing at all (submit_pending_stock_orders
    found zero STOCK rows, since both were queued as ETF rows instead)."""
    mocker.patch("analysis.universe.is_member", return_value=True)
    from analysis.backtest import simulate_portfolio_compounding_mtm

    cache_a = _build_cache_entry("A_HIGHMOM_LOWRANK", _ticker_a_frame_backtest())
    cache_b = _build_cache_entry("B_LOWMOM_HIGHRANK", _ticker_b_frame_backtest())
    gspc_close = pd.Series(4000.0, index=cache_a["frame"].index)  # perfectly calm -- never a crisis day

    cfg = _backtest_config(admission_ranking_mode=mode)
    trades = simulate_portfolio_compounding_mtm(
        [cache_a, cache_b], cfg, gspc_close,
        per_trade_pct=_RELATIVE_RANK_CONFIG_EXTRAS["per_trade_pct"],
        max_deployed_pct=_RELATIVE_RANK_CONFIG_EXTRAS["max_total_deployed"],
    )
    first_entry_date = cache_a["frame"].index[-1].date()  # both gate-pass first on the final synthetic day
    admitted_on_first_day = {t["ticker"] for t in trades if t["entry_date"] == first_entry_date}
    return admitted_on_first_day


def test_backtest_admits_the_relative_rank_winner_not_the_momentum_winner(mocker):
    """Under relative_rank mode, with capacity for only one, the backtest
    engine must admit B (higher relative rank), not A (higher absolute
    momentum but lower rank) -- the entry gate itself is unaffected
    (verified separately below), only which one WINS the scarce slot."""
    admitted = _run_backtest_admission_race(mode="relative_rank", mocker=mocker)
    assert admitted == {"B_LOWMOM_HIGHRANK"}, (
        f"expected only B (higher relative rank) to win the scarce slot, got {admitted}"
    )


def test_backtest_admits_the_momentum_winner_under_the_default_mode(mocker):
    """Sanity check the fixture cuts both ways: under the DEFAULT
    (absolute_momentum) mode, the SAME two tickers must flip to A winning
    instead -- proving the scenario is genuinely mode-sensitive, not just
    "B always wins because of some other property of the fixture."""
    admitted = _run_backtest_admission_race(mode="absolute_momentum", mocker=mocker)
    assert admitted == {"A_HIGHMOM_LOWRANK"}, (
        f"expected only A (higher absolute momentum) to win under the default mode, got {admitted}"
    )


# ---------------------------------------------------------------------------
# Live side
# ---------------------------------------------------------------------------

def _mock_price_data(mocker, frames: dict):
    def _fake_get_price_df(ticker, days=8000):
        return frames[ticker].copy()
    mocker.patch("brokers.trading212.get_price_df", side_effect=_fake_get_price_df)
    mocker.patch("brokers.trading212._live_current_price", side_effect=lambda t: frames[t]["close"].iloc[-1])


def _live_config(mode: str) -> dict:
    return {
        "trading212_invest": {
            "enabled": True, "live": False,
            "admission_ranking_mode": mode,
            "per_trade_pct": _RELATIVE_RANK_CONFIG_EXTRAS["per_trade_pct"],
            "max_total_deployed": _RELATIVE_RANK_CONFIG_EXTRAS["max_total_deployed"],
            "var_confidence": 0.95, "var_lookback_days": 252,
            "max_risk_pct": 0.01, "min_position_gbp": 50,
            "price_sanity_threshold_pct": 0.03,
            "stock_submission_enabled": True,
        },
        "backtest": {"atr_stop_multiplier": 2.0},
        "signals": {"atr_period": 14},
    }


@pytest.fixture
def _live_race_mocks(db_engine, monkeypatch, mocker):
    monkeypatch.setenv("T212_API_KEY", "test-key")
    monkeypatch.setenv("T212_API_SECRET", "test-secret")
    monkeypatch.setattr("brokers.trading212.time.sleep", lambda *_a, **_k: None)

    mock_client = mocker.MagicMock()
    mock_client.get_account_summary.return_value = {
        "totalValue": 10_000.0,
        "cash": {"availableToTrade": 10_000.0},
        "investments": {"currentValue": 0.0},
    }
    mock_client.get_positions.return_value = []
    mock_client.get_instruments.return_value = []
    # Deliberately NOT flat constants -- _submit_pending_orders()'s running
    # `invested` capacity tracker accumulates est_value_gbp = quantity *
    # get_instrument_price_gbp(), which must actually track each row's own
    # real (VaR-sized) position_gbp for the capacity race this test exists
    # to exercise. A flat get_quantity_for_gbp()==10.0/get_instrument_
    # price_gbp()==50.0 (a fine default for tests that don't care about
    # capacity) silently adds a constant £500 per fill regardless of the
    # real position size -- caught here because it let a same-mode second
    # candidate slip under the cap that its own real, larger position_gbp
    # should have pushed over it. price_gbp fixed at 100.0 so quantity =
    # gbp/100 and est_value_gbp = quantity*100 reconstructs the original
    # gbp amount (up to whole-share flooring for the non-ETF path).
    mock_client.get_quantity_for_gbp.side_effect = lambda t212_ticker, gbp, **kw: gbp / 100.0
    mock_client.get_instrument_price_gbp.return_value = 100.0
    mock_client.place_market_order.return_value = {"id": "mock-order-1"}
    mocker.patch("brokers.trading212.Trading212Client", return_value=mock_client)

    mocker.patch("analysis.universe.get_live_stock_candidates",
                  return_value=["A_HIGHMOM_LOWRANK", "B_LOWMOM_HIGHRANK"])
    # is_member() mocked True unconditionally, purely to bypass a real
    # DB-backed membership lookup -- this fixture is about admission ORDER,
    # not index-membership gating (covered elsewhere). Deliberately NOT
    # ETF_TICKERS -- see _run_backtest_admission_race()'s own docstring for
    # why that mock is wrong here (it silently reroutes both tickers'
    # decisions onto the ETF submission path instead of the stock one).
    mocker.patch("analysis.universe.is_member", return_value=True)
    with db_engine.begin() as conn:
        for ticker in ("A_HIGHMOM_LOWRANK", "B_LOWMOM_HIGHRANK"):
            conn.execute(sa_text("""
                INSERT INTO instrument_resolutions
                    (ticker, t212_instrument_id, currency, status, note, attempted_at, resolved_at)
                VALUES (:t, :tt, 'USD', 'VERIFIED', 'seeded for test', :now, :now)
            """), {"t": ticker, "tt": f"{ticker}_EQ", "now": "2026-07-30T00:00:00"})
    return mock_client


def test_live_submits_the_relative_rank_winner_not_the_momentum_winner(db_engine, _live_race_mocks, mocker):
    from brokers.trading212 import maybe_place_orders, submit_pending_stock_orders

    frames = {
        "A_HIGHMOM_LOWRANK": _ticker_a_frame_live(), "B_LOWMOM_HIGHRANK": _ticker_b_frame_live(),
    }
    _mock_price_data(mocker, frames)
    gspc = pd.DataFrame({
        "date": _ticker_a_frame_live()["date"], "open": 4000.0, "high": 4004.0, "low": 3996.0, "close": 4000.0,
    })
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: (
        frames.get(t, gspc).copy()
    ))
    mocker.patch("brokers.trading212._live_current_price",
                  side_effect=lambda t: frames.get(t, gspc)["close"].iloc[-1])

    cfg = _live_config("relative_rank")
    maybe_place_orders(cfg, run_type="full")
    submit_pending_stock_orders(cfg)

    placed = [c.args[0] for c in _live_race_mocks.place_market_order.call_args_list]
    assert placed == ["B_LOWMOM_HIGHRANK_EQ"], (
        f"expected only B (higher relative rank) to be submitted, got {placed}"
    )


def test_live_submits_the_momentum_winner_under_the_default_mode(db_engine, _live_race_mocks, mocker):
    frames = {"A_HIGHMOM_LOWRANK": _ticker_a_frame_live(), "B_LOWMOM_HIGHRANK": _ticker_b_frame_live()}
    gspc = pd.DataFrame({
        "date": _ticker_a_frame_live()["date"], "open": 4000.0, "high": 4004.0, "low": 3996.0, "close": 4000.0,
    })
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: frames.get(t, gspc).copy())
    mocker.patch("brokers.trading212._live_current_price",
                  side_effect=lambda t: frames.get(t, gspc)["close"].iloc[-1])

    from brokers.trading212 import maybe_place_orders, submit_pending_stock_orders
    cfg = _live_config("absolute_momentum")
    maybe_place_orders(cfg, run_type="full")
    submit_pending_stock_orders(cfg)

    placed = [c.args[0] for c in _live_race_mocks.place_market_order.call_args_list]
    assert placed == ["A_HIGHMOM_LOWRANK_EQ"], (
        f"expected only A (higher absolute momentum) to be submitted under the default mode, got {placed}"
    )


# ---------------------------------------------------------------------------
# The actual parity assertion, plus proof it would catch a real regression
# ---------------------------------------------------------------------------

def test_live_and_backtest_agree_on_the_admission_winner_under_relative_rank_mode(db_engine, _live_race_mocks, mocker):
    """The genuine parity check: both real code paths, run independently
    against the identical crossover scenario, must pick the SAME winner."""
    backtest_winner = _run_backtest_admission_race(mode="relative_rank", mocker=mocker)

    frames = {"A_HIGHMOM_LOWRANK": _ticker_a_frame_live(), "B_LOWMOM_HIGHRANK": _ticker_b_frame_live()}
    gspc = pd.DataFrame({
        "date": _ticker_a_frame_live()["date"], "open": 4000.0, "high": 4004.0, "low": 3996.0, "close": 4000.0,
    })
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: frames.get(t, gspc).copy())
    mocker.patch("brokers.trading212._live_current_price",
                  side_effect=lambda t: frames.get(t, gspc)["close"].iloc[-1])

    from brokers.trading212 import maybe_place_orders, submit_pending_stock_orders
    cfg = _live_config("relative_rank")
    maybe_place_orders(cfg, run_type="full")
    submit_pending_stock_orders(cfg)
    live_winner = {c.args[0].replace("_EQ", "") for c in _live_race_mocks.place_market_order.call_args_list}

    assert backtest_winner == live_winner == {"B_LOWMOM_HIGHRANK"}


def test_parity_scenario_actually_fails_if_backtest_stops_calling_the_shared_resolver(
    db_engine, _live_race_mocks, mocker, monkeypatch,
):
    """Proves this scenario has real discriminating power, the same
    verification standard tests/test_live_backtest_parity.py's own two
    deliberately-injected bugs were held to (see that file's module
    docstring / CLAUDE.md's 'Verified to actually fail' section). Monkey-
    patches analysis.backtest's OWN bound reference to resolve_admission_
    rank_value so it silently ignores admission_ranking_mode and always
    returns momentum -- simulating exactly the regression this parity test
    exists to catch (a call site that quietly stops calling the shared
    function correctly) -- and confirms live and backtest then DISAGREE."""
    import analysis.backtest as backtest_module

    monkeypatch.setattr(
        backtest_module, "resolve_admission_rank_value",
        lambda *, momentum, relative_rank, mode: momentum,  # broken: ignores mode entirely
    )

    broken_backtest_winner = _run_backtest_admission_race(mode="relative_rank", mocker=mocker)

    frames = {"A_HIGHMOM_LOWRANK": _ticker_a_frame_live(), "B_LOWMOM_HIGHRANK": _ticker_b_frame_live()}
    gspc = pd.DataFrame({
        "date": _ticker_a_frame_live()["date"], "open": 4000.0, "high": 4004.0, "low": 3996.0, "close": 4000.0,
    })
    mocker.patch("brokers.trading212.get_price_df", side_effect=lambda t, days=8000: frames.get(t, gspc).copy())
    mocker.patch("brokers.trading212._live_current_price",
                  side_effect=lambda t: frames.get(t, gspc)["close"].iloc[-1])

    from brokers.trading212 import maybe_place_orders, submit_pending_stock_orders
    cfg = _live_config("relative_rank")
    maybe_place_orders(cfg, run_type="full")  # live is NOT broken -- still calls the real shared resolver
    submit_pending_stock_orders(cfg)
    live_winner = {c.args[0].replace("_EQ", "") for c in _live_race_mocks.place_market_order.call_args_list}

    assert broken_backtest_winner == {"A_HIGHMOM_LOWRANK"}, (
        "the injected bug should make the broken backtest fall back to the momentum winner"
    )
    assert live_winner == {"B_LOWMOM_HIGHRANK"}, "live (unbroken) must still pick the relative-rank winner"
    assert broken_backtest_winner != live_winner, (
        "the parity scenario failed to detect a real, injected divergence -- "
        "it has no discriminating power"
    )
