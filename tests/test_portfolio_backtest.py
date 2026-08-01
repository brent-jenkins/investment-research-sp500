"""Tests for analysis/backtest.py:simulate_portfolio() -- the shared-account,
portfolio-capped simulation mode added 2026-08-01 alongside the pre-existing
run_candidate()/simulate_trades() (left completely unchanged; this is an
additional, selectable mode -- see CLAUDE.md's "Portfolio-capped backtest
engine" section for the full design writeup).

All synthetic data, no DB/network -- matches this project's self-contained
test-suite convention (see tests/test_backtest.py for the pattern this file
follows).
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
        "max_open_positions": 999999, "max_total_deployed": 999.0,
    },
}


def _frame(n=200, start="2020-01-01", signal_always_on=True, atr=0.01, price_fn=None):
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


def _flat_gspc(n=500, start="2020-01-01"):
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.Series([100.0 + i * 0.01 for i in range(n)], index=dates)


def _config_with_caps(max_open=999999, max_deployed=999.0, max_position_gbp=500, min_position_gbp=50):
    import copy
    cfg = copy.deepcopy(_CONFIG)
    cfg["trading212_invest"]["max_open_positions"] = max_open
    cfg["trading212_invest"]["max_total_deployed"] = max_deployed
    cfg["trading212_invest"]["max_position_gbp"] = max_position_gbp
    cfg["trading212_invest"]["min_position_gbp"] = min_position_gbp
    return cfg


def _reconstruct_max_concurrent(trades: list[dict]) -> int:
    """Sweep entry/exit events chronologically and return the highest
    simultaneous open-position count reached. Exits are processed before
    entries on a same-day tie -- matching simulate_portfolio()'s own Phase
    A (exits) before Phase B (entries) ordering -- so a same-day exit +
    different-ticker entry is never miscounted as briefly holding both
    concurrently."""
    events = []
    for t in trades:
        events.append((pd.Timestamp(t["entry_date"]), 1))
        events.append((pd.Timestamp(t["exit_date"]), -1))
    events.sort(key=lambda e: (e[0], e[1]))  # exits (-1) before entries (+1) on a tie
    running = 0
    peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


# ---------------------------------------------------------------------------
# 1. Throttling correctness
# ---------------------------------------------------------------------------

def test_max_open_positions_never_exceeded_and_priority_rule_respected(mocker):
    """5 tickers (3 ETFs, 2 stocks), all always-on signal from day 0,
    max_open_positions=2. Confirm: (a) never more than 2 concurrently open,
    (b) on day 0 specifically the two ETFs win over both stocks, regardless
    of the stocks appearing earlier in the input list -- the ETF-before-
    stock half of the priority rule -- and (c) among the ETFs, the one
    appearing first in the input list wins over the one appearing later."""
    mocker.patch.object(v2u, "is_member", return_value=True)

    n = 200
    # Deliberately interleaved input order: stocks placed BEFORE some ETFs
    # in the list, to prove group (ETF-vs-stock) beats raw input position.
    cached = [
        {"ticker": "STOCK_A", "frame": _frame(n=n)},   # idx 0, stock
        {"ticker": "VOO",     "frame": _frame(n=n)},   # idx 1, ETF
        {"ticker": "STOCK_B", "frame": _frame(n=n)},   # idx 2, stock
        {"ticker": "GLD",     "frame": _frame(n=n)},   # idx 3, ETF
        {"ticker": "SLV",     "frame": _frame(n=n)},   # idx 4, ETF
    ]
    cfg = _config_with_caps(max_open=2, max_deployed=999.0)
    trades = v2b.simulate_portfolio(cached, cfg, _flat_gspc(n))

    assert trades, "expected at least some trades"
    assert _reconstruct_max_concurrent(trades) <= 2

    day0 = pd.Timestamp("2020-01-01").date()
    day0_entrants = {t["ticker"] for t in trades if t["entry_date"] == day0}
    # ETFs (in input order VOO, then GLD -- SLV is idx4, third-priority ETF)
    # must win the first two slots over either stock, and specifically VOO
    # (idx 1) must win over GLD (idx 3) since it appears first among ETFs.
    assert day0_entrants == {"VOO", "GLD"}, f"expected VOO+GLD to win day 0, got {day0_entrants}"


def test_priority_rule_etf_group_beats_stock_group_even_when_stock_is_listed_first(mocker):
    """Narrower, more direct version of the rule above: a single ETF and a
    single stock, both signal-on from day 0, cap=1 -- the ETF must win even
    though the stock is FIRST in the input list."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 100
    cached = [
        {"ticker": "STOCK_ONLY", "frame": _frame(n=n)},  # idx 0, stock -- listed first
        {"ticker": "VOO",        "frame": _frame(n=n)},  # idx 1, ETF -- listed second
    ]
    cfg = _config_with_caps(max_open=1, max_deployed=999.0)
    trades = v2b.simulate_portfolio(cached, cfg, _flat_gspc(n))

    day0 = pd.Timestamp("2020-01-01").date()
    day0_entrants = [t["ticker"] for t in trades if t["entry_date"] == day0]
    assert day0_entrants == ["VOO"], f"expected only VOO to enter on day 0, got {day0_entrants}"

    # While VOO holds its position, STOCK_ONLY (the only other flat
    # candidate every day) must never be admitted -- max_open_positions=1
    # is fully occupied.
    voo_first_exit = next(t for t in trades if t["ticker"] == "VOO")["exit_date"]
    assert not any(
        t["ticker"] == "STOCK_ONLY" and t["entry_date"] < voo_first_exit for t in trades
    )
    # A ticker can never exit and re-enter on the SAME day it exits (see
    # simulate_portfolio()'s docstring point 5) -- so on VOO's own exit day,
    # VOO itself is not entry-eligible (it wasn't flat before that day
    # started); STOCK_ONLY -- which has been waiting the whole time and
    # WAS flat before that day started -- is the only day-of candidate and
    # wins the freed slot by default.
    stock_only_first_entry = next(
        (t["entry_date"] for t in trades if t["ticker"] == "STOCK_ONLY"), None
    )
    assert stock_only_first_entry == voo_first_exit, (
        f"expected STOCK_ONLY to win the slot on VOO's own exit day {voo_first_exit} "
        f"(VOO itself is same-day-re-entry-ineligible), got entry {stock_only_first_entry}"
    )


