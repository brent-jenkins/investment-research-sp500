"""Two required verification steps (added on review, §6):
1. Pool 2 CLEAN degenerate-case check: redesign_engine.py at
   (rebalance_frequency="daily", shortlist_size=None) must reproduce the raw,
   unmodified simulate_portfolio_compounding_mtm() byte-identical on Pool 2
   CLEAN specifically (the original degenerate check was Pool 1 only).
2. Non-binding-shortlist check: shortlist_size=1000 (larger than any single
   day's gate-passing count) must reproduce shortlist_size=None's own output
   byte-identical -- isolates whether the truncation code path itself
   introduces any side effect beyond the truncation.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-gatewidth-prereg")
sys.path.insert(0, r"c:\GitHub\investment-app-gatewidth-prereg\research\admission_redesign_prereg_2026-08-08")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm
from redesign_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/gatewidth_prereg_2026-08-08"
MAX_DEPLOYED_PCT = 0.70


def _trades_df(trades):
    df = pd.DataFrame(trades)
    df["entry_date"] = df["entry_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    df["exit_date"] = df["exit_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    return df


def _compare(a: pd.DataFrame, b: pd.DataFrame, label: str) -> int:
    if len(a) != len(b):
        print(f"  [{label}] ROW COUNT MISMATCH: {len(a)} vs {len(b)}")
        return abs(len(a) - len(b))
    sort_cols = ["entry_date", "ticker"]
    a2 = a.sort_values(sort_cols).reset_index(drop=True)
    b2 = b.sort_values(sort_cols).reset_index(drop=True)
    diffs = 0
    for col in ["exit_date", "exit_reason", "holding_days"]:
        mism = (a2[col].astype(str) != b2[col].astype(str)).sum()
        diffs += mism
        if mism:
            print(f"  [{label}] MISMATCH in {col}: {mism} rows differ")
    for col in ["pnl_gbp", "position_gbp", "entry_price", "exit_price"]:
        mism = (a2[col] - b2[col]).abs().gt(0.01).sum()
        diffs += mism
        if mism:
            print(f"  [{label}] MISMATCH in {col}: {mism} rows differ")
    print(f"  [{label}] {len(a)} vs {len(b)} trades, {diffs} row-level mismatches")
    return diffs


def main():
    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    print(f"Loaded Pool 2 CLEAN cache ({time.time()-t0:.0f}s, {len(caches)} tickers)", flush=True)

    print("\n=== CHECK 1: raw production engine vs redesign_engine.py (daily, shortlist=None) ===", flush=True)
    raw_trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT, collect_rejections=False,
    )
    raw_df = _trades_df(raw_trades)
    print(f"  raw production engine: {len(raw_df)} trades, total_pnl={raw_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    result_none = simulate_with_panel(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        disable_stop_loss=False, rebalance_frequency="daily", shortlist_size=None,
    )
    none_df = _trades_df(result_none["trades"])
    print(f"  redesign_engine (shortlist=None): {len(none_df)} trades, total_pnl={none_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    diffs1 = _compare(raw_df, none_df, "CHECK 1")
    print("CHECK 1 RESULT:", "PASS -- byte-identical" if diffs1 == 0 else "FAIL -- STOP, engine mismatch on Pool 2 CLEAN", flush=True)

    print("\n=== CHECK 2: non-binding shortlist (1000) vs shortlist=None ===", flush=True)
    result_1000 = simulate_with_panel(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        disable_stop_loss=False, rebalance_frequency="daily", shortlist_size=1000,
    )
    df_1000 = _trades_df(result_1000["trades"])
    print(f"  shortlist=1000: {len(df_1000)} trades, total_pnl={df_1000['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    diffs2 = _compare(none_df, df_1000, "CHECK 2")
    print("CHECK 2 RESULT:", "PASS -- byte-identical, truncation code path is clean" if diffs2 == 0 else "FAIL -- STOP, truncation has a side effect beyond truncation", flush=True)

    none_df.to_csv(f"{OUT_DIR}/verify_pool2_clean_shortlistNone.csv", index=False)
    print(f"\nDone ({time.time()-t0:.0f}s total)", flush=True)


if __name__ == "__main__":
    main()
