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
        "starting_capital_gbp": 10000, "atr_stop_multiplier": 2.0, "max_holding_days": 30,
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


def test_walk_forward_pf_empty_trades():
    wf = v2b.walk_forward_pf([])
    assert wf["n_total"] == 0
    assert wf["success_rate_pct"] == 0.0


def test_make_blocks_returns_requested_count_contiguous():
    blocks = v2b.make_blocks(pd.Timestamp("2007-01-01"), pd.Timestamp("2026-07-30"), n_blocks=16)
    assert len(blocks) == 16
    for i in range(len(blocks) - 1):
        assert blocks[i][1] == blocks[i + 1][0]


def test_run_candidate_uses_etf_tickers_set_to_decide_is_etf(mocker):
    """A ticker in v2_universe.ETF_TICKERS must be simulated with
    is_etf=True (no membership gating) even if a stray membership check
    would otherwise say no."""
    mocker.patch.object(v2u, "is_member", return_value=False)
    n = 60
    cached = [{"ticker": "VOO", "frame": _frame(n=n, signal_always_on=True)}]
    trades = v2b.run_candidate(cached, _CONFIG, _flat_gspc(n))
    assert all(t["exit_reason"] != "INDEX_REMOVAL" for t in trades)
