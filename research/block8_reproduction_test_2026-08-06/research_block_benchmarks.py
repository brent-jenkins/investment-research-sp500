"""16-block benchmark comparison: strategy vs. four passive alternatives.

CONVENTIONS (stated explicitly, per CLAUDE.md's standing requirement):
  * RESET-AT-BOUNDARY. Every block starts fresh at `starting_capital` with
    zero positions, truncated to that block's own window. This is a
    cross-variant comparison, so reset-at-boundary is the correct
    convention (never the continuous walk).
  * Corrected engine: simulate_portfolio_compounding_mtm(), which carries
    the 2026-08-06 realistic (intraday-touch) STOP_LOSS fill as its
    permanent default. Not the frozen exempt engines.
  * Both drawdown conventions reported for every leg: peak-relative
    ((equity-running_peak)/running_peak) and fixed-denominator
    ((equity-start)/start).
  * Denomination: ALL legs unconverted USD-denominated index/ETF levels
    scaled onto a nominal GBP starting capital. Symmetric across legs; no
    FX conversion is applied to any leg.
  * CAGR for EVERY leg (strategy included) is annualised over the SAME
    block calendar span, derived from the S&P's own first/last trading bar
    inside the block. This deliberately does NOT use
    compute_metrics_fixed()'s own cagr_pct for the strategy, which
    annualises over the span of the strategy's TRADES rather than the
    block window -- against a passive leg held for the whole block that
    would be an apples-to-oranges denominator. The production-convention
    figure is reported alongside it as `strat_cagr_tradespan_pct`.

LEGS
  A  S&P 500 buy-and-hold                       (^GSPC)
  B  NASDAQ-100 buy-and-hold                    (^NDX)
  C  Drawdown-matched index+cash, monthly rebal (^GSPC + cash)
       C-zero: cash earns 0%      (symmetric with the strategy's own
                                   idle cash, which earns nothing)
       C-real: cash earns the 13-week T-bill rate (^IRX)
  D  60/40 equity/bonds, monthly rebal          (^GSPC + AGG total return)

TOTAL RETURN. ^GSPC and ^NDX are PRICE-return indices (close == adj_close
for 100% of stored rows). The total-return variant of each is ESTIMATED by
scaling the price index by the realised dividend-reinvestment factor of its
matching ETF -- SPY for the S&P 500, QQQ for the NASDAQ-100 -- computed as
(adj_close(t)/close(t)) / (adj_close(t0)/close(t0)) over the block. This is
a MEASURED dividend factor rather than an assumed constant yield, but it is
still an ESTIMATE of the index's own total return: it is net of each ETF's
expense ratio (SPY ~0.0945%/yr, QQQ ~0.20%/yr) and of ETF tracking
difference, so it understates the true index total return by roughly that
much. The implied annualised yield actually applied is reported per block
per index so the assumption is visible and checkable.

Leg D's bond sleeve uses AGG's adj_close, a genuine total-return series
(coupons reinvested) -- price return alone would badly misstate a bond
holding. Leg C's cash uses ^IRX, the 13-week US Treasury bill discount
rate, accrued on an actual/365 basis.

LEG C IS A DELIBERATELY HARD TEST, NOT A FAIR ONE. Its index weight is
solved ex-post, per block, from that block's OWN realised strategy
drawdown -- information the strategy itself did not have at the time. A
narrow strategy win against leg C is therefore a stronger result than it
looks, and a narrow loss is weaker than it looks.
"""
import json
import sys
from pathlib import Path

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
from analysis.validation import _strategy_ending_balance_gbp
from data.ingestion import get_price_df

OUT = Path("out")
OUT.mkdir(exist_ok=True)

START = pd.Timestamp("2007-01-01")
# 2026-08-03, NOT 2026-08-06: this is the data vintage that exactly
# reproduces the cited single-window reference figures (verified: strategy
# 10.74 / -15.70 / +95.74 and S&P 13.73 / -33.92 / +133.30). See
# verify_0803.log.
END = pd.Timestamp("2026-08-03")
N_BLOCKS = 16
CAPITAL = 50_000.0

SPY_EXPENSE_RATIO = 0.000945
QQQ_EXPENSE_RATIO = 0.0020


def _era(mid: pd.Timestamp) -> str:
    y = mid.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def _load_close(ticker: str, col: str = "close") -> pd.Series:
    df = get_price_df(ticker, days=9000).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df[col].astype(float).sort_index()


# ---------------------------------------------------------------------------
# Passive-leg machinery
# ---------------------------------------------------------------------------

def peak_rel_dd(eq: pd.Series) -> float:
    peak = eq.cummax()
    return float(((eq - peak) / peak * 100.0).min())


def fixed_dd(eq: pd.Series) -> float:
    return float(((eq - eq.iloc[0]) / eq.iloc[0] * 100.0).min())