# ---------------------------------------------------------------------------
# 2. Sanity check -- huge caps must exactly reproduce run_candidate()
# ---------------------------------------------------------------------------

def _sortable(trades: list[dict]) -> list[tuple]:
    keys = ["ticker", "entry_date", "exit_date", "entry_price", "exit_price",
            "exit_reason", "pnl_pct", "position_gbp", "holding_days", "is_win"]
    return sorted(
        (tuple(round(t[k], 6) if isinstance(t[k], float) else t[k] for k in keys) for t in trades),
        key=lambda row: (row[0], str(row[1])),
    )


def test_huge_caps_reproduce_run_candidate_exactly(mocker):
    """With max_open_positions/max_total_deployed set arbitrarily high (never
    binding), simulate_portfolio() must produce the IDENTICAL trade list --
    same tickers, entry/exit dates, prices, exit reasons, position sizes,
    and pnl_pct -- as the existing, unmodified run_candidate(). This is the
    critical regression guard: portfolio mode must not silently change
    behaviour when its caps don't bind."""
    mocker.patch.object(v2u, "is_member", return_value=True)

    n = 300
    gspc = _flat_gspc(n)

    # Ticker 1: SIGNAL_OFF exit pattern.
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    sig = pd.Series([True] * 40 + [False] * (n - 40), index=dates)
    f1 = pd.DataFrame({
        "close": pd.Series([100.0 + i * 0.1 for i in range(n)], index=dates),
        "atr": pd.Series([1.0] * n, index=dates),
        "signal": sig,
    })

    # Ticker 2: STOP_LOSS exit pattern.
    prices2 = [100.0] * 10 + [90.0] * (n - 10)
    f2 = pd.DataFrame({
        "close": pd.Series(prices2, index=dates),
        "atr": pd.Series([2.0] * n, index=dates),
        "signal": pd.Series([True] * n, index=dates),
    })

    # Ticker 3: MAX_HOLDING backstop (tiny ATR, always-on signal, rising price).
    f3 = _frame(n=n, signal_always_on=True, atr=0.01)

    cached = [
        {"ticker": "VOO", "frame": f1},
        {"ticker": "STOCK1", "frame": f2},
        {"ticker": "GLD", "frame": f3},
    ]

    cfg_uncapped = _config_with_caps(max_open=999999, max_deployed=999.0)
    portfolio_trades = v2b.simulate_portfolio(cached, cfg_uncapped, gspc)
    candidate_trades = v2b.run_candidate(cached, cfg_uncapped, gspc)

    assert portfolio_trades, "expected trades from simulate_portfolio()"
    assert candidate_trades, "expected trades from run_candidate()"
    assert _sortable(portfolio_trades) == _sortable(candidate_trades)


