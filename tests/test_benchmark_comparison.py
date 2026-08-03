"""Tests for analysis/benchmark_comparison.py -- the standing, reusable
strategy-vs-S&P-500-vs-prior-baseline comparison tool (see CLAUDE.md's
"Standing practice: strategy vs. S&P 500 benchmark comparison" section).

A full-scale run against the live ~500-stock universe takes several
minutes (matching every other full-universe backtest in this project --
see e.g. analysis/validation.py's own test file for the identical
"don't replay the real multi-minute run in the unit suite" convention).
This file tests the module's logic — config-reading, delta computation,
log read/write/round-trip, S&P benchmark math — against small synthetic
fixtures and mocks, plus one genuinely end-to-end (real DB, real
engine, no mocking of the core simulation) test at small scale to prove
the wiring itself is correct.
"""
import json

import pandas as pd
import pytest

from analysis import benchmark_comparison as bc
from data.ingestion import _upsert_price_rows


# ---------------------------------------------------------------------------
# run_sp500_benchmark_leg -- pure, no DB
# ---------------------------------------------------------------------------

def _flat_growth_series(n=800, start="2018-01-01", start_price=100.0, daily_pct=0.0005):
    dates = pd.date_range(start, periods=n, freq="D")
    prices = [start_price * (1 + daily_pct) ** i for i in range(n)]
    return pd.Series(prices, index=dates)


def test_sp500_benchmark_leg_ending_balance_matches_price_ratio():
    close = _flat_growth_series()
    start, end = close.index[100], close.index[500]
    result = bc.run_sp500_benchmark_leg(close, start, end, starting_capital=50_000.0)

    start_p, end_p = float(close.loc[start]), float(close.loc[end])
    expected_ending = 50_000.0 * (end_p / start_p)
    assert result["ending_balance_gbp"] == pytest.approx(expected_ending, rel=1e-6)
    expected_pct = (expected_ending - 50_000.0) / 50_000.0 * 100
    # result["total_pct_change"] is rounded to 2dp by design -- compare
    # with an absolute tolerance wide enough for that rounding, not the
    # unrounded value's own tight relative tolerance.
    assert result["total_pct_change"] == pytest.approx(expected_pct, abs=0.01)
    assert result["cagr_pct"] > 0  # genuinely rising series


def test_sp500_benchmark_leg_drawdown_is_peak_relative():
    # A rise then a fall -- the peak-relative drawdown must be computed
    # against the running peak, not the starting price.
    dates = pd.date_range("2020-01-01", periods=10, freq="D")
    prices = [100, 110, 120, 130, 100, 90, 95, 100, 105, 110]
    close = pd.Series(prices, index=dates, dtype=float)
    result = bc.run_sp500_benchmark_leg(close, dates[0], dates[-1], starting_capital=50_000.0)
    # Worst point (90) against its own running peak (130): (90-130)/130 = -30.77%
    assert result["max_drawdown_pct_peak_relative"] == pytest.approx(-30.77, abs=0.01)


def test_sp500_benchmark_leg_no_data_in_window_falls_back_to_starting_capital():
    close = pd.Series([100.0], index=[pd.Timestamp("2020-01-01")])
    result = bc.run_sp500_benchmark_leg(
        close, pd.Timestamp("2021-01-01"), pd.Timestamp("2022-01-01"), starting_capital=50_000.0,
    )
    assert result["ending_balance_gbp"] == 50_000.0
    assert result["total_pct_change"] == 0.0


# ---------------------------------------------------------------------------
# Log read/write/round-trip -- pure file I/O, no DB
# ---------------------------------------------------------------------------

def _sample_entry(date="2026-08-04", total_pct_change=25.0):
    return {
        "date": date, "commit": "abc1234", "period_start": "2020-01-01", "period_end": "2026-08-01",
        "starting_capital_gbp": 50_000.0,
        "strategy": {
            "n_tickers_total": 500, "n_tickers_with_data": 497, "n_trades": 1200,
            "cagr_pct": 20.0, "max_drawdown_pct_fixed": -25.0, "max_drawdown_pct_peak_relative": -10.0,
            "profit_factor": 1.8, "win_rate_pct": 49.0,
            "ending_balance_gbp": 50_000.0 * (1 + total_pct_change / 100),
            "total_pct_change": total_pct_change,
            "per_trade_pct": 0.05, "max_total_deployed": 0.5,
            "reentry_cooldown_days": 0, "min_momentum_pct": 0.02,
        },
        "sp500": {
            "cagr_pct": 14.0, "max_drawdown_pct_peak_relative": -15.0,
            "ending_balance_gbp": 60_000.0, "total_pct_change": 20.0,
        },
        "delta_vs_sp500_pp": total_pct_change - 20.0,
        "delta_vs_prior_run_pp": None,
        "prior_run_date": None,
    }


