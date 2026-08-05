"""Concentration-risk investigation, Part 3: a correlation-aware sizing
haircut, layered on top of the real, unmodified shared decision functions --
NOT a fork of entry/exit logic, per CLAUDE.md's "There is exactly one
implementation of any given trading decision" section. This is a SIZING
constraint applied after the real _var_position_size_core() has already run,
analogous in spirit to how the existing deployment cap
(resolve_cash_constrained_position_gbp / is_position_size_viable) is already
layered on top of the VaR-sized position_gbp rather than baked into it.

Built the same way every prior sizing/gating research script in this project
has been built (see research/var_sizing_sweep_2026-08-05/var_sizing_engine.py's
own docstring for precedent) -- a verbatim copy of the real, current
analysis.backtest.simulate_portfolio_compounding_mtm() (read directly from
analysis/backtest.py lines 1132-1535 on 2026-08-05) with ISOLATED CHANGES,
each marked inline, plus the same block-truncation technique already
validated and reused throughout this project for both reset-at-boundary
variant comparisons and 16-block validation.

MOTIVATION (see results/tail_risk_isolation.json and REPORT.md Part 2):
the worst single days in the 19-year continuous-walk backtest are enriched,
well beyond chance (binomial p<0.0001, raw; still significant within-vol-
tercile matched comparisons for the indep_gap_60d measure specifically in 2
of 3 vol terciles / 2 of 3 bad-day thresholds), for elevated realized joint
risk among simultaneously-open positions relative to what independent VaR
sizing assumed. The full multivariate logistic regression did NOT reach
significance -- this constraint is being tested DESPITE that ambiguity,
exactly because CLAUDE.md's own culture requires testing real candidate
findings against block validation rather than pre-judging them from a single
comparison, but the researcher should stay skeptical throughout.

ISOLATED CHANGE from the real simulate_portfolio_compounding_mtm(): in
_admit_group(), immediately after the real, unmodified _var_position_size_core()
returns position_gbp (and BEFORE the existing cash-constraint/capacity checks,
which are untouched), compute the candidate ticker's trailing 60-trading-day
correlation (log returns, strictly before dt, same window/lookback convention
already used throughout this investigation) against EACH currently-open
ticker's own close series, take the MAX (a single highly-correlated existing
position is treated as "already holding this same bet", not averaged away by
several uncorrelated others), and if that max correlation exceeds
`corr_haircut_threshold`, multiply position_gbp by `corr_haircut_factor`
(e.g. 0.5 = halve the size). Below the threshold, or with fewer than
MIN_CORR_OBS overlapping observations, position_gbp is completely unchanged
-- byte-identical to the real function whenever corr_haircut_threshold=None.

block_start/block_end truncation: identical technique to
var_sizing_sweep_2026-08-05/var_sizing_engine.py (frame itself truncated
before close_series/raw_crisis/entry_blocked are derived, corrected
END_OF_DATA keyed to the truncated window's own last visited date).
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

CORR_LOOKBACK_DAYS = 60
MIN_CORR_OBS = 35


def simulate_with_concentration_haircut(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    corr_haircut_threshold: float = None,
    corr_haircut_factor: float = 1.0,
    block_start=None,
    block_end=None,
):
    """See module docstring. corr_haircut_threshold=None reproduces the real
    simulate_portfolio_compounding_mtm() exactly (no haircut ever applied)."""
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
        # ISOLATED CHANGE (block truncation, verbatim technique from
        # var_sizing_engine.py):
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

    def _max_corr_with_open(ticker: str, dt) -> float:
        """ISOLATED CHANGE helper: max trailing 60d log-return correlation
        of `ticker` against every currently-open ticker, using data strictly
        before dt. Returns 0.0 (no haircut) if fewer than MIN_CORR_OBS
        overlapping observations exist against every open ticker, or if
        nothing is open."""
        open_tickers = [t for t, s in per_ticker.items() if s["open_trade"] is not None]
        if not open_tickers:
            return 0.0
        cand_close = per_ticker[ticker]["close_series"]
        cand_prior = cand_close.loc[cand_close.index < dt].tail(CORR_LOOKBACK_DAYS + 1)
        cand_ret = np.log(cand_prior / cand_prior.shift(1)).dropna()
        if len(cand_ret) < MIN_CORR_OBS:
            return 0.0
        max_c = 0.0
        for ot_ticker in open_tickers:
            other_close = per_ticker[ot_ticker]["close_series"]
            other_prior = other_close.loc[other_close.index < dt].tail(CORR_LOOKBACK_DAYS + 1)
            other_ret = np.log(other_prior / other_prior.shift(1)).dropna()
            joined = pd.concat([cand_ret, other_ret], axis=1, join="inner")
            if len(joined) < MIN_CORR_OBS:
                continue
            c = joined.iloc[:, 0].corr(joined.iloc[:, 1])
            if pd.notna(c) and c > max_c:
                max_c = float(c)
        return max_c

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

            # ISOLATED CHANGE: correlation-aware sizing haircut, applied
            # after the real VaR-based position_gbp, before the existing
            # cash/capacity clamps below (which are otherwise untouched).
            if corr_haircut_threshold is not None:
                max_corr = _max_corr_with_open(ticker, dt)
                if max_corr > corr_haircut_threshold:
                    position_gbp = round(position_gbp * corr_haircut_factor, 2)

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
