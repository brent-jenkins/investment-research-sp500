"""Reset-at-boundary comparison (per CLAUDE.md's "Two backtest conventions"
-- this IS a variant comparison: baseline (real, unmodified engine) vs. the
stop-cluster-coordination variant, so reset-at-boundary is the correct
convention, not continuous walk) at the project's standard 2020-01-01
train/test split (analysis.validation.VALIDATION_TRAIN_TEST_SPLIT).

Usage: python reset_sweep.py <W|none> <N|none> <label> <out_json>
W=none, N=none reproduces the real baseline exactly (already fidelity-
checked in self_check.py).
"""
import sys
import json
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\stop_cluster_coordination_2026-08-05')

import pandas as pd
import yaml

import analysis.universe as universe
import analysis.validation as validation_mod
from analysis.backtest import precompute_ticker, compute_metrics_fixed, compute_peak_relative_drawdown_pct
from data.ingestion import get_price_df
from stop_cluster_engine import simulate_with_stop_cluster_coordination

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = validation_mod.VALIDATION_TRAIN_TEST_SPLIT


def main():
    w_arg, n_arg, label, out_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    W = None if w_arg == "none" else int(w_arg)
    N = None if n_arg == "none" else int(n_arg)

    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    t0 = time.time()
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    caches = [precompute_ticker(t, config) for t in candidate_tickers]

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    trades = simulate_with_stop_cluster_coordination(
        caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
        cluster_window_days=W, cluster_min_stops=N,
        block_start=TEST_SPLIT, block_end=END,
    )
    metrics = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    n_coord = sum(1 for t in trades if t["exit_reason"] == "STOP_CLUSTER_COORDINATED")
    n_stop = sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS")

    elapsed = time.time() - t0
    result = {
        "label": label, "cluster_window_days": W, "cluster_min_stops": N,
        "window": {"start": TEST_SPLIT.date().isoformat(), "end": END.date().isoformat()},
        "backtest_convention": "reset_at_boundary",
        "starting_capital_gbp": starting_capital,
        "n_trades": len(trades), "n_stop_loss": n_stop, "n_coordinated": n_coord,
        "metrics": metrics, "peak_relative_max_drawdown_pct": peak_rel_dd,
        "elapsed_sec": elapsed, "trades": trades,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{label}] done {elapsed:.0f}s -- trades={len(trades)} (stop_loss={n_stop} coordinated={n_coord}) "
          f"cagr={metrics.get('cagr_pct')} dd_fixed={metrics.get('max_drawdown_pct')} dd_peak={peak_rel_dd} "
          f"pf={metrics.get('profit_factor')} sharpe={metrics.get('sharpe_ratio')}", flush=True)


if __name__ == "__main__":
    main()
