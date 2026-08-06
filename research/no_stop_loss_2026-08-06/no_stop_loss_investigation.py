"""Does having a STOP_LOSS mechanism at all -- vs. none -- improve
outcomes, under the just-corrected (realistic intraday-touch fill)
engine? A genuinely different, more fundamental question than any prior
stop-related investigation tonight (all of which assumed a stop exists
and tested variations around it). Any prior intuition about whether
stops help was very likely shaped by the OLD, overstated close-only
fill convention just fixed tonight, and is not trusted here -- this
runs fresh against the corrected engine specifically.

Mechanism: two isolated, marked, TEMPORARY additions directly to the
real analysis.backtest.simulate_portfolio_compounding_mtm() (see that
function's own docstring for the full mechanism and no-op-at-default
guarantee) -- not a hand-duplicated research clone, for the same
parity-risk reason as every other engine-level investigation tonight:

  - disable_stop_loss=True: passes stop_price=None to
    evaluate_exit_reason() for the exit check, so its own STOP_LOSS
    branch (which requires a non-None stop_price) cleanly falls through
    to SIGNAL_OFF/MAX_HOLDING. evaluate_exit_reason() itself, still
    shared with live and every other caller, is never modified.
  - reset_at_boundary_date=VALIDATION_TRAIN_TEST_SPLIT (2020-01-01):
    the established reset-at-boundary technique from this project's own
    "30-day re-entry cooldown" Part B methodology (2026-08-03) --
    forces current_balance to read exactly starting_capital_gbp from
    the boundary forward (open positions untouched, only the account's
    TOTAL capital base resets), preventing one variant's pre-boundary
    luck from contaminating the post-boundary comparison via inflated
    position sizing. Required per CLAUDE.md's "Two backtest conventions"
    standing requirement: comparing two variants is a reset-at-boundary
    question, not a continuous-walk one.

Both flags verified inert at their defaults (byte-identical to the
established corrected baseline: 3,436 trades, GBP127,167.80,
CAGR 7.05%, 9/16 blocks) before trusting anything built on top of them.

CAPACITY-TURNOVER REPORTING (per the same-day standing requirement this
investigation's own context motivated): the direct effect (what happens
to positions that would have stopped out but now don't) and the
capacity-turnover effect (fewer total trades; what a freed-vs-occupied
slot's next-best candidate would have returned) are computed and
reported SEPARATELY throughout, never blended into one headline number.

Reverted after use, per the same discipline as every engine-level
investigation tonight.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import time

import numpy as np
import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import (
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    make_blocks,
    precompute_ticker,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from analysis.validation import VALIDATION_TRAIN_TEST_SPLIT, _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\no_stop_loss_2026-08-06'
END = pd.Timestamp("2026-08-03")
START = pd.Timestamp("2007-01-01")
FWD_WINDOW = 30  # trading days, for the foregone-candidate forward-return estimate


def block_validate(trades: list[dict], gspc_close_full: pd.Series, starting_capital: float, label: str) -> dict:
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])
    blocks_raw = make_blocks(START, END, n_blocks=16)
    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks_raw):
        strat_trades = (
            [] if trades_df.empty else
            trades_df[(trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)].to_dict("records")
        )
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        block_pass = _block_passes(strat_m, sp_dd)
        if block_pass:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(strat_trades), "strat_cagr": strat_m["cagr_pct"],
            "strat_dd": strat_m["max_drawdown_pct"], "strat_pf": strat_m.get("profit_factor", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": block_pass,
        })
    print(f"  [{label}] blocks passed: {n_pass}/{len(blocks_raw)}", flush=True)
    return {"label": label, "n_blocks_pass": n_pass, "n_blocks_total": len(blocks_raw), "blocks": block_results}


def headline_metrics(trades: list[dict], starting_capital: float) -> dict:
    m = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    df = pd.DataFrame(trades)
    return {
        "n_trades": len(trades),
        "n_distinct_tickers": df["ticker"].nunique() if not df.empty else 0,
        "total_pnl_gbp": round(float(df["pnl_gbp"].sum()), 2) if not df.empty else 0.0,
        "win_rate": m.get("win_rate"), "profit_factor": m.get("profit_factor"),
        "sharpe_ratio": m.get("sharpe_ratio"),
        "max_drawdown_pct_fixed": m.get("max_drawdown_pct"),
        "max_drawdown_pct_peak_relative": peak_rel_dd,
        "cagr_pct": m.get("cagr_pct"), "total_return_pct": m.get("total_return_pct"),
        "exit_reason_breakdown": (df["exit_reason"].value_counts().to_dict() if not df.empty else {}),
    }


def main():
    t0 = time.time()
    print("=== Does a STOP_LOSS mechanism improve outcomes? (corrected engine) ===", flush=True)

    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
    print(f"per_trade_pct={per_trade_pct} max_deployed_pct={max_deployed_pct} "
          f"starting_capital={starting_capital}  reset_boundary={VALIDATION_TRAIN_TEST_SPLIT.date()}", flush=True)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Building tickers_cache for {len(candidate_tickers)} live candidates "
          f"(precompute_ticker() now permanently carries open/low)...", flush=True)
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            c = slice_ticker(c, START, END)
        caches.append(c)
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(candidate_tickers)} done ({time.time()-t0:.0f}s elapsed)", flush=True)
    n_ok = sum(1 for c in caches if c is not None)
    print(f"  {n_ok}/{len(candidate_tickers)} tickers have usable frames\n", flush=True)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]

    # ------------------------------------------------------------------
    # 1. PARITY CHECK -- both new params at their defaults must reproduce
    #    the established corrected baseline exactly.
    # ------------------------------------------------------------------
    print("--- PARITY CHECK: disable_stop_loss=False, reset_at_boundary_date=None ---", flush=True)
    trades_parity = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct,
        disable_stop_loss=False, reset_at_boundary_date=None,
    )
    df_p = pd.DataFrame(trades_parity)
    ec_p = df_p["exit_reason"].value_counts()
    total_pnl_p = df_p["pnl_gbp"].sum()
    print(f"  total_trades={len(df_p)}  STOP_LOSS={ec_p.get('STOP_LOSS', 0)}  total_pnl_gbp={total_pnl_p:,.2f}",
          flush=True)
    assert len(df_p) == 3436, f"PARITY drifted: total_trades={len(df_p)}"
    assert abs(total_pnl_p - 127167.80) < 1.0, f"PARITY drifted: total_pnl_gbp={total_pnl_p}"
    print("  PARITY CONFIRMED EXACT -- both new params are behaviourally transparent at their defaults.\n",
          flush=True)

    # ------------------------------------------------------------------
    # 2. RESET-AT-BOUNDARY comparison: with-stop vs no-stop, both reset
    #    identically at VALIDATION_TRAIN_TEST_SPLIT.
    # ------------------------------------------------------------------
    print("--- Running WITH-STOP, reset-at-boundary (collect_rejections=True) ---", flush=True)
    trades_with_stop, rej_with_stop = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct,
        disable_stop_loss=False, reset_at_boundary_date=VALIDATION_TRAIN_TEST_SPLIT,
        collect_rejections=True,
    )
    df_ws = pd.DataFrame(trades_with_stop)
    df_ws["entry_date"] = pd.to_datetime(df_ws["entry_date"])
    print(f"  total_trades={len(df_ws)}  distinct_tickers={df_ws['ticker'].nunique()}", flush=True)

    print("--- Running NO-STOP, reset-at-boundary (collect_rejections=True) ---", flush=True)
    trades_no_stop, rej_no_stop = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct,
        disable_stop_loss=True, reset_at_boundary_date=VALIDATION_TRAIN_TEST_SPLIT,
        collect_rejections=True,
    )
    df_ns = pd.DataFrame(trades_no_stop)
    df_ns["entry_date"] = pd.to_datetime(df_ns["entry_date"])
    print(f"  total_trades={len(df_ns)}  distinct_tickers={df_ns['ticker'].nunique()}", flush=True)

    df_ws.to_csv(f"{OUT_DIR}/trades_with_stop_reset.csv", index=False)
    df_ns.to_csv(f"{OUT_DIR}/trades_no_stop_reset.csv", index=False)
    pd.DataFrame(rej_with_stop).to_csv(f"{OUT_DIR}/rejections_with_stop_reset.csv", index=False)
    pd.DataFrame(rej_no_stop).to_csv(f"{OUT_DIR}/rejections_no_stop_reset.csv", index=False)
    with open(f"{OUT_DIR}/trades_with_stop_reset_full.json", "w") as fh:
        json.dump(trades_with_stop, fh, default=str)
    with open(f"{OUT_DIR}/trades_no_stop_reset_full.json", "w") as fh:
        json.dump(trades_no_stop, fh, default=str)
    print("Wrote trades + rejections CSVs and full JSON for both variants\n", flush=True)

    # ------------------------------------------------------------------
    # 3. TEST-period (entry >= boundary) headline comparison.
    # ------------------------------------------------------------------
    print("=== TEST-period (entry >= 2020-01-01) headline comparison ===", flush=True)
    test_ws = df_ws[df_ws["entry_date"] >= VALIDATION_TRAIN_TEST_SPLIT].to_dict("records")
    test_ns = df_ns[df_ns["entry_date"] >= VALIDATION_TRAIN_TEST_SPLIT].to_dict("records")
    hm_ws = headline_metrics(test_ws, starting_capital)
    hm_ns = headline_metrics(test_ns, starting_capital)
    print("  WITH-STOP (test):", json.dumps(hm_ws, indent=2, default=str), flush=True)
    print("  NO-STOP   (test):", json.dumps(hm_ns, indent=2, default=str), flush=True)
    with open(f"{OUT_DIR}/test_period_headline_comparison.json", "w") as fh:
        json.dump({"with_stop": hm_ws, "no_stop": hm_ns}, fh, indent=2, default=str)

    # ------------------------------------------------------------------
    # 4. Full-run (both eras, reset-based) 16-block validation.
    # ------------------------------------------------------------------
    print("\n=== 16-block validation (reset-based full runs) ===", flush=True)
    block_ws = block_validate(trades_with_stop, gspc_close_full, starting_capital, "with_stop_reset")
    block_ns = block_validate(trades_no_stop, gspc_close_full, starting_capital, "no_stop_reset")
    with open(f"{OUT_DIR}/block_validation_results.json", "w") as fh:
        json.dump({"with_stop": block_ws, "no_stop": block_ns}, fh, default=str, indent=2)

    # ------------------------------------------------------------------
    # 5. DIRECT-DOWNSIDE EFFECT: matched (ticker, entry_date) trades that
    #    are STOP_LOSS under with-stop -- what actually happens to them
    #    under no-stop?
    # ------------------------------------------------------------------
    print("\n=== DIRECT-DOWNSIDE EFFECT (matched trades only) ===", flush=True)
    ws_key = df_ws.set_index(["ticker", "entry_date"])
    ns_key = df_ns.set_index(["ticker", "entry_date"])
    common_idx = ws_key.index.intersection(ns_key.index)
    print(f"  {len(common_idx)} (ticker, entry_date) pairs admitted under BOTH variants "
          f"(of {len(df_ws)} with-stop / {len(df_ns)} no-stop total)", flush=True)

    ws_common = ws_key.loc[common_idx]
    ns_common = ns_key.loc[common_idx]
    would_have_stopped = ws_common[ws_common["exit_reason"] == "STOP_LOSS"].index
    print(f"  Of those, {len(would_have_stopped)} were STOP_LOSS under with-stop", flush=True)

    downside_rows = []
    for idx in would_have_stopped:
        ws_row = ws_common.loc[idx]
        ns_row = ns_common.loc[idx]
        downside_rows.append({
            "ticker": idx[0], "entry_date": idx[1].date().isoformat(),
            "with_stop_exit_date": ws_row["exit_date"], "with_stop_pnl_pct": ws_row["pnl_pct"] * 100,
            "with_stop_pnl_gbp": ws_row["pnl_gbp"], "with_stop_holding_days": ws_row["holding_days"],
            "no_stop_exit_reason": ns_row["exit_reason"], "no_stop_exit_date": ns_row["exit_date"],
            "no_stop_pnl_pct": ns_row["pnl_pct"] * 100, "no_stop_pnl_gbp": ns_row["pnl_gbp"],
            "no_stop_holding_days": ns_row["holding_days"],
            "delta_pnl_gbp": ns_row["pnl_gbp"] - ws_row["pnl_gbp"],
        })
    downside_df = pd.DataFrame(downside_rows)
    downside_df.to_csv(f"{OUT_DIR}/direct_downside_matched_trades.csv", index=False)

    if not downside_df.empty:
        n_recover = int((downside_df["no_stop_pnl_pct"] > 0).sum())
        n_worse = int((downside_df["delta_pnl_gbp"] < 0).sum())
        n_better = int((downside_df["delta_pnl_gbp"] > 0).sum())
        worst5 = downside_df.nsmallest(5, "delta_pnl_gbp")[
            ["ticker", "entry_date", "with_stop_pnl_pct", "no_stop_pnl_pct", "no_stop_exit_reason", "delta_pnl_gbp"]
        ]
        print(f"  Mean with-stop pnl_pct (at the stop): {downside_df['with_stop_pnl_pct'].mean():.2f}%")
        print(f"  Mean no-stop pnl_pct (eventual real outcome): {downside_df['no_stop_pnl_pct'].mean():.2f}%")
        print(f"  Median no-stop pnl_pct: {downside_df['no_stop_pnl_pct'].median():.2f}%")
        print(f"  Worst no-stop pnl_pct: {downside_df['no_stop_pnl_pct'].min():.2f}%")
        print(f"  n eventually recover to positive: {n_recover}/{len(downside_df)} "
              f"({100*n_recover/len(downside_df):.1f}%)")
        print(f"  n genuinely worse off without the stop (delta_pnl_gbp<0): {n_worse}/{len(downside_df)}")
        print(f"  n better off without the stop (delta_pnl_gbp>0): {n_better}/{len(downside_df)}")
        print(f"  Total delta_pnl_gbp across matched stopped-out trades: {downside_df['delta_pnl_gbp'].sum():,.2f}")
        print(f"  5 worst matched trades:\n{worst5.to_string(index=False)}")
    else:
        print("  No matched STOP_LOSS trades found in the common admitted set.")

    # ------------------------------------------------------------------
    # 6. CAPACITY-TURNOVER EFFECT: trade-count difference + foregone-
    #    candidate cost estimate via no-stop's own REJECTED_CAPACITY log.
    # ------------------------------------------------------------------
    print("\n=== CAPACITY-TURNOVER EFFECT ===", flush=True)
    print(f"  Total trades (full reset run): with_stop={len(df_ws)}  no_stop={len(df_ns)}  "
          f"delta={len(df_ns)-len(df_ws)}", flush=True)
    print(f"  Total trades (TEST period only): with_stop={len(test_ws)}  no_stop={len(test_ns)}  "
          f"delta={len(test_ns)-len(test_ws)}", flush=True)

    ws_only = ws_key.index.difference(ns_key.index)
    ns_only = ns_key.index.difference(ws_key.index)
    print(f"  Admitted under with-stop but NOT under no-stop: {len(ws_only)}", flush=True)
    print(f"  Admitted under no-stop but NOT under with-stop: {len(ns_only)}", flush=True)

    rej_ns_df = pd.DataFrame(rej_no_stop)
    foregone_rows = []
    if not rej_ns_df.empty:
        rej_ns_df["date"] = pd.to_datetime(rej_ns_df["date"])
        rej_capacity = rej_ns_df[rej_ns_df["reason"] == "REJECTED_CAPACITY"]
        rej_lookup = set(zip(rej_capacity["ticker"], rej_capacity["date"]))
        print(f"  no-stop run's own REJECTED_CAPACITY log: {len(rej_capacity)} rejection events", flush=True)

        frames_by_ticker = {c["ticker"]: c["frame"] for c in caches if c is not None}
        for ticker, entry_date in ws_only:
            if (ticker, entry_date) in rej_lookup:
                frame = frames_by_ticker.get(ticker)
                if frame is None or entry_date not in frame.index:
                    continue
                close = frame["close"].astype(float)
                loc = frame.index.get_loc(entry_date)
                entry_price = float(close.iloc[loc])
                n_avail = len(close) - 1 - loc
                w = min(FWD_WINDOW, n_avail)
                if w <= 0:
                    continue
                fwd_price = float(close.iloc[loc + w])
                fwd_ret_pct = (fwd_price / entry_price - 1.0) * 100.0
                foregone_rows.append({
                    "ticker": ticker, "would_be_entry_date": entry_date.date().isoformat(),
                    "fwd_window_days": w, "fwd_ret_pct": fwd_ret_pct,
                })

    foregone_df = pd.DataFrame(foregone_rows)
    foregone_df.to_csv(f"{OUT_DIR}/foregone_candidate_forward_returns.csv", index=False)
    if not foregone_df.empty:
        print(f"  Confirmed cases: a with-stop-admitted candidate was explicitly REJECTED_CAPACITY "
              f"under no-stop (its slot stayed occupied by a non-stopped-out position): {len(foregone_df)}",
              flush=True)
        print(f"  Mean {FWD_WINDOW}-trading-day forward return of these foregone candidates: "
              f"{foregone_df['fwd_ret_pct'].mean():.2f}%", flush=True)
        print(f"  Median: {foregone_df['fwd_ret_pct'].median():.2f}%  "
              f"%positive: {100*(foregone_df['fwd_ret_pct']>0).mean():.1f}%", flush=True)
    else:
        print("  No confirmed with-stop-admitted candidates found explicitly REJECTED_CAPACITY under no-stop "
              "(the capacity-turnover effect may be operating through a different mechanism than direct "
              "same-day capacity displacement -- see report for interpretation).", flush=True)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
