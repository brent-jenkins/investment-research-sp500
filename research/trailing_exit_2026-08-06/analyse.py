"""Analysis for the trailing-exit investigation.

The capacity-turnover decomposition is EXACT, not an approximation. For a
trade present in both runs, pnl = position_gbp * pnl_pct, so

    pnl_V - pnl_B = pos_B*(r_V - r_B)  +  (pos_V - pos_B)*r_V
                    \-- exit effect --/    \-- sizing effect --/

and trades present in only one run contribute their whole pnl. Total delta is
therefore split three ways with no residual:

  (a) EXIT effect     -- the direct effect of the changed exit rule on trades
                         that happened in both runs, at BASELINE position size
  (b) SIZING effect   -- same trades, re-sized because the capital path moved
  (c) TURNOVER effect -- pnl of trades that only the variant fills, minus pnl
                         of trades the baseline filled that the variant no
                         longer does (slots stay occupied longer)

(b) and (c) are both capacity consequences and are reported separately from
(a) and from each other; a combined capacity figure is shown alongside but
never substituted for the split.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("trailing/out")
PRES = Path("trailing/preserved")
pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 80)

VARIANTS = ["T1", "T2", "T3"]
CAPITAL = 50_000.0


def main() -> None:
    b = pd.read_csv(OUT / "variant_blocks.csv")
    td = pd.read_csv(OUT / "variant_trades.csv")
    pres = pd.read_csv(PRES / "block_comparison_wide.csv")

    b = b.merge(
        pres[["block", "A_sp500_pr_cagr", "C_ddmatch_zero_tr_cagr",
              "C_ddmatch_real_tr_cagr", "D_6040_tr_cagr", "strat_cagr"]],
        on="block", how="left",
    )
    # sanity: the sweep's own baseline must equal the preserved benchmark run
    drift = (b["baseline_cagr"] - b["strat_cagr"]).abs().max()
    print(f"baseline vs preserved benchmark-run CAGR: max abs drift {drift:.4f}pp "
          f"({'CONSISTENT' if drift < 0.005 else 'DRIFTED -- investigate'})\n")

    # ---------------- headline per-variant ------------------------------
    print("=== PER-BLOCK CAGR BY VARIANT ===")
    cols = ["block", "start", "era", "baseline_cagr", "T1_cagr", "T2_cagr", "T3_cagr",
            "baseline_n_trades", "T1_n_trades", "T2_n_trades", "T3_n_trades",
            "baseline_dd_peak", "T1_dd_peak", "T2_dd_peak", "T3_dd_peak"]
    print(b[cols].to_string(index=False))

    print("\n=== MEAN / MAX HOLDING DAYS (does the variant actually bind?) ===")
    print(b[["block", "baseline_mean_hold", "T1_mean_hold", "T2_mean_hold", "T3_mean_hold",
             "baseline_max_hold", "T1_max_hold", "T2_max_hold", "T3_max_hold"]].to_string(index=False))

    # ---------------- win rates + chained aggregate ---------------------
    recs = []
    for v in VARIANTS:
        gap_base = b[f"{v}_cagr"] - b["baseline_cagr"]
        gap_C0 = b[f"{v}_cagr"] - b["C_ddmatch_zero_tr_cagr"]
        gap_Cr = b[f"{v}_cagr"] - b["C_ddmatch_real_tr_cagr"]
        gap_D = b[f"{v}_cagr"] - b["D_6040_tr_cagr"]
        chained = np.prod(1 + b[f"{v}_total"] / 100.0) - 1
        yrs = b["years"].sum()
        recs.append({
            "variant": v,
            "wins_vs_baseline": int((gap_base > 0).sum()),
            "median_gap_vs_baseline_pp": round(float(gap_base.median()), 2),
            "mean_gap_vs_baseline_pp": round(float(gap_base.mean()), 2),
            "wins_vs_legC_zero": int((gap_C0 > 0).sum()),
            "median_gap_vs_legC_zero_pp": round(float(gap_C0.median()), 2),
            "wins_vs_legC_real": int((gap_Cr > 0).sum()),
            "median_gap_vs_legC_real_pp": round(float(gap_Cr.median()), 2),
            "wins_vs_legD": int((gap_D > 0).sum()),
            "median_gap_vs_legD_pp": round(float(gap_D.median()), 2),
            "chained_total_pct": round(chained * 100, 1),
            "chained_cagr_pct": round(((1 + chained) ** (1 / yrs) - 1) * 100, 2),
        })
    base_chained = np.prod(1 + b["baseline_total"] / 100.0) - 1
    yrs = b["years"].sum()
    recs.append({
        "variant": "baseline", "wins_vs_baseline": None,
        "chained_total_pct": round(base_chained * 100, 1),
        "chained_cagr_pct": round(((1 + base_chained) ** (1 / yrs) - 1) * 100, 2),
    })
    sm = pd.DataFrame(recs)
    sm.to_csv(OUT / "variant_summary.csv", index=False)
    print("\n=== HEADLINE: variant vs baseline and vs the passive legs ===")
    print(sm.to_string(index=False))

    # reference chained figures for the passive legs (recomputed identically)
    print("\n=== CHAINED AGGREGATE REFERENCE (reset-at-boundary blocks chained) ===")
    for lab, c in [("leg C-zero tr", "C_ddmatch_zero_tr_cagr"),
                   ("leg C-real tr", "C_ddmatch_real_tr_cagr"),
                   ("leg D 60/40 tr", "D_6040_tr_cagr"),
                   ("leg A S&P pr", "A_sp500_pr_cagr")]:
        r = (1 + b[c] / 100.0) ** b["years"] - 1
        tot = np.prod(1 + r) - 1
        print(f"  {lab:16s} chained {tot*100:8.1f}%   CAGR {((1+tot)**(1/yrs)-1)*100:6.2f}%")

    # ---------------- capacity-turnover decomposition -------------------
    print("\n=== CAPACITY-TURNOVER DECOMPOSITION (exact, three-way) ===")
    dec = []
    for v in VARIANTS:
        for blk in sorted(b["block"].unique()):
            B = td[(td.variant == "baseline") & (td.block == blk)].copy()
            V = td[(td.variant == v) & (td.block == blk)].copy()
            key = ["ticker", "entry_date"]
            B["k"] = list(zip(B.ticker, B.entry_date))
            V["k"] = list(zip(V.ticker, V.entry_date))
            # a (ticker, entry_date) can in principle repeat; keep first
            Bi = B.drop_duplicates("k").set_index("k")
            Vi = V.drop_duplicates("k").set_index("k")
            common = Bi.index.intersection(Vi.index)
            bc, vc = Bi.loc[common], Vi.loc[common]

            exit_eff = float((bc.position_gbp * (vc.pnl_pct - bc.pnl_pct)).sum())
            size_eff = float(((vc.position_gbp - bc.position_gbp) * vc.pnl_pct).sum())
            v_only = float(Vi.loc[Vi.index.difference(common), "pnl_gbp"].sum())
            b_only = float(Bi.loc[Bi.index.difference(common), "pnl_gbp"].sum())
            turnover = v_only - b_only
            total = float(Vi.pnl_gbp.sum() - Bi.pnl_gbp.sum())
            dec.append({
                "variant": v, "block": blk,
                "n_common": len(common),
                "n_variant_only": len(Vi.index.difference(common)),
                "n_baseline_only": len(Bi.index.difference(common)),
                "exit_effect_gbp": round(exit_eff, 2),
                "sizing_effect_gbp": round(size_eff, 2),
                "turnover_effect_gbp": round(turnover, 2),
                "capacity_combined_gbp": round(size_eff + turnover, 2),
                "total_delta_gbp": round(total, 2),
                "residual_gbp": round(total - (exit_eff + size_eff + turnover), 2),
            })
    dd = pd.DataFrame(dec)
    dd.to_csv(OUT / "capacity_decomposition.csv", index=False)
    maxres = dd["residual_gbp"].abs().max()
    print(f"decomposition identity check: max |residual| = £{maxres:.2f} "
          f"({'EXACT' if maxres < 0.01 else 'NOT EXACT -- investigate'})")

    agg = dd.groupby("variant")[["exit_effect_gbp", "sizing_effect_gbp",
                                 "turnover_effect_gbp", "capacity_combined_gbp",
                                 "total_delta_gbp"]].sum().round(0)
    agg["n_variant_only"] = dd.groupby("variant")["n_variant_only"].sum()
    agg["n_baseline_only"] = dd.groupby("variant")["n_baseline_only"].sum()
    print("\nSummed across all 16 blocks (GBP, on £50k per block):")
    print(agg.to_string())
    print("\nSign reading: exit_effect is the DIRECT effect of the rule change; "
          "sizing+turnover is the CAPACITY consequence.")
    for v in VARIANTS:
        r = agg.loc[v]
        rev = (r.exit_effect_gbp > 0) and (r.total_delta_gbp < 0)
        print(f"  {v}: direct £{r.exit_effect_gbp:+,.0f} | capacity "
              f"£{r.capacity_combined_gbp:+,.0f} | net £{r.total_delta_gbp:+,.0f}"
              f"{'   <-- CAPACITY REVERSES THE DIRECT EFFECT' if rev else ''}")

    print("\nPer-block decomposition:")
    print(dd.to_string(index=False))

    # ---------------- era decomposition ---------------------------------
    print("\n=== ERA DECOMPOSITION (vs baseline) ===")
    era_recs = []
    for era, sub in b.groupby("era"):
        for v in VARIANTS:
            g = sub[f"{v}_cagr"] - sub["baseline_cagr"]
            gC = sub[f"{v}_cagr"] - sub["C_ddmatch_real_tr_cagr"]
            era_recs.append({
                "era": era, "variant": v, "n_blocks": len(sub),
                "wins_vs_baseline": int((g > 0).sum()),
                "median_gap_vs_baseline_pp": round(float(g.median()), 2),
                "mean_gap_vs_baseline_pp": round(float(g.mean()), 2),
                "wins_vs_legC_real": int((gC > 0).sum()),
                "median_gap_vs_legC_real_pp": round(float(gC.median()), 2),
            })
    ed = pd.DataFrame(era_recs)
    ed.to_csv(OUT / "era_decomposition.csv", index=False)
    print(ed.to_string(index=False))

    # ---------------- acid test: block 11 --------------------------------
    print("\n=== PRE-SPECIFIED ACID TEST: BLOCK 11 (2020-06 -> 2021-09 melt-up) ===")
    r = b[b.block == 11].iloc[0]
    sp = r["A_sp500_pr_cagr"]
    print(f"S&P 500 (price return) CAGR over block 11: {sp:.2f}%")
    for v in ["baseline"] + VARIANTS:
        print(f"  {v:9s} CAGR {r[f'{v}_cagr']:6.2f}%   gap vs S&P {r[f'{v}_cagr']-sp:+7.2f}pp"
              f"   trades {int(r[f'{v}_n_trades'])}  mean hold {r[f'{v}_mean_hold']}"
              f"  max hold {r[f'{v}_max_hold']}")
    print(f"\nbaseline gap {r['baseline_cagr']-sp:+.2f}pp -> best variant gap "
          f"{max(r[f'{v}_cagr'] for v in VARIANTS)-sp:+.2f}pp")

    # ---------------- exit mix -------------------------------------------
    print("\n=== EXIT-REASON MIX (all blocks pooled) ===")
    mix = td.groupby(["variant", "exit_reason"]).size().unstack(fill_value=0)
    print(mix.to_string())
    print("\nMean pnl_pct by exit reason:")
    print((td.groupby(["variant", "exit_reason"])["pnl_pct"].mean() * 100).round(2).unstack().to_string())

    b.to_csv(OUT / "variant_blocks_enriched.csv", index=False)
    print("\nWrote variant_summary.csv, capacity_decomposition.csv, "
          "era_decomposition.csv, variant_blocks_enriched.csv")


if __name__ == "__main__":
    main()
