"""Trailing-momentum lookback-window sweep (2026-08-05).

THE CORE SIGNAL, not a peripheral parameter. analysis/signals.py's
MOMENTUM_LOOKBACK_DAYS = 252 (12 months) was chosen once during the V1->V2
rebuild, compared only against a 200-day SMA, and never swept.

HOW THE VARIANT IS BUILT -- deliberately NO engine fork.
analysis/backtest.py:precompute_ticker() calls signals.compute_momentum_series()
and signals.compute_signal_series(), both of which read the module-level
MOMENTUM_LOOKBACK_DAYS at call time (`from analysis import signals` at
backtest.py:21 means attribute lookup happens per call, not at import).
Setting that module constant before precompute therefore varies the signal
with ZERO change to any decision, sizing, exit or admission logic -- the
engine sees an ordinary precomputed frame and cannot tell the difference.

_MAX_ANCHOR_GAP_DAYS MUST SCALE WITH THE LOOKBACK -- this is not optional.
It is a calendar-staleness guard (see signals.py's own long comment) sized so
a genuine, gapless 252-TRADING-day window (~365 calendar days) is accepted
with slack, while a real multi-month hole is rejected. Left at its hardcoded
400 while sweeping to 24 months (504 trading days ~= 730 calendar days), it
would mark EVERY row stale and NaN out the entire signal -- silently
producing "this lookback generates no trades" rather than an error. Scaled
proportionally, preserving the exact current value at 252:

    _MAX_ANCHOR_GAP_DAYS = round(lookback_days * 400 / 252)
    # 63->100, 126->200, 189->300, 252->400 (unchanged), 315->500, 378->600, 504->800

precompute_ticker()'s own `len(df) < 300` minimum is deliberately NOT scaled
-- keeping it fixed means every arm sees the IDENTICAL candidate pool, so the
only thing varying across the sweep is the signal itself.

ENGINE: research/risk_adjusted_momentum_2026-08-05/risk_adjusted_engine.py's
simulate_with_risk_adjusted_ranking(rank_mode="raw", corr_haircut_threshold=None)
-- already committed and already proven BYTE-IDENTICAL to the real, unmodified
analysis.backtest.simulate_portfolio_compounding_mtm() by that investigation's
own self_check.py. Imported rather than re-copied specifically so there is one
verified copy of the block-truncation plumbing, not two that can drift.

CONVENTION: reset-at-boundary throughout (this is a variant comparison, per
CLAUDE.md's "Two backtest conventions").

Usage: python lookback_worker.py <months> <reset|blocks> <out_json>
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "risk_adjusted_momentum_2026-08-05"))

import pandas as pd
import yaml

import analysis.signals as signals
import analysis.universe as universe
import analysis.validation as validation_mod
from analysis.backtest import (
    precompute_ticker, compute_metrics_fixed, compute_peak_relative_drawdown_pct,
    make_blocks,
)
from analysis.validation import _block_passes, _cagr_dd_for_series, VALIDATION_START
from data.ingestion import get_price_df
from risk_adjusted_engine import simulate_with_risk_adjusted_ranking

CONFIG_PATH = ROOT / "config.yaml"
END = pd.Timestamp("2026-08-03")
TEST_SPLIT = validation_mod.VALIDATION_TRAIN_TEST_SPLIT

BASE_LOOKBACK = 252
BASE_ANCHOR_GAP = 400
TRADING_DAYS_PER_MONTH = 21  # 252/12, this codebase's own convention


def months_to_days(months: int) -> int:
    return int(round(months * TRADING_DAYS_PER_MONTH))


def apply_lookback(days: int) -> int:
    """Set the signal lookback and its proportionally-scaled staleness guard.
    Returns the guard used. At days==252 both are exactly the production
    values, which is what makes the self-check a real byte-identical proof."""
    gap = int(round(days * BASE_ANCHOR_GAP / BASE_LOOKBACK))
    signals.MOMENTUM_LOOKBACK_DAYS = days
    signals._MAX_ANCHOR_GAP_DAYS = gap
    return gap


def main():
    months, mode, out_path = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    days = months_to_days(months)
    gap = apply_lookback(days)

    config = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))
    t0 = time.time()

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    caches = [precompute_ticker(t, config) for t in candidate_tickers]
    print(f"[{months}m] candidates={len(candidate_tickers)} lookback={days}d "
          f"anchor_gap={gap}d precompute={time.time()-t0:.0f}s", flush=True)

    t212 = config["trading212_invest"]
    per_trade_pct = float(t212["per_trade_pct"])
    max_deployed_pct = float(t212["max_total_deployed"])
    starting_capital = float(config["backtest"]["starting_capital_gbp"])

    common = dict(corr_haircut_threshold=None, corr_haircut_factor=1.0, rank_mode="raw")
    result = {
        "months": months, "lookback_days": days, "anchor_gap_days": gap,
        "backtest_convention": "reset_at_boundary",
        "starting_capital_gbp": starting_capital, "mode": mode,
    }

    if mode == "reset":
        trades = simulate_with_risk_adjusted_ranking(
            caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
            block_start=TEST_SPLIT, block_end=END, **common,
        )
        m = compute_metrics_fixed(trades, starting_capital)
        result.update({
            "window": {"start": TEST_SPLIT.date().isoformat(), "end": END.date().isoformat()},
            "n_trades": len(trades), "metrics": m,
            "peak_relative_max_drawdown_pct": compute_peak_relative_drawdown_pct(trades, starting_capital),
            "trades": trades,
        })
        print(f"[{months}m] RESET trades={len(trades)} cagr={m.get('cagr_pct')} "
              f"dd={m.get('max_drawdown_pct')} pf={m.get('profit_factor')} "
              f"sharpe={m.get('sharpe_ratio')}", flush=True)
    else:
        blocks = make_blocks(VALIDATION_START, END, n_blocks=16)
        br, n_pass, all_tr = [], 0, {}
        for i, (b_s, b_e) in enumerate(blocks):
            trades = simulate_with_risk_adjusted_ranking(
                caches, config, gspc_close_full, per_trade_pct, max_deployed_pct,
                block_start=b_s, block_end=b_e, **common,
            )
            m = compute_metrics_fixed(trades, starting_capital)
            sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_s, b_e)
            passed = _block_passes(m, sp_dd)
            n_pass += int(passed)
            all_tr[i] = trades
            br.append({"block": i, "start": b_s.date().isoformat(), "end": b_e.date().isoformat(),
                       "n_trades": len(trades), "strat_cagr": m["cagr_pct"],
                       "strat_dd": m["max_drawdown_pct"], "strat_pf": m.get("profit_factor", 0.0),
                       "strat_sharpe": m.get("sharpe_ratio", 0.0),
                       "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
                       "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
                       "passed": passed})
            print(f"  [{months}m] block {i}: trades={len(trades)} cagr={m['cagr_pct']} "
                  f"pf={m.get('profit_factor')} pass={passed}", flush=True)
        result.update({"n_blocks_pass": n_pass, "n_blocks_total": len(blocks), "blocks": br,
                       "pass_rule": "abs(max_drawdown_pct) <= abs(sp_dd)*1.5 AND profit_factor > 1.0"})
        json.dump(all_tr, open(str(out_path).replace(".json", "_trades.json"), "w"),
                  indent=2, default=str)
        print(f"[{months}m] BLOCKS {n_pass}/16 pass", flush=True)

    result["elapsed_sec"] = time.time() - t0
    json.dump(result, open(out_path, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
