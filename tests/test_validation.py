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

    result = v2v.run_full_validation(
        {"backtest": {"starting_capital_gbp": 10000}},
        start=pd.Timestamp("2015-01-01"), end=pd.Timestamp("2026-07-30"),
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
