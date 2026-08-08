"""Part 7: momentum-ranked admission vs random admission from the same
gate-passing set, Pool 2 (get_research_stock_candidates(), current
163-quarantined pool -- the "_final" cache, matching production's current
state) as the primary universe. Arm R = instrumented_engine.simulate_instrumented(
..., admission_mode="random", rng_seed=<seed>) -- identical gate, identical
capacity constraint, identical exit rules, identical sizing; the ONLY change
from the real baseline engine is that _admit_group shuffles the already-
gate-passed candidate list instead of sorting it by momentum descending.

Parallelized across worker processes (multiprocessing) -- each worker loads
the 141MB cache once and runs its assigned block of seeds sequentially, to
avoid re-paying the pickle-load cost per seed. Each run's summary (not the
full trade list, to keep memory/IO bounded across 100 runs) is written to
its own worker-specific CSV; combine_results.py-equivalent logic at the
bottom concatenates them once all workers finish.

Cost-adjusted CAGR here models FX round-trip cost only (0.30% of each run's
own total notional, T212's confirmed rate -- see
research/deployment_cap_sweep_2026-08-06/cost_adjustment.py, reused
methodology), NOT idle-cash credit -- idle credit needs a full daily
deployment panel per run, which is not cheap to compute across 100 runs and
would only ADD a small (historically ~0.3-0.5pp/yr), roughly constant credit
back to every run's CAGR. Omitting it makes every run's own cost-adjusted
CAGR a conservative (slightly understated) figure by a roughly constant
amount across runs -- explicitly NOT a bias toward one arm over another
(both draw from the same idle-cash mechanics), so it does not distort the
baseline's PERCENTILE within the random distribution, only the distribution's
absolute level. Stated plainly, not glossed over, per this project's own
capacity-cascade / cost-deferral standing requirements.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import multiprocessing as mp
import pickle
import time

import pandas as pd
import yaml

from analysis.backtest import compute_metrics_fixed, compute_peak_relative_drawdown_pct, equity_curve_gbp
from instrumented_engine import simulate_instrumented

OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"
CACHE_PATH = r"c:\GitHub\investment-app-selection-audit\research\stop_loss_reconfiguration_2026-08-07\_cache_pool2_final.pkl"
MAX_DEPLOYED_PCT = 0.70
N_SEEDS = 100
N_WORKERS = 7
FX_ROUND_TRIP_FEE = 0.0030
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")
TOTAL_YEARS = (END - START).days / 365.25


def _worker(args):
    worker_id, seeds, config = args
    t0 = time.time()
    with open(CACHE_PATH, "rb") as fh:
        data = pickle.load(fh)
    caches = data["caches"]
    gspc_close = data["gspc_close"]
    per_trade_pct = float(config.get("trading212_invest", {}).get("per_trade_pct", 0.05))
    print(f"[worker {worker_id}] cache loaded ({time.time()-t0:.0f}s), running {len(seeds)} seeds", flush=True)

    rows = []
    for seed in seeds:
        ts = time.time()
        result = simulate_instrumented(
            caches, config, gspc_close, per_trade_pct, MAX_DEPLOYED_PCT,
            disable_stop_loss=False, admission_mode="random", rng_seed=seed, log_candidates=False,
        )
        trades = result["trades"]
        starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))
        m = compute_metrics_fixed(trades, starting_capital)
        peak_dd = compute_peak_relative_drawdown_pct(trades, starting_capital)
        total_notional = sum(t["position_gbp"] for t in trades)
        fx_cost_gbp = total_notional * FX_ROUND_TRIP_FEE
        ec = equity_curve_gbp(pd.DataFrame(trades), starting_capital_gbp=starting_capital) if trades else pd.DataFrame()
        mean_balance = float(ec["equity_gbp"].mean()) if not ec.empty else starting_capital
        fx_drag_pct_per_year = (fx_cost_gbp / TOTAL_YEARS) / mean_balance * 100.0 if mean_balance > 0 else 0.0
        cost_adj_cagr = m["cagr_pct"] - fx_drag_pct_per_year

        rows.append({
            "seed": seed, "n_trades": len(trades), "total_pnl_gbp": round(sum(t["pnl_gbp"] for t in trades), 2),
            "cagr_pct": m["cagr_pct"], "max_drawdown_fixed_pct": m["max_drawdown_pct"],
            "peak_relative_drawdown_pct": peak_dd, "profit_factor": m.get("profit_factor"),
            "win_rate": m.get("win_rate"), "total_notional_gbp": round(total_notional, 2),
            "fx_drag_pct_per_year": round(fx_drag_pct_per_year, 4), "cost_adjusted_cagr_pct": round(cost_adj_cagr, 4),
        })
        print(f"[worker {worker_id}] seed={seed} n_trades={len(trades)} cagr={m['cagr_pct']:.2f}% "
              f"cost_adj_cagr={cost_adj_cagr:.2f}% dd={m['max_drawdown_pct']:.2f}% ({time.time()-ts:.0f}s)", flush=True)

    df = pd.DataFrame(rows)
    out_path = f"{OUT_DIR}/part7_armR_worker{worker_id}.csv"
    df.to_csv(out_path, index=False)
    print(f"[worker {worker_id}] DONE, wrote {out_path} ({time.time()-t0:.0f}s total)", flush=True)
    return out_path


def main():
    with open(r"c:\GitHub\investment-app-selection-audit\config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    seeds = list(range(N_SEEDS))
    chunks = [seeds[i::N_WORKERS] for i in range(N_WORKERS)]
    args = [(i, chunks[i], config) for i in range(N_WORKERS)]

    t0 = time.time()
    with mp.Pool(N_WORKERS) as pool:
        paths = pool.map(_worker, args)

    all_rows = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True).sort_values("seed")
    all_rows.to_csv(f"{OUT_DIR}/part7_armR_all_seeds.csv", index=False)
    print(f"\nCombined {len(all_rows)} runs -> part7_armR_all_seeds.csv ({time.time()-t0:.0f}s total)")
    print(all_rows.describe().to_string())


if __name__ == "__main__":
    main()