def test_huge_caps_reproduce_run_candidate_with_crisis_override(mocker):
    """Same equivalence check, this time including a crisis-override event
    (a >=7% single-day S&P decline) to confirm CRISIS_EXIT / entry-blocking
    behaviour is unaffected by the portfolio-mode refactor."""
    mocker.patch.object(v2u, "is_member", return_value=True)

    n = 400
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    gspc_vals = [100.0 + i * 0.01 for i in range(n)]
    gspc_vals[200] = gspc_vals[199] * 0.90  # -10% single day
    gspc = pd.Series(gspc_vals, index=dates)

    cached = [
        {"ticker": "VOO", "frame": _frame(n=n, signal_always_on=True, atr=0.01, start="2020-01-01")},
        {"ticker": "SLV", "frame": _frame(n=n, signal_always_on=True, atr=0.01, start="2020-01-01")},
    ]
    cfg_uncapped = _config_with_caps(max_open=999999, max_deployed=999.0)

    portfolio_trades = v2b.simulate_portfolio(cached, cfg_uncapped, gspc)
    candidate_trades = v2b.run_candidate(cached, cfg_uncapped, gspc)

    assert any(t["exit_reason"] == "CRISIS_EXIT" for t in candidate_trades), "fixture sanity check"
    assert _sortable(portfolio_trades) == _sortable(candidate_trades)


# ---------------------------------------------------------------------------
# 3. max_total_deployed enforcement, independent of max_open_positions
# ---------------------------------------------------------------------------

def test_max_total_deployed_blocks_entry_even_with_open_positions_capacity_free(mocker):
    """3 tickers, all signal-on from day 0, position size sized by the
    <60-day-history fallback (round(max_position_gbp * 0.5, 2)) --
    deterministic and independent of any real VaR calculation. With
    max_position_gbp=400 -> position_gbp=200 per trade, starting_capital
    =10000, max_total_deployed=0.035 (=GBP350): candidate 1 admits at
    deployed=0 (0% < 3.5%) -> deployed=200; candidate 2 admits at
    deployed=200 (2% < 3.5%) -> deployed=400; candidate 3 is checked at
    deployed=400 (4% >= 3.5%) -> BLOCKED. max_open_positions is set to 999
    (nowhere near binding) so this isolates the deployment cap specifically."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 150
    cached = [
        {"ticker": "VOO", "frame": _frame(n=n)},
        {"ticker": "GLD", "frame": _frame(n=n)},
        {"ticker": "SLV", "frame": _frame(n=n)},
    ]
    cfg = _config_with_caps(max_open=999, max_deployed=0.035, max_position_gbp=400, min_position_gbp=50)
    trades = v2b.simulate_portfolio(cached, cfg, _flat_gspc(n))

    day0 = pd.Timestamp("2020-01-01").date()
    day0_entrants = {t["ticker"] for t in trades if t["entry_date"] == day0}
    assert day0_entrants == {"VOO", "GLD"}, (
        f"expected exactly VOO+GLD (first two by priority) to be admitted on day 0 "
        f"before the deployment cap blocks the third, got {day0_entrants}"
    )
    assert "SLV" not in {t["ticker"] for t in trades if t["entry_date"] == day0}

    for t in trades:
        if t["entry_date"] == day0:
            assert t["position_gbp"] == pytest.approx(200.0)

    # Confirm max_open_positions=999 genuinely never bound in this run --
    # never more than 2 concurrently open, well under 999, proving it was
    # the deployment cap alone that stopped the third entry.
    assert _reconstruct_max_concurrent(trades) <= 2


def test_max_total_deployed_never_binds_when_generous(mocker):
    """Control case: same setup but with a generous deployment cap -- all
    three candidates should be admitted on day 0, confirming the block
    above is genuinely caused by the cap and not some other gating."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 150
    cached = [
        {"ticker": "VOO", "frame": _frame(n=n)},
        {"ticker": "GLD", "frame": _frame(n=n)},
        {"ticker": "SLV", "frame": _frame(n=n)},
    ]
    cfg = _config_with_caps(max_open=999, max_deployed=999.0, max_position_gbp=400, min_position_gbp=50)
    trades = v2b.simulate_portfolio(cached, cfg, _flat_gspc(n))

    day0 = pd.Timestamp("2020-01-01").date()
    day0_entrants = {t["ticker"] for t in trades if t["entry_date"] == day0}
    assert day0_entrants == {"VOO", "GLD", "SLV"}
