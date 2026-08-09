"""Engine-correctness check (Sec 6 of the pre-registration): the instrumented
position_count_engine.py, at per_trade_pct=0.05 (today's actual, unchanged
config value -- this study's own "22" / baseline arm), must reproduce the
raw, unmodified analysis.backtest.simulate_portfolio_compounding_mtm() output
byte-identical -- on BOTH pools, since this study is decisive on Pool 2 CLEAN
but reports Pool 1 too, and neither has been checked against this specific
new engine yet.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm
from position_count_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/position_count_prereg_2026-08-09"
MAX_DEPLOYED_PCT = 0.70
BASELINE_PER_TRADE_PCT = 0.05


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


def _check_pool(pool: str) -> int:
    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"[{pool}] cache loaded ({time.time()-t0:.0f}s, {len(caches)} tickers)", flush=True)

    raw_trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, BASELINE_PER_TRADE_PCT, MAX_DEPLOYED_PCT, collect_rejections=False,
    )
    raw_df = _trades_df(raw_trades)
    print(f"[{pool}] raw production engine: {len(raw_df)} trades, total_pnl={raw_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    result = simulate_with_panel(caches, CONFIG, gspc_close, BASELINE_PER_TRADE_PCT, MAX_DEPLOYED_PCT)
    new_df = _trades_df(result["trades"])
    print(f"[{pool}] position_count_engine (per_trade_pct=0.05): {len(new_df)} trades, "
          f"total_pnl={new_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    diffs = _compare(raw_df, new_df, f"{pool} CHECK")
    print(f"[{pool}] RESULT:", "PASS -- byte-identical" if diffs == 0 else "FAIL -- STOP, engine mismatch", flush=True)

    new_df.to_csv(f"{OUT_DIR}/verify_{pool}_baseline.csv", index=False)
    return diffs


def main():
    total_diffs = 0
    for pool in ("pool1", "pool2_clean"):
        print(f"\n=== {pool} ===", flush=True)
        total_diffs += _check_pool(pool)
    print(f"\n{'ALL CHECKS PASS' if total_diffs == 0 else 'CHECKS FAILED -- DO NOT PROCEED'} (total_diffs={total_diffs})", flush=True)


if __name__ == "__main__":
    main()
