"""Step 5 -- 16-block independent validation, run ONLY for schedules that
showed real promise in the single reset-at-boundary comparison (Step 3),
per the task's own instruction and the repeated lesson from tonight's
sibling investigations. make_blocks() and _block_passes() imported
DIRECTLY from analysis.backtest / analysis.validation -- never reimplemented.

Each of the 16 blocks is a genuine independent reset (fresh £50,000, zero
open positions, truncated [block_start, block_end]) -- not a bucketing of
one continuous walk.
"""
import sys
import json
import pickle
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05')

import pandas as pd
import yaml

from analysis.backtest import (
    slice_ticker, simulate_portfolio_compounding_mtm, make_blocks, compute_metrics_fixed,
)
from analysis.validation import _block_passes, _cagr_dd_for_series
from multiplier_engine import simulate_portfolio_compounding_mtm_multiplier

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

RESEARCH_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05'
STARTING_CAPITAL = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 50000))
PER_TRADE_PCT = float(CONFIG["trading212_invest"]["per_trade_pct"])
MAX_DEPLOYED_PCT = float(CONFIG["trading212_invest"]["max_total_deployed"])

SCHEDULE_MODEST     = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.15, "Very Strong": 1.3}
SCHEDULE_AGGRESSIVE = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.3,  "Very Strong": 1.6}
SCHEDULE_WHOLE_DIST = {"Weak": 1.0, "Moderate": 1.1, "Strong": 1.2,  "Very Strong": 1.35}
SCHEDULES = {
    "baseline": None, "modest": SCHEDULE_MODEST,
    "aggressive": SCHEDULE_AGGRESSIVE, "whole_dist": SCHEDULE_WHOLE_DIST,
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_block(arm, schedule, caches, gspc_close_full, b_start, b_end):
    caches_sliced = [slice_ticker(c, b_start, b_end) if c is not None else None for c in caches]
    gspc_close = gspc_close_full[(gspc_close_full.index >= b_start) & (gspc_close_full.index <= b_end)]
    if arm == "baseline":
        trades = simulate_portfolio_compounding_mtm(
            caches_sliced, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        )
    else:
        trades = simulate_portfolio_compounding_mtm_multiplier(
            caches_sliced, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
            band_multiplier=schedule, collect_stats=False,
        )
    metrics = compute_metrics_fixed(trades, STARTING_CAPITAL)
    return trades, metrics


def main(arms_to_validate):
    log(f"Validating arms: {arms_to_validate}")
    with open(f"{RESEARCH_DIR}\\caches.pkl", "rb") as f:
        data = pickle.load(f)
    caches, gspc_close_full = data["caches"], data["gspc_close_full"]

    blocks = make_blocks(pd.Timestamp("2007-01-01"), pd.Timestamp("2026-08-03"), 16)
    log(f"{len(blocks)} blocks: {[(str(b[0].date()), str(b[1].date())) for b in blocks]}")

    all_results = {}
    for arm in arms_to_validate:
        schedule = SCHEDULES[arm]
        block_rows = []
        block_trades = {}
        for bi, (b_start, b_end) in enumerate(blocks):
            t0 = time.time()
            sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
            trades, metrics = run_block(arm, schedule, caches, gspc_close_full, b_start, b_end)
            passed = _block_passes(metrics, sp_dd)
            elapsed = time.time() - t0
            log(f"  {arm} block {bi} [{b_start.date()}..{b_end.date()}]: "
                f"{metrics.get('total_trades', 0)} trades, CAGR={metrics.get('cagr_pct')}, "
                f"DD={metrics.get('max_drawdown_pct')}, PF={metrics.get('profit_factor')}, "
                f"sp_dd={sp_dd}, PASS={passed} ({elapsed:.0f}s)")
            block_rows.append({
                "block": bi, "start": str(b_start.date()), "end": str(b_end.date()),
                "sp_cagr": sp_cagr, "sp_dd": sp_dd, "passed": passed, **metrics,
            })
            block_trades[bi] = trades
        all_results[arm] = block_rows
        with open(f"{RESEARCH_DIR}\\results\\blocks_{arm}_trades.json", "w") as f:
            json.dump(block_trades, f, default=str)
        with open(f"{RESEARCH_DIR}\\results\\blocks_{arm}.json", "w") as f:
            json.dump(block_rows, f, indent=2, default=str)

    with open(f"{RESEARCH_DIR}\\block_validation_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    log("DONE")


if __name__ == "__main__":
    arms = sys.argv[1:] if len(sys.argv) > 1 else ["baseline"]
    main(arms)
