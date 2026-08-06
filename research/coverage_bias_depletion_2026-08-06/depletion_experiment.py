"""Task D -- bound the historical-universe coverage bias by controlled
depletion of blocks 13-15 (the highest-coverage recent blocks) down to the
dominant era's ~64.3% coverage level, holding regime constant.

CONVENTIONS (stated explicitly, matching CLAUDE.md's standing requirement
and research/block_benchmark_comparison_2026-08-06/research_block_benchmarks.py,
whose harness this script reuses directly rather than reimplementing):
  * RESET-AT-BOUNDARY, corrected engine (simulate_portfolio_compounding_mtm,
    intraday-touch STOP_LOSS fill), GBP50,000 per block, window
    2007-01-01 -> 2026-08-03, 16 blocks (identical make_blocks() call).
  * CAGR annualised over the block's own calendar span (S&P's first/last
    bar inside the block), matching the harness's strat_cagr_pct
    convention -- not the trade-span convention.
  * Both drawdown conventions: peak-relative and fixed-denominator.
  * Runs against an isolated copy of the production DB
    (database/depletion_research.db), never production itself.

ARMS
  1  Baseline       -- full candidate pool, unmodified. Must reproduce the
                        existing block_results_raw.json rows for blocks
                        13/14/15 before anything else here is trusted.
  2  Random          -- remove a random 36% of the block's USABLE candidate
                        pool (tickers with real price data inside the
                        block -- a candidate with none is already inert,
                        so depleting it would be a no-op that inflates the
                        removal count without changing anything). >=10 seeds.
  3  Realistic       -- remove usable candidates whose S&P 500 membership
                        has since ended (as of today, the real mechanism
                        get_all_watchlist_entries() used to produce -- see
                        Task A). Achieved depletion %, not fixed at 36%.
  4  Adversarial     -- remove the worst-36%-by-realised-buy-and-hold-return
                        usable candidates (hindsight). Upper bound, not an
                        estimate.
"""
import json
import random
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import data.database as dbmod  # noqa: E402
RESEARCH_DB = REPO_ROOT / "database" / "depletion_research.db"
dbmod.get_db_path = lambda: RESEARCH_DB

from analysis import universe  # noqa: E402
from analysis.backtest import (  # noqa: E402
    compute_metrics_fixed,
    compute_peak_relative_drawdown_pct,
    make_blocks,
    precompute_ticker,
    simulate_portfolio_compounding_mtm,
    slice_ticker,
)
from analysis.validation import _strategy_ending_balance_gbp  # noqa: E402
from data.ingestion import get_price_df  # noqa: E402

OUT = Path(__file__).resolve().parent
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
N_BLOCKS = 16
CAPITAL = 50_000.0
TARGET_BLOCKS = [13, 14, 15]
DEPLETION_FRAC = 0.36
N_RANDOM_SEEDS = 12
TODAY = date(2026, 8, 6)

EXISTING_RESULTS = REPO_ROOT / "research" / "block_benchmark_comparison_2026-08-06" / "block_results_raw.json"


def _load_close(ticker: str, col: str = "close") -> pd.Series:
    df = get_price_df(ticker, days=9000).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df[col].astype(float).sort_index()


