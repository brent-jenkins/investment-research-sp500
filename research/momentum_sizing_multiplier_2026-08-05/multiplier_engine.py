"""Momentum-strength-conditioned sizing multiplier — research engine.

A verbatim copy of analysis/backtest.py:simulate_portfolio_compounding_mtm()
with exactly ONE isolated, marked addition inside _admit_group(): after the
real, unmodified _var_position_size_core() has produced position_gbp (pure
volatility-based VaR sizing, momentum plays no role in that computation --
see Step 1 of REPORT.md), a band-conditioned multiplier is applied to
position_gbp, and the absolute_max_position_gbp-aware ceiling
(dynamic_max_position_gbp) is re-applied AFTER the multiplier so the
existing safety backstop still binds on a multiplied-up position. Everything
else -- entry gate, momentum floor, re-entry cooldown, exit priority,
cash-availability clamp, minimum-viable-order floor, capacity/deployment
check, event-walk skeleton -- is copied unchanged from the real function.

Fidelity requirement (see CLAUDE.md's "Research scripts must verify
decision-engine parity"): with band_multiplier=None (or every band mapped to
1.0), this function must be byte-identical to the real
simulate_portfolio_compounding_mtm() -- verified in self_check.py.

Momentum-strength bands/labels (_compute_momentum_strength_bands /
_momentum_strength_label below) are a byte-for-byte port of
main.py's own functions of the same name (verified against main.py
2026-08-05) -- NOT re-derived or approximated. Computed once per admission
group per day, from that day's real gate-passing candidate pool (the exact
population brokers/trading212.py's maybe_place_orders() writes into
pending_orders for that decided_date -- see REPORT.md Step 2 for why this is
the correct population, not some other one).
"""
import numpy as np
import pandas as pd

from analysis import crisis, universe
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
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _atr, _resolve_entry_stop, _var_position_size_core
from analysis.backtest import _would_deploy_pct


# ---------------------------------------------------------------------------
# Ported verbatim from main.py (checked 2026-08-05) -- do not "improve" or
# approximate this; the whole point is to reuse the EXACT population/band
# definition the Dashboard already displays and the task brief specified.
# ---------------------------------------------------------------------------

def _compute_momentum_strength_bands(momentum_values: pd.Series) -> dict:
    vals = momentum_values.dropna()
    if len(vals) < 4:
        return {}
    return {
        "q25": vals.quantile(0.25),
        "q75": vals.quantile(0.75),
        "q90": vals.quantile(0.90),
    }


def _momentum_strength_label(momentum_pct, bands: dict) -> str:
    if not bands or pd.isna(momentum_pct):
        return "—"
    if momentum_pct >= bands["q90"]:
        return "Very Strong"
    if momentum_pct >= bands["q75"]:
        return "Strong"
    if momentum_pct >= bands["q25"]:
        return "Moderate"
    return "Weak"


# ---------------------------------------------------------------------------
# The engine -- copy of simulate_portfolio_compounding_mtm() with the one
# marked multiplier addition inside _admit_group().
# ---------------------------------------------------------------------------

def simulate_portfolio_compounding_mtm_multiplier(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    band_multiplier: dict | None = None,
    collect_rejections: bool = False,
    collect_stats: bool = False,
):
    """See analysis/backtest.py:simulate_portfolio_compounding_mtm()'s own
    docstring for the full accounting model -- unchanged here. The ONLY
    behavioural difference is the multiplier block marked
    "=== MULTIPLIER ===" below. band_multiplier maps a
    _momentum_strength_label() string ("Weak"/"Moderate"/"Strong"/
    "Very Strong"/"—") to a float multiplier; missing keys default to 1.0
    (inert). band_multiplier=None is fully inert (equivalent to every band
    mapped to 1.0) -- required for the fidelity self-check.

    collect_stats=True additionally returns a list of per-candidate sizing
    records (ticker, date, strength_label, multiplier, pre/post-multiplier
    position_gbp, whether the backstop clamp bound) for Step 4's checks.
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
    multiplier_stats: list[dict] = []
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

    def _admit_group(group_tickers: list[str], dt, current_balance: float, current_deployed: float) -> float:
        nonlocal cash
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
            if is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days):
                continue
            candidates.append((ticker, momentum_val if momentum_val is not None else float("-inf")))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        # === MULTIPLIER: band computation (isolated addition) ===
        # Bands computed from THIS day's real gate-passing candidate pool
        # for THIS group (before sort, before capacity is even considered)
        # -- the exact population brokers/trading212.py's maybe_place_
        # orders() writes into pending_orders for this decided_date, and
        # therefore the exact population main.py's own per-decided-date
        # groupby already uses for the identical label. The -inf sentinel
        # (momentum_val was genuinely None, i.e. the floor was a no-op) is
        # excluded from the population, matching main.py's dropna() on a
        # real, possibly-missing momentum_pct value -- -inf is a private
        # sort-fallback here, never a real momentum quantity.
        _band_population = [v for _, v in candidates if v != float("-inf")]
        _bands = _compute_momentum_strength_bands(pd.Series(_band_population, dtype=float))
        # === END MULTIPLIER band computation ===

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

            # === MULTIPLIER: applied AFTER the real VaR-based position_gbp
            # (isolated addition; _var_position_size_core() itself is
            # completely untouched -- see docstring/REPORT.md Step 1/2).
            # ORDERING, stated explicitly (REPORT.md Step 2):
            #   1. VaR sizing (volatility-only, unmodified) -> position_gbp
            #   2. multiplier applied here
            #   3. dynamic_max_position_gbp (the absolute_max_position_gbp-
            #      aware ceiling) RE-APPLIED, since _var_position_size_
            #      core()'s own internal ceiling clamp already ran on the
            #      PRE-multiplier value and a multiplier > 1.0 can push the
            #      result back above it.
            #   4. resolve_cash_constrained_position_gbp() and
            #      is_position_size_viable() run AFTER, completely
            #      unchanged below -- so the cash clamp always sees the
            #      final, post-multiplier, post-backstop figure, never the
            #      pre-multiplier one.
            _position_gbp_var = position_gbp
            _label_input = None if momentum_val == float("-inf") else momentum_val
            _strength_label = _momentum_strength_label(_label_input, _bands)
            _multiplier = 1.0
            if band_multiplier:
                _multiplier = float(band_multiplier.get(_strength_label, 1.0))
            position_gbp = position_gbp * _multiplier
            _backstop_bound = position_gbp > dynamic_max_position_gbp
            position_gbp = min(position_gbp, dynamic_max_position_gbp)
            position_gbp = round(position_gbp, 2)
            if collect_stats:
                multiplier_stats.append({
                    "ticker": ticker, "date": dt.date() if hasattr(dt, "date") else dt,
                    "strength_label": _strength_label, "multiplier": _multiplier,
                    "position_gbp_var": round(_position_gbp_var, 2),
                    "position_gbp_post_multiplier_pre_clamp": round(_position_gbp_var * _multiplier, 2),
                    "dynamic_max_position_gbp": round(dynamic_max_position_gbp, 2),
                    "backstop_bound": _backstop_bound,
                    "position_gbp_final_presized": position_gbp,
                })
            # === END MULTIPLIER ===

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
        day_tickers: list[str] = []
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

    out = [trades]
    if collect_rejections:
        out.append(rejections)
    if collect_stats:
        out.append(multiplier_stats)
    if len(out) == 1:
        return out[0]
    return tuple(out)
