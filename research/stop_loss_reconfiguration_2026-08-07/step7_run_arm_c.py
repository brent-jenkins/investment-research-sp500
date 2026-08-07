"""Run Arm C (wider fixed ATR multiplier) against one pool's cache. Usage:
python step7_run_arm_c.py <pool1|pool2> <3.0|4.0> <out_label>
Reuses the existing pool caches -- atr_stop_multiplier is read fresh from
config at simulation time by _resolve_entry_stop(), never baked into
precompute_ticker()'s cached frames.
"""
import copy
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

with open("config.yaml", encoding="utf-8") as f:
    BASE_CONFIG = yaml.safe_load(f)

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"
MAX_DEPLOYED_PCT = 0.70


def main():
    pool = sys.argv[1]
    multiplier = float(sys.argv[2])
    out_label = sys.argv[3]

    t0 = time.time()
    config = copy.deepcopy(BASE_CONFIG)
    config.setdefault("backtest", {})["atr_stop_multiplier"] = multiplier

    t212_cfg = config.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))

    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"Loaded cache ({time.time()-t0:.0f}s). Running {pool}/C(atr_mult={multiplier}) "
          f"-> {out_label}...", flush=True)

    trades, rejections = simulate_portfolio_compounding_mtm(
        caches, config, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT, collect_rejections=True,
    )
    pd.DataFrame(rejections).to_csv(f"{OUT_DIR}/rejections_{out_label}.csv", index=False)

    df_v = pd.DataFrame(trades)
    ec = df_v["exit_reason"].value_counts()
    print(f"  total_trades={len(df_v)}  distinct_tickers={df_v['ticker'].nunique()}  "
          f"total_pnl_gbp={df_v['pnl_gbp'].sum():.2f}\n  by exit_reason:\n{ec}", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_{out_label}.csv", index=False)
    print(f"Wrote trades_{out_label}.csv ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
