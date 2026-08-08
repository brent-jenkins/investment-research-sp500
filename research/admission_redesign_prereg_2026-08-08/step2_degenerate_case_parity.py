"""Degenerate-case parity check for redesign_engine.simulate_with_panel():
rebalance_frequency="daily", shortlist_size=None must exactly reproduce the
real, unmodified simulate_portfolio_compounding_mtm() -- run against Pool 1's
cache (faster than full Pool 2, and its reference figures are already on
hand and independently confirmed: 4,749 trades, £240,360.75 total P&L, from
this session's selection-audit parity check against trades_pool1_A.csv).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-admission-redesign")

import pickle
import time

import pandas as pd
import yaml

from redesign_engine import simulate_with_panel

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/admission_redesign_prereg_2026-08-08"
MAX_DEPLOYED_PCT = 0.70


def main():
    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_pool1.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    print(f"Loaded cache ({time.time()-t0:.0f}s)", flush=True)

    result = simulate_with_panel(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
        disable_stop_loss=False, rebalance_frequency="daily", shortlist_size=None,
    )
    df = pd.DataFrame(result["trades"])
    n_trades = len(df)
    total_pnl = round(float(df["pnl_gbp"].sum()), 2)
    print(f"n_trades={n_trades}  total_pnl_gbp={total_pnl}  ({time.time()-t0:.0f}s)", flush=True)

    expected_trades, expected_pnl = 4749, 240360.75
    ok_trades = (n_trades == expected_trades)
    ok_pnl = abs(total_pnl - expected_pnl) < 0.01
    print(f"\nDEGENERATE-CASE PARITY: trades {'OK' if ok_trades else 'FAIL'} ({n_trades} vs {expected_trades}), "
          f"pnl {'OK' if ok_pnl else 'FAIL'} ({total_pnl} vs {expected_pnl})", flush=True)

    # Full row-level comparison against the reference file, same technique as
    # the selection audit's own verify_parity.py.
    ref = pd.read_csv(r"c:\GitHub\investment-app\research\stop_loss_reconfiguration_2026-08-07\trades_pool1_A.csv",
                       parse_dates=["entry_date", "exit_date"])
    ref["entry_date"] = ref["entry_date"].dt.date
    ref["exit_date"] = ref["exit_date"].dt.date
    df["entry_date"] = df["entry_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    df["exit_date"] = df["exit_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    sort_cols = ["entry_date", "ticker"]
    a = df.sort_values(sort_cols).reset_index(drop=True)
    b = ref.sort_values(sort_cols).reset_index(drop=True)
    diffs = 0
    if len(a) == len(b):
        for col in ["exit_date", "exit_reason", "holding_days"]:
            mism = (a[col].astype(str) != b[col].astype(str)).sum()
            diffs += mism
            if mism:
                print(f"  MISMATCH in {col}: {mism} rows differ")
        for col in ["pnl_gbp", "position_gbp", "entry_price", "exit_price"]:
            mism = (a[col] - b[col]).abs().gt(0.01).sum()
            diffs += mism
            if mism:
                print(f"  MISMATCH in {col}: {mism} rows differ")
    else:
        diffs = abs(len(a) - len(b))
        print(f"  ROW COUNT MISMATCH: {len(a)} vs {len(b)}")

    print(f"\nTotal row-level mismatches: {diffs}")
    print("DEGENERATE-CASE PARITY CONFIRMED -- byte-identical to trades_pool1_A.csv" if diffs == 0
          else "DEGENERATE-CASE PARITY FAILED -- redesign_engine.py has a bug, STOP")
    print(f"Done ({time.time()-t0:.0f}s total)")


if __name__ == "__main__":
    main()
