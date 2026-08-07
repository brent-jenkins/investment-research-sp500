"""Run the baseline (no earnings dampener, no other new mechanism -- out
of scope per the remediation task) variant from the pickled AFTER cache
and save its trades CSV. Usage: python step3_run_variant_after.py <cap> <out_label>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-data-quality")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/data_quality_remediation_2026-08-07"


def main():
    cap = float(sys.argv[1])
    out_label = sys.argv[2]

    t0 = time.time()
    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))

    with open(f"{OUT_DIR}/_cache_after.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"Loaded cache ({time.time()-t0:.0f}s). Running AFTER baseline cap={cap} -> {out_label}...", flush=True)

    trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, cap,
    )
    df_v = pd.DataFrame(trades)
    ec = df_v["exit_reason"].value_counts()
    print(f"  total_trades={len(df_v)}  distinct_tickers={df_v['ticker'].nunique()}  "
          f"total_pnl_gbp={df_v['pnl_gbp'].sum():.2f}\n  by exit_reason:\n{ec}", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_{out_label}.csv", index=False)
    print(f"Wrote trades_{out_label}.csv ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
