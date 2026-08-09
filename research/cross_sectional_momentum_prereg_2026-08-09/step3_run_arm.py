"""Run one (arm, pool) combination of the cross-sectional engine and write
trades/panel CSVs. CLI: python step3_run_arm.py <arm:B|C|D> <pool:pool1|pool2_clean>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg\research\cross_sectional_momentum_prereg_2026-08-09")

import pickle
import time

import pandas as pd
import yaml

from cross_sectional_engine import simulate_cross_sectional_momentum

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
PER_TRADE_PCT = 0.05
MAX_DEPLOYED_PCT = 0.70
TOP_N = 40

ARM_PARAMS = {
    "B": dict(max_holding_days=30, apply_momentum_floor=False),
    "C": dict(max_holding_days=None, apply_momentum_floor=False),
    "D": dict(max_holding_days=None, apply_momentum_floor=True),
}


def main():
    arm = sys.argv[1]
    pool = sys.argv[2]
    assert arm in ARM_PARAMS, f"unknown arm {arm}"
    assert pool in ("pool1", "pool2_clean"), f"unknown pool {pool}"

    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = [c for c in data["caches"] if c is not None]

    t0 = time.time()
    print(f"[{arm}/{pool}] starting, {len(caches)} tickers", flush=True)
    result = simulate_cross_sectional_momentum(
        caches, CONFIG, data["gspc_close"], PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        top_n=TOP_N, **ARM_PARAMS[arm],
    )
    trades = result["trades"]
    panel = result["panel"]
    total_pnl = sum(t["pnl_gbp"] for t in trades)
    print(f"[{arm}/{pool}] done: {len(trades)} trades, total_pnl={total_pnl:.2f} ({time.time()-t0:.0f}s)", flush=True)

    pd.DataFrame(trades).to_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", index=False)
    pd.DataFrame(panel).to_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv", index=False)
    pd.DataFrame(result["pre_entry_balances"]).to_csv(f"{OUT_DIR}/preentry_{pool}_{arm}.csv", index=False)
    print(f"[{arm}/{pool}] wrote trades_{pool}_{arm}.csv, panel_{pool}_{arm}.csv, preentry_{pool}_{arm}.csv", flush=True)


if __name__ == "__main__":
    main()
