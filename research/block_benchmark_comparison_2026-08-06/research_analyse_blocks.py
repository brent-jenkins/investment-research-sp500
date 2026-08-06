"""Analysis + reporting for the 16-block, 5-leg benchmark comparison.

Consumes out/block_results_raw.json and emits the per-block CSVs and the
headline/era/regime tables.

Win rates are reported SEPARATELY for every leg and never averaged into a
single number, and every comparison is reported on both a price-return and
an estimated-total-return basis for the benchmark legs.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("out")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)

# The single-window reference this whole task exists to test the
# generality of (verified reproduced exactly -- see verify_0803.log).
REF_STRAT_CAGR = 10.74
REF_SP_CAGR = 13.73
REF_GAP_PP = REF_STRAT_CAGR - REF_SP_CAGR  # -2.99pp


def legs_for(r: dict) -> dict:
    """Flatten one block's raw result into {leg_label: metrics_dict}."""
    out = {
        "A_sp500_pr": r["legA_pr"], "A_sp500_tr": r["legA_tr"],
        "B_ndx100_pr": r["legB_pr"], "B_ndx100_tr": r["legB_tr"],
        "D_6040_pr": r["legD"]["pr"], "D_6040_tr": r["legD"]["tr"],
    }
    for k, v in r["legC"].items():          # zero_pr, zero_tr, real_pr, real_tr
        cash, basis = k.split("_")
        out[f"C_ddmatch_{cash}_{basis}"] = v["metrics"]
    return out


