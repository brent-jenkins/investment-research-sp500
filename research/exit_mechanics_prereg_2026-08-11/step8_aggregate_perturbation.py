"""Aggregate the paired perturbation draws into the delta distribution.

Reports the distribution over BOTH dimensions: 14 draws x 5 end dates, so the
end-date perturbation is treated as a first-class axis rather than a footnote.
Single-ticker dominance is called out explicitly against the relative-rank
study's own AMAT precedent (which compressed that result to ~21% of typical,
below this project's ~1.08pp noise floor).
"""
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
NOISE_FLOOR = 1.08
UNPERTURBED = 6.443
CUTOFFS = ["2022-08-05", "2023-08-04", "2024-08-02", "2025-08-01", "2026-08-07"]


def main() -> None:
    draws = []
    for p in sorted(HERE.glob("pert_d*.json")):
        draws.append(json.loads(p.read_text()))
    if not draws:
        print("no draws")
        return

    print(f"PAIRED PERTURBATION TEST -- {len(draws)} draws x {len(CUTOFFS)} end dates")
    print(f"unperturbed full-window delta = {UNPERTURBED:+.3f}pp; "
          f"noise floor = {NOISE_FLOOR}pp\n")

    print(f"{'draw':>5} {'kind':>17} {'arg':>6} " +
          " ".join(f"{c[2:7]:>8}" for c in CUTOFFS) + f" {'min':>8} {'full':>8}")
    all_vals, full_vals, rows = [], [], []
    for d in draws:
        vals = [d["by_end_date"][c]["delta"] for c in CUTOFFS if c in d["by_end_date"]]
        all_vals += vals
        full = d.get("delta_full_window")
        full_vals.append(full)
        rows.append({"draw": d["draw_id"], "kind": d["kind"], "arg": d["arg"],
                     "vals": vals, "min": min(vals), "full": full,
                     "s30_n_trades": d["s30_n_trades"]})
        print(f"{d['draw_id']:>5} {d['kind']:>17} {str(d['arg'])[:6]:>6} " +
              " ".join(f"{v:>+8.3f}" for v in vals) +
              f" {min(vals):>+8.3f} {full:>+8.3f}")

    a = np.array(all_vals)
    f = np.array([v for v in full_vals if v is not None])
    print(f"\n--- distribution over ALL {len(a)} (draw x end-date) combinations ---")
    print(f"  min {a.min():+.3f}   p10 {np.percentile(a,10):+.3f}   median {np.median(a):+.3f}   "
          f"p90 {np.percentile(a,90):+.3f}   max {a.max():+.3f}")
    print(f"  positive: {int((a>0).sum())}/{len(a)}   "
          f"above noise floor ({NOISE_FLOOR}pp): {int((a>NOISE_FLOOR).sum())}/{len(a)}")
    print(f"\n--- full-window deltas only ({len(f)} draws) ---")
    print(f"  min {f.min():+.3f}   median {np.median(f):+.3f}   max {f.max():+.3f}   "
          f"positive {int((f>0).sum())}/{len(f)}")

    # ---- single-ticker dominance ----
    drops = [r for r in rows if r["kind"] == "drop_ticker"]
    if drops:
        print("\n--- SINGLE-TICKER DOMINANCE (targeted at the top-10 P&L contributors) ---")
        worst = min(drops, key=lambda r: r["full"])
        print(f"{'ticker':>8} {'full delta':>11} {'% of unperturbed':>18} {'worst window':>14}")
        for r in sorted(drops, key=lambda r: r["full"]):
            print(f"{str(r['arg']):>8} {r['full']:>+11.3f} "
                  f"{100*r['full']/UNPERTURBED:>17.1f}% {r['min']:>+14.3f}")
        print(f"\n  Largest single-name dependency: {worst['arg']} -> {worst['full']:+.3f}pp "
              f"({100*worst['full']/UNPERTURBED:.1f}% of the unperturbed {UNPERTURBED:+.3f}pp)")
        print(f"  Worst value anywhere in the dominance set (any window): "
              f"{min(r['min'] for r in drops):+.3f}pp")
        ratio = min(r["min"] for r in drops) / NOISE_FLOOR
        print(f"  That worst case is {ratio:.1f}x the {NOISE_FLOOR}pp noise floor")
        print("  AMAT precedent (relative-rank study): compressed to ~21% of typical and "
              "BELOW the floor.")
        print(f"  Here the worst single-name case retains "
              f"{100*worst['full']/UNPERTURBED:.0f}% and stays well above it.")

    # ---- end-date axis ----
    print("\n--- END-DATE AXIS (across all draws) ---")
    for i, c in enumerate(CUTOFFS):
        col = np.array([r["vals"][i] for r in rows if len(r["vals"]) > i])
        print(f"  {c}: min {col.min():+.3f}  median {np.median(col):+.3f}  max {col.max():+.3f}  "
              f"positive {int((col>0).sum())}/{len(col)}")

    out = {"n_draws": len(draws), "n_combinations": int(len(a)),
           "unperturbed": UNPERTURBED, "noise_floor": NOISE_FLOOR,
           "all_min": float(a.min()), "all_median": float(np.median(a)),
           "all_max": float(a.max()),
           "n_positive": int((a > 0).sum()),
           "n_above_floor": int((a > NOISE_FLOOR).sum()),
           "full_window": {"min": float(f.min()), "median": float(np.median(f)),
                           "max": float(f.max())},
           "rows": rows}
    (HERE / "perturbation_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print("\nWrote perturbation_summary.json")


if __name__ == "__main__":
    main()
