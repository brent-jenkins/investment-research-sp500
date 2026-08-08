"""Run one perturbed variant of the standard baseline (get_research_stock_
candidates(), cap 0.70, simulate_portfolio_compounding_mtm()). Usage:
  python step2_run_perturbation.py <run_id> <perturbation_type> <param>

perturbation_type:
  baseline          -- no perturbation (param ignored)
  drop_ticker       -- param = ticker symbol to drop
  shift_start       -- param = integer days to shift START forward
  shuffle_tiebreak  -- param = integer random seed for shuffling input order
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pickle
import random
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm, slice_ticker
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/measurement_toolkit_2026-08-08"
BASE_START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")
MAX_DEPLOYED_PCT = 0.70


def main():
    run_id = sys.argv[1]
    ptype = sys.argv[2]
    param = sys.argv[3] if len(sys.argv) > 3 else None

    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_full_history.pkl", "rb") as fh:
        data = pickle.load(fh)
    full_caches = data["caches"]
    tickers = list(data["candidate_tickers"])

    start = BASE_START
    if ptype == "drop_ticker":
        tickers = [t for t in tickers if t != param]
    elif ptype == "shift_start":
        start = BASE_START + pd.Timedelta(days=int(param))
    elif ptype == "shuffle_tiebreak":
        rng = random.Random(int(param))
        rng.shuffle(tickers)
    elif ptype != "baseline":
        raise ValueError(f"unknown perturbation type {ptype!r}")

    caches = []
    for t in tickers:
        c = full_caches.get(t)
        if c is not None:
            c = slice_ticker(c, start, END)
        caches.append(c)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= END)]

    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    trades = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT)
    df_v = pd.DataFrame(trades)
    print(f"[{run_id}] ptype={ptype} param={param} n_usable={sum(1 for c in caches if c is not None)} "
          f"total_trades={len(df_v)} total_pnl_gbp={df_v['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_{run_id}.csv", index=False)
    with open(f"{OUT_DIR}/meta_{run_id}.txt", "w") as fh:
        fh.write(f"{run_id}\t{ptype}\t{param}\t{start.date().isoformat()}\t{len(tickers)}\n")


if __name__ == "__main__":
    main()
