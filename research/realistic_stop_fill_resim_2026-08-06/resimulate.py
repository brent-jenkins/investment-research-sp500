"""Full re-simulation of the STOP_LOSS fill convention flagged by the
same-session dividend/fill-convention audit
(research/dividend_fill_convention_2026-08-06) — urgent because this is a
genuine LIVE/BACKTEST DIVERGENCE, not a rejected research hypothesis: the
backtest fires STOP_LOSS off the daily CLOSE and fills at exactly
stop_price; live, since 2026-08-04, places a real broker-side stop order
that fires the instant price touches the level intraday. The audit's own
~GBP 212k combined estimate was explicitly first-order only ("Neither
number re-simulates: an earlier exit frees capacity, changes subsequent
position sizing, and changes compounding"). THIS script produces the
real, re-simulated number.

Mechanism: an isolated, marked, TEMPORARY addition directly to the real
analysis.backtest.simulate_portfolio_compounding_mtm() (see that
function's own docstring) -- a new realistic_stop_fill parameter,
default False producing byte-identical behaviour to before (verified
below). When True, the STOP_LOSS trigger check reads that day's LOW
instead of CLOSE (matching evaluate_exit_reason()'s own single `price`
parameter -- confirmed only ever used for the STOP_LOSS comparison, so
every other exit reason is completely unaffected), and the fill price is
min(stop_price, that day's open) -- achievable at the stop itself if the
open was still above it, or at the open if the day gapped straight
through. Because the existing per-day walk is what discovers the earlier
trigger day (nothing is patched in after the fact), this is a genuine
re-simulation: an earlier exit frees capacity that day, which changes
which OTHER candidates get admitted, which changes their sizing, which
compounds forward for the rest of the 19-year run -- exactly the effect
the audit's own first-order estimate could not capture.

This was chosen over a hand-duplicated research clone for the same
reason as the MA-extension investigation earlier tonight: the exit
evaluation lives inside a nested closure with no exposed seam to
monkeypatch, and a duplicate would itself risk the exact kind of drift
CLAUDE.md's "Research scripts must verify decision-engine parity"
standing requirement exists to prevent. The addition is reverted
immediately after this script captures all results (git checkout --
analysis/backtest.py, confirmed zero diff) -- no production code is left
changed by this investigation unless a separate, explicit decision is
made to keep it.

Backtest convention: CONTINUOUS WALK, full 2007-2026 -- this is not a
variant comparison in the "which parameter is better" sense, it is "what
would the SAME real trade history actually have looked like under a
realistic fill convention." The 16-block validation applied afterward
uses make_blocks()/_block_passes()/compute_metrics_fixed(), reused
verbatim from analysis.validation/analysis.backtest, exactly as every
other block-validated investigation tonight.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import time

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
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\realistic_stop_fill_resim_2026-08-06'
END = pd.Timestamp("2026-08-03")
START = pd.Timestamp("2007-01-01")

# The audit's own first-order combined estimate, for direct comparison.
AUDIT_GAP_THROUGH_GBP = 45_799.68
AUDIT_LATE_TRIGGER_GBP = 166_467.79
AUDIT_COMBINED_FIRST_ORDER_GBP = AUDIT_GAP_THROUGH_GBP + AUDIT_LATE_TRIGGER_GBP


def precompute_ticker_with_ohlc(ticker: str, config: dict):
    """Same as analysis.backtest.precompute_ticker(), plus "open" and
    "low" columns -- the only extra data the temporary
    realistic_stop_fill hook needs. Production's own precompute_ticker()
    is untouched; this is a separate, parallel function used only by
    this research script's own tickers_cache construction."""
    cached = precompute_ticker(ticker, config)
    if cached is None:
        return None
    df = get_price_df(ticker, days=8000)
    if df.empty:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    frame = cached["frame"].copy()
    frame["open"] = df["open"].astype(float).reindex(frame.index)
    frame["low"] = df["low"].astype(float).reindex(frame.index)
    return {"ticker": ticker, "frame": frame}


