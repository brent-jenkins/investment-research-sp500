"""Runs the rolling-40 (quarterly market-cap-reconstituted) variant against
the current, corrected simulate_portfolio_compounding_mtm() engine,
reset-at-boundary convention, matching run_variant.py's curated40/full500
runs as closely as possible so all three sit on directly comparable
footing (CLAUDE_HISTORY.md's own "all three re-run with the identical
reset treatment ... so all three sit on the same clean footing"
methodology).

Decision-engine parity note (CLAUDE.md "Research scripts must verify
decision-engine parity" standing requirement): the ONLY function
monkeypatched here is analysis.universe.is_member() -- layered ON TOP OF
the real membership check (never replacing it: a ticker must be BOTH a
genuine S&P 500 member AND in the most recent quarterly top-40-by-market-
cap set to be eligible), matching the original study's own definition
exactly. Every other decision function (evaluate_exit_reason,
passes_entry_gate, passes_momentum_floor, is_reentry_blocked,
resolve_dynamic_max_position_gbp, resolve_cash_constrained_position_gbp,
is_position_size_viable, get_max_holding_days, _var_position_size_core) is
untouched -- this script calls the real, unmodified
simulate_portfolio_compounding_mtm_reset_at_boundary() (itself built via
source-introspection from the real production function, see
reset_wrapper.py), so parity for all of those holds by construction, not
merely by inspection. verify_rolling_membership_parity() below pulls real
admitted trades from this run's own output and independently re-derives
whether each one's ticker was genuinely in that quarter's top-40 at entry
time, with real evidence gathered from THIS run, per that standing
requirement -- not assumed from the fact that a monkeypatch is present.
"""
import bisect
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import env_setup

env_setup.patch_all()
config = env_setup.load_config()

from analysis import universe
from analysis.backtest import (
    precompute_ticker, slice_ticker, compute_metrics_fixed,
    compute_peak_relative_drawdown_pct, equity_curve_gbp,
)
from data.ingestion import get_price_df
from reset_wrapper import build_reset_wrapper, verify_faithful

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
SPLIT = pd.Timestamp("2020-01-01")
RESET_CAPITAL = 50000.0

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

with open(OUTPUT_DIR / "candidate_pool_500.json") as f:
    POOL = json.load(f)
with open(OUTPUT_DIR / "rolling40_schedule.json") as f:
    _raw_schedule = json.load(f)

# Sorted list of (quarter_end_date, set(top40_tickers)) for bisect lookup.
_SCHEDULE: list[tuple[date, set]] = sorted(
    (datetime.strptime(k, "%Y-%m-%d").date(), set(v)) for k, v in _raw_schedule.items()
)
_SCHEDULE_DATES = [d for d, _ in _SCHEDULE]

_real_is_member = universe.is_member


def top40_set_as_of(check_date: date):
    """The most recently EFFECTIVE quarterly reconstitution's top-40 set as
    of check_date -- the last quarter-end <= check_date. None if
    check_date predates the first reconstitution (2009-12-31), meaning
    rolling-40 has no eligible candidates yet at all before that date,
    matching the original study's own real SEC-XBRL-coverage-driven start
    point."""
    idx = bisect.bisect_right(_SCHEDULE_DATES, check_date) - 1
    if idx < 0:
        return None
    return _SCHEDULE[idx][1]


def rolling40_is_member(ticker: str, check_date: date) -> bool:
    """Layered on top of the real membership check, never replacing it --
    a ticker must be a genuine S&P 500 member AND in the most recent
    quarterly top-40-by-market-cap set."""
    if not _real_is_member(ticker, check_date):
        return False
    top40 = top40_set_as_of(check_date)
    if top40 is None:
        return False
    return ticker in top40


def build_caches(tickers: list[str]) -> list[dict]:
    caches = []
    for t in tickers:
        c = precompute_ticker(t, config)
        if c is not None:
            c = slice_ticker(c, START, END)
        caches.append(c)
    return caches


def get_gspc_close():
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    return gspc_close_full[(gspc_close_full.index >= START) & (gspc_close_full.index <= END)]


def metrics_block(trades: list[dict], starting_capital: float) -> dict:
    m = compute_metrics_fixed(trades, starting_capital)
    peak_rel_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
    ending = starting_capital
    if trades:
        ec = equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=starting_capital)
        if not ec.empty:
            ending = float(ec["equity_gbp"].iloc[-1])
    return {
        "n_trades": len(trades),
        "win_rate_pct": round(m.get("win_rate", 0.0) * 100, 2) if m.get("win_rate") is not None else None,
        "profit_factor": m.get("profit_factor"),
        "cagr_pct": m.get("cagr_pct"),
        "max_drawdown_pct_fixed_denom": m.get("max_drawdown_pct"),
        "max_drawdown_pct_peak_relative": peak_rel_dd,
        "ending_balance_gbp": round(ending, 2),
    }


