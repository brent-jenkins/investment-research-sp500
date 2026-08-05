"""Runs one variant (curated-40 / full-500-no-gate / rolling-40) of the
curated-40 vs rolling-40 vs full-500 comparison against the CURRENT,
corrected simulate_portfolio_compounding_mtm() engine and CURRENT
config.yaml (reentry_cooldown_days=0, min_momentum_pct=0.02 -- today's
real production defaults), reset-at-boundary convention (£50,000 reset at
2020-01-01), matching CLAUDE_HISTORY.md's "No market-cap gate at all --
the 'broadening dilutes' finding reverses under the corrected engine
(2026-08-02)" section's own methodology as closely as possible.

Usage: called with --variant curated40 | full500 | rolling40
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

import env_setup

env_setup.patch_all()
config = env_setup.load_config()

from analysis import universe
from analysis.backtest import (
    precompute_ticker, slice_ticker, compute_metrics_fixed,
    compute_peak_relative_drawdown_pct, equity_curve_gbp,
)
from data.ingestion import get_price_df
from reset_wrapper import build_reset_wrapper, verify_faithful

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")  # latest available price_data coverage in work.db
SPLIT = pd.Timestamp("2020-01-01")
RESET_CAPITAL = 50000.0

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
POOL_500_PATH = OUTPUT_DIR / "candidate_pool_500.json"


def load_pool_500() -> list[str]:
    with open(POOL_500_PATH) as f:
        return json.load(f)


def build_caches(tickers: list[str]) -> list[dict]:
    caches = []
    for t in tickers:
        c = precompute_ticker(t, config)
        if c is not None:
            c = slice_ticker(c, START, END)
        caches.append(c)
    return caches


def get_gspc_close():
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    return gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]


def metrics_block(trades: list[dict], starting_capital: float) -> dict:
    m = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    ending = starting_capital
    if trades:
        ec = equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=starting_capital)
        if not ec.empty:
            ending = float(ec["equity_gbp"].iloc[-1])
    return {
        "n_trades": len(trades),
        "win_rate_pct": round(m.get("win_rate", 0.0) * 100, 2) if m.get("win_rate") is not None else None,
        "profit_factor": m.get("profit_factor"),
        "cagr_pct": m.get("cagr_pct"),
        "max_drawdown_pct_fixed_denom": m.get("max_drawdown_pct"),
        "max_drawdown_pct_peak_relative": peak_rel_dd,
        "ending_balance_gbp": round(ending, 2),
    }


def run(variant: str, tickers: list[str], label: str):
    print(f"[{label}] building caches for {len(tickers)} tickers...", flush=True)
    caches = build_caches(tickers)
    n_ok = sum(1 for c in caches if c is not None)
    print(f"[{label}] {n_ok}/{len(tickers)} tickers have usable price history", flush=True)

    gspc_close = get_gspc_close()
    per_trade_pct = float(config["trading212_invest"]["per_trade_pct"])
    max_deployed_pct = float(config["trading212_invest"]["max_total_deployed"])

    wrapped = build_reset_wrapper()
    print(f"[{label}] verifying wrapper faithfulness...", flush=True)
    verify_faithful(wrapped, caches, config, gspc_close, per_trade_pct, max_deployed_pct)
    print(f"[{label}] wrapper verified faithful (byte-identical to the real function when reset is inert)", flush=True)

    trades = wrapped(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct,
        reset_boundary_ts=SPLIT, reset_capital=RESET_CAPITAL,
    )
    print(f"[{label}] {len(trades)} full-walk trades", flush=True)

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])
        test_trades = trades_df[trades_df["entry_date"] >= SPLIT].to_dict("records")
    else:
        test_trades = []

    test_metrics = metrics_block(test_trades, RESET_CAPITAL)
    print(f"[{label}] TEST metrics: {test_metrics}", flush=True)

    # Full trade-level preservation (Research Reproducibility Standard).
    trades_df.to_csv(OUTPUT_DIR / f"{variant}_full_trades.csv", index=False)
    if test_trades:
        pd.DataFrame(test_trades).to_csv(OUTPUT_DIR / f"{variant}_test_trades.csv", index=False)

    result = {
        "variant": variant,
        "label": label,
        "n_input_tickers": len(tickers),
        "n_tickers_with_data": n_ok,
        "start": START.isoformat(),
        "end": END.isoformat(),
        "split": SPLIT.isoformat(),
        "reset_capital_gbp": RESET_CAPITAL,
        "backtest_convention": "reset-at-boundary",
        "engine": "simulate_portfolio_compounding_mtm_reset_at_boundary (source-introspected from the real, current simulate_portfolio_compounding_mtm)",
        "config_reentry_cooldown_days": config["trading212_invest"].get("reentry_cooldown_days"),
        "config_min_momentum_pct": config["trading212_invest"].get("min_momentum_pct"),
        "config_per_trade_pct": per_trade_pct,
        "config_max_total_deployed": max_deployed_pct,
        "test_metrics": test_metrics,
        "full_walk_n_trades": len(trades),
    }
    with open(OUTPUT_DIR / f"{variant}_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"[{label}] wrote {variant}_result.json / _full_trades.csv / _test_trades.csv", flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=["curated40", "full500"])
    args = parser.parse_args()

    if args.variant == "curated40":
        tickers = sorted(universe.STOCK_MEMBERSHIP.keys())
        run("curated40", tickers, "Curated-40 (STOCK_MEMBERSHIP)")
    elif args.variant == "full500":
        tickers = load_pool_500()
        run("full500", tickers, "Full-500 (no market-cap gate)")
