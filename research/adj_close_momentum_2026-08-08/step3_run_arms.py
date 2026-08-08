"""Run Arm A (close) and Arm B (adj_close) against one pool's dual cache.
Usage: python step3_run_arms.py <pool1|pool2_clean|pool2_all>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

sys.path.insert(0, r"c:\GitHub\investment-app-revalidation\research\adj_close_momentum_2026-08-08")
from step2_build_dual_cache import derive_arm_frame

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/adj_close_momentum_2026-08-08"
MAX_DEPLOYED_PCT = 0.70


def main():
    pool = sys.argv[1]
    t0 = time.time()

    with open(f"{OUT_DIR}/_cache_{pool}_dual.pkl", "rb") as fh:
        data = pickle.load(fh)
    dual_caches = data["caches"]

    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))

    from data.ingestion import get_price_df
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    START = pd.Timestamp("2007-01-01")
    END = pd.Timestamp("2026-08-06")
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    for arm in ["A", "B"]:
        arm_caches = [derive_arm_frame(c, arm) for c in dual_caches]
        print(f"Running {pool}/{arm} ({time.time()-t0:.0f}s elapsed so far)...", flush=True)
        trades, rejections = simulate_portfolio_compounding_mtm(
            arm_caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
            collect_rejections=True,
        )
        pd.DataFrame(rejections).to_csv(f"{OUT_DIR}/rejections_{pool}_{arm}.csv", index=False)
        df_v = pd.DataFrame(trades)
        ec = df_v["exit_reason"].value_counts()
        print(f"  [{pool}_{arm}] total_trades={len(df_v)} distinct_tickers={df_v['ticker'].nunique()} "
              f"total_pnl_gbp={df_v['pnl_gbp'].sum():.2f}\n  by exit_reason:\n{ec}", flush=True)
        df_v.to_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", index=False)
        print(f"  Wrote trades_{pool}_{arm}.csv ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
