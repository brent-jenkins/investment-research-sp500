"""Tests for analysis/backtest.py:simulate_portfolio() -- the shared-account,
portfolio-capped simulation mode added 2026-08-01 alongside the pre-existing
run_candidate()/simulate_trades() (left completely unchanged; this is an
additional, selectable mode -- see CLAUDE.md's "Portfolio-capped backtest
engine" section for the full design writeup).

Also covers analysis/backtest.py:simulate_portfolio_compounding() -- the
genuinely compounding two-parameter mode added the same day (see this
file's "Compounding mode" section below and CLAUDE.md's "Compounding
portfolio backtest" section for the full design writeup). Both engines
share the same day-by-day walk structure; simulate_portfolio() stays fixed-
notional throughout (sizes/caps against backtest.starting_capital_gbp,
never against a moving balance) while simulate_portfolio_compounding()
tracks and sizes against a genuinely moving current_balance.

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


# ---------------------------------------------------------------------------
# 4. Compounding mode (simulate_portfolio_compounding) -- added 2026-08-01
# ---------------------------------------------------------------------------

def _fallback_position(balance: float, per_trade_pct: float) -> float:
    """Mirrors simulate_portfolio_compounding()'s own <60-day-history
    fallback formula exactly (round(dynamic_max_position_gbp * 0.5, 2),
    where dynamic_max_position_gbp = current_balance * per_trade_pct) --
    used by tests below to compute an *expected* position size from first
    principles rather than hardcoding a number that would silently rot if
    the formula's rounding behaviour ever changed."""
    return round(balance * per_trade_pct * 0.5, 2)


def _compounding_config(max_open_positions=999999):
    import copy
    cfg = copy.deepcopy(_CONFIG)
    cfg["trading212_invest"]["max_open_positions"] = max_open_positions
    cfg["trading212_invest"]["max_total_deployed"] = 999.0  # unused by compounding mode
    return cfg


def test_compounding_winning_first_trade_grows_a_later_entrys_position_size(mocker):
    """Ticker A wins big and closes before ticker B ever becomes a
    candidate. B's own entry has < 60 days of its own price history (it
    just sat flat/off since day 0), so its position size is computed via
    the same <60-day fallback for both the compounding and (hypothetical)
    fixed-notional case -- isolating the ONLY variable that can explain a
    difference: which balance the fallback's own dynamic_max_position_gbp
    was computed against. Confirms it is the GROWN balance (starting
    capital + A's realised profit), not the starting notional."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="D")

    starting_capital = 10000.0
    per_trade_pct = 0.05

    # A: signal on days 0-9, off from day 10 -- exits via SIGNAL_OFF on day
    # 10 at close=200 (entry close=100) -- a clean +100% winner.
    sig_a = pd.Series([True] * 10 + [False] * (n - 10), index=dates)
    close_a = pd.Series([100.0 + i * 10.0 for i in range(n)], index=dates)
    frame_a = pd.DataFrame({"close": close_a, "atr": pd.Series([1.0] * n, index=dates), "signal": sig_a})

    # B: flat/signal-off through day 11, signal on from day 12 onward --
    # becomes a candidate only after A has already fully closed (day 10).
    sig_b = pd.Series([False] * 12 + [True] * (n - 12), index=dates)
    close_b = pd.Series([50.0] * n, index=dates)  # flat -- no stop-loss risk
    frame_b = pd.DataFrame({"close": close_b, "atr": pd.Series([0.01] * n, index=dates), "signal": sig_b})

    cached = [{"ticker": "A_WINNER", "frame": frame_a}, {"ticker": "B_LATER", "frame": frame_b}]
    cfg = _compounding_config()
    trades = v2b.simulate_portfolio_compounding(cached, cfg, _flat_gspc(n), per_trade_pct, 0.90)

    trade_a = next(t for t in trades if t["ticker"] == "A_WINNER")
    trade_b = next(t for t in trades if t["ticker"] == "B_LATER")

    expected_pos_a = _fallback_position(starting_capital, per_trade_pct)
    assert trade_a["position_gbp"] == pytest.approx(expected_pos_a)
    assert trade_a["pnl_pct"] == pytest.approx(1.0)  # (200-100)/100
    expected_pnl_gbp_a = expected_pos_a * 1.0
    assert trade_a["pnl_gbp"] == pytest.approx(expected_pnl_gbp_a)

    balance_after_a = starting_capital + expected_pnl_gbp_a
    expected_pos_b = _fallback_position(balance_after_a, per_trade_pct)

    # The genuinely compounding expectation must differ from what a
    # fixed-notional engine would have produced -- otherwise this test
    # wouldn't actually distinguish the two behaviours.
    assert expected_pos_b != expected_pos_a
    assert trade_b["position_gbp"] == pytest.approx(expected_pos_b)


def test_compounding_losing_first_trade_shrinks_a_later_entrys_position_size(mocker):
    """Mirror image of the winning case above -- ticker A loses (via a
    SIGNAL_OFF exit at a lower close, not a stop-loss, so the loss isn't
    capped at the stop distance), and ticker B's later position size must
    reflect the SHRUNK balance."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="D")

    starting_capital = 10000.0
    per_trade_pct = 0.05

    # A: signal on days 0-9, off from day 10. ATR large enough that the
    # stop sits far below the exit price, so this exits via SIGNAL_OFF
    # (not STOP_LOSS) at close=60 -- a -40% loss.
    sig_a = pd.Series([True] * 10 + [False] * (n - 10), index=dates)
    close_a_vals = [100.0 - i * 4.0 for i in range(10)] + [60.0] * (n - 10)
    close_a = pd.Series(close_a_vals, index=dates)
    frame_a = pd.DataFrame({"close": close_a, "atr": pd.Series([50.0] * n, index=dates), "signal": sig_a})

    sig_b = pd.Series([False] * 12 + [True] * (n - 12), index=dates)
    close_b = pd.Series([50.0] * n, index=dates)
    frame_b = pd.DataFrame({"close": close_b, "atr": pd.Series([0.01] * n, index=dates), "signal": sig_b})

    cached = [{"ticker": "A_LOSER", "frame": frame_a}, {"ticker": "B_LATER", "frame": frame_b}]
    cfg = _compounding_config()
    trades = v2b.simulate_portfolio_compounding(cached, cfg, _flat_gspc(n), per_trade_pct, 0.90)

    trade_a = next(t for t in trades if t["ticker"] == "A_LOSER")
    trade_b = next(t for t in trades if t["ticker"] == "B_LATER")

    assert trade_a["exit_reason"] == "SIGNAL_OFF"
    expected_pos_a = _fallback_position(starting_capital, per_trade_pct)
    assert trade_a["position_gbp"] == pytest.approx(expected_pos_a)
    assert trade_a["pnl_pct"] == pytest.approx(-0.4)  # (60-100)/100

    expected_pnl_gbp_a = expected_pos_a * -0.4
    balance_after_a = starting_capital + expected_pnl_gbp_a
    assert balance_after_a < starting_capital

    expected_pos_b = _fallback_position(balance_after_a, per_trade_pct)
    assert expected_pos_b != expected_pos_a
    assert trade_b["position_gbp"] == pytest.approx(expected_pos_b)


