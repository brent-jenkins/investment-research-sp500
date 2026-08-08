"""Post-processing instruments for the admission-redesign study: literal and
month-level paired comparison, top-decile ticker overlap, cost adjustment
(FX + idle-cash from the daily deployment panel), and 16-block corroboration.
Pure functions over already-produced trades_*.csv / panel_*.csv -- no new
simulation runs.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-admission-redesign")

import json

import pandas as pd
import yaml

from analysis.backtest import make_blocks, compute_metrics_fixed
from analysis.validation import _cagr_dd_for_series, _block_passes
from data.ingestion import get_price_df

OUT_DIR = "research/admission_redesign_prereg_2026-08-08"
FX_ROUND_TRIP_FEE = 0.0030
START = pd.Timestamp("2007-01-01")
END = pd.Timestamp("2026-08-06")
TOTAL_YEARS = (END - START).days / 365.25
MIN_SHARED_PAIRS_USABLE = 200


def paired_comparison_literal(x: pd.DataFrame, y: pd.DataFrame, label_x: str, label_y: str) -> dict:
    """Exact (ticker, entry_date) match -- expected to be near-degenerate for
    this study (daily vs. monthly-rebalance calendars), per pre-registration
    §4's own stated expectation. Reported, not interpreted, if under the
    usable-sample floor."""
    x_by_key = {(r["ticker"], str(r["entry_date"])): r for _, r in x.iterrows()}
    y_by_key = {(r["ticker"], str(r["entry_date"])): r for _, r in y.iterrows()}
    shared = sorted(set(x_by_key) & set(y_by_key))
    n_shared = len(shared)
    result = {
        "label_x": label_x, "label_y": label_y, "n_total_x": len(x), "n_total_y": len(y),
        "n_shared_pairs": n_shared, "usable": n_shared >= MIN_SHARED_PAIRS_USABLE,
    }
    if n_shared >= MIN_SHARED_PAIRS_USABLE:
        diffs = [y_by_key[k]["pnl_pct"] - x_by_key[k]["pnl_pct"] for k in shared]
        result["mean_pnl_pct_diff_y_minus_x"] = round(sum(diffs) / len(diffs), 4)
        win_rate = sum(1 for d in diffs if d > 0) / len(diffs)
        result["win_rate_y_over_x_pct"] = round(100 * win_rate, 1)
    else:
        result["note"] = f"n_shared_pairs ({n_shared}) below usable floor ({MIN_SHARED_PAIRS_USABLE}) -- NOT interpreted, per pre-registration §4"
    return result


def paired_comparison_monthly(x: pd.DataFrame, y: pd.DataFrame, label_x: str, label_y: str) -> dict:
    """Month-level adaptation (pre-registration §4): pair on (ticker,
    calendar-year-month of entry). NOT fully cascade-free -- within-month
    capacity dynamics still differ between arms. If a ticker has more than
    one entry from the same arm in the same month, its pnl_pct values are
    averaged for that (ticker, month) cell -- stated methodology, not hidden."""
    def _keyed(df):
        d = df.copy()
        d["entry_date"] = pd.to_datetime(d["entry_date"])
        d["ym"] = d["entry_date"].dt.to_period("M")
        return d.groupby(["ticker", "ym"])["pnl_pct"].mean()

    x_k = _keyed(x)
    y_k = _keyed(y)
    shared = sorted(set(x_k.index) & set(y_k.index))
    n_shared = len(shared)
    result = {
        "label_x": label_x, "label_y": label_y, "n_total_x": len(x), "n_total_y": len(y),
        "n_shared_ticker_months": n_shared,
    }
    if n_shared > 0:
        diffs = [(y_k.loc[k] - x_k.loc[k]) for k in shared]
        result["mean_pnl_pct_diff_y_minus_x"] = round(sum(diffs) / len(diffs), 4)
        win_rate = sum(1 for d in diffs if d > 0) / len(diffs)
        result["win_rate_y_over_x_pct"] = round(100 * win_rate, 1)
        n_differ = sum(1 for d in diffs if abs(d) > 1e-9)
        result["n_differing_outcome"] = n_differ
    else:
        result["note"] = "zero shared (ticker, month) cells -- not usable"
    return result


def top_decile_ticker_overlap(x: pd.DataFrame, y: pd.DataFrame) -> dict:
    def _top_decile_tickers(df):
        n = max(1, len(df) // 10)
        return set(df.sort_values("pnl_gbp", ascending=False).head(n)["ticker"])

    tx, ty = _top_decile_tickers(x), _top_decile_tickers(y)
    inter = tx & ty
    union = tx | ty
    return {
        "n_top_decile_tickers_x": len(tx), "n_top_decile_tickers_y": len(ty),
        "n_intersection": len(inter), "n_union": len(union),
        "jaccard_ticker_overlap": round(len(inter) / len(union), 4) if union else None,
    }


def cost_adjustment(trades: pd.DataFrame, panel: pd.DataFrame, starting_capital: float, mean_irx_pct: float) -> dict:
    frictionless = compute_metrics_fixed(trades.to_dict("records"), starting_capital)
    total_notional = float(trades["position_gbp"].sum())
    fx_cost_gbp = total_notional * FX_ROUND_TRIP_FEE
    mean_balance = float(panel["current_balance"].mean())
    mean_deployed_pct = float(panel["current_deployed_pct"].mean())
    mean_idle_pct = 100.0 - mean_deployed_pct
    fx_drag_pct_yr = (fx_cost_gbp / TOTAL_YEARS) / mean_balance * 100.0 if mean_balance > 0 else 0.0
    idle_credit_pct_yr = (mean_idle_pct / 100.0) * mean_irx_pct
    net_adj_pct = idle_credit_pct_yr - fx_drag_pct_yr
    cost_adj_cagr = round(frictionless["cagr_pct"] + net_adj_pct, 3)
    return {
        "frictionless_cagr_pct": frictionless["cagr_pct"],
        "max_drawdown_fixed_pct": frictionless["max_drawdown_pct"],
        "total_notional_gbp": round(total_notional, 2),
        "mean_balance_gbp": round(mean_balance, 2),
        "mean_deployed_pct": round(mean_deployed_pct, 2),
        "mean_idle_pct": round(mean_idle_pct, 2),
        "fx_drag_pct_per_year": round(fx_drag_pct_yr, 4),
        "idle_credit_pct_per_year": round(idle_credit_pct_yr, 4),
        "net_adjustment_pct_per_year": round(net_adj_pct, 4),
        "cost_adjusted_cagr_pct": cost_adj_cagr,
    }


def peak_relative_drawdown(trades: pd.DataFrame, starting_capital: float) -> float:
    from analysis.backtest import compute_peak_relative_drawdown_pct
    return compute_peak_relative_drawdown_pct(trades.to_dict("records"), starting_capital)


def block_validation(trades: pd.DataFrame, gspc_close_full: pd.Series, starting_capital: float) -> dict:
    trades = trades.copy()
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])
    blocks = make_blocks(START, END, n_blocks=16)
    n_pass = 0
    details = []
    for i, (b_start, b_end) in enumerate(blocks):
        bt = trades[(trades["exit_date"] >= b_start) & (trades["exit_date"] <= b_end)].to_dict("records")
        m = compute_metrics_fixed(bt, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        p = _block_passes(m, sp_dd)
        if p:
            n_pass += 1
        details.append({"block": i, "n_trades": len(bt), "passed": p})
    return {"n_blocks_pass": n_pass, "n_blocks_total": len(blocks), "detail": details}


if __name__ == "__main__":
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))

    irx = get_price_df("^IRX", days=9000).set_index("date")
    irx.index = pd.to_datetime(irx.index)
    irx_close = irx["close"].astype(float).sort_index()
    irx_window = irx_close[(irx_close.index >= START) & (irx_close.index <= END)]
    mean_irx_pct = float(irx_window.mean())
    print(f"Mean ^IRX rate {START.date()}->{END.date()}: {mean_irx_pct:.4f}%/yr")

    results = {}
    import pickle
    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "rb") as fh:
        gspc_close_full = pickle.load(fh)["gspc_close_full"]

    for pool in ("pool1", "pool2_clean"):
        for arm in ("baseline", "redesign"):
            trades = pd.read_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv")
            panel = pd.read_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv")
            ca = cost_adjustment(trades, panel, starting_capital, mean_irx_pct)
            ca["peak_relative_drawdown_pct"] = peak_relative_drawdown(trades, starting_capital)
            if pool == "pool2_clean":
                bv = block_validation(trades, gspc_close_full, starting_capital)
                ca["block_pass"] = f"{bv['n_blocks_pass']}/{bv['n_blocks_total']}"
            results[f"{pool}_{arm}"] = ca
            print(f"{pool}/{arm}: {json.dumps(ca, indent=2)}")

    for pool in ("pool1", "pool2_clean"):
        x = pd.read_csv(f"{OUT_DIR}/trades_{pool}_baseline.csv")
        y = pd.read_csv(f"{OUT_DIR}/trades_{pool}_redesign.csv")
        results[f"{pool}_paired_literal"] = paired_comparison_literal(x, y, "baseline", "redesign")
        results[f"{pool}_paired_monthly"] = paired_comparison_monthly(x, y, "baseline", "redesign")
        results[f"{pool}_top_decile_overlap"] = top_decile_ticker_overlap(x, y)
        print(f"\n{pool} paired literal: {json.dumps(results[f'{pool}_paired_literal'], indent=2, default=str)}")
        print(f"{pool} paired monthly: {json.dumps(results[f'{pool}_paired_monthly'], indent=2, default=str)}")
        print(f"{pool} top-decile overlap: {json.dumps(results[f'{pool}_top_decile_overlap'], indent=2)}")

    with open(f"{OUT_DIR}/step4_all_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {OUT_DIR}/step4_all_results.json")
