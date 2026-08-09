"""Excess return over ^SP500TR, cumulative and per-block, per
PREREGISTRATION.md Section 7. Adapted from
research/measurement_toolkit_2026-08-08/step4_benchmark_relative.py's own
already-validated methodology (same functions: compute_metrics_fixed(),
compute_peak_relative_drawdown_pct(), make_blocks(), _cagr_dd_for_series())
-- copied rather than imported cross-worktree, since that script's own
sys.path.insert() points at a different, unrelated worktree and this
project's convention is each study's scripts are self-contained.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")

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
                                starting_capital: float) -> dict:
    """Continuous-walk excess return + per-block excess return vs.
    ^SP500TR, on the identical window the strategy was measured over.
    Headline uses frictionless CAGR (this study has no cost-adjustment
    template of its own -- FX/idle-cash cost adjustment is out of scope
    per the pre-registration, which never mentions it; reported plainly
    as frictionless, not silently presented as cost-adjusted)."""
    trades_df = trades_df.copy()
    trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])
    total_years = (end - start).days / 365.25
    strat_cw = continuous_walk_metrics(trades_df, starting_capital, total_years)

    tr_close = get_sp500tr_close(start, end)
    tr_start, tr_end = float(tr_close.iloc[0]), float(tr_close.iloc[-1])
    tr_cagr = round(((tr_end / tr_start) ** (1 / total_years) - 1) * 100, 2)
    tr_peak = tr_close.cummax()
    tr_dd = round(float(((tr_close - tr_peak) / tr_peak * 100).min()), 2)

    excess_cagr_pct = round(strat_cw["cagr_pct"] - tr_cagr, 2)

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
            "strat_cagr_pct": strat_m["cagr_pct"],
            "sp500tr_cagr_pct": round(tr_block_cagr, 2) if tr_block_cagr is not None else None,
            "excess_cagr_pct": excess,
        })

    return {
        "strat_continuous_cagr_pct": strat_cw["cagr_pct"],
        "strat_peak_relative_max_dd_pct": strat_cw["peak_relative_max_dd_pct"],
        "sp500tr_continuous_cagr_pct": tr_cagr,
        "sp500tr_peak_relative_max_dd_pct": tr_dd,
        "excess_cagr_pct_vs_sp500tr": excess_cagr_pct,
        "n_blocks_beating_sp500tr": n_beat, "n_blocks_total": len(blocks_raw),
        "blocks": block_rows,
    }
