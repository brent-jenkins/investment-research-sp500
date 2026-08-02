"""Tests for analysis/backtest.py -- the execution engine that reuses
V1's proven ATR stop / VaR sizing mechanics, with only the entry/exit
decision logic (momentum signal, point-in-time membership, crisis
override) being new.
"""
import pandas as pd
import pytest

from analysis import backtest as v2b
from analysis import universe as v2u

_CONFIG = {
    "signals": {"atr_period": 14},
    "backtest": {
        "starting_capital_gbp": 10000, "atr_stop_multiplier": 2.0,
    },
    "trading212_invest": {
        "var_confidence": 0.95, "var_lookback_days": 252, "max_risk_pct": 0.01,
        "min_position_gbp": 50, "max_position_gbp": 500,
    },
}


def _flat_gspc(n=500, start="2020-01-01"):
    """A calm S&P 500 series with no crisis day anywhere in range."""
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.Series([100.0 + i * 0.01 for i in range(n)], index=dates)


def _crisis_gspc(n=500, shock_idx=300, start="2020-01-01"):
    dates = pd.date_range(start, periods=n, freq="D")
    vals = [100.0 + i * 0.01 for i in range(n)]
    vals[shock_idx] = vals[shock_idx - 1] * 0.90  # -10% single day, well past the -7% trigger
    return pd.Series(vals, index=dates)


def _frame(n=500, start="2020-01-01", signal_always_on=True, atr=1.0, price_fn=None):
    dates = pd.date_range(start, periods=n, freq="D")
    if price_fn is None:
        close = pd.Series([100.0 + i * 0.05 for i in range(n)], index=dates)
    else:
        close = pd.Series([price_fn(i) for i in range(n)], index=dates)
    return pd.DataFrame({
        "close": close,
        "atr": pd.Series([atr] * n, index=dates),
        "signal": pd.Series([signal_always_on] * n, index=dates),
    })


def test_signal_off_exit_closes_the_position():
    n = 100
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    close = pd.Series([100.0 + i * 0.1 for i in range(n)], index=dates)
    sig = pd.Series([True] * 30 + [False] * (n - 30), index=dates)
    frame = pd.DataFrame({"close": close, "atr": pd.Series([1.0] * n, index=dates), "signal": sig})

    trades = v2b.simulate_trades(frame, "VOO", _CONFIG, _flat_gspc(n), is_etf=True)
    assert trades
    assert trades[0]["exit_reason"] == "SIGNAL_OFF"


def test_stop_loss_exit_fires_on_a_sharp_drop():
    n = 60
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    # Flat at 100 (stop = 100 - 2*ATR = 96), then a single sharp drop to 90
    # that stays there -- crosses the stop exactly once, no re-entry chain.
    prices = [100.0] * 10 + [90.0] * (n - 10)
    close = pd.Series(prices[:n], index=dates)
    frame = pd.DataFrame({
        "close": close, "atr": pd.Series([2.0] * n, index=dates),
        "signal": pd.Series([True] * n, index=dates),
    })
    trades = v2b.simulate_trades(frame, "VOO", _CONFIG, _flat_gspc(n), is_etf=True)
    assert trades
    assert trades[0]["exit_reason"] == "STOP_LOSS"


def test_crisis_exit_force_closes_regardless_of_signal(mocker):
    """A >=7% single-day S&P decline must force-exit an open position the
    same day, even though the ticker's own signal is still on."""
    n = 500
    frame = _frame(n=n, signal_always_on=True)
    gspc = _crisis_gspc(n=n, shock_idx=300)

    trades = v2b.simulate_trades(frame, "VOO", _CONFIG, gspc, is_etf=True)
    crisis_exits = [t for t in trades if t["exit_reason"] == "CRISIS_EXIT"]
    assert crisis_exits, "expected at least one CRISIS_EXIT trade"
    shock_date = gspc.index[300].date()
    assert crisis_exits[0]["exit_date"] == shock_date


def test_crisis_blocks_new_entries_during_cooldown():
    n = 500
    frame = _frame(n=n, signal_always_on=True)
    gspc = _crisis_gspc(n=n, shock_idx=50)

    trades = v2b.simulate_trades(frame, "VOO", _CONFIG, gspc, is_etf=True)
    # No trade should have an entry_date inside the 5-day cooldown window.
    shock_date = gspc.index[50].date()
    for t in trades:
        gap = (t["entry_date"] - shock_date).days
        assert not (0 <= gap <= 5), f"entry at {t['entry_date']} falls inside the post-crisis cooldown"


