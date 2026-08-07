"""Cost-adjustment, per arm per pool -- reuses the exact template from
research/deployment_cap_sweep_2026-08-06/cost_adjustment.py: FX = Sigma(real
position_gbp) x 0.30% round-trip; idle credit = mean idle-cash % x mean
^IRX rate over the run window; netted against each arm's own continuous-
walk CAGR. Turnover differs materially across A/B by construction (that is
the entire point of the comparison), so per the 2026-08-06 standing rule
this cannot be deferred as a constant.

Mean deployed % is reconstructed directly from each arm's own trade list
(no separate simulator instrumentation needed): on any given day, deployed
capital = sum of position_gbp for every trade with entry_date <= day <=
exit_date; account balance on that day = the nearest not-later point on
that arm's own realised equity curve (equity_curve_gbp(), the same
compounding-consistent curve used for the peak-relative drawdown figure).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import json

import pandas as pd
import yfinance as yf

from analysis.backtest import equity_curve_gbp

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")
TOTAL_YEARS = (END - START).days / 365.25
FX_ROUND_TRIP_FEE = 0.0030
STARTING_CAPITAL = 50000.0


def deployed_pct_and_mean_balance(trades_df: pd.DataFrame) -> tuple[float, float]:
    df = trades_df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])

    ec = equity_curve_gbp(df, starting_capital_gbp=STARTING_CAPITAL)
    ec = ec.set_index("date")
    ec_days = ec.index

    # Deployed-capital time series via a sweep-line over entry/exit events --
    # O(n log n), not O(n * days).
    events = []
    for _, row in df.iterrows():
        events.append((row["entry_date"], row["position_gbp"]))
        events.append((row["exit_date"] + pd.Timedelta(days=1), -row["position_gbp"]))
    ev = pd.Series([v for _, v in events], index=[d for d, _ in events]).groupby(level=0).sum()
    ev = ev.reindex(ec_days, fill_value=0).cumsum()

    deployed_pct = (ev / ec["equity_gbp"]).clip(lower=0, upper=1) * 100
    return float(deployed_pct.mean()), float(ec["equity_gbp"].mean())


def main():
    # ^IRX not present in this local DB copy (never ingested here, unlike
    # the deployment_cap_sweep run which apparently had it) -- fetched
    # directly, matching the one-off supplementary-data pattern this
    # project's own research scripts already use for reference series not
    # in the regular ingestion watchlist.
    irx_raw = yf.Ticker("^IRX").history(
        start=START.date().isoformat(), end=(END + pd.Timedelta(days=1)).date().isoformat()
    )
    irx_close = irx_raw["Close"].astype(float)
    irx_close.index = pd.to_datetime(irx_close.index).tz_localize(None)
    irx_window = irx_close[(irx_close.index >= START) & (irx_close.index <= END)]
    mean_irx_pct = float(irx_window.mean())
    print(f"Mean ^IRX rate over {START.date()} -> {END.date()}: {mean_irx_pct:.3f}%/year\n", flush=True)

    step3 = pd.read_csv(f"{OUT_DIR}/step3_summary.csv").set_index("label")

    out = {}
    for pool in ["pool1", "pool2"]:
        for arm in ["A", "C1", "C2", "B"]:
            label = f"{pool}_{arm}"
            df_v = pd.read_csv(f"{OUT_DIR}/trades_{label}.csv")
            total_notional_gbp = float(df_v["position_gbp"].sum())
            total_fx_cost_gbp = total_notional_gbp * FX_ROUND_TRIP_FEE

            mean_dep, mean_balance = deployed_pct_and_mean_balance(df_v)
            mean_idle = 100.0 - mean_dep

            reported_cagr = float(step3.loc[label, "continuous_cagr_pct"])

            fx_drag_pct_per_year = (total_fx_cost_gbp / TOTAL_YEARS) / mean_balance * 100.0
            idle_credit_pct_per_year = (mean_idle / 100.0) * mean_irx_pct
            net_adjustment_pct = idle_credit_pct_per_year - fx_drag_pct_per_year
            cost_adjusted_cagr = round(reported_cagr + net_adjustment_pct, 2)

            out[label] = {
                "pool": pool, "arm": arm,
                "total_notional_traded_gbp": round(total_notional_gbp, 2),
                "total_fx_cost_gbp": round(total_fx_cost_gbp, 2),
                "fx_drag_pct_per_year": round(fx_drag_pct_per_year, 3),
                "mean_deployed_pct": round(mean_dep, 2), "mean_idle_pct": round(mean_idle, 2),
                "idle_credit_pct_per_year": round(idle_credit_pct_per_year, 3),
                "net_adjustment_pct_per_year": round(net_adjustment_pct, 3),
                "reported_cagr_pct": reported_cagr,
                "cost_adjusted_cagr_pct": cost_adjusted_cagr,
            }
            print(f"[{label}] mean_deployed={mean_dep:.2f}% fx_drag={fx_drag_pct_per_year:.3f}pp/yr "
                  f"idle_credit={idle_credit_pct_per_year:.3f}pp/yr net={net_adjustment_pct:+.3f}pp/yr  "
                  f"CAGR {reported_cagr}% -> {cost_adjusted_cagr}%", flush=True)

    with open(f"{OUT_DIR}/step5_cost_adjustment.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nWrote step5_cost_adjustment.json", flush=True)


if __name__ == "__main__":
    main()
