"""Daily-admission relative-ranking engine for Arms B and C, per
PREREGISTRATION.md (narrowed on review — see Sec 1). Arm A is not built here
at all; it is the unmodified `simulate_portfolio_compounding_mtm()`.

**The one thing this engine changes relative to production, and nothing
else**: the SORT KEY used to order gate-passing candidates before the
capacity-constrained skip-and-continue admission walk. Every decision is
made by the real, unmodified shared functions, on the real, unmodified
absolute-momentum value:
  - Entry gate: passes_entry_gate() -- sig_on from ABSOLUTE momentum > 0.
  - 2% floor: passes_momentum_floor() -- applied to ABSOLUTE momentum.
  - Exits (CRISIS_EXIT/STOP_LOSS/SIGNAL_OFF/MAX_HOLDING): evaluate_exit_reason()
    -- sig_on from ABSOLUTE momentum, identical to production.
  - Sizing, cash clamp, viability, re-entry cooldown: identical shared
    functions, identical inputs, to production.

The cross-sectional rank signal (cross_sectional_signal.py, unmodified) is
computed for every ticker alongside the existing absolute-momentum value and
used ONLY as the sort key candidates.sort() ranks by, and (Arm C only) to
determine each day's top-N set for evaluate_rank_dropped_exit(). It never
reaches passes_entry_gate(), passes_momentum_floor(), or
evaluate_exit_reason()'s sig_on input -- verified directly in
step2_verification.py's signal-separation check (Sec 8.5 of the
pre-registration), not merely asserted by this docstring.

apply_rank_dropped_exit=False -> Arm B. apply_rank_dropped_exit=True -> Arm C.
"""
import numpy as np
import pandas as pd