def test_index_removal_force_exits_a_stock_position(mocker):
    """A stock whose S&P 500 membership ends mid-holding must be
    force-exited that day via INDEX_REMOVAL, mirroring the AMD/Beiersdorf
    gap already verified in the research clone."""
    n = 100
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    close = pd.Series([100.0 + i * 0.1 for i in range(n)], index=dates)
    frame = pd.DataFrame({
        "close": close, "atr": pd.Series([1.0] * n, index=dates),
        "signal": pd.Series([True] * n, index=dates),
    })
    removal_date = dates[15].date()  # well inside the 30-day max-holding backstop

    def fake_is_member(ticker, check_date):
        return check_date < removal_date

    mocker.patch.object(v2u, "is_member", side_effect=fake_is_member)

    trades = v2b.simulate_trades(frame, "SOMESTOCK", _CONFIG, _flat_gspc(n), is_etf=False)
    assert trades
    assert trades[0]["exit_reason"] == "INDEX_REMOVAL"
    assert trades[0]["exit_date"] == removal_date


def test_etf_never_gated_by_membership_even_if_is_member_would_say_no(mocker):
    """is_etf=True must bypass the membership check entirely -- an ETF
    trade should never see INDEX_REMOVAL."""
    n = 60
    frame = _frame(n=n, signal_always_on=True)
    mocker.patch.object(v2u, "is_member", return_value=False)

    trades = v2b.simulate_trades(frame, "VOO", _CONFIG, _flat_gspc(n), is_etf=True)
    assert all(t["exit_reason"] != "INDEX_REMOVAL" for t in trades)


def test_max_holding_backstop_fires_when_nothing_else_does():
    n = 100
    frame = _frame(n=n, signal_always_on=True, atr=0.01)  # tiny ATR -> stop far away, won't fire
    trades = v2b.simulate_trades(frame, "VOO", _CONFIG, _flat_gspc(n), is_etf=True)
    assert trades
    assert any(t["exit_reason"] == "MAX_HOLDING" for t in trades)


def test_ex_best_trades_drops_the_single_largest_winner():
    trades = [
        {"pnl_pct": 0.01}, {"pnl_pct": 0.50}, {"pnl_pct": -0.02}, {"pnl_pct": 0.03},
    ]
    ex_best = v2b.ex_best_trades(trades)
    assert len(ex_best) == 3
    assert all(t["pnl_pct"] != 0.50 for t in ex_best)


def test_ex_best_trades_empty_input():
    assert v2b.ex_best_trades([]) == []


def test_compute_metrics_fixed_empty_trades_returns_zeros():
    m = v2b.compute_metrics_fixed([], 10000)
    assert m["total_trades"] == 0
    assert m["cagr_pct"] == 0.0


# ---------------------------------------------------------------------------
# compute_peak_relative_drawdown_pct (2026-08-02) -- the genuine,
# peak-relative counterpart to compute_metrics_fixed()'s fixed-
# starting-capital-denominator max_drawdown_pct. See CLAUDE.md's "No market
# gate at all" section for why the gap between the two can be large on a
# real equity curve, not a rounding difference.
# ---------------------------------------------------------------------------

def _dd_trade(entry, exit_, pnl_gbp):
    return {"ticker": "X", "entry_date": entry, "exit_date": exit_, "pnl_gbp": pnl_gbp}


def test_compute_peak_relative_drawdown_pct_empty_trades_returns_none():
    assert v2b.compute_peak_relative_drawdown_pct([], 10000) is None


def test_compute_peak_relative_drawdown_pct_matches_hand_computed_example():
    """£1000 start -> +£1000 (peak £2000) -> -£500 (£1500). Peak-relative:
    (1500-2000)/2000 = -25%. Hand-computed, not just asserted against the
    implementation's own output."""
    trades = [
        _dd_trade("2020-01-01", "2020-01-05", 1000.0),
        _dd_trade("2020-01-06", "2020-01-10", -500.0),
    ]
    pr_dd = v2b.compute_peak_relative_drawdown_pct(trades, 1000.0)
    assert pr_dd == pytest.approx(-25.0, abs=0.01)


