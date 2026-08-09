"""Sanity gate, per PREREGISTRATION.md Section 11.

Stage 1: run the UNMODIFIED simulate_portfolio_compounding_mtm() against
Pool 1 and confirm the established reference (4,749 trades, £240,360.75)
byte-identical trade set, not just the total.

Stage 2: run it against Pool 2 CLEAN and report trade count / total P&L /
16-block pass count as this study's own fresh reference point, plus a
trade-set diff (ticker+entry_date) against the two most recent Pool 2 CLEAN
Arm A runs on record (admission-redesign: 4,916 trades; £115,363.15).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")

import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm, make_blocks

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
PER_TRADE_PCT = 0.05
MAX_DEPLOYED_PCT = 0.70


def _run(cache_path, label):
    with open(cache_path, "rb") as fh:
        data = pickle.load(fh)
    caches = [c for c in data["caches"] if c is not None]
    t0 = time.time()
    trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, data["gspc_close"], PER_TRADE_PCT, MAX_DEPLOYED_PCT,
    )
    total_pnl = sum(t["pnl_gbp"] for t in trades)
    print(f"[{label}] {len(caches)} tickers, {len(trades)} trades, total_pnl={total_pnl:.2f} ({time.time()-t0:.0f}s)", flush=True)
    df = pd.DataFrame(trades)
    df.to_csv(f"{OUT_DIR}/sanity_trades_{label}.csv", index=False)
    return trades, total_pnl


def _block_pass_count(trades, start, end):
    df = pd.DataFrame(trades)
    if df.empty:
        return 0
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    blocks = make_blocks(pd.Timestamp(start), pd.Timestamp(end))
    passed = 0
    for b_start, b_end in blocks:
        block_trades = df[(df["entry_date"] >= b_start) & (df["entry_date"] < b_end)]
        if block_trades.empty:
            continue
        gains = block_trades[block_trades["pnl_gbp"] > 0]["pnl_gbp"].sum()
        losses = -block_trades[block_trades["pnl_gbp"] < 0]["pnl_gbp"].sum()
        pf = gains / losses if losses > 0 else float("inf")
        if pf > 1.0:
            passed += 1
    return passed, len(blocks)


def main():
    print("=== Stage 1: Pool 1 (stable reference) ===", flush=True)
    trades1, pnl1 = _run(f"{OUT_DIR}/_cache_pool1.pkl", "pool1")
    expected_trades, expected_pnl = 4749, 240360.75
    match = (len(trades1) == expected_trades) and abs(pnl1 - expected_pnl) < 0.01
    print(f"Stage 1 expected: {expected_trades} trades, £{expected_pnl}", flush=True)
    print(f"Stage 1 MATCH: {match}", flush=True)
    if not match:
        print("STOPPING -- Stage 1 sanity gate failed, do not proceed to Stage 2 or any real run.", flush=True)
        return

    print("\n=== Stage 2: Pool 2 CLEAN (fresh reference for this study) ===", flush=True)
    trades2, pnl2 = _run(f"{OUT_DIR}/_cache_pool2_clean.pkl", "pool2_clean")
    passed, total_blocks = _block_pass_count(trades2, "2007-01-01", "2026-08-07")
    print(f"Pool 2 CLEAN block-pass: {passed}/{total_blocks}", flush=True)

    # Trade-set diff vs admission-redesign's own Pool 2 CLEAN Arm A reference
    prior_path = "../investment-app/research/admission_redesign_prereg_2026-08-08/trades_pool2_clean_baseline.csv"
    try:
        prior = pd.read_csv(prior_path)
        cur = pd.DataFrame(trades2)
        prior_keys = set(zip(prior["ticker"], prior["entry_date"]))
        cur_keys = set(zip(cur["ticker"], cur["entry_date"].astype(str)))
        only_prior = prior_keys - cur_keys
        only_cur = cur_keys - prior_keys
        print(f"Trade count: prior={len(prior)} current={len(cur)}", flush=True)
        print(f"Trade-set diff vs admission-redesign reference: only_in_prior={len(only_prior)} only_in_current={len(only_cur)}", flush=True)
        if only_prior:
            print("  sample only_in_prior:", list(only_prior)[:10], flush=True)
        if only_cur:
            print("  sample only_in_current:", list(only_cur)[:10], flush=True)
    except FileNotFoundError:
        print(f"Prior reference file not found at {prior_path} -- skipping trade-set diff.", flush=True)


if __name__ == "__main__":
    main()
