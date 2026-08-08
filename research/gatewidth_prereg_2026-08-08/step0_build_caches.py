"""Build Pool 1 and Pool 2 CLEAN caches for the gate-width study, against the
current, corrected database (612 CLEAN, confirmed fresh at run time -- not
assumed). Reuses precompute_ticker() exactly as every prior cache build has.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-gatewidth-prereg")

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

OUT_DIR = "research/gatewidth_prereg_2026-08-08"
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")


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
            print(f"  {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s, {n_ok} usable)", flush=True)
    print(f"  {n_ok}/{len(candidate_tickers)} usable ({time.time()-t0:.0f}s)", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    with open(f"{OUT_DIR}/_cache_{label}.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": candidate_tickers,
        }, fh)
    print(f"Pickled {label} ({time.time()-t0:.0f}s total)", flush=True)


def main():
    engine = get_engine()
    with engine.connect() as conn:
        clean = {row[0] for row in conn.execute(text("SELECT ticker FROM ticker_data_quality WHERE status = 'CLEAN'"))}
    print(f"CLEAN tickers in DB (fresh query): {len(clean)}", flush=True)

    pool2_clean = sorted(set(universe.get_research_stock_candidates()) & clean)
    print(f"Pool 2 CLEAN candidate list: {len(pool2_clean)}", flush=True)
    _build(pool2_clean, "pool2_clean")

    pool1 = sorted(universe.get_live_stock_candidates())
    _build(pool1, "pool1")


if __name__ == "__main__":
    main()
