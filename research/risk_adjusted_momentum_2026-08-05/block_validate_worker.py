"""16-block independent validation of the risk-adjusted ranking variant,
using the REAL, exact formula -- analysis.backtest.make_blocks() and
analysis.validation._block_passes() imported directly, not re-derived (per
CLAUDE.md's "Research reproducibility standard": exact code, not prose).

The pass rule, quoted verbatim from analysis/validation.py:_block_passes()
so it survives this script's own deletion:

    dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
    pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
    return bool(dd_ok and pf_ok)

Each block is a genuinely independent reset (fresh starting_capital, zero
open positions, truncated to [block_start, block_end] with a corrected
END_OF_DATA) -- not a bucketing of one continuous walk.

Usage: python block_validate_worker.py <raw|risk_adjusted> <out_json>
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
from analysis.backtest import precompute_ticker, compute_metrics_fixed, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series, VALIDATION_START
from data.ingestion import get_price_df
from risk_adjusted_engine import simulate_with_risk_adjusted_ranking

CONFIG_PATH = ROOT / "config.yaml"
END = pd.Timestamp("2026-08-03")


def main():
    mode, out_path = sys.argv[1], sys.argv[2]
    config = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))

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

    block_results, n_pass = [], 0
    all_trades = {}
    for i, (b_start, b_end) in enumerate(blocks):
        stats = {}
        trades = simulate_with_risk_adjusted_ranking(
            caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
            corr_haircut_threshold=None, corr_haircut_factor=1.0,
            block_start=b_start, block_end=b_end,
            rank_mode=mode, rank_stats=stats,
        )
        strat_m = compute_metrics_fixed(trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        passed = _block_passes(strat_m, sp_dd)
        n_pass += int(passed)
        all_trades[i] = trades
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(trades),
            "strat_cagr": strat_m["cagr_pct"], "strat_dd": strat_m["max_drawdown_pct"],
            "strat_pf": strat_m.get("profit_factor", 0.0),
            "strat_sharpe": strat_m.get("sharpe_ratio", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": passed, "ranking_fallback_stats": stats,
        })
        print(f"  [{mode}] block {i}: trades={len(trades)} cagr={strat_m['cagr_pct']} "
              f"dd={strat_m['max_drawdown_pct']} pf={strat_m.get('profit_factor')} "
              f"pass={passed}", flush=True)

    elapsed = time.time() - t0
    result = {
        "label": mode, "rank_mode": mode,
        "pass_rule": 'abs(max_drawdown_pct) <= abs(sp_dd)*1.5 AND profit_factor > 1.0',
        "n_blocks_pass": n_pass, "n_blocks_total": len(blocks),
        "blocks": block_results, "elapsed_sec": elapsed,
    }
    json.dump(result, open(out_path, "w"), indent=2, default=str)
    # Trade-level output preserved separately (per CLAUDE.md's 2026-08-02
    # extension: preserve full trade-level output, not just the formula).
    json.dump(all_trades, open(str(out_path).replace(".json", "_trades.json"), "w"),
              indent=2, default=str)
    print(f"[{mode}] BLOCK VALIDATION done {elapsed:.0f}s -- {n_pass}/{len(blocks)} pass", flush=True)


if __name__ == "__main__":
    main()
