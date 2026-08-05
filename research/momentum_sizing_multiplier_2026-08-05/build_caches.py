"""Build and pickle the live-universe ticker caches once, for reuse across
every reset-at-boundary run (baseline + every schedule) and block validation
-- avoids reloading/recomputing 500+ tickers' full price history from the DB
on every call, same pattern every sibling investigation tonight used."""
import pickle
import sys
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker, get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

END = pd.Timestamp("2026-08-03")

if __name__ == "__main__":
    t0 = time.time()
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"live candidate pool: {len(candidate_tickers)} tickers", flush=True)

    caches = []
    n_ok = 0
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            n_ok += 1
        caches.append(c)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(candidate_tickers)} ({n_ok} ok) elapsed={time.time()-t0:.0f}s", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    with open(r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05\caches.pkl', "wb") as f:
        pickle.dump({"caches": caches, "gspc_close_full": gspc_close_full, "candidate_tickers": candidate_tickers}, f)

    print(f"done: {n_ok}/{len(candidate_tickers)} tickers cached, elapsed={time.time()-t0:.0f}s", flush=True)
