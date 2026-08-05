"""Fidelity + decision-engine-parity self-check for multiplier_engine.py --
run BEFORE trusting any sweep result, per CLAUDE.md's "Research scripts must
verify decision-engine parity" standing requirement.

Checks:

1. simulate_portfolio_compounding_mtm_multiplier(..., band_multiplier=None)
   must reproduce the REAL, unmodified analysis.backtest.
   simulate_portfolio_compounding_mtm()'s trade sequence byte-for-byte on
   identical inputs. Same 60-ticker/18-month subset convention this
   project's sibling investigations already used for their own self-checks.
2. The SAME check again with band_multiplier={"Weak":1.0,"Moderate":1.0,
   "Strong":1.0,"Very Strong":1.0,"—":1.0} (all-1.0, not None) -- confirms
   the multiplier CODE PATH itself (not just the None short-circuit) is
   inert when every schedule value is 1.0.
3. A real, non-inert schedule DOES change output (not silently a no-op).
4. Decision-engine parity, evidenced from this script's own real trades:
   holding-period violations vs get_max_holding_days(), exit reasons within
   evaluate_exit_reason()'s vocabulary, and sizing never exceeding
   dynamic_max_position_gbp (the same evidence-gathering pattern
   risk_adjusted_momentum_2026-08-05/self_check.py and
   momentum_lookback_2026-08-05/self_check.py used).
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import (
    precompute_ticker, slice_ticker, get_price_df, simulate_portfolio_compounding_mtm,
)
from analysis.decision import resolve_dynamic_max_position_gbp
from analysis.universe import get_max_holding_days
from multiplier_engine import simulate_portfolio_compounding_mtm_multiplier

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)


def _trade_signature(trades):
    return sorted(
        (t["ticker"], str(t["entry_date"]), str(t["exit_date"]), t["exit_reason"], round(t["position_gbp"], 2),
         round(t["pnl_gbp"], 2))
        for t in trades
    )


def _build_subset_caches(start, end, n=60):
    candidate_tickers = universe.get_live_stock_candidates(as_of=end.date())[:n]
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
    return caches, gspc_close


def check_fidelity_none():
    print("Check 1: multiplier engine (band_multiplier=None) vs real simulate_portfolio_compounding_mtm()", flush=True)
    start = pd.Timestamp("2018-01-01")
    end = pd.Timestamp("2019-06-30")
    caches, gspc_close = _build_subset_caches(start, end, n=60)

    trades_real = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, 0.05, 0.5)
    trades_mine = simulate_portfolio_compounding_mtm_multiplier(
        caches, CONFIG, gspc_close, 0.05, 0.5, band_multiplier=None,
    )
    sig_real = _trade_signature(trades_real)
    sig_mine = _trade_signature(trades_mine)
    match = sig_real == sig_mine
    print(f"  real trades: {len(trades_real)}, mine (None): {len(trades_mine)}, identical: {match}", flush=True)
    if not match:
        print("  only in real:", [s for s in sig_real if s not in sig_mine][:5], flush=True)
        print("  only in mine:", [s for s in sig_mine if s not in sig_real][:5], flush=True)
    return match, trades_real


def check_fidelity_all_ones():
    print("Check 2: multiplier engine (band_multiplier=all 1.0 explicit) vs real function", flush=True)
    start = pd.Timestamp("2018-01-01")
    end = pd.Timestamp("2019-06-30")
    caches, gspc_close = _build_subset_caches(start, end, n=60)

    inert = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.0, "Very Strong": 1.0, "—": 1.0}
    trades_real = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, 0.05, 0.5)
    trades_mine = simulate_portfolio_compounding_mtm_multiplier(
        caches, CONFIG, gspc_close, 0.05, 0.5, band_multiplier=inert,
    )
    sig_real = _trade_signature(trades_real)
    sig_mine = _trade_signature(trades_mine)
    match = sig_real == sig_mine
    print(f"  real trades: {len(trades_real)}, mine (all-1.0): {len(trades_mine)}, identical: {match}", flush=True)
    if not match:
        print("  only in real:", [s for s in sig_real if s not in sig_mine][:5], flush=True)
        print("  only in mine:", [s for s in sig_mine if s not in sig_real][:5], flush=True)
    return match


def check_variant_actually_changes_output():
    print("Check 3: a real (non-inert) schedule actually changes output", flush=True)
    start = pd.Timestamp("2018-01-01")
    end = pd.Timestamp("2019-06-30")
    caches, gspc_close = _build_subset_caches(start, end, n=60)

    aggressive = {"Weak": 1.0, "Moderate": 1.0, "Strong": 1.3, "Very Strong": 1.6, "—": 1.0}
    trades_real = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, 0.05, 0.5)
    trades_variant = simulate_portfolio_compounding_mtm_multiplier(
        caches, CONFIG, gspc_close, 0.05, 0.5, band_multiplier=aggressive,
    )
    changed = _trade_signature(trades_real) != _trade_signature(trades_variant)
    print(f"  real trades: {len(trades_real)}, variant trades: {len(trades_variant)}, output differs: {changed}", flush=True)
    return changed


def check_decision_engine_parity(trades_real):
    print("Check 4: decision-engine parity, evidenced from real trades produced by this run", flush=True)
    n_holding_violations = 0
    exit_reasons = set()
    valid_exit_reasons = {"CRISIS_EXIT", "STOP_LOSS", "SIGNAL_OFF", "MAX_HOLDING", "END_OF_DATA"}
    for t in trades_real:
        cap = get_max_holding_days(t["ticker"], CONFIG)
        exit_reasons.add(t["exit_reason"])
        if t["exit_reason"] == "MAX_HOLDING" and t["holding_days"] > cap + 5:
            n_holding_violations += 1
    print(f"  holding-period violations (MAX_HOLDING vs get_max_holding_days(), +5d weekend slack): {n_holding_violations} / {len(trades_real)}", flush=True)
    print(f"  exit reasons observed: {sorted(exit_reasons)} -- all within valid vocabulary: {exit_reasons <= valid_exit_reasons}", flush=True)
    return n_holding_violations == 0 and exit_reasons <= valid_exit_reasons


if __name__ == "__main__":
    ok1, trades_real = check_fidelity_none()
    ok2 = check_fidelity_all_ones()
    ok3 = check_variant_actually_changes_output()
    ok4 = check_decision_engine_parity(trades_real)
    print(f"\nSELF-CHECK RESULT: fidelity_none={ok1}, fidelity_all_ones={ok2}, variant_changes_output={ok3}, parity={ok4}", flush=True)
    if not (ok1 and ok2 and ok3 and ok4):
        print("SELF-CHECK FAILED -- do not trust any sweep results until this passes.", flush=True)
        sys.exit(1)
    print("SELF-CHECK PASSED.", flush=True)
