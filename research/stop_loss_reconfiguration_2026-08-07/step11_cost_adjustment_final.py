"""Cost-adjustment for the re-run pool2_final arms -- exact same template as
step5_cost_adjustment.py (FX = 0.30% round-trip on Sigma(position_gbp), idle
credit = mean idle% x mean ^IRX rate).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

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


def deployed_pct_and_mean_balance(trades_df: pd.DataFrame):
    df = trades_df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])

    ec = equity_curve_gbp(df, starting_capital_gbp=STARTING_CAPITAL)
    ec = ec.set_index("date")
    ec_days = ec.index

    events = []
    for _, row in df.iterrows():
        events.append((row["entry_date"], row["position_gbp"]))
        events.append((row["exit_date"] + pd.Timedelta(days=1), -row["position_gbp"]))
    ev = pd.Series([v for _, v in events], index=[d for d, _ in events]).groupby(level=0).sum()
    ev = ev.reindex(ec_days, fill_value=0).cumsum()

    deployed_pct = (ev / ec["equity_gbp"]).clip(lower=0, upper=1) * 100
    return float(deployed_pct.mean()), float(ec["equity_gbp"].mean())


def main():
    irx_raw = yf.Ticker("^IRX").history(
        start=START.date().isoformat(), end=(END + pd.Timedelta(days=1)).date().isoformat()
    )
    irx_close = irx_raw["Close"].astype(float)
    irx_close.index = pd.to_datetime(irx_close.index).tz_localize(None)
    irx_window = irx_close[(irx_close.index >= START) & (irx_close.index <= END)]
    mean_irx_pct = float(irx_window.mean())
    print(f"Mean ^IRX rate over {START.date()} -> {END.date()}: {mean_irx_pct:.3f}%/year\n", flush=True)

    step10 = pd.read_csv(f"{OUT_DIR}/step10_summary.csv").set_index("label")

    out = {}
    for arm in ["A", "C1", "C2", "B"]:
        label = f"pool2_final_{arm}"
        df_v = pd.read_csv(f"{OUT_DIR}/trades_{label}.csv")
        total_notional_gbp = float(df_v["position_gbp"].sum())
        total_fx_cost_gbp = total_notional_gbp * FX_ROUND_TRIP_FEE

        mean_dep, mean_balance = deployed_pct_and_mean_balance(df_v)
        mean_idle = 100.0 - mean_dep

        reported_cagr = float(step10.loc[label, "continuous_cagr_pct"])

        fx_drag_pct_per_year = (total_fx_cost_gbp / TOTAL_YEARS) / mean_balance * 100.0
        idle_credit_pct_per_year = (mean_idle / 100.0) * mean_irx_pct
        net_adjustment_pct = idle_credit_pct_per_year - fx_drag_pct_per_year
        cost_adjusted_cagr = round(reported_cagr + net_adjustment_pct, 2)

        out[label] = {
            "arm": arm,
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

    with open(f"{OUT_DIR}/step11_cost_adjustment_final.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nWrote step11_cost_adjustment_final.json", flush=True)


if __name__ == "__main__":
    main()
