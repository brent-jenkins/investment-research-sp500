"""Long/short engine for Arms A, B and C.

Built as a strict extension of research/relative_ranking_prereg_2026-08-09/
relative_ranking_engine.py, which was itself proven byte-identical to
production simulate_portfolio_compounding_mtm() via a degenerate-case toggle.
The LONG path here is unchanged from that proven copy; everything short-side is
additive and gated behind enable_shorts.

DEGENERATE REDUCTION (Sec 11.1): with enable_shorts=False the short sleeve is
entirely inert -- margin_held is 0, short_liab is 0, equity reduces to
cash + long_mtm, free_cash reduces to cash -- so this engine must reproduce
Arm A byte-identical. step5_degenerate_parity.py proves it against raw
production output, not against this engine's own prior run.

Every long-side decision routes through the real shared production functions.
Every short-side decision routes through either the same shared function (where
direction-agnostic: sizing, stops, cooldown, viability) or the reflection-
verified mirror in ls_primitives.py (where not: the exit evaluator).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from analysis import universe, crisis                            # noqa: E402
from analysis.decision import (                                  # noqa: E402
    evaluate_exit_reason, passes_entry_gate, passes_momentum_floor,
    is_reentry_blocked, resolve_reentry_cooldown_days, resolve_min_momentum_floor,
    resolve_dynamic_max_position_gbp, resolve_cash_constrained_position_gbp,
    is_position_size_viable,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core   # noqa: E402
from analysis.backtest import get_max_holding_days, _MIN_VAR_RETURNS           # noqa: E402

import ls_primitives as lsp                                      # noqa: E402

SHORT_MARGIN_FRACTION = 0.50
"""Reg-T initial margin (Sec 7.2). Without it shorting is costless in the model
and the short sleeve sizes purely on VaR with no capital constraint at all."""


def simulate_long_short(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    long_cap: float,
    short_cap: float = 0.0,
    net_band: float | None = None,
    enable_shorts: bool = False,
    borrow_stress: float = 1.0,
    rally_cfg: dict | None = None,
    admission_order: str = "long_first",
    _sort_key: str = "rank",
):
    """admission_order: "long_first" (Arm B -- preserves baseline's own binding
    constraint) or "short_first" (Arm C -- lets scarce short capacity set the
    day's neutral budget; if longs went first they would hit the net ceiling
    immediately in any bull market and the short sleeve would rarely be
    reached, making C a throttled long book rather than a neutral one).

    _sort_key is TEST-ONLY: "rank" (real behaviour, relative_rank) or
    "momentum" (absolute momentum), used only to construct degenerate parity
    against production's two admission_ranking_mode settings.
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

    rally_cfg = rally_cfg or lsp.PRIMARY_CONFIG
    rally_raw_full = lsp.compute_rally_raw_trigger(
        gspc_close, rally_cfg["dd_gate"], rally_cfg["r5"])
    rally_block_full = lsp.extend_rally_trigger(rally_raw_full, rally_cfg["cooldown"])

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
        rally_raw = rally_raw_full.reindex(frame.index).ffill().fillna(False)
        rally_block = rally_block_full.reindex(frame.index).ffill().fillna(False)

        event_rank = (0 if is_etf else 1, idx)
        per_ticker[ticker] = {
            "frame": frame, "rows": frame.to_dict("index"), "is_etf": is_etf, "idx": idx,
            "close_series": close_series,
            "raw_crisis": raw_crisis, "entry_blocked": entry_blocked,
            "rally_raw": rally_raw, "rally_block": rally_block,
            "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, event_rank, ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list = []
    panel: list = []
    cash = starting_capital
    margin_held = 0.0

    # ---------------- valuation helpers ----------------
    def _long_mtm():
        total = 0.0
        for st in per_ticker.values():
            ot = st["open_trade"]
            if ot is None or ot["direction"] != "LONG":
                continue
            lp = st["last_price"]
            total += ot["position_gbp"] if (lp is None or ot["entry_price"] <= 0) \
                else ot["position_gbp"] * (lp / ot["entry_price"])
        return total

    def _short_liability():
        total = 0.0
        for st in per_ticker.values():
            ot = st["open_trade"]
            if ot is None or ot["direction"] != "SHORT":
                continue
            lp = st["last_price"]
            total += ot["position_gbp"] if (lp is None or ot["entry_price"] <= 0) \
                else ot["position_gbp"] * (lp / ot["entry_price"])
        return total

    # ---------------- sizing (shared, direction-agnostic) ----------------
    def _size(st, dt, equity):
        dyn_max = resolve_dynamic_max_position_gbp(equity, per_trade_pct, absolute_max_position_gbp)
        cs = st["close_series"]
        prior = cs.loc[cs.index < dt].tail(var_lookback + 1)
        lr = np.log(prior / prior.shift(1)).dropna()
        if len(lr) >= _MIN_VAR_RETURNS:
            pos, _dv, _hv = _var_position_size_core(
                lr, equity, dyn_max, st["max_hold"], var_confidence, max_risk_pct, min_position_gbp)
        else:
            pos = round(dyn_max * 0.5, 2)
        return pos

    # ---------------- admission: LONG (identical to production) ----------------
    def _admit_longs(group_tickers, dt, equity, long_mtm, short_liab):
        nonlocal cash
        candidates = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt)
            blocked_now = bool(st["entry_blocked"].loc[dt])
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member, entry_blocked=blocked_now):
                continue
            mv = row.get("momentum")
            mv = float(mv) if pd.notna(mv) else None
            if not passes_momentum_floor(mv, min_momentum_floor):
                continue
            last_exit = st.get("last_exit_date")
            dse = (dt - last_exit).days if last_exit is not None else None
            if is_reentry_blocked(dse, cooldown_days=reentry_cooldown_days):
                continue
            if _sort_key == "momentum":
                sv = mv if mv is not None else float("-inf")
            else:
                rv = row.get("relative_rank")
                sv = float(rv) if pd.notna(rv) else float("-inf")
            candidates.append((ticker, sv))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        for ticker, _sv in candidates:
            if equity <= 0:
                break
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_dist = _resolve_entry_stop("LONG", price, atr, config)

            pos = _size(st, dt, equity)
            pos = resolve_cash_constrained_position_gbp(pos, cash - margin_held)
            if not is_position_size_viable(pos):
                continue
            if (long_mtm + pos) / equity > long_cap:
                continue
            if net_band is not None and ((long_mtm + pos) - short_liab) / equity > net_band:
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "LONG", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_dist, "position_gbp": pos,
                "borrow_cost_gbp": 0.0, "htb_at_entry": False, "rate_at_entry": 0.0,
                "last_accrual_date": dt,
                # Emitted so the Sec 11.4 sizing check verifies against the
                # engine's OWN pre-entry balance, never a proxy reconstructed
                # after the fact -- the exact error that produced the
                # cross-sectional study's false 21/30 sizing "failure".
                "equity_at_entry": equity,
            }
            st["last_price"] = price
            long_mtm += pos
            cash -= pos
        return long_mtm

    # ---------------- admission: SHORT ----------------
    def _admit_shorts(group_tickers, dt, equity, long_mtm, short_liab):
        nonlocal cash, margin_held
        if not enable_shorts:
            return short_liab
        candidates = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            mv = row.get("momentum")
            mv = float(mv) if pd.notna(mv) else None
            if mv is None or mv >= 0:                    # mirror of signal > 0
                continue
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt)
            if is_member is not True:
                continue
            if bool(st["entry_blocked"].loc[dt]):        # crisis blocks NEW shorts (Sec 3)
                continue
            if bool(st["rally_block"].loc[dt]):          # rally breaker blocks NEW shorts
                continue
            # Sec 5.3 -- borrow cost enters the ENTRY DECISION via a dynamic floor.
            rate = lsp.borrow_rate_annual(row.get("close"), row.get("dd252"), row.get("vol60"),
                                          borrow_stress)
            if mv > -lsp.required_short_floor(min_momentum_floor, rate):
                continue
            last_exit = st.get("last_exit_date")
            dse = (dt - last_exit).days if last_exit is not None else None
            if is_reentry_blocked(dse, cooldown_days=reentry_cooldown_days):
                continue
            if _sort_key == "momentum":
                sv = mv if mv is not None else float("inf")
            else:
                rv = row.get("relative_rank")
                sv = float(rv) if pd.notna(rv) else float("inf")
            candidates.append((ticker, sv, rate))

        # ASCENDING -- weakest relative performers offered scarce short capacity
        # first. The exact mirror of the long sleeve's descending sort.
        candidates.sort(key=lambda c: (c[1], per_ticker[c[0]]["idx"]))

        for ticker, _sv, rate in candidates:
            if equity <= 0:
                break
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_dist = _resolve_entry_stop("SHORT", price, atr, config)

            pos = _size(st, dt, equity)
            if not is_position_size_viable(pos):
                continue
            if (short_liab + pos) / equity > short_cap:
                continue
            if net_band is not None and (long_mtm - (short_liab + pos)) / equity < -net_band:
                continue
            # Sec 7.2 -- Reg-T initial margin against genuinely free cash.
            required_margin = SHORT_MARGIN_FRACTION * pos
            if (cash - margin_held) < required_margin:
                continue

            st["open_trade"] = {
                "ticker": ticker, "direction": "SHORT", "entry_date": dt, "entry_price": price,
                "stop_price": stop, "stop_distance_pct": stop_dist, "position_gbp": pos,
                "borrow_cost_gbp": 0.0,
                "htb_at_entry": lsp.is_hard_to_borrow(row.get("close"), row.get("dd252"),
                                                      row.get("vol60")),
                "rate_at_entry": rate, "last_accrual_date": dt,
                "equity_at_entry": equity,
            }
            st["last_price"] = price
            short_liab += pos
            cash += pos                     # short proceeds
            margin_held += required_margin
        return short_liab

    # ---------------- exit recording ----------------
    def _record_exit(ticker, dt, exit_price, exit_reason, holding_days):
        nonlocal cash, margin_held
        st = per_ticker[ticker]
        ot = st["open_trade"]
        entry_price = ot["entry_price"]
        N = ot["position_gbp"]
        if ot["direction"] == "LONG":
            pnl_pct = (exit_price - entry_price) / entry_price
            pnl_gbp = N * float(pnl_pct)
            cash += N + pnl_gbp
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
            pnl_gbp = N * float(pnl_pct)
            liability = N * (exit_price / entry_price)
            cash -= liability                                  # buy back
            margin_held -= SHORT_MARGIN_FRACTION * N           # release collateral
        trades.append({
            "ticker": ticker, "direction": ot["direction"],
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": dt.date() if hasattr(dt, "date") else dt,
            "entry_price": entry_price, "exit_price": float(exit_price),
            "exit_reason": exit_reason, "pnl_pct": float(pnl_pct),
            "position_gbp": N, "pnl_gbp": pnl_gbp,
            "borrow_cost_gbp": ot["borrow_cost_gbp"],
            "net_pnl_gbp": pnl_gbp - ot["borrow_cost_gbp"],
            "htb_at_entry": ot["htb_at_entry"], "rate_at_entry": ot["rate_at_entry"],
            "equity_at_entry": ot["equity_at_entry"], "entry_stop_price": ot["stop_price"],
            "stop_distance_pct": ot["stop_distance_pct"], "holding_days": holding_days,
            "is_win": (pnl_gbp - ot["borrow_cost_gbp"]) > 0,
        })
        st["open_trade"] = None
        st["last_exit_date"] = dt

    # ---------------- the daily walk ----------------
    i, n_events = 0, len(events)
    while i < n_events:
        dt = events[i][0]
        j = i
        day_tickers = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}

        # -- Phase A: exits (both sleeves), always before same-day entries.
        for ticker in day_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            prev_price = st["last_price"]
            st["last_price"] = price

            ot = st["open_trade"]
            if ot is None:
                continue

            holding_days = (dt - ot["entry_date"]).days

            if ot["direction"] == "LONG":
                sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
                crisis_now = bool(st["raw_crisis"].loc[dt])
                stop_check = price
                lv = row.get("low")
                if lv is not None and pd.notna(lv):
                    stop_check = float(lv)
                exit_reason = evaluate_exit_reason(
                    crisis_now=crisis_now, price=stop_check, stop_price=ot["stop_price"],
                    sig_on=sig_on, holding_days=holding_days, max_holding_days=st["max_hold"])
                if exit_reason is None and dt == st["frame"].index[-1]:
                    exit_reason = "END_OF_DATA"
                if exit_reason == "STOP_LOSS":
                    ov = row.get("open")
                    op = float(ov) if ov is not None and pd.notna(ov) else price
                    exit_price = min(ot["stop_price"], op)
                else:
                    exit_price = price
            else:
                # Borrow accrues on the CURRENT mark-to-market notional, on a
                # calendar-day basis, at that day's own tier.
                days_elapsed = (dt - ot["last_accrual_date"]).days
                if days_elapsed > 0:
                    liab_prev = ot["position_gbp"] * (
                        (prev_price if prev_price is not None else ot["entry_price"])
                        / ot["entry_price"])
                    rate_today = lsp.borrow_rate_annual(
                        row.get("close"), row.get("dd252"), row.get("vol60"), borrow_stress)
                    cost = lsp.accrue_borrow_gbp(liab_prev, rate_today, days_elapsed)
                    ot["borrow_cost_gbp"] += cost
                    cash -= cost
                    ot["last_accrual_date"] = dt

                # The rally breaker is checked FIRST for shorts, exactly as
                # CRISIS_EXIT is checked first for longs.
                if bool(st["rally_raw"].loc[dt]):
                    exit_reason, exit_price = "RALLY_EXIT", price
                else:
                    short_sig_on = bool(row["momentum"] < 0) if pd.notna(row["momentum"]) else False
                    stop_check = price
                    hv = row.get("high")
                    if hv is not None and pd.notna(hv):
                        stop_check = float(hv)
                    exit_reason = lsp.evaluate_short_exit_reason(
                        crisis_now=False,          # Sec 3: crisis blocks new shorts, never exits them
                        price=stop_check, stop_price=ot["stop_price"], sig_on=short_sig_on,
                        holding_days=holding_days, max_holding_days=st["max_hold"])
                    if exit_reason is None and dt == st["frame"].index[-1]:
                        exit_reason = "END_OF_DATA"
                    if exit_reason == "STOP_LOSS":
                        ov = row.get("open")
                        op = float(ov) if ov is not None and pd.notna(ov) else price
                        exit_price = max(ot["stop_price"], op)
                    else:
                        exit_price = price

            if exit_reason:
                _record_exit(ticker, dt, exit_price, exit_reason, holding_days)

        # -- Phase B: entries, against post-exit state.
        long_mtm = _long_mtm()
        short_liab = _short_liability()
        equity = cash + long_mtm - short_liab

        if equity > 0:
            etf_group = [t for t in flat_before_today if per_ticker[t]["is_etf"]]
            stock_group = [t for t in flat_before_today if not per_ticker[t]["is_etf"]]
            if admission_order == "short_first":
                short_liab = _admit_shorts(etf_group, dt, equity, long_mtm, short_liab)
                short_liab = _admit_shorts(stock_group, dt, equity, long_mtm, short_liab)
                long_mtm = _admit_longs(etf_group, dt, equity, long_mtm, short_liab)
                long_mtm = _admit_longs(stock_group, dt, equity, long_mtm, short_liab)
            else:
                long_mtm = _admit_longs(etf_group, dt, equity, long_mtm, short_liab)
                long_mtm = _admit_longs(stock_group, dt, equity, long_mtm, short_liab)
                short_liab = _admit_shorts(etf_group, dt, equity, long_mtm, short_liab)
                short_liab = _admit_shorts(stock_group, dt, equity, long_mtm, short_liab)

        n_long = sum(1 for st in per_ticker.values()
                     if st["open_trade"] is not None and st["open_trade"]["direction"] == "LONG")
        n_short = sum(1 for st in per_ticker.values()
                      if st["open_trade"] is not None and st["open_trade"]["direction"] == "SHORT")
        panel.append({
            "date": dt.date() if hasattr(dt, "date") else dt,
            "cash": round(cash, 2), "margin_held": round(margin_held, 2),
            "free_cash": round(cash - margin_held, 2),
            "long_mtm": round(long_mtm, 2), "short_liab": round(short_liab, 2),
            "equity": round(equity, 2),
            "long_pct": round(100.0 * long_mtm / equity, 4) if equity > 0 else 0.0,
            "short_pct": round(100.0 * short_liab / equity, 4) if equity > 0 else 0.0,
            "gross_pct": round(100.0 * (long_mtm + short_liab) / equity, 4) if equity > 0 else 0.0,
            "net_pct": round(100.0 * (long_mtm - short_liab) / equity, 4) if equity > 0 else 0.0,
            "n_long": n_long, "n_short": n_short, "n_open_positions": n_long + n_short,
            # production-comparable alias, used by the degenerate parity check
            "current_deployed_pct": round(100.0 * long_mtm / equity, 4) if equity > 0 else 0.0,
            "current_balance": round(equity, 2),
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
