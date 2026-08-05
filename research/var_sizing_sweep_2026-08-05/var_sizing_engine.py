"""Research engine: parametrized var_confidence + time-scaling exponent for
analysis/volatility.py:_var_position_size_core(), built the identical way
every prior sizing/gating research script in this project has been built --
a verbatim copy of the real, current analysis.backtest.simulate_portfolio_
compounding_mtm() (read directly from analysis/backtest.py lines 1132-1535
on 2026-08-05) with a small number of ISOLATED CHANGES, each marked inline,
plus the same block-truncation technique already validated and used
repeatedly in this project (see CLAUDE_HISTORY.md's "Is the 5-day re-entry
cooldown result real, or a lucky historical sequence?" and the price-sanity
sweep's simulate_with_price_sanity() in
research/price_sanity_sweep_2026-08-03/ -- this file follows that same
block_start/block_end truncation pattern verbatim, including truncating
`frame` itself before close_series/log_returns are derived from it, so a
block's own VaR lookback never reaches back past that block's own start --
consistent with how every prior block-validation study in this project
computed its own blocks, not a new convention invented for this task).

ISOLATED CHANGES from the real simulate_portfolio_compounding_mtm(), each
marked inline with `# ISOLATED CHANGE`:

1. `var_confidence_override` -- if given, used INSTEAD of the config-resolved
   trading212_invest.var_confidence for every call to the position-sizing
   core. The real, unmodified `_var_position_size_core()` (imported directly
   from analysis.volatility, never copied) is still what actually runs --
   only which var_confidence value gets passed to it changes.

2. `scaling_exponent` -- if given (default 0.5, exactly reproducing the real
   function's hardcoded `math.sqrt(holding_days)` since x**0.5 == sqrt(x)),
   position sizing calls a new, separately-defined pure function
   `_var_position_size_core_scaled()` (below) instead of the real
   `_var_position_size_core()`. `_var_position_size_core_scaled()` is itself
   a verbatim copy of `_var_position_size_core()` (analysis/volatility.py
   lines 47-127) with exactly one line changed: `holding_var = daily_var *
   math.sqrt(max(holding_days, 1))` becomes `holding_var = daily_var *
   (max(holding_days, 1) ** scaling_exponent)`. Every other line -- the
   percentile formula, the zero-holding_var short-circuit, the ceiling-last
   clamp ordering -- is unchanged, character for character.

3. `block_start`/`block_end` -- when given, `frame` itself is truncated to
   `[block_start, block_end]` before `close_series`/`raw_crisis`/
   `entry_blocked` are derived from it (matching
   research/price_sanity_sweep_2026-08-03/simulate_with_price_sanity.py's
   own block-truncation technique exactly), and every position still open
   at the end of the truncated event walk is force-closed via a corrected
   `END_OF_DATA` keyed to the truncated window's own last visited date, not
   each ticker's absolute final row. `block_start is None` (the default)
   means "no truncation at all" -- the full ticker history is walked
   exactly as the real function would.

FIDELITY REQUIREMENT (checked by run_self_check() in sweep_driver.py before
any real comparison is trusted): with var_confidence_override=0.95 (the
current config value), scaling_exponent=0.5, and block_start=block_end=None,
this function must reproduce analysis.backtest.simulate_portfolio_
compounding_mtm()'s real trade sequence byte-for-byte on identical inputs.
"""
import math

import numpy as np
import pandas as pd

import analysis.universe as universe
import analysis.crisis as crisis
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _resolve_entry_stop, _var_position_size_core
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
from analysis.backtest import _would_deploy_pct


def _var_position_size_core_scaled(
    log_returns,
    account_value_gbp: float,
    max_position_gbp: float,
    holding_days: int,
    var_confidence: float,
    max_risk_pct: float,
    min_position_gbp: float,
    scaling_exponent: float,
) -> tuple:
    """Verbatim copy of analysis.volatility._var_position_size_core()
    (lines 47-127, read directly at the time this script was written) with
    EXACTLY ONE line changed (marked below): the square-root-of-time
    holding_var scaling is generalized to holding_days**scaling_exponent.
    scaling_exponent=0.5 makes this byte-identical to the real function
    (x**0.5 == math.sqrt(x) for x > 0 -- confirmed, not merely assumed, by
    the fidelity self-check in sweep_driver.py). Every other line --
    percentile formula, zero-holding_var short-circuit, ceiling-applied-last
    clamp ordering -- is unchanged.
    """
    daily_var = float(np.percentile(log_returns, (1 - var_confidence) * 100))
    # ISOLATED CHANGE: was `daily_var * math.sqrt(max(holding_days, 1))`
    holding_var = daily_var * (max(holding_days, 1) ** scaling_exponent)

    if holding_var == 0:
        return max_position_gbp, daily_var, holding_var

    position_gbp = (account_value_gbp * max_risk_pct) / abs(holding_var)
    position_gbp = min(max(position_gbp, min_position_gbp), max_position_gbp)
    return round(position_gbp, 2), daily_var, holding_var


