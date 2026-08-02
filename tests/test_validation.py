"""Tests for analysis/validation.py -- the shared full-battery runner used
by both the Backtest page and the due-diligence report generator.
"""
from datetime import date

import pandas as pd
import pytest

from analysis import validation as v2v
from analysis import universe as v2u


@pytest.fixture(autouse=True)
def _clear_membership_cache():
    v2u.clear_membership_cache()
    yield
    v2u.clear_membership_cache()


# ---------------------------------------------------------------------------
# _cagr_dd_for_series -- pure helper
# ---------------------------------------------------------------------------

def test_cagr_dd_for_series_too_short_returns_none():
    s = pd.Series([100.0], index=[pd.Timestamp("2020-01-01")])
    cagr, dd = v2v._cagr_dd_for_series(s, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-01"))
    assert cagr is None and dd is None


def test_cagr_dd_for_series_flat_growth_positive_cagr():
    dates = pd.date_range("2020-01-01", periods=366, freq="D")
    s = pd.Series([100.0 * (1.10 ** (i / 365.25)) for i in range(366)], index=dates)
    cagr, dd = v2v._cagr_dd_for_series(s, dates[0], dates[-1])
    assert cagr == pytest.approx(10.0, abs=0.5)
    assert dd <= 0.0


def test_cagr_dd_for_series_computes_max_drawdown():
    dates = pd.date_range("2020-01-01", periods=10, freq="D")
    vals = [100, 110, 120, 90, 95, 130, 140, 100, 110, 150]
    s = pd.Series(vals, index=dates)
    _, dd = v2v._cagr_dd_for_series(s, dates[0], dates[-1])
    # worst peak-to-trough: peak 140 (day 7) -> trough 100 (day 8) = -28.57%
    assert dd == pytest.approx(-28.57, abs=0.01)


# ---------------------------------------------------------------------------
# _block_passes -- the reconciled per-block pass/fail formula (2026-07-31)
#
# An earlier reconstruction gated on (CAGR >= S&P's own) AND (DD <= 1.5x
# S&P's own) AND (total_return_pct > -20), which reproduced the aggregate
# 14/16 pass count but flagged the WRONG two blocks against the documented
# record ("Block 1, Block 2/GFC-acute"). Reconciliation against the actual
# original criteria (CLAUDE.md's "V2 architecture — phased rebuild" section
# plus this project's own standing PF>1.0 convention, already used
# identically in analysis/backtest.py:walk_forward_pf()) found the real
# formula never had a per-block CAGR gate at all — it's DD<=1.5x S&P's own
# AND profit factor > 1.0. Re-run against the real 2007-2026 production
# reproduction (16 real blocks, real PF/DD per block) with this corrected
# formula reproduces 14/16 pass with EXACTLY blocks 0 and 1 (0-indexed)
# failing — i.e. the documented "Block 1, Block 2/GFC-acute" once the
# original record's own 1-indexed block numbering is accounted for (my
# Block 1, 2008-03-22 to 2009-06-12, is genuinely the Lehman-to-bottom
# acute crash window). These fixture values are the real, production
# per-block metrics from that reconciliation run — pinned here so the
# formula (and this specific historical result) can't silently drift again.
# ---------------------------------------------------------------------------

# (block index, profit_factor, strat_max_drawdown_pct, sp_max_drawdown_pct)
# -- the real 2007-2026 production reproduction, one row per block.
_REAL_BLOCK_METRICS = [
    (0, 0.100, -19.04, -18.64),   # 2007-01-01 -> 2008-03-22 -- FAILS (PF)
    (1, 0.780, -13.59, -52.58),   # 2008-03-22 -> 2009-06-12 -- FAILS (PF) -- the real GFC-acute window
    (2, 1.340, -18.61, -15.99),
    (3, 1.430, -20.78, -19.39),
    (4, 2.370, -5.76, -9.94),
    (5, 2.310, -6.25, -5.76),
    (6, 1.920, -10.20, -7.40),
    (7, 1.480, -15.05, -13.31),
    (8, 2.930, -3.17, -3.07),
    (9, 1.370, -23.73, -19.78),
    (10, 1.550, -23.47, -33.92),
    (11, 2.710, -5.03, -9.60),
    (12, 1.050, -22.51, -25.43),
    (13, 1.910, -14.05, -10.28),
    (14, 1.630, -19.55, -18.90),
    (15, 2.330, -8.09, -9.10),
]


def _strat_metrics(pf, dd):
    return {"profit_factor": pf, "max_drawdown_pct": dd}


def test_block_passes_reproduces_the_real_2007_2026_battery_exactly():
    n_pass, failing = 0, []
    for block_idx, pf, strat_dd, sp_dd in _REAL_BLOCK_METRICS:
        if v2v._block_passes(_strat_metrics(pf, strat_dd), sp_dd):
            n_pass += 1
        else:
            failing.append(block_idx)
    assert n_pass == 14
    assert failing == [0, 1]  # documented "Block 1, Block 2/GFC-acute" under 1-indexing


def test_block_passes_pf_floor_is_strictly_greater_than_one():
    """Matches analysis/backtest.py:walk_forward_pf()'s identical `pf > 1.0`
    convention exactly -- PF == 1.0 must fail, not pass."""
    assert v2v._block_passes(_strat_metrics(1.0, -5.0), -10.0) is False
    assert v2v._block_passes(_strat_metrics(1.0001, -5.0), -10.0) is True


def test_block_passes_dd_floor_is_1point5x_sp_own_drawdown():
    # sp_dd = -10 -> ceiling is -15.0 exactly
    assert v2v._block_passes(_strat_metrics(2.0, -15.0), -10.0) is True
    assert v2v._block_passes(_strat_metrics(2.0, -15.01), -10.0) is False


def test_block_passes_requires_both_pf_and_dd_to_clear():
    # Good PF, blown DD -> fail.
    assert v2v._block_passes(_strat_metrics(3.0, -50.0), -10.0) is False
    # Good DD, blown PF -> fail.
    assert v2v._block_passes(_strat_metrics(0.5, -5.0), -10.0) is False


def test_block_passes_no_sp_dd_data_defaults_dd_leg_to_pass():
    assert v2v._block_passes(_strat_metrics(2.0, -99.0), None) is True


# ---------------------------------------------------------------------------
# make_blocks boundaries -- pinned exactly, so a future change can't
# silently shift which real-world dates each block index refers to.
# Confirmed unchanged (byte-identical, same start/end anchors) since the
# very first V2 commit (afa7794) that introduced make_blocks() — a boundary
# mismatch was explicitly ruled out as the cause of the pass/fail
# discrepancy reconciled above, this just makes that permanent.
# ---------------------------------------------------------------------------

_EXPECTED_BLOCK_BOUNDARIES = [
    ("2007-01-01", "2008-03-22"), ("2008-03-22", "2009-06-12"),
    ("2009-06-12", "2010-09-02"), ("2010-09-02", "2011-11-23"),
    ("2011-11-23", "2013-02-12"), ("2013-02-12", "2014-05-05"),
    ("2014-05-05", "2015-07-26"), ("2015-07-26", "2016-10-15"),
    ("2016-10-15", "2018-01-04"), ("2018-01-04", "2019-03-27"),
    ("2019-03-27", "2020-06-16"), ("2020-06-16", "2021-09-06"),
    ("2021-09-06", "2022-11-27"), ("2022-11-27", "2024-02-17"),
    ("2024-02-17", "2025-05-09"), ("2025-05-09", "2026-07-30"),
]


def test_make_blocks_boundaries_pinned_to_the_reconciled_dates():
    from analysis.backtest import make_blocks
    blocks = make_blocks(pd.Timestamp("2007-01-01"), pd.Timestamp("2026-07-30"), n_blocks=16)
    assert len(blocks) == 16
    actual = [(b[0].date().isoformat(), b[1].date().isoformat()) for b in blocks]
    assert actual == _EXPECTED_BLOCK_BOUNDARIES


# ---------------------------------------------------------------------------
# universe_snapshot
# ---------------------------------------------------------------------------

def test_universe_snapshot_shape(db_engine):
    v2u.seed_baseline_membership()
    snap = v2v.universe_snapshot(as_of=date(2026, 7, 30))
    assert snap["etf_count"] == len(v2u.ETF_TICKERS)
    assert snap["stock_candidate_count"] == len(v2u.STOCK_MEMBERSHIP)
    assert snap["total_candidates"] == len(v2u.ALL_TICKERS)
    assert snap["live_tradeable_count"] == snap["etf_count"] + snap["stock_member_count"]
    assert "GOLD" in snap["non_members"]  # GOLD has an empty membership window by design


def test_universe_snapshot_defaults_as_of_to_today(db_engine):
    v2u.seed_baseline_membership()
    snap = v2v.universe_snapshot()
    assert snap["as_of"] == date.today().isoformat()


# ---------------------------------------------------------------------------
# run_full_validation -- heavily mocked (full real run is a multi-minute,
# production-DB-scale operation covered by the manual reproducibility check
# documented in CLAUDE.md, not something to replay verbatim in the unit
# suite). This test exercises the block pass/fail wiring, cost-sensitivity
# and train/test split logic against small synthetic trades.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# compute_gbp_growth -- pure helper
# ---------------------------------------------------------------------------

def test_compute_gbp_growth_sp500_ending_matches_price_ratio():
    """S&P leg is starting_capital * (end_price / start_price) directly --
    not re-derived from a rounded CAGR percentage."""
    dates = pd.date_range("2019-01-01", periods=400, freq="D")
    close = pd.Series([100.0 * (1.10 ** (i / 365.25)) for i in range(len(dates))], index=dates)

    gg = v2v.compute_gbp_growth(
        close, [], [], 50000.0,
        dates[0], dates[200], dates[-1],
    )
    train_expected = 50000.0 * (float(close.loc[dates[200]]) / float(close.loc[dates[0]]))
    assert gg["train"]["sp500_ending_gbp"] == pytest.approx(train_expected, abs=1.0)


def test_compute_gbp_growth_strategy_ending_matches_equity_curve_gbp():
    """Strategy leg is equity_curve_gbp()'s own final balance -- the same
    additive-£ methodology as everywhere else, not a differently-computed
    figure."""
    from analysis.backtest import equity_curve_gbp

    dates = pd.date_range("2019-01-01", periods=10, freq="D")
    close = pd.Series([100.0] * len(dates), index=dates)
    trades = [
        _synthetic_trade("VOO", "2019-01-01", "2019-01-03", 0.05, position_gbp=1000.0),
        _synthetic_trade("VOO", "2019-01-04", "2019-01-06", -0.02, position_gbp=1000.0),
    ]

    gg = v2v.compute_gbp_growth(close, trades, [], 50000.0, dates[0], dates[-1], dates[-1])
    expected = float(equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=50000.0)["equity_gbp"].iloc[-1])
    assert gg["train"]["strategy_ending_gbp"] == pytest.approx(expected, abs=0.01)


