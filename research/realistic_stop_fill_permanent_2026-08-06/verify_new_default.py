"""Verify the new permanent default (realistic intraday-touch STOP_LOSS
fill, 2026-08-06) via the real, standard analysis.validation.run_full_
validation() entry point -- no custom harness needed now that the fix is
the only behavior, not an optional flag. This is the exact call the
Backtest page and DD report generator make.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import time

import pandas as pd
import yaml

from analysis.backtest import compute_peak_relative_drawdown_pct
from analysis.validation import run_full_validation

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\realistic_stop_fill_permanent_2026-08-06'
END = pd.Timestamp("2026-08-03")
START = pd.Timestamp("2007-01-01")


def main():
    t0 = time.time()
    print("=== Verifying the new permanent default via run_full_validation() ===", flush=True)
    result = run_full_validation(
        CONFIG, start=START, end=END, mark_to_market=True, live_universe=True,
    )
    print(f"engine_used={result['engine_used']} deployment_basis={result['deployment_basis']} "
          f"backtest_convention={result['backtest_convention']}", flush=True)
    print(f"total_trades={result['total_trades']}  n_tickers_with_data={result['n_tickers_with_data']}"
          f"/{result['n_tickers_total']}", flush=True)

    trades = result["trades"]
    trades_df = pd.DataFrame(trades)
    ec = trades_df["exit_reason"].value_counts()
    print("exit_reason breakdown:\n" + ec.to_string(), flush=True)

    total_pnl_gbp = trades_df["pnl_gbp"].sum()
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)

    print(f"\ntotal_pnl_gbp = {total_pnl_gbp:,.2f}", flush=True)
    print(f"n_blocks_pass = {result['n_blocks_pass']}/{result['n_blocks_total']}", flush=True)
    print("full (uncapped-window) fixed metrics:", json.dumps(result["full_raw"], indent=2, default=str), flush=True)
    print(f"peak_relative_max_drawdown_pct (full) = {peak_rel_dd}", flush=True)
    print("train:", json.dumps(result["train"], indent=2, default=str), flush=True)
    print("test:", json.dumps(result["test"], indent=2, default=str), flush=True)

    with open(f"{OUT_DIR}/new_default_result.json", "w") as fh:
        json.dump({k: v for k, v in result.items() if k != "trades"}, fh, indent=2, default=str)
    trades_df.to_csv(f"{OUT_DIR}/new_default_trades.csv", index=False)
    with open(f"{OUT_DIR}/new_default_trades_full.json", "w") as fh:
        json.dump(trades, fh, default=str)

    print(f"\nWrote new_default_result.json / new_default_trades.csv / new_default_trades_full.json", flush=True)
    print(f"Total runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