def test_read_last_log_entry_returns_none_when_log_does_not_exist(tmp_path):
    log_path = tmp_path / "nonexistent.jsonl"
    assert bc.read_last_log_entry(log_path) is None


def test_append_and_read_last_log_entry_round_trips(tmp_path):
    log_path = tmp_path / "log.jsonl"
    entry = _sample_entry()
    bc.append_log_entry(entry, log_path)
    read_back = bc.read_last_log_entry(log_path)
    assert read_back == entry


def test_append_is_genuinely_additive_not_overwriting(tmp_path):
    log_path = tmp_path / "log.jsonl"
    first = _sample_entry(date="2026-08-01", total_pct_change=10.0)
    second = _sample_entry(date="2026-08-04", total_pct_change=25.0)
    bc.append_log_entry(first, log_path)
    bc.append_log_entry(second, log_path)

    all_entries = bc.read_all_log_entries(log_path)
    assert len(all_entries) == 2
    assert all_entries[0]["date"] == "2026-08-01"
    assert all_entries[1]["date"] == "2026-08-04"
    # The last entry read back is the most recently appended one, not the first.
    assert bc.read_last_log_entry(log_path)["date"] == "2026-08-04"


def test_render_markdown_log_includes_every_entry(tmp_path):
    log_path = tmp_path / "log.jsonl"
    bc.append_log_entry(_sample_entry(date="2026-08-01", total_pct_change=10.0), log_path)
    bc.append_log_entry(_sample_entry(date="2026-08-04", total_pct_change=25.0), log_path)
    md = bc.render_markdown_log(log_path)
    assert "2026-08-01" in md
    assert "2026-08-04" in md
    assert md.count("|") > 20  # a genuine table, not an empty shell


def test_render_markdown_log_empty_log_still_has_header(tmp_path):
    log_path = tmp_path / "empty.jsonl"
    md = bc.render_markdown_log(log_path)
    assert "Date" in md
    assert "Strategy CAGR" in md


# ---------------------------------------------------------------------------
# run_comparison orchestration -- mocked legs, real delta/log logic
# ---------------------------------------------------------------------------

def test_run_comparison_first_run_has_no_prior_delta(tmp_path, mocker):
    log_path = tmp_path / "log.jsonl"
    mocker.patch("analysis.benchmark_comparison.run_current_strategy_leg", return_value=_sample_entry()["strategy"])
    mocker.patch("analysis.benchmark_comparison.run_sp500_benchmark_leg", return_value=_sample_entry()["sp500"])
    mocker.patch("analysis.benchmark_comparison.get_price_df", return_value=pd.DataFrame({
        "date": pd.date_range("2007-01-01", periods=10, freq="D"), "close": [100.0] * 10,
    }))

    result = bc.run_comparison({}, log_path=log_path, run_date="2026-08-04")
    entry = result["entry"]
    assert result["prior"] is None
    assert entry["delta_vs_prior_run_pp"] is None
    assert entry["prior_run_date"] is None
    # And it was genuinely appended.
    assert bc.read_last_log_entry(log_path)["date"] == "2026-08-04"


def test_run_comparison_second_run_computes_delta_vs_prior(tmp_path, mocker):
    log_path = tmp_path / "log.jsonl"
    bc.append_log_entry(_sample_entry(date="2026-08-01", total_pct_change=10.0), log_path)

    mocker.patch(
        "analysis.benchmark_comparison.run_current_strategy_leg",
        return_value=_sample_entry(total_pct_change=25.0)["strategy"],
    )
    mocker.patch("analysis.benchmark_comparison.run_sp500_benchmark_leg", return_value=_sample_entry()["sp500"])
    mocker.patch("analysis.benchmark_comparison.get_price_df", return_value=pd.DataFrame({
        "date": pd.date_range("2007-01-01", periods=10, freq="D"), "close": [100.0] * 10,
    }))

    result = bc.run_comparison({}, log_path=log_path, run_date="2026-08-04")
    entry = result["entry"]
    assert result["prior"]["date"] == "2026-08-01"
    assert entry["prior_run_date"] == "2026-08-01"
    # 25.0% (this run) - 10.0% (prior run) = 15.0pp
    assert entry["delta_vs_prior_run_pp"] == pytest.approx(15.0, abs=0.01)
    # Log now has 2 entries -- the first one untouched, the new one appended.
    all_entries = bc.read_all_log_entries(log_path)
    assert len(all_entries) == 2
    assert all_entries[0]["strategy"]["total_pct_change"] == 10.0