def simulate_var_sizing_variant(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    var_confidence_override: float = None,
    scaling_exponent: float = 0.5,
    block_start=None,
    block_end=None,
    collect_rejections: bool = False,
):
    """See module docstring for the three ISOLATED CHANGES from the real
    simulate_portfolio_compounding_mtm(). Everything else below is that
    function's own body, unchanged."""
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    # ISOLATED CHANGE: override, if given, instead of the config-resolved value
    var_confidence = (
        float(var_confidence_override) if var_confidence_override is not None
        else float(var_cfg.get("var_confidence", 0.95))
    )
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))
    reentry_cooldown_days = resolve_reentry_cooldown_days(config)
    min_momentum_floor = resolve_min_momentum_floor(config)

    per_ticker: dict = {}
    events: list = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        # ISOLATED CHANGE: block truncation (see module docstring) --
        # matches research/price_sanity_sweep_2026-08-03's own technique.
        if block_start is not None:
            frame = frame[(frame.index >= block_start) & (frame.index <= block_end)]
            if frame.empty:
                continue
        is_etf = ticker in universe.ETF_TICKERS
        close_series = frame["close"].astype(float)

        raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

        event_rank = (0 if is_etf else 1, idx)
        per_ticker[ticker] = {
            "frame": frame,
            "rows": frame.to_dict("index"),
            "is_etf": is_etf,
            "idx": idx,
            "close_series": close_series,
            "raw_crisis": raw_crisis,
            "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None,
            "last_price": None,
            "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list = []
    rejections: list = []
    cash = starting_capital

    def _open_positions_mtm_value() -> float:
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

    def _admit_group(group_tickers: list, dt, current_balance: float, current_deployed: float) -> float:
        nonlocal cash
        candidates: list = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt
            )
            blocked_now = bool(st["entry_blocked"].loc[dt])
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            momentum_val = row.get("momentum")
            momentum_val = float(momentum_val) if pd.notna(momentum_val) else None
            if not passes_momentum_floor(momentum_val, min_momentum_floor):
                continue
            last_exit = st.get("last_exit_date")
            days_since_exit = (dt - last_exit).days if last_exit is not None else None
            if is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days):
                continue
            candidates.append((ticker, momentum_val if momentum_val is not None else float("-inf")))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        for ticker, momentum_val in candidates:
            if current_balance <= 0:
                break

            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)

            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                current_balance, per_trade_pct, absolute_max_position_gbp,
            )
            close_series = st["close_series"]
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                if scaling_exponent == 0.5:
                    # Uses the REAL, unmodified shared function whenever the
                    # exponent is exactly the production value -- this is
                    # what the var_confidence-only sweep runs through, so
                    # that sweep calls the exact same code live/backtest
                    # both call, with only var_confidence itself varied.
                    position_gbp, _dv, _hv = _var_position_size_core(
                        log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                        var_confidence, max_risk_pct, min_position_gbp,
                    )
                else:
                    position_gbp, _dv, _hv = _var_position_size_core_scaled(
                        log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                        var_confidence, max_risk_pct, min_position_gbp, scaling_exponent,
                    )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

            position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)

            if not is_position_size_viable(position_gbp):
                continue

            would_deploy_pct = _would_deploy_pct(current_deployed, position_gbp, current_balance)
            if would_deploy_pct > max_deployed_pct:
                if collect_rejections:
                    rejections.append({
                        "ticker": ticker, "date": dt.date() if hasattr(dt, "date") else dt,
                        "is_etf": st["is_etf"], "momentum": momentum_val,
                        "position_gbp": position_gbp,
                        "would_deploy_pct": round(would_deploy_pct, 4),
                        "max_deployed_pct": max_deployed_pct,
                        "current_deployed_gbp": round(current_deployed, 2),
                        "current_balance_gbp": round(current_balance, 2),
                        "reason": "REJECTED_CAPACITY",
                    })
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            st["last_price"] = price
            current_deployed += position_gbp
            cash -= position_gbp

        return current_deployed

    i = 0
    n_events = len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers: list = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        for ticker in day_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            st["last_price"] = price

            ot = st["open_trade"]
            if ot is None:
                continue
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[dt])

            entry_price = ot["entry_price"]
            stop_price = ot["stop_price"]
            holding_days = (dt - ot["entry_date"]).days

            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=price,
                stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                max_holding_days=st["max_hold"],
            )
            if exit_reason is None and dt == st["frame"].index[-1]:
                # ISOLATED CHANGE (block truncation only): st["frame"] is
                # already truncated to [block_start, block_end] above, so
                # this correctly force-closes at the BLOCK's own last
                # visited date when block_start/block_end are set, not just
                # each ticker's absolute final row -- matching the
                # established "corrected END_OF_DATA" technique.
                exit_reason = "END_OF_DATA"
            exit_price = stop_price if exit_reason == "STOP_LOSS" else price

            if exit_reason:
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_gbp = ot["position_gbp"] * float(pnl_pct)
                trades.append({
                    "ticker": ticker, "direction": "LONG",
                    "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                    "exit_date": dt.date() if hasattr(dt, "date") else dt,
                    "entry_price": entry_price, "exit_price": float(exit_price),
                    "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                    "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
                    "stop_distance_pct": ot["stop_distance_pct"],
                    "holding_days": holding_days, "is_win": pnl_pct > 0,
                })
                cash += ot["position_gbp"] + pnl_gbp
                st["open_trade"] = None
                st["last_exit_date"] = dt

        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_balance, current_deployed)
            current_deployed = _admit_group(stock_group, dt, current_balance, current_deployed)

        i = j

    # Defensive backstop -- catches a position entered on a ticker's own
    # literal final (block-truncated) row, same as the real function.
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
