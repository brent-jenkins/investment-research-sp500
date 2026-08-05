"""Re-runs the DAX (European stocks-only) study against the CURRENT
analysis.backtest.simulate_trades()/run_candidate() engine and CURRENT
config.yaml, using this investigation's own point-in-time-reconstructed
DAX_MEMBERSHIP (dax_membership.py) -- matching CLAUDE_HISTORY.md's "DAX
(European stocks-only) -- corrected 2026-07-31" section's own methodology
as closely as possible: identical signal/crisis-override/VaR-sizing/exit
logic to analysis/backtest.py's uncapped engine, identical 16-block
boundaries (make_blocks()), identical reconciled pass/fail rule (DD <=
1.5x benchmark AND PF > 1.0), benchmarked against ^GDAXI.

Backtest convention: CONTINUOUS WALK over the full [2007-01-01, end]
window, train/test computed by partitioning entry_date after the fact --
matching the original DAX study's own convention (simulate_trades() has
no compounding/balance-carryover concept at all: every ticker is sized
independently off the same fixed starting_capital_gbp notional baseline,
so continuous-walk vs reset-at-boundary makes no numerical difference for
this specific engine -- see CLAUDE.md's "Two backtest conventions"
section; this is stated explicitly per that section's own labeling
requirement, not left implicit).

Decision-engine parity note: the ONLY function monkeypatched here is
analysis.universe.is_member() -- swapped wholesale for DAX tickers (not
layered, since DAX tickers have no real S&P-500 membership concept at
all; the swap only ever affects the 40 DAX tickers being simulated, never
any other ticker). Every other decision function (evaluate_exit_reason,
passes_entry_gate, get_max_holding_days, _var_position_size_core) is the
real, unmodified analysis.backtest.simulate_trades() -- parity holds by
construction for those. verify_dax_membership_parity() below pulls real
admitted trades from this run's own output and independently re-derives
each one's membership status from dax_membership.py directly (not from
the monkeypatch itself), per CLAUDE.md's "Research scripts must verify
decision-engine parity" standing requirement.
"""
import json
from pathlib import Path

import pandas as pd

import env_setup

env_setup.patch_all()
config = env_setup.load_config()

from analysis import universe
from analysis.backtest import (
    precompute_ticker, slice_ticker, run_candidate, make_blocks,
    compute_metrics_fixed, compute_peak_relative_drawdown_pct, equity_curve_gbp,
)
from data.ingestion import get_price_df
from dax_membership import DAX_MEMBERSHIP, ALL_DAX_TICKERS, is_dax_member

START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
SPLIT = pd.Timestamp("2020-01-01")
STARTING_CAPITAL = float(config.get("backtest", {}).get("starting_capital_gbp", 50000))

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

_real_is_member = universe.is_member


def dax_is_member(ticker: str, check_date):
    if ticker in ALL_DAX_TICKERS:
        return is_dax_member(ticker, check_date)
    return _real_is_member(ticker, check_date)


def cagr_dd_for_series(close: pd.Series, b_start, b_end):
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


def block_passes(strat_metrics, sp_dd):
    dd_ok = sp_dd is None or sp_dd == 0 or abs(strat_metrics["max_drawdown_pct"]) <= abs(sp_dd) * 1.5
    pf_ok = strat_metrics.get("profit_factor", 0.0) > 1.0
    return bool(dd_ok and pf_ok)


def metrics_block(trades, starting_capital):
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


def verify_dax_membership_parity(trades, n_sample=25):
    from analysis.universe import get_max_holding_days

    if not trades:
        return {"sample_size": 0, "membership_ok": 0, "holding_period_ok": 0, "failures": []}
    df = pd.DataFrame(trades)
    sample = df.sample(n=min(n_sample, len(df)), random_state=42)
    failures = []
    membership_ok = holding_ok = 0
    for _, row in sample.iterrows():
        entry_date = row["entry_date"]
        entry_date = entry_date.date() if hasattr(entry_date, "date") else entry_date
        ticker = row["ticker"]
        ok = is_dax_member(ticker, entry_date)
        if ok:
            membership_ok += 1
        else:
            failures.append({"ticker": ticker, "entry_date": str(entry_date), "check": "membership"})
        max_hold = get_max_holding_days(ticker, config)
        if row["holding_days"] <= max_hold:
            holding_ok += 1
        else:
            failures.append({
                "ticker": ticker, "entry_date": str(entry_date),
                "holding_days": int(row["holding_days"]), "max_hold": max_hold, "check": "holding_period",
            })
    return {"sample_size": len(sample), "membership_ok": membership_ok,
            "holding_period_ok": holding_ok, "failures": failures}