def test_compute_gbp_growth_no_trades_stays_at_starting_capital():
    dates = pd.date_range("2019-01-01", periods=10, freq="D")
    close = pd.Series([100.0] * len(dates), index=dates)
    gg = v2v.compute_gbp_growth(close, [], [], 50000.0, dates[0], dates[5], dates[-1])
    assert gg["train"]["strategy_ending_gbp"] == 50000.0
    assert gg["test"]["strategy_ending_gbp"] == 50000.0


def _synthetic_trade(ticker, entry, exit_, pnl_pct, position_gbp=1000.0):
    return {
        "ticker": ticker, "direction": "LONG",
        "entry_date": pd.Timestamp(entry), "exit_date": pd.Timestamp(exit_),
        "entry_price": 100.0, "exit_price": 100.0 * (1 + pnl_pct),
        "exit_reason": "SIGNAL_OFF", "pnl_pct": pnl_pct,
        "position_gbp": position_gbp, "pnl_gbp": position_gbp * pnl_pct,
        "stop_distance_pct": 5.0, "holding_days": 20, "is_win": pnl_pct > 0,
    }


def test_run_full_validation_shape_and_train_test_split(db_engine, mocker):
    v2u.seed_baseline_membership()

    gspc_dates = pd.date_range("2015-01-01", "2026-07-30", freq="D")
    gspc_df = pd.DataFrame({
        "date": gspc_dates,
        "close": [100.0 * (1.0002 ** i) for i in range(len(gspc_dates))],
    })
    mocker.patch("analysis.validation.get_price_df", return_value=gspc_df)
    mocker.patch("analysis.validation.precompute_ticker", return_value={"ticker": "VOO", "frame": pd.DataFrame()})
    mocker.patch("analysis.validation.slice_ticker", return_value={"ticker": "VOO", "frame": pd.DataFrame()})

    trades = [
        _synthetic_trade("VOO", "2018-01-01", "2018-06-01", 0.05),   # train
        _synthetic_trade("VOO", "2021-01-01", "2021-06-01", -0.02),  # test
        _synthetic_trade("VOO", "2022-01-01", "2022-06-01", 0.10),   # test
    ]
    mocker.patch("analysis.validation.run_candidate", return_value=trades)

    # uncapped=True forces run_candidate() (mocked above) -- this test is
    # about the shape of the returned dict / train-test-split logic, not
    # about which engine is the default (2026-08-01: a bare call now uses
    # simulate_portfolio_compounding() instead -- see
    # test_default_engine_is_compounding_and_config_sourced below).
    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        uncapped=True,
    )

    assert result["total_trades"] == 3
    assert result["train"]["total_trades"] == 1
    assert result["test"]["total_trades"] == 2
    assert result["n_blocks_total"] == 16
    assert 0 <= result["n_blocks_pass"] <= 16
    assert set(result["cost_sensitivity"].keys()) == {0, 10, 25}
    assert "walk_forward" in result
    assert result["full_raw"]["total_trades"] == 3
    assert result["full_ex_best"]["total_trades"] == 2
    assert result["train_sp"]["cagr_pct"] is not None
    assert result["test_sp"]["cagr_pct"] is not None

    gg = result["gbp_growth"]
    assert gg["starting_capital_gbp"] == 10000
    assert set(gg.keys()) == {"starting_capital_gbp", "train", "test"}
    for leg in ("train", "test"):
        assert set(gg[leg].keys()) == {"strategy_ending_gbp", "sp500_ending_gbp"}


