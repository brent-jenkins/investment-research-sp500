"""Run ONE var_confidence/scaling-exponent variant, reset-at-boundary at the
project's standard 2020-01-01 train/test split (analysis.validation.
VALIDATION_TRAIN_TEST_SPLIT) -- i.e. the walk visits ONLY the test period
[2020-01-01, end], starting fresh at starting_capital_gbp with zero open
positions, exactly the "Two backtest conventions" reset-at-boundary
convention CLAUDE.md requires for a variant-comparison question, matching
this project's own established "Part B" sweep precedent (re-entry cooldown,
momentum floor) exactly: block_start/block_end truncation achieves the reset
with no separate cash-reset step needed, since nothing before block_start is
ever visited.

Usage:
    python reset_sweep_worker.py <var_confidence|none> <scaling_exponent> <label> <out_json>

var_confidence=none means "use config's own var_confidence (0.95)" -- used
for the scaling-exponent sweep, which varies scaling_exponent instead.
"""
import sys
import json
import time
import datetime as dt

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml

import analysis.universe as universe
import analysis.validation as validation_mod
from analysis.backtest import precompute_ticker, compute_metrics_fixed
from data.ingestion import get_price_df

sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\var_sizing_sweep_2026-08-05')
from var_sizing_engine import simulate_var_sizing_variant

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")  # pinned for reproducibility, matches local DB's own max date
TEST_SPLIT = validation_mod.VALIDATION_TRAIN_TEST_SPLIT  # 2020-01-01


def main():
    conf_arg, exp_arg, label, out_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    var_confidence_override = None if conf_arg == "none" else float(conf_arg)
    scaling_exponent = float(exp_arg)

    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    t0 = time.time()
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    caches = []
    for ticker in candidate_tickers:
        c = precompute_ticker(ticker, config)
        caches.append(c)
    n_ok = sum(1 for c in caches if c is not None)

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    trades = simulate_var_sizing_variant(
        caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
        var_confidence_override=var_confidence_override,
        scaling_exponent=scaling_exponent,
        block_start=TEST_SPLIT, block_end=END,
    )
    metrics = compute_metrics_fixed(trades, starting_capital)

    elapsed = time.time() - t0
    result = {
        "label": label,
        "var_confidence_override": var_confidence_override,
        "scaling_exponent": scaling_exponent,
        "n_tickers": len(candidate_tickers), "n_tickers_with_data": n_ok,
        "window": {"start": TEST_SPLIT.date().isoformat(), "end": END.date().isoformat()},
        "backtest_convention": "reset_at_boundary",
        "starting_capital_gbp": starting_capital,
        "n_trades": len(trades),
        "metrics": metrics,
        "elapsed_sec": elapsed,
        "trades": trades,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{label}] done in {elapsed:.1f}s -- trades={len(trades)} cagr={metrics.get('cagr_pct')} "
          f"dd={metrics.get('max_drawdown_pct')} pf={metrics.get('profit_factor')} "
          f"sharpe={metrics.get('sharpe_ratio')}", flush=True)


if __name__ == "__main__":
    main()
