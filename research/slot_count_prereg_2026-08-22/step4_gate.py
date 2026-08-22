"""PREREGISTRATION.md Sec 2 -- THE MANDATORY GATE.

Reports, for every arm, BEFORE any return figure exists anywhere:

  2.1  realised mean concurrent positions (full-sample gating measure +
       steady-state context) and the materiality test
  2.2  realised mean deployment and the two-tier guard
  2.3  total positions opened and the 50-position evaluability floor
  2.4  capacity lock-up / entry front-loading: entries per calendar year,
       distinct tickers, share of P&L from pre-2010 entries, longest hold
  plus Sec 7 checks 2-4 for the treated arms: binding-clamp distribution
  (CASH must be zero by construction), slot-cap binding frequency, and the
  ceiling-pinned share against Sec 3.2's N-invariance prediction.

DELIBERATELY DOES NOT COMPUTE OR PRINT: CAGR, excess return over ^SP500TR,
cost adjustments, or any arm-level total P&L. Sec 2's own rule.
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
ARMS = ["baseline", "n14", "n8", "n5", "n3"]
NOMINAL = {"n14": 14, "n8": 8, "n5": 5, "n3": 3}
EVALUABILITY_FLOOR = 50
DEPLOY_FLAG_PP = 2.0
DEPLOY_EXCLUDE_PP = 4.0
FRONTLOAD_PCT = 60.0


def gate_for_pool(pool: str) -> dict:
    out = {}
    base_panel = pd.read_csv(HERE / f"panel_{pool}_baseline.csv")
    base_mean_n = float(base_panel["n_open_positions"].mean())
    base_mean_dep = float(base_panel["current_deployed_pct"].mean()) * 100.0

    for arm in ARMS:
        tag = f"{pool}_{arm}"
        tpath, ppath, apath = (HERE / f"trades_{tag}.csv", HERE / f"panel_{tag}.csv",
                               HERE / f"admissions_{tag}.csv")
        if not tpath.exists():
            out[arm] = {"error": f"missing {tpath.name}"}
            continue
        trades = pd.read_csv(tpath)
        panel = pd.read_csv(ppath)
        adm = pd.read_csv(apath)
        if trades.empty or panel.empty:
            out[arm] = {"error": "empty trades or panel -- gate would be VACUOUS"}
            continue

        panel["date"] = pd.to_datetime(panel["date"])
        steady = panel[panel["date"] >= "2010-01-01"]

        # ---- 2.1
        mean_n = float(panel["n_open_positions"].mean())
        # Independent cross-check of the panel counter: derive mean concurrent
        # occupancy from the TRADES file alone (sum of each position's
        # trading-day span / total trading days). Two independent derivations
        # of the same quantity -- a disagreement would mean the added
        # n_open_positions counter is wrong, which no parity check on TRADES
        # would ever catch.
        di = pd.Series(range(len(panel)), index=panel["date"])
        _e = pd.to_datetime(trades["entry_date"])
        _x = pd.to_datetime(trades["exit_date"])
        occ = float((di.reindex(_x).to_numpy() - di.reindex(_e).to_numpy()).sum())
        mean_n_occ = occ / len(panel)
        mean_n_steady = float(steady["n_open_positions"].mean())
        rec = {
            "trading_days": int(len(panel)),
            "mean_concurrent_positions": round(mean_n, 3),
            "mean_concurrent_positions_2010on": round(mean_n_steady, 3),
            "mean_concurrent_occupancy_derived": round(mean_n_occ, 3),
            "counter_vs_occupancy_gap": round(mean_n - mean_n_occ, 3),
            "max_concurrent_positions": int(panel["n_open_positions"].max()),
        }
        if arm in NOMINAL:
            n = NOMINAL[arm]
            d_target = abs(mean_n - n)
            d_base = abs(mean_n - base_mean_n)
            rec.update({
                "nominal_target": n,
                "dist_to_own_target": round(d_target, 3),
                "dist_to_baseline_realised": round(d_base, 3),
                "materiality_verdict": "TESTED" if d_target < d_base else "NOT TESTED",
            })

        # ---- 2.2
        mean_dep = float(panel["current_deployed_pct"].mean()) * 100.0
        mean_dep_steady = float(steady["current_deployed_pct"].mean()) * 100.0
        div = mean_dep - base_mean_dep
        rec.update({
            "mean_deployed_pct": round(mean_dep, 2),
            "mean_deployed_pct_2010on": round(mean_dep_steady, 2),
            "deployment_divergence_pp": round(div, 2),
            "deployment_verdict": (
                "clean" if abs(div) <= DEPLOY_FLAG_PP
                else "FLAGGED" if abs(div) <= DEPLOY_EXCLUDE_PP
                else "EXCLUDED"),
            "min_cash_pct_of_account": round(
                float((panel["cash"] / panel["current_balance"]).min()) * 100.0, 2),
        })

        # ---- 2.3
        rec["positions_opened"] = int(len(trades))
        rec["evaluability"] = ("NOT EVALUABLE" if len(trades) < EVALUABILITY_FLOOR
                               else "evaluable")

        # ---- 2.4
        trades["entry_date"] = pd.to_datetime(trades["entry_date"])
        by_year = trades["entry_date"].dt.year.value_counts().sort_index()
        pre2010 = trades[trades["entry_date"] < "2010-01-01"]
        total_pnl = float(trades["pnl_gbp"].sum())
        share_pre2010 = (100.0 * float(pre2010["pnl_gbp"].sum()) / total_pnl
                         if total_pnl != 0 else float("nan"))
        # trading days on which a pre-2010-entered position was open
        rec.update({
            "entries_by_year": {int(k): int(v) for k, v in by_year.items()},
            "distinct_tickers_held": int(trades["ticker"].nunique()),
            "pre2010_entries": int(len(pre2010)),
            "pnl_share_from_pre2010_entries_pct": (round(share_pre2010, 2)
                                                   if total_pnl != 0 else None),
            "frontload_verdict": ("START-DATE CONDITIONAL"
                                  if total_pnl != 0 and share_pre2010 > FRONTLOAD_PCT
                                  else "ok"),
            "longest_hold_days": int(trades["holding_days"].max()),
            "mean_hold_days": round(float(trades["holding_days"].mean()), 1),
        })

        # ---- Sec 7 checks 2-4
        clamp = adm["binding_clamp"].value_counts().to_dict() if not adm.empty else {}
        rec["binding_clamp_distribution"] = {k: int(v) for k, v in clamp.items()}
        rec["cash_bound_admissions"] = int(clamp.get("CASH", 0))
        rec["ceiling_pinned_pct"] = (round(100.0 * int(clamp.get("CEILING", 0)) / len(adm), 2)
                                     if len(adm) else None)
        sf_path = HERE / f"slotfull_{tag}.csv"
        if sf_path.exists():
            sf = pd.read_csv(sf_path)
            days = int(pd.to_datetime(sf["date"]).nunique()) if not sf.empty else 0
            rec["slot_full_events"] = int(len(sf))
            rec["slot_full_days"] = days
            rec["slot_full_days_pct_of_walk"] = round(100.0 * days / len(panel), 2)
            rec["mean_turned_away_when_full"] = (round(float(sf["n_turned_away"].mean()), 1)
                                                 if not sf.empty else 0.0)
            rec["slot_cap_binds"] = "YES" if days > 0 else "NO -- ARM TESTS NOTHING"
            # WHICH constraint actually held the book below N? With
            # per_trade_pct_N = max_total_deployed/N the two bind at the same
            # nominal point, and mark-to-market appreciation can push
            # deployment to the cap with FEWER than N positions open -- in
            # which case the arm is limited by capacity, not by its slot
            # count, and "slot count" is not the operative variable.
            rj_path = HERE / f"rejections_{tag}.csv"
            if rj_path.exists():
                rj = pd.read_csv(rj_path)
                cap_days = set(pd.to_datetime(rj["date"]).dt.date) if not rj.empty else set()
                slot_days = set(pd.to_datetime(sf["date"]).dt.date) if not sf.empty else set()
                rec["capacity_reject_days"] = len(cap_days)
                rec["capacity_reject_days_pct_of_walk"] = round(
                    100.0 * len(cap_days) / len(panel), 2)
                rec["days_blocked_by_capacity_only"] = len(cap_days - slot_days)
                rec["days_blocked_by_slotcap_only"] = len(slot_days - cap_days)
                rec["days_blocked_by_both"] = len(cap_days & slot_days)
                rec["binding_constraint_verdict"] = (
                    "SLOT COUNT" if len(slot_days - cap_days) > len(cap_days - slot_days)
                    else "DEPLOYMENT CAP")
        out[arm] = rec
    return out


def main() -> None:
    report = {}
    for pool in ("pool2_clean", "pool1"):
        if not (HERE / f"panel_{pool}_baseline.csv").exists():
            print(f"skipping {pool}: no baseline panel yet")
            continue
        report[pool] = gate_for_pool(pool)
    (HERE / "step4_gate.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    for pool, arms in report.items():
        print(f"\n{'='*78}\n{pool}\n{'='*78}")
        hdr = f"{'arm':<10}{'mean N':>9}{'max N':>7}{'trades':>8}{'depl%':>8}{'d_depl':>8}{'materiality':>14}{'deploy':>10}"
        print(hdr)
        for arm, r in arms.items():
            if "error" in r:
                print(f"{arm:<10} ERROR: {r['error']}")
                continue
            print(f"{arm:<10}{r['mean_concurrent_positions']:>9.2f}"
                  f"{r['max_concurrent_positions']:>7}{r['positions_opened']:>8}"
                  f"{r['mean_deployed_pct']:>8.2f}{r['deployment_divergence_pp']:>8.2f}"
                  f"{r.get('materiality_verdict', '-- (control)'):>14}"
                  f"{r['deployment_verdict']:>10}")
    print("\nFull detail: step4_gate.json")


if __name__ == "__main__":
    main()
