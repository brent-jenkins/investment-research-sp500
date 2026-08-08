"""Uncapped diagnostic arm: calls the REAL, unmodified
simulate_portfolio_compounding_mtm() with max_deployed_pct driven to
effectively-infinite and absolute_max_position_gbp overridden the same way
in a copied config dict -- no analysis/backtest.py change, just unusual
parameter values from a research script. Cash-affordability
(resolve_cash_constrained_position_gbp) is NOT touched -- you still can't
buy more than you have cash for, a real economic constraint, not an
arbitrary ceiling. Research-only; never reachable from any production path.

Usage: python step3_uncapped_arm.py <run_id> <cache_pkl> <arm A|B> <disable_stop 0|1>
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import copy
import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm

sys.path.insert(0, r"c:\GitHub\investment-app-revalidation\research\adj_close_momentum_2026-08-08")

OUT_DIR = "research/measurement_toolkit_2026-08-08"
UNCAPPED_MAX_DEPLOYED_PCT = 1_000_000.0
UNCAPPED_ABS_MAX_POSITION_GBP = 1e15


def main():
    run_id = sys.argv[1]
    cache_path = sys.argv[2]
    signal_arm = sys.argv[3] if len(sys.argv) > 3 else None  # "A"/"B" for dual caches, None for plain caches
    if signal_arm in ("", "none", "None"):
        signal_arm = None
    disable_stop = sys.argv[4] == "1" if len(sys.argv) > 4 else False

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config = copy.deepcopy(config)
    # absolute_max_position_gbp lives under trading212_invest: (confirmed via
    # `var_cfg = config.get("trading212_invest", {})` in analysis/backtest.py).
    # Recursive override is robust to that regardless of exact nesting.
    def _override_abs_max(d):
        if isinstance(d, dict):
            if "absolute_max_position_gbp" in d:
                d["absolute_max_position_gbp"] = UNCAPPED_ABS_MAX_POSITION_GBP
            for v in d.values():
                _override_abs_max(v)
    _override_abs_max(config)

    t0 = time.time()
    with open(cache_path, "rb") as fh:
        data = pickle.load(fh)
    raw_caches = data["caches"]

    START = pd.Timestamp("2007-01-01")
    END = pd.Timestamp("2026-08-06")

    if signal_arm is not None:
        from step2_build_dual_cache import derive_arm_frame
        caches = [derive_arm_frame(c, signal_arm) for c in raw_caches]
    elif isinstance(raw_caches, dict):
        # Full-history cache (step1_build_full_history_cache.py) -- un-sliced,
        # needs slicing to START/END here (the dual caches are pre-sliced).
        from analysis.backtest import slice_ticker
        caches = [slice_ticker(c, START, END) for c in raw_caches.values()]
    else:
        caches = raw_caches

    from data.ingestion import get_price_df
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    per_trade_pct = float(config.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    trades, rejections = simulate_portfolio_compounding_mtm(
        caches, config, gspc_close, per_trade_pct, UNCAPPED_MAX_DEPLOYED_PCT,
        collect_rejections=True, disable_stop_loss=disable_stop,
    )
    df_v = pd.DataFrame(trades)
    df_rej = pd.DataFrame(rejections)
    reasons = df_rej["reason"].value_counts().to_dict() if not df_rej.empty else {}
    print(f"[{run_id}] n_trades={len(df_v)} total_pnl_gbp={df_v['pnl_gbp'].sum():.2f} "
          f"rejection_reasons={reasons} ({time.time()-t0:.0f}s)", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_{run_id}.csv", index=False)


if __name__ == "__main__":
    main()
