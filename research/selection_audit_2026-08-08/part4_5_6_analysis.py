"""Parts 4, 5, 6 analysis. Usage: python part4_5_6_analysis.py <pool1|pool2_final>

Reads <pool>_trades_instrumented.csv, <pool>_rejections_instrumented.csv,
<pool>_candidate_log.csv (from run_candidate_log.py) and the pool's own
_cache_<pool>.pkl (for the counterfactual simulator, Parts 5 & 6).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-selection-audit")

import json
import pickle
import random

import numpy as np
import pandas as pd
import yaml

from analysis import crisis
from analysis.backtest import get_max_holding_days
from counterfactual_sim import simulate_single_position

OUT_DIR = r"c:\GitHub\investment-app-selection-audit\research\selection_audit_2026-08-08"
CACHE_DIR = r"c:\GitHub\investment-app-selection-audit\research\stop_loss_reconfiguration_2026-08-07"
SAMPLE_N = 3000
RNG_SEED = 42


def main():
    pool = sys.argv[1]
    with open(r"c:\GitHub\investment-app-selection-audit\config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    trades = pd.read_csv(f"{OUT_DIR}/{pool}_trades_instrumented.csv", parse_dates=["entry_date", "exit_date"])
    rejections = pd.read_csv(f"{OUT_DIR}/{pool}_rejections_instrumented.csv", parse_dates=["date"])
    clog = pd.read_csv(f"{OUT_DIR}/{pool}_candidate_log.csv", parse_dates=["date"])
    print(f"[{pool}] trades={len(trades)} rejections={len(rejections)} candidate_log={len(clog)}")

    results = {"pool": pool}

    # =========================================================================
    # PART 4: timing vs quality in admission
    # =========================================================================
    n_eligible = trades["n_eligible_at_admission"].replace(0, np.nan)
    rank_pctile = trades["rank_at_admission"] / n_eligible  # 1/n .. n/n, lower = better rank
    top_quartile = rank_pctile <= 0.25
    trades["_top_quartile"] = top_quartile
    trades["_admitted_via_freed_capacity"] = trades["admitted_via_freed_capacity"].fillna(False)

    pct_outside_top_quartile = float((~top_quartile).mean())
    pct_freed_capacity = float(trades["_admitted_via_freed_capacity"].mean())

    hi = trades[trades["_top_quartile"] == True]
    lo = trades[trades["_top_quartile"] == False]
    freed = trades[trades["_admitted_via_freed_capacity"] == True]
    not_freed = trades[trades["_admitted_via_freed_capacity"] == False]

    def _summary(df):
        if df.empty:
            return {"n": 0}
        return {
            "n": int(len(df)), "win_rate": float((df["pnl_gbp"] > 0).mean()),
            "mean_pnl_pct": float(df["pnl_pct"].mean()), "median_pnl_pct": float(df["pnl_pct"].median()),
            "mean_pnl_gbp": float(df["pnl_gbp"].mean()),
        }

    results["part4"] = {
        "n_admitted_trades_with_rank_data": int(trades["rank_at_admission"].notna().sum()),
        "pct_outside_top_quartile_at_admission": pct_outside_top_quartile,
        "pct_admitted_via_freed_capacity": pct_freed_capacity,
        "rank_at_admission_percentile_distribution": {
            k: float(v) for k, v in rank_pctile.describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).items()
        },
        "outcomes_top_quartile_rank": _summary(hi),
        "outcomes_below_top_quartile_rank": _summary(lo),
        "outcomes_admitted_via_freed_capacity": _summary(freed),
        "outcomes_admitted_normal_capacity": _summary(not_freed),
    }
    print("\n--- PART 4 ---")
    print(json.dumps(results["part4"], indent=2, default=str))

    # =========================================================================
    # Load cache once for the counterfactual simulator (Parts 5 & 6)
    # =========================================================================
    with open(f"{CACHE_DIR}/_cache_{pool}.pkl", "rb") as fh:
        cache_data = pickle.load(fh)
    caches_by_ticker = {c["ticker"]: c for c in cache_data["caches"] if c is not None}
    gspc_close = cache_data["gspc_close"]
    raw_crisis_by_ticker = {}

    def _get_raw_crisis(ticker):
        if ticker not in raw_crisis_by_ticker:
            frame = caches_by_ticker[ticker]["frame"]
            raw_crisis_by_ticker[ticker] = crisis.compute_raw_trigger(gspc_close).reindex(frame.index).ffill().fillna(False)
        return raw_crisis_by_ticker[ticker]

    max_hold_cache = {}
    def _get_max_hold(ticker):
        if ticker not in max_hold_cache:
            max_hold_cache[ticker] = get_max_holding_days(ticker, config)
        return max_hold_cache[ticker]

    def _run_counterfactuals(df, label, sample_n=SAMPLE_N, seed=RNG_SEED):
        if df.empty:
            return {"n_population": 0, "n_sampled": 0}, pd.DataFrame()
        rng = random.Random(seed)
        n_pop = len(df)
        idx = list(df.index)
        if n_pop > sample_n:
            idx = rng.sample(idx, sample_n)
        sample = df.loc[idx]
        rows = []
        for _, r in sample.iterrows():
            ticker = r["ticker"]
            if ticker not in caches_by_ticker:
                continue
            entry_date = pd.Timestamp(r["date"])
            res = simulate_single_position(
                caches_by_ticker[ticker], entry_date, config, _get_max_hold(ticker), _get_raw_crisis(ticker),
            )
            if res is None or res["exit_reason"] in ("NO_FORWARD_DATA", "NO_EXIT_FOUND"):
                continue
            rows.append({"ticker": ticker, "date": entry_date, "momentum": r["momentum"], **res})
        cf = pd.DataFrame(rows)
        print(f"  [{label}] population={n_pop} sampled_ok={len(cf)}")
        if cf.empty:
            return {"n_population": int(n_pop), "n_sampled": 0}, cf
        outcomes = {
            "n_population": int(n_pop), "n_sampled": int(len(cf)),
            "win_rate": float((cf["pnl_pct"] > 0).mean()),
            "mean_pnl_pct": float(cf["pnl_pct"].mean()), "median_pnl_pct": float(cf["pnl_pct"].median()),
            "mean_holding_days": float(cf["holding_days"].mean()),
            "exit_reason_counts": cf["exit_reason"].value_counts().to_dict(),
        }
        return outcomes, cf

    # =========================================================================
    # PART 5: rejected-capacity population
    # =========================================================================
    print("\n--- PART 5: REJECTED_CAPACITY counterfactuals ---")
    rej_outcomes, rej_cf = _run_counterfactuals(rejections, "rejected_capacity")
    admitted_summary = _summary(trades)
    results["part5"] = {
        "n_rejected_capacity_events": int(len(rejections)),
        "rejected_counterfactual": rej_outcomes,
        "admitted_actual": admitted_summary,
    }
    rej_cf.to_csv(f"{OUT_DIR}/{pool}_part5_rejected_counterfactual_sample.csv", index=False)
    print(json.dumps(results["part5"], indent=2, default=str))

    # =========================================================================
    # PART 6: gate-failure population
    # =========================================================================
    print("\n--- PART 6: gate-failure counterfactuals ---")
    gate_fails = clog[clog["gate_pass"] == False].copy()
    gate_fails["momentum"] = pd.to_numeric(gate_fails["momentum"], errors="coerce")

    floor_band = gate_fails[(gate_fails["momentum"] >= 0) & (gate_fails["momentum"] <= 2.0) & (gate_fails["fail_reason"] == "GATE_FAIL_MOMENTUM_FLOOR")]
    below_zero = gate_fails[gate_fails["momentum"] < 0].dropna(subset=["momentum"])

    below_zero_deciles = []
    if not below_zero.empty:
        below_zero = below_zero.copy()
        below_zero["decile"] = pd.qcut(below_zero["momentum"], 10, labels=False, duplicates="drop") + 1
        for d, g in below_zero.groupby("decile"):
            samp_df = g.sample(n=min(len(g), 400), random_state=RNG_SEED) if len(g) > 400 else g
            outc, cf = _run_counterfactuals(samp_df, f"below_zero_decile_{d}", sample_n=len(samp_df))
            below_zero_deciles.append({
                "decile": int(d), "momentum_min": float(g["momentum"].min()), "momentum_max": float(g["momentum"].max()),
                "n_in_bucket": int(len(g)), **outc,
            })

    floor_outc, floor_cf = _run_counterfactuals(floor_band, "floor_0_2pct_band")
    gate_fail_all_outc, gate_fail_all_cf = _run_counterfactuals(gate_fails.dropna(subset=["momentum"]), "all_gate_fails")

    results["part6"] = {
        "n_gate_fail_events_total": int(len(gate_fails)),
        "n_floor_band_0_2pct": int(len(floor_band)),
        "n_below_zero_momentum": int(len(below_zero)),
        "floor_band_counterfactual": floor_outc,
        "all_gate_fail_counterfactual": gate_fail_all_outc,
        "below_zero_deciles": below_zero_deciles,
        "admitted_actual_for_comparison": admitted_summary,
    }
    print(json.dumps({k: v for k, v in results["part6"].items() if k != "below_zero_deciles"}, indent=2, default=str))
    for d in below_zero_deciles:
        print(f"  D{d['decile']} mom=[{d['momentum_min']:.2f},{d['momentum_max']:.2f}] n={d['n_in_bucket']} "
              f"win_rate={d.get('win_rate', float('nan')):.3f} mean_pnl_pct={d.get('mean_pnl_pct', float('nan')):.4f}")

    with open(f"{OUT_DIR}/{pool}_part4_5_6_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {OUT_DIR}/{pool}_part4_5_6_results.json")


if __name__ == "__main__":
    main()