def rebalanced_equity(ret_df: pd.DataFrame, weights: dict) -> pd.Series:
    """Equity curve for a fixed-weight portfolio rebalanced at the start of
    every calendar MONTH (the stated, fixed schedule; monthly, not
    quarterly). Within a month the sleeves drift; at each month boundary
    they are reset to `weights`."""
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


def solve_dd_matched_weight(ret_df: pd.DataFrame, target_dd: float,
                            idx_col: str, cash_col: str):
    """Solve for the index weight whose monthly-rebalanced index+cash
    portfolio reproduces `target_dd` (a negative peak-relative %) in THIS
    block. Bisection on the real, realised drawdown of the actual
    constructed curve -- never a linear-scaling shortcut, which would be
    wrong whenever the drawdown path is not proportional to exposure.

    Returns (weight, realised_dd, matched_ok). matched_ok is False when the
    target is deeper than 100% index exposure can reach; the weight is then
    capped at 1.0 and the realised drawdown reported as achieved, so the
    match quality stays visible.
    """
    def dd_for(w: float) -> float:
        return peak_rel_dd(rebalanced_equity(ret_df, {idx_col: w, cash_col: 1.0 - w}))

    if target_dd is None:
        return None, None, None
    full = dd_for(1.0)
    if full > target_dd:          # even 100% index is shallower than target
        return 1.0, round(full, 2), False
    lo, hi = 0.0, 1.0             # dd_for(lo)=0 (shallow), dd_for(hi)=full (deep)
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
    config = yaml.safe_load(open("config.yaml"))
    t212 = config.get("trading212_invest", {})
    per_trade_pct = float(t212.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212.get("max_total_deployed", 0.5))

    print("Loading benchmark series ...", flush=True)
    gspc = _load_close("^GSPC")
    ndx = _load_close("^NDX")
    spy_c, spy_a = _load_close("SPY"), _load_close("SPY", "adj_close")
    qqq_c, qqq_a = _load_close("QQQ"), _load_close("QQQ", "adj_close")
    agg_a = _load_close("AGG", "adj_close")
    irx = _load_close("^IRX")

    blocks = make_blocks(START, END, n_blocks=N_BLOCKS)

    # Union of every candidate ticker needed across all block ends, so
    # precompute_ticker() runs exactly once per ticker rather than 16x.
    print("Resolving candidate universe ...", flush=True)
    per_block_cands = {i: universe.get_live_stock_candidates(as_of=be.date())
                       for i, (_, be) in enumerate(blocks)}
    all_tickers = sorted({t for v in per_block_cands.values() for t in v})
    print(f"  {len(all_tickers)} distinct tickers across all 16 block ends", flush=True)

    print("Precomputing tickers (once) ...", flush=True)
    cache = {}
    for n, t in enumerate(all_tickers, 1):
        cache[t] = precompute_ticker(t, config)
        if n % 100 == 0:
            print(f"  {n}/{len(all_tickers)}", flush=True)
    n_ok = sum(1 for v in cache.values() if v is not None)
    print(f"  {n_ok}/{len(all_tickers)} have usable price history", flush=True)

    rows = []
    all_trades = []
    for i, (bs, be) in enumerate(blocks):
        print(f"\n=== Block {i}: {bs.date()} -> {be.date()} ===", flush=True)

        gb = gspc[(gspc.index >= bs) & (gspc.index <= be)]
        if len(gb) < 2:
            print("  skipped: no S&P data")
            continue
        bidx = gb.index
        years = (bidx[-1] - bidx[0]).days / 365.25

        # ---- strategy leg (reset-at-boundary) --------------------------
        cands = per_block_cands[i]
        caches = [slice_ticker(cache.get(t), bs, be) for t in cands]
        trades = simulate_portfolio_compounding_mtm(
            caches, config, gb, per_trade_pct, max_deployed_pct, collect_rejections=False,
        )
        for t in trades:
            r = dict(t)
            r["block"] = i
            r["block_start"] = bs.date().isoformat()
            r["block_end"] = be.date().isoformat()
            all_trades.append(r)
        m = compute_metrics_fixed(trades, CAPITAL)
        dd_peak = compute_peak_relative_drawdown_pct(trades, CAPITAL)
        end_bal = _strategy_ending_balance_gbp(trades, CAPITAL)
        strat_total = (end_bal - CAPITAL) / CAPITAL * 100.0
        strat_cagr_window = ((end_bal / CAPITAL) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
        print(f"  strategy: {len(trades)} trades, CAGR(window)={strat_cagr_window:.2f}%, "
              f"ddPeak={dd_peak}, total={strat_total:.2f}%", flush=True)

        # ---- shared daily return frames --------------------------------
        def align(s):
            return s.reindex(bidx).ffill().bfill()

        gb_ret = gb.pct_change().fillna(0.0)
        ndx_b = align(ndx)
        ndx_ret = ndx_b.pct_change().fillna(0.0)

        # measured dividend-reinvestment factor (ETF-derived, an estimate)
        def tr_ret(price_b, etf_c, etf_a):
            ec, ea = align(etf_c), align(etf_a)
            factor = (ea / ec)
            factor = factor / float(factor.iloc[0])
            return (price_b * factor).pct_change().fillna(0.0), factor

        gspc_tr_ret, gspc_factor = tr_ret(gb, spy_c, spy_a)
        ndx_tr_ret, ndx_factor = tr_ret(ndx_b, qqq_c, qqq_a)
        implied_sp_yield = (float(gspc_factor.iloc[-1]) ** (1.0 / years) - 1.0) * 100.0
        implied_ndx_yield = (float(ndx_factor.iloc[-1]) ** (1.0 / years) - 1.0) * 100.0

        # cash: ^IRX 13-week T-bill discount rate, actual/365 accrual
        irx_b = align(irx) / 100.0
        daygap = pd.Series(bidx, index=bidx).diff().dt.days.fillna(0.0)
        cash_ret = (irx_b.shift(1).bfill() * daygap / 365.0).fillna(0.0)
        zero_ret = pd.Series(0.0, index=bidx)

        agg_ret = align(agg_a).pct_change().fillna(0.0)

        # ---- leg A: S&P buy-and-hold -----------------------------------
        legA_pr = leg_metrics((1.0 + gb_ret).cumprod(), years, CAPITAL)
        legA_tr = leg_metrics((1.0 + gspc_tr_ret).cumprod(), years, CAPITAL)

        # ---- leg B: NASDAQ-100 buy-and-hold ----------------------------
        legB_pr = leg_metrics((1.0 + ndx_ret).cumprod(), years, CAPITAL)
        legB_tr = leg_metrics((1.0 + ndx_tr_ret).cumprod(), years, CAPITAL)

        # ---- leg C: drawdown-matched index+cash ------------------------
        legC = {}
        for cash_label, cret in (("zero", zero_ret), ("real", cash_ret)):
            for ret_label, iret in (("pr", gb_ret), ("tr", gspc_tr_ret)):
                rdf = pd.DataFrame({"idx": iret, "cash": cret})
                w, realised, ok = solve_dd_matched_weight(rdf, dd_peak, "idx", "cash")
                if w is None:
                    legC[f"{cash_label}_{ret_label}"] = {"weight": None, "realised_dd": None,
                                                         "matched": None, "metrics": None}
                    continue
                eq = rebalanced_equity(rdf, {"idx": w, "cash": 1.0 - w})
                legC[f"{cash_label}_{ret_label}"] = {
                    "weight": w, "realised_dd": realised, "matched": ok,
                    "metrics": leg_metrics(eq, years, CAPITAL),
                }

        # ---- leg D: 60/40 ----------------------------------------------
        legD = {}
        for ret_label, iret in (("pr", gb_ret), ("tr", gspc_tr_ret)):
            rdf = pd.DataFrame({"idx": iret, "bond": agg_ret})
            eq = rebalanced_equity(rdf, {"idx": 0.6, "bond": 0.4})
            legD[ret_label] = leg_metrics(eq, years, CAPITAL)

        rows.append({
            "block": i, "start": bs.date().isoformat(), "end": be.date().isoformat(),
            "era": _era(bs + (be - bs) / 2), "years": round(years, 3),
            "n_candidates": len(cands),
            "strat_n_trades": len(trades),
            "strat_cagr_pct": round(strat_cagr_window, 2),
            "strat_cagr_tradespan_pct": m["cagr_pct"],
            "strat_total_return_pct": round(strat_total, 2),
            "strat_dd_peak_pct": dd_peak,
            "strat_dd_fixed_pct": m["max_drawdown_pct"],
            "strat_profit_factor": m.get("profit_factor", 0.0),
            "strat_ending_gbp": round(end_bal, 2),
            "strat_return_per_unit_dd": round(strat_cagr_window / abs(dd_peak), 3)
                if dd_peak else None,
            "implied_sp_div_yield_pct": round(implied_sp_yield, 3),
            "implied_ndx_div_yield_pct": round(implied_ndx_yield, 3),
            "legA_pr": legA_pr, "legA_tr": legA_tr,
            "legB_pr": legB_pr, "legB_tr": legB_tr,
            "legC": legC, "legD": legD,
        })

    Path(OUT / "block_results_raw.json").write_text(json.dumps(rows, indent=2, default=str))
    # Full trade-level output preserved, per CLAUDE.md's "preserve full
    # trade-level output, not just the formula" standing requirement -- this
    # is what makes a specific future figure auditable rather than merely
    # re-derivable.
    td = pd.DataFrame(all_trades)
    td.to_csv(OUT / "block_strategy_trades.csv", index=False)
    print(f"\nWrote {OUT/'block_results_raw.json'} ({len(rows)} blocks), "
          f"{OUT/'block_strategy_trades.csv'} ({len(td)} trades)")


if __name__ == "__main__":
    main()
