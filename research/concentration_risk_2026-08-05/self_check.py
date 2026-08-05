"""Fidelity check: with corr_haircut_threshold=None, concentration_constraint_
engine.simulate_with_concentration_haircut() must reproduce the real,
unmodified analysis.backtest.simulate_portfolio_compounding_mtm() exactly, on
a real subset of tickers/history -- the same requirement and pattern as
research/var_sizing_sweep_2026-08-05/self_check.py before it.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')
sys.path.insert(0, r'c:\GitHub\investment-research-sp500\research\concentration_risk_2026-08-05')

import pandas as pd
import yaml

import analysis.universe as universe
from analysis.backtest import precompute_ticker, simulate_portfolio_compounding_mtm
from data.ingestion import get_price_df
from concentration_constraint_engine import simulate_with_concentration_haircut

CONFIG_PATH = r'c:\GitHub\investment-research-sp500\config.yaml'


def main():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    gspc_df = get_price_df("^GSPC", days=3000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=pd.Timestamp("2026-08-03").date())[:60]
    caches = [precompute_ticker(t, config) for t in candidate_tickers]

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])

    real_trades = simulate_portfolio_compounding_mtm(caches, config, gspc_close, per_trade_pct, max_deployed_pct)
    variant_trades = simulate_with_concentration_haircut(
        caches, config, gspc_close, per_trade_pct, max_deployed_pct,
        corr_haircut_threshold=None, corr_haircut_factor=1.0,
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


if __name__ == "__main__":
    main()