from analysis import universe, crisis
from analysis.decision import (
    evaluate_exit_reason, evaluate_rank_dropped_exit, passes_entry_gate,
    passes_momentum_floor, is_reentry_blocked, resolve_reentry_cooldown_days,
    resolve_min_momentum_floor, resolve_dynamic_max_position_gbp,
    resolve_cash_constrained_position_gbp, is_position_size_viable,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core
from analysis.backtest import get_max_holding_days, _MIN_VAR_RETURNS

from cross_sectional_signal import compute_cross_sectional_rank_series

TOP_N = 30


def simulate_relative_ranking(
    tickers_cache: list, config: dict, gspc_close: pd.Series, per_trade_pct: float,
    max_deployed_pct: float, apply_rank_dropped_exit: bool = False,
    _sort_key: str = "rank",
):
    """_sort_key is TEST-ONLY, not part of the pre-registered design: "rank"
    (default, real Arm B/C behaviour) or "momentum" (sorts by the same
    absolute-momentum value production uses instead of the cross-sectional
    rank -- everything else in this engine unchanged). This creates a real
    degenerate-case parity check that was not available when the pre-
    registration was written: with _sort_key="momentum" and
    apply_rank_dropped_exit=False, this engine should reproduce
    simulate_portfolio_compounding_mtm() byte-identical, proving every part
    of this engine EXCEPT the one sort-key line is a faithful copy of
    production logic, not just documented as one."""
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
        rank_series = compute_cross_sectional_rank_series(close_series)

        raw_crisis = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        entry_blocked = crisis.extend_trigger(raw_crisis).reindex(frame.index).ffill().fillna(False)

        event_rank = (0 if is_etf else 1, idx)
        per_ticker[ticker] = {
            "frame": frame, "rows": frame.to_dict("index"), "is_etf": is_etf, "idx": idx,
            "close_series": close_series, "rank_series": rank_series,
            "raw_crisis": raw_crisis, "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))
    all_dates_index = pd.DatetimeIndex(sorted({e[0] for e in events}))

    # Rank value at every real trading day, per ticker -- forward-filled from
    # each ticker's own last known rank (mirrors how the monthly engine did
    # this for rebalance dates only; here it's every day, since B's own
    # admission sort needs it every day, not just C's rank-dropped check).
    rank_at_date = {
        ticker: st["rank_series"].reindex(all_dates_index, method="ffill")
        for ticker, st in per_ticker.items()
    }

    trades: list = []
    panel: list = []
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

    def _admit_group(group_tickers: list, dt, current_balance: float, current_deployed: float) -> float:
        """Identical to the production daily engine's own _admit_group()
        except the sort key: ranked by cross-sectional rank (descending),
        not absolute momentum. The gate itself (sig_on, is_member,
        entry_blocked, momentum floor, re-entry cooldown) is byte-identical
        to production -- reads ABSOLUTE momentum throughout, never the rank
        value."""
        nonlocal cash
        candidates: list[tuple[str, float]] = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
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
            if _sort_key == "momentum":
                sort_val = momentum_val if momentum_val is not None else float("-inf")
            else:
                rank_val = rank_at_date[ticker].get(dt)
                sort_val = float(rank_val) if rank_val is not None and pd.notna(rank_val) else float("-inf")
            candidates.append((ticker, sort_val))

        # THE ONLY LINE THAT DIFFERS FROM PRODUCTION'S _admit_group(): sort
        # key is the cross-sectional rank value, not absolute momentum.
        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        for ticker, _rank_val in candidates:
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
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

            position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)
            if not is_position_size_viable(position_gbp):
                continue

            would_deploy_pct = (current_deployed + position_gbp) / current_balance if current_balance > 0 else float("inf")
            if would_deploy_pct > max_deployed_pct:
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_distance_pct, "position_gbp": position_gbp,
            }
            st["last_price"] = price
            current_deployed += position_gbp
            cash -= position_gbp

        return current_deployed

    def _record_exit(ticker, dt, exit_price, exit_reason, holding_days):
        nonlocal cash
        st = per_ticker[ticker]
        ot = st["open_trade"]
        entry_price = ot["entry_price"]
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
        })
        cash += ot["position_gbp"] + pnl_gbp
        st["open_trade"] = None
        st["last_exit_date"] = dt

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

        # Phase A -- exits, every day, byte-identical decision logic to
        # production (evaluate_exit_reason(), absolute momentum sig_on).
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
                _record_exit(ticker, dt, exit_price, exit_reason, holding_days)

        # Arm C only: RANK_DROPPED, evaluated every day (not just monthly),
        # for positions that survived the standard exit check above.
        if apply_rank_dropped_exit:
            candidates_today = []
            for ticker, st in per_ticker.items():
                row = st["rows"].get(dt)
                if row is None:
                    continue
                rank_val = rank_at_date[ticker].get(dt)
                if rank_val is None or pd.isna(rank_val):
                    continue
                is_member = st["is_etf"] or universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
                if not is_member:
                    continue
                blocked_now = bool(st["entry_blocked"].loc[dt]) if dt in st["entry_blocked"].index else False
                if blocked_now:
                    continue
                candidates_today.append((ticker, float(rank_val)))
            candidates_today.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))
            current_top_n = {t for t, _ in candidates_today[:TOP_N]}

            for ticker, st in per_ticker.items():
                if st["open_trade"] is None:
                    continue
                is_in = ticker in current_top_n
                reason = evaluate_rank_dropped_exit(is_in_current_top_n=is_in)
                if reason:
                    row = st["rows"].get(dt)
                    exit_price = float(row["close"]) if row is not None else st["last_price"]
                    if exit_price is None:
                        exit_price = st["open_trade"]["entry_price"]
                    holding_days = (dt - st["open_trade"]["entry_date"]).days
                    _record_exit(ticker, dt, exit_price, reason, holding_days)

        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value

        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_balance, current_deployed)
            current_deployed = _admit_group(stock_group, dt, current_balance, current_deployed)

        panel.append({
            "date": dt.date() if hasattr(dt, "date") else dt, "cash": round(cash, 2),
            "mtm_value": round(mtm_value, 2), "current_balance": round(current_balance, 2),
            "current_deployed": round(current_deployed, 2),
            "current_deployed_pct": round(100.0 * current_deployed / current_balance, 4) if current_balance > 0 else 0.0,
            "n_open_positions": sum(1 for st in per_ticker.values() if st["open_trade"] is not None),
        })

        i = j

    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        exit_p = float(frame.iloc[-1]["close"])
        hd = (frame.index[-1] - ot["entry_date"]).days
        _record_exit(ticker, frame.index[-1], exit_p, "END_OF_DATA", hd)

    return {"trades": trades, "panel": panel}
