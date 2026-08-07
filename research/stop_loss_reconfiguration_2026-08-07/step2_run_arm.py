"""Run one arm against one pool's cache. Usage:
python step2_run_arm.py <pool1|pool2> <A|B> <out_label>
Arm A = baseline (disable_stop_loss=False). Arm B = no stop (disable_stop_loss=True).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"
MAX_DEPLOYED_PCT = 0.70  # held fixed across every arm, per pre-registration


def main():
    pool = sys.argv[1]
    arm = sys.argv[2]
    out_label = sys.argv[3]

    t0 = time.time()
    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))

    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    disable_stop = (arm == "B")
    print(f"Loaded cache ({time.time()-t0:.0f}s). Running {pool}/{arm} "
          f"(disable_stop_loss={disable_stop}) -> {out_label}...", flush=True)

    trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        collect_rejections=True, disable_stop_loss=disable_stop,
    )
    # collect_rejections=True changes the return shape in some engines --
    # confirm what this one actually returns before assuming a tuple.
    if isinstance(trades, tuple):
        trades, rejections = trades
        pd.DataFrame(rejections).to_csv(f"{OUT_DIR}/rejections_{out_label}.csv", index=False)
        print(f"  rejections: {len(rejections)}", flush=True)

    df_v = pd.DataFrame(trades)
    ec = df_v["exit_reason"].value_counts()
    print(f"  total_trades={len(df_v)}  distinct_tickers={df_v['ticker'].nunique()}  "
          f"total_pnl_gbp={df_v['pnl_gbp'].sum():.2f}\n  by exit_reason:\n{ec}", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_{out_label}.csv", index=False)
    print(f"Wrote trades_{out_label}.csv ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
