"""§5.2 (THE ACTUAL QUESTION), §5.3 (capacity turnover) and §5.6 (top-decile
overlap), for each arm against the baseline.

§5.2 is written fresh rather than reusing the generic `paired` block in
step6_instruments.py, because that block computes "cell better on shared %"
over ALL shared keys -- which scores byte-identical, never-diverged trades as
"not better". That is precisely the error the exit sweep's 11.96% headline made
(235 of its 326 shared keys were identical trades). §5.2 requires them counted
SEPARATELY.

READ-ONLY over produced trade files. No simulation, no database writes.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
BASE_ARM = "a_none"
ARMS = [("b30", 0.30), ("b50", 0.50), ("b100", 1.00)]
POOLS = [("pool2_clean", "stage0_peaks_pool_2_clean.csv"),
         ("pool1", "stage0_peaks_pool_1.csv")]
TOL = 1e-9


def keyed(df):
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.strftime("%Y-%m-%d")
    df["key"] = df["ticker"] + "|" + df["entry_date"]
    return df[~df["key"].duplicated()]


def analyse(pool, peaks_file):
    base = keyed(pd.read_csv(HERE / f"trades_{pool}_{BASE_ARM}.csv"))
    peaks = pd.read_csv(HERE / peaks_file)
    peaks["key"] = (peaks["ticker"] + "|"
                    + pd.to_datetime(peaks["entry_date"]).dt.strftime("%Y-%m-%d"))
    peak_of = dict(zip(peaks["key"], peaks["peak_gain"]))
    base["peak_gain"] = base["key"].map(peak_of)

    out = {}
    for arm, T in ARMS:
        f = HERE / f"trades_{pool}_{arm}.csv"
        if not f.exists():
            print(f"  {pool}/{arm}: not run"); continue
        a = keyed(pd.read_csv(f))

        kb, ka = set(base["key"]), set(a["key"])
        shared = kb & ka
        b_only = base[~base["key"].isin(shared)]
        a_only = a[~a["key"].isin(shared)]

        # ---------- §5.2 -- restricted to the THRESHOLD-REACHING population ----------
        reach = base[base["peak_gain"] >= T]
        pop = reach[reach["key"].isin(shared)]
        bi = base.set_index("key")
        ai = a.set_index("key")
        rows = []
        for k in pop["key"]:
            bp, ap = bi.loc[k], ai.loc[k]
            identical = (bp["exit_date"] == ap["exit_date"]
                         and abs(float(bp["pnl_pct"]) - float(ap["pnl_pct"])) < TOL)
            rows.append({
                "key": k, "ticker": bp["ticker"], "entry_date": bp["entry_date"],
                "identical": identical,
                "base_reason": bp["exit_reason"], "arm_reason": ap["exit_reason"],
                "base_pnl_pct": float(bp["pnl_pct"]), "arm_pnl_pct": float(ap["pnl_pct"]),
                "delta_pp": (float(ap["pnl_pct"]) - float(bp["pnl_pct"])) * 100,
                "base_pnl_gbp": float(bp["pnl_gbp"]), "arm_pnl_gbp": float(ap["pnl_gbp"]),
                "delta_gbp": float(ap["pnl_gbp"]) - float(bp["pnl_gbp"]),
                "peak_gain": float(bp["peak_gain"]),
            })
        d = pd.DataFrame(rows)
        div = d[~d["identical"]] if len(d) else d
        prot = div[div["delta_pp"] > 0] if len(div) else div
        forg = div[div["delta_pp"] < 0] if len(div) else div
        s52 = {
            "threshold": T,
            "n_reached_in_baseline": int(len(reach)),
            "n_reached_and_shared": int(len(d)),
            "n_byte_identical": int(d["identical"].sum()) if len(d) else 0,
            "n_genuinely_diverged": int(len(div)),
            "n_protected": int(len(prot)), "n_forgone": int(len(forg)),
            "protected_sum_pp": round(float(prot["delta_pp"].sum()), 2) if len(prot) else 0.0,
            "forgone_sum_pp": round(float(forg["delta_pp"].sum()), 2) if len(forg) else 0.0,
            "net_sum_pp": round(float(div["delta_pp"].sum()), 2) if len(div) else 0.0,
            "protected_sum_gbp": round(float(prot["delta_gbp"].sum()), 2) if len(prot) else 0.0,
            "forgone_sum_gbp": round(float(forg["delta_gbp"].sum()), 2) if len(forg) else 0.0,
            "net_sum_gbp": round(float(div["delta_gbp"].sum()), 2) if len(div) else 0.0,
            "median_delta_pp": round(float(div["delta_pp"].median()), 2) if len(div) else None,
            "mean_delta_pp": round(float(div["delta_pp"].mean()), 2) if len(div) else None,
            "meets_10_diverged_floor": bool(len(div) >= 10),
        }
        if len(d):
            d.sort_values("delta_pp").to_csv(HERE / f"sec52_{pool}_{arm}.csv", index=False)

        # ---------- §5.3 -- capacity turnover, SEPARATE from the direct effect ----------
        s53 = {
            "n_trades_base": int(len(base)), "n_trades_arm": int(len(a)),
            "trade_count_delta": int(len(a) - len(base)),
            "n_arm_only_admissions": int(len(a_only)),
            "arm_only_pnl_gbp": round(float(a_only["pnl_gbp"].sum()), 2),
            "n_baseline_only": int(len(b_only)),
            "baseline_only_pnl_gbp": round(float(b_only["pnl_gbp"].sum()), 2),
            "arm_only_by_decade": (a_only["entry_date"].str[:3] + "0s").value_counts().to_dict(),
            "total_pnl_base": round(float(base["pnl_gbp"].sum()), 2),
            "total_pnl_arm": round(float(a["pnl_gbp"].sum()), 2),
            "total_pnl_delta": round(float(a["pnl_gbp"].sum() - base["pnl_gbp"].sum()), 2),
            "direct_effect_gbp": s52["net_sum_gbp"],
        }
        tot = s53["total_pnl_delta"]
        s53["capacity_residual_gbp"] = round(tot - s52["net_sum_gbp"], 2)
        s53["direct_share_of_delta_pct"] = (
            round(100 * s52["net_sum_gbp"] / tot, 1) if abs(tot) > 1e-9 else None)

        # ---------- §5.6 -- top-decile overlap ----------
        nb, na = max(1, len(base) // 10), max(1, len(a) // 10)
        tb = base.nlargest(nb, "pnl_gbp")
        ta = a.nlargest(na, "pnl_gbp")
        stb, sta = set(tb["key"]), set(ta["key"])
        trunc = d[(~d["identical"]) & (d["key"].isin(stb))] if len(d) else pd.DataFrame()
        s56 = {
            "shared_pct_of_base": round(100 * len(shared) / len(base), 1),
            "shared_pct_of_arm": round(100 * len(shared) / len(a), 1),
            "jaccard_all": round(len(shared) / len(kb | ka), 4),
            "top_decile_n_base": nb, "top_decile_n_arm": na,
            "top_decile_shared": int(len(stb & sta)),
            "top_decile_jaccard": round(len(stb & sta) / len(stb | sta), 4),
            "arm_pnl_share_from_non_shared_pct": round(
                100 * float(a_only["pnl_gbp"].sum()) / float(a["pnl_gbp"].sum()), 1)
            if abs(float(a["pnl_gbp"].sum())) > 1e-9 else None,
            "n_top_decile_baseline_truncated": int(len(trunc)),
            "top_decile_truncated_delta_gbp": round(float(trunc["delta_gbp"].sum()), 2)
            if len(trunc) else 0.0,
        }
        if len(trunc):
            trunc.sort_values("delta_gbp").to_csv(
                HERE / f"sec56_truncated_{pool}_{arm}.csv", index=False)

        out[arm] = {"sec52": s52, "sec53": s53, "sec56": s56}

        print(f"\n--- {pool} / {arm} (T=+{int(T*100)}%) ---")
        print(f"  §5.2 reached&shared={s52['n_reached_and_shared']}  "
              f"identical={s52['n_byte_identical']}  DIVERGED={s52['n_genuinely_diverged']}"
              f"  (>=10 floor: {s52['meets_10_diverged_floor']})")
        print(f"       PROTECTED {s52['n_protected']} = £{s52['protected_sum_gbp']:,.0f}"
              f"   FORGONE {s52['n_forgone']} = £{s52['forgone_sum_gbp']:,.0f}"
              f"   NET £{s52['net_sum_gbp']:,.0f}")
        print(f"       median Δ {s52['median_delta_pp']}pp   mean Δ {s52['mean_delta_pp']}pp")
        print(f"  §5.3 trades {s53['n_trades_base']} -> {s53['n_trades_arm']}"
              f"   arm-only admissions={s53['n_arm_only_admissions']} (£{s53['arm_only_pnl_gbp']:,.0f})")
        print(f"       total P&L Δ £{s53['total_pnl_delta']:,.0f} = direct £{s52['net_sum_gbp']:,.0f}"
              f" + capacity residual £{s53['capacity_residual_gbp']:,.0f}"
              f"   (direct share {s53['direct_share_of_delta_pct']}%)")
        print(f"  §5.6 shared {s56['shared_pct_of_base']}%/{s56['shared_pct_of_arm']}%"
              f"  jaccard={s56['jaccard_all']}  topdec_jacc={s56['top_decile_jaccard']}"
              f"  top-decile truncated={s56['n_top_decile_baseline_truncated']}"
              f" (£{s56['top_decile_truncated_delta_gbp']:,.0f})")
    return out


if __name__ == "__main__":
    res = {}
    for pool, pf in POOLS:
        print("=" * 78); print(pool); print("=" * 78)
        res[pool] = analyse(pool, pf)
    (HERE / "sec52_sec53_sec56_results.json").write_text(json.dumps(res, indent=2, default=str))
    print("\nwrote sec52_sec53_sec56_results.json")
