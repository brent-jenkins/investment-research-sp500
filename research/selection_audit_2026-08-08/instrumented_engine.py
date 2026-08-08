"""Instrumented copy of analysis.backtest.simulate_portfolio_compounding_mtm
for the descriptive selection audit (2026-08-08).

WHY A COPY, NOT A MONKEYPATCH: the questions this audit needs to answer
(momentum rank among that day's gate-passing set; whether an admission only
happened because TODAY's own exits freed capacity; a full gate-fail log with
reason) all need visibility into intermediate state inside the _admit_group
closure that the real function does not expose on its return value, and that
cannot be intercepted by wrapping a shared analysis.decision function (the
enriched-export precedent's technique) because the thing being logged here
IS the _admit_group loop itself, not a call to a shared pure function.

PARITY: every actual decision -- passes_entry_gate(), passes_momentum_floor(),
is_reentry_blocked(), evaluate_exit_reason(), resolve_dynamic_max_position_gbp(),
resolve_cash_constrained_position_gbp(), is_position_size_viable(),
_resolve_entry_stop(), _var_position_size_core() -- is still made by calling
the real, unmodified functions from analysis.decision / analysis.volatility,
imported directly, never re-derived. The ONLY intentional deviations from the
real simulate_portfolio_compounding_mtm are:
  (a) additive logging (candidate_log / rejections) that changes no control
      flow, and
  (b) admission_mode="random": replaces the momentum-descending sort key with
      a seeded random shuffle of the SAME already-gate-passed candidate list,
      for Part 7's ranking-vs-random test. momentum_mode (default) reproduces
      the original sort exactly.
Verified byte-identical to the real engine on momentum_mode: see
verify_parity.py in this directory.
"""
import random

import numpy as np
import pandas as pd

