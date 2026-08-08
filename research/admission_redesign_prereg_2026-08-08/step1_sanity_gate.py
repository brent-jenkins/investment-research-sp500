"""Sanity gate (pre-registration §7): unmodified simulate_portfolio_compounding_mtm()
against the full, unrestricted Pool 2 cache must reproduce, exactly:
  4,922 trades, total P&L £114,990.04, 7/16 block-pass count.
If not, STOP and report before touching Pool 2 CLEAN or the redesign engine.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-admission-redesign")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import (
    simulate_portfolio_compounding_mtm, make_blocks, compute_metrics_fixed,
)
from analysis.validation import _cagr_dd_for_series, _block_passes

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/admission_redesign_prereg_2026-08-08"
MAX_DEPLOYED_PCT = 0.70
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")


def main():
    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_pool2_full.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    gspc_close_full = data["gspc_close_full"]
    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    print(f"Loaded cache ({time.time()-t0:.0f}s)", flush=True)

    trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT, collect_rejections=False,
    )
    df = pd.DataFrame(trades)
    n_trades = len(df)
    total_pnl = round(float(df["pnl_gbp"].sum()), 2)
    print(f"n_trades={n_trades}  total_pnl_gbp={total_pnl}  ({time.time()-t0:.0f}s)", flush=True)

    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
    blocks = make_blocks(START, END, n_blocks=16)
    n_pass = 0
    for b_start, b_end in blocks:
        strat_trades = df[(pd.to_datetime(df["exit_date"]) >= b_start) & (pd.to_datetime(df["exit_date"]) <= b_end)].to_dict("records")
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        if _block_passes(strat_m, sp_dd):
            n_pass += 1
    print(f"block_pass_count={n_pass}/16  ({time.time()-t0:.0f}s total)", flush=True)

    expected_trades, expected_pnl, expected_blocks = 4922, 114990.04, 7
    ok_trades = (n_trades == expected_trades)
    ok_pnl = abs(total_pnl - expected_pnl) < 0.01
    ok_blocks = (n_pass == expected_blocks)
    print(f"\nSANITY GATE: trades {'OK' if ok_trades else 'FAIL'} ({n_trades} vs {expected_trades}), "
          f"pnl {'OK' if ok_pnl else 'FAIL'} ({total_pnl} vs {expected_pnl}), "
          f"blocks {'OK' if ok_blocks else 'FAIL'} ({n_pass} vs {expected_blocks})", flush=True)
    print("SANITY GATE: ALL PASS -- safe to proceed" if (ok_trades and ok_pnl and ok_blocks)
          else "SANITY GATE: FAILED -- STOP, do not proceed to Pool 2 CLEAN or the redesign engine", flush=True)

    df.to_csv(f"{OUT_DIR}/sanity_gate_trades_pool2_full.csv", index=False)


if __name__ == "__main__":
    main()
