"""Build the AFTER-remediation tickers_cache -- using universe.
get_research_stock_candidates() (get_all_known_stock_tickers() with
QUARANTINED tickers excluded, per ticker_data_quality) instead of the
raw, unrestricted universe.get_all_known_stock_tickers() the invalidated
re-verification and the "before" baseline (research/earnings_dampener_
reverify_2026-08-07/trades_cap05_baseline.csv / trades_cap07_baseline.csv)
both used. This is the single changed variable for item 9's before/after
comparison -- same engine, same caps, same config, same date range.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-data-quality")

import pickle
import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/data_quality_remediation_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def main():
    t0 = time.time()
    all_known = universe.get_all_known_stock_tickers()
    quarantined = universe.get_quarantined_tickers()
    candidate_tickers = sorted(universe.get_research_stock_candidates())
    print(f"all_known_stock_tickers={len(all_known)}  quarantined={len(quarantined)}  "
          f"research_candidates(after)={len(candidate_tickers)}", flush=True)

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

    with open(f"{OUT_DIR}/_cache_after.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "candidate_tickers": candidate_tickers,
        }, fh)
    print(f"Pickled to {OUT_DIR}/_cache_after.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