def block_metrics(trades, years, capital):
    m = compute_metrics_fixed(trades, capital)
    dd_peak = compute_peak_relative_drawdown_pct(trades, capital)
    end_bal = _strategy_ending_balance_gbp(trades, capital)
    total = (end_bal - capital) / capital * 100.0
    cagr_window = ((end_bal / capital) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    return {
        "n_trades": len(trades),
        "cagr_window_pct": round(cagr_window, 2),
        "total_return_pct": round(total, 2),
        "dd_peak_pct": dd_peak,
        "dd_fixed_pct": m["max_drawdown_pct"],
        "profit_factor": m.get("profit_factor", 0.0),
        "win_rate": round(m.get("win_rate", 0.0), 4),
        "ending_gbp": round(end_bal, 2),
    }


def run_block(cands, cache, bs, be, gb, years, config, per_trade_pct, max_deployed_pct):
    caches = [slice_ticker(cache.get(t), bs, be) for t in cands]
    trades = simulate_portfolio_compounding_mtm(
        caches, config, gb, per_trade_pct, max_deployed_pct, collect_rejections=False,
    )
    metrics = block_metrics(trades, years, CAPITAL)
    return trades, metrics


def main():
    config = yaml.safe_load(open(REPO_ROOT / "config.yaml"))
    t212 = config.get("trading212_invest", {})
    per_trade_pct = float(t212.get("per_trade_pct", 0.05))
    max_deployed_pct = float(t212.get("max_total_deployed", 0.5))

    print("Loading ^GSPC ...", flush=True)
    gspc = _load_close("^GSPC")

    blocks = make_blocks(START, END, n_blocks=N_BLOCKS)

    existing = {r["block"]: r for r in json.loads(EXISTING_RESULTS.read_text())}

    all_results = {}
    all_trades_tagged = []

    for i in TARGET_BLOCKS:
        bs, be = blocks[i]
        print(f"\n=== Block {i}: {bs.date()} -> {be.date()} ===", flush=True)
        gb = gspc[(gspc.index >= bs) & (gspc.index <= be)]
        bidx = gb.index
        years = (bidx[-1] - bidx[0]).days / 365.25

        cands = universe.get_live_stock_candidates(as_of=be.date())
        print(f"  {len(cands)} candidates at block end", flush=True)

        print("  Precomputing tickers ...", flush=True)
        cache = {}
        for n, t in enumerate(cands, 1):
            cache[t] = precompute_ticker(t, config)
            if n % 100 == 0:
                print(f"    {n}/{len(cands)}", flush=True)

        usable = [t for t in cands if slice_ticker(cache.get(t), bs, be) is not None]
        print(f"  {len(usable)} usable (have price data inside the block)", flush=True)

        n_remove = round(DEPLETION_FRAC * len(usable))

        # ---- Arm 1: baseline ------------------------------------------
        trades1, m1 = run_block(cands, cache, bs, be, gb, years, config, per_trade_pct, max_deployed_pct)
        print(f"  Arm1 baseline: {m1}", flush=True)

        exist = existing[i]
        parity = {
            "n_trades_match": trades1.__len__() == exist["strat_n_trades"],
            "cagr_match": m1["cagr_window_pct"] == exist["strat_cagr_pct"],
            "dd_peak_match": m1["dd_peak_pct"] == exist["strat_dd_peak_pct"],
            "dd_fixed_match": m1["dd_fixed_pct"] == exist["strat_dd_fixed_pct"],
            "ending_gbp_match": m1["ending_gbp"] == exist["strat_ending_gbp"],
        }
        print(f"  PARITY CHECK vs existing block_results_raw.json: {parity}", flush=True)

        # ---- Arm 2: random depletion, N seeds --------------------------
        arm2_runs = []
        for seed in range(N_RANDOM_SEEDS):
            rng = random.Random(seed)
            removed = set(rng.sample(usable, n_remove))
            depleted_cands = [t for t in cands if t not in removed]
            trades2, m2 = run_block(depleted_cands, cache, bs, be, gb, years, config, per_trade_pct, max_deployed_pct)
            arm2_runs.append({"seed": seed, "n_removed": len(removed), **m2})
        print(f"  Arm2 (random, {N_RANDOM_SEEDS} seeds) done", flush=True)

        # ---- Arm 3: realistic depletion (real membership departures) --
        departed = [t for t in usable if not universe.is_member(t, TODAY)]
        depleted_cands3 = [t for t in cands if t not in set(departed)]
        trades3, m3 = run_block(depleted_cands3, cache, bs, be, gb, years, config, per_trade_pct, max_deployed_pct)
        achieved_pct3 = len(departed) / len(usable) * 100.0
        print(f"  Arm3 (realistic): removed {len(departed)}/{len(usable)} ({achieved_pct3:.1f}%): {m3}", flush=True)

        # ---- Arm 4: adversarial depletion (worst realised return) -----
        returns = {}
        for t in usable:
            sl = slice_ticker(cache.get(t), bs, be)
            fr = sl["frame"]
            c0, c1 = float(fr["close"].iloc[0]), float(fr["close"].iloc[-1])
            returns[t] = (c1 / c0) - 1.0 if c0 > 0 else 0.0
        worst = sorted(returns, key=lambda t: returns[t])[:n_remove]
        depleted_cands4 = [t for t in cands if t not in set(worst)]
        trades4, m4 = run_block(depleted_cands4, cache, bs, be, gb, years, config, per_trade_pct, max_deployed_pct)
        print(f"  Arm4 (adversarial, removed {len(worst)}): {m4}", flush=True)

        # ---- Capacity-turnover split (direct vs admission effect) -----
        def turnover_split(depleted_trades, removed_set):
            direct_lost = [t for t in trades1 if t["ticker"] in removed_set]
            direct_lost_pnl = sum(t["pnl_gbp"] for t in direct_lost)
            baseline_survivor_trades = len(trades1) - len(direct_lost)
            admission_effect = len(depleted_trades) - baseline_survivor_trades
            return {
                "direct_trades_lost": len(direct_lost),
                "direct_pnl_lost_gbp": round(direct_lost_pnl, 2),
                "admission_effect_trades": admission_effect,
            }

        turnover3 = turnover_split(trades3, set(departed))
        turnover4 = turnover_split(trades4, set(worst))
        print(f"  Arm3 turnover split: {turnover3}", flush=True)
        print(f"  Arm4 turnover split: {turnover4}", flush=True)

        for tag, tl in [("arm1_baseline", trades1), ("arm3_realistic", trades3), ("arm4_adversarial", trades4)]:
            for t in tl:
                r = dict(t)
                r["block"] = i
                r["arm"] = tag
                all_trades_tagged.append(r)
        for run in arm2_runs:
            pass  # arm2 trade-level output omitted (12 seeds x thousands of trades); metrics preserved below

        all_results[i] = {
            "block_start": bs.date().isoformat(), "block_end": be.date().isoformat(),
            "years": round(years, 3), "n_candidates": len(cands), "n_usable": len(usable),
            "n_remove_target": n_remove,
            "arm1_baseline": m1,
            "parity_check_vs_existing": parity,
            "arm2_random_runs": arm2_runs,
            "arm2_summary": {
                "cagr_mean": round(float(np.mean([r["cagr_window_pct"] for r in arm2_runs])), 2),
                "cagr_std": round(float(np.std([r["cagr_window_pct"] for r in arm2_runs])), 2),
                "cagr_min": min(r["cagr_window_pct"] for r in arm2_runs),
                "cagr_max": max(r["cagr_window_pct"] for r in arm2_runs),
                "n_trades_mean": round(float(np.mean([r["n_trades"] for r in arm2_runs])), 1),
            },
            "arm3_realistic": m3, "arm3_achieved_depletion_pct": round(achieved_pct3, 1),
            "arm3_departed_tickers": departed, "arm3_turnover": turnover3,
            "arm4_adversarial": m4, "arm4_removed_tickers": worst, "arm4_turnover": turnover4,
        }

    OUT.mkdir(exist_ok=True)
    (OUT / "depletion_results.json").write_text(json.dumps(all_results, indent=2, default=str))
    pd.DataFrame(all_trades_tagged).to_csv(OUT / "depletion_trades_arm_tagged.csv", index=False)
    print(f"\nWrote {OUT/'depletion_results.json'} and {OUT/'depletion_trades_arm_tagged.csv'}")


if __name__ == "__main__":
    main()