def test_compounding_deployment_cap_denominator_uses_grown_balance_not_starting_capital(mocker):
    """After A's big win grows the balance, a THIRD candidate (C) is
    admitted under max_deployed_pct only because the cap's denominator is
    the grown balance -- with a max_deployed_pct set precisely so that a
    (buggy) fixed-starting-capital denominator would have blocked C, while
    the correct grown-balance denominator admits it. This is a genuine
    regression guard: reverting the denominator to starting_capital would
    flip this test from pass to fail."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="D")

    starting_capital = 10000.0
    per_trade_pct = 0.05

    sig_a = pd.Series([True] * 10 + [False] * (n - 10), index=dates)
    close_a = pd.Series([100.0 + i * 10.0 for i in range(n)], index=dates)
    frame_a = pd.DataFrame({"close": close_a, "atr": pd.Series([1.0] * n, index=dates), "signal": sig_a})

    sig_bc = pd.Series([False] * 12 + [True] * (n - 12), index=dates)
    frame_b = pd.DataFrame({
        "close": pd.Series([50.0] * n, index=dates),
        "atr": pd.Series([0.01] * n, index=dates), "signal": sig_bc,
    })
    frame_c = pd.DataFrame({
        "close": pd.Series([80.0] * n, index=dates),
        "atr": pd.Series([0.01] * n, index=dates), "signal": sig_bc,
    })

    cached = [
        {"ticker": "A_WINNER", "frame": frame_a},
        {"ticker": "B_CAND", "frame": frame_b},
        {"ticker": "C_CAND", "frame": frame_c},
    ]

    expected_pos_a = _fallback_position(starting_capital, per_trade_pct)  # 250.0
    balance_after_a = starting_capital + expected_pos_a * 1.0             # 10250.0
    expected_pos_b = _fallback_position(balance_after_a, per_trade_pct)   # 256.25

    ratio_if_fixed_denominator = expected_pos_b / starting_capital        # 0.025625
    ratio_if_compounding_denominator = expected_pos_b / balance_after_a   # 0.025 exactly
    assert ratio_if_compounding_denominator < ratio_if_fixed_denominator, "fixture sanity check"

    max_deployed_pct = (ratio_if_fixed_denominator + ratio_if_compounding_denominator) / 2

    cfg = _compounding_config()
    trades = v2b.simulate_portfolio_compounding(cached, cfg, _flat_gspc(n), per_trade_pct, max_deployed_pct)

    day12 = pd.Timestamp("2020-01-13").date()
    day12_entrants = {t["ticker"] for t in trades if t["entry_date"] == day12}
    assert "B_CAND" in day12_entrants
    assert "C_CAND" in day12_entrants, (
        "C should be admitted because the deployment cap's denominator is the "
        "GROWN balance (10250), not the fixed starting capital (10000) -- "
        f"max_deployed_pct={max_deployed_pct:.6f} sits strictly between the two "
        "possible ratios by construction"
    )


# ---------------------------------------------------------------------------
# 5. Emergent position count -- no explicit count cap in compounding mode
# ---------------------------------------------------------------------------

def test_compounding_mode_has_no_explicit_position_count_cap(mocker):
    """10 tickers, all signal-on from day 0, tiny per_trade_pct (so each
    position is small relative to the account) and a generous
    max_deployed_pct -- all 10 should be admitted concurrently. Crucially,
    config sets trading212_invest.max_open_positions=2 (deliberately low)
    to prove compounding mode genuinely never reads/enforces that key at
    all -- position count is purely an emergent consequence of
    per_trade_pct and max_deployed_pct together."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 100
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    cached = [
        {
            "ticker": f"STOCK_{i}",
            "frame": pd.DataFrame({
                "close": pd.Series([100.0] * n, index=dates),
                "atr": pd.Series([0.01] * n, index=dates),
                "signal": pd.Series([True] * n, index=dates),
            }),
        }
        for i in range(10)
    ]

    cfg = _compounding_config(max_open_positions=2)  # deliberately low -- must be ignored
    trades = v2b.simulate_portfolio_compounding(cached, cfg, _flat_gspc(n), per_trade_pct=0.01, max_deployed_pct=0.5)

    assert _reconstruct_max_concurrent(trades) >= 10, (
        "expected all 10 small positions to be admitted concurrently -- "
        "max_open_positions=2 in config must NOT be enforced by compounding mode"
    )


