"""Feature screen, step 2 -- apply the pre-registered rule mechanically.

Order of operations is enforced by the code, not by discipline:
  1. RSI degeneracy pre-check (PREREGISTRATION Sec 4).
  2. Distribution + trade-level Spearman/Pearson for every feature, PRE-2022 only.
  3. |Spearman| < 0.05  -> NULL, and NO bucket table is computed for it. The
     quintile/robustness code is only reachable for features that clear the bar,
     so a null feature cannot be sliced looking for structure.
  4. Survivors only: quintile monotonicity + four robustness checks + the
     held-out 2022+ period.

Usage: python step2_screen.py <B_relative_rank|A_absolute_momentum>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent

FEATURES = ["adv20", "adv60", "vol_ratio_entry", "vol_trend", "dollar_volume20",
            "rsi14", "atr_pct", "realised_vol60", "price_at_entry",
            "gap_to_ma50", "gap_to_ma200", "ma50_above_ma200", "up_day_fraction60",
            "return_5d", "return_21d", "dist_from_52w_low"]

SPEARMAN_FLOOR = 0.05
HOLDOUT_START = pd.Timestamp("2022-01-01")


def rsi_degeneracy(s: pd.Series) -> dict:
    """Pre-registered: IQR < 15 points, or >80% inside a 20-point band =>
    UNINFORMATIVE BY CONSTRUCTION, and no correlation is interpreted."""
    q25, q75 = float(s.quantile(0.25)), float(s.quantile(0.75))
    iqr = q75 - q25
    # widest 20-point band share: slide over the observed range
    best = 0.0
    for lo in np.arange(np.floor(s.min()), np.ceil(s.max()) - 19, 1.0):
        share = float(((s >= lo) & (s < lo + 20)).mean())
        best = max(best, share)
    degenerate = (iqr < 15) or (best > 0.80)
    return {"p25": round(q25, 2), "p75": round(q75, 2), "iqr": round(iqr, 2),
            "max_20pt_band_share": round(best, 4), "degenerate": bool(degenerate)}


def quintile_table(d: pd.DataFrame, feat: str) -> list:
    d = d.copy()
    try:
        d["q"] = pd.qcut(d[feat], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
    except ValueError:
        return []
    rows = []
    for q in [c for c in ["Q1", "Q2", "Q3", "Q4", "Q5"] if c in set(d["q"].dropna())]:
        g = d[d["q"] == q]
        gx = g.drop(g["pnl_pct"].idxmax()) if len(g) > 1 else g
        rows.append({
            "q": q, "n": int(len(g)),
            "feat_min": round(float(g[feat].min()), 4),
            "feat_max": round(float(g[feat].max()), 4),
            "win_pct": round(100 * float((g["pnl_gbp"] > 0).mean()), 2),
            "mean_pnl_pct": round(100 * float(g["pnl_pct"].mean()), 3),
            "median_pnl_pct": round(100 * float(g["pnl_pct"].median()), 3),
            "mean_ex_top1": round(100 * float(gx["pnl_pct"].mean()), 3),
        })
    return rows


def is_monotonic(rows, key="mean_pnl_pct") -> bool:
    v = [r[key] for r in rows]
    return len(v) >= 3 and (all(a < b for a, b in zip(v, v[1:]))
                            or all(a > b for a, b in zip(v, v[1:])))


def main() -> None:
    label = sys.argv[1]
    td = pd.read_csv(HERE / f"features_{label}.csv")
    td["entry_date"] = pd.to_datetime(td["entry_date"])

    # momentum-at-entry is needed for the "is this a momentum proxy" check.
    # Joined from the stale-peak diagnostic's already-committed per-trade output
    # rather than recomputed, so both diagnostics use the identical values.
    sp_path = (HERE.parent / "stale_peak_diagnostic_2026-08-11" / f"metrics_{label}.csv")
    mm = pd.read_csv(sp_path, usecols=["ticker", "entry_date", "momentum_at_entry_pct"])
    mm["entry_date"] = pd.to_datetime(mm["entry_date"])
    before = len(td)
    td = td.merge(mm.drop_duplicates(["ticker", "entry_date"]),
                  on=["ticker", "entry_date"], how="left")
    assert len(td) == before, "join changed row count"
    print(f"joined momentum_at_entry_pct: {td['momentum_at_entry_pct'].notna().sum():,}"
          f"/{len(td):,} matched")
    pre = td[td["entry_date"] < HOLDOUT_START].reset_index(drop=True)
    hold = td[td["entry_date"] >= HOLDOUT_START].reset_index(drop=True)

    print("=" * 96)
    print(f"ENTRY-TIME FEATURE SCREEN -- {label}")
    print(f"  screening set (pre-2022): {len(pre):,} trades   "
          f"HELD OUT (2022+): {len(hold):,} trades")
    print(f"  pre-registered rule: NULL if |Spearman| < {SPEARMAN_FLOOR}; "
          f"no bucket-slicing of NULL features")
    print("=" * 96)

    out = {"label": label, "n_pre2022": int(len(pre)), "n_holdout": int(len(hold)),
           "spearman_floor": SPEARMAN_FLOOR, "features": {}}

    # ---- RSI degeneracy pre-check, before any correlation is interpreted ----
    rsi = rsi_degeneracy(pre["rsi14"])
    out["rsi_degeneracy"] = rsi
    print(f"\n### RSI14 degeneracy pre-check (pre-registered, runs first)")
    print(f"  p25={rsi['p25']}  p75={rsi['p75']}  IQR={rsi['iqr']}  "
          f"largest 20-point band holds {100*rsi['max_20pt_band_share']:.1f}% of trades")
    print(f"  => {'UNINFORMATIVE BY CONSTRUCTION' if rsi['degenerate'] else 'sufficient spread; screened normally'}")

    # ---- distributions + correlations for every feature ----
    print(f"\n### Screening table (pre-2022, n={len(pre):,})")
    print(f"  {'feature':>18} {'p10':>10} {'p50':>10} {'p90':>10} {'spearman':>9} "
          f"{'pearson':>9} {'p(sp)':>7}  verdict")
    survivors = []
    for f in FEATURES:
        s = pre[f].replace([np.inf, -np.inf], np.nan)
        d = pre[s.notna() & pre["pnl_pct"].notna()]
        sp = stats.spearmanr(d[f], d["pnl_pct"])
        pe = stats.pearsonr(d[f], d["pnl_pct"])
        rec = {
            "n": int(len(d)),
            "p10": round(float(s.quantile(0.10)), 4),
            "p50": round(float(s.quantile(0.50)), 4),
            "p90": round(float(s.quantile(0.90)), 4),
            "mean": round(float(s.mean()), 4), "sd": round(float(s.std()), 4),
            "spearman": round(float(sp.statistic), 4),
            "spearman_p": round(float(sp.pvalue), 5),
            "pearson": round(float(pe.statistic), 4),
        }
        if f == "rsi14" and rsi["degenerate"]:
            rec["verdict"] = "UNINFORMATIVE BY CONSTRUCTION"
        elif abs(rec["spearman"]) < SPEARMAN_FLOOR:
            rec["verdict"] = "NULL"
        else:
            rec["verdict"] = "clears floor -> robustness"
            survivors.append(f)
        out["features"][f] = rec
        print(f"  {f:>18} {rec['p10']:>10.4g} {rec['p50']:>10.4g} {rec['p90']:>10.4g} "
              f"{rec['spearman']:>+9.4f} {rec['pearson']:>+9.4f} {rec['spearman_p']:>7.4f}  "
              f"{rec['verdict']}")

    n_null = sum(1 for f in FEATURES if out["features"][f]["verdict"] == "NULL")
    print(f"\n  NULL: {n_null}/{len(FEATURES)}   clears floor: {len(survivors)}   "
          f"uninformative-by-construction: "
          f"{sum(1 for f in FEATURES if out['features'][f]['verdict'].startswith('UNINFORM'))}")

    # ---- survivors only: quintiles + robustness + hold-out ----
    if not survivors:
        print("\n### No feature cleared the floor. No bucket tables computed, by design.")
    for f in survivors:
        print(f"\n{'='*96}\n### {f} -- cleared the floor, running the four robustness checks")
        rows = quintile_table(pre, f)
        mono = is_monotonic(rows)
        print(f"  quintiles (pre-2022):")
        for r in rows:
            print(f"    {r['q']} n={r['n']:>4} range[{r['feat_min']:>10.4g},{r['feat_max']:>10.4g}] "
                  f"win={r['win_pct']:>6.2f}% mean={r['mean_pnl_pct']:>+7.3f}% "
                  f"med={r['median_pnl_pct']:>+7.3f}% ex-top1={r['mean_ex_top1']:>+7.3f}%")
        print(f"  monotonic across quintiles: {mono}")

        checks = {"monotonic_quintiles": bool(mono)}
        mono_x = is_monotonic(rows, "mean_ex_top1")
        checks["monotonic_ex_top1"] = bool(mono_x)
        for name, subset in (("excl_2008", pre[pre["entry_date"].dt.year != 2008]),
                             ("from_2015", pre[pre["entry_date"].dt.year >= 2015])):
            r2 = quintile_table(subset, f)
            checks[f"monotonic_{name}"] = bool(is_monotonic(r2))
            sp2 = stats.spearmanr(subset[f], subset["pnl_pct"]).statistic
            checks[f"spearman_{name}"] = round(float(sp2), 4)
            print(f"  {name}: monotonic={checks[f'monotonic_{name}']}  spearman={sp2:+.4f}")

        # conditioning on momentum quartile -- is it a momentum proxy?
        pre2 = pre.copy()
        pre2["mq"] = pd.qcut(pre2["momentum_at_entry_pct"], 4, labels=["M1", "M2", "M3", "M4"]) \
            if "momentum_at_entry_pct" in pre2.columns else None
        if pre2["mq"] is not None:
            within = {}
            for mq in ["M1", "M2", "M3", "M4"]:
                g = pre2[pre2["mq"] == mq]
                within[mq] = round(float(stats.spearmanr(g[f], g["pnl_pct"]).statistic), 4)
            checks["spearman_within_momentum_quartile"] = within
            same_sign = all(np.sign(v) == np.sign(out["features"][f]["spearman"])
                            for v in within.values())
            checks["consistent_sign_within_quartiles"] = bool(same_sign)
            print(f"  within momentum quartiles: {within}  consistent sign: {same_sign}")

        # HELD-OUT PERIOD -- only examined now
        sph = stats.spearmanr(hold[f], hold["pnl_pct"])
        rh = quintile_table(hold, f)
        checks["holdout_spearman"] = round(float(sph.statistic), 4)
        checks["holdout_monotonic"] = bool(is_monotonic(rh))
        checks["holdout_same_sign"] = bool(
            np.sign(sph.statistic) == np.sign(out["features"][f]["spearman"]))
        print(f"  HELD-OUT 2022+ (n={len(hold):,}): spearman={sph.statistic:+.4f}  "
              f"monotonic={checks['holdout_monotonic']}  "
              f"same sign as pre-2022: {checks['holdout_same_sign']}")

        passed = (checks["monotonic_quintiles"] and checks["monotonic_ex_top1"]
                  and checks["monotonic_excl_2008"] and checks["monotonic_from_2015"]
                  and checks.get("consistent_sign_within_quartiles", False))
        verdict = ("WORTH A PROPER STUDY" if passed and checks["holdout_same_sign"]
                   else "INCONCLUSIVE")
        checks["verdict"] = verdict
        out["features"][f]["robustness"] = checks
        out["features"][f]["verdict"] = verdict
        out["features"][f]["quintiles_pre2022"] = rows
        print(f"  ==> {verdict}")

    (HERE / f"screen_{label}.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote screen_{label}.json")


if __name__ == "__main__":
    main()
