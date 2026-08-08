"""Aggregate all completed noise-floor perturbation runs into a distribution
report: cost-adjusted CAGR, max drawdown, total trades, block pass count.
Run after all step2_run_perturbation.py runs have completed.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import glob
import json
import os

import pandas as pd
import yfinance as yf

from analysis.backtest import compute_metrics_fixed, compute_peak_relative_drawdown_pct, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

OUT_DIR = "research/measurement_toolkit_2026-08-08"
BASE_START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")
FX_ROUND_TRIP_FEE = 0.0030
STARTING_CAPITAL = 50000.0

sys.path.insert(0, OUT_DIR)
from step4_benchmark_relative import get_sp500tr_close  # noqa: E402


def deployed_pct_and_mean_balance(trades_df, start):
    from analysis.backtest import equity_curve_gbp
    df = trades_df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    ec = equity_curve_gbp(df, starting_capital_gbp=STARTING_CAPITAL).set_index("date")
    events = []
    for _, row in df.iterrows():
        events.append((row["entry_date"], row["position_gbp"]))
        events.append((row["exit_date"] + pd.Timedelta(days=1), -row["position_gbp"]))
    ev = pd.Series([v for _, v in events], index=[d for d, _ in events]).groupby(level=0).sum()
    ev = ev.reindex(ec.index, fill_value=0).cumsum()
    deployed_pct = (ev / ec["equity_gbp"]).clip(lower=0, upper=1) * 100
    return float(deployed_pct.mean()), float(ec["equity_gbp"].mean())


def main():
    irx_raw = yf.Ticker("^IRX").history(start=BASE_START.date().isoformat(), end=(END + pd.Timedelta(days=1)).date().isoformat())
    irx_close = irx_raw["Close"].astype(float)
    irx_close.index = pd.to_datetime(irx_close.index).tz_localize(None)
    mean_irx_pct = float(irx_close[(irx_close.index >= BASE_START) & (irx_close.index <= END)].mean())

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    meta_files = sorted(glob.glob(f"{OUT_DIR}/meta_*.txt"))
    rows = []
    for mf in meta_files:
        with open(mf) as fh:
            run_id, ptype, param, start_str, n_tickers = fh.read().strip().split("\t")
        trades_path = f"{OUT_DIR}/trades_{run_id}.csv"
        if not os.path.exists(trades_path):
            continue
        df_v = pd.read_csv(trades_path)
        df_v["exit_date"] = pd.to_datetime(df_v["exit_date"])
        df_v["entry_date"] = pd.to_datetime(df_v["entry_date"])
        start = pd.Timestamp(start_str)
        total_years = (END - start).days / 365.25
        total_pnl = df_v["pnl_gbp"].sum()
        ending_equity = STARTING_CAPITAL + total_pnl
        cagr = ((ending_equity / STARTING_CAPITAL) ** (1 / total_years) - 1) * 100
        peak_dd = compute_peak_relative_drawdown_pct(df_v.to_dict("records"), STARTING_CAPITAL)

        blocks_raw = make_blocks(start, END, n_blocks=16)
        n_pass = 0
        for b_start, b_end in blocks_raw:
            strat_trades = df_v[(df_v["exit_date"] >= b_start) & (df_v["exit_date"] <= b_end)].to_dict("records")
            strat_m = compute_metrics_fixed(strat_trades, STARTING_CAPITAL)
            sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
            if _block_passes(strat_m, sp_dd):
                n_pass += 1

        mean_dep, mean_balance = deployed_pct_and_mean_balance(df_v, start)
        mean_idle = 100.0 - mean_dep
        total_notional = float(df_v["position_gbp"].sum())
        fx_drag = (total_notional * FX_ROUND_TRIP_FEE / total_years) / mean_balance * 100.0
        idle_credit = (mean_idle / 100.0) * mean_irx_pct
        cost_adj_cagr = cagr + idle_credit - fx_drag

        rows.append({
            "run_id": run_id, "perturbation_type": ptype, "param": param,
            "n_trades": len(df_v), "n_tickers": int(n_tickers),
            "raw_cagr_pct": round(cagr, 3), "cost_adjusted_cagr_pct": round(cost_adj_cagr, 3),
            "peak_relative_max_dd_pct": round(peak_dd, 3),
            "blocks_pass": n_pass, "total_pnl_gbp": round(total_pnl, 2),
        })
        print(f"[{run_id}] {ptype} {param}: trades={len(df_v)} cost_adj_CAGR={cost_adj_cagr:.3f}% "
              f"peakDD={peak_dd:.3f}% blocks={n_pass}/16", flush=True)

    df_summary = pd.DataFrame(rows)
    df_summary.to_csv(f"{OUT_DIR}/step2_noise_floor_summary.csv", index=False)

    baseline_row = df_summary[df_summary["perturbation_type"] == "baseline"]
    perturbed = df_summary[df_summary["perturbation_type"] != "baseline"]

    dist = {}
    for col in ["cost_adjusted_cagr_pct", "peak_relative_max_dd_pct", "n_trades", "blocks_pass"]:
        dist[col] = {
            "baseline": float(baseline_row[col].iloc[0]) if not baseline_row.empty else None,
            "perturbed_min": float(perturbed[col].min()), "perturbed_max": float(perturbed[col].max()),
            "perturbed_mean": round(float(perturbed[col].mean()), 4),
            "perturbed_std": round(float(perturbed[col].std()), 4),
            "perturbed_range": round(float(perturbed[col].max() - perturbed[col].min()), 4),
        }
    with open(f"{OUT_DIR}/step2_noise_floor_distribution.json", "w") as fh:
        json.dump(dist, fh, indent=2, default=str)
    print("\n=== NOISE FLOOR DISTRIBUTION ===")
    print(json.dumps(dist, indent=2, default=str))


if __name__ == "__main__":
    main()
