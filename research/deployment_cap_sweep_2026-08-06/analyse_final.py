"""Final headline/era/ordering analysis for the deployment cap sweep.

Consumes:
  - results/sweep_block_results_raw.json (this sweep's own strategy + re-
    solved leg C, per cap per block)
  - ../block_benchmark_comparison_2026-08-06/block_results_raw.json (legs
    A/B/D, UNCHANGED, reused verbatim since they don't depend on the
    strategy's own cap -- per the task spec's explicit permission to do so)
  - results/deployment_and_risk_summary.json
  - results/capacity_turnover_summary.json

Emits, per cap value: win rates vs every leg (never averaged), chained
aggregate CAGR + final ordering, era decomposition (GFC / dominant
2010-2019 / recent 2020-2026), block pass-count, plus the realised-
deployment / joint-risk-gap / capacity-turnover summaries pulled together
into one JSON + a few CSVs.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RESULTS = HERE / "results"
PRIOR = HERE.parent / "block_benchmark_comparison_2026-08-06" / "block_results_raw.json"
CAPS = [0.50, 0.70, 0.95]
CAPITAL = 50_000.0
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
TOTAL_YEARS = (END - START).days / 365.25

LEGS = ["A_pr", "A_tr", "B_pr", "B_tr", "D_pr", "D_tr",
        "C_zero_pr", "C_zero_tr", "C_real_pr", "C_real_tr"]


def cap_tag(cap):
    return str(cap).replace(".", "")


def era_of(block: int) -> str:
    if block <= 1:
        return "2007-2009_GFC"
    if block <= 10:
        return "2010-2019_bull"
    return "2020-2026_recent"


def main() -> None:
    prior = {r["block"]: r for r in json.loads(PRIOR.read_text())}
    sweep = json.loads((RESULTS / "sweep_block_results_raw.json").read_text())

    long_rows = []
    for cap in CAPS:
        cap_rows = [r for r in sweep if abs(r["cap"] - cap) < 1e-9]
        cap_rows.sort(key=lambda r: r["block"])
        for r in cap_rows:
            b = r["block"]
            p = prior[b]
            legc = r["legC"]
            row = {
                "cap": cap, "block": b, "start": r["start"], "end": r["end"],
                "era": era_of(b), "years": r["years"],
                "strat_trades": r["strat_n_trades"], "strat_cagr": r["strat_cagr_pct"],
                "strat_total": r["strat_total_return_pct"],
                "strat_dd_peak": r["strat_dd_peak_pct"], "strat_dd_fixed": r["strat_dd_fixed_pct"],
                "strat_rpd": r["strat_return_per_unit_dd"],
                "strat_ending_gbp": r["strat_ending_gbp"],
                "block_pass": r["block_pass"],
                "n_capacity_reject_days": r["n_capacity_reject_days"],
                "n_block_days": r["n_block_days"],
            }
            # legs A/B/D -- unchanged, reused from prior run
            for leg_prefix, key in (("A", "legA"), ("B", "legB")):
                for basis in ("pr", "tr"):
                    m = p[f"{key}_{basis}"]
                    row[f"{leg_prefix}_{basis}_cagr"] = m["cagr_pct"]
                    row[f"{leg_prefix}_{basis}_dd_peak"] = m["max_dd_peak_relative_pct"]
                    row[f"{leg_prefix}_{basis}_rpd"] = m["return_per_unit_dd"]
                    row[f"{leg_prefix}_{basis}_ending_gbp"] = m["ending_balance_gbp"]
            for basis in ("pr", "tr"):
                m = p["legD"][basis]
                row[f"D_{basis}_cagr"] = m["cagr_pct"]
                row[f"D_{basis}_dd_peak"] = m["max_dd_peak_relative_pct"]
                row[f"D_{basis}_rpd"] = m["return_per_unit_dd"]
                row[f"D_{basis}_ending_gbp"] = m["ending_balance_gbp"]
            # leg C -- RE-SOLVED for this cap
            for cash_label in ("zero", "real"):
                for basis in ("pr", "tr"):
                    key = f"{cash_label}_{basis}"
                    c = legc[key]
                    m = c["metrics"]
                    row[f"C_{key}_w"] = c["weight"]
                    row[f"C_{key}_dd"] = c["realised_dd"]
                    row[f"C_{key}_matched"] = c["matched"]
                    if m is not None:
                        row[f"C_{key}_cagr"] = m["cagr_pct"]
                        row[f"C_{key}_dd_peak"] = m["max_dd_peak_relative_pct"]
                        row[f"C_{key}_rpd"] = m["return_per_unit_dd"]
                        row[f"C_{key}_ending_gbp"] = m["ending_balance_gbp"]
                    else:
                        row[f"C_{key}_cagr"] = None
                        row[f"C_{key}_dd_peak"] = None
                        row[f"C_{key}_rpd"] = None
                        row[f"C_{key}_ending_gbp"] = None
            long_rows.append(row)

    wdf = pd.DataFrame(long_rows)
    wdf.to_csv(RESULTS / "sweep_comparison_wide.csv", index=False)

    # ---------------- win rates + chained aggregate, per cap -------------
    headline = {}
    for cap in CAPS:
        sub = wdf[wdf["cap"] == cap]
        leg_stats = {}
        for leg in LEGS:
            gap = sub["strat_cagr"] - sub[f"{leg}_cagr"]
            leg_stats[leg] = {
                "wins": int((gap > 0).sum()), "n_blocks": int(gap.notna().sum()),
                "win_rate_pct": round(100.0 * (gap > 0).mean(), 1),
                "median_gap_pp": round(float(gap.median()), 2),
                "mean_gap_pp": round(float(gap.mean()), 2),
                "std_gap_pp": round(float(gap.std()), 2),
            }
        # chained aggregate: product of per-block ending-balance ratios,
        # annualised over the full 2007-01-01 -> 2026-08-03 span
        chained = {}
        strat_factor = np.prod(sub["strat_ending_gbp"].values / CAPITAL)
        chained["STRATEGY"] = round((strat_factor ** (1.0 / TOTAL_YEARS) - 1.0) * 100.0, 2)
        for leg in LEGS:
            vals = sub[f"{leg}_ending_gbp"].dropna().values
            if len(vals) == 0:
                chained[leg] = None
                continue
            factor = np.prod(vals / CAPITAL)
            chained[leg] = round((factor ** (1.0 / TOTAL_YEARS) - 1.0) * 100.0, 2)

        ordering = sorted(chained.items(), key=lambda kv: (kv[1] is None, -(kv[1] or -999)))
        strat_rank = [i for i, (k, v) in enumerate(ordering, 1) if k == "STRATEGY"][0]

        # era decomposition vs C_zero_tr and A_pr
        era_recs = []
        for era, esub in sub.groupby("era"):
            for leg in ["C_zero_tr", "A_pr"]:
                gap = esub["strat_cagr"] - esub[f"{leg}_cagr"]
                era_recs.append({
                    "era": era, "leg": leg, "n_blocks": len(esub),
                    "wins": int((gap > 0).sum()),
                    "win_rate_pct": round(100.0 * (gap > 0).mean(), 1),
                    "median_gap_pp": round(float(gap.median()), 2),
                })

        n_pass = int(sub["block_pass"].sum())

        headline[cap_tag(cap)] = {
            "cap": cap, "leg_win_rates": leg_stats, "chained_aggregate_cagr": chained,
            "ordering_descending": ordering, "strategy_rank": strat_rank,
            "n_blocks_passing": n_pass, "n_blocks_total": len(sub),
            "era_decomposition": era_recs,
        }

    # ---------------- merge in deployment/risk/capacity-turnover ---------
    dep_risk = json.loads((RESULTS / "deployment_and_risk_summary.json").read_text()) \
        if (RESULTS / "deployment_and_risk_summary.json").exists() else None
    capturn = json.loads((RESULTS / "capacity_turnover_summary.json").read_text()) \
        if (RESULTS / "capacity_turnover_summary.json").exists() else None

    out = {"headline": headline, "deployment_and_risk": dep_risk, "capacity_turnover": capturn}
    (RESULTS / "final_analysis.json").write_text(json.dumps(out, indent=2, default=str))

    for cap in CAPS:
        tag = cap_tag(cap)
        h = headline[tag]
        print(f"\n=== cap={cap} ===")
        print(f"  n_blocks_passing: {h['n_blocks_passing']}/{h['n_blocks_total']}")
        print(f"  win rates: " + ", ".join(f"{leg}={h['leg_win_rates'][leg]['wins']}/16"
                                            f"({h['leg_win_rates'][leg]['median_gap_pp']:+.2f}pp)" for leg in LEGS))
        print(f"  chained aggregate: " + ", ".join(f"{k}={v}" for k, v in h["chained_aggregate_cagr"].items()))
        print(f"  ordering: " + " > ".join(f"{k}({v})" for k, v in h["ordering_descending"]))
        print(f"  strategy rank: {h['strategy_rank']}/{len(h['ordering_descending'])}")

    print(f"\nWrote {RESULTS/'sweep_comparison_wide.csv'}, {RESULTS/'final_analysis.json'}")


if __name__ == "__main__":
    main()
