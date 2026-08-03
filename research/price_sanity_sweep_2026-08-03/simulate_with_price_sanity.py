"""Disposable research script -- price-sanity threshold sweep (2026-08-03).

Question: does the 3% submission-time price-sanity threshold
(brokers/trading212.py:_submit_pending_orders(), price_sanity_threshold_pct)
disproportionately filter out the strongest-momentum candidates? Tests a
genuine range of threshold values, both symmetric (block on any move) and
asymmetric (block only on a drop -- a rise confirms the momentum thesis
rather than invalidating it), each independently across the 16 historical
blocks this project already uses elsewhere (not just one continuous walk,
per the flat-cooldown-sweep's own path-dependency lesson).

Modeling choice, stated explicitly: this backtest is daily-bar-only (no
intraday data). decided_price = the signal day's close (exactly what the
existing engine already uses as entry_price); the "submission-time price"
proxy is the NEXT trading day's OPEN -- this mirrors live's real timing
(decide at EOD close, stock submission ~mid-morning the next trading day,
well before that day's own close) far better than using the next day's own
close would. A candidate that fails the check is skipped entirely for that
day (never enters at that day's close) -- exactly mirroring live's
SKIPPED_PRICE_MOVED (a dead decision, not retried), while the day-by-day
walk's own re-consideration of a still-eligible ticker the following day
reproduces live's real "the next EOD decide cycle re-queues it if the
signal is still on" behaviour for free, with no extra bookkeeping needed.
A passing candidate still enters at THAT day's own close (the existing
engine's own convention, left unchanged) -- the filter only gates
WHETHER entry happens, never repricing an admitted entry to the next-day
open, so the sizing/composition-skew question under test isn't conflated
with a second, unrelated change to execution-price convention.
"""
import copy
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

import yaml
from analysis import crisis, signals, universe
from analysis.backtest import (
    _would_deploy_pct,
    make_blocks,
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
)
from analysis.decision import (
    evaluate_exit_reason,
    is_position_size_viable,
    is_reentry_blocked,
    passes_entry_gate,
    resolve_cash_constrained_position_gbp,
    resolve_dynamic_max_position_gbp,
    resolve_reentry_cooldown_days,
)
from analysis.universe import get_max_holding_days
from analysis.volatility import _MIN_VAR_RETURNS, _atr, _resolve_entry_stop, _var_position_size_core
from data.ingestion import get_price_df

CONFIG = yaml.safe_load(open("config.yaml"))
STARTING_CAPITAL = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
VAR_CFG = CONFIG.get("trading212_invest", {})
PER_TRADE_PCT = float(VAR_CFG.get("per_trade_pct", 0.05))
MAX_DEPLOYED_PCT = float(VAR_CFG.get("max_total_deployed", 0.5))
END_DATE = pd.Timestamp("2026-08-01")
VALIDATION_START = pd.Timestamp("2007-01-01")


def precompute_ticker_with_open(ticker: str, config: dict):
    """Mirrors analysis.backtest.precompute_ticker() exactly (same
    get_price_df call, same ATR/momentum/signal computation, same 300-row
    floor) but additionally carries 'open' and a per-row 'next_open'
    column (that ticker's own next available trading day's open, aligned
    to today's index via shift(-1) -- NaN on a ticker's own final row,
    handled as "no data to check against" by the caller)."""
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    momentum_s = signals.compute_momentum_series(close)
    sig = signals.compute_signal_series(close)
    next_open = open_.shift(-1)

    frame = pd.DataFrame({
        "close": close, "atr": atr_s, "signal": sig, "momentum": momentum_s,
        "open": open_, "next_open": next_open,
    })
    return {"ticker": ticker, "frame": frame}


def slice_ticker(cached, ts_start, ts_end):
    if cached is None:
        return None
    frame = cached["frame"]
    in_range = frame[(frame.index >= ts_start) & (frame.index <= ts_end)].dropna(subset=["close"])
    if in_range.empty:
        return None
    return {"ticker": cached["ticker"], "frame": in_range}


