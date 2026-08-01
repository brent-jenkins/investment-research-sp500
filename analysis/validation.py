"""Full validated-battery runner — momentum signal + crisis override, the
current point-in-time-gated universe, train (2007-2019) / test (2020-2026)
split, 16 historical blocks each checked against the real S&P 500 over the
same window, a concentration check (ex-best-trade), transaction-cost
sensitivity, and walk-forward. Shared by the Backtest page (main.py) and the
due-diligence report generator (analysis/dd_report.py) so both are always
computed the exact same way from the exact same run.

Block pass/fail rule (reconciled 2026-07-31 -- see CLAUDE.md's "Block
pass/fail formula reconciliation" section for the full investigation):
strategy max drawdown no worse than 1.5x the S&P's own over the same
window, AND strategy profit factor > 1.0 over the same window (the same
">1.0 = profitable" convention this project already uses in
walk_forward_pf() below and in the pre-V1-removal
compute_walk_forward_summary()). A per-block CAGR-vs-S&P comparison and an
invented "-20% of capital" floor were both tried in an earlier
reconstruction and produced the wrong two failing blocks; neither survives
against the real reproduction data (see CLAUDE.md) and neither is used
here. `_block_passes()` is exported specifically so a regression test can
pin this exact formula against real historical block metrics and catch any
future silent drift.

`make_blocks()`'s boundaries (imported from analysis.backtest, unchanged
byte-for-byte since the very first V2 commit) are NOT a reconstruction --
confirmed via git history to be identical to what the original
reproducibility check used, so a boundary mismatch was ruled out before
looking at the formula itself.
"""
import datetime
import logging
from typing import Optional

import pandas as pd

from analysis import universe
from analysis.backtest import (
    compute_cost_sensitivity,
    compute_metrics_fixed,
    equity_curve_gbp,
    full_metrics_fixed,
    make_blocks,
    precompute_ticker,
    run_candidate,
    simulate_portfolio,
    simulate_portfolio_compounding,
    slice_ticker,
    walk_forward_pf,
)
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

VALIDATION_START = pd.Timestamp("2007-01-01")
VALIDATION_TRAIN_TEST_SPLIT = pd.Timestamp("2020-01-01")


def _cagr_dd_for_series(close: pd.Series, b_start: pd.Timestamp, b_end: pd.Timestamp):
    s = close[(close.index >= b_start) & (close.index <= b_end)]
    if len(s) < 2:
        return None, None
    start_v, end_v = float(s.iloc[0]), float(s.iloc[-1])
    days = (s.index[-1] - s.index[0]).days
    years = days / 365.25 if days > 0 else 0.0
    cagr = ((end_v / start_v) ** (1.0 / years) - 1.0) * 100 if years > 0 and start_v > 0 else 0.0
    peak = s.cummax()
    dd = float(((s - peak) / peak * 100).min())
    return cagr, dd


def _sp500_ending_balance_gbp(
    close: pd.Series, start: pd.Timestamp, end: pd.Timestamp, starting_capital: float,
) -> Optional[float]:
    """£ ending balance for a buy-and-hold position in *close* over
    [start, end], sized at starting_capital -- computed directly from the
    window's first/last price rather than re-derived from a rounded CAGR
    percentage, so it matches _cagr_dd_for_series()'s own CAGR exactly
    (same start_v/end_v, same window bounds) rather than compounding a
    second, lossier calculation on top of it. None only when the series
    has fewer than 2 rows in the window (nothing to compare against) --
    same "no data, not zero" convention _cagr_dd_for_series() uses.
    """
    s = close[(close.index >= start) & (close.index <= end)]
    if len(s) < 2 or float(s.iloc[0]) <= 0:
        return None
    return starting_capital * (float(s.iloc[-1]) / float(s.iloc[0]))


def _strategy_ending_balance_gbp(trades: list[dict], starting_capital: float) -> float:
    """£ ending balance for *trades* under the additive-£ methodology
    (equity_curve_gbp()) -- the same one behind every other £ figure this
    project reports (compute_metrics_fixed(), the DD report's own equity
    curve, the Run #26 real-return reconciliation). No trades -> the
    account never moved, so it's still sitting at starting_capital.
    """
    if not trades:
        return starting_capital
    ec = equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=starting_capital)
    if ec.empty:
        return starting_capital
    return float(ec["equity_gbp"].iloc[-1])


