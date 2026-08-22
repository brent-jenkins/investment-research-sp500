"""Exit-mechanics sweep engine.

Built from research/long_short_prereg_2026-08-11/ls_engine.py's long path, which
was itself proven byte-identical to production simulate_portfolio_compounding_mtm()
in BOTH admission_ranking_mode settings. Only exit mechanics are added here.

Three dials, per PREREGISTRATION.md Sec 2:
  * atr_stop_multiplier  -- passed via a COPIED config dict, read by the real
                            _resolve_entry_stop(); never a code change.
  * max_holding_days     -- int, or None for "unlimited", which uses
                            evaluate_exit_reason()'s existing None-tolerance.
                            No change to analysis/decision.py.
  * trailing stop        -- Sec 4, research-only, NOT added to decision.py.

DEGENERATE REDUCTION (Sec 7.1): with trail_activation=None, max_holding_days=30
and atr_stop_multiplier=2.0, this must reproduce raw production byte-identical.
step2_verify.py proves it against real production output.

THE ANTI-LOOKAHEAD ORDERING (Sec 4) is the load-bearing detail. Each day, in
this exact order:
  1. CHECK the stop against today's LOW, using the trail level as it stood at
     YESTERDAY's close.
  2. Only if it survived: check activation against today's CLOSE.
  3. Update the high-water mark from today's CLOSE.
  4. Ratchet the trail UP (never down).
Doing 3-4 before 1 would let a single day's range both tighten the stop and then
trigger it, manufacturing exits no sequentially-executed trailing stop could
produce -- and doing so invisibly, since the result would look like an ordinary
STOP_LOSS.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis import universe, crisis                            # noqa: E402
from analysis.decision import (                                  # noqa: E402
    evaluate_exit_reason, passes_entry_gate, passes_momentum_floor,
    is_reentry_blocked, resolve_reentry_cooldown_days, resolve_min_momentum_floor,
    resolve_dynamic_max_position_gbp, resolve_cash_constrained_position_gbp,
    is_position_size_viable,
)
from analysis.volatility import _resolve_entry_stop, _var_position_size_core   # noqa: E402
from analysis.backtest import get_max_holding_days, _MIN_VAR_RETURNS           # noqa: E402


def simulate_exit_sweep(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    *,
    disable_stop_loss: bool = False,
    max_holding_days_override: int | None = 30,
    unlimited_holding: bool = False,
    trail_activation: float | None = None,
    trail_multiplier: float | None = None,
    trail_atr_source: str = "current",
    _sort_key: str = "rank",
):
    """config must already carry the cell's atr_stop_multiplier (copied dict).

    unlimited_holding=True passes max_holding_days=None to evaluate_exit_reason().
    trail_multiplier defaults to the cell's own atr_stop_multiplier (Sec 4:
    trail distance is FIXED at the multiplier being tested, never swept).

    trail_atr_source (added 2026-08-22, high-threshold study RULING Sec 4.3):
      'current' -- trail = hwm - k * ATR(t), ATR recomputed each day. THE
                   DEFAULT, and the ruled convention. Keeps '3.0x ATR'
                   meaning the same economic thing at +200% as at entry.
      'entry'   -- trail = hwm - k * ATR_at_entry, the original exit-sweep
                   convention. Retained ONLY so that study's cells stay
                   reproducible; NOT used by this study, because at high
                   activation it silently narrows the trail by the
                   activation multiple (1.3x at +30% .. 3.0x at +200%,
                   8.2x at +722%) and would test stop WIDTH while claiming
                   to test activation THRESHOLD.

    Sec 4.4 RULING -- the entry stop stays live after activation: the
    effective stop is max(entry_stop, trail). This holds BY CONSTRUCTION
    below (stop_price is seeded to initial_stop and only ever raised via
    max()), and is asserted rather than assumed in step3_verify.py V6.
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
    atr_mult = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))
    k_trail = float(trail_multiplier) if trail_multiplier is not None else atr_mult
    if trail_atr_source not in ("current", "entry"):
        raise ValueError(f"trail_atr_source must be current|entry, got {trail_atr_source!r}")

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

        if unlimited_holding:
            max_hold = None
        elif max_holding_days_override is not None:
            max_hold = int(max_holding_days_override)
        else:
            max_hold = get_max_holding_days(ticker, config)

        per_ticker[ticker] = {
            "frame": frame, "rows": frame.to_dict("index"), "is_etf": is_etf, "idx": idx,
            "close_series": close_series, "raw_crisis": raw_crisis, "entry_blocked": entry_blocked,
            "max_hold": max_hold,
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, (0 if is_etf else 1, idx), ticker))

    events.sort(key=lambda e: (e[0], e[1]))
    trades: list = []
    panel: list = []
    cash = starting_capital

    def _mtm():
        total = 0.0
        for st in per_ticker.values():
            ot = st["open_trade"]
            if ot is None:
                continue
            lp = st["last_price"]
            total += ot["position_gbp"] if (lp is None or ot["entry_price"] <= 0) \
                else ot["position_gbp"] * (lp / ot["entry_price"])
        return total

    def _admit(group_tickers, dt, equity, deployed):
        nonlocal cash
        candidates = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt)
            if not passes_entry_gate(sig_on=sig_on, is_member=is_member,
                                     entry_blocked=bool(st["entry_blocked"].loc[dt])):
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

            dyn_max = resolve_dynamic_max_position_gbp(equity, per_trade_pct,
                                                      absolute_max_position_gbp)
            cs = st["close_series"]
            prior = cs.loc[cs.index < dt].tail(var_lookback + 1)
            lr = np.log(prior / prior.shift(1)).dropna()
            if len(lr) >= _MIN_VAR_RETURNS:
                pos, _dv, _hv = _var_position_size_core(
                    lr, equity, dyn_max, st["max_hold"] if st["max_hold"] is not None else 30,
                    var_confidence, max_risk_pct, min_position_gbp)
            else:
                pos = round(dyn_max * 0.5, 2)
            pos = resolve_cash_constrained_position_gbp(pos, cash)
            if not is_position_size_viable(pos):
                continue
            if (deployed + pos) / equity > max_deployed_pct:
                continue

            st["open_trade"] = {
                "ticker": ticker, "entry_date": dt, "entry_price": price,
                "initial_stop": stop, "stop_price": stop, "stop_distance_pct": stop_dist,
                "position_gbp": pos, "atr_at_entry": atr,
                "hwm": price, "trail_active": False, "equity_at_entry": equity,
            }
            st["last_price"] = price
            deployed += pos
            cash -= pos
        return deployed

    def _record(ticker, dt, exit_price, reason, holding_days):
        nonlocal cash
        st = per_ticker[ticker]
        ot = st["open_trade"]
        pnl_pct = (exit_price - ot["entry_price"]) / ot["entry_price"]
        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": dt.date() if hasattr(dt, "date") else dt,
            "entry_price": ot["entry_price"], "exit_price": float(exit_price),
            "exit_reason": reason, "pnl_pct": float(pnl_pct),
            "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
            "stop_distance_pct": ot["stop_distance_pct"], "holding_days": holding_days,
            "is_win": pnl_pct > 0,
            "trail_activated": bool(ot["trail_active"]),
            "final_stop": ot["stop_price"], "initial_stop": ot["initial_stop"],
            "equity_at_entry": ot["equity_at_entry"],
        })
        cash += ot["position_gbp"] + pnl_gbp
        st["open_trade"] = None
        st["last_exit_date"] = dt

    i, n = 0, len(events)
    while i < n:
        dt = events[i][0]
        j = i
        day = []
        while j < n and events[j][0] == dt:
            day.append(events[j][2])
            j += 1
        flat_before = {t for t in day if per_ticker[t]["open_trade"] is None}

        # ---- Phase A: exits ----
        for ticker in day:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            st["last_price"] = price
            ot = st["open_trade"]
            if ot is None:
                continue

            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[dt])
            holding_days = (dt - ot["entry_date"]).days

            # STEP 1 -- check against today's LOW using YESTERDAY's trail level.
            lv = row.get("low")
            stop_check_price = float(lv) if lv is not None and pd.notna(lv) else price
            trail_has_raised = ot["stop_price"] > ot["initial_stop"] + 1e-12
            if disable_stop_loss and not ot["trail_active"]:
                effective_stop = None
            elif disable_stop_loss and not trail_has_raised:
                effective_stop = None          # trail armed but not yet above entry-stop level
            else:
                effective_stop = ot["stop_price"]

            reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=stop_check_price, stop_price=effective_stop,
                sig_on=sig_on, holding_days=holding_days, max_holding_days=st["max_hold"])
            if reason is None and dt == st["frame"].index[-1]:
                reason = "END_OF_DATA"

            if reason == "STOP_LOSS":
                ov = row.get("open")
                op = float(ov) if ov is not None and pd.notna(ov) else price
                exit_price = min(effective_stop, op)
                if trail_has_raised:
                    reason = "TRAILING_STOP"
            else:
                exit_price = price

            if reason:
                _record(ticker, dt, exit_price, reason, holding_days)
                continue

            # STEPS 2-4 -- only reached if the position survived today.
            if trail_activation is not None and not ot["trail_active"]:
                if price / ot["entry_price"] - 1.0 >= trail_activation:
                    ot["trail_active"] = True
            ot["hwm"] = max(ot["hwm"], price)
            if ot["trail_active"]:
                # Sec 4.3 RULING: current-date ATR by default (see docstring).
                if trail_atr_source == "current":
                    _a = row.get("atr")
                    atr_for_trail = float(_a) if pd.notna(_a) else None
                else:
                    atr_for_trail = ot["atr_at_entry"]
                if atr_for_trail is not None:
                    # max() => effective stop is max(entry_stop, trail): Sec 4.4.
                    ot["stop_price"] = max(ot["stop_price"],
                                           ot["hwm"] - k_trail * atr_for_trail)

        # ---- Phase B: entries ----
        mtm = _mtm()
        equity = cash + mtm
        deployed = mtm
        if equity > 0:
            etf = [t for t in flat_before if per_ticker[t]["is_etf"]]
            stk = [t for t in flat_before if not per_ticker[t]["is_etf"]]
            deployed = _admit(etf, dt, equity, deployed)
            deployed = _admit(stk, dt, equity, deployed)

        panel.append({
            "date": dt.date() if hasattr(dt, "date") else dt,
            "cash": round(cash, 2), "mtm_value": round(mtm, 2),
            "current_balance": round(equity, 2), "current_deployed": round(deployed, 2),
            "current_deployed_pct": round(100.0 * deployed / equity, 4) if equity > 0 else 0.0,
            "n_open_positions": sum(1 for s in per_ticker.values() if s["open_trade"] is not None),
        })
        i = j

    for ticker, st in per_ticker.items():
        ot = st["open_trade"]
        if ot is None:
            continue
        fr = st["frame"]
        _record(ticker, fr.index[-1], float(fr.iloc[-1]["close"]), "END_OF_DATA",
                (fr.index[-1] - ot["entry_date"]).days)

    return {"trades": trades, "panel": panel}
