"""Run ONE var_confidence/scaling-exponent variant through the real, exact
16-block validation formula -- analysis.backtest.make_blocks() for the
boundaries (unchanged, byte-for-byte, since the first V2 commit) and
analysis.validation._block_passes() for pass/fail (dd_ok: strategy max
drawdown no worse than 1.5x the S&P's own over the same window; pf_ok:
strategy profit factor > 1.0 -- both imported directly, not re-derived).

Each of the 16 blocks is a GENUINELY INDEPENDENT reset -- the day-by-day
event walk is truncated to [block_start, block_end] and every position still
open at the block's own end is force-closed via a corrected END_OF_DATA, the
identical technique already validated in CLAUDE_HISTORY.md's "Is the 5-day
re-entry cooldown result real, or a lucky historical sequence?" investigation
and reused unchanged by research/price_sanity_sweep_2026-08-03's own block
check. This is NOT the same thing as run_full_validation()'s own "blocks"
output (which buckets a single continuous walk's trades by exit_date --
still subject to train-period-compounding contamination carrying into later
blocks' position sizing, per CLAUDE.md's "Two backtest conventions" section)
-- this script builds each block from a fresh £starting_capital, zero-
open-positions walk that never visits any date outside that block.

Usage:
    python block_validate_worker.py <var_confidence|none> <scaling_exponent> <label> <out_json>
"""
import sys
import json
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml

import analysis.universe as universe
import analysis.validation as validation_mod
from analysis.backtest import precompute_ticker, compute_metrics_fixed, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series, VALIDATION_START
from data.ingestion import get_price_df

sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\var_sizing_sweep_2026-08-05')
from var_sizing_engine import simulate_var_sizing_variant

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")


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

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    blocks = make_blocks(VALIDATION_START, END, n_blocks=16)

    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks):
        trades = simulate_var_sizing_variant(
            caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
            var_confidence_override=var_confidence_override,
            scaling_exponent=scaling_exponent,
            block_start=b_start, block_end=b_end,
        )
        strat_m = compute_metrics_fixed(trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        passed = _block_passes(strat_m, sp_dd)
        if passed:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(trades),
            "strat_cagr": strat_m["cagr_pct"], "strat_dd": strat_m["max_drawdown_pct"],
            "strat_pf": strat_m.get("profit_factor", 0.0),
            "strat_sharpe": strat_m.get("sharpe_ratio", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": passed,
        })
        print(f"  [{label}] block {i}: trades={len(trades)} cagr={strat_m['cagr_pct']} "
              f"dd={strat_m['max_drawdown_pct']} pf={strat_m.get('profit_factor')} pass={passed}", flush=True)

    elapsed = time.time() - t0
    result = {
        "label": label,
        "var_confidence_override": var_confidence_override,
        "scaling_exponent": scaling_exponent,
        "n_blocks_pass": n_pass, "n_blocks_total": len(blocks),
        "blocks": block_results,
        "elapsed_sec": elapsed,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{label}] BLOCK VALIDATION done in {elapsed:.1f}s -- {n_pass}/{len(blocks)} pass", flush=True)


if __name__ == "__main__":
    main()
