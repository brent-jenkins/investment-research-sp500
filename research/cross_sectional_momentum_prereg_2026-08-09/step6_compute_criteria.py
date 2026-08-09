"""Compute all PASS/FAIL conditions (PREREGISTRATION.md Section 7) for B/C/D
vs A, on both pools. Requires trades_{pool}_{A,B,C,D}.csv and
panel_{pool}_{A,B,C,D}.csv to already exist.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg")
sys.path.insert(0, r"c:\GitHub\investment-app-crosssectional-prereg\research\cross_sectional_momentum_prereg_2026-08-09")

import json

import pandas as pd

from benchmark_relative import benchmark_relative_report
from corroboration import paired_comparison, month_level_pairing, disagreement_population_comparison

OUT_DIR = "research/cross_sectional_momentum_prereg_2026-08-09"
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-07")
STARTING_CAPITAL = 50000.0

DD_CEILING_PP = 15.0
DEPLOY_GUARD_PP = 2.0
EXCESS_RETURN_MARGIN_PP = 2.5


def mean_deployed_pct(pool, arm):
    panel = pd.read_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv")
    return float(panel["current_deployed_pct"].mean())


def top_decile_overlap(pool, arm_x, arm_y):
    x = pd.read_csv(f"{OUT_DIR}/trades_{pool}_{arm_x}.csv")
    y = pd.read_csv(f"{OUT_DIR}/trades_{pool}_{arm_y}.csv")
    x_top = set(x.nlargest(max(1, len(x) // 10), "pnl_gbp")["ticker"])
    y_top = set(y.nlargest(max(1, len(y) // 10), "pnl_gbp")["ticker"])
    if not x_top and not y_top:
        return None
    return round(len(x_top & y_top) / len(x_top | y_top), 3)


def run_pool(pool):
    print(f"\n{'='*70}\nPOOL: {pool}\n{'='*70}", flush=True)
    trades = {arm: pd.read_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv") for arm in "ABCD"}

    bench = {arm: benchmark_relative_report(trades[arm], START, END, STARTING_CAPITAL) for arm in "ABCD"}
    for arm in "ABCD":
        b = bench[arm]
        print(f"Arm {arm}: CAGR={b['strat_continuous_cagr_pct']}% DD={b['strat_peak_relative_max_dd_pct']}% "
              f"excess_vs_SP500TR={b['excess_cagr_pct_vs_sp500tr']}pp blocks_beating={b['n_blocks_beating_sp500tr']}/{b['n_blocks_total']}", flush=True)
    print(f"^SP500TR continuous CAGR: {bench['A']['sp500tr_continuous_cagr_pct']}%  DD: {bench['A']['sp500tr_peak_relative_max_dd_pct']}%", flush=True)

    excess_a = bench["A"]["excess_cagr_pct_vs_sp500tr"]
    dd_a = bench["A"]["strat_peak_relative_max_dd_pct"]
    deploy_a = mean_deployed_pct(pool, "A")
    print(f"\nBaseline: excess={excess_a}pp DD={dd_a}% mean_deployed={deploy_a:.2f}%", flush=True)

    results = {}
    for arm in "BCD":
        excess_arm = bench[arm]["excess_cagr_pct_vs_sp500tr"]
        dd_arm = bench[arm]["strat_peak_relative_max_dd_pct"]
        deploy_arm = mean_deployed_pct(pool, arm)

        cond1_gap = round(excess_arm - excess_a, 2)
        cond1_pass = cond1_gap >= EXCESS_RETURN_MARGIN_PP

        dd_gap = round(dd_arm - dd_a, 2)  # negative dd_arm more negative = worse; want dd_arm not worse than dd_a - 15pp
        cond2_pass = dd_arm >= (dd_a - DD_CEILING_PP)

        deploy_gap = round(deploy_arm - deploy_a, 2)
        if arm == "D":
            cond3_pass = None  # exempt per Section 6/7.3 amendment
            cond3_note = "EXEMPT (Arm D, per amendment)"
        else:
            cond3_pass = deploy_gap >= -DEPLOY_GUARD_PP
            cond3_note = "PASS" if cond3_pass else "FAIL"

        overlap_vs_a = top_decile_overlap(pool, "A", arm)

        results[arm] = {
            "excess_return_pp": excess_arm, "excess_gap_vs_baseline_pp": cond1_gap, "cond1_excess_return_PASS": cond1_pass,
            "dd_pct": dd_arm, "dd_gap_vs_baseline_pp": dd_gap, "cond2_drawdown_ceiling_PASS": cond2_pass,
            "mean_deployed_pct": round(deploy_arm, 2), "deploy_gap_vs_baseline_pp": deploy_gap, "cond3_deployment_guard": cond3_note,
            "top_decile_ticker_overlap_vs_A": overlap_vs_a,
        }
        print(f"\nArm {arm} vs A: excess_gap={cond1_gap}pp (need >=+{EXCESS_RETURN_MARGIN_PP}) -> {'PASS' if cond1_pass else 'FAIL'} | "
              f"DD_gap={dd_gap}pp (ceiling {DD_CEILING_PP}pp) -> {'PASS' if cond2_pass else 'FAIL'} | "
              f"deploy_gap={deploy_gap}pp -> {cond3_note} | top_decile_overlap_vs_A={overlap_vs_a}", flush=True)

    # Corroboration instruments
    print("\n--- Corroboration: B vs C (exact-key, expected usable) ---", flush=True)
    bc = paired_comparison(f"{OUT_DIR}/trades_{pool}_B.csv", f"{OUT_DIR}/trades_{pool}_C.csv", "B", "C")
    print(json.dumps(bc, indent=2, default=str), flush=True)

    print("\n--- Corroboration: C vs D (exact-key, expected usable) ---", flush=True)
    cd = paired_comparison(f"{OUT_DIR}/trades_{pool}_C.csv", f"{OUT_DIR}/trades_{pool}_D.csv", "C", "D")
    print(json.dumps(cd, indent=2, default=str), flush=True)

    month_level = {}
    exact_key = {}
    disagreement = {}
    for arm in "BCD":
        print(f"\n--- Corroboration: A vs {arm} (exact-key) ---", flush=True)
        ek = paired_comparison(f"{OUT_DIR}/trades_{pool}_A.csv", f"{OUT_DIR}/trades_{pool}_{arm}.csv", "A", arm)
        print(json.dumps(ek, indent=2, default=str), flush=True)
        exact_key[arm] = ek

        print(f"\n--- Corroboration: A vs {arm} (month-level, primary instrument) ---", flush=True)
        ml = month_level_pairing(f"{OUT_DIR}/trades_{pool}_A.csv", f"{OUT_DIR}/trades_{pool}_{arm}.csv", "A", arm)
        print(json.dumps(ml, indent=2, default=str), flush=True)
        month_level[arm] = ml

        print(f"\n--- Corroboration: A vs {arm} (disagreement distribution, supplementary) ---", flush=True)
        dp = disagreement_population_comparison(f"{OUT_DIR}/trades_{pool}_A.csv", f"{OUT_DIR}/trades_{pool}_{arm}.csv", "A", arm)
        print(json.dumps(dp, indent=2, default=str), flush=True)
        disagreement[arm] = dp

    out = {
        "pool": pool, "benchmark": {a: bench[a] for a in "ABCD"},
        "baseline": {"excess_return_pp": excess_a, "dd_pct": dd_a, "mean_deployed_pct": deploy_a},
        "arms": results,
        "corroboration": {
            "B_vs_C_exact": bc, "C_vs_D_exact": cd,
            "A_vs_arm_exact": exact_key, "A_vs_arm_month_level": month_level,
            "A_vs_arm_disagreement": disagreement,
        },
    }
    with open(f"{OUT_DIR}/criteria_{pool}.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out


if __name__ == "__main__":
    for pool in ["pool1", "pool2_clean"]:
        run_pool(pool)