def test_compute_peak_relative_drawdown_pct_less_severe_than_fixed_denominator_after_a_gain():
    """The whole point of this metric existing: after the account has
    genuinely grown, a later decline reads as much milder against its own
    peak than against the ORIGINAL starting capital -- the fixed-
    denominator convention compute_metrics_fixed() uses can substantially
    overstate real risk once genuine growth has occurred first."""
    trades = [
        {"ticker": "X", "entry_date": "2020-01-01", "exit_date": "2020-01-05",
         "pnl_gbp": 1000.0, "pnl_pct": 1.0, "is_win": True, "holding_days": 4},
        {"ticker": "X", "entry_date": "2020-01-06", "exit_date": "2020-01-10",
         "pnl_gbp": -500.0, "pnl_pct": -0.25, "is_win": False, "holding_days": 4},
    ]
    fixed_dd = v2b.compute_metrics_fixed(trades, 1000.0)["max_drawdown_pct"]
    peak_dd = v2b.compute_peak_relative_drawdown_pct(trades, 1000.0)
    assert fixed_dd == pytest.approx(-50.0, abs=0.01)
    assert abs(peak_dd) < abs(fixed_dd)


def test_compute_peak_relative_drawdown_pct_no_decline_ever_is_zero():
    trades = [_dd_trade("2020-01-01", "2020-01-05", 500.0)]
    assert v2b.compute_peak_relative_drawdown_pct(trades, 1000.0) == pytest.approx(0.0, abs=0.01)


def test_walk_forward_pf_empty_trades():
    wf = v2b.walk_forward_pf([])
    assert wf["n_total"] == 0
    assert wf["success_rate_pct"] == 0.0


def test_make_blocks_returns_requested_count_contiguous():
    blocks = v2b.make_blocks(pd.Timestamp("2007-01-01"), pd.Timestamp("2026-07-30"), n_blocks=16)
    assert len(blocks) == 16
    for i in range(len(blocks) - 1):
        assert blocks[i][1] == blocks[i + 1][0]


def test_compute_cost_sensitivity_empty_trades():
    result = v2b.compute_cost_sensitivity([], 10000, cost_bps_list=(0, 10, 25))
    assert set(result.keys()) == {0, 10, 25}
    for m in result.values():
        assert m["total_trades"] == 0


def test_compute_cost_sensitivity_higher_cost_never_improves_return():
    """A round-trip cost subtracted from every trade must never make the
    same trade set look better -- CAGR/win-rate should be monotonically
    non-increasing as cost_bps rises."""
    trades = [
        {"pnl_pct": 0.05, "pnl_gbp": 50.0, "position_gbp": 1000.0,
         "entry_date": pd.Timestamp("2020-01-01"), "exit_date": pd.Timestamp("2020-02-01"), "is_win": True},
        {"pnl_pct": -0.02, "pnl_gbp": -20.0, "position_gbp": 1000.0,
         "entry_date": pd.Timestamp("2020-02-01"), "exit_date": pd.Timestamp("2020-03-01"), "is_win": False},
        {"pnl_pct": 0.03, "pnl_gbp": 30.0, "position_gbp": 1000.0,
         "entry_date": pd.Timestamp("2020-03-01"), "exit_date": pd.Timestamp("2020-04-01"), "is_win": True},
    ]
    result = v2b.compute_cost_sensitivity(trades, 10000, cost_bps_list=(0, 10, 25))
    assert result[0]["total_pnl_gbp"] >= result[10]["total_pnl_gbp"] >= result[25]["total_pnl_gbp"]


def test_compute_cost_sensitivity_can_flip_a_marginal_win_to_a_loss():
    trades = [
        {"pnl_pct": 0.001, "pnl_gbp": 1.0, "position_gbp": 1000.0,
         "entry_date": pd.Timestamp("2020-01-01"), "exit_date": pd.Timestamp("2020-01-02"), "is_win": True},
    ]
    result = v2b.compute_cost_sensitivity(trades, 10000, cost_bps_list=(0, 25))
    assert result[0]["total_trades"] == 1
    assert result[25]["total_pnl_gbp"] < result[0]["total_pnl_gbp"]


def test_run_candidate_uses_etf_tickers_set_to_decide_is_etf(mocker):
    """A ticker in v2_universe.ETF_TICKERS must be simulated with
    is_etf=True (no membership gating) even if a stray membership check
    would otherwise say no."""
    mocker.patch.object(v2u, "is_member", return_value=False)
    n = 60
    cached = [{"ticker": "VOO", "frame": _frame(n=n, signal_always_on=True)}]
    trades = v2b.run_candidate(cached, _CONFIG, _flat_gspc(n))
    assert all(t["exit_reason"] != "INDEX_REMOVAL" for t in trades)
