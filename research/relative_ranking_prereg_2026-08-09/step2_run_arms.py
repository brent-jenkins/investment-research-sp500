"""Run one arm against one pool.
Usage: python step2_run_arms.py <pool1|pool2_clean> <A|B|C>

A: unmodified simulate_portfolio_compounding_mtm() (production).
B: simulate_relative_ranking(apply_rank_dropped_exit=False, _sort_key="rank").
C: simulate_relative_ranking(apply_rank_dropped_exit=True, _sort_key="rank").
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-relative-ranking")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm
from relative_ranking_engine import simulate_relative_ranking

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/relative_ranking_prereg_2026-08-09"
MAX_DEPLOYED_PCT = 0.70
PER_TRADE_PCT = 0.05


def main():
    pool = sys.argv[1]
    arm = sys.argv[2]
    assert pool in ("pool1", "pool2_clean")
    assert arm in ("A", "B", "C")

    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"[{pool}/{arm}] cache loaded ({time.time()-t0:.0f}s, {len(caches)} tickers)", flush=True)

    if arm == "A":
        trades = simulate_portfolio_compounding_mtm(
            caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT, collect_rejections=False,
        )
        trades_df = pd.DataFrame(trades)
        panel_df = None
    else:
        result = simulate_relative_ranking(
            caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
            apply_rank_dropped_exit=(arm == "C"), _sort_key="rank",
        )
        trades_df = pd.DataFrame(result["trades"])
        panel_df = pd.DataFrame(result["panel"])

    print(f"[{pool}/{arm}] n_trades={len(trades_df)} total_pnl={trades_df['pnl_gbp'].sum():.2f} "
          + (f"mean_deployed_pct={panel_df['current_deployed_pct'].mean():.2f} "
             f"mean_n_open_positions={panel_df['n_open_positions'].mean():.2f} " if panel_df is not None else "")
          + f"({time.time()-t0:.0f}s total)", flush=True)

    trades_df.to_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", index=False)
    if panel_df is not None:
        panel_df.to_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv", index=False)
    print(f"[{pool}/{arm}] wrote trades{'/panel' if panel_df is not None else ''}_{pool}_{arm}.csv", flush=True)


if __name__ == "__main__":
    main()
