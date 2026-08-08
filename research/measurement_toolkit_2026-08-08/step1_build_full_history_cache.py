"""Full-history (un-sliced), standard close-based precompute cache for
get_research_stock_candidates() -- built once, reused across all 20 noise-
floor perturbation runs so each run only needs to re-slice/re-simulate, not
re-read price_data. Standard (close-based) momentum/signal, matching "the
current baseline configuration" -- NOT the adj_close dual-signal variant.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pickle
import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/measurement_toolkit_2026-08-08"


def main():
    candidate_tickers = sorted(universe.get_research_stock_candidates())
    t0 = time.time()
    print(f"{len(candidate_tickers)} candidate tickers", flush=True)

    caches = {}
    n_ok = 0
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            n_ok += 1
            caches[ticker] = c
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s, {n_ok} usable so far)", flush=True)
    print(f"  {n_ok}/{len(candidate_tickers)} tickers have usable frames ({time.time()-t0:.0f}s)", flush=True)

    with open(f"{OUT_DIR}/_cache_full_history.pkl", "wb") as fh:
        pickle.dump({"caches": caches, "candidate_tickers": candidate_tickers}, fh)
    print(f"Pickled to {OUT_DIR}/_cache_full_history.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
