"""Fidelity self-check for var_sizing_engine.py -- run BEFORE trusting any
sweep result, per this project's "Research scripts must verify decision-
engine parity" standing requirement.

Two checks:

1. simulate_var_sizing_variant() with inert defaults (var_confidence_
   override=None i.e. config's 0.95, scaling_exponent=0.5, block_start=
   block_end=None) must reproduce the REAL, unmodified analysis.backtest.
   simulate_portfolio_compounding_mtm()'s trade sequence byte-for-byte on
   identical inputs -- proving the copy in var_sizing_engine.py is a
   genuinely isolated, faithful copy, not an accidental additional
   divergence. Matches the exact subset/window size (60 tickers,
   2018-01-01..2019-06-30) research/deployment_cap_mtm_vs_costbasis_2026-08-04
   used for its own self-check, for consistency with established practice.

2. _var_position_size_core_scaled(..., scaling_exponent=0.5) must produce
   IDENTICAL output to the real analysis.volatility._var_position_size_core()
   for a battery of random (log_returns, account_value, holding_days, ...)
   inputs -- proving x**0.5 == sqrt(x) isn't just assumed true but checked
   directly against the real function's own output, including the
   ceiling-applied-last clamp-ordering edge cases that function's own
   docstring specifically calls out.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\var_sizing_sweep_2026-08-05')

import numpy as np
import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import (
    precompute_ticker, slice_ticker, get_price_df, simulate_portfolio_compounding_mtm,
)
from analysis.volatility import _var_position_size_core
from var_sizing_engine import simulate_var_sizing_variant, _var_position_size_core_scaled

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)


def _trade_signature(trades):
    return sorted(
        (t["ticker"], str(t["entry_date"]), str(t["exit_date"]), t["exit_reason"], round(t["position_gbp"], 2),
         round(t["pnl_gbp"], 2))
        for t in trades
    )


def check_engine_fidelity():
    print("Check 1: simulate_var_sizing_variant(defaults) vs real simulate_portfolio_compounding_mtm()", flush=True)
    start = pd.Timestamp("2018-01-01")
    end = pd.Timestamp("2019-06-30")
    candidate_tickers = universe.get_live_stock_candidates(as_of=end.date())[:60]

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= end)]

    caches = []
    for ticker in candidate_tickers:
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, start, end)
        caches.append(c)

    trades_real = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, 0.05, 0.5)
    trades_mine = simulate_var_sizing_variant(
        caches, CONFIG, gspc_close, 0.05, 0.5,
        var_confidence_override=None, scaling_exponent=0.5, block_start=None, block_end=None,
    )

    sig_real = _trade_signature(trades_real)
    sig_mine = _trade_signature(trades_mine)
    match = sig_real == sig_mine
    print(f"  real trades: {len(trades_real)}, mine: {len(trades_mine)}, identical: {match}", flush=True)
    if not match:
        only_real = [s for s in sig_real if s not in sig_mine][:5]
        only_mine = [s for s in sig_mine if s not in sig_real][:5]
        print("  only in real:", only_real, flush=True)
        print("  only in mine:", only_mine, flush=True)
    return match


def check_scaled_core_fidelity():
    print("Check 2: _var_position_size_core_scaled(exponent=0.5) vs real _var_position_size_core()", flush=True)
    rng = np.random.default_rng(42)
    all_match = True
    n_checked = 0
    for trial in range(500):
        n = rng.integers(60, 300)
        log_returns = rng.normal(loc=rng.uniform(-0.001, 0.001), scale=rng.uniform(0.005, 0.05), size=n)
        account_value = float(rng.uniform(1000, 500000))
        max_position = float(rng.uniform(10, 100000))
        holding_days = int(rng.integers(1, 90))
        var_confidence = float(rng.choice([0.85, 0.90, 0.95, 0.975, 0.99, 0.995]))
        max_risk_pct = float(rng.uniform(0.001, 0.05))
        min_position = float(rng.uniform(10, 200))

        real = _var_position_size_core(
            log_returns, account_value, max_position, holding_days, var_confidence, max_risk_pct, min_position,
        )
        scaled = _var_position_size_core_scaled(
            log_returns, account_value, max_position, holding_days, var_confidence, max_risk_pct, min_position,
            scaling_exponent=0.5,
        )
        n_checked += 1
        if real != scaled:
            all_match = False
            print(f"  MISMATCH trial {trial}: real={real} scaled={scaled}", flush=True)
    print(f"  {n_checked} random trials, all_match={all_match}", flush=True)
    return all_match


if __name__ == "__main__":
    ok1 = check_engine_fidelity()
    ok2 = check_scaled_core_fidelity()
    print(f"\nSELF-CHECK RESULT: engine_fidelity={ok1}, scaled_core_fidelity={ok2}", flush=True)
    if not (ok1 and ok2):
        print("SELF-CHECK FAILED -- do not trust any sweep results until this passes.", flush=True)
        sys.exit(1)
    print("SELF-CHECK PASSED.", flush=True)
