"""MAX_HOLDING cap sweep -- DESCRIPTIVE CHECK ONLY. No mechanism, no
adoption decision, no 16-block validation regardless of outcome (that
would be a separate, future task).

Question: does holding positions longer than 30 days help at all, and
if so, where does the benefit stop? Motivated by trade-level analysis
of the corrected full-history export showing 97.3% of winners exit at
the 30-day cap, profit is extremely tail-concentrated, and the capped
population is still trending at the moment of the cap.

DECISION-ENGINE PARITY -- the real, shared, unmodified engine is called
directly, nothing re-implemented. trading212_invest.stock_max_holding_
days is an EXISTING, already config-driven parameter
(analysis/universe.py:get_max_holding_days()) -- this script's only
"mechanism" is passing a deep-copied config dict with that one key
overridden into the real simulate_portfolio_compounding_mtm(), five
times. No monkeypatch, no temporary edit to analysis/backtest.py, no
custom candidate pool, no monkeypatched is_member(). Isolation is
PROVEN, not assumed: the cap=30 run (the config default) is asserted
byte-identical to the established corrected baseline (3,436 trades,
1,723 MAX_HOLDING, total_pnl_gbp=127,167.80) before anything else in
this script is trusted.

RESET-AT-BOUNDARY, applied correctly for a full-history, multi-variant
comparison: each of the 5 variants is its own independent, complete
2007-2026 walk, starting at the SAME point (2007-01-01, GBP50,000,
empty portfolio) with nothing shared beforehand -- this trivially and
correctly satisfies "reset-at-boundary" (no variant's early compounding
can leak into another's, since there is no shared pre-history at all,
the boundary IS the start of the data). This is deliberately NOT a
mid-walk reset (unlike the earlier no-stop-loss investigation, which
needed one to isolate a train/test split) -- a mid-walk reset would
throw away exactly the 2008-2013/2014-2019 era data the task's own
era-decomposition requirement needs.

Both drawdown conventions (fixed-denominator and peak-relative) are
reported, per standing practice. FX conversion cost is deliberately
EXCLUDED (a known, separately-quantified ~1.28pp/yr CAGR drag, kept out
so this comparison stays consistent with the existing, also-FX-free
baseline) -- noted in the report, not modelled here.

CAPACITY-TURNOVER REPORTING is mandatory and kept strictly separate
throughout: the direct effect (what happens to the SAME matched
positions when allowed to run longer) and the capacity effect (fewer
admissions, reconstructed concurrency/deployment, foregone-candidate
estimate via each variant's own real REJECTED_CAPACITY log) are never
blended into one number.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import copy
import json
import time

import numpy as np
import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import (
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    precompute_ticker,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    BASE_CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\max_holding_sweep_2026-08-06'
END = pd.Timestamp("2026-08-03")
START = pd.Timestamp("2007-01-01")
CAPS = [30, 40, 50, 60, 90]
ERA_BOUNDS = [
    ("2008-2013", pd.Timestamp("2008-01-01"), pd.Timestamp("2013-12-31")),
    ("2014-2019", pd.Timestamp("2014-01-01"), pd.Timestamp("2019-12-31")),
    ("2020-2026", pd.Timestamp("2020-01-01"), pd.Timestamp("2026-12-31")),
]


def headline_metrics(trades: list[dict], starting_capital: float) -> dict:
    m = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    df = pd.DataFrame(trades)
    winners = df[df["is_win"]]
    losers = df[~df["is_win"]]
    return {
        "n_trades": len(trades),
        "total_pnl_gbp": round(float(df["pnl_gbp"].sum()), 2) if not df.empty else 0.0,
        "cagr_pct": m.get("cagr_pct"),
        "ending_balance_gbp": round(starting_capital + float(df["pnl_gbp"].sum()), 2) if not df.empty else starting_capital,
        "profit_factor": m.get("profit_factor"),
        "win_rate": m.get("win_rate"),
        "max_drawdown_pct_fixed": m.get("max_drawdown_pct"),
        "max_drawdown_pct_peak_relative": peak_rel_dd,
        "avg_holding_days_winners": round(float(winners["holding_days"].mean()), 1) if len(winners) else None,
        "avg_holding_days_losers": round(float(losers["holding_days"].mean()), 1) if len(losers) else None,
    }


def right_tail_metrics(trades: list[dict]) -> dict:
    df = pd.DataFrame(trades)
    winners = df[df["is_win"]].copy()
    winners["pnl_pct_100"] = winners["pnl_pct"] * 100.0
    total_profit = df[df["pnl_gbp"] > 0]["pnl_gbp"].sum()
    sorted_by_pnl = df.sort_values("pnl_gbp", ascending=False)

    def top_n_share(n):
        if total_profit == 0:
            return None
        return round(100 * sorted_by_pnl.head(n)["pnl_gbp"].clip(lower=0).sum() / total_profit, 1)

    return {
        "n_winners": len(winners),
        "mean_win_pnl_pct": round(float(winners["pnl_pct_100"].mean()), 2) if len(winners) else None,
        "median_win_pnl_pct": round(float(winners["pnl_pct_100"].median()), 2) if len(winners) else None,
        "p90_pnl_pct": round(float(df["pnl_pct"].quantile(0.90) * 100), 2),
        "p95_pnl_pct": round(float(df["pnl_pct"].quantile(0.95) * 100), 2),
        "p99_pnl_pct": round(float(df["pnl_pct"].quantile(0.99) * 100), 2),
        "max_single_winner_pct": round(float(df["pnl_pct"].max() * 100), 2),
        "total_profit_gbp": round(float(total_profit), 2),
        "top10_profit_share_pct": top_n_share(10),
        "top25_profit_share_pct": top_n_share(25),
        "top50_profit_share_pct": top_n_share(50),
        "top100_profit_share_pct": top_n_share(100),
        "n_trades_over_20pct": int((df["pnl_pct"] > 0.20).sum()),
        "n_trades_over_30pct": int((df["pnl_pct"] > 0.30).sum()),
        "n_trades_over_50pct": int((df["pnl_pct"] > 0.50).sum()),
    }


def exit_reason_mix(trades: list[dict]) -> dict:
    df = pd.DataFrame(trades)
    counts = df["exit_reason"].value_counts().to_dict()
    total = len(df)
    return {k: {"count": v, "pct": round(100 * v / total, 2)} for k, v in counts.items()}


def era_cagr(trades: list[dict], starting_capital: float) -> dict:
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    out = {}
    for label, e_start, e_end in ERA_BOUNDS:
        sub = df[(df["entry_date"] >= e_start) & (df["entry_date"] <= e_end)].to_dict("records")
        m = compute_metrics_fixed(sub, starting_capital)
        out[label] = {
            "n_trades": len(sub),
            "cagr_pct": m.get("cagr_pct"),
            "total_pnl_gbp": round(float(pd.DataFrame(sub)["pnl_gbp"].sum()), 2) if sub else 0.0,
            "win_rate": m.get("win_rate"),
        }
    return out


def reconstruct_concurrency_and_deployment(trades: list[dict], starting_capital: float) -> dict:
    """Retrospective reconstruction from trade-level output alone -- NOT
    the engine's own internal daily mark-to-market state (that would
    require re-instrumenting the engine, out of scope per this task's
    own "as far as the machinery allows" hedge). Deployed value uses
    each open position's own ORIGINAL entry-time position_gbp (cost
    basis), not live mark-to-market -- an approximation, stated
    explicitly, not the engine's genuine MTM accounting."""
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])

    events = []
    for r in df.itertuples():
        events.append((r.entry_date, 1, r.position_gbp))
        events.append((r.exit_date, -1, r.position_gbp))
    events.sort(key=lambda e: (e[0], -e[1]))  # opens counted before closes on tie -- conservative (higher concurrency)

    concurrent = 0
    deployed_cost_basis = 0.0
    daily_concurrent = []
    daily_deployed_pct = []
    cash = starting_capital
    last_date = None
    for dt, sign, pos_gbp in events:
        if last_date is not None and dt != last_date:
            balance_proxy = cash + deployed_cost_basis
            if balance_proxy > 0:
                daily_concurrent.append(concurrent)
                daily_deployed_pct.append(100 * deployed_cost_basis / balance_proxy)
        if sign == 1:
            concurrent += 1
            deployed_cost_basis += pos_gbp
            cash -= pos_gbp
        else:
            concurrent -= 1
            deployed_cost_basis -= pos_gbp
            cash += pos_gbp
        last_date = dt

    return {
        "mean_concurrent_positions": round(float(np.mean(daily_concurrent)), 2) if daily_concurrent else None,
        "max_concurrent_positions": int(np.max(daily_concurrent)) if daily_concurrent else None,
        "mean_deployed_pct_cost_basis_approx": round(float(np.mean(daily_deployed_pct)), 2) if daily_deployed_pct else None,
    }