def run():
    print(f"building caches for {len(ALL_DAX_TICKERS)} DAX tickers...", flush=True)
    caches = []
    for t in ALL_DAX_TICKERS:
        c = precompute_ticker(t, config)
        if c is not None:
            c = slice_ticker(c, START, END)
        caches.append(c)
    n_ok = sum(1 for c in caches if c is not None)
    print(f"{n_ok}/{len(ALL_DAX_TICKERS)} DAX tickers have usable price history "
          f"({[t for t, c in zip(ALL_DAX_TICKERS, caches) if c is None]} missing)", flush=True)

    gdaxi_df = get_price_df("^GDAXI", days=8000).set_index("date")
    gdaxi_df.index = pd.to_datetime(gdaxi_df.index)
    gdaxi_close_full = gdaxi_df["close"].astype(float)
    gdaxi_close = gdaxi_close_full[(gdaxi_close_full.index >= START) & (gdaxi_close_full.index <= END)]

    print("monkeypatching analysis.universe.is_member -> dax_is_member (DAX tickers only)...", flush=True)
    universe.is_member = dax_is_member
    try:
        trades = run_candidate(caches, config, gdaxi_close)
    finally:
        universe.is_member = _real_is_member
        print("restored the real analysis.universe.is_member", flush=True)

    print(f"{len(trades)} full-walk trades (crisis override still keyed to ^GSPC's own >=7% single-day "
          f"decline, matching the original DAX study and today's live system -- crisis logic is not "
          f"DAX-specific)", flush=True)

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])
        trades_df["exit_date"] = pd.to_datetime(trades_df["exit_date"])
        train_trades = trades_df[trades_df["entry_date"] < SPLIT].to_dict("records")
        test_trades = trades_df[trades_df["entry_date"] >= SPLIT].to_dict("records")
    else:
        train_trades, test_trades = [], []

    train_metrics = metrics_block(train_trades, STARTING_CAPITAL)
    test_metrics = metrics_block(test_trades, STARTING_CAPITAL)
    full_metrics = metrics_block(trades, STARTING_CAPITAL)
    print(f"TRAIN: {train_metrics}", flush=True)
    print(f"TEST: {test_metrics}", flush=True)
    print(f"FULL: {full_metrics}", flush=True)

    train_sp_cagr, train_sp_dd = cagr_dd_for_series(gdaxi_close_full, START, SPLIT)
    test_sp_cagr, test_sp_dd = cagr_dd_for_series(gdaxi_close_full, SPLIT, END)
    full_sp_cagr, full_sp_dd = cagr_dd_for_series(gdaxi_close_full, START, END)
    print(f"^GDAXI TRAIN cagr={train_sp_cagr:.2f} dd={train_sp_dd:.2f}", flush=True)
    print(f"^GDAXI TEST cagr={test_sp_cagr:.2f} dd={test_sp_dd:.2f}", flush=True)
    print(f"^GDAXI FULL cagr={full_sp_cagr:.2f} dd={full_sp_dd:.2f}", flush=True)

    blocks_raw = make_blocks(START, END, n_blocks=16)
    block_results = []
    n_pass = 0
    for i, (b_start, b_end) in enumerate(blocks_raw):
        strat_trades = (
            trades_df[(trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)].to_dict("records")
            if not trades_df.empty else []
        )
        strat_m = compute_metrics_fixed(strat_trades, STARTING_CAPITAL)
        sp_cagr, sp_dd = cagr_dd_for_series(gdaxi_close_full, b_start, b_end)
        passed = block_passes(strat_m, sp_dd)
        if passed:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(strat_trades), "strat_cagr": strat_m["cagr_pct"],
            "strat_dd": strat_m["max_drawdown_pct"], "strat_pf": strat_m.get("profit_factor", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": passed,
        })
        print(f"block {i} ({b_start.date()}..{b_end.date()}): n={len(strat_trades)} "
              f"pf={strat_m.get('profit_factor', 0.0):.2f} dd={strat_m['max_drawdown_pct']:.2f} "
              f"gdaxi_dd={sp_dd} pass={passed}", flush=True)

    print(f"BLOCKS PASSED: {n_pass}/16", flush=True)

    parity = verify_dax_membership_parity(trades)
    print(f"parity check: {parity['membership_ok']}/{parity['sample_size']} membership-ok, "
          f"{parity['holding_period_ok']}/{parity['sample_size']} holding-period-ok, "
          f"{len(parity['failures'])} failures", flush=True)
    if parity["failures"]:
        print("FAILURES:", parity["failures"][:10], flush=True)

    trades_df.to_csv(OUTPUT_DIR / "dax_full_trades.csv", index=False)

    result = {
        "variant": "dax",
        "label": "DAX (European stocks-only), point-in-time reconstructed",
        "n_input_tickers": len(ALL_DAX_TICKERS),
        "n_tickers_with_data": n_ok,
        "start": START.isoformat(), "end": END.isoformat(), "split": SPLIT.isoformat(),
        "starting_capital_gbp": STARTING_CAPITAL,
        "backtest_convention": "continuous-walk (no compounding/carryover in simulate_trades(), so continuous-walk and reset-at-boundary are numerically identical for this engine)",
        "engine": "analysis.backtest.run_candidate()/simulate_trades() (uncapped, frozen VaR-sized engine) + monkeypatched analysis.universe.is_member() for the 40 DAX tickers only",
        "config_starting_capital_gbp": STARTING_CAPITAL,
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "full_metrics": full_metrics,
        "benchmark_gdaxi": {
            "train_cagr_pct": round(train_sp_cagr, 2), "train_dd_pct": round(train_sp_dd, 2),
            "test_cagr_pct": round(test_sp_cagr, 2), "test_dd_pct": round(test_sp_dd, 2),
            "full_cagr_pct": round(full_sp_cagr, 2), "full_dd_pct": round(full_sp_dd, 2),
        },
        "blocks_passed": n_pass, "blocks_total": 16, "block_results": block_results,
        "decision_engine_parity_check": parity,
    }
    with open(OUTPUT_DIR / "dax_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    print("wrote dax_result.json / dax_full_trades.csv", flush=True)


if __name__ == "__main__":
    run()