# ---------------------------------------------------------------------------
# Engine selection (2026-08-01) -- a bare call now defaults to the same
# compounding, portfolio-capped engine live order placement uses, sourced
# from trading212_invest.per_trade_pct / max_total_deployed in config; the
# three older engines remain explicitly selectable for reproducing specific
# historical figures already published in CLAUDE.md. See CLAUDE.md's "Live
# execution switched to the compounding two-parameter scheme" section.
# ---------------------------------------------------------------------------

def _engine_selection_setup(mocker):
    v2u.seed_baseline_membership()
    gspc_dates = pd.date_range("2015-01-01", "2026-07-30", freq="D")
    gspc_df = pd.DataFrame({
        "date": gspc_dates, "close": [100.0 * (1.0002 ** i) for i in range(len(gspc_dates))],
    })
    mocker.patch("analysis.validation.get_price_df", return_value=gspc_df)
    mocker.patch("analysis.validation.precompute_ticker", return_value={"ticker": "VOO", "frame": pd.DataFrame()})
    mocker.patch("analysis.validation.slice_ticker", return_value={"ticker": "VOO", "frame": pd.DataFrame()})


def test_default_engine_is_compounding_and_config_sourced(db_engine, mocker):
    """A bare run_full_validation(config) call -- no engine flags at all --
    must call simulate_portfolio_compounding() with per_trade_pct /
    max_total_deployed read from config["trading212_invest"], not
    run_candidate() or simulate_portfolio()."""
    _engine_selection_setup(mocker)
    mock_uncapped = mocker.patch("analysis.validation.run_candidate", return_value=[])
    mock_capped = mocker.patch("analysis.validation.simulate_portfolio", return_value=[])
    mock_compounding = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])

    config = {"backtest": {"starting_capital_gbp": 10000},
              "trading212_invest": {"per_trade_pct": 0.07, "max_total_deployed": 0.6}}
    result = v2v.run_full_validation(config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"))

    mock_uncapped.assert_not_called()
    mock_capped.assert_not_called()
    mock_compounding.assert_called_once()
    call_args = mock_compounding.call_args[0]
    assert call_args[3] == pytest.approx(0.07)   # per_trade_pct, positional
    assert call_args[4] == pytest.approx(0.6)    # max_deployed_pct, positional
    assert result["engine_used"] == "compounding_default"
    assert result["per_trade_pct"] == pytest.approx(0.07)
    assert result["max_deployed_pct"] == pytest.approx(0.6)


def test_default_engine_falls_back_to_5pct_50pct_when_config_absent(db_engine, mocker):
    """No trading212_invest section at all -- the documented, recommended
    5% / 50% defaults apply (matching config.yaml's own shipped values)."""
    _engine_selection_setup(mocker)
    mock_compounding = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
    )

    call_args = mock_compounding.call_args[0]
    assert call_args[3] == pytest.approx(0.05)
    assert call_args[4] == pytest.approx(0.5)
    assert result["engine_used"] == "compounding_default"


