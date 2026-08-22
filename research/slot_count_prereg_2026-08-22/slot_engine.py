"""slot_engine.py -- research-only ADDITIVE copy of
analysis/backtest.py:simulate_portfolio_compounding_mtm(), per
PREREGISTRATION.md Sec 6.

No shared module is modified. Four additions, every one parameterised so the
live behaviour is recoverable exactly:

  1. `slot_cap: int | None` -- portfolio-wide concurrent-position limit,
     checked inside _admit_group() BEFORE sizing is computed. A count limit
     cannot be relieved by a smaller position, so once the count is full the
     ranked loop BREAKS rather than continues. Shared counter across both the
     ETF and stock group passes. None = uncapped = production behaviour.
  2. `cash_fraction` -- passed explicitly to the EXISTING
     analysis/decision.py:resolve_cash_constrained_position_gbp() parameter.
     That parameter already exists; decision.py is NOT modified.
  3. `max_risk_pct` override -- NOT a parameter here. Supplied by the caller
     via a COPIED config dict (config["trading212_invest"]["max_risk_pct"]),
     following research/measurement_toolkit_2026-08-08's uncapped-arm
     precedent verbatim. The engine reads it from config exactly as
     production does.
  4. `instrument` -- additive logging only. Never consulted by any decision.

Everything else -- exit priority, the entry gate, the 2% momentum floor, the
re-entry cooldown, relative-rank admission ordering, the realistic
intraday-touch STOP_LOSS fill, END_OF_DATA force-close, mark-to-market
capacity accounting, cash bookkeeping -- is a verbatim copy of production and
routes through the same shared analysis/decision.py functions.

DIAGNOSTIC-ONLY arithmetic is marked `# DIAG`. The one such expression
(`var_raw`) is derived from _var_position_size_core()'s OWN returned
holding_var, never by recomputing the return percentile, and is used for no
decision.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analysis import crisis  # noqa: F401  (kept for parity of import surface)
from analysis import universe
from analysis.backtest import _would_deploy_pct
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    passes_entry_gate,
    passes_momentum_floor,
    resolve_admission_rank_value,
    resolve_admission_ranking_mode,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_min_momentum_floor,
    resolve_reentry_cooldown_days,
    resolve_var_horizon_days,
)
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _resolve_entry_stop, _var_position_size_core

# Disclosed cap on per-date turned-away logging (see PREREGISTRATION.md Sec 6
# and CLAUDE.md's "no silent caps" rule): the FULL count is always recorded;
# only the ticker list is truncated to this many by rank.
SLOT_FULL_TICKER_LOG_CAP = 50


def simulate_slot_capped(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    slot_cap: int | None = None,
    cash_fraction: float = 0.25,
    instrument: bool = False,
):
    """Returns `trades` when instrument=False (production-shaped output), or
    `(trades, panel, admissions, slot_full, rejections)` when instrument=True.

    With slot_cap=None, cash_fraction=0.25 and an unmodified config, the
    `trades` output must be byte-identical to
    analysis/backtest.py:simulate_portfolio_compounding_mtm() -- Sec 7 check 1.
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
    admission_ranking_mode = resolve_admission_ranking_mode(config)

    per_ticker: dict[str, dict] = {}
    events: list[tuple] = []

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

    trades: list[dict] = []
    rejections: list[dict] = []
    panel: list[dict] = []
    admissions: list[dict] = []
    slot_full: list[dict] = []
    cash = starting_capital
    n_open = 0  # ADDITION: concurrent-position counter for the slot cap.

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

    def _admit_group(group_tickers: list[str], dt, current_balance: float, current_deployed: float) -> float:
        nonlocal cash, n_open
        candidates: list[tuple[str, float]] = []
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
            if is_reentry_blocked_local(days_since_exit, reentry_cooldown_days):
                continue
            relative_rank_val = row.get("relative_rank")
            relative_rank_val = float(relative_rank_val) if pd.notna(relative_rank_val) else None
            rank_val = resolve_admission_rank_value(
                momentum=momentum_val, relative_rank=relative_rank_val, mode=admission_ranking_mode,
            )
            candidates.append((ticker, rank_val if rank_val is not None else float("-inf")))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        n_eligible = len(candidates)
        for rank_idx, (ticker, momentum_val) in enumerate(candidates):
            if current_balance <= 0:
                break

            # ---- ADDITION: hard slot cap. Checked BEFORE sizing; a count
            # limit cannot be relieved by a smaller position, so BREAK.
            if slot_cap is not None and n_open >= slot_cap:
                if instrument:
                    remaining = candidates[rank_idx:]
                    slot_full.append({
                        "date": dt.date() if hasattr(dt, "date") else dt,
                        "n_turned_away": len(remaining),
                        "n_eligible": n_eligible,
                        "top_ranked_turned_away": remaining[0][0],
                        "tickers_logged": min(len(remaining), SLOT_FULL_TICKER_LOG_CAP),
                        "tickers": ";".join(t for t, _ in remaining[:SLOT_FULL_TICKER_LOG_CAP]),
                    })
                break
            # ---- end addition

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
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp,
                    resolve_var_horizon_days(st["max_hold"], config),
                    var_confidence, max_risk_pct, min_position_gbp,
                )
                # DIAG (never used for any decision): reconstruct the
                # unclamped VaR-implied size from the function's OWN returned
                # holding_var, so the binding clamp can be labelled.
                if _hv == 0:
                    var_raw = float("inf")
                    clamp = "NO_VAR"
                else:
                    var_raw = (current_balance * max_risk_pct) / abs(_hv)
                    if var_raw > dynamic_max_position_gbp:
                        clamp = "CEILING"
                    elif var_raw < min_position_gbp:
                        clamp = "VAR_FLOOR"
                    else:
                        clamp = "VAR"
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)
                var_raw = float("nan")
                clamp = "INSUFFICIENT_HISTORY"

            pre_cash_clamp = position_gbp
            position_gbp = resolve_cash_constrained_position_gbp(
                position_gbp, cash, cash_fraction=cash_fraction,
            )
            if position_gbp < pre_cash_clamp:
                clamp = "CASH"

            if not is_position_size_viable(position_gbp):
                continue

            would_deploy_pct = _would_deploy_pct(current_deployed, position_gbp, current_balance)
            if would_deploy_pct > max_deployed_pct:
                if instrument:
                    rejections.append({
                        "date": dt.date() if hasattr(dt, "date") else dt,
                        "ticker": ticker, "rank_within_day": rank_idx + 1,
                        "n_eligible": n_eligible,
                        "position_gbp": position_gbp,
                        "would_deploy_pct": round(would_deploy_pct, 4),
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
            n_open += 1  # ADDITION

            if instrument:
                admissions.append({
                    "date": dt.date() if hasattr(dt, "date") else dt,
                    "ticker": ticker,
                    "rank_within_day": rank_idx + 1,
                    "rank_pctile_within_day": round((rank_idx + 1) / max(n_eligible, 1), 6),
                    "n_eligible": n_eligible,
                    "position_gbp": position_gbp,
                    "var_raw_gbp": round(var_raw, 2) if np.isfinite(var_raw) else None,
                    "ceiling_gbp": round(dynamic_max_position_gbp, 2),
                    "cash_clamp_gbp": round(cash_fraction * (cash + position_gbp), 2),
                    "binding_clamp": clamp,
                    "balance_at_entry_gbp": round(current_balance, 2),
                    "cash_before_entry_gbp": round(cash + position_gbp, 2),
                    "deployed_pct_after": round(current_deployed / current_balance, 6),
                    "n_open_after": n_open,
                    "rank_value": momentum_val,
                })

        return current_deployed

    i = 0
    n_events = len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers: list[str] = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        # Phase A -- exits.
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
                crisis_now=crisis_now, price=stop_check_price,
                stop_price=stop_price, sig_on=sig_on, holding_days=holding_days,
                max_holding_days=st["max_hold"],
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
                n_open -= 1  # ADDITION

        # Phase B -- entries.
        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_balance, current_deployed)
            current_deployed = _admit_group(stock_group, dt, current_balance, current_deployed)

        if instrument:
            mtm_after = _open_positions_mtm_value()
            bal_after = cash + mtm_after
            panel.append({
                "date": dt.date() if hasattr(dt, "date") else dt,
                "cash": round(cash, 2),
                "mtm_value": round(mtm_after, 2),
                "current_balance": round(bal_after, 2),
                "current_deployed": round(mtm_after, 2),
                "current_deployed_pct": round(mtm_after / bal_after, 6) if bal_after > 0 else 0.0,
                "n_open_positions": n_open,
            })

        i = j

    # Defensive backstop only -- verbatim from production.
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

    if instrument:
        return trades, panel, admissions, slot_full, rejections
    return trades


# Local alias so the import surface above stays explicit about what is shared.
from analysis.decision import is_reentry_blocked as _is_reentry_blocked  # noqa: E402


def is_reentry_blocked_local(days_since_exit, cooldown_days):
    """Thin pass-through to the SHARED analysis/decision.py implementation --
    exists only so the call site above reads identically to production's
    keyword form. No logic of its own."""
    return _is_reentry_blocked(days_since_exit, cooldown_days=cooldown_days)
