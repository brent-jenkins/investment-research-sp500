"""Build the CORRECTED tickers_cache -- using universe.get_all_known_stock_
tickers() (the full historical universe, 1,205 tickers) instead of
universe.get_live_stock_candidates(as_of=today) (503, survivorship-biased
to only today's members). Point-in-time membership is still correctly
enforced per-day inside simulate_portfolio_compounding_mtm()'s own
_admit_group() via universe.is_member(ticker, dt) -- that was never
broken; only the upstream candidate list was too narrow.
"""
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

OUT_DIR = "research/earnings_dampener_reverify_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


def main():
    t0 = time.time()
    candidate_tickers = sorted(universe.get_all_known_stock_tickers())
    print(f"Corrected candidate pool (full historical universe): {len(candidate_tickers)} tickers", flush=True)

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

    e1 = pd.read_csv("research/earnings_proximity_2026-08-07/earnings_dates.csv", parse_dates=["earnings_date"])
    e2 = pd.read_csv(f"{OUT_DIR}/earnings_dates_additional.csv", parse_dates=["earnings_date"])
    earnings = pd.concat([e1, e2], ignore_index=True).drop_duplicates()
    earnings_by_ticker = {t: sorted(g["earnings_date"].tolist()) for t, g in earnings.groupby("ticker")}
    print(f"Combined earnings-date coverage: {len(earnings_by_ticker)} tickers, {len(earnings)} rows", flush=True)

    with open(f"{OUT_DIR}/_cache.pkl", "wb") as fh:
        pickle.dump({
            "caches": caches, "gspc_close_full": gspc_close_full, "gspc_close": gspc_close,
            "earnings_by_ticker": earnings_by_ticker,
        }, fh)
    print(f"Pickled to {OUT_DIR}/_cache.pkl ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
