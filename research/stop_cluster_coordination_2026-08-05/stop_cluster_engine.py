"""
Part 2 -- coordinated-exit engine for the correlated/uncoordinated
STOP_LOSS clustering investigation.

Built the same way every prior sizing/gating research script in this
project has been built (see research/concentration_risk_2026-08-05/
concentration_constraint_engine.py's own docstring for precedent, and
research/var_sizing_sweep_2026-08-05/var_sizing_engine.py before it) -- a
verbatim copy of the real, current analysis.backtest.
simulate_portfolio_compounding_mtm() (read directly from analysis/backtest.py
lines 1132-1535 on 2026-08-05) with ONE ISOLATED CHANGE, marked inline, plus
the same block-truncation technique already validated and reused throughout
this project for both reset-at-boundary variant comparisons and 16-block
validation.

**The exact mechanism (forward-looking, non-lookahead):**

Parameters `cluster_window_days` (W) and `cluster_min_stops` (N), both
None by default -- the inert/disabled state, which must reproduce the real
simulate_portfolio_compounding_mtm() BYTE-IDENTICAL (verified directly in
self_check.py, not assumed).

A running list `stop_loss_day_indices` records the trading-day index (via
the SAME calendar convention Part 1 used: built from the gspc_close series
passed into this function, which is always the FULL, unsliced 2007-2026
S&P 500 close series in every caller here -- exactly like every other
block-truncated engine in this project, only the per-ticker `frame` is ever
truncated for block/reset purposes, never gspc_close) of every STOP_LOSS
exit AS IT HAPPENS, in Phase A's per-ticker exit loop -- the identical,
unmodified real exit logic (evaluate_exit_reason()) decides STOP_LOSS
exactly as before; this only ADDS a record of when it happened.

After Phase A's exit loop finishes for day `dt` (i.e. after every ticker's
own natural exit for today, including any today's-own STOP_LOSS, has
already been recorded) -- and only if both `cluster_window_days` and
`cluster_min_stops` are set:

    day_idx_today = day_index_map[dt]   # trading-day index, ^GSPC calendar
    trailing_count = sum(
        1 for d in stop_loss_day_indices
        if day_idx_today - (cluster_window_days - 1) <= d <= day_idx_today
    )
    if trailing_count >= cluster_min_stops:
        for every ticker still open_trade is not None (i.e. every position
        that did NOT already naturally exit today):
            force-close it NOW, exit_reason="STOP_CLUSTER_COORDINATED",
            exit_price = st["last_price"] -- the ticker's own most
            recently known close (today's, if it has a row today; its
            last known forward-filled close otherwise) -- genuinely the
            "next available price", no lookahead: this is exactly the
            same "last known price" convention _open_positions_mtm_value()
            already uses for daily mark-to-market valuation.

**EDGE-TRIGGERED, not state-based** (corrected 2026-08-05 -- an earlier
version of this engine fired the coordination action on EVERY day the
rolling trailing_count remained >= N, not just the first. That was a
real bug, caught before any block-validation result was trusted: it made
Part 2 answer a different question than Part 1 actually quantified. Part
1's cluster count (129 genuine clusters for W=3/N=3 over 19 years) was
built by MERGING consecutive/overlapping flagged windows into ONE
detection event per cluster -- the whole point of "a cluster" is that it
is a single, discrete episode, not a re-triggering condition. A
state-based mechanism that re-fires on every day trailing_count stays
>= N does not test "what would coordinating each of Part 1's 129
clusters have cost/saved" at all -- it tests a much more aggressive,
different policy (confirmed directly: on the reset-at-boundary 2020-2026
test window, the state-based version fired 1,509 coordinated closures
against only 391 real STOP_LOSS exits in the SAME window -- a ~4x
mismatch versus anything resembling Part 1's cluster frequency, immediate
evidence the two were not the same mechanism).

The corrected rule tracks `cluster_active: bool`, exactly mirroring Part
1's window-merge logic: the coordination action fires ONCE, the moment
trailing_count first reaches N (the transition from `cluster_active=False`
to now `>= N`) -- this is the cluster's "detection moment", identical in
spirit to Part 1's earliest-flagged-trade-in-the-merged-span definition.
While trailing_count remains >= N on subsequent days (the same, still-
ongoing cluster), `cluster_active` stays True and NOTHING further fires
-- there are no more "other open positions" to force-close after the
first firing in the vast majority of cases regardless, but this also
correctly prevents any newly-opened position from being caught by an
already-fired, still-elevated trailing count. Only once trailing_count
drops back below N does `cluster_active` reset to False, so a genuinely
NEW, later cluster can fire again. Positions closed via
STOP_CLUSTER_COORDINATED do NOT themselves feed `stop_loss_day_indices`
(only genuine, independently-triggered STOP_LOSS exits do) -- this
prevents the mechanism's own actions from snowballing the trigger count.

Everything else is untouched, verbatim from the real engine.
"""
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


