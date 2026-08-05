"""Deployment-cap/per-trade-pct trade-off investigation -- run ONE
(max_total_deployed, per_trade_pct) grid combination through:

  (a) a RESET-AT-BOUNDARY primary sweep over the standard test period
      [2020-01-01, 2026-08-03] (analysis.validation.VALIDATION_TRAIN_TEST_SPLIT
      to the local DB's own max date) -- a variant comparison, per CLAUDE.md's
      "Two backtest conventions" section, not a track-record report.

  (b) the mandatory 16-block independent validation
      (analysis.backtest.make_blocks(2007-01-01, 2026-08-03, 16) +
      analysis.validation._block_passes(), both imported directly, never
      reimplemented) -- each block is its own genuine reset (fresh
      starting_capital, zero open positions, truncated to [block_start,
      block_end] with a corrected END_OF_DATA), NOT a bucketing of one
      continuous walk.

This calls analysis.backtest.simulate_portfolio_compounding_mtm() DIRECTLY
-- the real, unmodified, current production function -- with per_trade_pct/
max_deployed_pct passed straight through as its own native parameters (no
custom engine, no monkeypatch, no hand-rolled approximation of the
admission/capacity logic; see CLAUDE.md's "Research scripts must verify
decision-engine parity" -- the risk that rule warns about is exactly
avoided here because these two values are already first-class parameters
of the real function). Ticker frames are precomputed ONCE (build_caches.py,
via the real analysis.backtest.precompute_ticker()) and reused across every
grid cell and every block via analysis.backtest.slice_ticker() (also the
real, unmodified function) -- purely a performance optimization (avoids
144 redundant DB reads / ATR+momentum recomputations per grid cell), not a
change to any decision logic.

Usage:
    python grid_worker.py <max_deployed_pct> <per_trade_pct> <label> <out_json>
"""
import sys
import json
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml
import pickle

from analysis.backtest import (
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    make_blocks,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from analysis.validation import _block_passes, _cagr_dd_for_series, VALIDATION_START

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
CACHE_PATH = r'c:\GitHub\investment-research-sp500\research\deployment_cap_tradeoff_2026-08-05\caches.pkl'
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = pd.Timestamp("2020-01-01")  # analysis.validation.VALIDATION_TRAIN_TEST_SPLIT


def log(msg, label):
    print(f"[{time.strftime('%H:%M:%S')}] [{label}] {msg}", flush=True)


def run_window(caches, gspc_close_full, config, per_trade_pct, max_deployed_pct, w_start, w_end):
    """One genuine reset -- slice every cached ticker frame + gspc_close to
    [w_start, w_end] and call the REAL simulate_portfolio_compounding_mtm()
    fresh (starting_capital, zero open positions -- nothing outside
    [w_start, w_end] is ever visited by the event walk)."""
    sliced = [slice_ticker(c, w_start, w_end) for c in caches]
    gspc_slice = gspc_close_full[(gspc_close_full.index >= w_start) & (gspc_close_full.index <= w_end)]
    trades = simulate_portfolio_compounding_mtm(
        sliced, config, gspc_slice, per_trade_pct, max_deployed_pct,
    )
    return trades


def main():
    max_deployed_pct, per_trade_pct, label, out_path = (
        float(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4]
    )

    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    with open(CACHE_PATH, 'rb') as f:
        cache_bundle = pickle.load(f)
    caches = cache_bundle["caches"]
    gspc_close_full = cache_bundle["gspc_close_full"]

    t0 = time.time()
    log(f"START max_deployed_pct={max_deployed_pct} per_trade_pct={per_trade_pct}", label)

    # ---- (a) primary reset-at-boundary sweep, test period -----------------
    primary_trades = run_window(
        caches, gspc_close_full, config, per_trade_pct, max_deployed_pct, TEST_SPLIT, END,
    )
    primary_metrics = compute_metrics_fixed(primary_trades, starting_capital)
    primary_peak_rel_dd = compute_peak_relative_drawdown_pct(primary_trades, starting_capital)
    log(f"primary sweep done ({time.time()-t0:.0f}s): trades={len(primary_trades)} "
        f"cagr={primary_metrics.get('cagr_pct')} dd_fixed={primary_metrics.get('max_drawdown_pct')} "
        f"dd_peak={primary_peak_rel_dd} pf={primary_metrics.get('profit_factor')} "
        f"sharpe={primary_metrics.get('sharpe_ratio')}", label)

    # ---- (b) 16-block independent validation -------------------------------
    blocks = make_blocks(VALIDATION_START, END, n_blocks=16)
    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks):
        bt0 = time.time()
        block_trades = run_window(
            caches, gspc_close_full, config, per_trade_pct, max_deployed_pct, b_start, b_end,
        )
        strat_m = compute_metrics_fixed(block_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        passed = _block_passes(strat_m, sp_dd)
        if passed:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(block_trades),
            "strat_cagr": strat_m["cagr_pct"], "strat_dd": strat_m["max_drawdown_pct"],
            "strat_pf": strat_m.get("profit_factor", 0.0),
            "strat_sharpe": strat_m.get("sharpe_ratio", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": passed,
        })
        log(f"  block {i} ({b_start.date()}..{b_end.date()}) done in {time.time()-bt0:.0f}s: "
            f"trades={len(block_trades)} cagr={strat_m['cagr_pct']} dd={strat_m['max_drawdown_pct']} "
            f"pf={strat_m.get('profit_factor')} pass={passed}", label)

    cagrs = [b["strat_cagr"] for b in block_results]
    import statistics
    mean_cagr = statistics.mean(cagrs)
    median_cagr = statistics.median(cagrs)

    elapsed = time.time() - t0
    result = {
        "label": label,
        "max_deployed_pct": max_deployed_pct,
        "per_trade_pct": per_trade_pct,
        "backtest_convention_primary": "reset_at_boundary",
        "window_primary": {"start": TEST_SPLIT.date().isoformat(), "end": END.date().isoformat()},
        "starting_capital_gbp": starting_capital,
        "primary": {
            "n_trades": len(primary_trades),
            "metrics": primary_metrics,
            "peak_relative_drawdown_pct": primary_peak_rel_dd,
        },
        "blocks": block_results,
        "n_blocks_pass": n_pass,
        "n_blocks_total": len(blocks),
        "mean_block_cagr": mean_cagr,
        "median_block_cagr": median_cagr,
        "elapsed_sec": elapsed,
        "primary_trades": primary_trades,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    log(f"DONE in {elapsed:.0f}s -- primary: trades={len(primary_trades)} cagr={primary_metrics.get('cagr_pct')} "
        f"pf={primary_metrics.get('profit_factor')} | blocks: {n_pass}/{len(blocks)} pass, "
        f"mean_cagr={mean_cagr:.2f} median_cagr={median_cagr:.2f}", label)


if __name__ == "__main__":
    main()