def test_mark_to_market_flag_forces_mtm_engine_not_cost_basis(db_engine, mocker):
    """mark_to_market=True on an otherwise-default call must dispatch to
    simulate_portfolio_compounding_mtm(), never the cost-basis
    simulate_portfolio_compounding() -- and record deployment_basis
    accordingly, so a caller reading the result dict can tell which
    accounting basis produced it without inspecting engine_used alone."""
    _engine_selection_setup(mocker)
    mock_cost_basis = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])
    mock_mtm = mocker.patch("analysis.validation.simulate_portfolio_compounding_mtm", return_value=[])

    config = {"backtest": {"starting_capital_gbp": 10000},
              "trading212_invest": {"per_trade_pct": 0.05, "max_total_deployed": 0.5}}
    result = v2v.run_full_validation(
        config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"), mark_to_market=True,
    )

    mock_cost_basis.assert_not_called()
    mock_mtm.assert_called_once()
    assert result["engine_used"] == "compounding_default"
    assert result["deployment_basis"] == "mark_to_market"


def test_mark_to_market_flag_defaults_false_preserving_cost_basis_behaviour(db_engine, mocker):
    """The default (mark_to_market not passed at all) must be completely
    unchanged -- every figure already published against the cost-basis
    engine stays reproducible without any caller needing to opt in."""
    _engine_selection_setup(mocker)
    mock_cost_basis = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])
    mock_mtm = mocker.patch("analysis.validation.simulate_portfolio_compounding_mtm", return_value=[])

    config = {"backtest": {"starting_capital_gbp": 10000},
              "trading212_invest": {"per_trade_pct": 0.05, "max_total_deployed": 0.5}}
    result = v2v.run_full_validation(config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"))

    mock_mtm.assert_not_called()
    mock_cost_basis.assert_called_once()
    assert result["deployment_basis"] == "cost_basis"


def test_mark_to_market_flag_has_no_effect_under_uncapped_or_portfolio_mode(db_engine, mocker):
    """mark_to_market only applies to the two compounding variants --
    passing it alongside uncapped or portfolio_mode must not silently
    call the mtm engine instead of the one those flags actually select,
    and deployment_basis must correctly read cost_basis (there is no
    mark-to-market variant of either legacy engine)."""
    _engine_selection_setup(mocker)
    mock_uncapped = mocker.patch("analysis.validation.run_candidate", return_value=[])
    mock_mtm = mocker.patch("analysis.validation.simulate_portfolio_compounding_mtm", return_value=[])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        uncapped=True, mark_to_market=True,
    )

    mock_uncapped.assert_called_once()
    mock_mtm.assert_not_called()
    assert result["engine_used"] == "uncapped"
    assert result["deployment_basis"] == "cost_basis"


