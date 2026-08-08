"""Run the four gate-width arms (None/100/50/25) against a given pool.
Usage: python step3_run_arms.py <pool1|pool2_clean> <baseline|top100|top50|top25>
All arms use rebalance_frequency="daily" -- this is NOT a rebalancing test.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-gatewidth-prereg")
sys.path.insert(0, r"c:\GitHub\investment-app-gatewidth-prereg\research\admission_redesign_prereg_2026-08-08")

import pickle
import time

import pandas as pd
import yaml

from redesign_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/gatewidth_prereg_2026-08-08"
MAX_DEPLOYED_PCT = 0.70
SHORTLIST = {"baseline": None, "top100": 100, "top50": 50, "top25": 25}


def main():
    pool = sys.argv[1]
    arm = sys.argv[2]
    assert pool in ("pool1", "pool2_clean")
    assert arm in SHORTLIST

    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    print(f"[{pool}/{arm}] cache loaded ({time.time()-t0:.0f}s, {len(caches)} tickers)", flush=True)

    result = simulate_with_panel(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        disable_stop_loss=False, rebalance_frequency="daily", shortlist_size=SHORTLIST[arm],
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
