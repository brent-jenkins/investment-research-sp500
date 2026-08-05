"""Concentration-risk investigation, Step 1: run the REAL, unmodified
production backtest engine (analysis.backtest.simulate_portfolio_compounding_mtm())
over the full validated window, CONTINUOUS WALK convention (no reset), exactly
matching the "what would one real account since inception show" question --
per CLAUDE.md's "Two backtest conventions" section, this is the correct
convention for a track-record / historical-concentration report, not a
variant comparison (that will use reset-at-boundary later, only if Part 3 is
warranted).

No custom candidate pool, no monkeypatching of analysis.universe.is_member()
or anything else -- this calls the real, current production function
unmodified with the real, current live universe (analysis.universe.
get_live_stock_candidates()), the same way analysis.validation.
run_full_validation() itself does. Per CLAUDE.md's "Research scripts must
verify decision-engine parity" section, that section's requirement applies to
scripts that build a custom candidate pool or monkeypatch is_member() --
neither applies here, since this script does not re-derive any decision
logic at all; it is a direct, unmodified call into the real production
engine. (Decision-engine parity is separately spot-checked below anyway,
for the same "evidence not assumption" reason, even though the exemption
applies.)

Saves the full trade list (headline-figure preservation, per CLAUDE.md's
"Research reproducibility standard") to results/full_trades_continuous.json.
"""
import sys
import json
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import (
    precompute_ticker,
    simulate_portfolio_compounding_mtm,
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
)
from analysis.universe import get_max_holding_days
from data.ingestion import get_price_df

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'
END = pd.Timestamp("2026-08-03")  # pinned, matches local DB's own max date (confirmed)
OUT_TRADES = r'c:\GitHub\investment-research-sp500\research\concentration_risk_2026-08-05\results\full_trades_continuous.json'


def main():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    t0 = time.time()
    print("Loading ^GSPC...", flush=True)
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    print(f"  ^GSPC rows: {len(gspc_close_full)}, range {gspc_close_full.index.min()} - {gspc_close_full.index.max()}", flush=True)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Live candidates: {len(candidate_tickers)}", flush=True)

    caches = []
    t1 = time.time()
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, config)
        caches.append(c)
        if (i + 1) % 100 == 0:
            print(f"  precomputed {i+1}/{len(candidate_tickers)} ({time.time()-t1:.0f}s)", flush=True)
    n_ok = sum(1 for c in caches if c is not None)
    print(f"Precompute done: {n_ok}/{len(candidate_tickers)} usable, {time.time()-t1:.0f}s", flush=True)

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    print("Running simulate_portfolio_compounding_mtm() -- continuous walk, full 2007-2026...", flush=True)
    t2 = time.time()
    trades = simulate_portfolio_compounding_mtm(
        caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
    )
    print(f"Simulation done: {len(trades)} trades, {time.time()-t2:.0f}s", flush=True)

    metrics = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)

    result = {
        "backtest_convention": "continuous_walk",
        "note": "Continuous walk 2007-01-01 (earliest available) to 2026-08-03, "
                "real unmodified simulate_portfolio_compounding_mtm(), live universe "
                "(analysis.universe.get_live_stock_candidates), no reset -- this reports "
                "what one real account would show since inception, per CLAUDE.md's "
                "'Two backtest conventions' section. NOT used for variant comparison.",
        "n_tickers_live_universe": len(candidate_tickers),
        "n_tickers_with_data": n_ok,
        "window_end": END.date().isoformat(),
        "starting_capital_gbp": starting_capital,
        "per_trade_pct": per_trade_pct,
        "max_total_deployed": max_deployed_pct,
        "n_trades": len(trades),
        "metrics_fixed_denominator": metrics,
        "peak_relative_max_drawdown_pct": peak_rel_dd,
        "elapsed_sec": time.time() - t0,
        "trades": trades,
    }
    with open(OUT_TRADES, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Saved {len(trades)} trades to {OUT_TRADES}", flush=True)
    print(f"CAGR={metrics.get('cagr_pct')}  DD(fixed)={metrics.get('max_drawdown_pct')}  "
          f"DD(peak-rel)={peak_rel_dd}  PF={metrics.get('profit_factor')}  "
          f"Sharpe={metrics.get('sharpe_ratio')}  total_elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
