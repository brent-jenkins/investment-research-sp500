"""corr_engine.py -- research-only ADDITIVE copy of
analysis/backtest.py:simulate_portfolio_compounding_mtm(), per
PREREGISTRATION.md Sec 2.

No shared module is modified. The ONLY behavioural change is the admission
sort key inside _admit_group():

    adjusted(c,t) = rank_pct(c,t) - k * corr_pct(c,t)

where both terms are within-day percentiles among that day's gate-passing
candidates. k = 0 reproduces production BYTE-IDENTICALLY while still running
the entire new path -- correlations computed, percentiles taken, adjusted sort
applied -- which is what makes the Sec 6.1 degenerate-parity check meaningful
rather than a bypass.

Everything else -- gate, 2% floor, re-entry cooldown, sizing, capacity, every
exit reason, the realistic intraday-touch STOP_LOSS fill, END_OF_DATA
force-close, mark-to-market accounting -- is a verbatim copy of production and
routes through the same shared analysis/decision.py functions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analysis import crisis, universe
from analysis.backtest import _would_deploy_pct
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    is_reentry_blocked,
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
from analysis.signals import _MAX_ANCHOR_GAP_DAYS
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _resolve_entry_stop, _var_position_size_core

CORR_WINDOW = 120          # trading days, Sec 2.1
MIN_OVERLAP = 60           # Sec 2.5 -- reuses _MIN_VAR_RETURNS's own floor
UNDEFINED_CORR_PCT = 0.5   # Sec 2.4 -- neutral, NOT zero


class CorrelationBook:
    """Rolling candidate-vs-held correlation over a shared returns matrix.

    Built once per pool and reused by every arm. Holds daily LOG returns as a
    dense (dates x tickers) array plus a validity mask, so a day's
    candidate-vs-held correlation block is one masked matrix product rather
    than thousands of pairwise calls.

    STALENESS GUARD (Sec 2.5). A correlation computed across a genuine data
    gap returns a real, WRONG number rather than NaN, which is strictly worse
    than missing -- the same hazard analysis/signals.py:_MAX_ANCHOR_GAP_DAYS
    exists for. A ticker whose own calendar gap anywhere inside the window
    exceeds that threshold is marked invalid for that window, as is any pair
    with fewer than MIN_OVERLAP overlapping observations.
    """

    def __init__(self, caches: list, dates: pd.DatetimeIndex):
        self.dates = dates
        self.tickers: list[str] = []
        cols = []
        for cached in caches:
            if cached is None:
                continue
            frame = cached["frame"]
            if frame is None or frame.empty:
                continue
            close = frame["close"].astype(float).reindex(dates)
            cols.append(np.log(close / close.shift(1)).to_numpy())
            self.tickers.append(cached["ticker"])
        self.R = np.column_stack(cols) if cols else np.zeros((len(dates), 0))
        self.idx = {t: i for i, t in enumerate(self.tickers)}
        self.valid = np.isfinite(self.R)
        # Per-ticker maximum calendar gap ending at each date, for the guard.
        day_gap = np.diff(dates.to_numpy()).astype("timedelta64[D]").astype(int)
        self._day_gap = np.r_[0, day_gap]

    def _window_ok(self, lo: int, hi: int) -> bool:
        """True if no single calendar gap inside [lo, hi) exceeds the guard."""
        return bool(self._day_gap[lo:hi].max(initial=0) <= _MAX_ANCHOR_GAP_DAYS)

    def corr_to_held(self, day_i: int, cand: list[str], held: list[str]) -> dict:
        """{ticker: mean correlation to `held`} for each candidate, omitting
        any candidate whose value is undefined. Undefined is NOT 0 -- the
        caller applies Sec 2.4's neutral fallback."""
        out: dict = {}
        if not held or not cand or day_i < CORR_WINDOW:
            return out
        lo, hi = day_i - CORR_WINDOW, day_i          # strictly BEFORE today
        if not self._window_ok(lo, hi):
            return out                                # whole window unusable

        ci = [self.idx[t] for t in cand if t in self.idx]
        hi_ = [self.idx[t] for t in held if t in self.idx]
        if not ci or not hi_:
            return out
        cand_kept = [t for t in cand if t in self.idx]

        W = self.R[lo:hi, :]
        M = self.valid[lo:hi, :].astype(np.float64)
        Wz = np.where(self.valid[lo:hi, :], W, 0.0)

        # Per-column mean/std over its own valid entries in this window.
        n = M.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mu = np.where(n > 0, Wz.sum(axis=0) / np.maximum(n, 1), 0.0)
            dev = np.where(self.valid[lo:hi, :], W - mu, 0.0)
            sd = np.sqrt(np.where(n > 1, (dev ** 2).sum(axis=0) / np.maximum(n - 1, 1), 0.0))
            Z = np.where(sd > 0, dev / np.where(sd > 0, sd, 1.0), 0.0)

        num = Z[:, ci].T @ Z[:, hi_]                  # sum of z-products
        cnt = M[:, ci].T @ M[:, hi_]                  # overlapping observations
        with np.errstate(invalid="ignore", divide="ignore"):
            C = np.where(cnt >= MIN_OVERLAP, num / np.maximum(cnt - 1, 1), np.nan)
        # A candidate with no usable pair at all stays undefined.
        with np.errstate(invalid="ignore"):
            means = np.nanmean(C, axis=1) if C.size else np.array([])
        for t, m in zip(cand_kept, means):
            if np.isfinite(m):
                out[t] = float(m)
        return out