def test_uncapped_flag_forces_run_candidate(db_engine, mocker):
    _engine_selection_setup(mocker)
    mock_uncapped = mocker.patch("analysis.validation.run_candidate", return_value=[])
    mock_compounding = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        uncapped=True,
    )

    mock_uncapped.assert_called_once()
    mock_compounding.assert_not_called()
    assert result["engine_used"] == "uncapped"
    assert result["per_trade_pct"] is None
    assert result["max_deployed_pct"] is None


def test_portfolio_mode_flag_forces_simulate_portfolio(db_engine, mocker):
    _engine_selection_setup(mocker)
    mock_capped = mocker.patch("analysis.validation.simulate_portfolio", return_value=[])
    mock_compounding = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        portfolio_mode=True,
    )

    mock_capped.assert_called_once()
    mock_compounding.assert_not_called()
    assert result["engine_used"] == "portfolio_capped"


def test_explicit_compounding_params_override_config_and_take_priority_over_portfolio_mode(db_engine, mocker):
    """Explicit per_trade_pct/max_deployed_pct_compounding (the research-
    sweep interface) use those exact values, not config -- and take
    priority over portfolio_mode=True if both were somehow passed together
    (documented as not a supported combination, resolved deterministically
    rather than raising)."""
    _engine_selection_setup(mocker)
    mock_capped = mocker.patch("analysis.validation.simulate_portfolio", return_value=[])
    mock_compounding = mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])

    config = {"backtest": {"starting_capital_gbp": 10000},
              "trading212_invest": {"per_trade_pct": 0.05, "max_total_deployed": 0.5}}
    result = v2v.run_full_validation(
        config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        portfolio_mode=True, per_trade_pct=0.10, max_deployed_pct_compounding=0.90,
    )

    mock_capped.assert_not_called()
    mock_compounding.assert_called_once()
    call_args = mock_compounding.call_args[0]
    assert call_args[3] == pytest.approx(0.10)
    assert call_args[4] == pytest.approx(0.90)
    assert result["engine_used"] == "compounding_explicit"


