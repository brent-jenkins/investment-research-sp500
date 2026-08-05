"""Reset-at-boundary comparison (per CLAUDE.md's "Two backtest conventions"
-- this IS a variant comparison: baseline vs. correlation-haircut variants,
so reset-at-boundary is the correct convention, not continuous walk) of the
correlation-aware sizing haircut against the real, unmodified baseline, at
the project's standard 2020-01-01 train/test split
(analysis.validation.VALIDATION_TRAIN_TEST_SPLIT).

Usage: python reset_sweep_constraint.py <threshold|none> <factor> <label> <out_json>
threshold=none, factor=1.0 reproduces the real baseline exactly (already
fidelity-checked in self_check.py).
"""
import sys
import json
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\concentration_risk_2026-08-05')

import pandas as pd
import yaml

import analysis.universe as universe
import analysis.validation as validation_mod
from analysis.backtest import precompute_ticker, compute_metrics_fixed, compute_peak_relative_drawdown_pct
from data.ingestion import get_price_df
from concentration_constraint_engine import simulate_with_concentration_haircut

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = validation_mod.VALIDATION_TRAIN_TEST_SPLIT


def main():
    thr_arg, factor_arg, label, out_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    threshold = None if thr_arg == "none" else float(thr_arg)
    factor = float(factor_arg)

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

    trades = simulate_with_concentration_haircut(
        caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
        corr_haircut_threshold=threshold, corr_haircut_factor=factor,
        block_start=TEST_SPLIT, block_end=END,
    )
    metrics = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)

    elapsed = time.time() - t0
    result = {
        "label": label, "corr_haircut_threshold": threshold, "corr_haircut_factor": factor,
        "window": {"start": TEST_SPLIT.date().isoformat(), "end": END.date().isoformat()},
        "backtest_convention": "reset_at_boundary",
        "starting_capital_gbp": starting_capital,
        "n_trades": len(trades), "metrics": metrics, "peak_relative_max_drawdown_pct": peak_rel_dd,
        "elapsed_sec": elapsed, "trades": trades,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{label}] done {elapsed:.0f}s -- trades={len(trades)} cagr={metrics.get('cagr_pct')} "
          f"dd_fixed={metrics.get('max_drawdown_pct')} dd_peak={peak_rel_dd} pf={metrics.get('profit_factor')} "
          f"sharpe={metrics.get('sharpe_ratio')}", flush=True)


if __name__ == "__main__":
    main()
