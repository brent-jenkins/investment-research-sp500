"""Step 2 -- test TWO genuinely different entry-time "extension ceiling"
mechanisms against the real engine, only run because Step 1 (step1_
descriptive.py) cleared its own gate: a real, MAX_HOLDING-isolated,
momentum-controlled, three-MA-window-consistent, outlier-ROBUST (not
merely outlier-tolerant -- the effect got STRONGER after trimming)
relationship between price-vs-50-day-MA at entry and trade outcome,
concentrated in but not exclusive to the dominant 2010-2019 regime.

Two mechanisms, tested separately (per the task's explicit instruction
that a gate and a re-ranking are genuinely different interventions,
given momentum-ranked, capacity-constrained admission -- a gate can only
ever REMOVE a bad candidate; it cannot let a better one win a slot
something else already consumed. Re-ranking can):

1. GATE: block a candidate outright if its price sits more than
   extension_threshold_pct=15.0 above its own 50-day MA at entry.
   Threshold chosen from the real historical admitted-trade population's
   own pos_vs_ma50_pct distribution (research/ma_extension_ceiling_2026-
   08-06/trades_with_ma_extension.csv): 15% sits between that
   population's own 85th (14.6%) and 90th (17.2%) percentile -- a
   defensible "clearly stretched" cutoff, stated explicitly as a first,
   reasonable choice, not a carefully-derived optimum (same standard
   this project has used for every other Step 2 parameter tonight).

2. RERANK: adjusted_score = momentum * (1 - min(1, max(0, ext_pct/100) *
   extension_discount_coef)), extension_discount_coef=1.0 -- a
   proportional discount (not a fixed subtraction), chosen so the
   penalty scales with the candidate's own momentum magnitude (a very
   high-momentum, moderately-extended candidate is not automatically
   crushed by a small absolute penalty the way a fixed subtraction would
   under- or over-penalize across the huge momentum range this system's
   candidates span -- see Step 1's own band investigations, momentum
   values from a few percent to 1000%+).

MECHANISM IMPLEMENTATION: a genuinely additive, isolated, marked change
to the REAL analysis.backtest.simulate_portfolio_compounding_mtm() --
NOT a hand-duplicated research clone (which would itself be exactly the
kind of drift risk CLAUDE.md's "Research scripts must verify decision-
engine parity" section warns about). Three new keyword parameters
(extension_mode/extension_threshold_pct/extension_discount_coef) were
added directly to the real function, default extension_mode="none"
producing byte-identical behaviour to before this addition (verified
below by reproducing the exact established 3,161-trade/1,796-
MAX_HOLDING/56.8% baseline). This is a TEMPORARY, marked addition
(see the function's own docstring) -- to be reverted after this
investigation concludes unless the evidence is exceptional enough to
warrant production adoption with explicit sign-off.

Backtest convention: each variant (baseline/gate/rerank) is run as ONE
continuous 2007-2026 walk -- necessary to preserve genuine capacity/
compounding dynamics (the whole point of testing admission-order
interaction). The 16-INDEPENDENT-WINDOW block validation applied
afterward (make_blocks()+_block_passes(), reused verbatim from
analysis.validation, never reimplemented) uses compute_metrics_fixed()'s
fixed-starting-capital-per-block convention -- this is what actually
delivers the reset-at-boundary-equivalent fairness this project's
"Two backtest conventions" standing requirement calls for in a variant
comparison (each block's metrics are computed on a FIXED capital basis,
not compounded across blocks), without needing a literal per-block
re-simulation -- exactly how every other block-validated variant
comparison tonight (momentum_sizing_multiplier, price_sanity_sweep, ...)
already does this.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import time

import pandas as pd
import yaml

from analysis import universe
from analysis.backtest import (
    compute_metrics_fixed,
    make_blocks,
    precompute_ticker,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\ma_extension_ceiling_2026-08-06'
END = pd.Timestamp("2026-08-03")
START = pd.Timestamp("2007-01-01")
EXTENSION_THRESHOLD_PCT = 15.0
EXTENSION_DISCOUNT_COEF = 1.0


def precompute_ticker_with_ma50(ticker: str, config: dict):
    """Same as analysis.backtest.precompute_ticker(), plus a "ma_50"
    column -- the ONLY extra data the temporary extension_mode hook
    needs. Production's own precompute_ticker() is untouched; this is a
    separate, parallel function used only by this research script's own
    tickers_cache construction."""
    cached = precompute_ticker(ticker, config)
    if cached is None:
        return None
    frame = cached["frame"]
    frame = frame.copy()
    frame["ma_50"] = frame["close"].rolling(50).mean()
    return {"ticker": ticker, "frame": frame}


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


def main():
    t0 = time.time()
    print("=== Step 2: extension-ceiling GATE vs RERANK mechanism validation ===", flush=True)

    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
    print(f"per_trade_pct={per_trade_pct} max_deployed_pct={max_deployed_pct} "
          f"starting_capital={starting_capital}", flush=True)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Building tickers_cache (with ma_50) for {len(candidate_tickers)} live candidates...", flush=True)
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker_with_ma50(ticker, CONFIG)
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

    variants = {}

    print("--- Running BASELINE (extension_mode='none') -- must reproduce established baseline exactly ---", flush=True)
    trades_baseline = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct, extension_mode="none",
    )
    df_b = pd.DataFrame(trades_baseline)
    ec_b = df_b["exit_reason"].value_counts()
    print(f"  total_trades={len(df_b)}  MAX_HOLDING={ec_b.get('MAX_HOLDING', 0)} "
          f"({100*ec_b.get('MAX_HOLDING', 0)/len(df_b):.1f}%)", flush=True)
    assert len(df_b) == 3161, f"BASELINE drifted from established total: {len(df_b)}"
    assert int(ec_b.get("MAX_HOLDING", 0)) == 1796, f"BASELINE drifted from established MAX_HOLDING: {ec_b.get('MAX_HOLDING')}"
    print("  Baseline reproduction CONFIRMED EXACT -- the temporary addition is behaviourally transparent "
          "at its default parameters.\n", flush=True)
    variants["baseline"] = trades_baseline

    print(f"--- Running GATE (extension_mode='gate', threshold={EXTENSION_THRESHOLD_PCT}%) ---", flush=True)
    trades_gate = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct,
        extension_mode="gate", extension_threshold_pct=EXTENSION_THRESHOLD_PCT,
    )
    df_g = pd.DataFrame(trades_gate)
    ec_g = df_g["exit_reason"].value_counts()
    print(f"  total_trades={len(df_g)}  MAX_HOLDING={ec_g.get('MAX_HOLDING', 0)} "
          f"distinct_tickers={df_g['ticker'].nunique()}\n", flush=True)
    variants["gate"] = trades_gate

    print(f"--- Running RERANK (extension_mode='rerank', discount_coef={EXTENSION_DISCOUNT_COEF}) ---", flush=True)
    trades_rerank = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct,
        extension_mode="rerank", extension_discount_coef=EXTENSION_DISCOUNT_COEF,
    )
    df_r = pd.DataFrame(trades_rerank)
    ec_r = df_r["exit_reason"].value_counts()
    print(f"  total_trades={len(df_r)}  MAX_HOLDING={ec_r.get('MAX_HOLDING', 0)} "
          f"distinct_tickers={df_r['ticker'].nunique()}\n", flush=True)
    variants["rerank"] = trades_rerank

    for label, trades in variants.items():
        d = pd.DataFrame(trades)
        d.to_csv(f"{OUT_DIR}/trades_{label}.csv", index=False)
    print("Wrote trades_baseline.csv / trades_gate.csv / trades_rerank.csv\n", flush=True)

    # ------------------------------------------------------------------
    # Whole-run summary metrics (not block-level) for each variant.
    # ------------------------------------------------------------------
    print("=== Whole-run summary (fixed-capital metrics, full 2007-2026 window) ===", flush=True)
    summary_rows = []
    for label, trades in variants.items():
        m = compute_metrics_fixed(trades, starting_capital)
        row = {"variant": label, "n_trades": len(trades),
               "n_distinct_tickers": pd.DataFrame(trades)["ticker"].nunique() if trades else 0,
               **{k: m.get(k) for k in ("win_rate", "profit_factor", "sharpe_ratio", "max_drawdown_pct",
                                          "cagr_pct", "total_return_pct")}}
        print(f"  {label}: {row}", flush=True)
        summary_rows.append(row)
    pd.DataFrame(summary_rows).to_csv(f"{OUT_DIR}/whole_run_summary.csv", index=False)

    # ------------------------------------------------------------------
    # 16-block validation for each variant.
    # ------------------------------------------------------------------
    print("\n=== 16-block validation (reused analysis.validation._block_passes) ===", flush=True)
    all_block_results = {}
    for label, trades in variants.items():
        all_block_results[label] = block_validate(trades, gspc_close_full, starting_capital, label)
    import json
    with open(f"{OUT_DIR}/block_validation_results.json", "w") as fh:
        json.dump(all_block_results, fh, default=str, indent=2)

    # ------------------------------------------------------------------
    # Admission-order-interaction check: diff the (ticker, entry_date)
    # admitted-trade sets between baseline and each variant.
    # ------------------------------------------------------------------
    print("\n=== Admission-order interaction (baseline vs. each variant) ===", flush=True)
    base_set = set(zip(df_b["ticker"], df_b["entry_date"].astype(str)))
    interaction_rows = []
    for label, df_v in [("gate", df_g), ("rerank", df_r)]:
        var_set = set(zip(df_v["ticker"], df_v["entry_date"].astype(str)))
        removed = base_set - var_set   # admitted in baseline, not in variant
        added = var_set - base_set     # admitted in variant, not in baseline
        removed_dates = {d for _, d in removed}
        added_dates = {d for _, d in added}
        same_day_swap_dates = removed_dates & added_dates
        n_swap_events = sum(1 for d in same_day_swap_dates)
        print(f"  {label}: {len(removed)} trades removed vs. baseline, {len(added)} trades newly admitted, "
              f"{len(base_set & var_set)} unchanged")
        print(f"    same-day dates with BOTH a removal and a new addition (genuine contested-slot swaps): "
              f"{n_swap_events} distinct dates")
        interaction_rows.append({
            "variant": label, "n_removed": len(removed), "n_added": len(added),
            "n_unchanged": len(base_set & var_set), "n_same_day_swap_dates": n_swap_events,
        })
    pd.DataFrame(interaction_rows).to_csv(f"{OUT_DIR}/admission_interaction.csv", index=False)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