# ---------------------------------------------------------------------------
# 6. Graceful handling of a non-positive balance
# ---------------------------------------------------------------------------

def test_compounding_handles_balance_reaching_negative_without_crashing_or_guessing(mocker):
    """A deliberately pathological single trade (an extreme per_trade_pct
    combined with a crisis-forced exit at a synthetic, deeply negative
    close price -- not a realistic market scenario, constructed purely to
    exercise the current_balance <= 0 defensive path) drives the account
    balance negative. Confirms: (a) no exception is raised, (b) a second
    ticker whose signal turns on well after the crisis cooldown has
    cleared is NEVER entered, because Phase B is skipped entirely once the
    balance is non-positive, and (c) the pathological ticker itself never
    re-enters either."""
    mocker.patch.object(v2u, "is_member", return_value=True)
    n = 60
    dates = pd.date_range("2020-01-01", periods=n, freq="D")

    # Z: entry day 0. per_trade_pct=2.0 (deliberately > 1.0) makes the
    # <60-day-history fallback size the ENTIRE current balance into this
    # one position (dynamic_max_position_gbp = balance * 2.0; fallback =
    # dynamic_max * 0.5 = balance * 1.0).
    sig_z = pd.Series([True] * n, index=dates)
    close_z_vals = [100.0] + [-50.0] * (n - 1)  # day 1 close crashes to a synthetic negative
    close_z = pd.Series(close_z_vals, index=dates)
    frame_z = pd.DataFrame({"close": close_z, "atr": pd.Series([1.0] * n, index=dates), "signal": sig_z})

    # Y: flat/off until well after the crisis cooldown (trigger day + 5
    # trading days) clears, so if entries were still gated only by the
    # crisis block (not the balance check), Y would normally enter here.
    sig_y = pd.Series([False] * 20 + [True] * (n - 20), index=dates)
    frame_y = pd.DataFrame({
        "close": pd.Series([50.0] * n, index=dates),
        "atr": pd.Series([0.01] * n, index=dates), "signal": sig_y,
    })

    cached = [{"ticker": "Z_PATHOLOGICAL", "frame": frame_z}, {"ticker": "Y_LATER", "frame": frame_y}]

    # gspc: a single >=7% decline on day 1 forces Z's crisis exit that day.
    gspc_vals = [100.0 + i * 0.01 for i in range(n)]
    gspc_vals[1] = gspc_vals[0] * 0.90  # -10% single day
    gspc = pd.Series(gspc_vals, index=dates)

    cfg = _compounding_config()
    trades = v2b.simulate_portfolio_compounding(cached, cfg, gspc, per_trade_pct=2.0, max_deployed_pct=0.9)

    z_trades = [t for t in trades if t["ticker"] == "Z_PATHOLOGICAL"]
    assert len(z_trades) == 1
    assert z_trades[0]["exit_reason"] == "CRISIS_EXIT"
    assert z_trades[0]["pnl_gbp"] < -10000.0, "fixture sanity check -- loss must exceed starting capital"

    y_trades = [t for t in trades if t["ticker"] == "Y_LATER"]
    assert y_trades == [], (
        "Y must never enter -- once the balance goes non-positive, Phase B is "
        "skipped entirely for every subsequent day, regardless of the crisis "
        "cooldown having long since cleared"
    )
    # No further Z re-entry either.
    assert len(z_trades) == 1
