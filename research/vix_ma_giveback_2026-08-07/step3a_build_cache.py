"""Build and pickle tickers_cache (with 'high') + gspc_close + vix_close
once, so each variant run (step3b) doesn't have to rebuild it (~70s) and
can stay comfortably under any single-command time budget."""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker
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
    print(f"  {n_ok}/{len(candidate_tickers)} tickers have usable frames ({time.time()-t0:.0f}s)", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    vix_df = pd.read_csv(f"{OUT_DIR}/vix_history.csv", parse_dates=["date"]).set_index("date")
    vix_close = vix_df["vix_close"].astype(float)
    vix_close = vix_close[(vix_close.index >= START) & (vix_close.index <= END)]

    with open(f"{OUT_DIR}/_cache.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "vix_close": vix_close,
        }, fh)
    print(f"Pickled to {OUT_DIR}/_cache.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