# ---------------------------------------------------------------------------
# Asset-class trading switches (2026-08-01) -- etf_trading_enabled /
# stock_trading_enabled must filter the CANDIDATE universe itself, before
# any engine's tickers_cache is built, using the identical shared function
# (analysis.decision.is_asset_class_enabled()) brokers/trading212.py's live
# decide phase checks -- see tests/test_execution.py's
# test_decide_phase_calls_the_shared_asset_class_gate for the live-side
# half of this same guarantee. See CLAUDE.md's "Clean, independent
# ETF/stock trading switches" section.
# ---------------------------------------------------------------------------

def _candidate_universe_setup(mocker):
    v2u.seed_baseline_membership()
    gspc_dates = pd.date_range("2015-01-01", "2026-07-30", freq="D")
    gspc_df = pd.DataFrame({
        "date": gspc_dates, "close": [100.0 * (1.0002 ** i) for i in range(len(gspc_dates))],
    })
    mocker.patch("analysis.validation.get_price_df", return_value=gspc_df)
    requested: list = []

    def _fake_precompute(ticker, config):
        requested.append(ticker)
        return None

    mocker.patch("analysis.validation.precompute_ticker", side_effect=_fake_precompute)
    mocker.patch("analysis.validation.run_candidate", return_value=[])
    return requested


def test_default_candidate_universe_includes_every_ticker(db_engine, mocker):
    """No trading212_invest keys at all -- both switches default True, so
    the candidate pool is exactly analysis.universe.ALL_TICKERS, unchanged
    from before this feature existed."""
    requested = _candidate_universe_setup(mocker)

    result = v2v.run_full_validation(
        {"backtest": {}}, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        uncapped=True,
    )

    assert set(requested) == set(v2u.ALL_TICKERS)
    assert result["n_tickers_total"] == len(v2u.ALL_TICKERS)
    assert result["etf_trading_enabled"] is True
    assert result["stock_trading_enabled"] is True


def test_etf_trading_disabled_excludes_etfs_from_candidate_universe(db_engine, mocker):
    """A disabled ETF class must never even be precompute_ticker()'d --
    genuinely excluded from candidacy, not merely filtered out of the
    result afterward -- while every stock candidate is completely
    unaffected."""
    requested = _candidate_universe_setup(mocker)

    config = {"backtest": {}, "trading212_invest": {"etf_trading_enabled": False}}
    result = v2v.run_full_validation(
        config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"), uncapped=True,
    )

    assert set(requested) == set(v2u.STOCK_MEMBERSHIP.keys())
    assert not (set(requested) & set(v2u.ETF_TICKERS))  # never even requested
    assert result["n_tickers_total"] == len(v2u.STOCK_MEMBERSHIP)
    assert result["etf_trading_enabled"] is False
    assert result["stock_trading_enabled"] is True


def test_stock_trading_disabled_excludes_stocks_from_candidate_universe(db_engine, mocker):
    requested = _candidate_universe_setup(mocker)

    config = {"backtest": {}, "trading212_invest": {"stock_trading_enabled": False}}
    result = v2v.run_full_validation(
        config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"), uncapped=True,
    )

    assert set(requested) == set(v2u.ETF_TICKERS)
    assert not (set(requested) & set(v2u.STOCK_MEMBERSHIP.keys()))
    assert result["n_tickers_total"] == len(v2u.ETF_TICKERS)
    assert result["etf_trading_enabled"] is True
    assert result["stock_trading_enabled"] is False


