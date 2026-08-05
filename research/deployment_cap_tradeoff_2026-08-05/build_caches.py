"""Deployment-cap/per-trade-pct trade-off investigation -- Step 1: precompute
every live candidate ticker's FULL price history + ATR/momentum/signal
(analysis.backtest.precompute_ticker(), the real, unmodified production
function -- not re-derived) exactly once, and pickle the result so every
grid cell (9 combinations) and every block (16 per combination = 144 block
runs) can cheaply analysis.backtest.slice_ticker() the same cached frame
instead of re-loading/re-computing ATR+momentum from the DB on every single
one of the ~150 backtest calls this investigation needs. This mirrors
research/var_sizing_sweep_2026-08-05/block_validate_worker.py's own
"precompute once, slice per block" pattern.

Also pickles the raw ^GSPC close series (needed by every engine call for
crisis-trigger computation) and the live candidate ticker list itself.
"""
import pickle
import sys
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker
from data.ingestion import get_price_df

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\deployment_cap_tradeoff_2026-08-05'


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    log(f"{len(candidate_tickers)} live candidate tickers as of {END.date()}")

    t0 = time.time()
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, config)
        caches.append(c)
        if (i + 1) % 100 == 0:
            log(f"  precomputed {i+1}/{len(candidate_tickers)} ({time.time()-t0:.0f}s)")
    n_ok = sum(1 for c in caches if c is not None)
    log(f"Precompute done: {n_ok}/{len(candidate_tickers)} usable, {time.time()-t0:.0f}s total")

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    with open(OUT_DIR + r'\caches.pkl', 'wb') as f:
        pickle.dump({
            "candidate_tickers": candidate_tickers,
            "caches": caches,
            "gspc_close_full": gspc_close_full,
            "end": END,
        }, f, protocol=pickle.HIGHEST_PROTOCOL)
    log(f"Saved caches.pkl ({n_ok} usable tickers)")


if __name__ == "__main__":
    main()