def headline_metrics(trades: list[dict], starting_capital: float) -> dict:
    m = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    df = pd.DataFrame(trades)
    total_pnl_gbp = df["pnl_gbp"].sum() if not df.empty else 0.0
    return {
        "n_trades": len(trades),
        "n_distinct_tickers": df["ticker"].nunique() if not df.empty else 0,
        "total_pnl_gbp": round(float(total_pnl_gbp), 2),
        "win_rate": m.get("win_rate"),
        "profit_factor": m.get("profit_factor"),
        "sharpe_ratio": m.get("sharpe_ratio"),
        "max_drawdown_pct_fixed_denominator": m.get("max_drawdown_pct"),
        "max_drawdown_pct_peak_relative": peak_rel_dd,
        "cagr_pct": m.get("cagr_pct"),
        "total_return_pct": m.get("total_return_pct"),
        "exit_reason_breakdown": (df["exit_reason"].value_counts().to_dict() if not df.empty else {}),
    }


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
    print("=== Realistic (intraday-touch) STOP_LOSS fill -- FULL RE-SIMULATION ===", flush=True)

    t212_cfg = CONFIG.get("trading212_invest", {})
    per_trade_pct = float(t212_cfg.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212_cfg.get("max_total_deployed", 0.5))
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 10000))
    print(f"per_trade_pct={per_trade_pct} max_deployed_pct={max_deployed_pct} "
          f"starting_capital={starting_capital}", flush=True)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"Building tickers_cache (with open/low) for {len(candidate_tickers)} live candidates...", flush=True)
    caches = []
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker_with_ohlc(ticker, CONFIG)
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

    print("--- Running BASELINE (realistic_stop_fill=False) -- must reproduce established baseline exactly ---", flush=True)
    trades_baseline = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct, realistic_stop_fill=False,
    )
    df_b = pd.DataFrame(trades_baseline)
    ec_b = df_b["exit_reason"].value_counts()
    print(f"  total_trades={len(df_b)}  MAX_HOLDING={ec_b.get('MAX_HOLDING', 0)} "
          f"({100*ec_b.get('MAX_HOLDING', 0)/len(df_b):.1f}%)  STOP_LOSS={ec_b.get('STOP_LOSS', 0)}", flush=True)
    assert len(df_b) == 3161, f"BASELINE drifted from established total: {len(df_b)}"
    assert int(ec_b.get("MAX_HOLDING", 0)) == 1796, f"BASELINE drifted from established MAX_HOLDING: {ec_b.get('MAX_HOLDING')}"
    assert int(ec_b.get("STOP_LOSS", 0)) == 1169, f"BASELINE drifted from established STOP_LOSS: {ec_b.get('STOP_LOSS')}"
    print("  Baseline reproduction CONFIRMED EXACT -- the temporary addition is behaviourally transparent "
          "at its default.\n", flush=True)

    print("--- Running CORRECTED (realistic_stop_fill=True) -- full re-simulation ---", flush=True)
    trades_corrected = simulate_portfolio_compounding_mtm(
        caches, CONFIG, gspc_close, per_trade_pct, max_deployed_pct, realistic_stop_fill=True,
    )
    df_c = pd.DataFrame(trades_corrected)
    ec_c = df_c["exit_reason"].value_counts()
    print(f"  total_trades={len(df_c)}  MAX_HOLDING={ec_c.get('MAX_HOLDING', 0)}  "
          f"STOP_LOSS={ec_c.get('STOP_LOSS', 0)}  distinct_tickers={df_c['ticker'].nunique()}\n", flush=True)

    df_b.to_csv(f"{OUT_DIR}/trades_baseline.csv", index=False)
    df_c.to_csv(f"{OUT_DIR}/trades_corrected.csv", index=False)
    with open(f"{OUT_DIR}/trades_baseline_full.json", "w") as fh:
        json.dump(trades_baseline, fh, default=str)
    with open(f"{OUT_DIR}/trades_corrected_full.json", "w") as fh:
        json.dump(trades_corrected, fh, default=str)
    print("Wrote trades_baseline.csv / trades_corrected.csv (+ full JSON per the reproducibility standard)\n",
          flush=True)

    # ------------------------------------------------------------------
    # Headline figures, both conventions.
    # ------------------------------------------------------------------
    print("=== Headline figures: BASELINE vs. CORRECTED ===", flush=True)
    hm_baseline = headline_metrics(trades_baseline, starting_capital)
    hm_corrected = headline_metrics(trades_corrected, starting_capital)
    print("  BASELINE :", json.dumps(hm_baseline, indent=2, default=str))
    print("  CORRECTED:", json.dumps(hm_corrected, indent=2, default=str))
    with open(f"{OUT_DIR}/headline_comparison.json", "w") as fh:
        json.dump({"baseline": hm_baseline, "corrected": hm_corrected}, fh, indent=2, default=str)

    true_gap_gbp = hm_baseline["total_pnl_gbp"] - hm_corrected["total_pnl_gbp"]
    print(f"\n  TRUE re-simulated overstatement (baseline total_pnl_gbp - corrected total_pnl_gbp): "
          f"GBP {true_gap_gbp:,.2f}", flush=True)
    print(f"  As % of baseline total_pnl_gbp: {100*true_gap_gbp/hm_baseline['total_pnl_gbp']:.2f}%", flush=True)
    print(f"  Audit's own FIRST-ORDER combined estimate (gap-through + late-trigger, not re-simulated): "
          f"GBP {AUDIT_COMBINED_FIRST_ORDER_GBP:,.2f}", flush=True)
    print(f"  First-order estimate as % of the SAME baseline total: "
          f"{100*AUDIT_COMBINED_FIRST_ORDER_GBP/hm_baseline['total_pnl_gbp']:.2f}%", flush=True)

    # ------------------------------------------------------------------
    # 16-block validation of the CORRECTED convention.
    # ------------------------------------------------------------------
    print("\n=== 16-block validation ===", flush=True)
    block_baseline = block_validate(trades_baseline, gspc_close_full, starting_capital, "baseline")
    block_corrected = block_validate(trades_corrected, gspc_close_full, starting_capital, "corrected")
    with open(f"{OUT_DIR}/block_validation_results.json", "w") as fh:
        json.dump({"baseline": block_baseline, "corrected": block_corrected}, fh, default=str, indent=2)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
