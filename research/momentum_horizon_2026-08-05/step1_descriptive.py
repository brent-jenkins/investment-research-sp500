"""Step 1 -- descriptive, retrospective check of the 30-day MAX_HOLDING cap.

Question: does the position's OWN entry-time momentum-strength label
(Weak/Moderate/Strong/Very Strong, the exact live definition from
main.py:_compute_momentum_strength_bands()/_momentum_strength_label())
predict what price actually did AFTER a forced 30-day-cap exit? Purely
observational -- nothing here feeds a live decision, so no look-ahead-bias
concern; we are measuring what already happened.

Backtest convention: CONTINUOUS WALK (analysis.validation.run_full_validation()
is always a single continuous 2007-2026 walk; see its own docstring). This is
appropriate for Step 1 -- we are not comparing variants, we are pulling one
real, already-fidelity-established trade set and looking at what happened
next. (Step 2, if warranted, is the reset-at-boundary variant comparison.)

Engine: analysis.backtest.simulate_portfolio_compounding_mtm() via
analysis.validation.run_full_validation(config, mark_to_market=True,
live_universe=True) -- the one engine confirmed (CLAUDE.md) to genuinely
match live execution. Full 2007-08-05 window, live dynamic ~503-ticker stock
universe.

Momentum-strength bands: recomputed AT EACH TRADE'S OWN ENTRY DATE, against
that date's real eligible-candidate-pool momentum distribution -- not one
global 19-year distribution (regime-conflation trap the live version is
deliberately built to avoid; see main.py's own docstring, quoted in this
project's CLAUDE.md excerpt for this task).

"Eligible candidate pool" on a given date is defined identically to the
pool the live app's own Candidate Queue page bands against: every live
candidate ticker whose row on that date passes analysis.decision.
passes_entry_gate() (signal on AND genuine index member AND not
crisis-blocked) AND analysis.decision.passes_momentum_floor() against the
REAL, currently-configured floor (resolve_min_momentum_floor(config), i.e.
trading212_invest.min_momentum_pct = 0.02 in production) -- this is exactly
the pending_orders-queuing condition (brokers/trading212.py:
maybe_place_orders()'s decide-phase gate, and analysis/backtest.py:
simulate_portfolio_compounding_mtm()'s own _admit_group() candidate-
gathering step, before the momentum-descending sort and the capacity
skip-and-continue admission -- capacity does NOT gate what's "eligible",
only what's ultimately admitted, matching how pending_orders is queued
before any submission-time capacity check). Re-entry cooldown is NOT
applied to the pool definition -- resolve_reentry_cooldown_days(config) is
0 in production (the pipeline's own natural floor), so it is a no-op for
every already-flat candidate in this pool by construction, and computing
it per-ticker here would need each ticker's own last-exit history tracked
independently, adding real complexity for zero behavioural effect at the
production value. This is stated explicitly as a scope decision.

Every candidate day's pool is built directly from the SAME precompute_ticker()
frames (momentum/signal columns) the real engine used, and the SAME
analysis.universe.is_member() / analysis.crisis functions -- not a
re-derived approximation.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import pickle
import time

import numpy as np
import pandas as pd
import yaml

from analysis import crisis, universe
from analysis.backtest import precompute_ticker
from analysis.decision import (
    passes_entry_gate,
    passes_momentum_floor,
    resolve_min_momentum_floor,
)
from analysis.validation import run_full_validation
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_horizon_2026-08-05'
END = pd.Timestamp("2026-08-03")  # matches real DB coverage max date exactly
START = pd.Timestamp("2007-01-01")

FWD_WINDOWS = (10, 20, 30)
POST_EXIT_HIGH_WINDOW = 30  # trading days, for the "made a new high after exit" check


def _bands(momentum_values: pd.Series) -> dict:
    """Byte-identical logic to main.py:_compute_momentum_strength_bands()."""
    vals = momentum_values.dropna()
    if len(vals) < 4:
        return {}
    return {
        "q25": vals.quantile(0.25),
        "q75": vals.quantile(0.75),
        "q90": vals.quantile(0.90),
    }


def _label(momentum_pct, bands: dict) -> str:
    """Byte-identical logic to main.py:_momentum_strength_label()."""
    if not bands or pd.isna(momentum_pct):
        return "-"
    if momentum_pct >= bands["q90"]:
        return "Very Strong"
    if momentum_pct >= bands["q75"]:
        return "Strong"
    if momentum_pct >= bands["q25"]:
        return "Moderate"
    return "Weak"


def main():
    t0 = time.time()
    print("=== Step 1: MAX_HOLDING descriptive check ===", flush=True)

    # ------------------------------------------------------------------
    # 1. Real backtest run -- source of truth for MAX_HOLDING trades.
    # ------------------------------------------------------------------
    print("Running real backtest (simulate_portfolio_compounding_mtm, "
          "live_universe=True, continuous walk 2007-2026)...", flush=True)
    result = run_full_validation(
        CONFIG, start=START, end=END, mark_to_market=True, live_universe=True,
    )
    print(f"  engine_used={result['engine_used']} deployment_basis={result['deployment_basis']} "
          f"backtest_convention={result['backtest_convention']}", flush=True)
    print(f"  total_trades={result['total_trades']}  n_tickers_with_data={result['n_tickers_with_data']}"
          f"/{result['n_tickers_total']}", flush=True)

    all_trades = result["trades"]
    trades_df = pd.DataFrame(all_trades)
    exit_reason_counts = trades_df["exit_reason"].value_counts()
    print("  exit_reason breakdown:\n" + exit_reason_counts.to_string(), flush=True)

    max_hold_trades = trades_df[trades_df["exit_reason"] == "MAX_HOLDING"].copy()
    print(f"  MAX_HOLDING trades: {len(max_hold_trades)} "
          f"({100*len(max_hold_trades)/len(trades_df):.1f}% of all trades)", flush=True)

    with open(f"{OUT_DIR}/all_trades_full_run.json", "w") as fh:
        json.dump(all_trades, fh, default=str)
    print(f"  full trade-level output preserved -> all_trades_full_run.json ({len(all_trades)} trades)",
          flush=True)

    # ------------------------------------------------------------------
    # 2. Build the same precompute_ticker() caches the engine used, for
    #    every live candidate ticker -- needed to recover each admitted
    #    trade's own entry-time momentum value (not stored on the trade
    #    dict) and to reconstruct each entry date's real eligible pool.
    # ------------------------------------------------------------------
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"\nBuilding precompute_ticker() caches for {len(candidate_tickers)} live candidates...",
          flush=True)
    frames = {}
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            frames[ticker] = c["frame"]
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(candidate_tickers)} tickers cached "
                  f"({time.time()-t0:.0f}s elapsed)", flush=True)
    print(f"  {len(frames)}/{len(candidate_tickers)} tickers have usable frames", flush=True)

    with open(f"{OUT_DIR}/_frames_cache.pkl", "wb") as fh:
        pickle.dump(frames, fh)

    # Crisis series -- identical inputs the engine itself used (based on
    # ^GSPC only, so one series covers every ticker).
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    raw_crisis_gspc = crisis.compute_raw_trigger(gspc_close_full)
    entry_blocked_gspc = crisis.extend_trigger(raw_crisis_gspc)

    min_momentum_floor = resolve_min_momentum_floor(CONFIG)
    print(f"  min_momentum_floor (production) = {min_momentum_floor}", flush=True)

    # ------------------------------------------------------------------
    # 3. For every MAX_HOLDING trade: recover entry momentum, compute
    #    that date's real eligible-pool bands, label the trade, then
    #    measure real post-exit price behaviour.
    # ------------------------------------------------------------------
    print(f"\nProcessing {len(max_hold_trades)} MAX_HOLDING trades...", flush=True)
    records = []
    pool_cache = {}  # entry_date -> (bands dict, pool momentum list) memoised across tickers/date reuse

    for idx, trade in enumerate(max_hold_trades.to_dict("records")):
        ticker = trade["ticker"]
        entry_date = pd.Timestamp(trade["entry_date"])
        exit_date = pd.Timestamp(trade["exit_date"])
        frame = frames.get(ticker)
        if frame is None or entry_date not in frame.index:
            continue

        momentum_at_entry = frame.loc[entry_date, "momentum"]

        # --- Eligible pool + bands for this entry date (memoised) ---
        if entry_date not in pool_cache:
            pool_momentum = []
            crisis_val = bool(entry_blocked_gspc.reindex([entry_date]).ffill().iloc[0]) \
                if len(entry_blocked_gspc) else False
            for t2, f2 in frames.items():
                if entry_date not in f2.index:
                    continue
                row2 = f2.loc[entry_date]
                sig_on2 = bool(row2["signal"]) if pd.notna(row2["signal"]) else False
                if not sig_on2:
                    continue
                is_mem2 = universe.is_member(t2, entry_date.date())
                mom2 = row2["momentum"]
                mom2 = float(mom2) if pd.notna(mom2) else None
                if not passes_entry_gate(sig_on=sig_on2, is_member=is_mem2, entry_blocked=crisis_val):
                    continue
                if not passes_momentum_floor(mom2, min_momentum_floor):
                    continue
                pool_momentum.append(mom2)
            bands = _bands(pd.Series(pool_momentum))
            pool_cache[entry_date] = (bands, len(pool_momentum))
        bands, pool_size = pool_cache[entry_date]
        strength = _label(momentum_at_entry, bands)

        # --- Momentum at exit (same trailing-252d definition) + trend ---
        momentum_at_exit = frame.loc[exit_date, "momentum"] if exit_date in frame.index else np.nan
        # momentum ~20 trading days before exit, to assess rising/decaying
        exit_loc = frame.index.get_loc(exit_date) if exit_date in frame.index else None
        momentum_pre_exit = np.nan
        if exit_loc is not None and exit_loc >= 20:
            momentum_pre_exit = frame.iloc[exit_loc - 20]["momentum"]
        momentum_decaying = (
            bool(momentum_at_exit < momentum_pre_exit)
            if pd.notna(momentum_at_exit) and pd.notna(momentum_pre_exit) else None
        )

        # --- Price behaviour around the exit ---
        close = frame["close"].astype(float)
        entry_loc = frame.index.get_loc(entry_date)
        during_hold_max = float(close.iloc[entry_loc:exit_loc + 1].max()) if exit_loc is not None else np.nan
        exit_price = float(trade["exit_price"])
        already_peaked_at_exit = bool(exit_price < during_hold_max * 0.999) if pd.notna(during_hold_max) else None

        fwd = {}
        if exit_loc is not None:
            n_avail = len(close) - 1 - exit_loc
            for w in FWD_WINDOWS:
                if n_avail >= w:
                    fwd[f"fwd_ret_{w}d"] = float(close.iloc[exit_loc + w] / exit_price - 1.0)
                else:
                    fwd[f"fwd_ret_{w}d"] = None
            post_window = min(POST_EXIT_HIGH_WINDOW, n_avail)
            if post_window > 0:
                post_exit_max = float(close.iloc[exit_loc + 1: exit_loc + 1 + post_window].max())
                new_high_post_exit = bool(post_exit_max > exit_price)
                new_high_over_hold_max = bool(post_exit_max > during_hold_max)
            else:
                post_exit_max = None
                new_high_post_exit = None
                new_high_over_hold_max = None
        else:
            n_avail = 0
            post_exit_max = None
            new_high_post_exit = None
            new_high_over_hold_max = None

        records.append({
            "ticker": ticker,
            "entry_date": entry_date.date().isoformat(),
            "exit_date": exit_date.date().isoformat(),
            "entry_price": trade["entry_price"],
            "exit_price": exit_price,
            "pnl_pct": trade["pnl_pct"],
            "momentum_at_entry": None if pd.isna(momentum_at_entry) else float(momentum_at_entry),
            "strength_band": strength,
            "pool_size_at_entry": pool_size,
            "band_q25": None if not bands else float(bands["q25"]),
            "band_q75": None if not bands else float(bands["q75"]),
            "band_q90": None if not bands else float(bands["q90"]),
            "momentum_at_exit": None if pd.isna(momentum_at_exit) else float(momentum_at_exit),
            "momentum_decaying_at_exit": momentum_decaying,
            "during_hold_max_price": during_hold_max,
            "already_peaked_at_exit": already_peaked_at_exit,
            "trading_days_of_data_after_exit": n_avail,
            **fwd,
            "post_exit_max_price_30d": post_exit_max,
            "new_high_post_exit": new_high_post_exit,
            "new_high_over_hold_max": new_high_over_hold_max,
        })

        if (idx + 1) % 200 == 0:
            print(f"    {idx+1}/{len(max_hold_trades)} MAX_HOLDING trades processed "
                  f"({time.time()-t0:.0f}s elapsed)", flush=True)

    out_df = pd.DataFrame(records)
    out_df.to_csv(f"{OUT_DIR}/max_holding_trades_annotated.csv", index=False)
    print(f"\nWrote {len(out_df)} annotated MAX_HOLDING trades -> max_holding_trades_annotated.csv",
          flush=True)

    # ------------------------------------------------------------------
    # 4. Bucket by strength band, report real sample sizes + aggregates.
    # ------------------------------------------------------------------
    print("\n=== Bucketed by entry-time momentum-strength band ===", flush=True)
    band_order = ["Weak", "Moderate", "Strong", "Very Strong", "-"]
    summary_rows = []
    for band in band_order:
        sub = out_df[out_df["strength_band"] == band]
        if sub.empty:
            continue
        row = {
            "band": band,
            "n": len(sub),
            "mean_momentum_at_entry": sub["momentum_at_entry"].mean(),
            "mean_pnl_pct_at_cap": sub["pnl_pct"].mean(),
            "pct_already_peaked_at_exit": sub["already_peaked_at_exit"].mean(skipna=True),
            "pct_momentum_decaying_at_exit": sub["momentum_decaying_at_exit"].mean(skipna=True),
            "pct_momentum_still_positive_at_exit": (sub["momentum_at_exit"] > 0).mean(),
            "mean_fwd_ret_10d": sub["fwd_ret_10d"].mean(skipna=True),
            "mean_fwd_ret_20d": sub["fwd_ret_20d"].mean(skipna=True),
            "mean_fwd_ret_30d": sub["fwd_ret_30d"].mean(skipna=True),
            "median_fwd_ret_30d": sub["fwd_ret_30d"].median(skipna=True),
            "pct_fwd_ret_30d_positive": (sub["fwd_ret_30d"] > 0).mean(skipna=True),
            "pct_new_high_post_exit": sub["new_high_post_exit"].mean(skipna=True),
            "pct_new_high_over_hold_max": sub["new_high_over_hold_max"].mean(skipna=True),
        }
        summary_rows.append(row)
        print(f"\n--- {band}  (n={len(sub)}) ---")
        for k, v in row.items():
            if k in ("band", "n"):
                continue
            print(f"    {k}: {v}")

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(f"{OUT_DIR}/band_summary.csv", index=False)
    print(f"\nWrote band summary -> band_summary.csv", flush=True)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
