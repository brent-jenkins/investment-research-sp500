"""Run baseline and redesign arms against a given pool cache.
Usage: python step3_run_arms.py <pool1|pool2_clean> <baseline|redesign>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-admission-redesign")

import pickle
import time

import pandas as pd
import yaml

from redesign_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/admission_redesign_prereg_2026-08-08"
MAX_DEPLOYED_PCT = 0.70
CACHE_FILES = {"pool1": "_cache_pool1.pkl", "pool2_clean": "_cache_pool2_clean.pkl"}
SHORTLIST_SIZE = 50


def main():
    pool = sys.argv[1]
    arm = sys.argv[2]
    assert pool in CACHE_FILES
    assert arm in ("baseline", "redesign")

    t0 = time.time()
    with open(f"{OUT_DIR}/{CACHE_FILES[pool]}", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    print(f"[{pool}/{arm}] cache loaded ({time.time()-t0:.0f}s, {len(caches)} tickers)", flush=True)

    if arm == "baseline":
        kwargs = dict(rebalance_frequency="daily", shortlist_size=None)
    else:
        kwargs = dict(rebalance_frequency="monthly", shortlist_size=SHORTLIST_SIZE)

    result = simulate_with_panel(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        disable_stop_loss=False, **kwargs,
    )
    trades_df = pd.DataFrame(result["trades"])
    panel_df = pd.DataFrame(result["panel"])

    print(f"[{pool}/{arm}] n_trades={len(trades_df)} total_pnl={trades_df['pnl_gbp'].sum():.2f} "
          f"mean_deployed_pct={panel_df['current_deployed_pct'].mean():.2f} ({time.time()-t0:.0f}s total)", flush=True)

    trades_df.to_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", index=False)
    panel_df.to_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv", index=False)
    print(f"[{pool}/{arm}] wrote trades_{pool}_{arm}.csv, panel_{pool}_{arm}.csv", flush=True)


if __name__ == "__main__":
    main()
