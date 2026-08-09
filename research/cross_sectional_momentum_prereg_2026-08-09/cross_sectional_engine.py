"""Cross-sectional momentum engine (arms B/C/D), per PREREGISTRATION.md.

Not a parameterization of simulate_portfolio_compounding_mtm() -- there is
no shared degenerate reduction (Section 12), so this is a new engine, but
every actual DECISION reuses the real, unmodified, shared functions:
  - Exits (CRISIS_EXIT/STOP_LOSS/MAX_HOLDING): analysis.decision.evaluate_exit_reason()
  - Rebalance-day rank exit (RANK_DROPPED): analysis.decision.evaluate_rank_dropped_exit()
  - Entry sizing: resolve_dynamic_max_position_gbp() / _var_position_size_core() /
    resolve_cash_constrained_position_gbp() / is_position_size_viable()
  - Re-entry cooldown: is_reentry_blocked() / resolve_reentry_cooldown_days()
  - Absolute momentum floor (Arm D only): passes_momentum_floor() / resolve_min_momentum_floor()
New logic, specific to this construction only: the ranking computation
(cross_sectional_signal.py) and the rebalance-day admission/rank-drop walk
below (there is nothing existing to share this with -- no other engine or
live path ranks candidates against each other or rebalances on a fixed
calendar grid).

Three arms are the same engine, argument-level differences only:
  B: max_holding_days=30,   apply_momentum_floor=False
  C: max_holding_days=None, apply_momentum_floor=False
  D: max_holding_days=None, apply_momentum_floor=True
"""
import numpy as np
import pandas as pd

