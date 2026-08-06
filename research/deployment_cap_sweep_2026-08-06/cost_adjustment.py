"""FX conversion cost and idle-cash interest, modelled explicitly for these
THREE arms specifically -- per the user's challenge that deferring these
costs (correct when they're common to every arm, per every other report in
this project) is a bias, not a simplification, once the costs themselves
vary systematically with the variable under test (the deployment cap).

FX: T212's confirmed 0.15%/leg, 0.30%/round-trip fee (research/
fx_denomination_audit_2026-08-06/REPORT.md, verified against T212's own
published terms). That audit used per_trade_pct (5%, the VaR sizing
ceiling) as a proxy for average position size. Here we use each arm's own
REAL per-trade position_gbp directly -- more precise, not a proxy -- since
full trade-level output already exists for all three arms.

Idle cash: mean uninvested fraction (100% - mean realised deployment, from
deployment_and_risk.py's corrected reconstruction) x the mean ^IRX
(13-week T-bill) rate over the same 2007-01-01 -> 2026-08-03 window
already used for leg C's "real" cash treatment -- the same rate, not a
new assumption.

Both effects are converted to an annualised percentage of mean account
balance and netted against each arm's own reported (frictionless) chained
CAGR, to check directly whether the cap=0.95 leg-C reversal survives.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import json

import pandas as pd

from data.ingestion import get_price_df

HERE = Path(__file__).parent
RESULTS = HERE / "results"
CAPS = [0.50, 0.70, 0.95]
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-03")
TOTAL_YEARS = (END - START).days / 365.25
FX_ROUND_TRIP_FEE = 0.0030  # 0.15%/leg, confirmed T212 published terms


def cap_tag(cap):
    return str(cap).replace(".", "")


def main() -> None:
    irx = get_price_df("^IRX", days=9000).set_index("date")
    irx.index = pd.to_datetime(irx.index)
    irx_close = irx["close"].astype(float).sort_index()
    irx_window = irx_close[(irx_close.index >= START) & (irx_close.index <= END)]
    mean_irx_pct = float(irx_window.mean())  # already in % terms (e.g. 1.53)
    print(f"Mean ^IRX rate over {START.date()} -> {END.date()}: {mean_irx_pct:.3f}%/year\n")

    dep_summary = json.loads((RESULTS / "deployment_and_risk_summary.json").read_text())["deployment"]
    final = json.loads((RESULTS / "final_analysis.json").read_text())["headline"]

    out = {}
    for cap in CAPS:
        tag = cap_tag(cap)
        td = pd.read_csv(RESULTS / f"trades_cap{tag}.csv")
        n_trades = len(td)
        total_notional_gbp = float(td["position_gbp"].sum())
        total_fx_cost_gbp = total_notional_gbp * FX_ROUND_TRIP_FEE

        dep = dep_summary[tag]
        mean_deployed_pct = dep["mean_deployed_pct"]
        mean_idle_pct = 100.0 - mean_deployed_pct

        # mean account balance across the reconstruction -- reload the
        # (now-corrected) per-cap deployment panel directly rather than
        # re-deriving it, so this uses the exact same balance series the
        # deployment table itself reports from.
        panel = pd.read_csv(RESULTS / f"deployment_panel_cap{tag}.csv")
        mean_balance_gbp = float(panel["balance"].mean())

        fx_drag_pct_per_year = (total_fx_cost_gbp / TOTAL_YEARS) / mean_balance_gbp * 100.0
        idle_credit_pct_per_year = (mean_idle_pct / 100.0) * mean_irx_pct

        net_adjustment_pct = idle_credit_pct_per_year - fx_drag_pct_per_year

        reported_chained_cagr = final[tag]["chained_aggregate_cagr"]["STRATEGY"]
        adjusted_chained_cagr = round(reported_chained_cagr + net_adjustment_pct, 2)

        legC_zero_tr = final[tag]["chained_aggregate_cagr"]["C_zero_tr"]
        legC_real_tr = final[tag]["chained_aggregate_cagr"]["C_real_tr"]
        legA_pr = final[tag]["chained_aggregate_cagr"]["A_pr"]

        out[tag] = {
            "cap": cap, "n_trades": n_trades,
            "total_notional_traded_gbp": round(total_notional_gbp, 2),
            "total_fx_cost_gbp": round(total_fx_cost_gbp, 2),
            "fx_drag_pct_per_year": round(fx_drag_pct_per_year, 3),
            "mean_deployed_pct": mean_deployed_pct, "mean_idle_pct": round(mean_idle_pct, 2),
            "mean_balance_gbp": round(mean_balance_gbp, 2),
            "idle_credit_pct_per_year": round(idle_credit_pct_per_year, 3),
            "net_adjustment_pct_per_year": round(net_adjustment_pct, 3),
            "reported_chained_cagr": reported_chained_cagr,
            "cost_adjusted_chained_cagr": adjusted_chained_cagr,
            "legC_zero_tr_chained_cagr": legC_zero_tr,
            "legC_real_tr_chained_cagr": legC_real_tr,
            "legA_pr_chained_cagr": legA_pr,
            "still_beats_legC_zero_tr": adjusted_chained_cagr > legC_zero_tr,
            "still_beats_legC_real_tr": adjusted_chained_cagr > legC_real_tr,
            "still_beats_legA_pr": adjusted_chained_cagr > legA_pr,
        }
        print(f"cap={cap}: n_trades={n_trades}, total_notional=£{total_notional_gbp:,.0f}, "
              f"fx_cost=£{total_fx_cost_gbp:,.0f} ({fx_drag_pct_per_year:.3f}pp/yr drag)")
        print(f"  mean_deployed={mean_deployed_pct}%, mean_idle={mean_idle_pct:.2f}%, "
              f"idle_credit={idle_credit_pct_per_year:.3f}pp/yr")
        print(f"  net adjustment: {net_adjustment_pct:+.3f}pp/yr")
        print(f"  reported chained CAGR: {reported_chained_cagr}%  ->  cost-adjusted: {adjusted_chained_cagr}%")
        print(f"  vs C-zero-tr ({legC_zero_tr}%): {'still beats' if out[tag]['still_beats_legC_zero_tr'] else 'NO LONGER BEATS'}")
        print(f"  vs C-real-tr ({legC_real_tr}%): {'still beats' if out[tag]['still_beats_legC_real_tr'] else 'NO LONGER BEATS'}")
        print(f"  vs A-pr ({legA_pr}%): {'still beats' if out[tag]['still_beats_legA_pr'] else 'NO LONGER BEATS'}")
        print()

    (RESULTS / "cost_adjustment_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"Wrote {RESULTS/'cost_adjustment_summary.json'}")


if __name__ == "__main__":
    main()
