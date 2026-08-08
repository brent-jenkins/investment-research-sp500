"""Benchmark-relative reporting: excess return over ^SP500TR (S&P 500 Total
Return, dividends reinvested -- confirmed full coverage 2006-01-03 through
2026-08-07 via yfinance) on the SAME dates as the strategy run. Every figure
this project produces elsewhere is absolute; this nets out what each block
happens to contain, which is plausibly a large part of why blocks disagree.
Reusable functions -- called by other scripts in this toolkit, not run
standalone for its own output (see step7_validate_toolkit.py for usage).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import pandas as pd
import yfinance as yf

from analysis.backtest import compute_metrics_fixed, compute_peak_relative_drawdown_pct, make_blocks
from analysis.validation import _cagr_dd_for_series

_SP500TR_CACHE = {}


def get_sp500tr_close(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    key = (start, end)
    if key in _SP500TR_CACHE:
        return _SP500TR_CACHE[key]
    raw = yf.Ticker("^SP500TR").history(
        start=start.date().isoformat(), end=(end + pd.Timedelta(days=2)).date().isoformat()
    )
    close = raw["Close"].astype(float)
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close[(close.index >= start) & (close.index <= end)]
    _SP500TR_CACHE[key] = close
    return close


def continuous_walk_metrics(trades_df: pd.DataFrame, starting_capital: float, total_years: float) -> dict:
    trades = trades_df.to_dict("records")
    total_pnl = trades_df["pnl_gbp"].sum()
    ending_equity = starting_capital + total_pnl
    cagr_pct = round((((ending_equity / starting_capital) ** (1 / total_years)) - 1) * 100, 2)
    peak_dd_pct = compute_peak_relative_drawdown_pct(trades, starting_capital)
    return {"cagr_pct": cagr_pct, "ending_equity_gbp": round(ending_equity, 2), "peak_relative_max_dd_pct": peak_dd_pct}


def benchmark_relative_report(trades_df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                                starting_capital: float, cost_adjusted_cagr_pct: float = None) -> dict:
    """Continuous-walk excess return + per-block (reset-at-boundary) excess
    return vs. ^SP500TR, on the identical window the strategy was measured
    over. cost_adjusted_cagr_pct, if given, is used for the headline excess
    figure instead of the raw/frictionless CAGR (matches this project's own
    standard of reporting cost-adjusted numbers as the real headline)."""
    total_years = (end - start).days / 365.25
    strat_cw = continuous_walk_metrics(trades_df, starting_capital, total_years)

    tr_close = get_sp500tr_close(start, end)
    tr_start, tr_end = float(tr_close.iloc[0]), float(tr_close.iloc[-1])
    tr_cagr = round(((tr_end / tr_start) ** (1 / total_years) - 1) * 100, 2)
    tr_peak = tr_close.cummax()
    tr_dd = round(float(((tr_close - tr_peak) / tr_peak * 100).min()), 2)

    headline_strat_cagr = cost_adjusted_cagr_pct if cost_adjusted_cagr_pct is not None else strat_cw["cagr_pct"]
    excess_cagr_pct = round(headline_strat_cagr - tr_cagr, 2)

    blocks_raw = make_blocks(start, end, n_blocks=16)
    block_rows = []
    n_beat = 0
    for i, (b_start, b_end) in enumerate(blocks_raw):
        strat_trades = trades_df[(trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)].to_dict("records")
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        tr_block_cagr, tr_block_dd = _cagr_dd_for_series(tr_close, b_start, b_end)
        excess = None
        if tr_block_cagr is not None:
            excess = round(strat_m["cagr_pct"] - tr_block_cagr, 2)
            if excess > 0:
                n_beat += 1
        block_rows.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "strat_cagr_pct": strat_m["cagr_pct"], "sp500tr_cagr_pct": round(tr_block_cagr, 2) if tr_block_cagr is not None else None,
            "excess_cagr_pct": excess,
        })

    return {
        "strat_continuous_cagr_pct": strat_cw["cagr_pct"],
        "strat_cost_adjusted_cagr_pct": cost_adjusted_cagr_pct,
        "strat_peak_relative_max_dd_pct": strat_cw["peak_relative_max_dd_pct"],
        "sp500tr_continuous_cagr_pct": tr_cagr,
        "sp500tr_peak_relative_max_dd_pct": tr_dd,
        "excess_cagr_pct_vs_sp500tr": excess_cagr_pct,
        "n_blocks_beating_sp500tr": n_beat, "n_blocks_total": len(blocks_raw),
        "blocks": block_rows,
    }
