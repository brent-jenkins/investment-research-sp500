"""Stage 2 of the sanity gate (Section 11): Pool 2 CLEAN baseline (Arm A,
unmodified production engine), established fresh -- this session's own
reference, not a stale prior number, per CLAUDE.md's repeatedly-confirmed
Pool-2-CLEAN drift finding. Also diffs against the most recent prior
Pool 2 CLEAN Arm A reference (trade sets, not just totals).

Stage 1 (Pool 1) already run separately: 4,752 trades / GBP241,673.18,
diagnosed and confirmed BENIGN against the established 4,749/GBP240,360.75
reference -- the 3-trade/GBP1,313 difference is fully explained by this
session's database being one calendar day fresher (2026-08-07 vs the
reference run's 2026-08-06), which shifts 11 END_OF_DATA boundary trades
(all pnl_gbp=0.0) by exactly one day; confirmed via trade-set diff, not
assumed from the total alone.
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


def _block_pass_count(trades, start, end):
    df = pd.DataFrame(trades)
    if df.empty:
        return 0, 0
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
    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = [c for c in data["caches"] if c is not None]
    t0 = time.time()
    trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, data["gspc_close"], PER_TRADE_PCT, MAX_DEPLOYED_PCT,
    )
    total_pnl = sum(t["pnl_gbp"] for t in trades)
    print(f"[pool2_clean/baseline] {len(caches)} tickers, {len(trades)} trades, total_pnl={total_pnl:.2f} ({time.time()-t0:.0f}s)", flush=True)
    df = pd.DataFrame(trades)
    df.to_csv(f"{OUT_DIR}/sanity_trades_pool2_clean.csv", index=False)

    passed, total_blocks = _block_pass_count(trades, "2007-01-01", "2026-08-07")
    print(f"Pool 2 CLEAN block-pass: {passed}/{total_blocks}", flush=True)

    prior_path = "research/gatewidth_prereg_2026-08-08/trades_pool2_clean_baseline.csv"
    try:
        prior = pd.read_csv(prior_path)
        prior_keys = set(zip(prior["ticker"], prior["entry_date"]))
        cur_keys = set(zip(df["ticker"], df["entry_date"].astype(str)))
        only_prior = prior_keys - cur_keys
        only_cur = cur_keys - prior_keys
        print(f"Trade count: prior={len(prior)} current={len(df)}", flush=True)
        print(f"Trade-set diff vs prior Pool 2 CLEAN reference ({prior_path}): only_in_prior={len(only_prior)} only_in_current={len(only_cur)}", flush=True)
        if only_prior:
            print("  sample only_in_prior:", list(only_prior)[:15], flush=True)
        if only_cur:
            print("  sample only_in_current:", list(only_cur)[:15], flush=True)
    except FileNotFoundError:
        print(f"Prior reference file not found at {prior_path} -- skipping trade-set diff.", flush=True)


if __name__ == "__main__":
    main()
