"""Deployment cap sweep (50% / 70% / 95%), 16-block validated.

Single variable: max_total_deployed in {0.50, 0.70, 0.95}. per_trade_pct
fixed at 0.05 (config's own current value). Everything else identical to
research/block_benchmark_comparison_2026-08-06's methodology, reused
directly wherever possible:

  * RESET-AT-BOUNDARY. Every block starts fresh at CAPITAL with zero
    positions, truncated to its own window. Cross-variant comparison =
    reset-at-boundary is the correct convention (never continuous walk).
  * Corrected engine: simulate_portfolio_compounding_mtm() (2026-08-06
    intraday-touch STOP_LOSS fill, its permanent default). Not the frozen
    exempt engines.
  * Window 2007-01-01 -> 2026-08-03 (the verified reproduction window from
    the prior run, NOT 08-06 -- see that report's own "Verification gate").
  * 16 blocks, CAPITAL = £50,000 each.

Decision-engine parity: this script calls simulate_portfolio_compounding_
mtm() -- the actual production function -- directly, with real caches and
real config-driven sizing parameters. No monkeypatching, no custom
candidate pool beyond the same live-universe-per-block-end resolution the
prior run already used and parity-verified. parity_check.py separately
proves the cap=0.50 arm reproduces the prior run's block-by-block figures
byte-for-byte (well, rounded-equal at 2dp, matching how both scripts round
before writing) before anything downstream is trusted.

collect_rejections=True is used throughout (a NATIVE, production-supported
toggle on the shared function -- see its own signature -- not a research
hack): it does not alter admission/trade logic at all, only whether
REJECTED_CAPACITY rows are additionally recorded. This gives a precise,
real "did the cap actually block a candidate today" signal for the
realised-vs-permitted-deployment section, and (via each rejection's own
recorded momentum) the raw material for the capacity-turnover
admission-effect analysis -- without needing any custom instrumentation of
engine internals.
"""
import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

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
from analysis.validation import (
    _block_passes,
    _cagr_dd_for_series,
    _strategy_ending_balance_gbp,
)
from data.ingestion import get_price_df

HERE = Path(__file__).parent
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
N_BLOCKS = 16
CAPITAL = 50_000.0
CAPS = [0.50, 0.70, 0.95]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_close(ticker: str, col: str = "close") -> pd.Series:
    df = get_price_df(ticker, days=9000).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df[col].astype(float).sort_index()


def peak_rel_dd(eq: pd.Series) -> float:
    peak = eq.cummax()
    return float(((eq - peak) / peak * 100.0).min())


def fixed_dd(eq: pd.Series) -> float:
    return float(((eq - eq.iloc[0]) / eq.iloc[0] * 100.0).min())


def rebalanced_equity(ret_df: pd.DataFrame, weights: dict) -> pd.Series:
    """Verbatim from research_block_benchmarks.py -- monthly rebalance."""
    idx = ret_df.index
    out = pd.Series(index=idx, dtype=float)
    cur = 1.0
    for _, seg in pd.Series(range(len(idx)), index=idx).groupby(idx.to_period("M")):
        sub = ret_df.loc[seg.index]
        growth = (1.0 + sub).cumprod()
        vals = sum(weights[a] * growth[a] for a in weights)
        out.loc[seg.index] = cur * vals
        cur = float(out.loc[seg.index].iloc[-1])
    return out


def solve_dd_matched_weight(ret_df: pd.DataFrame, target_dd: float, idx_col: str, cash_col: str):
    """Verbatim from research_block_benchmarks.py -- bisection on the real,
    realised drawdown of the actual constructed curve. Re-run fresh for
    EVERY (cap, block) combination here -- this is the reversal this task
    spec requires: leg C's weight must track each cap's own drawdown, not
    reuse the 0.50 arm's weights."""
    def dd_for(w: float) -> float:
        return peak_rel_dd(rebalanced_equity(ret_df, {idx_col: w, cash_col: 1.0 - w}))

    if target_dd is None:
        return None, None, None
    full = dd_for(1.0)
    if full > target_dd:
        return 1.0, round(full, 2), False
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if dd_for(mid) > target_dd:
            lo = mid
        else:
            hi = mid
    w = 0.5 * (lo + hi)
    return round(w, 4), round(dd_for(w), 2), True


