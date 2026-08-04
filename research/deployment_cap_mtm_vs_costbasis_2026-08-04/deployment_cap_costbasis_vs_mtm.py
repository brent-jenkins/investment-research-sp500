"""
Research script: mark-to-market (current live/production) vs cost-basis
deployment-cap accounting, isolated to the ONE variable in question.

Context: max_total_deployed is confirmed (by direct code read) to be
enforced against T212's `investments.currentValue` live (genuine
mark-to-market -- see brokers/trading212.py:_submit_pending_orders() and
_capacity_fit_reason()), matched by analysis/backtest.py:
simulate_portfolio_compounding_mtm(), which analysis/validation.py:
run_full_validation() calls when mark_to_market=True (the flag main.py's
Backtest page and the DD report always pass).

The naive alternative -- comparing mark_to_market=True vs
mark_to_market=False in run_full_validation() -- does NOT isolate the cap
accounting convention alone: simulate_portfolio_compounding() (the
cost-basis engine mark_to_market=False dispatches to) is a DELIBERATELY
FROZEN, pre-2026-08-03 engine that is also missing the minimum-momentum
floor (passes_momentum_floor()), exempt from the re-entry cooldown
(is_reentry_blocked()), and has no cash-availability clamp
(resolve_cash_constrained_position_gbp()) -- confirmed directly by
reading both engines' source and their own docstrings, which say so
explicitly. Using it would conflate the deployment-cap question with
three unrelated, already-settled differences.

This script instead builds `simulate_portfolio_compounding_mtm_costbasis_cap()`
-- copied verbatim from simulate_portfolio_compounding_mtm() with EXACTLY
ONE change: the capacity check's own "already deployed" figure is tracked
as a running cost-basis total (+= position_gbp on entry, -= position_gbp
on exit) instead of being recomputed mark-to-market each day. Position
SIZING (current_balance, what per_trade_pct is measured against) is left
untouched -- still cash + mark-to-market open-position value, exactly as
production -- since that convention is already settled elsewhere in this
project and is not what this investigation is about.

Self-check built in: with max_deployed_pct set absurdly high (10.0, i.e.
the cap can never bind), the two engines must produce byte-identical
trade sequences -- proving the only behavioural difference is what
happens when the cap actually binds, i.e. that this is a genuinely
isolated copy, not an accidental additional divergence.

Uses run_full_validation() itself for the real comparison (monkeypatching
analysis.validation.simulate_portfolio_compounding_mtm to point at the
cost-basis-cap variant for the second run) -- reuses 100% of the real
data loading, live universe, block-splitting (make_blocks/_block_passes),
S&P benchmark, and metrics computation. Zero custom reimplementation of
anything except the one accounting line under test.

Two comparisons, each explicitly labelled per this project's own "state
the backtest convention" standing requirement:
  1. Continuous walk, full 2007-present, out-of-sample test window
     (2020-01-01+) headline -- what a real account running each
     convention since inception would show.
  2. 16-block reset-at-boundary robustness check (each block's own
     metrics computed fresh from starting_capital, the project's
     existing make_blocks()/_block_passes() machinery) -- comparing
     the two conventions' pass rate without one variant's train-period
     compounding luck contaminating the comparison.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
import json
import copy
import datetime as dt

import numpy as np
import pandas as pd
import yaml

import analysis.validation as validation_mod
from analysis.backtest import (
    _MIN_VAR_RETURNS,
    _resolve_entry_stop,
    _var_position_size_core,
    _would_deploy_pct,
)
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    is_reentry_blocked,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_reentry_cooldown_days,
)
import analysis.crisis as crisis
import analysis.universe as universe
from analysis.universe import get_max_holding_days

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)


def simulate_portfolio_compounding_mtm_costbasis_cap(
    tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct, collect_rejections=False,
):
    """Verbatim copy of analysis.backtest.simulate_portfolio_compounding_mtm()
    (read directly from the real source at the time this script was
    written) with exactly one change, marked inline with `# ISOLATED
    CHANGE`: the deployment-cap check uses a running COST-BASIS total
    (deployed_cost_basis) instead of the mark-to-market open-position
    value. current_balance (position sizing) is untouched -- still
    mark-to-market, matching production.
    """
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))
    reentry_cooldown_days = resolve_reentry_cooldown_days(config)
    min_momentum_floor = resolve_min_momentum_floor(config)

    per_ticker = {}
    events = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        is_etf = ticker in universe.ETF_TICKERS
        close_series = frame["close"].astype(float)

        raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

        event_rank = (0 if is_etf else 1, idx)
        per_ticker[ticker] = {
            "frame": frame, "rows": frame.to_dict("index"), "is_etf": is_etf, "idx": idx,
            "close_series": close_series, "raw_crisis": raw_crisis, "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for d in frame.index:
            events.append((d, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades = []
    rejections = []
    cash = starting_capital
    deployed_cost_basis = 0.0  # ISOLATED CHANGE: running cost-basis deployed total

    def _open_positions_mtm_value():
        total = 0.0
        for st in per_ticker.values():
            ot = st["open_trade"]
            if ot is None:
                continue
            last_price = st["last_price"]
            if last_price is None or ot["entry_price"] <= 0:
                total += ot["position_gbp"]
            else:
                total += ot["position_gbp"] * (last_price / ot["entry_price"])
        return total

    def _admit_group(group_tickers, d, current_balance, current_deployed):
        nonlocal cash, deployed_cost_basis
        candidates = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][d]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(ticker, d.date() if hasattr(d, "date") else d)
            blocked_now = bool(st["entry_blocked"].loc[d])
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            momentum_val = row.get("momentum")
            momentum_val = float(momentum_val) if pd.notna(momentum_val) else None
            if not passes_momentum_floor(momentum_val, min_momentum_floor):
                continue
            last_exit = st.get("last_exit_date")
            days_since_exit = (d - last_exit).days if last_exit is not None else None
            if is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days):
                continue
            candidates.append((ticker, momentum_val if momentum_val is not None else float("-inf")))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        for ticker, momentum_val in candidates:
            if current_balance <= 0:
                break

            st = per_ticker[ticker]
            row = st["rows"][d]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)

            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                current_balance, per_trade_pct, absolute_max_position_gbp,
            )
            close_series = st["close_series"]
            prior_closes = close_series.loc[close_series.index < d].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

            position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)

            if not is_position_size_viable(position_gbp):
                continue

            # ISOLATED CHANGE: capacity check against deployed_cost_basis,
            # not the mark-to-market current_deployed the MTM engine uses.
            would_deploy_pct = _would_deploy_pct(deployed_cost_basis, position_gbp, current_balance)
            if would_deploy_pct > max_deployed_pct:
                if collect_rejections:
                    rejections.append({
                        "ticker": ticker, "date": d.date() if hasattr(d, "date") else d,
                        "is_etf": st["is_etf"], "momentum": momentum_val,
                        "position_gbp": position_gbp, "would_deploy_pct": round(would_deploy_pct, 4),
                        "max_deployed_pct": max_deployed_pct,
                        "current_deployed_gbp": round(deployed_cost_basis, 2),
                        "current_balance_gbp": round(current_balance, 2),
                        "reason": "REJECTED_CAPACITY",
                    })
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": d, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            st["last_price"] = price
            deployed_cost_basis += position_gbp  # ISOLATED CHANGE
            cash -= position_gbp

        return current_deployed

    i = 0
    n_events = len(events)
    while i < n_events:
        d = events[i][0]
        j = i
        day_tickers = []
        while j < n_events and events[j][0] == d:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        for ticker in day_tickers:
            st = per_ticker[ticker]
            row = st["rows"][d]
            price = float(row["close"])
            st["last_price"] = price

            ot = st["open_trade"]
            if ot is None:
                continue
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[d])

            entry_price = ot["entry_price"]
            stop_price = ot["stop_price"]
            holding_days = (d - ot["entry_date"]).days

            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=price, stop_price=stop_price, sig_on=sig_on,
                holding_days=holding_days, max_holding_days=st["max_hold"],
            )
            if exit_reason is None and d == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"
            exit_price = stop_price if exit_reason == "STOP_LOSS" else price

            if exit_reason:
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_gbp = ot["position_gbp"] * float(pnl_pct)
                trades.append({
                    "ticker": ticker, "direction": "LONG",
                    "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                    "exit_date": d.date() if hasattr(d, "date") else d,
                    "entry_price": entry_price, "exit_price": float(exit_price),
                    "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                    "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
                    "stop_distance_pct": ot["stop_distance_pct"],
                    "holding_days": holding_days, "is_win": pnl_pct > 0,
                })
                cash += ot["position_gbp"] + pnl_gbp
                deployed_cost_basis -= ot["position_gbp"]  # ISOLATED CHANGE
                st["open_trade"] = None
                st["last_exit_date"] = d

        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            _admit_group(etf_group, d, current_balance, deployed_cost_basis)
            _admit_group(stock_group, d, current_balance, deployed_cost_basis)

        i = j

    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        last = frame.iloc[-1]
        exit_p = float(last["close"])
        pnl_pct = (exit_p - ot["entry_price"]) / ot["entry_price"]
        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
        hd = (frame.index[-1] - ot["entry_date"]).days
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": frame.index[-1].date(),
            "entry_price": ot["entry_price"], "exit_price": exit_p,
            "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
            "stop_distance_pct": ot["stop_distance_pct"],
            "holding_days": hd, "is_win": pnl_pct > 0,
        })
        cash += ot["position_gbp"] + pnl_gbp

    if collect_rejections:
        return trades, rejections
    return trades


def _trade_signature(trades):
    """A compact, order-independent-safe signature of a trade list for the
    self-check -- ticker/entry_date/exit_date/exit_reason/position_gbp
    rounded, sorted deterministically."""
    sig = sorted(
        (t["ticker"], str(t["entry_date"]), str(t["exit_date"]), t["exit_reason"], round(t["position_gbp"], 2))
        for t in trades
    )
    return sig


def run_self_check():
    """With the cap effectively disabled (max_deployed_pct huge), the two
    engines must produce byte-identical trades -- proving the cost-basis
    variant is a genuinely isolated copy of the MTM engine, not an
    accidental additional divergence."""
    print("Self-check: cap disabled (max_deployed_pct=10.0) -- trade sequences must match exactly.", flush=True)
    from analysis.backtest import (
        get_price_df, precompute_ticker, simulate_portfolio_compounding_mtm, slice_ticker,
    )

    start = pd.Timestamp("2018-01-01")
    end = pd.Timestamp("2019-06-30")
    candidate_tickers = universe.get_live_stock_candidates(as_of=end.date())[:60]

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= end)]

    caches = []
    for ticker in candidate_tickers:
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, start, end)
        caches.append(c)

    trades_mtm = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, 0.05, 10.0)
    trades_cb = simulate_portfolio_compounding_mtm_costbasis_cap(caches, CONFIG, gspc_close, 0.05, 10.0)

    sig_mtm = _trade_signature(trades_mtm)
    sig_cb = _trade_signature(trades_cb)
    match = sig_mtm == sig_cb
    print(f"  MTM trades: {len(trades_mtm)}, cost-basis-cap trades: {len(trades_cb)}, identical: {match}", flush=True)
    if not match:
        print("  MISMATCH -- first few differing entries:", flush=True)
        only_mtm = [s for s in sig_mtm if s not in sig_cb][:5]
        only_cb = [s for s in sig_cb if s not in sig_mtm][:5]
        print("  only in MTM:", only_mtm, flush=True)
        print("  only in cost-basis:", only_cb, flush=True)
    return match


def run_comparison():
    config = copy.deepcopy(CONFIG)

    real_mtm_func = validation_mod.simulate_portfolio_compounding_mtm

    print("Running MARK-TO-MARKET (current production) convention...", flush=True)
    result_mtm = validation_mod.run_full_validation(config, live_universe=True, mark_to_market=True)

    print("Running COST-BASIS-CAP (isolated variant) convention...", flush=True)
    validation_mod.simulate_portfolio_compounding_mtm = simulate_portfolio_compounding_mtm_costbasis_cap
    try:
        result_cb = validation_mod.run_full_validation(config, live_universe=True, mark_to_market=True)
    finally:
        validation_mod.simulate_portfolio_compounding_mtm = real_mtm_func

    return result_mtm, result_cb


def summarize(label, result):
    print(f"\n=== {label} ===")
    print(f"backtest_convention: {result['backtest_convention']} (continuous walk; blocks below are reset-at-boundary)")
    print(f"total_trades: {result['total_trades']}")
    for period in ("train", "test"):
        m = result[period]
        print(
            f"  {period}: trades={m['total_trades']} win_rate={m['win_rate']:.4f} "
            f"pf={m.get('profit_factor')} cagr={m['cagr_pct']} dd={m['max_drawdown_pct']} "
            f"pnl_gbp={m.get('total_pnl_gbp')}"
        )
    print(f"  n_blocks_pass: {result['n_blocks_pass']} / {result['n_blocks_total']}")
    for b in result["blocks"]:
        print(f"    block {b['block']:2d} [{b['start']}..{b['end']}] pf={b['strat_pf']} dd={b['strat_dd']} cagr={b['strat_cagr']} pass={b['passed']}")


if __name__ == "__main__":
    ok = run_self_check()
    if not ok:
        print("SELF-CHECK FAILED -- the cost-basis variant is NOT an isolated copy. Aborting real comparison.", flush=True)
        sys.exit(1)

    result_mtm, result_cb = run_comparison()

    summarize("MARK-TO-MARKET (current production, mark_to_market=True)", result_mtm)
    summarize("COST-BASIS-CAP (isolated deployment-cap variant)", result_cb)

    out = {"self_check_passed": ok, "mark_to_market": result_mtm, "cost_basis_cap": result_cb}
    out_path = r'C:\Users\user\AppData\Local\Temp\claude\c--GitHub-investment-research-sp500\5c25ce7d-a81a-44c0-b492-d23b052752f9\scratchpad\deployment_cap_costbasis_vs_mtm_result.json'
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nDONE. Full result written to {out_path}", flush=True)