def test_run_full_validation_calls_the_same_shared_gate_function_as_live(db_engine, mocker):
    """Proves run_full_validation() genuinely delegates to
    analysis.decision.is_asset_class_enabled() per ticker -- the identical
    function brokers/trading212.py's decide phase calls (see
    tests/test_execution.py's
    test_decide_phase_calls_the_shared_asset_class_gate) -- rather than
    re-deriving the same config lookup independently. This is the
    backtest-side half of the live/backtest parity guarantee for this
    feature."""
    import analysis.validation as validation_module

    spy = mocker.spy(validation_module, "is_asset_class_enabled")
    _candidate_universe_setup(mocker)

    config = {"backtest": {}, "trading212_invest": {"etf_trading_enabled": False}}
    v2v.run_full_validation(
        config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"), uncapped=True,
    )

    # Two summary calls (etf_trading_enabled / stock_trading_enabled) plus
    # one per ticker in the full universe while building the candidate list.
    assert spy.call_count == 2 + len(v2u.ALL_TICKERS)
    for call in spy.call_args_list:
        assert call.kwargs["config"] is config


def test_run_full_validation_no_trades_all_blocks_fail_gracefully(db_engine, mocker):
    v2u.seed_baseline_membership()
    gspc_dates = pd.date_range("2015-01-01", "2026-07-30", freq="D")
    gspc_df = pd.DataFrame({
        "date": gspc_dates, "close": [100.0 + i * 0.01 for i in range(len(gspc_dates))],
    })
    mocker.patch("analysis.validation.get_price_df", return_value=gspc_df)
    mocker.patch("analysis.validation.precompute_ticker", return_value=None)
    mocker.patch("analysis.validation.slice_ticker", return_value=None)
    mocker.patch("analysis.validation.run_candidate", return_value=[])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
    )
    assert result["total_trades"] == 0
    assert result["n_tickers_with_data"] == 0
    assert result["train"]["total_trades"] == 0
    assert result["test"]["total_trades"] == 0


# ---------------------------------------------------------------------------
# live_universe_snapshot() -- the dynamic-pool counterpart to
# universe_snapshot() above. Reports genuine S&P 500 index membership
# eligibility only, never T212 tradeability (2026-08-02).
# ---------------------------------------------------------------------------

def test_live_universe_snapshot_shape(db_engine):
    v2u.seed_baseline_membership()
    snap = v2v.live_universe_snapshot(as_of=date(2026, 7, 30))
    assert snap["as_of"] == "2026-07-30"
    known = v2u.get_all_known_stock_tickers()
    eligible = v2u.get_live_stock_candidates(as_of=date(2026, 7, 30))
    assert snap["candidate_universe_count"] == len(known)
    assert snap["live_eligible_count"] == len(eligible)
    assert snap["excluded_count"] == len(known) - len(eligible)
    assert "GOLD" in snap["excluded_sample"] or snap["excluded_count"] > len(snap["excluded_sample"])
    # No T212-tradeability concept anywhere in this dict -- membership only.
    assert "etf_count" not in snap
    assert not any("t212" in k.lower() or "tradeable" in k.lower() for k in snap)


def test_live_universe_snapshot_defaults_as_of_to_today(db_engine):
    v2u.seed_baseline_membership()
    snap = v2v.live_universe_snapshot()
    assert snap["as_of"] == date.today().isoformat()


# ---------------------------------------------------------------------------
# run_full_validation(live_universe=True) -- the genuinely dynamic,
# live-execution-matching candidate pool, replacing the frozen 73-ticker
# universe.ALL_TICKERS. See CLAUDE.md's "rebuild the backtest/DD-report
# page to genuinely run against the current, real live engine" (2026-08-02).
# ---------------------------------------------------------------------------

def _live_universe_setup(mocker, live_candidates):
    v2u.seed_baseline_membership()
    gspc_dates = pd.date_range("2015-01-01", "2026-07-30", freq="D")
    gspc_df = pd.DataFrame({
        "date": gspc_dates, "close": [100.0 * (1.0002 ** i) for i in range(len(gspc_dates))],
    })
    mocker.patch("analysis.validation.get_price_df", return_value=gspc_df)
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=live_candidates)
    requested: list = []

    def _fake_precompute(ticker, config):
        requested.append(ticker)
        return None

    mocker.patch("analysis.validation.precompute_ticker", side_effect=_fake_precompute)
    mocker.patch("analysis.validation.run_candidate", return_value=[])
    mocker.patch("analysis.validation.simulate_portfolio_compounding", return_value=[])
    return requested


