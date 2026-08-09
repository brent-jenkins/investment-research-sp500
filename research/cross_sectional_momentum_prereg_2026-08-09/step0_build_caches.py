"""Build Pool 1 and Pool 2 CLEAN caches for the cross-sectional momentum
study, against the current, freshly-copied database (612 CLEAN, confirmed
fresh at run time -- not assumed, per this project's own standing practice).
Reuses precompute_ticker()/slice_ticker() exactly as every prior cache build
in this project has -- no new per-ticker computation invented here.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")

import pickle
import time

import pandas as pd
import yaml
from sqlalchemy import text

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker
from data.database import get_engine
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-07")


def _build(candidate_tickers, label):
    t0 = time.time()
    print(f"{label}: {len(candidate_tickers)} candidate tickers", flush=True)
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
    print(f"  {n_ok}/{len(candidate_tickers)} usable ({time.time()-t0:.0f}s)", flush=True)
    return caches


def main():
    engine = get_engine()
    with engine.connect() as conn:
        clean = {
            row[0] for row in conn.execute(
                text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'")
            )
        }
    print(f"CLEAN tickers in DB (fresh query): {len(clean)}", flush=True)

    pool1_tickers = sorted(universe.get_live_stock_candidates())
    pool2_all = universe.get_research_stock_candidates()
    pool2_clean_tickers = sorted(set(pool2_all) & clean)
    print(f"Pool 1: {len(pool1_tickers)} tickers", flush=True)
    print(f"Pool 2 CLEAN: {len(pool2_clean_tickers)} tickers", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    caches_pool1 = _build(pool1_tickers, "pool1")
    with open(f"{OUT_DIR}/_cache_pool1.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches_pool1, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": pool1_tickers,
        }, fh)
    print("Pickled pool1", flush=True)

    caches_pool2_clean = _build(pool2_clean_tickers, "pool2_clean")
    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches_pool2_clean, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": pool2_clean_tickers,
        }, fh)
    print("Pickled pool2_clean", flush=True)


if __name__ == "__main__":
    main()
