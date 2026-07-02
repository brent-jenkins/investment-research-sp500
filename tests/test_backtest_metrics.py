"""Tests for analysis/backtest_metrics.py.

Every function here is pure pandas over a trades DataFrame (as returned by
analysis.backtest.get_run_trades()) — no DB, no yfinance, no broker API —
so these tests build synthetic trades DataFrames directly.
"""

import pandas as pd
import pytest

from analysis.backtest_metrics import (
    compute_metrics,
    equity_curve,
    forex_holding_sensitivity,
    module_attribution,
    period_breakdown,
    regime_performance,
    signal_decay,
    walk_forward,
)


def _trade(**overrides) -> dict:
    base = {
        "ticker": "TEST",
        "direction": "LONG",
        "entry_date": pd.Timestamp("2023-01-01"),
        "exit_date": pd.Timestamp("2023-01-10"),
        "entry_price": 100.0,
        "exit_price": 105.0,
        "entry_signal": "STRONG_BUY",
        "exit_reason": "SIGNAL_REVERSAL",
        "pnl_pct": 0.05,
        "regime_at_entry": "TRENDING_BULL",
        "trend_at_entry": 1,
        "mean_reversion_at_entry": 0,
        "volatility_at_entry": 0,
        "volume_at_entry": 0,
        "macro_at_entry": 0,
        "holding_days": 9,
        "is_win": True,
    }
    base.update(overrides)
    return base


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------

def test_compute_metrics_empty_df_returns_zero_trades():
    """An empty trades DataFrame must report total_trades=0 without raising
    (e.g. a divide-by-zero computing win_rate on an empty set)."""
    assert compute_metrics(pd.DataFrame()) == {"total_trades": 0}


