"""Step 2: establish today's real baseline (current config, current DB,
live universe, mark-to-market engine -- the exact call the Backtest page
makes) via the REAL, unmodified simulate_portfolio_compounding_mtm(), then
run two post-hoc diagnostics against it (no engine changes yet):

  A) Peak-to-exit "giveback" distribution for winning trades that exited
     via MAX_HOLDING/SIGNAL_OFF (the population a peak-drawdown trailing
     exit would actually act on) -- to pick a sensible giveback_pct rather
     than blindly testing 1/3/5% with no grounding.
  B) S&P 500 vs. its own 7/14/30-day MA -- prevalence and agreement between
     windows, to pick a sensible single definition for the regime filter
     rather than grid-searching all three against the outcome.

`high` is loaded via a parallel precompute function (precompute_ticker_
with_high), mirroring the ma_extension_ceiling precedent's own
precompute_ticker_with_ma50 pattern -- production's real precompute_ticker()
is untouched. No engine code is modified in this step.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import time

import numpy as np
import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import _atr, precompute_ticker, simulate_portfolio_compounding_mtm, slice_ticker
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/vix_ma_giveback_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def precompute_ticker_with_high(ticker: str, config: dict):
    cached = precompute_ticker(ticker, config)
    if cached is None:
        return None
    frame = cached["frame"].copy()
    df = get_price_df(ticker, days=8000).set_index("date")
    df.index = pd.to_datetime(df.index)
    frame["high"] = df["high"].astype(float).reindex(frame.index)
    return {"ticker": ticker, "frame": frame}


def main():
    t0 = time.time()
    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.8))
    print(f"per_trade_pct={per_trade_pct} max_deployed_pct={max_deployed_pct}", flush=True)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Building tickers_cache (with high) for {len(candidate_tickers)} live candidates...", flush=True)
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker_with_high(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, END)
        caches.append(c)
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s elapsed)", flush=True)
    n_ok = sum(1 for c in caches if c is not None)
    print(f"  {n_ok}/{len(candidate_tickers)} tickers have usable frames ({time.time()-t0:.0f}s)\n", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    print("--- Running REAL simulate_portfolio_compounding_mtm() (unmodified, today's config) ---", flush=True)
    trades = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct)
    df = pd.DataFrame(trades)
    ec = df["exit_reason"].value_counts()
    print(f"  total_trades={len(df)}  by exit_reason:\n{ec}", flush=True)
    print(f"  total_pnl_gbp={df['pnl_gbp'].sum():.2f}", flush=True)
    df.to_csv(f"{OUT_DIR}/baseline_trades_today_config.csv", index=False)

    # ------------------------------------------------------------------
    # Diagnostic A: peak-to-exit giveback for MAX_HOLDING/SIGNAL_OFF wins.
    # ------------------------------------------------------------------
    print("\n=== Diagnostic A: peak-to-exit giveback (post-hoc, using 'high') ===", flush=True)
    frames_by_ticker = {c["ticker"]: c["frame"] for c in caches if c is not None}
    rows = []
    for _, tr in df.iterrows():
        if tr["exit_reason"] not in ("MAX_HOLDING", "SIGNAL_OFF"):
            continue
        frame = frames_by_ticker.get(tr["ticker"])
        if frame is None:
            continue
        entry_date = pd.Timestamp(tr["entry_date"])
        exit_date = pd.Timestamp(tr["exit_date"])
        window = frame.loc[(frame.index >= entry_date) & (frame.index <= exit_date), "high"]
        if window.empty or window.isna().all():
            continue
        peak_price = float(window.max())
        peak_date = window.idxmax()
        entry_price = float(tr["entry_price"])
        exit_price = float(tr["exit_price"])
        peak_gain_pct = (peak_price - entry_price) / entry_price * 100
        if peak_price <= entry_price:
            continue  # never went into profit intraday -- not this exit's population
        giveback_pct = (peak_price - exit_price) / peak_price * 100
        days_to_peak = (peak_date - entry_date).days
        holding_days = (exit_date - entry_date).days
        rows.append({
            "ticker": tr["ticker"], "entry_date": tr["entry_date"], "exit_date": tr["exit_date"],
            "exit_reason": tr["exit_reason"], "entry_price": entry_price, "exit_price": exit_price,
            "peak_price": peak_price, "peak_gain_pct": peak_gain_pct, "final_pnl_pct": tr["pnl_pct"] * 100,
            "giveback_pct": giveback_pct, "days_to_peak": days_to_peak, "holding_days": holding_days,
            "peak_frac_of_hold": days_to_peak / holding_days if holding_days > 0 else np.nan,
            "is_win": tr["is_win"],
        })
    gdf = pd.DataFrame(rows)
    gdf.to_csv(f"{OUT_DIR}/giveback_diagnostic.csv", index=False)
    print(f"  n={len(gdf)} MAX_HOLDING/SIGNAL_OFF trades that were in-profit at some point", flush=True)
    print(f"  final win rate among these: {(gdf['is_win']).mean():.1%}", flush=True)
    print("\n  giveback_pct distribution (all):")
    print(gdf["giveback_pct"].describe())
    print("\n  giveback_pct distribution, WINNERS only (final_pnl_pct > 0):")
    win_g = gdf[gdf["final_pnl_pct"] > 0]
    print(win_g["giveback_pct"].describe())
    print("\n  giveback_pct distribution, eventual LOSERS (peaked in profit, exited at a loss):")
    lose_g = gdf[gdf["final_pnl_pct"] <= 0]
    print(f"  n={len(lose_g)} ({len(lose_g)/len(gdf):.1%} of the population)")
    print(lose_g["giveback_pct"].describe())
    print("\n  where in the hold did the peak occur (peak_frac_of_hold, winners only)?")
    print(win_g["peak_frac_of_hold"].describe())

    for cap in [1, 3, 5, 8, 10, 15]:
        would_trigger = gdf["giveback_pct"] >= cap
        # Money-weighted effect: for trades that would trigger, compare
        # actual final pnl_gbp-equivalent (via pnl_pct) to what a
        # giveback-capped exit at peak*(1-cap%) would have captured, vs
        # what was actually realized.
        sub = gdf[would_trigger]
        if sub.empty:
            continue
        # Approximate captured pct if exited exactly at the cap trigger:
        captured_pct = sub["peak_gain_pct"] - cap
        actual_pct = sub["final_pnl_pct"]
        print(f"  cap={cap}%: triggers on {len(sub)}/{len(gdf)} ({len(sub)/len(gdf):.1%}) trades | "
              f"mean actual_final_pnl%={actual_pct.mean():.2f} vs mean capped_pnl%={captured_pct.mean():.2f} | "
              f"among these, {(sub['final_pnl_pct'] <= 0).mean():.1%} ended as losers despite peaking in profit")

    # ------------------------------------------------------------------
    # Diagnostic B: S&P 500 vs its own 7/14/30-day MA.
    # ------------------------------------------------------------------
    print("\n=== Diagnostic B: S&P 500 vs its own 7/14/30-day MA ===", flush=True)
    sp = gspc_close.to_frame("close")
    for w in (7, 14, 30):
        sp[f"ma_{w}"] = sp["close"].rolling(w).mean()
        sp[f"above_ma_{w}"] = sp["close"] > sp[f"ma_{w}"]
    sp = sp.dropna()
    for w in (7, 14, 30):
        print(f"  above MA{w}: {sp[f'above_ma_{w}'].mean():.1%} of days")
    agree_7_30 = (sp["above_ma_7"] == sp["above_ma_30"]).mean()
    agree_14_30 = (sp["above_ma_14"] == sp["above_ma_30"]).mean()
    agree_7_14 = (sp["above_ma_7"] == sp["above_ma_14"]).mean()
    print(f"  agreement MA7 vs MA30: {agree_7_30:.1%} | MA14 vs MA30: {agree_14_30:.1%} | MA7 vs MA14: {agree_7_14:.1%}")

    # Forward 20/60-trading-day S&P return conditional on regime, as a
    # sanity check of direction only (not an optimization search).
    for horizon in (20, 60):
        fwd = sp["close"].shift(-horizon) / sp["close"] - 1
        for w in (7, 14, 30):
            above = fwd[sp[f"above_ma_{w}"]]
            below = fwd[~sp[f"above_ma_{w}"]]
            print(f"  MA{w}, fwd{horizon}d return: above={above.mean()*100:.2f}% (n={above.count()}) "
                  f"vs below={below.mean()*100:.2f}% (n={below.count()})")

    sp.to_csv(f"{OUT_DIR}/sp500_ma_regime_diagnostic.csv")

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