from analysis import universe, crisis
from analysis.decision import (
    evaluate_exit_reason, evaluate_rank_dropped_exit,
    is_reentry_blocked, resolve_reentry_cooldown_days,
    passes_momentum_floor, resolve_min_momentum_floor,
    resolve_dynamic_max_position_gbp, resolve_cash_constrained_position_gbp,
    is_position_size_viable,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core
from analysis.backtest import _MIN_VAR_RETURNS

from cross_sectional_signal import compute_cross_sectional_rank_series

TOP_N_DEFAULT = 40


def simulate_cross_sectional_momentum(
    tickers_cache: list, config: dict, gspc_close: pd.Series, per_trade_pct: float,
    max_deployed_pct: float, top_n: int = TOP_N_DEFAULT,
    max_holding_days=None, apply_momentum_floor: bool = False,
):
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))
    reentry_cooldown_days = resolve_reentry_cooldown_days(config)
    min_momentum_floor = resolve_min_momentum_floor(config)

    # Global crisis series -- computed once against the shared gspc_close,
    # looked up by-date for any ticker on any rebalance date (avoids N
    # per-ticker reindex operations for a signal that's actually shared).
    raw_crisis_global = crisis.compute_raw_trigger(gspc_close)
    entry_blocked_global = crisis.extend_trigger(raw_crisis_global)

    per_ticker: dict = {}
    events: list = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        close_series = frame["close"].astype(float)
        rank_series = compute_cross_sectional_rank_series(close_series)

        per_ticker[ticker] = {
            "frame": frame, "rows": frame.to_dict("index"), "idx": idx,
            "close_series": close_series, "rank_series": rank_series,
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, idx, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    # Rebalance dates are derived from the real event calendar (the union of
    # every ticker's own trading days) -- the first trading day of each
    # (year, month) that actually appears in the walk. Precompute the
    # reindexed-ffill rank/crisis lookups ONCE per rebalance date, per
    # ticker -- vectorized, not N per-date asof() calls in the inner loop.
    all_dates = sorted({e[0] for e in events})
    rebalance_dates = []
    seen_months = set()
    for dt in all_dates:
        key = (dt.year, dt.month)
        if key not in seen_months:
            seen_months.add(key)
            rebalance_dates.append(dt)
    rebalance_index = pd.DatetimeIndex(rebalance_dates)

    rank_at_rebalance = {
        ticker: st["rank_series"].reindex(rebalance_index, method="ffill")
        for ticker, st in per_ticker.items()
    }
    entry_blocked_at_rebalance = entry_blocked_global.reindex(rebalance_index, method="ffill").fillna(False)

    trades: list = []
    panel: list = []
    pre_entry_balances: list = []  # diagnostic only, for verification spot-checks
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

    def _record_exit(ticker, dt, exit_price, exit_reason):
        nonlocal cash
        st = per_ticker[ticker]
        ot = st["open_trade"]
        entry_price = ot["entry_price"]
        pnl_pct = (exit_price - entry_price) / entry_price
        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
        holding_days = (dt - ot["entry_date"]).days
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

    def _try_entry(ticker, dt, current_balance, current_deployed):
        """Returns updated current_deployed; no-op (returns input unchanged)
        if the entry can't be made (no row today, capacity, sizing floor)."""
        nonlocal cash
        st = per_ticker[ticker]
        row = st["rows"].get(dt)
        if row is None:
            return current_deployed  # no real row today -- can't size/enter; try again next rebalance
        last_exit = st.get("last_exit_date")
        days_since_exit = (dt - last_exit).days if last_exit is not None else None
        if is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days):
            return current_deployed
        if current_balance <= 0:
            return current_deployed

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
                log_returns, current_balance, dynamic_max_position_gbp, max_holding_days or 9999,
                var_confidence, max_risk_pct, min_position_gbp,
            )
        else:
            position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

        position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)
        if not is_position_size_viable(position_gbp):
            return current_deployed

        would_deploy_pct = (current_deployed + position_gbp) / current_balance if current_balance > 0 else float("inf")
        if would_deploy_pct > max_deployed_pct:
            return current_deployed

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
    last_rebalance_month = None
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        # Phase A -- exits, every day, byte-identical decision to production
        # (only sig_on is always None: no daily time-series signal exists in
        # this construction, so SIGNAL_OFF structurally never fires).
        for ticker in day_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            st["last_price"] = price

            ot = st["open_trade"]
            if ot is None:
                continue
            crisis_now = bool(raw_crisis_global.reindex([dt], method="ffill").iloc[0]) if len(raw_crisis_global) else False

            entry_price = ot["entry_price"]
            stop_price = ot["stop_price"]
            holding_days = (dt - ot["entry_date"]).days

            stop_check_price = price
            low_val = row.get("low")
            if low_val is not None and pd.notna(low_val):
                stop_check_price = float(low_val)

            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=stop_check_price, stop_price=stop_price,
                sig_on=None, holding_days=holding_days, max_holding_days=max_holding_days,
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
                _record_exit(ticker, dt, exit_price, exit_reason)

        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value

        month_key = (dt.year, dt.month)
        is_rebalance_day = (month_key != last_rebalance_month) and (dt in rebalance_index)
        if is_rebalance_day:
            last_rebalance_month = month_key

            # Rank the FULL universe as of dt (not just day_tickers) --
            # cross-sectional selection considers every eligible candidate,
            # not only tickers that happened to trade exactly on dt.
            candidates = []
            for ticker, st in per_ticker.items():
                rank_val = rank_at_rebalance[ticker].get(dt)
                if rank_val is None or pd.isna(rank_val):
                    continue
                is_member = universe.is_member(ticker, dt.date() if hasattr(dt, "date") else dt)
                if not is_member:
                    continue
                blocked_now = bool(entry_blocked_at_rebalance.get(dt, False))
                if blocked_now:
                    continue
                if apply_momentum_floor and not passes_momentum_floor(float(rank_val), min_momentum_floor):
                    continue
                candidates.append((ticker, float(rank_val)))

            candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))
            current_top_n = {t for t, _ in candidates[:top_n]}

            # RANK_DROPPED: currently-held (post-Phase-A) positions no
            # longer in the freshly-computed top-N.
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
                    _record_exit(ticker, dt, exit_price, reason)

            # Recompute balance/deployed after RANK_DROPPED exits, before entries.
            mtm_value = _open_positions_mtm_value()
            current_balance = cash + mtm_value
            current_deployed = mtm_value

            # New entries: top-N candidates not currently held, in rank order.
            for ticker, _rank_val in candidates:
                if ticker not in current_top_n:
                    continue  # candidates list is already truncated to top_n above; defensive only
                if per_ticker[ticker]["open_trade"] is not None:
                    continue  # already held, left untouched
                pre_entry_balances.append({
                    "date": dt.date() if hasattr(dt, "date") else dt, "ticker": ticker,
                    "current_balance": current_balance,
                })
                current_deployed = _try_entry(ticker, dt, current_balance, current_deployed)

        n_held = sum(1 for st in per_ticker.values() if st["open_trade"] is not None)
        panel.append({
            "date": dt.date() if hasattr(dt, "date") else dt, "cash": round(cash, 2),
            "mtm_value": round(_open_positions_mtm_value(), 2), "current_balance": round(current_balance, 2),
            "current_deployed": round(current_deployed, 2),
            "current_deployed_pct": round(100.0 * current_deployed / current_balance, 4) if current_balance > 0 else 0.0,
            "is_rebalance_day": bool(is_rebalance_day), "n_held": n_held,
        })

        i = j

    # Trailing END_OF_DATA exits for anything still open.
    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        last_dt = frame.index[-1]
        exit_p = float(frame.iloc[-1]["close"])
        _record_exit(ticker, last_dt, exit_p, "END_OF_DATA")

    return {"trades": trades, "panel": panel, "pre_entry_balances": pre_entry_balances}
