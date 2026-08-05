"""Fidelity check (required before trusting any grid cell, per this
investigation's brief): confirm grid_worker.py's cached-slice call to the
REAL simulate_portfolio_compounding_mtm() reproduces analysis.validation.
run_full_validation() -- the actual production entrypoint the Backtest page
and dd_report use -- byte-identical at the CURRENT production config
(max_total_deployed=0.5, per_trade_pct=0.05), reset-at-boundary test period
[2020-01-01, 2026-08-03], live universe, mark-to-market.

Two independent code paths are compared:
  A) analysis.validation.run_full_validation(config, start=2020-01-01,
     end=2026-08-03, per_trade_pct=0.05, max_deployed_pct_compounding=0.5,
     mark_to_market=True, live_universe=True) -- the real, unmodified
     production function, its own fresh precompute_ticker() pass.
  B) grid_worker.py's run_window() using the pre-built cache
     (build_caches.py's precompute_ticker() output, sliced via the real
     slice_ticker()) -- what every grid cell / block in this investigation
     actually uses for speed.

If A and B produce the same trade count and the same total pnl_gbp (and,
ideally, byte-identical trade lists), the cache-based approach is confirmed
faithful to the real production entrypoint and every other combination in
the grid can be trusted.
"""
import sys
import json
import pickle
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml

import analysis.validation as validation_mod
from analysis.backtest import slice_ticker, simulate_portfolio_compounding_mtm, compute_metrics_fixed

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
CACHE_PATH = r'c:\GitHub\investment-research-sp500\research\deployment_cap_tradeoff_2026-08-05\caches.pkl'
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = pd.Timestamp("2020-01-01")


def main():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    t0 = time.time()
    print("Running path A: run_full_validation() (real production entrypoint)...", flush=True)
    result_a = validation_mod.run_full_validation(
        config, start=TEST_SPLIT, end=END,
        per_trade_pct=0.05, max_deployed_pct_compounding=0.5,
        mark_to_market=True, live_universe=True,
    )
    trades_a = result_a["trades"]
    print(f"  A: {len(trades_a)} trades, {time.time()-t0:.0f}s", flush=True)

    t1 = time.time()
    print("Running path B: cached-slice + direct simulate_portfolio_compounding_mtm() call...", flush=True)
    with open(CACHE_PATH, 'rb') as f:
        cache_bundle = pickle.load(f)
    caches = cache_bundle["caches"]
    gspc_close_full = cache_bundle["gspc_close_full"]
    sliced = [slice_ticker(c, TEST_SPLIT, END) for c in caches]
    gspc_slice = gspc_close_full[(gspc_close_full.index >= TEST_SPLIT) & (gspc_close_full.index <= END)]
    trades_b = simulate_portfolio_compounding_mtm(sliced, config, gspc_slice, 0.05, 0.5)
    print(f"  B: {len(trades_b)} trades, {time.time()-t1:.0f}s", flush=True)

    # Normalize for comparison (dates -> isoformat strings, floats rounded)
    def norm(trades):
        out = []
        for t in trades:
            d = dict(t)
            d["entry_date"] = str(d["entry_date"])
            d["exit_date"] = str(d["exit_date"])
            out.append(d)
        return sorted(out, key=lambda x: (x["ticker"], x["entry_date"]))

    na, nb = norm(trades_a), norm(trades_b)
    byte_identical = (na == nb)
    sum_pnl_a = sum(t["pnl_gbp"] for t in trades_a)
    sum_pnl_b = sum(t["pnl_gbp"] for t in trades_b)

    metrics_a = compute_metrics_fixed(trades_a, 50000.0)
    metrics_b = compute_metrics_fixed(trades_b, 50000.0)

    report = {
        "n_trades_a": len(trades_a), "n_trades_b": len(trades_b),
        "byte_identical": byte_identical,
        "sum_pnl_gbp_a": sum_pnl_a, "sum_pnl_gbp_b": sum_pnl_b,
        "metrics_a": metrics_a, "metrics_b": metrics_b,
    }
    print(json.dumps(report, indent=2, default=str), flush=True)

    out_path = r'c:\GitHub\investment-research-sp500\research\deployment_cap_tradeoff_2026-08-05\fidelity_check_result.json'
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Saved {out_path}", flush=True)

    if not byte_identical:
        print("MISMATCH -- inspecting first few differing trades...", flush=True)
        for i, (ra, rb) in enumerate(zip(na, nb)):
            if ra != rb:
                print(f"  #{i}: A={ra}\n       B={rb}", flush=True)
                if i > 5:
                    break


if __name__ == "__main__":
    main()
