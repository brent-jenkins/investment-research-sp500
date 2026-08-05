"""Builds simulate_portfolio_compounding_mtm_reset_at_boundary() from the
REAL, CURRENT analysis.backtest.simulate_portfolio_compounding_mtm() via
source introspection (inspect.getsource) -- never retyped, eliminating
transcription risk -- with one additive injection at the top of the
day-by-day event loop: the first event date on or after RESET_BOUNDARY,
`cash` is force-set to RESET_CAPITAL and every ticker's open_trade is
discarded (not recorded as a trade), so current_balance (= cash + mtm
value of open positions) reads exactly RESET_CAPITAL that day, before that
day's entries are evaluated. Training-period signal history and membership
eligibility are unaffected -- only the tracked account state resets.

This exactly follows the technique CLAUDE_HISTORY.md documents repeatedly
(see "No market-cap gate at all" / "position sizing" / "re-entry cooldown
sweep" sections) for building a reset-at-boundary variant of a production
engine without ever hand-retyping its logic. Faithfulness MUST be verified
by every caller before trusting this for anything -- see
verify_faithful() below -- by pushing RESET_BOUNDARY past the end of all
data (reset code never fires) and confirming the wrapped function
reproduces the real, unmodified simulate_portfolio_compounding_mtm()
byte-for-byte.
"""
import inspect
import re

from analysis import backtest as bt


def build_reset_wrapper():
    """Returns a NEW function object,
    simulate_portfolio_compounding_mtm_reset_at_boundary(tickers_cache,
    config, gspc_close, per_trade_pct, max_deployed_pct,
    reset_boundary_ts, reset_capital=50000.0, collect_rejections=False),
    compiled from the real function's own current source.
    """
    src = inspect.getsource(bt.simulate_portfolio_compounding_mtm)

    # Rename the function itself.
    src = src.replace(
        "def simulate_portfolio_compounding_mtm(",
        "def simulate_portfolio_compounding_mtm_reset_at_boundary(",
        1,
    )
    # Add the two new parameters to the signature.
    src = src.replace(
        "    collect_rejections: bool = False,\n):",
        "    collect_rejections: bool = False,\n"
        "    reset_boundary_ts=None,\n"
        "    reset_capital: float = 50000.0,\n):",
        1,
    )

    # Injection point: top of the day-by-day while loop, right after
    # `dt = events[i][0]` is assigned -- before flat_before_today is
    # computed, before Phase A exits, before Phase B entries. Exactly one
    # occurrence expected.
    anchor = "        dt = events[i][0]\n"
    assert src.count(anchor) == 1, f"expected exactly 1 anchor match, found {src.count(anchor)}"
    injection = (
        anchor
        + "        nonlocal_reset_fired = _reset_state.get('fired', False)\n"
        + "        if (not nonlocal_reset_fired) and reset_boundary_ts is not None and dt >= reset_boundary_ts:\n"
        + "            for _rst in per_ticker.values():\n"
        + "                _rst['open_trade'] = None\n"
        + "            cash = reset_capital\n"
        + "            _reset_state['fired'] = True\n"
    )
    src = src.replace(anchor, injection, 1)

    # `cash` is a local variable of the outer function, reassigned inside
    # the while loop body directly (the while loop is NOT a nested closure
    # relative to `cash`'s own scope -- both live in the same function
    # body), so a plain reassignment is legal there with no `nonlocal`
    # needed. `_reset_state` is a small mutable dict declared once before
    # the loop, used only so the "have we already fired" flag survives
    # across loop iterations without needing its own nonlocal/global
    # ceremony inside nested closures (_admit_group, _open_positions_mtm_value
    # are separate nested functions and never need to see this flag).
    src = src.replace(
        "    cash = starting_capital\n",
        "    cash = starting_capital\n    _reset_state = {'fired': False}\n",
        1,
    )

    # Compile in a namespace that has every name the original function
    # body references at module scope (pd, np, universe, crisis,
    # evaluate_exit_reason, passes_entry_gate, passes_momentum_floor,
    # is_reentry_blocked, resolve_reentry_cooldown_days,
    # resolve_min_momentum_floor, resolve_dynamic_max_position_gbp,
    # resolve_cash_constrained_position_gbp, is_position_size_viable,
    # _var_position_size_core, _resolve_entry_stop, _MIN_VAR_RETURNS,
    # _would_deploy_pct, get_max_holding_days) -- reuse analysis.backtest's
    # own module namespace directly so every one of these resolves exactly
    # as it does in the real module, with zero risk of a stale/duplicated
    # copy drifting from the real implementations.
    namespace = dict(vars(bt))
    exec(compile(src, "<reset_wrapper>", "exec"), namespace)
    return namespace["simulate_portfolio_compounding_mtm_reset_at_boundary"]


def verify_faithful(wrapped_fn, tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct):
    """Reset boundary pushed far past any real data -- the injected reset
    code must never fire, and the wrapped function's trades must exactly
    match the real, unmodified simulate_portfolio_compounding_mtm()'s
    trades, field-for-field, trade-for-trade. Raises AssertionError with a
    diagnostic message on any mismatch; returns silently (True) on success.
    """
    import pandas as pd

    real_trades = bt.simulate_portfolio_compounding_mtm(
        tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct,
    )
    wrapped_trades = wrapped_fn(
        tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct,
        reset_boundary_ts=pd.Timestamp("2099-01-01"),
    )
    if len(real_trades) != len(wrapped_trades):
        raise AssertionError(
            f"trade count mismatch: real={len(real_trades)} wrapped={len(wrapped_trades)}"
        )
    for i, (r, w) in enumerate(zip(real_trades, wrapped_trades)):
        if r != w:
            raise AssertionError(f"trade {i} mismatch:\nreal={r}\nwrapped={w}")
    return True