def simulate_with_stop_cluster_coordination(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    cluster_window_days: int = None,
    cluster_min_stops: int = None,
    block_start=None,
    block_end=None,
):
    """See module docstring. cluster_window_days=None or cluster_min_stops=None
    (either) reproduces the real simulate_portfolio_compounding_mtm() exactly
    -- the coordination hook never fires."""
    var_cfg = config.get("trading212_invest", {})
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(var_cfg.get("var_confidence", 0.95))
    var_lookback = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(var_cfg.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(var_cfg.get("absolute_max_position_gbp", 1_000_000))
    reentry_cooldown_days = resolve_reentry_cooldown_days(config)
    min_momentum_floor = resolve_min_momentum_floor(config)

    # ISOLATED CHANGE: trading-day index map, built once from the FULL
    # gspc_close series (never block-truncated by any caller in this
    # project), same convention Part 1's cluster analysis used.
    cluster_enabled = cluster_window_days is not None and cluster_min_stops is not None
    day_index_map = {d: i for i, d in enumerate(gspc_close.index)} if cluster_enabled else {}
    trading_days_sorted = gspc_close.index if cluster_enabled else None
    stop_loss_day_indices: list = []
    cluster_active = False  # edge-trigger state -- see module docstring

    def _day_idx(dt):
        if dt in day_index_map:
            return day_index_map[dt]
        earlier = trading_days_sorted[trading_days_sorted <= dt]
        if len(earlier):
            return day_index_map[earlier[-1]]
        return 0

    per_ticker: dict = {}
    events: list = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
        # Block truncation (verbatim technique from concentration_
        # constraint_engine.py / var_sizing_engine.py) -- used for
        # reset-at-boundary and 16-block validation only; None/None (the
        # default) leaves frame untouched.
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
            "frame": frame, "rows": frame.to_dict("index"), "is_etf": is_etf, "idx": idx,
            "close_series": close_series, "raw_crisis": raw_crisis, "entry_blocked": entry_blocked,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list = []
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
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp, st["max_hold"],
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(dynamic_max_position_gbp * 0.5, 2)

            position_gbp = resolve_cash_constrained_position_gbp(position_gbp, cash)
            if not is_position_size_viable(position_gbp):
                continue

            would_deploy_pct = _would_deploy_pct(current_deployed, position_gbp, current_balance)
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
                crisis_now=crisis_now, price=price, stop_price=stop_price, sig_on=sig_on,
                holding_days=holding_days, max_holding_days=st["max_hold"],
            )
            if exit_reason is None and dt == st["frame"].index[-1]:
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
                # ISOLATED CHANGE: record every genuine STOP_LOSS exit's
                # trading-day index for the rolling-window trigger below.
                if cluster_enabled and exit_reason == "STOP_LOSS":
                    stop_loss_day_indices.append(_day_idx(dt))

        # ISOLATED CHANGE: stop-cluster coordination trigger. Evaluated
        # once per day, after every ticker's own natural exit for today
        # has already been processed above, and BEFORE today's new
        # entries (Phase B) so freed capital is available the same day --
        # exactly mirroring how a real, same-day force-close-then-reassess
        # process would work. Inert (never evaluated at all) unless both
        # cluster_window_days and cluster_min_stops are set.
        if cluster_enabled:
            day_idx_today = _day_idx(dt)
            trailing_count = sum(
                1 for d in stop_loss_day_indices
                if day_idx_today - (cluster_window_days - 1) <= d <= day_idx_today
            )
            # EDGE-TRIGGERED (corrected 2026-08-05 -- see module docstring):
            # only fire on the transition into cluster_active, i.e. the
            # first day this trailing window reaches the threshold. While
            # cluster_active stays True on subsequent days (the same,
            # still-ongoing cluster), nothing further fires. Only once
            # trailing_count drops back below N does cluster_active reset,
            # so a later, genuinely new cluster can fire again.
            if trailing_count >= cluster_min_stops:
                if not cluster_active:
                    for ticker, st in per_ticker.items():
                        ot = st["open_trade"]
                        if ot is None:
                            continue
                        exit_price = st["last_price"] if st["last_price"] is not None else ot["entry_price"]
                        entry_price = ot["entry_price"]
                        pnl_pct = (exit_price - entry_price) / entry_price
                        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
                        holding_days = (dt - ot["entry_date"]).days
                        trades.append({
                            "ticker": ticker, "direction": "LONG",
                            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
                            "exit_date": dt.date() if hasattr(dt, "date") else dt,
                            "entry_price": entry_price, "exit_price": float(exit_price),
                            "exit_reason": "STOP_CLUSTER_COORDINATED", "pnl_pct": float(pnl_pct),
                            "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
                            "stop_distance_pct": ot["stop_distance_pct"],
                            "holding_days": holding_days, "is_win": pnl_pct > 0,
                        })
                        cash += ot["position_gbp"] + pnl_gbp
                        st["open_trade"] = None
                        st["last_exit_date"] = dt
                        # Deliberately NOT appended to stop_loss_day_indices
                        # -- only genuine, independently-triggered STOP_LOSS
                        # exits feed the trigger, per the module docstring.
                    cluster_active = True
                # else: already fired for this ongoing cluster -- no repeat.
            else:
                cluster_active = False

        mtm_value = _open_positions_mtm_value()
        current_balance = cash + mtm_value
        current_deployed = mtm_value
        if current_balance > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            current_deployed = _admit_group(etf_group, dt, current_balance, current_deployed)
            current_deployed = _admit_group(stock_group, dt, current_balance, current_deployed)

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

    return trades
