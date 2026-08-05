"""16-block independent validation of a stop-cluster-coordination variant,
using the REAL, exact formula -- analysis.backtest.make_blocks() and
analysis.validation._block_passes() imported directly, not re-derived (per
CLAUDE.md's "Research reproducibility standard": exact code, not prose).
Each block is a genuinely independent reset (fresh starting_capital, zero
open positions, truncated to [block_start, block_end] with a corrected
END_OF_DATA) -- not a bucketing of one continuous walk. Same pattern as
research/concentration_risk_2026-08-05/block_validate_constraint.py.

Usage: python block_validate.py <W|none> <N|none> <label> <out_json>
"""
import sys
import json
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\stop_cluster_coordination_2026-08-05')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker, compute_metrics_fixed, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series, VALIDATION_START
from data.ingestion import get_price_df
from stop_cluster_engine import simulate_with_stop_cluster_coordination

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")


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

    blocks = make_blocks(VALIDATION_START, END, n_blocks=16)

    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks):
        trades = simulate_with_stop_cluster_coordination(
            caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
            cluster_window_days=W, cluster_min_stops=N,
            block_start=b_start, block_end=b_end,
        )
        strat_m = compute_metrics_fixed(trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        passed = _block_passes(strat_m, sp_dd)
        if passed:
            n_pass += 1
        n_coord = sum(1 for t in trades if t["exit_reason"] == "STOP_CLUSTER_COORDINATED")
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(trades), "n_coordinated": n_coord,
            "strat_cagr": strat_m["cagr_pct"], "strat_dd": strat_m["max_drawdown_pct"],
            "strat_pf": strat_m.get("profit_factor", 0.0), "strat_sharpe": strat_m.get("sharpe_ratio", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": passed,
        })
        print(f"  [{label}] block {i}: trades={len(trades)} (coord={n_coord}) cagr={strat_m['cagr_pct']} "
              f"dd={strat_m['max_drawdown_pct']} pf={strat_m.get('profit_factor')} pass={passed}", flush=True)

    elapsed = time.time() - t0
    result = {
        "label": label, "cluster_window_days": W, "cluster_min_stops": N,
        "n_blocks_pass": n_pass, "n_blocks_total": len(blocks), "blocks": block_results,
        "elapsed_sec": elapsed,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{label}] BLOCK VALIDATION done {elapsed:.0f}s -- {n_pass}/{len(blocks)} pass", flush=True)


if __name__ == "__main__":
    main()
