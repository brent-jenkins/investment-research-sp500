"""Post-processing instruments for the deployment-cap study: cost adjustment,
excess return vs ^SP500TR (per block AND cumulative), the inverted deployment
guard (realised mean deployment vs. each arm's own nominal cap), exact
(ticker, entry_date) paired comparison, top-decile overlap, and 16-block
corroboration (Pool 2 CLEAN only).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import json
import pickle

import pandas as pd
import yaml

from analysis.backtest import make_blocks, compute_metrics_fixed, compute_peak_relative_drawdown_pct
from analysis.validation import _cagr_dd_for_series, _block_passes
from data.ingestion import get_price_df

OUT_DIR = "research/deployment_cap_prereg_2026-08-09"
CACHE_DIR = "research/position_count_prereg_2026-08-09"
FX_ROUND_TRIP_FEE = 0.0030
START = pd.Timestamp("2007-01-01")
ARMS = ["c70", "c80", "c90", "c95"]
NOMINAL_CAP = {"c70": 70.0, "c80": 80.0, "c90": 90.0, "c95": 95.0}
MIN_DIFFERING_USABLE = 100


def cost_adjustment(trades: pd.DataFrame, panel: pd.DataFrame, starting_capital: float,
                     mean_irx_pct: float, total_years: float) -> dict:
    frictionless = compute_metrics_fixed(trades.to_dict("records"), starting_capital)
    total_notional = float(trades["position_gbp"].sum())
    fx_cost_gbp = total_notional * FX_ROUND_TRIP_FEE
    mean_balance = float(panel["current_balance"].mean())
    mean_deployed_pct = float(panel["current_deployed_pct"].mean())
    mean_idle_pct = 100.0 - mean_deployed_pct
    fx_drag_pct_yr = (fx_cost_gbp / total_years) / mean_balance * 100.0 if mean_balance > 0 else 0.0
    idle_credit_pct_yr = (mean_idle_pct / 100.0) * mean_irx_pct
    net_adj_pct = idle_credit_pct_yr - fx_drag_pct_yr
    cost_adj_cagr = round(frictionless["cagr_pct"] + net_adj_pct, 3)
    return {
        "n_trades": len(trades),
        "frictionless_cagr_pct": frictionless["cagr_pct"],
        "peak_relative_drawdown_pct": compute_peak_relative_drawdown_pct(trades.to_dict("records"), starting_capital),
        "max_drawdown_fixed_pct": frictionless["max_drawdown_pct"],
        "mean_deployed_pct": round(mean_deployed_pct, 2),
        "median_deployed_pct": round(float(panel["current_deployed_pct"].median()), 2),
        "mean_n_open_positions": round(float(panel["n_open_positions"].mean()), 2),
        "fx_drag_pct_per_year": round(fx_drag_pct_yr, 4),
        "idle_credit_pct_per_year": round(idle_credit_pct_yr, 4),
        "cost_adjusted_cagr_pct": cost_adj_cagr,
    }


def rejection_summary(rejections: pd.DataFrame) -> dict:
    if rejections is None or rejections.empty:
        return {"n_rejected_subminimum": 0, "n_rejected_capacity": 0}
    counts = rejections["reason"].value_counts()
    return {
        "n_rejected_subminimum": int(counts.get("REJECTED_SUBMINIMUM", 0)),
        "n_rejected_capacity": int(counts.get("REJECTED_CAPACITY", 0)),
    }


def excess_vs_benchmark_cumulative(trades: pd.DataFrame, starting_capital: float, sp500tr_close: pd.Series, end: pd.Timestamp) -> dict:
    strat_cagr = compute_metrics_fixed(trades.to_dict("records"), starting_capital)["cagr_pct"]
    bench_cagr, _ = _cagr_dd_for_series(sp500tr_close, START, end)
    return {"strategy_frictionless_cagr_pct": strat_cagr, "sp500tr_cagr_pct": round(bench_cagr, 2),
            "excess_frictionless_pct": round(strat_cagr - bench_cagr, 3)}


def excess_vs_benchmark_cost_adjusted(cost_adj_cagr: float, sp500tr_close: pd.Series, end: pd.Timestamp) -> dict:
    bench_cagr, _ = _cagr_dd_for_series(sp500tr_close, START, end)
    return {"sp500tr_cagr_pct": round(bench_cagr, 2), "excess_cost_adjusted_pct": round(cost_adj_cagr - bench_cagr, 3)}


def excess_per_block(trades: pd.DataFrame, starting_capital: float, sp500tr_close: pd.Series, end: pd.Timestamp) -> list:
    trades = trades.copy()
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])
    blocks = make_blocks(START, end, n_blocks=16)
    out = []
    for i, (b_start, b_end) in enumerate(blocks):
        bt = trades[(trades["exit_date"] >= b_start) & (trades["exit_date"] <= b_end)].to_dict("records")
        m = compute_metrics_fixed(bt, starting_capital)
        bench_cagr, _ = _cagr_dd_for_series(sp500tr_close, b_start, b_end)
        excess = round(m["cagr_pct"] - bench_cagr, 3) if bench_cagr is not None else None
        out.append({"block": i + 1, "start": str(b_start.date()), "end": str(b_end.date()),
                    "n_trades": len(bt), "strategy_cagr_pct": m["cagr_pct"],
                    "sp500tr_cagr_pct": round(bench_cagr, 2) if bench_cagr is not None else None,
                    "excess_pct": excess})
    return out


def block_validation(trades: pd.DataFrame, gspc_close_full: pd.Series, starting_capital: float, end: pd.Timestamp) -> dict:
    trades = trades.copy()
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])
    blocks = make_blocks(START, end, n_blocks=16)
    n_pass = 0
    for b_start, b_end in blocks:
        bt = trades[(trades["exit_date"] >= b_start) & (trades["exit_date"] <= b_end)].to_dict("records")
        m = compute_metrics_fixed(bt, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        if _block_passes(m, sp_dd):
            n_pass += 1
    return {"n_blocks_pass": n_pass, "n_blocks_total": len(blocks)}


def paired_exact(x: pd.DataFrame, y: pd.DataFrame, label_x: str, label_y: str) -> dict:
    x_by_key = {(r["ticker"], str(r["entry_date"])): r for _, r in x.iterrows()}
    y_by_key = {(r["ticker"], str(r["entry_date"])): r for _, r in y.iterrows()}
    shared = sorted(set(x_by_key) & set(y_by_key))
    x_only_keys = sorted(set(x_by_key) - set(y_by_key))
    y_only_keys = sorted(set(y_by_key) - set(x_by_key))
    n_shared = len(shared)
    result = {
        "label_x": label_x, "label_y": label_y, "n_total_x": len(x), "n_total_y": len(y),
        "n_shared_pairs": n_shared,
        "shared_pct_of_x": round(100 * n_shared / len(x), 1) if len(x) else None,
        "shared_pct_of_y": round(100 * n_shared / len(y), 1) if len(y) else None,
        "n_x_only": len(x_only_keys), "n_y_only": len(y_only_keys),
    }
    x_only = x[x.apply(lambda r: (r["ticker"], str(r["entry_date"])) in set(x_only_keys), axis=1)] if x_only_keys else x.iloc[0:0]
    y_only = y[y.apply(lambda r: (r["ticker"], str(r["entry_date"])) in set(y_only_keys), axis=1)] if y_only_keys else y.iloc[0:0]
    n_differing = len(x_only_keys) + len(y_only_keys)
    result["n_differing_total"] = n_differing
    result["usable"] = n_differing >= MIN_DIFFERING_USABLE
    if result["usable"]:
        def _summary(df):
            if df.empty:
                return {"n": 0}
            return {
                "n": len(df), "win_rate": round(float((df["pnl_gbp"] > 0).mean()), 4),
                "mean_pnl_pct": round(float(df["pnl_pct"].mean()), 4),
                "mean_pnl_gbp": round(float(df["pnl_gbp"].mean()), 2),
            }
        result[f"{label_x}_only"] = _summary(x_only)
        result[f"{label_y}_only"] = _summary(y_only)
    else:
        result["note"] = f"n_differing_total ({n_differing}) below usable floor ({MIN_DIFFERING_USABLE}) -- NOT interpreted"
    return result


def top_decile_overlap(x: pd.DataFrame, y: pd.DataFrame) -> dict:
    def _top_decile(df):
        n = max(1, len(df) // 10)
        return df.sort_values("pnl_gbp", ascending=False).head(n)

    tx, ty = _top_decile(x), _top_decile(y)
    tx_exact = set(zip(tx["ticker"], tx["entry_date"].astype(str)))
    ty_exact = set(zip(ty["ticker"], ty["entry_date"].astype(str)))
    exact_inter, exact_union = tx_exact & ty_exact, tx_exact | ty_exact

    tx_tick, ty_tick = set(tx["ticker"]), set(ty["ticker"])
    tick_inter, tick_union = tx_tick & ty_tick, tx_tick | ty_tick

    return {
        "n_top_decile_x": len(tx), "n_top_decile_y": len(ty),
        "exact_jaccard": round(len(exact_inter) / len(exact_union), 4) if exact_union else None,
        "ticker_jaccard": round(len(tick_inter) / len(tick_union), 4) if tick_union else None,
    }


if __name__ == "__main__":
    import yfinance as yf

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))

    with open(f"{CACHE_DIR}/_cache_pool2_clean.pkl", "rb") as fh:
        cache_meta = pickle.load(fh)
    end = pd.Timestamp(cache_meta["end"])
    total_years = (end - START).days / 365.25
    gspc_close_full = cache_meta["gspc_close_full"]

    irx_h = yf.Ticker("^IRX").history(start="2007-01-01", end=str((end + pd.Timedelta(days=1)).date()), auto_adjust=False)
    irx_h.index = irx_h.index.tz_localize(None)
    irx_close = irx_h["Close"].astype(float).sort_index()
    irx_close.index = pd.to_datetime(irx_close.index)
    irx_window = irx_close[(irx_close.index >= START) & (irx_close.index <= end)]
    mean_irx_pct = float(irx_window.mean())
    print(f"Mean ^IRX rate {START.date()}->{end.date()}: {mean_irx_pct:.4f}%/yr")

    h = yf.Ticker("^SP500TR").history(start="2007-01-01", end=str((end + pd.Timedelta(days=1)).date()), auto_adjust=False)
    h.index = h.index.tz_localize(None)
    sp500tr_close = h["Close"].astype(float)
    sp500tr_close.index = pd.to_datetime(sp500tr_close.index)
    print(f"^SP500TR: {len(sp500tr_close)} rows, {sp500tr_close.index.min().date()} -> {sp500tr_close.index.max().date()}")

    results = {}
    for pool in ("pool1", "pool2_clean"):
        for arm in ARMS:
            trades = pd.read_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", parse_dates=["entry_date", "exit_date"])
            panel = pd.read_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv")
            try:
                rejections = pd.read_csv(f"{OUT_DIR}/rejections_{pool}_{arm}.csv")
            except (FileNotFoundError, pd.errors.EmptyDataError):
                rejections = pd.DataFrame()

            ca = cost_adjustment(trades, panel, starting_capital, mean_irx_pct, total_years)
            ca.update(rejection_summary(rejections))
            ca.update(excess_vs_benchmark_cumulative(trades, starting_capital, sp500tr_close, end))
            ca.update(excess_vs_benchmark_cost_adjusted(ca["cost_adjusted_cagr_pct"], sp500tr_close, end))
            ca["nominal_cap_pct"] = NOMINAL_CAP[arm]
            ca["deployment_gap_pp"] = round(NOMINAL_CAP[arm] - ca["mean_deployed_pct"], 2)
            if pool == "pool2_clean":
                bv = block_validation(trades, gspc_close_full, starting_capital, end)
                ca["block_pass"] = f"{bv['n_blocks_pass']}/{bv['n_blocks_total']}"
                ca["excess_per_block"] = excess_per_block(trades, starting_capital, sp500tr_close, end)
            results[f"{pool}_{arm}"] = ca
            print(f"{pool}/{arm}: {json.dumps({k: v for k, v in ca.items() if k != 'excess_per_block'}, indent=2)}")

    for pool in ("pool1", "pool2_clean"):
        base = pd.read_csv(f"{OUT_DIR}/trades_{pool}_c70.csv", parse_dates=["entry_date", "exit_date"])
        base["entry_date"] = base["entry_date"].dt.date
        for arm in ("c80", "c90", "c95"):
            arm_df = pd.read_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", parse_dates=["entry_date", "exit_date"])
            arm_df["entry_date"] = arm_df["entry_date"].dt.date
            results[f"{pool}_paired_c70_vs_{arm}"] = paired_exact(base, arm_df, "c70", arm)
            results[f"{pool}_overlap_c70_vs_{arm}"] = top_decile_overlap(base, arm_df)
            print(f"\n{pool} paired c70 vs {arm}: {json.dumps(results[f'{pool}_paired_c70_vs_{arm}'], indent=2, default=str)}")
            print(f"{pool} top-decile overlap c70 vs {arm}: {json.dumps(results[f'{pool}_overlap_c70_vs_{arm}'], indent=2)}")

    with open(f"{OUT_DIR}/step2_all_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {OUT_DIR}/step2_all_results.json")
