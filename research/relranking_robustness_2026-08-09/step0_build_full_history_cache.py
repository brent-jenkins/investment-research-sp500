"""Full-history (un-sliced), standard close-based precompute cache for Pool 2
CLEAN -- the relative-ranking study's own decisive pool, not the broader
get_research_stock_candidates() the original noise-floor toolkit used. Built
once, un-sliced, so each perturbation draw can re-slice to its own (possibly
shifted) start date without re-reading price_data.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-relranking-robustness")

import pickle
import time

import yaml
from sqlalchemy import text

from analysis import universe
from analysis.backtest import precompute_ticker
from data.database import get_engine

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/relranking_robustness_2026-08-09"


def main():
    engine = get_engine()
    with engine.connect() as conn:
        clean = {row[0] for row in conn.execute(text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'"))}
    print(f"CLEAN tickers in DB (fresh query): {len(clean)}", flush=True)

    candidate_tickers = sorted(set(universe.get_research_stock_candidates()) & clean)
    t0 = time.time()
    print(f"Pool 2 CLEAN: {len(candidate_tickers)} candidate tickers", flush=True)

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
