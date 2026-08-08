"""Build the full Pool 2 cache (get_research_stock_candidates(), current
163-quarantined state) -- needed for the sanity gate (full-pool reproduction
of the published 4,922-trade/£114,990.04/7-16-block reference) -- and derive
the Pool 2 CLEAN cache (intersected with ticker_data_quality.status='CLEAN')
from the same per-ticker computation, rather than re-fetching/re-computing
per-ticker frames twice. A ticker's precomputed frame doesn't depend on which
named "pool" it's nominally grouped under, so this is a pure re-use, not an
approximation.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-admission-redesign")

import pickle
import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker
from data.ingestion import get_price_df
from data.database import get_engine
from sqlalchemy import text

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/admission_redesign_prereg_2026-08-08"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def main():
    candidate_tickers = sorted(universe.get_research_stock_candidates())
    t0 = time.time()
    print(f"pool2_full: {len(candidate_tickers)} candidate tickers", flush=True)

    engine = get_engine()
    with engine.connect() as conn:
        clean = {
            row[0] for row in conn.execute(
                text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'")
            )
        }
    print(f"CLEAN tickers in DB: {len(clean)}", flush=True)
    clean_in_pool2 = sorted(set(candidate_tickers) & clean)
    print(f"CLEAN tickers within Pool 2 candidate list: {len(clean_in_pool2)}", flush=True)

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

    with open(f"{OUT_DIR}/_cache_pool2_full.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": candidate_tickers,
        }, fh)
    print(f"Pickled full Pool 2 cache ({time.time()-t0:.0f}s total)", flush=True)

    # Derive Pool 2 CLEAN: same per-ticker cache entries, filtered to CLEAN-status tickers only.
    clean_set = set(clean_in_pool2)
    caches_clean = [c for c in caches if c is not None and c["ticker"] in clean_set]
    n_clean_ok = len(caches_clean)
    print(f"Pool 2 CLEAN: {n_clean_ok}/{len(clean_in_pool2)} CLEAN tickers have usable frames", flush=True)
    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches_clean, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": clean_in_pool2,
        }, fh)
    print(f"Pickled Pool 2 CLEAN cache ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
