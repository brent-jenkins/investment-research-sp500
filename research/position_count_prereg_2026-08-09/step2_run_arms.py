"""Run the four position-count arms (5/10/22/40) against a given pool.
Usage: python step2_run_arms.py <pool1|pool2_clean> <n5|n10|n22|n40>

per_trade_pct derivation (Sec 1 of the pre-registration): max_total_deployed
(0.70) / N, except n22 (baseline) which keeps today's actual, unchanged
config value (0.05) rather than a value re-derived from 22 -- 22 is the
*realized* current outcome of 0.05, not a chosen target.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from position_count_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/position_count_prereg_2026-08-09"
MAX_DEPLOYED_PCT = 0.70
PER_TRADE_PCT = {
    "n5": 0.70 / 5,     # 0.14
    "n10": 0.70 / 10,   # 0.07
    "n22": 0.05,        # baseline, unchanged production value
    "n40": 0.70 / 40,   # 0.0175
}


def main():
    pool = sys.argv[1]
    arm = sys.argv[2]
    assert pool in ("pool1", "pool2_clean")
    assert arm in PER_TRADE_PCT

    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    per_trade_pct = PER_TRADE_PCT[arm]
    print(f"[{pool}/{arm}] cache loaded ({time.time()-t0:.0f}s, {len(caches)} tickers), "
          f"per_trade_pct={per_trade_pct}", flush=True)

    result = simulate_with_panel(caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT)
    trades_df = pd.DataFrame(result["trades"])
    panel_df = pd.DataFrame(result["panel"])
    rejections_df = pd.DataFrame(result["rejections"])

    n_subminimum = int((rejections_df["reason"] == "REJECTED_SUBMINIMUM").sum()) if len(rejections_df) else 0
    n_capacity = int((rejections_df["reason"] == "REJECTED_CAPACITY").sum()) if len(rejections_df) else 0

    print(f"[{pool}/{arm}] n_trades={len(trades_df)} total_pnl={trades_df['pnl_gbp'].sum():.2f} "
          f"mean_deployed_pct={panel_df['current_deployed_pct'].mean():.2f} "
          f"mean_n_open_positions={panel_df['n_open_positions'].mean():.2f} "
          f"n_rejected_subminimum={n_subminimum} n_rejected_capacity={n_capacity} "
          f"({time.time()-t0:.0f}s total)", flush=True)

    trades_df.to_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", index=False)
    panel_df.to_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv", index=False)
    rejections_df.to_csv(f"{OUT_DIR}/rejections_{pool}_{arm}.csv", index=False)
    print(f"[{pool}/{arm}] wrote trades/panel/rejections_{pool}_{arm}.csv", flush=True)


if __name__ == "__main__":
    main()
