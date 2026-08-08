"""Re-run Arms A, B, C1, C2 against the rebuilt Pool 2 cache (current,
163-quarantined pool) -- closes the staleness gap: the original Pool 2 arm
runs used the pre-Wikipedia-refinement 162-quarantined pool (confirmed via
byte-identical diff between trades_pool2_A.csv and the phase2 162-quarantined
baseline). Usage: python step9_run_arm_final.py <A|B|C1|C2>
"""
import copy
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

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
    arm = sys.argv[1]
    out_label = f"pool2_final_{arm}"

    t0 = time.time()
    config = copy.deepcopy(BASE_CONFIG)
    disable_stop = (arm == "B")
    if arm == "C1":
        config.setdefault("backtest", {})["atr_stop_multiplier"] = 3.0
    elif arm == "C2":
        config.setdefault("backtest", {})["atr_stop_multiplier"] = 4.0

    t212_cfg = config.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))

    with open(f"{OUT_DIR}/_cache_pool2_final.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"Loaded cache ({time.time()-t0:.0f}s). Running pool2_final/{arm} "
          f"(disable_stop_loss={disable_stop}, atr_mult={config.get('backtest', {}).get('atr_stop_multiplier', 'default')}) "
          f"-> {out_label}...", flush=True)

    trades, rejections = simulate_portfolio_compounding_mtm(
        caches, config, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        collect_rejections=True, disable_stop_loss=disable_stop,
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