def compute_gbp_growth(
    gspc_close_full: pd.Series,
    train_trades: list[dict],
    test_trades: list[dict],
    starting_capital: float,
    start: pd.Timestamp,
    split: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    """What a real £starting_capital investment would have actually become,
    V2 vs. buy-and-hold S&P 500 -- train and test kept separate, never
    blended into one full-period number (blending would hide how much of
    any headline figure comes from the in-sample training window).

    The S&P leg reuses the identical ^GSPC series (price_data, sourced via
    get_price_df() -- a price-only index level, no dividend reinvestment;
    ^SP500TR, the total-return variant, is not tracked anywhere in this
    project) already used for train_sp/test_sp's CAGR figures elsewhere in
    run_full_validation() -- same series, same window boundaries, so this
    is a £-terms restatement of the exact same comparison already
    validated, not a second, differently-sourced or differently-
    conventioned one running alongside it.
    """
    return {
        "starting_capital_gbp": starting_capital,
        "train": {
            "strategy_ending_gbp": round(_strategy_ending_balance_gbp(train_trades, starting_capital), 2),
            "sp500_ending_gbp": round(
                _sp500_ending_balance_gbp(gspc_close_full, start, split, starting_capital) or starting_capital, 2,
            ),
        },
        "test": {
            "strategy_ending_gbp": round(_strategy_ending_balance_gbp(test_trades, starting_capital), 2),
            "sp500_ending_gbp": round(
                _sp500_ending_balance_gbp(gspc_close_full, split, end, starting_capital) or starting_capital, 2,
            ),
        },
    }


def _block_passes(strat_metrics: dict, sp_dd: Optional[float]) -> bool:
    """The reconciled per-block pass/fail rule -- see this module's
    docstring. Pure and separately exported so a regression test can pin it
    directly against real historical block metrics, independent of the full
    (expensive, production-data-scale) battery run.

    dd_ok: strategy's own max drawdown no worse than 1.5x the S&P's own
    over the same window (sp_dd is None only when the S&P series itself
    has fewer than 2 rows in the window -- treated as an automatic pass
    for that leg, since there is nothing to compare against).

    pf_ok: strategy profit factor strictly greater than 1.0 -- this
    project's own standing "did this window actually make money" bar
    (analysis/backtest.py:walk_forward_pf()'s identical `pf > 1.0` check).
    """
    dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
    pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
    return bool(dd_ok and pf_ok)


def universe_snapshot(as_of: Optional[datetime.date] = None) -> dict:
    """Live, current universe composition -- candidate counts plus how many
    of the 40 stock candidates are genuinely S&P 500 members right now
    (not assumed from before any refactor)."""
    as_of = as_of or datetime.date.today()
    members_today = [t for t in universe.STOCK_MEMBERSHIP if universe.is_member(t, as_of)]
    non_members = [t for t in universe.STOCK_MEMBERSHIP if t not in members_today]
    return {
        "as_of": as_of.isoformat(),
        "etf_count": len(universe.ETF_TICKERS),
        "stock_candidate_count": len(universe.STOCK_MEMBERSHIP),
        "stock_member_count": len(members_today),
        "non_members": non_members,
        "total_candidates": len(universe.ALL_TICKERS),
        "live_tradeable_count": len(universe.ETF_TICKERS) + len(members_today),
    }


def run_full_validation(
    config: dict,
    start: pd.Timestamp = VALIDATION_START,
    end: Optional[pd.Timestamp] = None,
    portfolio_mode: bool = False,
    per_trade_pct: Optional[float] = None,
    max_deployed_pct_compounding: Optional[float] = None,
) -> dict:
    """Run the complete validated battery over [start, end] and return every
    figure the Backtest page / DD report need. end defaults to today.

    portfolio_mode (added 2026-08-01, default False — no change to any
    existing caller's behaviour): when True, trades are generated by
    analysis.backtest.simulate_portfolio() instead of run_candidate() — a
    single shared-account walk across every ticker together, enforcing
    trading212_invest.max_open_positions / max_total_deployed as one real
    cross-ticker cap, rather than simulating every ticker fully
    independently against the same notional starting-capital baseline (see
    CLAUDE.md's "Position-sizing caps ... binding-frequency investigation"
    and "Portfolio-capped backtest engine" sections for why this matters —
    those caps were found to bind on 93-98% of days/entries even on the
    current 73-ticker universe under the uncapped engine). Every other step
    below (train/test split, blocks, walk-forward, cost sensitivity, £
    growth) is unchanged and operates on whichever trade list was produced.

    per_trade_pct / max_deployed_pct_compounding (added 2026-08-01, default
    None — no change to any existing caller's behaviour): when BOTH are
    provided, trades are instead generated by
    analysis.backtest.simulate_portfolio_compounding() — a genuinely
    compounding shared-account walk (see that function's own docstring),
    overriding portfolio_mode entirely (compounding mode takes priority —
    passing all three is not a supported combination and is resolved by
    ignoring portfolio_mode in that case, since the two flat-notional modes
    and the compounding mode are mutually exclusive simulation engines, not
    stackable options).
    """
    end = end or pd.Timestamp(datetime.date.today())
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 50000))
    compounding_mode = per_trade_pct is not None and max_deployed_pct_compounding is not None

    gspc_df = get_price_df("^GSPC", days=8000)
    gspc_df = gspc_df.set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    gspc_close = gspc_close_full[(gspc_close_full.index >= start) & (gspc_close_full.index <= end)]

    caches = []
    for ticker in universe.ALL_TICKERS:
        c = precompute_ticker(ticker, config)
        if c is not None:
            c = slice_ticker(c, start, end)
        caches.append(c)
    n_ok = sum(1 for c in caches if c is not None)

    if compounding_mode:
        all_trades = simulate_portfolio_compounding(
            caches, config, gspc_close, per_trade_pct, max_deployed_pct_compounding,
        )
    elif portfolio_mode:
        all_trades = simulate_portfolio(caches, config, gspc_close)
    else:
        all_trades = run_candidate(caches, config, gspc_close)
    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])

    if trades_df.empty:
        train_trades, test_trades = [], []
    else:
        train_trades = trades_df[trades_df["entry_date"] < VALIDATION_TRAIN_TEST_SPLIT].to_dict("records")
        test_trades = trades_df[trades_df["entry_date"] >= VALIDATION_TRAIN_TEST_SPLIT].to_dict("records")

    train_metrics = compute_metrics_fixed(train_trades, starting_capital)
    test_metrics = compute_metrics_fixed(test_trades, starting_capital)
    full_metrics = full_metrics_fixed(all_trades, starting_capital)

    train_sp_cagr, train_sp_dd = _cagr_dd_for_series(gspc_close_full, start, VALIDATION_TRAIN_TEST_SPLIT)
    test_sp_cagr, test_sp_dd = _cagr_dd_for_series(gspc_close_full, VALIDATION_TRAIN_TEST_SPLIT, end)
    full_sp_cagr, full_sp_dd = _cagr_dd_for_series(gspc_close_full, start, end)

    blocks_raw = make_blocks(start, end, n_blocks=16)
    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks_raw):
        if trades_df.empty:
            strat_trades = []
        else:
            strat_trades = trades_df[
                (trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)
            ].to_dict("records")
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)

        block_pass = _block_passes(strat_m, sp_dd)
        if block_pass:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(strat_trades), "strat_cagr": strat_m["cagr_pct"],
            "strat_dd": strat_m["max_drawdown_pct"],
            "strat_pf": strat_m.get("profit_factor", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": block_pass,
        })

    wf = walk_forward_pf(all_trades)
    cost_sensitivity = compute_cost_sensitivity(all_trades, starting_capital, cost_bps_list=(0, 10, 25))
    gbp_growth = compute_gbp_growth(
        gspc_close_full, train_trades, test_trades, starting_capital,
        start, VALIDATION_TRAIN_TEST_SPLIT, end,
    )

    return {
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "starting_capital_gbp": starting_capital,
        "portfolio_mode": portfolio_mode,
        "compounding_mode": compounding_mode,
        "per_trade_pct": per_trade_pct,
        "max_deployed_pct_compounding": max_deployed_pct_compounding,
        "universe": universe_snapshot(),
        "n_tickers_with_data": n_ok, "n_tickers_total": len(universe.ALL_TICKERS),
        "total_trades": len(all_trades),
        "train": train_metrics, "test": test_metrics,
        "train_sp": {"cagr_pct": train_sp_cagr, "max_drawdown_pct": train_sp_dd},
        "test_sp": {"cagr_pct": test_sp_cagr, "max_drawdown_pct": test_sp_dd},
        "full_sp": {"cagr_pct": full_sp_cagr, "max_drawdown_pct": full_sp_dd},
        "full_raw": full_metrics["raw"], "full_ex_best": full_metrics["ex_best"],
        "blocks": block_results, "n_blocks_pass": n_pass, "n_blocks_total": len(blocks_raw),
        "walk_forward": wf,
        "cost_sensitivity": cost_sensitivity,
        "gbp_growth": gbp_growth,
        "trades": all_trades,
    }