def simulate_with_price_sanity(
    tickers_cache, config, gspc_close, per_trade_pct, max_deployed_pct,
    price_sanity_threshold=None, asymmetric=False,
    block_start=None, block_end=None,
    collect_skips=False,
):
    """Modified simulate_portfolio_compounding_mtm() -- identical exit
    logic, entry gate, momentum-priority skip-and-continue admission,
    cash/mtm accounting, and re-entry cooldown verbatim (see that
    function's own docstring in analysis/backtest.py for anything not
    called out here). Two additions:

    1. Price-sanity admission filter -- right after computing today's
       close (the candidate's would-be entry_price and "decided_price"),
       compare against that ticker's next_open. price_sanity_threshold=
       None means "no filter at all" (must reproduce the real engine
       exactly -- this is the fidelity check). asymmetric=True blocks
       only on a drop (pct_move < -threshold); asymmetric=False (the
       real live behaviour) blocks on abs(pct_move) > threshold either
       direction. A candidate with no next_open available (ticker's own
       final row) is treated as "no data to check against" -- skipped,
       mirroring live's real fail-loud "no current price -- leave
       pending" behaviour rather than guessing it's safe.

    2. Block truncation, built in from the start (not bolted on after
       the fact) -- when block_start/block_end are given, the event walk
       is restricted to that window and every still-open position is
       force-closed at block_end via END_OF_DATA, exactly the technique
       already validated in the "Is the 5-day re-entry cooldown result
       real" investigation (see CLAUDE.md). Every ticker's own fresh
       £starting_capital and zero open positions at block_start is
       already guaranteed by construction (nothing before block_start is
       ever touched), so no separate cash-reset step is needed.
    """
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
    var_confidence = float(VAR_CFG.get("var_confidence", 0.95))
    var_lookback = int(VAR_CFG.get("var_lookback_days", 252))
    max_risk_pct = float(VAR_CFG.get("max_risk_pct", 0.01))
    min_position_gbp = float(VAR_CFG.get("min_position_gbp", 50))
    absolute_max_position_gbp = float(VAR_CFG.get("absolute_max_position_gbp", 1_000_000))
    reentry_cooldown_days = resolve_reentry_cooldown_days(config)

    per_ticker: dict = {}
    events: list = []

    for idx, cached in enumerate(tickers_cache):
        if cached is None:
            continue
        ticker = cached["ticker"]
        frame = cached["frame"]
        if frame is None or frame.empty:
            continue
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

    trades: list = []
    skips: list = []
    # Every candidate actually reached inside the ranked per-candidate loop
    # (i.e. not cut short by current_balance<=0) when price_sanity_threshold
    # is active -- gives an apples-to-apples denominator for the skew
    # question: of top-decile candidates actually reached, what fraction
    # got blocked, vs of non-top-decile candidates reached?
    reached: list = []
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

    def _admit_group(group_tickers, dt, current_balance, current_deployed):
        nonlocal cash
        candidates = []
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
            last_exit = st.get("last_exit_date")
            days_since_exit = (dt - last_exit).days if last_exit is not None else None
            if is_reentry_blocked(days_since_exit, cooldown_days=reentry_cooldown_days):
                continue
            momentum_val = row.get("momentum")
            momentum_val = float(momentum_val) if pd.notna(momentum_val) else float("-inf")
            candidates.append((ticker, momentum_val))

        candidates.sort(key=lambda c: (-c[1], per_ticker[c[0]]["idx"]))

        # Momentum-decile context for this day's eligible pool (before the
        # price-sanity filter removes anything) -- used to classify each
        # skip as "top decile" or not, mirroring
        # main.py:_compute_momentum_strength_bands()'s own "relative to
        # today's own real pool" convention.
        finite_moms = [m for _, m in candidates if m != float("-inf")]
        p90 = np.percentile(finite_moms, 90) if len(finite_moms) >= 4 else None

        for ticker, momentum_val in candidates:
            if current_balance <= 0:
                break

            st = per_ticker[ticker]
            row = st["rows"][dt]
            price = float(row["close"])
            is_top_decile = (p90 is not None and momentum_val >= p90)

            # --- Price-sanity admission filter -----------------------------
            if price_sanity_threshold is not None:
                next_open = row.get("next_open")
                if next_open is None or pd.isna(next_open) or price <= 0:
                    # No data to check against (ticker's own final row) --
                    # fail loud, don't guess: treat as blocked, mirroring
                    # live's real "_live_current_price() returned None --
                    # leave pending" behaviour.
                    if collect_skips:
                        skips.append({
                            "date": dt.date() if hasattr(dt, "date") else dt,
                            "ticker": ticker, "momentum": momentum_val,
                            "pct_move": None, "top_decile": is_top_decile,
                            "reason": "no_next_open",
                        })
                        reached.append({"top_decile": is_top_decile, "blocked": True})
                    continue
                pct_move = (float(next_open) - price) / price
                blocked = (
                    (pct_move < -price_sanity_threshold) if asymmetric
                    else (abs(pct_move) > price_sanity_threshold)
                )
                if collect_skips:
                    reached.append({"top_decile": is_top_decile, "blocked": blocked})
                if blocked:
                    if collect_skips:
                        skips.append({
                            "date": dt.date() if hasattr(dt, "date") else dt,
                            "ticker": ticker, "momentum": momentum_val,
                            "pct_move": pct_move,
                            "top_decile": is_top_decile,
                            "reason": "price_sanity",
                        })
                    continue
            # -----------------------------------------------------------------

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
        day_tickers = []
        while j < n_events and events[j][0] == dt:
            day_tickers.append(events[j][2])
            j += 1

        flat_before_today = {t for t in day_tickers if per_ticker[t]["open_trade"] is None}
        is_last_event_day = (j == n_events)

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

    # Force-close every still-open position at the end of the (possibly
    # block-truncated) window -- covers both a ticker's own genuine final
    # row (matching the unmodified engine's own defensive backstop) and,
    # when block_end is set, every position still open at the block
    # boundary.
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

    if collect_skips:
        return trades, skips, reached
    return trades


if __name__ == "__main__":
    t0 = time.time()
    print("Building candidate universe (live_universe, as_of=%s)..." % END_DATE.date())
    candidate_tickers = universe.get_live_stock_candidates(as_of=END_DATE.date())
    print("Candidates:", len(candidate_tickers))

    print("Precomputing (this is the expensive, one-time step)...")
    caches = []
    n_ok = 0
    for t in candidate_tickers:
        c = precompute_ticker_with_open(t, CONFIG)
        if c is not None:
            n_ok += 1
        caches.append(c)
    print(f"Precompute done in {time.time()-t0:.1f}s -- {n_ok}/{len(candidate_tickers)} usable")

    gspc_raw = get_price_df("^GSPC", days=8000)
    gspc_raw = gspc_raw.set_index("date")
    gspc_raw.index = pd.to_datetime(gspc_raw.index)
    gspc_close_full = gspc_raw["close"].astype(float)

    import pickle
    with open("precomputed_caches.pkl", "wb") as f:
        pickle.dump({"caches": caches, "gspc_close_full": gspc_close_full}, f)
    print(f"Saved precomputed caches. Total elapsed: {time.time()-t0:.1f}s")
