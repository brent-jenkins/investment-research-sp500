"""Reset-at-boundary comparison (per CLAUDE.md's "Two backtest conventions"
-- this IS a variant comparison: raw-momentum ranking vs. risk-adjusted
ranking, so reset-at-boundary is the correct convention, NOT continuous walk)
at the project's standard train/test split
(analysis.validation.VALIDATION_TRAIN_TEST_SPLIT).

Runs BOTH arms in one process against one identical set of precomputed
ticker caches, so the only difference between them is rank_mode itself.

Usage: python reset_sweep_worker.py <raw|risk_adjusted> <out_json>
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import yaml

import analysis.universe as universe
import analysis.validation as validation_mod
from analysis.backtest import (
    precompute_ticker,
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
)
from data.ingestion import get_price_df
from risk_adjusted_engine import simulate_with_risk_adjusted_ranking

CONFIG_PATH = ROOT / "config.yaml"
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = validation_mod.VALIDATION_TRAIN_TEST_SPLIT


def main():
    mode, out_path = sys.argv[1], sys.argv[2]

    config = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))

    t0 = time.time()
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"candidates: {len(candidate_tickers)}", flush=True)
    caches = [precompute_ticker(t, config) for t in candidate_tickers]
    print(f"precompute done {time.time()-t0:.0f}s", flush=True)

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    stats = {}
    trades = simulate_with_risk_adjusted_ranking(
        caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
        corr_haircut_threshold=None, corr_haircut_factor=1.0,
        block_start=TEST_SPLIT, block_end=END,
        rank_mode=mode, rank_stats=stats,
    )
    metrics = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)

    elapsed = time.time() - t0
    result = {
        "label": mode,
        "rank_mode": mode,
        "window": {"start": TEST_SPLIT.date().isoformat(), "end": END.date().isoformat()},
        "backtest_convention": "reset_at_boundary",
        "starting_capital_gbp": starting_capital,
        "n_trades": len(trades),
        "metrics": metrics,
        "peak_relative_max_drawdown_pct": peak_rel_dd,
        "ranking_fallback_stats": stats,
        "elapsed_sec": elapsed,
        "trades": trades,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{mode}] done {elapsed:.0f}s -- trades={len(trades)} "
          f"cagr={metrics.get('cagr_pct')} dd_fixed={metrics.get('max_drawdown_pct')} "
          f"dd_peak={peak_rel_dd} pf={metrics.get('profit_factor')} "
          f"sharpe={metrics.get('sharpe_ratio')}", flush=True)


if __name__ == "__main__":
    main()