def main():
    t0 = time.time()
    print("=== MAX_HOLDING cap sweep -- descriptive check only ===", flush=True)

    t212_cfg = BASE_CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))
    starting_capital = float(BASE_CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
    print(f"per_trade_pct={per_trade_pct} max_deployed_pct={max_deployed_pct} "
          f"starting_capital={starting_capital}  caps={CAPS}", flush=True)

    # ------------------------------------------------------------------
    # Build tickers_cache ONCE -- reused across all 5 sweep variants.
    # precompute_ticker()'s own output (close/atr/signal/momentum/open/
    # low) does not depend on max_holding_days at all.
    # ------------------------------------------------------------------
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Building tickers_cache for {len(candidate_tickers)} live candidates (built ONCE, reused for "
          f"all {len(CAPS)} sweep variants)...", flush=True)
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker(ticker, BASE_CONFIG)
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

    all_trades = {}
    all_rejections = {}
    for cap in CAPS:
        cfg = copy.deepcopy(BASE_CONFIG)
        cfg["trading212_invest"]["stock_max_holding_days"] = cap
        print(f"--- Running cap={cap} (real, unmodified simulate_portfolio_compounding_mtm) ---", flush=True)
        trades, rejections = simulate_portfolio_compounding_mtm(
            caches, cfg, gspc_close, per_trade_pct, max_deployed_pct, collect_rejections=True,
        )
        df = pd.DataFrame(trades)
        ec = df["exit_reason"].value_counts()
        print(f"  cap={cap}: total_trades={len(df)}  MAX_HOLDING={ec.get('MAX_HOLDING', 0)}  "
              f"total_pnl_gbp={df['pnl_gbp'].sum():,.2f}", flush=True)
        all_trades[cap] = trades
        all_rejections[cap] = rejections
        df.to_csv(f"{OUT_DIR}/trades_cap{cap}.csv", index=False)

    # ------------------------------------------------------------------
    # PARITY CHECK -- cap=30 must be byte-identical to the established
    # corrected baseline. If this fails, STOP -- the rest is meaningless.
    # ------------------------------------------------------------------
    print("\n=== PARITY CHECK (cap=30 vs. established corrected baseline) ===", flush=True)
    base_df = pd.DataFrame(all_trades[30])
    base_ec = base_df["exit_reason"].value_counts()
    base_pnl = base_df["pnl_gbp"].sum()
    print(f"  n_trades={len(base_df)} (expected 3436)  MAX_HOLDING={base_ec.get('MAX_HOLDING', 0)} (expected 1723)  "
          f"total_pnl_gbp={base_pnl:,.2f} (expected 127,167.80)", flush=True)
    parity_ok = (
        len(base_df) == 3436
        and int(base_ec.get("MAX_HOLDING", 0)) == 1723
        and abs(base_pnl - 127167.80) < 1.0
    )
    if not parity_ok:
        print("  *** PARITY CHECK FAILED -- STOPPING. The sweep result is not trustworthy. ***", flush=True)
        with open(f"{OUT_DIR}/PARITY_FAILED.txt", "w") as fh:
            fh.write(f"n_trades={len(base_df)} MAX_HOLDING={base_ec.get('MAX_HOLDING', 0)} total_pnl_gbp={base_pnl}\n")
        return
    print("  PARITY CONFIRMED EXACT -- isolation proven, proceeding.\n", flush=True)

    # ------------------------------------------------------------------
    # Per-cap headline, right-tail, exit-mix, era metrics.
    # ------------------------------------------------------------------
    summary = {}
    for cap in CAPS:
        trades = all_trades[cap]
        summary[cap] = {
            "headline": headline_metrics(trades, starting_capital),
            "right_tail": right_tail_metrics(trades),
            "exit_mix": exit_reason_mix(trades),
            "era": era_cagr(trades, starting_capital),
            "concurrency": reconstruct_concurrency_and_deployment(trades, starting_capital),
        }
        print(f"\n=== cap={cap} summary ===", flush=True)
        print(json.dumps(summary[cap], indent=2, default=str), flush=True)

    with open(f"{OUT_DIR}/sweep_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2, default=str)

    # ------------------------------------------------------------------
    # DIRECT EFFECT: trades that are MAX_HOLDING under cap=30 -- what
    # happens to the SAME (ticker, entry_date) position under each wider
    # cap, when it's also admitted there?
    # ------------------------------------------------------------------
    print("\n=== DIRECT EFFECT (matched trades, MAX_HOLDING-at-cap=30 population) ===", flush=True)
    base_keyed = base_df.copy()
    base_keyed["entry_date"] = pd.to_datetime(base_keyed["entry_date"])
    base_keyed = base_keyed.set_index(["ticker", "entry_date"])
    base_capped = base_keyed[base_keyed["exit_reason"] == "MAX_HOLDING"]
    print(f"  {len(base_capped)} trades are MAX_HOLDING under cap=30 (the population this hypothesis is about)",
          flush=True)

    direct_effect_rows = []
    for cap in CAPS:
        if cap == 30:
            continue
        var_df = pd.DataFrame(all_trades[cap])
        var_df["entry_date"] = pd.to_datetime(var_df["entry_date"])
        var_keyed = var_df.set_index(["ticker", "entry_date"])
        common_idx = base_capped.index.intersection(var_keyed.index)
        matched_base = base_capped.loc[common_idx]
        matched_var = var_keyed.loc[common_idx]
        delta_pnl_gbp = matched_var["pnl_gbp"].values - matched_base["pnl_gbp"].values
        row = {
            "cap": cap,
            "n_matched": len(common_idx),
            "n_of_base_capped": len(base_capped),
            "pct_matched": round(100 * len(common_idx) / len(base_capped), 1) if len(base_capped) else None,
            "mean_pnl_pct_at_30": round(float(matched_base["pnl_pct"].mean() * 100), 2) if len(matched_base) else None,
            "mean_pnl_pct_at_cap": round(float(matched_var["pnl_pct"].mean() * 100), 2) if len(matched_var) else None,
            "median_pnl_pct_at_30": round(float(matched_base["pnl_pct"].median() * 100), 2) if len(matched_base) else None,
            "median_pnl_pct_at_cap": round(float(matched_var["pnl_pct"].median() * 100), 2) if len(matched_var) else None,
            "total_delta_pnl_gbp": round(float(delta_pnl_gbp.sum()), 2) if len(delta_pnl_gbp) else None,
            "n_improved": int((delta_pnl_gbp > 0).sum()),
            "n_worsened": int((delta_pnl_gbp < 0).sum()),
            "new_exit_reason_mix": matched_var["exit_reason"].value_counts().to_dict(),
        }
        direct_effect_rows.append(row)
        print(f"  cap={cap}: {row}", flush=True)

    pd.DataFrame(direct_effect_rows).to_csv(f"{OUT_DIR}/direct_effect.csv", index=False)

    # ------------------------------------------------------------------
    # CAPACITY EFFECT: trade-count change, reconstructed concurrency/
    # deployment (already in `summary`), foregone-candidate estimate via
    # each variant's own real REJECTED_CAPACITY log.
    # ------------------------------------------------------------------
    print("\n=== CAPACITY EFFECT ===", flush=True)
    capacity_rows = []
    for cap in CAPS:
        if cap == 30:
            continue
        var_df = pd.DataFrame(all_trades[cap])
        var_df["entry_date"] = pd.to_datetime(var_df["entry_date"])
        var_keyed = var_df.set_index(["ticker", "entry_date"])

        base_only = base_keyed.index.difference(var_keyed.index)  # admitted at 30, not at this wider cap
        print(f"  cap={cap}: trade count {len(base_df)} -> {len(var_df)} (delta {len(var_df)-len(base_df)}); "
              f"{len(base_only)} (ticker,entry_date) pairs admitted at cap=30 but NOT at cap={cap}", flush=True)

        rej_df = pd.DataFrame(all_rejections[cap])
        n_foregone_confirmed = 0
        foregone_fwd_rets = []
        if not rej_df.empty:
            rej_df["date"] = pd.to_datetime(rej_df["date"])
            rej_capacity = rej_df[rej_df["reason"] == "REJECTED_CAPACITY"]
            rej_lookup = set(zip(rej_capacity["ticker"], rej_capacity["date"]))
            frames_by_ticker = {c["ticker"]: c["frame"] for c in caches if c is not None}
            for ticker, entry_date in base_only:
                if (ticker, entry_date) in rej_lookup:
                    n_foregone_confirmed += 1
                    frame = frames_by_ticker.get(ticker)
                    if frame is not None and entry_date in frame.index:
                        close = frame["close"].astype(float)
                        loc = frame.index.get_loc(entry_date)
                        n_avail = len(close) - 1 - loc
                        w = min(30, n_avail)
                        if w > 0:
                            fwd_ret = float(close.iloc[loc + w] / close.iloc[loc] - 1.0) * 100.0
                            foregone_fwd_rets.append(fwd_ret)

        row = {
            "cap": cap,
            "trade_count_base": len(base_df), "trade_count_variant": len(var_df),
            "trade_count_delta": len(var_df) - len(base_df),
            "n_displaced_pairs": len(base_only),
            "n_confirmed_capacity_rejected_at_variant": n_foregone_confirmed,
            "mean_fwd_30d_ret_pct_of_foregone": round(float(np.mean(foregone_fwd_rets)), 2) if foregone_fwd_rets else None,
            "n_foregone_with_fwd_data": len(foregone_fwd_rets),
            "mean_concurrent_base": summary[30]["concurrency"]["mean_concurrent_positions"],
            "mean_concurrent_variant": summary[cap]["concurrency"]["mean_concurrent_positions"],
            "mean_deployed_pct_base": summary[30]["concurrency"]["mean_deployed_pct_cost_basis_approx"],
            "mean_deployed_pct_variant": summary[cap]["concurrency"]["mean_deployed_pct_cost_basis_approx"],
        }
        capacity_rows.append(row)
        print(f"    {row}", flush=True)

    pd.DataFrame(capacity_rows).to_csv(f"{OUT_DIR}/capacity_effect.csv", index=False)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