def _pct_rank(values: list[float]) -> list[float]:
    """Within-day percentile in [0,1], ties averaged. A single candidate maps
    to 0.5 (neutral) rather than 0 or 1, so a one-candidate day cannot be
    swung by the adjustment."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = pd.Series(values).rank(method="average").to_numpy()
    return list((order - 1.0) / (n - 1.0))


def simulate_corr_admission(
    tickers_cache: list,
    config: dict,
    gspc_close: pd.Series,
    per_trade_pct: float,
    max_deployed_pct: float,
    k: float = 0.0,
    book: "CorrelationBook | None" = None,
    instrument: bool = False,
):
    """Returns `trades`, or `(trades, panel, admissions, corr_log)` when
    instrument=True. With k = 0 the `trades` output must be byte-identical to
    analysis/backtest.py:simulate_portfolio_compounding_mtm()."""
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
        per_ticker[ticker] = {
            "frame": frame, "rows": frame.to_dict("index"), "is_etf": is_etf, "idx": idx,
            "close_series": close_series, "raw_crisis": raw_crisis,
            "entry_blocked": entry_blocked, "max_hold": get_max_holding_days(ticker, config),
            "open_trade": None, "last_price": None, "last_exit_date": None,
        }
        for dt in frame.index:
            events.append((dt, (0 if is_etf else 1, idx), ticker))

    events.sort(key=lambda e: (e[0], e[1]))

    trades: list[dict] = []
    panel: list[dict] = []
    admissions: list[dict] = []
    corr_log: list[dict] = []
    cash = starting_capital
    day_pos = {d: i for i, d in enumerate(book.dates)} if book is not None else {}

    def _open_positions_mtm_value() -> float:
        total = 0.0
        for st in per_ticker.values():
            ot = st["open_trade"]
            if ot is None:
                continue
            lp = st["last_price"]
            total += ot["position_gbp"] if (lp is None or ot["entry_price"] <= 0) \
                else ot["position_gbp"] * (lp / ot["entry_price"])
        return total

    def _held_tickers() -> list[str]:
        return [t for t, st in per_ticker.items() if st["open_trade"] is not None]

    def _admit_group(group_tickers: list[str], dt, current_balance: float, current_deployed: float) -> float:
        nonlocal cash
        candidates: list[tuple[str, float]] = []
        for ticker in group_tickers:
            st = per_ticker[ticker]
            row = st["rows"][dt]
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            is_member = st["is_etf"] or universe.is_member(
                ticker, dt.date() if hasattr(dt, "date") else dt)
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
            rr = row.get("relative_rank")
            rr = float(rr) if pd.notna(rr) else None
            rank_val = resolve_admission_rank_value(
                momentum=momentum_val, relative_rank=rr, mode=admission_ranking_mode)
            candidates.append((ticker, rank_val if rank_val is not None else float("-inf")))

        if not candidates:
            return current_deployed

        # ---- Sec 2.3: the ONLY behavioural change. At k = 0 the adjusted key
        # is a strictly monotone function of rank_val, so the resulting order
        # is identical to production's -- verified by Sec 6.1.
        names = [c[0] for c in candidates]
        raw_vals = [c[1] for c in candidates]
        rank_pct = _pct_rank(raw_vals)

        corr_map: dict = {}
        n_undef = 0
        if k != 0.0 and book is not None:
            held = _held_tickers()
            di = day_pos.get(dt)
            if di is not None:
                corr_map = book.corr_to_held(di, names, held)
            defined = [corr_map.get(t) for t in names]
            have = [v for v in defined if v is not None]
            n_undef = len(names) - len(have)
            if have:
                have_pct = dict(zip([t for t in names if t in corr_map], _pct_rank(have)))
                corr_pct = [have_pct.get(t, UNDEFINED_CORR_PCT) for t in names]
            else:
                corr_pct = [UNDEFINED_CORR_PCT] * len(names)
            adjusted = [rp - k * cp for rp, cp in zip(rank_pct, corr_pct)]
        else:
            corr_pct = [UNDEFINED_CORR_PCT] * len(names)
            adjusted = list(rank_pct)
            n_undef = len(names) if k != 0.0 else 0

        if instrument:
            corr_log.append({
                "date": dt.date() if hasattr(dt, "date") else dt,
                "n_candidates": len(names), "n_undefined": n_undef,
                "n_held": len(_held_tickers()),
            })

        ordered = sorted(
            range(len(names)),
            key=lambda i: (-adjusted[i], -raw_vals[i], per_ticker[names[i]]["idx"]),
        )

        for pos, i in enumerate(ordered):
            if current_balance <= 0:
                break
            ticker = names[i]
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
            stop, stop_distance_pct = _resolve_entry_stop("LONG", price, atr, config)

            dynamic_max_position_gbp = resolve_dynamic_max_position_gbp(
                current_balance, per_trade_pct, absolute_max_position_gbp)
            cs = st["close_series"]
            prior_closes = cs.loc[cs.index < dt].tail(var_lookback + 1)
            log_returns = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _dv, _hv = _var_position_size_core(
                    log_returns, current_balance, dynamic_max_position_gbp,
                    resolve_var_horizon_days(st["max_hold"], config),
                    var_confidence, max_risk_pct, min_position_gbp)
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
                "stop_price": stop, "stop_distance_pct": stop_distance_pct,
                "position_gbp": position_gbp,
            }
            st["last_price"] = price
            current_deployed += position_gbp
            cash -= position_gbp

            if instrument:
                admissions.append({
                    "date": dt.date() if hasattr(dt, "date") else dt, "ticker": ticker,
                    "rank_within_day": pos + 1, "n_eligible": len(names),
                    "rank_pctile_within_day": round((pos + 1) / max(len(names), 1), 6),
                    "raw_rank_value": raw_vals[i], "rank_pct": round(rank_pct[i], 6),
                    "corr_raw": corr_map.get(ticker), "corr_pct": round(corr_pct[i], 6),
                    "adjusted": round(adjusted[i], 6),
                    "position_gbp": position_gbp,
                    "balance_at_entry_gbp": round(current_balance, 2),
                    "cash_before_entry_gbp": round(cash + position_gbp, 2),
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

        for ticker in day_tickers:                       # Phase A -- exits
            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            st["last_price"] = price
            ot = st["open_trade"]
            if ot is None:
                continue
            sig_on = bool(row["signal"]) if pd.notna(row["signal"]) else False
            crisis_now = bool(st["raw_crisis"].loc[dt])
            entry_price, stop_price = ot["entry_price"], ot["stop_price"]
            holding_days = (dt - ot["entry_date"]).days
            low_val = row.get("low")
            stop_check_price = float(low_val) if (low_val is not None and pd.notna(low_val)) else price
            exit_reason = evaluate_exit_reason(
                crisis_now=crisis_now, price=stop_check_price, stop_price=stop_price,
                sig_on=sig_on, holding_days=holding_days, max_holding_days=st["max_hold"])
            if exit_reason is None and dt == st["frame"].index[-1]:
                exit_reason = "END_OF_DATA"
            if exit_reason == "STOP_LOSS":
                open_val = row.get("open")
                open_price = float(open_val) if (open_val is not None and pd.notna(open_val)) else price
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

        mtm_value = _open_positions_mtm_value()          # Phase B -- entries
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
            held_now = _held_tickers()
            panel.append({
                "date": dt.date() if hasattr(dt, "date") else dt,
                "cash": round(cash, 2), "mtm_value": round(mtm_after, 2),
                "current_balance": round(bal_after, 2),
                "current_deployed_pct": round(mtm_after / bal_after, 6) if bal_after > 0 else 0.0,
                "n_open_positions": len(held_now),
                "held": ";".join(sorted(held_now)),
            })
        i = j

    for ticker, st in per_ticker.items():                # defensive backstop
        ot = st["open_trade"]
        if ot is None:
            continue
        frame = st["frame"]
        exit_p = float(frame.iloc[-1]["close"])
        pnl_pct = (exit_p - ot["entry_price"]) / ot["entry_price"]
        pnl_gbp = ot["position_gbp"] * float(pnl_pct)
        trades.append({
            "ticker": ticker, "direction": "LONG",
            "entry_date": ot["entry_date"].date() if hasattr(ot["entry_date"], "date") else ot["entry_date"],
            "exit_date": frame.index[-1].date(), "entry_price": ot["entry_price"],
            "exit_price": exit_p, "exit_reason": "END_OF_DATA", "pnl_pct": float(pnl_pct),
            "position_gbp": ot["position_gbp"], "pnl_gbp": pnl_gbp,
            "stop_distance_pct": ot["stop_distance_pct"],
            "holding_days": (frame.index[-1] - ot["entry_date"]).days, "is_win": pnl_pct > 0,
        })
        cash += ot["position_gbp"] + pnl_gbp

    if instrument:
        return trades, panel, admissions, corr_log
    return trades