def test_compute_metrics_win_rate_and_profit_factor():
    """Win rate and profit factor must match a hand-computed value: 2 wins
    summing +0.15, 1 loss of -0.05 -> profit_factor = 0.15 / 0.05 = 3.0."""
    df = _df([
        _trade(pnl_pct=0.10, is_win=True),
        _trade(pnl_pct=0.05, is_win=True),
        _trade(pnl_pct=-0.05, is_win=False),
    ])
    m = compute_metrics(df)
    assert m["total_trades"] == 3
    assert m["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["profit_factor"] == pytest.approx(3.0, abs=1e-2)


def test_compute_metrics_profit_factor_capped_when_no_losses():
    """With zero losing trades the raw profit factor is infinite;
    compute_metrics must cap it at 99.9 rather than returning inf to the UI."""
    df = _df([_trade(pnl_pct=0.05, is_win=True), _trade(pnl_pct=0.03, is_win=True)])
    assert compute_metrics(df)["profit_factor"] == 99.9


def test_compute_metrics_max_drawdown_from_compounded_equity():
    """max_drawdown_pct must reflect the peak-to-trough decline of the
    compounded (not simply summed) equity curve: +20% then -10% takes
    equity from 1.0 -> 1.2 -> 1.08, a -10% drawdown from the 1.2 peak."""
    df = _df([
        _trade(pnl_pct=0.20, is_win=True),
        _trade(pnl_pct=-0.10, is_win=False),
    ])
    assert compute_metrics(df)["max_drawdown_pct"] == pytest.approx(-10.0, abs=0.01)


def test_compute_metrics_max_loss_streak_counts_consecutive_losses_only():
    """max_loss_streak must count the longest run of consecutive losses (3),
    not the total number of losing trades (4)."""
    df = _df([
        _trade(is_win=True),
        _trade(is_win=False),
        _trade(is_win=False),
        _trade(is_win=False),
        _trade(is_win=True),
        _trade(is_win=False),
    ])
    assert compute_metrics(df)["max_loss_streak"] == 3


# ---------------------------------------------------------------------------
# equity_curve
# ---------------------------------------------------------------------------

def test_equity_curve_empty_df_returns_empty_with_expected_columns():
    """An empty input must still return a DataFrame with the documented
    columns, so callers can safely access .columns / iterate without a
    KeyError on a completely empty result."""
    result = equity_curve(pd.DataFrame())
    assert list(result.columns) == ["date", "equity", "drawdown_pct"]
    assert result.empty


def test_equity_curve_compounds_returns_and_tracks_drawdown():
    """Equity must compound (not sum) trade returns in exit_date order —
    the order P&L actually lands in a real account — and drawdown_pct must
    be measured against the running peak. The curve is a daily series: days
    with no trade exit (including the gap before the first exit and any day
    between exits) must carry forward the previous value."""
    df = _df([
        _trade(entry_date=pd.Timestamp("2023-01-01"), exit_date=pd.Timestamp("2023-01-05"), pnl_pct=0.10),
        _trade(entry_date=pd.Timestamp("2023-01-03"), exit_date=pd.Timestamp("2023-01-10"), pnl_pct=-0.05),
    ])
    curve = equity_curve(df, start_value=1.0)

    # Daily series spanning the first entry to the last exit, no gaps.
    assert curve["date"].min() == pd.Timestamp("2023-01-01")
    assert curve["date"].max() == pd.Timestamp("2023-01-10")
    assert len(curve) == 10

    by_date = curve.set_index("date")["equity"]
    assert by_date[pd.Timestamp("2023-01-01")] == pytest.approx(1.0)              # before any exit
    assert by_date[pd.Timestamp("2023-01-04")] == pytest.approx(1.0)              # still before first exit
    assert by_date[pd.Timestamp("2023-01-05")] == pytest.approx(1.10, abs=1e-4)   # first trade realised
    assert by_date[pd.Timestamp("2023-01-07")] == pytest.approx(1.10, abs=1e-4)   # carried forward
    assert by_date[pd.Timestamp("2023-01-10")] == pytest.approx(1.10 * 0.95, abs=1e-4)  # second trade realised

    dd_by_date = curve.set_index("date")["drawdown_pct"]
    assert dd_by_date[pd.Timestamp("2023-01-10")] == pytest.approx(-5.0, abs=1e-2)


def test_equity_curve_dates_are_strictly_monotonically_increasing():
    """Regression test for the original bug: sorting trades by entry_date
    while plotting exit_date produced backwards jumps in the time axis
    whenever trades of different holding periods overlapped — a trade
    entered first but held the longest could still exit after a
    later-entered, shorter trade. The daily series must never move
    backwards or repeat a date."""
    df = _df([
        _trade(entry_date=pd.Timestamp("2023-01-01"), exit_date=pd.Timestamp("2023-03-01"), pnl_pct=0.20),
        _trade(entry_date=pd.Timestamp("2023-01-15"), exit_date=pd.Timestamp("2023-01-20"), pnl_pct=-0.02),
        _trade(entry_date=pd.Timestamp("2023-02-01"), exit_date=pd.Timestamp("2023-02-10"), pnl_pct=0.03),
    ])
    curve = equity_curve(df)

    assert curve["date"].is_monotonic_increasing
    assert curve["date"].is_unique
    diffs = curve["date"].diff().dropna()
    assert (diffs == pd.Timedelta(days=1)).all()


def test_equity_curve_same_day_exits_use_fully_compounded_value():
    """Multiple trades exiting on the same calendar day must all be
    realised into that day's equity — the day's value is the fully
    compounded result, not just the last trade processed."""
    df = _df([
        _trade(entry_date=pd.Timestamp("2023-01-01"), exit_date=pd.Timestamp("2023-01-05"), pnl_pct=0.10),
        _trade(entry_date=pd.Timestamp("2023-01-01"), exit_date=pd.Timestamp("2023-01-05"), pnl_pct=0.05),
    ])
    curve = equity_curve(df, start_value=1.0)
    by_date = curve.set_index("date")["equity"]
    assert by_date[pd.Timestamp("2023-01-05")] == pytest.approx(1.10 * 1.05, abs=1e-4)


# ---------------------------------------------------------------------------
# period_breakdown
# ---------------------------------------------------------------------------

def test_period_breakdown_filters_trades_into_the_correct_predefined_period():
    """A trade entered inside the 2022 Bear Market window must be counted
    there and NOT bleed into COVID Crash or 2023-24 Recovery; a trade outside
    every predefined period must appear in none of them."""
    df = _df([
        _trade(entry_date=pd.Timestamp("2022-06-15")),  # inside 2022 Bear Market
        _trade(entry_date=pd.Timestamp("2020-03-01")),  # inside COVID Crash
        _trade(entry_date=pd.Timestamp("2019-01-01")),  # outside every period
    ])
    result = period_breakdown(df)
    assert result["2022 Bear Market"]["total_trades"] == 1
    assert result["COVID Crash"]["total_trades"] == 1
    assert result["2023–24 Recovery"]["total_trades"] == 0


# ---------------------------------------------------------------------------
# module_attribution
# ---------------------------------------------------------------------------

def test_module_attribution_long_trade_agreement_requires_positive_score():
    """For a LONG trade, a module score of +1 counts as 'agreement'; a score
    of -1 does not, even though both trades are otherwise valid rows."""
    df = _df([
        _trade(direction="LONG", trend_at_entry=1, is_win=True),
        _trade(direction="LONG", trend_at_entry=1, is_win=True),
        _trade(direction="LONG", trend_at_entry=-1, is_win=False),
    ])
    result = module_attribution(df)
    assert result.loc["Trend", "n_trades"] == 3
    assert result.loc["Trend", "agreement_pct"] == pytest.approx(66.7, abs=0.1)
    assert result.loc["Trend", "win_rate_when_agree"] == pytest.approx(100.0, abs=0.1)


def test_module_attribution_short_trade_agreement_requires_negative_score():
    """For a SHORT trade the agreement convention flips: a score of -1 (not
    +1) counts as agreement — this is the opposite of the LONG-trade rule
    and must not be conflated with it."""
    df = _df([
        _trade(direction="SHORT", trend_at_entry=-1, is_win=True),
        _trade(direction="SHORT", trend_at_entry=1, is_win=False),
    ])
    result = module_attribution(df)
    assert result.loc["Trend", "agreement_pct"] == pytest.approx(50.0, abs=0.1)


# ---------------------------------------------------------------------------
# signal_decay
# ---------------------------------------------------------------------------

def test_signal_decay_exit_reason_percentages_sum_correctly():
    """Exit-reason percentages must reflect each reason's share of all
    trades: 2/4 STOP_LOSS = 50%, 1/4 each of the other two = 25%."""
    df = _df([
        _trade(exit_reason="STOP_LOSS", holding_days=2),
        _trade(exit_reason="STOP_LOSS", holding_days=3),
        _trade(exit_reason="SIGNAL_REVERSAL", holding_days=10),
        _trade(exit_reason="MAX_HOLDING", holding_days=30),
    ])
    result = signal_decay(df)
    assert result["pct_stop_loss"] == pytest.approx(50.0, abs=0.1)
    assert result["pct_signal_reversal"] == pytest.approx(25.0, abs=0.1)
    assert result["pct_max_holding"] == pytest.approx(25.0, abs=0.1)


def test_signal_decay_average_hold_computed_per_regime():
    """regime_holds must average holding_days within each regime_at_entry
    group independently, not across the whole DataFrame."""
    df = _df([
        _trade(regime_at_entry="TRENDING_BULL", holding_days=10),
        _trade(regime_at_entry="TRENDING_BULL", holding_days=20),
        _trade(regime_at_entry="MIXED", holding_days=5),
    ])
    result = signal_decay(df)
    assert result["regime_holds"]["TRENDING_BULL"] == pytest.approx(15.0)
    assert result["regime_holds"]["MIXED"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# walk_forward
# ---------------------------------------------------------------------------

def test_walk_forward_excludes_windows_with_fewer_than_3_trades():
    """A rolling window with only 1-2 trades is too small to be meaningful —
    walk_forward requires at least 3 trades per window to report it."""
    df = _df([
        _trade(entry_date=pd.Timestamp("2023-01-05")),
        _trade(entry_date=pd.Timestamp("2023-01-10")),
    ])
    assert walk_forward(df, window_months=6, step_months=1) == []


def test_walk_forward_includes_windows_that_meet_the_trade_minimum():
    """A window with exactly 3 qualifying trades must be included, with
    n_trades reflecting the count within that window."""
    df = _df([
        _trade(entry_date=pd.Timestamp("2023-01-05"), pnl_pct=0.05, is_win=True),
        _trade(entry_date=pd.Timestamp("2023-01-10"), pnl_pct=0.03, is_win=True),
        _trade(entry_date=pd.Timestamp("2023-01-15"), pnl_pct=-0.02, is_win=False),
    ])
    result = walk_forward(df, window_months=6, step_months=1)
    assert len(result) > 0
    assert result[0]["n_trades"] == 3


# ---------------------------------------------------------------------------
# regime_performance
# ---------------------------------------------------------------------------

def test_regime_performance_groups_metrics_by_regime_at_entry():
    """Each regime's win rate must be computed independently — TRENDING_BULL
    at 100% must not be diluted by MIXED's 0% or vice versa."""
    df = _df([
        _trade(regime_at_entry="TRENDING_BULL", pnl_pct=0.05, is_win=True),
        _trade(regime_at_entry="TRENDING_BULL", pnl_pct=0.03, is_win=True),
        _trade(regime_at_entry="MIXED", pnl_pct=-0.05, is_win=False),
    ])
    result = regime_performance(df)
    assert result.loc["TRENDING_BULL", "n_trades"] == 2
    assert result.loc["TRENDING_BULL", "win_rate_pct"] == pytest.approx(100.0)
    assert result.loc["MIXED", "win_rate_pct"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# forex_holding_sensitivity
# ---------------------------------------------------------------------------

def test_forex_holding_sensitivity_filters_to_forex_tickers_only():
    """Only tickers ending in '=X' are forex pairs — a non-forex trade must
    be excluded entirely from every threshold bucket, not just underweighted."""
    df = _df([
        _trade(ticker="GBPUSD=X", holding_days=2, pnl_pct=0.01, is_win=True),
        _trade(ticker="AAPL", holding_days=2, pnl_pct=0.01, is_win=True),
    ])
    result = forex_holding_sensitivity(df, thresholds=[5])
    assert result[0]["n_trades"] == 1


def test_forex_holding_sensitivity_empty_when_no_forex_trades_present():
    """A trades DataFrame with no '=X' tickers must return an empty list,
    not a list of zero-trade threshold rows."""
    df = _df([_trade(ticker="AAPL")])
    assert forex_holding_sensitivity(df) == []


def test_forex_holding_sensitivity_threshold_caps_which_trades_count():
    """A trade held longer than a given threshold must be excluded from
    that threshold's bucket — approximating a shorter max-holding cap —
    while still counting toward a larger threshold."""
    df = _df([
        _trade(ticker="GBPUSD=X", holding_days=2, pnl_pct=0.01, is_win=True),
        _trade(ticker="GBPUSD=X", holding_days=10, pnl_pct=0.02, is_win=True),
    ])
    result = forex_holding_sensitivity(df, thresholds=[5, 15])
    by_threshold = {r["threshold"]: r for r in result}
    assert by_threshold[5]["n_trades"] == 1
    assert by_threshold[15]["n_trades"] == 2
