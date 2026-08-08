"""Build a dual-signal cache: for every candidate ticker, one price_data
read produces BOTH close-based and adj_close-based momentum/signal columns,
alongside the untouched close/open/low/atr columns used for entry/exit/stop
pricing in every arm. Two arm-specific per-ticker frames are then derived
in-memory (no second DB read) by selecting which momentum/signal pair maps
to the 'momentum'/'signal' keys simulate_portfolio_compounding_mtm() reads --
this is the whole "signal changes, prices stay close" separation, enforced
structurally rather than by convention.

Usage: python step2_build_dual_cache.py <pool1|pool2_clean|pool2_all>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pickle
import time

import pandas as pd
import yaml

from analysis import signals, universe
from analysis.backtest import _atr, slice_ticker
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/adj_close_momentum_2026-08-08"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def get_clean_tickers() -> set[str]:
    import sqlite3
    conn = sqlite3.connect("database/investment_research.db")
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM ticker_data_quality WHERE status='CLEAN'")
    clean = {row[0] for row in cur.fetchall()}
    conn.close()
    return clean


def precompute_ticker_dual(ticker: str, config: dict):
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    adj_close = df["adj_close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])

    momentum_close = signals.compute_momentum_series(close)
    signal_close = signals.compute_signal_series(close)
    momentum_adj = signals.compute_momentum_series(adj_close)
    signal_adj = signals.compute_signal_series(adj_close)

    frame = pd.DataFrame({
        "close": close, "atr": atr_s, "open": open_, "low": low,
        "momentum_close": momentum_close, "signal_close": signal_close,
        "momentum_adj": momentum_adj, "signal_adj": signal_adj,
    })
    return {"ticker": ticker, "frame": frame}


def derive_arm_frame(dual_cached, arm: str):
    """arm 'A' -> momentum_close/signal_close under 'momentum'/'signal'.
    arm 'B' -> momentum_adj/signal_adj under 'momentum'/'signal'.
    close/atr/open/low always pass through untouched."""
    if dual_cached is None:
        return None
    f = dual_cached["frame"]
    suffix = "close" if arm == "A" else "adj"
    out = pd.DataFrame({
        "close": f["close"], "atr": f["atr"], "open": f["open"], "low": f["low"],
        "momentum": f[f"momentum_{suffix}"], "signal": f[f"signal_{suffix}"],
    })
    return {"ticker": dual_cached["ticker"], "frame": out}


def main():
    pool = sys.argv[1]
    if pool == "pool1":
        candidate_tickers = sorted(universe.get_live_stock_candidates(as_of=pd.Timestamp("2026-08-07").date()))
    elif pool == "pool2_clean":
        candidate_tickers = sorted(get_clean_tickers() & universe.get_all_known_stock_tickers())
    elif pool == "pool2_all":
        candidate_tickers = sorted(universe.get_research_stock_candidates())
    else:
        raise ValueError(f"unknown pool {pool!r}")

    t0 = time.time()
    print(f"{pool}: {len(candidate_tickers)} candidate tickers", flush=True)

    caches = []
    n_ok = 0
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker_dual(ticker, CONFIG)
        if c is not None:
            f = c["frame"]
            in_range = f[(f.index >= START) & (f.index <= END)].dropna(subset=["close"])
            c = {"ticker": c["ticker"], "frame": in_range} if not in_range.empty else None
        if c is not None:
            n_ok += 1
        caches.append(c)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s, {n_ok} usable so far)", flush=True)
    print(f"  {n_ok}/{len(candidate_tickers)} tickers have usable frames ({time.time()-t0:.0f}s)", flush=True)

    with open(f"{OUT_DIR}/_cache_{pool}_dual.pkl", "wb") as fh:
        pickle.dump({"caches": caches, "candidate_tickers": candidate_tickers}, fh)
    print(f"Pickled to {OUT_DIR}/_cache_{pool}_dual.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