def verify_rolling_membership_parity(trades: list[dict], n_sample: int = 25):
    """Decision-engine parity evidence, gathered from THIS run's own real
    output, per CLAUDE.md's "Research scripts must verify decision-engine
    parity" standing requirement. For a sample of real admitted trades,
    independently re-derive (from the schedule, not from the monkeypatch
    itself) whether the ticker was genuinely in the effective quarter's
    top-40 AND a genuine S&P member at entry, and separately confirm
    holding_days never exceeds get_max_holding_days() for that ticker --
    proving the sizing/holding-period machinery downstream of the
    monkeypatch was untouched and still enforced correctly, not merely
    assumed from the fact that the real engine function was reused."""
    from analysis.universe import get_max_holding_days

    if not trades:
        return {"sample_size": 0, "membership_ok": 0, "holding_period_ok": 0, "failures": []}

    df = pd.DataFrame(trades)
    sample = df.sample(n=min(n_sample, len(df)), random_state=42)
    failures = []
    membership_ok = 0
    holding_ok = 0
    for _, row in sample.iterrows():
        entry_date = row["entry_date"]
        entry_date = entry_date.date() if hasattr(entry_date, "date") else entry_date
        ticker = row["ticker"]

        is_real_member = _real_is_member(ticker, entry_date)
        top40 = top40_set_as_of(entry_date)
        in_top40 = top40 is not None and ticker in top40
        if is_real_member and in_top40:
            membership_ok += 1
        else:
            failures.append({
                "ticker": ticker, "entry_date": str(entry_date),
                "is_real_member": is_real_member, "in_top40": in_top40,
                "check": "membership",
            })

        max_hold = get_max_holding_days(ticker, config)
        if row["holding_days"] <= max_hold:
            holding_ok += 1
        else:
            failures.append({
                "ticker": ticker, "entry_date": str(entry_date),
                "holding_days": int(row["holding_days"]), "max_hold": max_hold,
                "check": "holding_period",
            })

    return {
        "sample_size": len(sample),
        "membership_ok": membership_ok,
        "holding_period_ok": holding_ok,
        "failures": failures,
    }


def run():
    print(f"building caches for {len(POOL)} tickers (rolling-40 candidate pool)...", flush=True)
    caches = build_caches(POOL)
    n_ok = sum(1 for c in caches if c is not None)
    print(f"{n_ok}/{len(POOL)} tickers have usable price history", flush=True)

    gspc_close = get_gspc_close()
    per_trade_pct = float(config["trading212_invest"]["per_trade_pct"])
    max_deployed_pct = float(config["trading212_invest"]["max_total_deployed"])

    wrapped = build_reset_wrapper()
    print("verifying wrapper faithfulness (with the REAL is_member, before any monkeypatch)...", flush=True)
    verify_faithful(wrapped, caches, config, gspc_close, per_trade_pct, max_deployed_pct)
    print("wrapper verified faithful", flush=True)

    print("monkeypatching analysis.universe.is_member -> rolling40_is_member (layered on top of the real check)...", flush=True)
    universe.is_member = rolling40_is_member
    try:
        trades = wrapped(
            caches, config, gspc_close, per_trade_pct, max_deployed_pct,
            reset_boundary_ts=SPLIT, reset_capital=RESET_CAPITAL,
        )
    finally:
        universe.is_member = _real_is_member
        print("restored the real analysis.universe.is_member", flush=True)

    print(f"{len(trades)} full-walk trades", flush=True)
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])
        test_trades = trades_df[trades_df["entry_date"] >= SPLIT].to_dict("records")
    else:
        test_trades = []

    test_metrics = metrics_block(test_trades, RESET_CAPITAL)
    print(f"TEST metrics: {test_metrics}", flush=True)

    parity = verify_rolling_membership_parity(trades)
    print(f"parity check: {parity['membership_ok']}/{parity['sample_size']} membership-ok, "
          f"{parity['holding_period_ok']}/{parity['sample_size']} holding-period-ok, "
          f"{len(parity['failures'])} failures", flush=True)
    if parity["failures"]:
        print("FAILURES:", parity["failures"][:10], flush=True)

    trades_df.to_csv(OUTPUT_DIR / "rolling40_full_trades.csv", index=False)
    if test_trades:
        pd.DataFrame(test_trades).to_csv(OUTPUT_DIR / "rolling40_test_trades.csv", index=False)

    result = {
        "variant": "rolling40",
        "label": "Rolling-40 (quarterly market-cap reconstitution, SEC EDGAR XBRL, resurrected)",
        "n_input_tickers": len(POOL),
        "n_tickers_with_data": n_ok,
        "n_quarterly_reconstitutions": len(_SCHEDULE),
        "start": START.isoformat(), "end": END.isoformat(), "split": SPLIT.isoformat(),
        "reset_capital_gbp": RESET_CAPITAL,
        "backtest_convention": "reset-at-boundary",
        "engine": "simulate_portfolio_compounding_mtm_reset_at_boundary + monkeypatched analysis.universe.is_member (layered AND with real membership)",
        "config_reentry_cooldown_days": config["trading212_invest"].get("reentry_cooldown_days"),
        "config_min_momentum_pct": config["trading212_invest"].get("min_momentum_pct"),
        "config_per_trade_pct": per_trade_pct,
        "config_max_total_deployed": max_deployed_pct,
        "test_metrics": test_metrics,
        "full_walk_n_trades": len(trades),
        "decision_engine_parity_check": parity,
        "shares_outstanding_coverage": "483/500 candidate tickers resolved via SEC EDGAR XBRL; 16 no_data (incl. META, a known SEC/XBRL data gap per CLAUDE_HISTORY.md), 1 no_cik (AEP)",
    }
    with open(OUTPUT_DIR / "rolling40_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("wrote rolling40_result.json / _full_trades.csv / _test_trades.csv", flush=True)


if __name__ == "__main__":
    run()
