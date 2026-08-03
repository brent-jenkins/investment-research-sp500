"""CLI entry point for the standing strategy-vs-S&P-500-vs-prior-baseline
comparison — see analysis/benchmark_comparison.py for the real logic and
CLAUDE.md's "Standing practice: strategy vs. S&P 500 benchmark
comparison" section for when to run this.

Usage (from the project root):
    python scripts/compare_strategy_vs_benchmark.py

Reads config.yaml as it actually is on disk at run time (never a
snapshot or a hardcoded set of parameters) and the live price_data table
for the current, real S&P 500 stock candidate pool. Requires a fresh,
verified-current local DB — this script does not fetch or verify data
freshness itself, matching every other local research tool in this
project (see CLAUDE.md's "Experimental/research work runs locally, not
on the server" section for the standing sync discipline this assumes
has already been followed).

Appends one entry to research/strategy_vs_benchmark_log.jsonl (git-
tracked, never gitignored) and prints both the machine-readable entry
and the regenerated markdown table (paste straight into CLAUDE.md's log
section) to stdout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The markdown log table (and this project's own CLAUDE.md style
# generally) uses real Unicode characters (arrows, deltas) -- reconfigure
# stdout to UTF-8 so printing them never crashes on a Windows console's
# default cp1252 codepage, rather than stripping the characters
# themselves (they're meant to end up in CLAUDE.md, which already uses
# them throughout).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from analysis.benchmark_comparison import render_markdown_log, run_comparison


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    config = _load_config()
    print("Running strategy-vs-benchmark comparison against the live config and DB...")
    print(f"  per_trade_pct={config.get('trading212_invest', {}).get('per_trade_pct')}  "
          f"max_total_deployed={config.get('trading212_invest', {}).get('max_total_deployed')}  "
          f"reentry_cooldown_days={config.get('trading212_invest', {}).get('reentry_cooldown_days')}  "
          f"min_momentum_pct={config.get('trading212_invest', {}).get('min_momentum_pct')}")
    print()

    result = run_comparison(config)
    entry = result["entry"]
    prior = result["prior"]

    s = entry["strategy"]
    sp = entry["sp500"]
    print(f"=== Current strategy ({entry['period_start']} -> {entry['period_end']}, "
          f"£{entry['starting_capital_gbp']:,.0f} start) ===")
    print(f"  Universe: {s['n_tickers_with_data']}/{s['n_tickers_total']} tickers usable")
    print(f"  Trades: {s['n_trades']}")
    print(f"  CAGR: {s['cagr_pct']}%")
    print(f"  Max drawdown (fixed-denominator): {s['max_drawdown_pct_fixed']}%")
    print(f"  Max drawdown (peak-relative): {s['max_drawdown_pct_peak_relative']}%")
    print(f"  Profit factor: {s['profit_factor']}   Win rate: {s['win_rate_pct']}%")
    print(f"  Ending balance: £{s['ending_balance_gbp']:,.2f}")
    print(f"  Total % change from £{entry['starting_capital_gbp']:,.0f}: {s['total_pct_change']}%")
    print()
    print(f"=== S&P 500 buy-and-hold (identical window/capital) ===")
    print(f"  CAGR: {sp['cagr_pct']}%")
    print(f"  Max drawdown (peak-relative): {sp['max_drawdown_pct_peak_relative']}%")
    print(f"  Ending balance: £{sp['ending_balance_gbp']:,.2f}")
    print(f"  Total % change: {sp['total_pct_change']}%")
    print()
    print(f"=== Deltas ===")
    print(f"  Strategy vs. S&P 500 (pp on total % change): {entry['delta_vs_sp500_pp']}")
    if prior is not None:
        print(f"  Strategy vs. prior recorded run ({prior['date']}): {entry['delta_vs_prior_run_pp']}pp")
    else:
        print("  No prior run recorded — this is the first entry in the log.")
    print()
    print("=== Markdown log (paste into CLAUDE.md's log section) ===")
    print(render_markdown_log())


if __name__ == "__main__":
    main()
