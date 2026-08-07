"""Build and pickle tickers_cache + gspc_close + earnings_dates_by_ticker
once, so each variant run doesn't rebuild it (~80s)."""
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

OUT_DIR = "research/earnings_proximity_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def main():
    t0 = time.time()
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Building tickers_cache for {len(candidate_tickers)} live candidates...", flush=True)
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
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

    earnings = pd.read_csv(f"{OUT_DIR}/earnings_dates.csv", parse_dates=["earnings_date"])
    earnings_by_ticker = {t: sorted(g["earnings_date"].tolist()) for t, g in earnings.groupby("ticker")}

    with open(f"{OUT_DIR}/_cache.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "earnings_by_ticker": earnings_by_ticker,
        }, fh)
    print(f"Pickled to {OUT_DIR}/_cache.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
