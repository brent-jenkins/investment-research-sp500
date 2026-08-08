"""Rebuild Pool 2's cache against the current (post-Wikipedia-refinement,
163-quarantined) get_research_stock_candidates() pool -- the original
_cache_pool2.pkl was built before CPWR was quarantined. Same methodology
as step1_build_caches.py, new output filename so the original is untouched.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pickle
import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def main():
    candidate_tickers = sorted(universe.get_research_stock_candidates())
    t0 = time.time()
    print(f"pool2_final: {len(candidate_tickers)} candidate tickers", flush=True)

    caches = []
    n_ok = 0
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, END)
        if c is not None:
            n_ok += 1
        caches.append(c)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s, {n_ok} usable so far)", flush=True)
    print(f"  {n_ok}/{len(candidate_tickers)} tickers have usable frames ({time.time()-t0:.0f}s)", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    with open(f"{OUT_DIR}/_cache_pool2_final.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": candidate_tickers,
        }, fh)
    print(f"Pickled to {OUT_DIR}/_cache_pool2_final.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
