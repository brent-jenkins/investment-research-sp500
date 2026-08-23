"""PREREGISTRATION.md Sec 3 -- THE MANDATORY GATE: realised effect size.

Reports, per arm, BEFORE any return figure exists:
  1. admissions changed versus baseline  (THE GATING MEASURE, floor 5%)
  2. same-day substitutions, distinguished from time-shifted admissions
  3. mean absolute rank movement among gate-passers and in the top decile
  4. undefined-correlation share

Also reports the Sec 4.3 / 4.5 design-constraint diagnostics that belong
with the gate rather than with returns: realised book correlation and mean
deployment.

DELIBERATELY COMPUTES NO RETURN METRIC. No CAGR, no excess return, no cost
adjustment, no arm-level total P&L. Sec 3's own rule.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
ARMS = ["baseline", "D1", "D2", "D3", "R1", "R2", "R3"]
K = {"baseline": 0.0, "D1": 0.25, "D2": 0.5, "D3": 1.0, "R1": -0.25, "R2": -0.5, "R3": -1.0}
CHANGE_FLOOR_PCT = 5.0


def key(df):
    return df["ticker"] + "|" + pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")


def book_corr(panel_path: Path, book) -> float | None:
    """Sec 4.3 -- mean pairwise correlation AMONG HELD positions, averaged
    over trading days. Answers whether the portfolio actually became less or
    more correlated, as opposed to merely being ordered differently.

    Computes the whole held x held block in ONE standardisation + matmul per
    day. A per-pair loop would restandardise the full window matrix ~14 times
    per day across ~4,900 days, which is the per-ticker fan-out cost this
    project has been bitten by four times.
    """
    import numpy as _np
    from corr_engine import CORR_WINDOW as _W, MIN_OVERLAP as _MO
    pn = pd.read_csv(panel_path)
    if "held" not in pn.columns:
        return None
    pn["date"] = pd.to_datetime(pn["date"])
    day_pos = {d: i for i, d in enumerate(book.dates)}
    vals = []
    for _, r in pn.iterrows():
        held = [t for t in str(r["held"]).split(";") if t and t in book.idx]
        if len(held) < 2:
            continue
        di = day_pos.get(pd.Timestamp(r["date"]))
        if di is None or di < _W:
            continue
        lo, hi = di - _W, di
        if not book._window_ok(lo, hi):
            continue
        cols = [book.idx[t] for t in held]
        W = book.R[lo:hi, cols]
        V = book.valid[lo:hi, cols]
        M = V.astype(_np.float64)
        n = M.sum(axis=0)
        with _np.errstate(invalid="ignore", divide="ignore"):
            mu = _np.where(n > 0, _np.where(V, W, 0.0).sum(axis=0) / _np.maximum(n, 1), 0.0)
            dev = _np.where(V, W - mu, 0.0)
            sd = _np.sqrt(_np.where(n > 1, (dev ** 2).sum(axis=0) / _np.maximum(n - 1, 1), 0.0))
            Z = _np.where(sd > 0, dev / _np.where(sd > 0, sd, 1.0), 0.0)
            num = Z.T @ Z
            cnt = M.T @ M
            C = _np.where(cnt >= _MO, num / _np.maximum(cnt - 1, 1), _np.nan)
        iu = _np.triu_indices(len(held), k=1)
        off = C[iu]
        off = off[_np.isfinite(off)]
        if off.size:
            vals.append(float(off.mean()))
    return round(float(_np.mean(vals)), 4) if vals else None


def main() -> None:
    pool = sys.argv[1]
    do_bookcorr = "--bookcorr" in sys.argv
    base_t = pd.read_csv(HERE / f"trades_{pool}_baseline.csv")
    base_keys = set(key(base_t))
    base_panel = pd.read_csv(HERE / f"panel_{pool}_baseline.csv")
    base_dep = float(base_panel["current_deployed_pct"].mean()) * 100.0

    book = None
    if do_bookcorr:
        import pickle
        sys.path.insert(0, str(HERE))
        from corr_engine import CorrelationBook
        with open(HERE / f"_cache_{pool}.pkl", "rb") as fh:
            cache = pickle.load(fh)
        dates = None
        for c in cache["caches"]:
            if c is None:
                continue
            dates = c["frame"].index if dates is None else dates.union(c["frame"].index)
        book = CorrelationBook(cache["caches"], dates)

    out = {}
    for arm in ARMS:
        tp = HERE / f"trades_{pool}_{arm}.csv"
        if not tp.exists():
            out[arm] = {"error": f"missing {tp.name}"}
            continue
        t = pd.read_csv(tp)
        ad = pd.read_csv(HERE / f"admissions_{pool}_{arm}.csv")
        cl = pd.read_csv(HERE / f"corrlog_{pool}_{arm}.csv")
        pn = pd.read_csv(HERE / f"panel_{pool}_{arm}.csv")
        if t.empty:
            out[arm] = {"error": "no trades -- gate would be vacuous"}
            continue

        ks = set(key(t))
        changed = len(ks ^ base_keys)
        denom = max(len(base_keys), 1)
        rec = {
            "k": K[arm],
            "trades": int(len(t)),
            "admissions_only_in_arm": int(len(ks - base_keys)),
            "admissions_only_in_baseline": int(len(base_keys - ks)),
            "admissions_changed": changed,
            "admissions_changed_pct": round(100.0 * changed / denom, 2),
        }
        rec["gate_verdict"] = (
            "-- (control)" if arm == "baseline"
            else "TESTED" if rec["admissions_changed_pct"] >= CHANGE_FLOOR_PCT
            else "NOT TESTED")

        # 2. same-day substitution vs time-shift
        b_by_day = base_t.assign(d=pd.to_datetime(base_t["entry_date"]).dt.date).groupby("d")["ticker"].apply(set)
        a_by_day = t.assign(d=pd.to_datetime(t["entry_date"]).dt.date).groupby("d")["ticker"].apply(set)
        subs = 0
        for d in set(b_by_day.index) & set(a_by_day.index):
            if (a_by_day[d] - b_by_day[d]) and (b_by_day[d] - a_by_day[d]):
                subs += 1
        rec["same_day_substitution_dates"] = subs
        rec["dates_arm_admitted"] = int(len(a_by_day))

        # 3. rank movement induced by the adjustment
        if {"rank_pct", "adjusted"}.issubset(ad.columns) and not ad.empty:
            rec["mean_abs_rank_pct_shift_at_admission"] = round(
                float((ad["rank_pct"] - (ad["adjusted"] + K[arm] * ad["corr_pct"])).abs().mean()), 6)
            rec["median_admitted_within_day_pctile"] = round(
                float(ad["rank_pctile_within_day"].median()) * 100, 3)
            rec["mean_corr_raw_at_admission"] = (
                round(float(ad["corr_raw"].dropna().mean()), 4) if ad["corr_raw"].notna().any() else None)

        # 4. undefined-correlation share
        if not cl.empty and {"n_candidates", "n_undefined"}.issubset(cl.columns):
            tot_c, tot_u = float(cl["n_candidates"].sum()), float(cl["n_undefined"].sum())
            rec["undefined_corr_share_pct"] = round(100.0 * tot_u / max(tot_c, 1), 2)
            rec["mean_candidates_per_day"] = round(float(cl["n_candidates"].mean()), 1)
            rec["mean_held_per_day"] = round(float(cl["n_held"].mean()), 2)

        # design-constraint diagnostics
        dep = float(pn["current_deployed_pct"].mean()) * 100.0
        rec["mean_deployed_pct"] = round(dep, 2)
        rec["deployment_divergence_pp"] = round(dep - base_dep, 2)
        rec["mean_n_open"] = round(float(pn["n_open_positions"].mean()), 2)
        if do_bookcorr:
            rec["realised_book_correlation"] = book_corr(HERE / f"panel_{pool}_{arm}.csv", book)
        out[arm] = rec

    (HERE / f"step4_gate_{pool}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{'='*104}\n{pool}  --  Sec 3 GATE (no return figures)\n{'='*104}")
    hdr = (f"{'arm':<9}{'k':>7}{'trades':>8}{'chg%':>8}{'armOnly':>9}{'baseOnly':>9}"
           f"{'subDays':>9}{'undef%':>8}{'depl%':>8}{'dDepl':>7}{'meanN':>7}  verdict")
    print(hdr)
    for arm, r in out.items():
        if "error" in r:
            print(f"{arm:<9} ERROR: {r['error']}")
            continue
        print(f"{arm:<9}{r['k']:>+7.2f}{r['trades']:>8}{r['admissions_changed_pct']:>8.1f}"
              f"{r['admissions_only_in_arm']:>9}{r['admissions_only_in_baseline']:>9}"
              f"{r['same_day_substitution_dates']:>9}"
              f"{r.get('undefined_corr_share_pct', float('nan')):>8.1f}"
              f"{r['mean_deployed_pct']:>8.2f}{r['deployment_divergence_pp']:>+7.2f}"
              f"{r['mean_n_open']:>7.2f}  {r['gate_verdict']}")
    if do_bookcorr:
        print("\nrealised book correlation (Sec 4.3):")
        for arm, r in out.items():
            if "error" not in r:
                print(f"   {arm:<9} {r.get('realised_book_correlation')}")
    print(f"\nWrote step4_gate_{pool}.json")


if __name__ == "__main__":
    main()