def main() -> None:
    rows = json.loads((OUT / "block_results_raw.json").read_text())
    print(f"{len(rows)} blocks loaded\n")

    # ---------------- per-block, per-leg long table --------------------
    long = []
    for r in rows:
        long.append({
            "block": r["block"], "start": r["start"], "end": r["end"], "era": r["era"],
            "years": r["years"], "leg": "STRATEGY",
            "n_trades": r["strat_n_trades"],
            "cagr_pct": r["strat_cagr_pct"],
            "total_return_pct": r["strat_total_return_pct"],
            "max_dd_peak_relative_pct": r["strat_dd_peak_pct"],
            "max_dd_fixed_pct": r["strat_dd_fixed_pct"],
            "return_per_unit_dd": r["strat_return_per_unit_dd"],
            "ending_balance_gbp": r["strat_ending_gbp"],
            "legC_weight": None, "legC_realised_dd": None, "legC_matched": None,
        })
        for label, m in legs_for(r).items():
            if m is None:
                continue
            cw = cr = cm = None
            if label.startswith("C_"):
                key = "_".join(label.split("_")[2:])   # zero_pr etc.
                c = r["legC"][key]
                cw, cr, cm = c["weight"], c["realised_dd"], c["matched"]
            long.append({
                "block": r["block"], "start": r["start"], "end": r["end"], "era": r["era"],
                "years": r["years"], "leg": label, "n_trades": None,
                "cagr_pct": m["cagr_pct"], "total_return_pct": m["total_return_pct"],
                "max_dd_peak_relative_pct": m["max_dd_peak_relative_pct"],
                "max_dd_fixed_pct": m["max_dd_fixed_pct"],
                "return_per_unit_dd": m["return_per_unit_dd"],
                "ending_balance_gbp": m["ending_balance_gbp"],
                "legC_weight": cw, "legC_realised_dd": cr, "legC_matched": cm,
            })
    ldf = pd.DataFrame(long)
    ldf.to_csv(OUT / "per_block_per_leg.csv", index=False)

    # ---------------- wide comparison table ----------------------------
    wide = []
    for r in rows:
        d = {
            "block": r["block"], "start": r["start"], "end": r["end"], "era": r["era"],
            "years": r["years"], "strat_trades": r["strat_n_trades"],
            "strat_cagr": r["strat_cagr_pct"],
            "strat_cagr_tradespan": r["strat_cagr_tradespan_pct"],
            "strat_total": r["strat_total_return_pct"],
            "strat_dd_peak": r["strat_dd_peak_pct"],
            "strat_dd_fixed": r["strat_dd_fixed_pct"],
            "strat_rpd": r["strat_return_per_unit_dd"],
            "sp_div_yield_est": r["implied_sp_div_yield_pct"],
            "ndx_div_yield_est": r["implied_ndx_div_yield_pct"],
        }
        for label, m in legs_for(r).items():
            d[f"{label}_cagr"] = m["cagr_pct"]
            d[f"{label}_dd_peak"] = m["max_dd_peak_relative_pct"]
            d[f"{label}_rpd"] = m["return_per_unit_dd"]
        for k, v in r["legC"].items():
            d[f"C_{k}_w"] = v["weight"]
            d[f"C_{k}_dd"] = v["realised_dd"]
            d[f"C_{k}_matched"] = v["matched"]
        wide.append(d)
    wdf = pd.DataFrame(wide)
    wdf.to_csv(OUT / "block_comparison_wide.csv", index=False)

    # ---------------- headline win rates -------------------------------
    LEGS = ["A_sp500_pr", "A_sp500_tr", "B_ndx100_pr", "B_ndx100_tr",
            "C_ddmatch_zero_pr", "C_ddmatch_zero_tr",
            "C_ddmatch_real_pr", "C_ddmatch_real_tr",
            "D_6040_pr", "D_6040_tr"]

    def summarise(df: pd.DataFrame, tag: str) -> pd.DataFrame:
        recs = []
        for leg in LEGS:
            gap = df["strat_cagr"] - df[f"{leg}_cagr"]
            rgap = df["strat_rpd"] - df[f"{leg}_rpd"]
            recs.append({
                "leg": leg, "scope": tag, "n_blocks": len(df),
                "wins_cagr": int((gap > 0).sum()),
                "win_rate_cagr_pct": round(100.0 * (gap > 0).mean(), 1),
                "wins_rpd": int((rgap > 0).sum()),
                "win_rate_rpd_pct": round(100.0 * (rgap > 0).mean(), 1),
                "median_gap_pp": round(float(gap.median()), 2),
                "mean_gap_pp": round(float(gap.mean()), 2),
                "std_gap_pp": round(float(gap.std()), 2),
                "min_gap_pp": round(float(gap.min()), 2),
                "p25_gap_pp": round(float(gap.quantile(0.25)), 2),
                "p75_gap_pp": round(float(gap.quantile(0.75)), 2),
                "max_gap_pp": round(float(gap.max()), 2),
            })
        return pd.DataFrame(recs)

    full = summarise(wdf, "all_16")
    ex0 = summarise(wdf[wdf["block"] > 0], "ex_block0")
    summary = pd.concat([full, ex0], ignore_index=True)
    summary.to_csv(OUT / "headline_win_rates.csv", index=False)

    print("=== HEADLINE: win rate vs each leg (never averaged) ===")
    print(summary.to_string(index=False))

    # ---------------- era decomposition --------------------------------
    era_recs = []
    for era, sub in wdf.groupby("era"):
        for leg in LEGS:
            gap = sub["strat_cagr"] - sub[f"{leg}_cagr"]
            era_recs.append({
                "era": era, "leg": leg, "n_blocks": len(sub),
                "wins_cagr": int((gap > 0).sum()),
                "win_rate_cagr_pct": round(100.0 * (gap > 0).mean(), 1),
                "median_gap_pp": round(float(gap.median()), 2),
                "mean_gap_pp": round(float(gap.mean()), 2),
            })
    edf = pd.DataFrame(era_recs)
    edf.to_csv(OUT / "era_decomposition.csv", index=False)
    print("\n=== ERA DECOMPOSITION ===")
    print(edf.to_string(index=False))

    # ---------------- regime conditioning ------------------------------
    # Benchmark's OWN return over the block, sign-split. Uses the S&P
    # price-return leg as the regime definition (stated, not implicit).
    wdf["regime"] = np.where(wdf["A_sp500_pr_cagr"] > 0, "rising", "falling")
    reg_recs = []
    for reg, sub in wdf.groupby("regime"):
        for leg in LEGS:
            gap = sub["strat_cagr"] - sub[f"{leg}_cagr"]
            reg_recs.append({
                "regime": reg, "leg": leg, "n_blocks": len(sub),
                "wins_cagr": int((gap > 0).sum()),
                "win_rate_cagr_pct": round(100.0 * (gap > 0).mean(), 1),
                "median_gap_pp": round(float(gap.median()), 2),
                "mean_gap_pp": round(float(gap.mean()), 2),
            })
    rdf = pd.DataFrame(reg_recs)
    rdf.to_csv(OUT / "regime_conditioning.csv", index=False)
    print("\n=== REGIME CONDITIONING (S&P price-return sign over the block) ===")
    print(rdf.to_string(index=False))
    print("\nBlocks by regime:")
    print(wdf[["block", "start", "end", "era", "regime", "A_sp500_pr_cagr",
               "strat_cagr", "strat_trades"]].to_string(index=False))

    # ---------------- where does 2020-2026 sit? ------------------------
    gapA = wdf["strat_cagr"] - wdf["A_sp500_pr_cagr"]
    pct_rank = float((gapA < REF_GAP_PP).mean()) * 100.0
    print(f"\n=== WHERE THE 2020-2026 SINGLE WINDOW SITS ===")
    print(f"single-window gap vs S&P (price return): {REF_GAP_PP:+.2f}pp")
    print(f"per-block gap vs leg A(pr): median {gapA.median():+.2f}pp, "
          f"mean {gapA.mean():+.2f}pp, sd {gapA.std():.2f}, "
          f"range [{gapA.min():+.2f}, {gapA.max():+.2f}]")
    print(f"percentile rank of the single-window gap within the 16 block gaps: "
          f"{pct_rank:.0f}th  ({int((gapA < REF_GAP_PP).sum())}/16 blocks worse)")

    # ---------------- leg C match quality ------------------------------
    print("\n=== LEG C MATCH QUALITY (ex-post, per block) ===")
    cq = wdf[["block", "start", "era", "strat_dd_peak",
              "C_zero_tr_w", "C_zero_tr_dd", "C_zero_tr_matched",
              "C_real_tr_w", "C_real_tr_dd", "C_real_tr_matched"]]
    print(cq.to_string(index=False))

    print("\n=== C-ZERO vs C-REAL SPREAD (the honest band) ===")
    for basis in ("pr", "tr"):
        gz = wdf["strat_cagr"] - wdf[f"C_ddmatch_zero_{basis}_cagr"]
        gr = wdf["strat_cagr"] - wdf[f"C_ddmatch_real_{basis}_cagr"]
        print(f"  basis={basis}: strategy beats C-zero in {int((gz>0).sum())}/16, "
              f"C-real in {int((gr>0).sum())}/16; "
              f"median gap {gz.median():+.2f}pp vs {gr.median():+.2f}pp "
              f"(spread {abs(gz.median()-gr.median()):.2f}pp)")

    print(f"\nWrote per_block_per_leg.csv, block_comparison_wide.csv, "
          f"headline_win_rates.csv, era_decomposition.csv, regime_conditioning.csv")


if __name__ == "__main__":
    main()
