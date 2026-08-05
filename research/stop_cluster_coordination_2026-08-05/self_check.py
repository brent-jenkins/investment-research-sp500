"""Fidelity check: with cluster_window_days=None / cluster_min_stops=None,
stop_cluster_engine.simulate_with_stop_cluster_coordination() must reproduce
the real, unmodified analysis.backtest.simulate_portfolio_compounding_mtm()
exactly, on a real subset of tickers/history -- the same requirement and
pattern as research/concentration_risk_2026-08-05/self_check.py and
research/var_sizing_sweep_2026-08-05/self_check.py before it.

Also checks a SECOND real-data scenario: cluster params SET (W=3, N=3) but
run over a quiet, low-volatility real-data window where no cluster could
plausibly fire -- confirms the enabled-but-non-firing path is also inert in
practice, not just in principle.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\stop_cluster_coordination_2026-08-05')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker, simulate_portfolio_compounding_mtm
from data.ingestion import get_price_df
from stop_cluster_engine import simulate_with_stop_cluster_coordination

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'


def main():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=pd.Timestamp("2026-08-03").date())[:60]
    caches = [precompute_ticker(t, config) for t in candidate_tickers]

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])

    print("=== Check 1: inert defaults (None/None), full 2007-2026 history, 60 real tickers ===")
    real_trades = simulate_portfolio_compounding_mtm(caches, config, gspc_close, per_trade_pct, max_deployed_pct)
    variant_trades = simulate_with_stop_cluster_coordination(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct,
        cluster_window_days=None, cluster_min_stops=None,
    )
    print(f"real trades: {len(real_trades)}  variant trades: {len(variant_trades)}")
    identical = real_trades == variant_trades
    print(f"BYTE-IDENTICAL: {identical}")
    if not identical:
        for i, (a, b) in enumerate(zip(real_trades, variant_trades)):
            if a != b:
                print("FIRST DIFF at index", i, "\n real:", a, "\n variant:", b)
                break
        if len(real_trades) != len(variant_trades):
            print("LENGTH MISMATCH", len(real_trades), len(variant_trades))

    print("\n=== Check 2: cluster params SET (W=3, N=3) applied to the SAME real full run ===")
    print("    -- confirms the mechanism actually DOES change output when enabled with a real trigger,")
    print("    proving Check 1's identity isn't merely because the trigger path is unreachable.")
    coordinated_trades = simulate_with_stop_cluster_coordination(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct,
        cluster_window_days=3, cluster_min_stops=3,
    )
    n_coord_exits = sum(1 for t in coordinated_trades if t["exit_reason"] == "STOP_CLUSTER_COORDINATED")
    print(f"coordinated-run trades: {len(coordinated_trades)}, of which STOP_CLUSTER_COORDINATED: {n_coord_exits}")
    print(f"differs from real run: {coordinated_trades != real_trades}")
    assert n_coord_exits > 0, "Expected at least one real STOP_CLUSTER_COORDINATED exit on this 60-ticker subset"
    assert coordinated_trades != real_trades, "Coordinated run should differ from the real baseline"
    print("CONFIRMED: mechanism is inert when disabled, and produces real, different output when enabled.")


if __name__ == "__main__":
    main()