from analysis import universe
from analysis.decision import (
    evaluate_exit_reason, passes_entry_gate, passes_momentum_floor,
    is_reentry_blocked, resolve_reentry_cooldown_days, resolve_min_momentum_floor,
    resolve_dynamic_max_position_gbp, resolve_cash_constrained_position_gbp,
    is_position_size_viable,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core
from analysis.backtest import get_max_holding_days, _MIN_VAR_RETURNS


def simulate_instrumented(
    tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct,
    disable_stop_loss=False, admission_mode="momentum", rng_seed=None,
    log_candidates=False,
):
    """admission_mode: 'momentum' (real production ranking) or 'random'
    (Part 7's Arm R -- random admission among the SAME gate-passed set)."""
    from analysis import crisis  # local import, matches analysis/backtest.py's own module-level usage

    assert admission_mode in ("momentum", "random")
    rng = random.Random(rng_seed) if admission_mode == "random" else None

    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
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
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list = []
    rejections: list = []
    candidate_log: list = []
    cash = starting_capital

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

    def _admit_group(group_tickers, dt, current_balance, current_deployed, deployed_before_exits):
        nonlocal cash
        candidates = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
            blocked_now = bool(st["entry_blocked"].loc[dt])
            gate_ok = passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now)

            momentum_val = row.get("momentum")
            momentum_val = float(momentum_val) if pd.notna(momentum_val) else None
            floor_ok = passes_momentum_floor(momentum_val, min_momentum_floor) if gate_ok else None

            last_exit = st.get("last_exit_date")
            days_since_exit = (dt - last_exit).days if last_exit is not None else None
            reentry_ok = (not is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days)) if (gate_ok and floor_ok) else None

            final_pass = bool(gate_ok and floor_ok and reentry_ok)

            if log_candidates:
                reason = None
                if not gate_ok:
                    if not sig_on:
                        reason = "GATE_FAIL_SIGNAL_OFF"
                    elif not is_member:
                        reason = "GATE_FAIL_NOT_MEMBER"
                    elif blocked_now:
                        reason = "GATE_FAIL_CRISIS_BLOCKED"
                    else:
                        reason = "GATE_FAIL_OTHER"
                elif not floor_ok:
                    reason = "GATE_FAIL_MOMENTUM_FLOOR"
                elif not reentry_ok:
                    reason = "GATE_FAIL_REENTRY_COOLDOWN"
                candidate_log.append({
                    "date": dt.date() if hasattr(dt, "date") else dt, "ticker": ticker,
                    "momentum": momentum_val, "gate_pass": final_pass, "fail_reason": reason,
                })

            if not final_pass:
                continue
            candidates.append((ticker, momentum_val if momentum_val is not None else float("-inf")))

        if admission_mode == "momentum":
            candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))
        else:
            candidates = list(candidates)
            rng.shuffle(candidates)

        n_eligible_today = len(candidates)
        for rank0, (ticker, momentum_val) in enumerate(candidates):
            rank = rank0 + 1  # 1 = best-ranked (momentum mode); arbitrary order (random mode)
            if current_balance <= 0:
                break

            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)
            if disable_stop_loss:
                stop = None

            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                current_balance, per_trade_pct, absolute_max_position_gbp,
            )
            close_series = st["close_series"]
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
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
                if log_candidates:
                    candidate_log.append({
                        "date": dt.date() if hasattr(dt, "date") else dt, "ticker": ticker,
                        "momentum": momentum_val, "gate_pass": True, "fail_reason": "NOT_VIABLE_SIZE",
                        "rank_in_gate_passing_set": rank, "n_eligible_today": n_eligible_today,
                    })
                continue

            would_deploy_pct = (current_deployed + position_gbp) / current_balance if current_balance > 0 else float("inf")
            would_deploy_before_exits_pct = (
                (deployed_before_exits + position_gbp) / current_balance if current_balance > 0 else float("inf")
            )
            admitted_via_freed_capacity = (
                would_deploy_pct <= max_deployed_pct and would_deploy_before_exits_pct > max_deployed_pct
            )

            if would_deploy_pct > max_deployed_pct:
                rejections.append({
                    "ticker": ticker, "date": dt.date() if hasattr(dt, "date") else dt,
                    "is_etf": st["is_etf"], "momentum": momentum_val, "position_gbp": position_gbp,
                    "would_deploy_pct": round(would_deploy_pct, 4), "max_deployed_pct": max_deployed_pct,
                    "current_deployed_gbp": round(current_deployed, 2), "current_balance_gbp": round(current_balance, 2),
                    "reason": "REJECTED_CAPACITY",
                    "rank_in_gate_passing_set": rank, "n_eligible_today": n_eligible_today,
                })
                if log_candidates:
                    candidate_log.append({
                        "date": dt.date() if hasattr(dt, "date") else dt, "ticker": ticker,
                        "momentum": momentum_val, "gate_pass": True, "fail_reason": "REJECTED_CAPACITY",
                        "rank_in_gate_passing_set": rank, "n_eligible_today": n_eligible_today,
                        "admitted": False,
                    })
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
                "rank_at_admission": rank, "n_eligible_today": n_eligible_today,
                "admitted_via_freed_capacity": admitted_via_freed_capacity,
            }
            st["last_price"] = price
            current_deployed += position_gbp
            cash -= position_gbp
            if log_candidates:
                candidate_log.append({
                    "date": dt.date() if hasattr(dt, "date") else dt, "ticker": ticker,
                    "momentum": momentum_val, "gate_pass": True, "fail_reason": None,
                    "rank_in_gate_passing_set": rank, "n_eligible_today": n_eligible_today,
                    "admitted": True, "admitted_via_freed_capacity": admitted_via_freed_capacity,
                })

        return current_deployed

    i = 0
    n_events = len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        exits_freed_mtm_value_today = 0.0

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

            stop_check_price = price
            low_val = row.get("low")
            if low_val is not None and pd.notna(low_val):
                stop_check_price = float(low_val)

            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=stop_check_price, stop_price=stop_price,
                sig_on=sig_on, holding_days=holding_days, max_holding_days=st["max_hold"],
            )
            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"

            if exit_reason == "STOP_LOSS":
                open_val = row.get("open")
                open_price = float(open_val) if open_val is not None and pd.notna(open_val) else price
                exit_price = min(stop_price, open_price)
            else:
                exit_price = price

            if exit_reason:
                # mtm value this position had immediately before it exited --
                # what gets "freed" from current_deployed by this exit, used
                # to compute the freed-capacity counterfactual in Phase B.
                pre_exit_mtm = ot["position_gbp"] * (price / entry_price) if entry_price > 0 else ot["position_gbp"]
                exits_freed_mtm_value_today += pre_exit_mtm

                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_gbp = ot["position_gbp"] * float(pnl_pct)
                trades.append({
                    "ticker": ticker, "direction": "LONG",
                    "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                    "exit_date": dt.date() if hasattr(dt, "date") else dt,
                    "entry_price": entry_price, "exit_price": float(exit_price),
                    "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
                    "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
                    "stop_distance_pct": ot["stop_distance_pct"], "holding_days": holding_days,
                    "is_win": pnl_pct > 0,
                    "rank_at_admission": ot.get("rank_at_admission"),
                    "n_eligible_at_admission": ot.get("n_eligible_today"),
                    "admitted_via_freed_capacity": ot.get("admitted_via_freed_capacity"),
                })
                cash += ot["position_gbp"] + pnl_gbp
                st["open_trade"] = None
                st["last_exit_date"] = dt

        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value
        deployed_before_exits = mtm_value + exits_freed_mtm_value_today
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_balance, current_deployed, deployed_before_exits)
            current_deployed = _admit_group(stock_group, dt, current_balance, current_deployed, deployed_before_exits)

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
            "stop_distance_pct": ot["stop_distance_pct"], "holding_days": hd, "is_win": pnl_pct > 0,
            "rank_at_admission": ot.get("rank_at_admission"),
            "n_eligible_at_admission": ot.get("n_eligible_today"),
            "admitted_via_freed_capacity": ot.get("admitted_via_freed_capacity"),
        })
        cash += ot["position_gbp"] + pnl_gbp

    return {"trades": trades, "rejections": rejections, "candidate_log": candidate_log}