def leg_metrics(eq: pd.Series, years: float, capital: float) -> dict:
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0) * 100.0
    cagr = (float(eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    dd_p = peak_rel_dd(eq)
    return {
        "cagr_pct": round(cagr, 2),
        "total_return_pct": round(total, 2),
        "max_dd_peak_relative_pct": round(dd_p, 2),
        "max_dd_fixed_pct": round(fixed_dd(eq), 2),
        "ending_balance_gbp": round(capital * float(eq.iloc[-1] / eq.iloc[0]), 2),
        "return_per_unit_dd": round(cagr / abs(dd_p), 3) if dd_p < 0 else None,
    }


def main() -> None:
    config = yaml.safe_load(open(HERE.parent.parent / "config.yaml"))
    t212 = config.get("trading212_invest", {})
    per_trade_pct = float(t212.get("per_trade_pct", 0.05))
    assert abs(per_trade_pct - 0.05) < 1e-9, f"expected per_trade_pct=0.05, got {per_trade_pct}"
    log(f"per_trade_pct fixed at {per_trade_pct}; sweeping max_total_deployed over {CAPS}")

    log("Loading benchmark series ...")
    gspc = _load_close("^GSPC")
    spy_c, spy_a = _load_close("SPY"), _load_close("SPY", "adj_close")
    irx = _load_close("^IRX")

    blocks = make_blocks(START, END, n_blocks=N_BLOCKS)

    log("Resolving candidate universe ...")
    per_block_cands = {i: universe.get_live_stock_candidates(as_of=be.date())
                       for i, (_, be) in enumerate(blocks)}
    all_tickers = sorted({t for v in per_block_cands.values() for t in v})
    log(f"  {len(all_tickers)} distinct tickers across all 16 block ends")

    cache_path = HERE / "ticker_caches.pkl"
    if cache_path.exists():
        log(f"Loading cached precomputed tickers from {cache_path} ...")
        with open(cache_path, "rb") as f:
            cache = pickle.load(f)
    else:
        log("Precomputing tickers (once, reused across all 3 caps) ...")
        cache = {}
        for n, t in enumerate(all_tickers, 1):
            cache[t] = precompute_ticker(t, config)
            if n % 100 == 0:
                log(f"  {n}/{len(all_tickers)}")
        with open(cache_path, "wb") as f:
            pickle.dump(cache, f)
    n_ok = sum(1 for v in cache.values() if v is not None)
    log(f"  {n_ok}/{len(all_tickers)} have usable price history")

    block_rows = []       # one row per (cap, block): strategy metrics + legC
    all_trades = {c: [] for c in CAPS}
    all_rejections = {c: [] for c in CAPS}

    for i, (bs, be) in enumerate(blocks):
        log(f"=== Block {i}: {bs.date()} -> {be.date()} ===")
        gb = gspc[(gspc.index >= bs) & (gspc.index <= be)]
        if len(gb) < 2:
            log("  skipped: no S&P data")
            continue
        bidx = gb.index
        years = (bidx[-1] - bidx[0]).days / 365.25

        cands = per_block_cands[i]
        caches_sliced = [slice_ticker(cache.get(t), bs, be) for t in cands]

        # Shared return series for leg C's bisection -- independent of cap,
        # computed once per block and reused for all 3 cap arms. Both cash
        # treatments (zero, real=^IRX) and both index bases (price return,
        # estimated total return via SPY's measured dividend factor) --
        # verbatim from research_block_benchmarks.py's own leg C machinery,
        # per this task spec's explicit "both C-zero and C-real ... both
        # price-return and estimated-total-return, exactly as before".
        def align(s):
            return s.reindex(bidx).ffill().bfill()

        gb_ret = gb.pct_change().fillna(0.0)
        spy_c_b, spy_a_b = align(spy_c), align(spy_a)
        factor = (spy_a_b / spy_c_b)
        factor = factor / float(factor.iloc[0])
        gspc_tr_ret = (gb * factor).pct_change().fillna(0.0)

        irx_b = align(irx) / 100.0
        daygap = pd.Series(bidx, index=bidx).diff().dt.days.fillna(0.0)
        cash_ret = (irx_b.shift(1).bfill() * daygap / 365.0).fillna(0.0)
        zero_ret = pd.Series(0.0, index=bidx)

        sp_cagr, sp_dd = _cagr_dd_for_series(gspc, bs, be)

        for cap in CAPS:
            trades, rejections = simulate_portfolio_compounding_mtm(
                caches_sliced, config, gb, per_trade_pct, cap, collect_rejections=True,
            )
            for t in trades:
                r = dict(t)
                r["block"] = i
                r["cap"] = cap
                r["block_start"] = bs.date().isoformat()
                r["block_end"] = be.date().isoformat()
                all_trades[cap].append(r)
            for rj in rejections:
                r = dict(rj)
                r["block"] = i
                r["cap"] = cap
                all_rejections[cap].append(r)

            m = compute_metrics_fixed(trades, CAPITAL)
            dd_peak = compute_peak_relative_drawdown_pct(trades, CAPITAL)
            end_bal = _strategy_ending_balance_gbp(trades, CAPITAL)
            strat_total = (end_bal - CAPITAL) / CAPITAL * 100.0
            strat_cagr_window = ((end_bal / CAPITAL) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
            block_pass = _block_passes(m, sp_dd)

            # ---- leg C: re-solved fresh for THIS cap's own dd_peak -------
            # All 4 variants (cash zero/real x index pr/tr), matching the
            # prior report's structure exactly -- this is the reversal the
            # task spec requires: do NOT reuse the 0.50 arm's weights.
            legc = {}
            for cash_label, cret in (("zero", zero_ret), ("real", cash_ret)):
                for ret_label, iret in (("pr", gb_ret), ("tr", gspc_tr_ret)):
                    rdf = pd.DataFrame({"idx": iret, "cash": cret})
                    w, realised, ok = solve_dd_matched_weight(rdf, dd_peak, "idx", "cash")
                    if w is None:
                        legc[f"{cash_label}_{ret_label}"] = {"weight": None, "realised_dd": None,
                                                              "matched": None, "metrics": None}
                        continue
                    eq = rebalanced_equity(rdf, {"idx": w, "cash": 1.0 - w})
                    legc[f"{cash_label}_{ret_label}"] = {
                        "weight": w, "realised_dd": realised, "matched": ok,
                        "metrics": leg_metrics(eq, years, CAPITAL),
                    }
            # primary/headline variant kept at top level for convenience
            w = legc["zero_pr"]["weight"]
            realised = legc["zero_pr"]["realised_dd"]
            ok = legc["zero_pr"]["matched"]
            legc_metrics = legc["zero_pr"]["metrics"]

            n_capacity_rejections = sum(1 for r in rejections if r.get("reason") == "REJECTED_CAPACITY")
            n_capacity_reject_days = len({r["date"] for r in rejections if r.get("reason") == "REJECTED_CAPACITY"})

            block_rows.append({
                "cap": cap, "block": i, "start": bs.date().isoformat(), "end": be.date().isoformat(),
                "years": round(years, 3), "n_candidates": len(cands),
                "strat_n_trades": len(trades),
                "strat_cagr_pct": round(strat_cagr_window, 2),
                "strat_cagr_tradespan_pct": m["cagr_pct"],
                "strat_total_return_pct": round(strat_total, 2),
                "strat_dd_peak_pct": dd_peak,
                "strat_dd_fixed_pct": m["max_drawdown_pct"],
                "strat_profit_factor": m.get("profit_factor", 0.0),
                "strat_ending_gbp": round(end_bal, 2),
                "strat_return_per_unit_dd": round(strat_cagr_window / abs(dd_peak), 3) if dd_peak else None,
                "sp_dd_peak_pct": round(sp_dd, 2) if sp_dd is not None else None,
                "sp_cagr_pct": round(sp_cagr, 2) if sp_cagr is not None else None,
                "block_pass": bool(block_pass),
                "legC_weight": w, "legC_realised_dd": realised, "legC_matched": ok,
                "legC_metrics": legc_metrics,
                "legC": legc,
                "n_capacity_rejections": n_capacity_rejections,
                "n_capacity_reject_days": n_capacity_reject_days,
                "n_block_days": len(bidx),
            })
            log(f"  cap={cap}: {len(trades)} trades, CAGR(window)={strat_cagr_window:.2f}%, "
                f"ddPeak={dd_peak}, pass={block_pass}, legC_w={w}, "
                f"capacity_reject_days={n_capacity_reject_days}/{len(bidx)}")

    Path(OUT / "sweep_block_results_raw.json").write_text(json.dumps(block_rows, indent=2, default=str))
    for cap in CAPS:
        tag = str(cap).replace(".", "")
        td = pd.DataFrame(all_trades[cap])
        td.to_csv(OUT / f"trades_cap{tag}.csv", index=False)
        rj = pd.DataFrame(all_rejections[cap])
        rj.to_csv(OUT / f"rejections_capacity_cap{tag}.csv", index=False)
        log(f"cap={cap}: wrote {len(td)} trades, {len(rj)} capacity rejections")

    log(f"\nWrote {OUT/'sweep_block_results_raw.json'} ({len(block_rows)} rows, "
        f"{len(CAPS)} caps x 16 blocks)")


if __name__ == "__main__":
    main()
