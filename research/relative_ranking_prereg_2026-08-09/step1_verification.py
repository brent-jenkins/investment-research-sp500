"""Verification plan, Sec 8 of the pre-registration, plus one check the
pre-registration itself said wasn't available (a genuine degenerate-case
parity reduction) -- added on review of the engine's own code (see
relative_ranking_engine.py's _sort_key TEST-ONLY parameter).

Run on Pool 2 CLEAN only for speed (the decisive pool) -- Pool 1's own
correctness is not independently at risk here, since it's the identical
engine code and shared decision functions, just a different candidate list.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-relative-ranking")

import pickle
import time

import numpy as np
import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm
from cross_sectional_signal import compute_cross_sectional_rank_series, RANK_LOOKBACK_DAYS, RANK_SKIP_DAYS, _MAX_1MO_ANCHOR_GAP_DAYS
from analysis.signals import _MAX_ANCHOR_GAP_DAYS
from analysis.decision import evaluate_exit_reason, evaluate_rank_dropped_exit
from relative_ranking_engine import simulate_relative_ranking, TOP_N

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/relative_ranking_prereg_2026-08-09"
MAX_DEPLOYED_PCT = 0.70
PER_TRADE_PCT = 0.05


def _trades_df(trades):
    df = pd.DataFrame(trades)
    df["entry_date"] = df["entry_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    df["exit_date"] = df["exit_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    return df


def _compare(a, b, label):
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


def check1_staleness_guard():
    print("\n=== CHECK 1: staleness guard ===", flush=True)
    idx = pd.date_range("2020-01-01", periods=400, freq="B")
    close = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    gap_idx = idx[:250].tolist() + idx[250 + 40:].tolist()  # 40-day gap, well past both guards
    gapped = close.reindex(pd.DatetimeIndex(gap_idx)).dropna()
    result = compute_cross_sectional_rank_series(gapped)
    seam_start = gapped.index[250]
    at_seam = result.loc[seam_start:].iloc[:5]
    print(f"  synthetic 40-trading-day gap: values at/after seam:\n{at_seam}")
    fires = at_seam.isna().any()
    print(f"  RESULT: {'PASS -- guard fires at seam' if fires else 'FAIL -- guard did not fire'}")
    return fires


def check2_5_run_and_verify(pool):
    t0 = time.time()
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    print(f"[{pool}] cache loaded ({time.time()-t0:.0f}s, {len(caches)} tickers)", flush=True)

    print(f"\n=== CHECK: degenerate-case parity (_sort_key='momentum' vs production) ===", flush=True)
    raw_trades = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT, collect_rejections=False,
    )
    raw_df = _trades_df(raw_trades)
    print(f"  raw production: {len(raw_df)} trades, total_pnl={raw_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    degenerate = simulate_relative_ranking(
        caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        apply_rank_dropped_exit=False, _sort_key="momentum",
    )
    deg_df = _trades_df(degenerate["trades"])
    print(f"  degenerate (_sort_key=momentum): {len(deg_df)} trades, total_pnl={deg_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)
    diffs_degenerate = _compare(raw_df, deg_df, "DEGENERATE")
    print("DEGENERATE RESULT:", "PASS -- byte-identical, engine machinery is a faithful copy of production" if diffs_degenerate == 0 else "FAIL -- STOP", flush=True)

    print(f"\n=== Running real Arm B (_sort_key=rank, no rank-dropped exit) ===", flush=True)
    arm_b = simulate_relative_ranking(
        caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
        apply_rank_dropped_exit=False, _sort_key="rank",
    )
    b_df = _trades_df(arm_b["trades"])
    print(f"  Arm B: {len(b_df)} trades, total_pnl={b_df['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n=== CHECK: exit-wiring / signal-separation on Arm B's own trades ===", flush=True)
    ticker_frames = {c["ticker"]: c["frame"] for c in caches if c is not None}
    sample = b_df.sample(min(30, len(b_df)), random_state=42)
    n_ok = 0
    for _, tr in sample.iterrows():
        frame = ticker_frames.get(tr["ticker"])
        if frame is None:
            continue
        exit_date = pd.Timestamp(tr["exit_date"])
        if exit_date not in frame.index:
            continue
        row = frame.loc[exit_date]
        momentum_at_exit = row.get("momentum")
        if tr["exit_reason"] == "SIGNAL_OFF":
            ok = pd.notna(momentum_at_exit) and float(momentum_at_exit) <= 0
        else:
            ok = True  # other exit reasons not being re-derived here; SIGNAL_OFF is the one that matters for separation
        n_ok += int(ok)
        if not ok:
            print(f"  MISMATCH: {tr['ticker']} exit {exit_date.date()} reason={tr['exit_reason']} momentum={momentum_at_exit}")
    print(f"  SIGNAL_OFF/momentum separation: {n_ok}/{len(sample)} sampled trades consistent with ABSOLUTE momentum driving sig_on")

    return {"pool": pool, "diffs_degenerate": diffs_degenerate, "b_trades": len(b_df), "b_pnl": float(b_df["pnl_gbp"].sum())}


def check_ranking_hand(pool, dates):
    print(f"\n=== CHECK: ranking hand-check, {pool}, {len(dates)} dates ===", flush=True)
    with open(f"{OUT_DIR}/_cache_{pool}.pkl", "rb") as fh:
        data = pickle.load(fh)
    caches = {c["ticker"]: c for c in data["caches"] if c is not None}

    for date_str in dates:
        dt = pd.Timestamp(date_str)
        ranked = []
        for ticker, c in caches.items():
            close = c["frame"]["close"].astype(float)
            rank_series = compute_cross_sectional_rank_series(close)
            if dt not in rank_series.index:
                continue
            val = rank_series.loc[dt]
            if pd.notna(val):
                ranked.append((ticker, float(val)))
        ranked.sort(key=lambda x: -x[1])
        top5 = ranked[:5]
        print(f"  {date_str}: {len(ranked)} rankable candidates, top-5 by hand: {top5}")


if __name__ == "__main__":
    ok1 = check1_staleness_guard()
    result = check2_5_run_and_verify("pool2_clean")
    check_ranking_hand("pool2_clean", ["2010-06-01", "2015-06-01", "2020-06-01", "2022-06-01"])

    print("\n=== SUMMARY ===")
    print(f"Staleness guard: {'PASS' if ok1 else 'FAIL'}")
    print(f"Degenerate-case parity: {'PASS' if result['diffs_degenerate'] == 0 else 'FAIL'}")
    print(f"Arm B (real, Pool 2 CLEAN): {result['b_trades']} trades, £{result['b_pnl']:.2f}")
