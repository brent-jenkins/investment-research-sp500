"""Assemble the final 9-combination summary table (primary reset-at-boundary
metrics + 16-block validation win-count/mean/median CAGR) from each grid_
worker.py output JSON. Run after all 9 results/*.json files exist."""
import json

RESULTS_DIR = r'c:\GitHub\investment-research-sp500\research\deployment_cap_tradeoff_2026-08-05\results'
GRID_LABELS = [
    "d50_t05", "d50_t06", "d50_t07",
    "d60_t05", "d60_t06", "d60_t07",
    "d70_t05", "d70_t06", "d70_t07",
]


def main():
    print(f"{'label':10s} {'deployed':>8s} {'per_trade':>9s} {'trades':>7s} {'CAGR':>7s} "
          f"{'DD_fixed':>9s} {'DD_peak':>8s} {'PF':>6s} {'Sharpe':>7s} | "
          f"{'blk_pass':>8s} {'mean_CAGR':>9s} {'med_CAGR':>9s}")
    for label in GRID_LABELS:
        try:
            with open(f"{RESULTS_DIR}\\{label}.json") as f:
                r = json.load(f)
        except FileNotFoundError:
            print(f"{label:10s} NOT DONE YET")
            continue
        m = r["primary"]["metrics"]
        print(f"{label:10s} {r['max_deployed_pct']:>8.2f} {r['per_trade_pct']:>9.2f} "
              f"{r['primary']['n_trades']:>7d} {m['cagr_pct']:>7.2f} "
              f"{m['max_drawdown_pct']:>9.2f} {r['primary']['peak_relative_drawdown_pct']:>8.2f} "
              f"{m['profit_factor']:>6.2f} {m['sharpe_ratio']:>7.3f} | "
              f"{r['n_blocks_pass']:>5d}/{r['n_blocks_total']:<2d} {r['mean_block_cagr']:>9.2f} "
              f"{r['median_block_cagr']:>9.2f}")


if __name__ == "__main__":
    main()