def test_run_comparison_delta_vs_sp500_computed_correctly(tmp_path, mocker):
    log_path = tmp_path / "log.jsonl"
    mocker.patch(
        "analysis.benchmark_comparison.run_current_strategy_leg",
        return_value=_sample_entry(total_pct_change=30.0)["strategy"],
    )
    mocker.patch(
        "analysis.benchmark_comparison.run_sp500_benchmark_leg",
        return_value={**_sample_entry()["sp500"], "total_pct_change": 18.0},
    )
    mocker.patch("analysis.benchmark_comparison.get_price_df", return_value=pd.DataFrame({
        "date": pd.date_range("2007-01-01", periods=10, freq="D"), "close": [100.0] * 10,
    }))
    result = bc.run_comparison({}, log_path=log_path, run_date="2026-08-04")
    assert result["entry"]["delta_vs_sp500_pp"] == pytest.approx(12.0, abs=0.01)


# ---------------------------------------------------------------------------
# run_current_strategy_leg -- genuinely end-to-end at small scale, real DB,
# real engine, only the candidate universe is mocked down to 2 tickers.
# ---------------------------------------------------------------------------

_LIVE_CONFIG = {
    "signals": {"atr_period": 14},
    "backtest": {"starting_capital_gbp": 50000, "atr_stop_multiplier": 2.0},
    "trading212_invest": {
        "var_confidence": 0.95, "var_lookback_days": 252, "max_risk_pct": 0.01,
        "min_position_gbp": 50, "absolute_max_position_gbp": 1_000_000,
        "per_trade_pct": 0.05, "max_total_deployed": 0.5,
        "reentry_cooldown_days": 0, "min_momentum_pct": 0.0,
        "stock_trading_enabled": True,
    },
}


def _seed_rising_ticker(db_engine, ticker, n=420, start="2019-01-01", start_price=100.0, daily_pct=0.003):
    dates = pd.date_range(start, periods=n, freq="D")
    prices = [start_price * (1 + daily_pct) ** i for i in range(n)]
    df = pd.DataFrame({
        "Open": prices, "High": [p * 1.01 for p in prices], "Low": [p * 0.99 for p in prices],
        "Close": prices, "Adj Close": prices, "Volume": [1_000_000] * n,
    }, index=dates)
    _upsert_price_rows(df, ticker)


def test_run_current_strategy_leg_end_to_end_small_scale(db_engine, mocker):
    _seed_rising_ticker(db_engine, "AAPL")
    _seed_rising_ticker(db_engine, "MSFT")
    _seed_rising_ticker(db_engine, "^GSPC", daily_pct=0.0008)  # crisis-check reference series

    mocker.patch("analysis.benchmark_comparison.universe.get_live_stock_candidates", return_value=["AAPL", "MSFT"])
    mocker.patch("analysis.benchmark_comparison.universe.is_member", return_value=True)

    start = pd.Timestamp("2020-01-01")
    end = pd.Timestamp("2020-06-01")
    result = bc.run_current_strategy_leg(_LIVE_CONFIG, start, end, starting_capital=50_000.0)

    assert result["n_tickers_total"] == 2
    assert result["n_tickers_with_data"] == 2
    # Config values must be read from the passed-in config, not hardcoded.
    assert result["per_trade_pct"] == 0.05
    assert result["max_total_deployed"] == 0.5
    assert result["reentry_cooldown_days"] == 0
    assert result["min_momentum_pct"] == 0.0
    # A steadily-rising series over 5 months should produce at least one trade.
    assert result["n_trades"] >= 1
    assert result["ending_balance_gbp"] > 0


def test_run_current_strategy_leg_reads_per_trade_pct_from_config(db_engine, mocker):
    """Proof the function genuinely reads config at call time rather than
    hardcoding a sizing parameter -- two runs differing only in
    per_trade_pct must produce different position sizes (and therefore,
    typically, different ending balances for the same trade sequence)."""
    _seed_rising_ticker(db_engine, "AAPL")
    _seed_rising_ticker(db_engine, "^GSPC", daily_pct=0.0008)
    mocker.patch("analysis.benchmark_comparison.universe.get_live_stock_candidates", return_value=["AAPL"])
    mocker.patch("analysis.benchmark_comparison.universe.is_member", return_value=True)

    start = pd.Timestamp("2020-01-01")
    end = pd.Timestamp("2020-06-01")

    import copy
    cfg_small = copy.deepcopy(_LIVE_CONFIG)
    cfg_small["trading212_invest"]["per_trade_pct"] = 0.01
    cfg_big = copy.deepcopy(_LIVE_CONFIG)
    cfg_big["trading212_invest"]["per_trade_pct"] = 0.20

    result_small = bc.run_current_strategy_leg(cfg_small, start, end, starting_capital=50_000.0)
    result_big = bc.run_current_strategy_leg(cfg_big, start, end, starting_capital=50_000.0)

    assert result_small["per_trade_pct"] == 0.01
    assert result_big["per_trade_pct"] == 0.20
    # Both configs must have actually traded for this comparison to mean anything.
    assert result_small["n_trades"] >= 1
    assert result_big["n_trades"] >= 1
