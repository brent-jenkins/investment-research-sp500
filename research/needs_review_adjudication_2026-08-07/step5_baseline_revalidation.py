"""Re-run the standard baseline 16-block validation on
get_research_stock_candidates() after the Wikipedia-cross-reference CLEAN
promotions -- per explicit request, since a CLEAN promotion is the first
step in this whole sequence that ADDS tickers to the trusted research
pool rather than removing them, and the sanity gate discipline established
throughout this project says any pool change should be re-confirmed, not
assumed safe.

get_research_stock_candidates() only ever excludes QUARANTINED, so the
pool changes by exactly the net QUARANTINED delta today: 162 -> 163
(CPWR). The 104 CLEAN promotions do not change pool membership at all
(NEEDS_REVIEW tickers were already included) -- this run is expected to
be very close to the prior 9/16 baseline, and is run to confirm that
directly rather than assume it.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import pickle
import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import compute_metrics_fixed, compute_peak_relative_drawdown_pct, make_blocks, \
    precompute_ticker, simulate_portfolio_compounding_mtm, slice_ticker
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/needs_review_adjudication_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")
MAX_DEPLOYED_PCT = 0.70


def main():
    t0 = time.time()
    candidate_tickers = sorted(universe.get_research_stock_candidates())
    print(f"get_research_stock_candidates() (post-Wikipedia-refinement): {len(candidate_tickers)} tickers", flush=True)

    caches = []
    n_ok = 0
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, END)
        if c is not None:
            n_ok += 1
        caches.append(c)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s, {n_ok} usable)", flush=True)
    print(f"  {n_ok}/{len(candidate_tickers)} usable frames ({time.time()-t0:.0f}s)", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    per_trade_pct = float(CONFIG.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    trades = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT)
    df_v = pd.DataFrame(trades)
    print(f"total_trades={len(df_v)} total_pnl_gbp={df_v['pnl_gbp'].sum():.2f} ({time.time()-t0:.0f}s)", flush=True)
    df_v.to_csv(f"{OUT_DIR}/trades_baseline_revalidation.csv", index=False)

    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 50000))
    blocks_raw = make_blocks(START, END, n_blocks=16)
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks_raw):
        strat_trades = df_v[(pd.to_datetime(df_v["exit_date"]) >= b_start) & (pd.to_datetime(df_v["exit_date"]) <= b_end)].to_dict("records")
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        if _block_passes(strat_m, sp_dd):
            n_pass += 1

    total_pnl = df_v["pnl_gbp"].sum()
    ending_equity = starting_capital + total_pnl
    years = (END - START).days / 365.25
    cagr = ((ending_equity / starting_capital) ** (1 / years) - 1) * 100
    peak_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)

    print(f"\n=== SANITY CHECK: blocks_pass={n_pass}/16 (expected ~9/16) ===", flush=True)
    print(f"CAGR={cagr:.2f}%  peak_relative_max_dd={peak_dd:.2f}%  ending_equity=£{ending_equity:,.2f}", flush=True)
    if abs(n_pass - 9) > 1:
        print("  *** GATE FAILED -- materially off from expected ~9/16. ***", flush=True)
    else:
        print("  Gate passed.", flush=True)


if __name__ == "__main__":
    main()