def test_live_universe_true_uses_the_dynamic_candidate_pool_not_all_tickers(db_engine, mocker):
    """The frozen 73-ticker universe.ALL_TICKERS must never be touched when
    live_universe=True -- candidate_tickers must come from
    get_live_stock_candidates() alone, including tickers that were never
    in the old curated-40/ETF-33 set at all."""
    requested = _live_universe_setup(mocker, ["MU", "LRCX", "AAPL"])

    result = v2v.run_full_validation(
        {"backtest": {}}, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        live_universe=True,
    )

    assert set(requested) == {"MU", "LRCX", "AAPL"}
    assert not (set(requested) & set(v2u.ETF_TICKERS))  # never any ETF
    assert result["n_tickers_total"] == 3
    assert result["live_universe"] is True


def test_live_universe_false_default_unchanged_frozen_universe(db_engine, mocker):
    requested = _live_universe_setup(mocker, ["MU", "LRCX", "AAPL"])  # must be ignored entirely

    result = v2v.run_full_validation(
        {"backtest": {}}, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
    )

    assert set(requested) == set(v2u.ALL_TICKERS)
    assert result["live_universe"] is False


def test_live_universe_respects_stock_trading_enabled_switch(db_engine, mocker):
    requested = _live_universe_setup(mocker, ["MU", "LRCX"])

    config = {"backtest": {}, "trading212_invest": {"stock_trading_enabled": False}}
    result = v2v.run_full_validation(
        config, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"), live_universe=True,
    )

    assert requested == []
    assert result["n_tickers_total"] == 0


def test_live_universe_result_universe_key_uses_live_snapshot(db_engine, mocker):
    """result["universe"] must reflect whichever universe THIS run
    actually used -- live_universe_snapshot() when live_universe=True,
    never the frozen universe_snapshot()."""
    _live_universe_setup(mocker, ["MU", "LRCX"])

    result = v2v.run_full_validation(
        {"backtest": {}}, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
        live_universe=True,
    )

    assert "candidate_universe_count" in result["universe"]
    assert "etf_count" not in result["universe"]


def test_live_universe_false_result_universe_key_uses_frozen_snapshot(db_engine, mocker):
    requested = _live_universe_setup(mocker, ["MU", "LRCX"])
    del requested  # unused; asserting on result only

    result = v2v.run_full_validation(
        {"backtest": {}}, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
    )

    assert "etf_count" in result["universe"]  # the frozen-universe snapshot's own field


def test_result_always_states_the_continuous_walk_convention(db_engine, mocker):
    """Every run, regardless of any flag, is a continuous walk -- per the
    standing labeling requirement, this must be explicit in the result,
    not left for a caller to infer."""
    _live_universe_setup(mocker, ["MU"])

    for kwargs in ({}, {"live_universe": True}, {"uncapped": True}, {"portfolio_mode": True}):
        result = v2v.run_full_validation(
            {"backtest": {}}, start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"), **kwargs,
        )
        assert result["backtest_convention"] == "continuous_walk"


def test_result_includes_peak_relative_drawdown_always(db_engine, mocker):
    """peak_relative_drawdown must be present regardless of live_universe/
    engine flags -- any run with a real equity curve can show the
    fixed-denominator/peak-relative gap, not only live_universe runs."""
    _live_universe_setup(mocker, ["MU"])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
    )

    assert set(result["peak_relative_drawdown"].keys()) == {"train", "test", "full"}


def test_result_peak_relative_drawdown_none_when_no_trades(db_engine, mocker):
    v2u.seed_baseline_membership()
    gspc_dates = pd.date_range("2015-01-01", "2026-07-30", freq="D")
    gspc_df = pd.DataFrame({
        "date": gspc_dates, "close": [100.0 + i * 0.01 for i in range(len(gspc_dates))],
    })
    mocker.patch("analysis.validation.get_price_df", return_value=gspc_df)
    mocker.patch("analysis.validation.precompute_ticker", return_value=None)
    mocker.patch("analysis.validation.slice_ticker", return_value=None)
    mocker.patch("analysis.validation.run_candidate", return_value=[])

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
    )
    assert result["peak_relative_drawdown"] == {"train": None, "test": None, "full": None}
