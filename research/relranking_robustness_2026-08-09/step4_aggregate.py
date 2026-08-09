"""Aggregate all 20 paired draws: per-draw cost-adjusted excess return
(each draw's OWN [start, END] window vs ^SP500TR over that same window, since
shift_start draws have a different window than the others), B-minus-A delta,
drawdown delta, deployment parity, and the paired per-decision instrument
(exact ticker/entry_date pairing between that draw's own A and B trades).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-relranking-robustness")

import json

import pandas as pd
import yaml
import yfinance as yf

from analysis.backtest import compute_metrics_fixed, compute_peak_relative_drawdown_pct
from data.ingestion import get_price_df

OUT_DIR = "research/relranking_robustness_2026-08-09"
FX_ROUND_TRIP_FEE = 0.0030
END = pd.Timestamp("2026-08-07")
MIN_DIFFERING_USABLE = 100

with open("config.yaml", encoding="utf-8") as f:
    config = yaml.safe_load(f)
STARTING_CAPITAL = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))

# ^IRX and ^SP500TR fetched once, full range -- sliced per draw's own window.
irx_h = yf.Ticker("^IRX").history(start="2007-01-01", end=str((END + pd.Timedelta(days=1)).date()), auto_adjust=False)
irx_h.index = irx_h.index.tz_localize(None)
IRX_CLOSE = irx_h["Close"].astype(float).sort_index()
IRX_CLOSE.index = pd.to_datetime(IRX_CLOSE.index)

sp_h = yf.Ticker("^SP500TR").history(start="2007-01-01", end=str((END + pd.Timedelta(days=1)).date()), auto_adjust=False)
sp_h.index = sp_h.index.tz_localize(None)
SP500TR_CLOSE = sp_h["Close"].astype(float)
SP500TR_CLOSE.index = pd.to_datetime(SP500TR_CLOSE.index)
print(f"^SP500TR: {len(SP500TR_CLOSE)} rows, {SP500TR_CLOSE.index.min().date()} -> {SP500TR_CLOSE.index.max().date()}")


def bench_cagr(start, end):
    s = SP500TR_CLOSE[(SP500TR_CLOSE.index >= start) & (SP500TR_CLOSE.index <= end)]
    if len(s) < 2:
        return None
    days = (s.index[-1] - s.index[0]).days
    years = days / 365.25 if days > 0 else 0
    if years <= 0 or s.iloc[0] <= 0:
        return None
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1) * 100


def cost_adjusted(trades, panel, start, end):
    frictionless = compute_metrics_fixed(trades.to_dict("records"), STARTING_CAPITAL)
    total_years = (end - start).days / 365.25
    total_notional = float(trades["position_gbp"].sum())
    fx_cost_gbp = total_notional * FX_ROUND_TRIP_FEE
    mean_balance = float(panel["current_balance"].mean())
    mean_deployed_pct = float(panel["current_deployed_pct"].mean())
    mean_idle_pct = 100.0 - mean_deployed_pct
    irx_window = IRX_CLOSE[(IRX_CLOSE.index >= start) & (IRX_CLOSE.index <= end)]
    mean_irx_pct = float(irx_window.mean()) if len(irx_window) else 0.0
    fx_drag_pct_yr = (fx_cost_gbp / total_years) / mean_balance * 100.0 if mean_balance > 0 and total_years > 0 else 0.0
    idle_credit_pct_yr = (mean_idle_pct / 100.0) * mean_irx_pct
    cost_adj_cagr = frictionless["cagr_pct"] + idle_credit_pct_yr - fx_drag_pct_yr
    bc = bench_cagr(start, end)
    return {
        "n_trades": len(trades),
        "cost_adjusted_cagr_pct": round(cost_adj_cagr, 3),
        "peak_relative_drawdown_pct": compute_peak_relative_drawdown_pct(trades.to_dict("records"), STARTING_CAPITAL),
        "mean_deployed_pct": round(mean_deployed_pct, 2),
        "sp500tr_cagr_pct": round(bc, 2) if bc is not None else None,
        "excess_cost_adjusted_pct": round(cost_adj_cagr - bc, 3) if bc is not None else None,
    }


def paired_exact(x, y):
    x_by_key = {(r["ticker"], str(r["entry_date"])): r for _, r in x.iterrows()}
    y_by_key = {(r["ticker"], str(r["entry_date"])): r for _, r in y.iterrows()}
    x_only_keys = sorted(set(x_by_key) - set(y_by_key))
    y_only_keys = sorted(set(y_by_key) - set(x_by_key))
    n_shared = len(set(x_by_key) & set(y_by_key))
    x_only = x[x.apply(lambda r: (r["ticker"], str(r["entry_date"])) in set(x_only_keys), axis=1)] if x_only_keys else x.iloc[0:0]
    y_only = y[y.apply(lambda r: (r["ticker"], str(r["entry_date"])) in set(y_only_keys), axis=1)] if y_only_keys else y.iloc[0:0]
    n_differing = len(x_only_keys) + len(y_only_keys)
    result = {"n_shared_pairs": n_shared, "n_differing_total": n_differing, "usable": n_differing >= MIN_DIFFERING_USABLE}
    if result["usable"]:
        def _summary(df):
            if df.empty:
                return {"n": 0, "win_rate": None, "mean_pnl_pct": None}
            return {"n": len(df), "win_rate": round(float((df["pnl_gbp"] > 0).mean()), 4),
                    "mean_pnl_pct": round(float(df["pnl_pct"].mean()), 4)}
        result["A_only"] = _summary(x_only)
        result["B_only"] = _summary(y_only)
    return result


def main():
    with open(f"{OUT_DIR}/draws.json") as fh:
        draws = json.load(fh)

    rows = []
    for d in draws:
        draw_id = d["draw_id"]
        with open(f"{OUT_DIR}/meta_{draw_id}.json") as fh:
            meta = json.load(fh)
        start = pd.Timestamp(meta["start"])
        end = pd.Timestamp(meta["end"])

        trades_a = pd.read_csv(f"{OUT_DIR}/trades_A_{draw_id}.csv", parse_dates=["entry_date", "exit_date"])
        panel_a = pd.read_csv(f"{OUT_DIR}/panel_A_{draw_id}.csv")
        trades_b = pd.read_csv(f"{OUT_DIR}/trades_B_{draw_id}.csv", parse_dates=["entry_date", "exit_date"])
        panel_b = pd.read_csv(f"{OUT_DIR}/panel_B_{draw_id}.csv")

        ca_a = cost_adjusted(trades_a, panel_a, start, end)
        ca_b = cost_adjusted(trades_b, panel_b, start, end)

        a2 = trades_a.copy(); a2["entry_date"] = a2["entry_date"].dt.date
        b2 = trades_b.copy(); b2["entry_date"] = b2["entry_date"].dt.date
        paired = paired_exact(a2, b2)

        row = {
            "draw_id": draw_id, "ptype": d["ptype"], "param": d["param"],
            "start": str(start.date()), "n_usable": meta["n_usable"],
            "n_trades_A": ca_a["n_trades"], "n_trades_B": ca_b["n_trades"],
            "cost_adj_cagr_A": ca_a["cost_adjusted_cagr_pct"], "cost_adj_cagr_B": ca_b["cost_adjusted_cagr_pct"],
            "excess_A": ca_a["excess_cost_adjusted_pct"], "excess_B": ca_b["excess_cost_adjusted_pct"],
            "delta_B_minus_A": round(ca_b["cost_adjusted_cagr_pct"] - ca_a["cost_adjusted_cagr_pct"], 3),
            "dd_A": ca_a["peak_relative_drawdown_pct"], "dd_B": ca_b["peak_relative_drawdown_pct"],
            "dd_delta_B_minus_A": round(ca_b["peak_relative_drawdown_pct"] - ca_a["peak_relative_drawdown_pct"], 2)
                if ca_a["peak_relative_drawdown_pct"] is not None and ca_b["peak_relative_drawdown_pct"] is not None else None,
            "deployed_A": ca_a["mean_deployed_pct"], "deployed_B": ca_b["mean_deployed_pct"],
            "deployed_gap": round(ca_b["mean_deployed_pct"] - ca_a["mean_deployed_pct"], 2),
            "paired": paired,
        }
        rows.append(row)
        print(f"{draw_id}: delta_B_minus_A={row['delta_B_minus_A']:+.3f}pp  "
              f"(A={row['cost_adj_cagr_A']:.3f} B={row['cost_adj_cagr_B']:.3f})  "
              f"dd_delta={row['dd_delta_B_minus_A']}  deployed_gap={row['deployed_gap']}  "
              f"paired_usable={paired['usable']}", flush=True)

    deltas = [r["delta_B_minus_A"] for r in rows]
    n_pos = sum(1 for x in deltas if x > 0)
    summary = {
        "n_draws": len(deltas),
        "mean": round(sum(deltas) / len(deltas), 4),
        "median": round(sorted(deltas)[len(deltas) // 2], 4),
        "min": round(min(deltas), 4), "min_draw": rows[deltas.index(min(deltas))]["draw_id"],
        "max": round(max(deltas), 4), "max_draw": rows[deltas.index(max(deltas))]["draw_id"],
        "n_positive": n_pos, "n_total": len(deltas),
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))

    with open(f"{OUT_DIR}/step4_all_results.json", "w") as fh:
        json.dump({"rows": rows, "summary": summary}, fh, indent=2, default=str)
    print(f"\nWrote {OUT_DIR}/step4_all_results.json")


if __name__ == "__main__":
    main()
