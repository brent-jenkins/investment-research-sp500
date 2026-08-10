"""Post-processing instruments for the momentum-smoothness study: cost
adjustment, excess return vs ^SP500TR (per block AND cumulative), the
deployment guard, exact (ticker, entry_date) paired comparison, top-decile
overlap, A-vs-B overlap prediction check (Sec 5), and 16-block corroboration
(Pool 2 CLEAN only).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-smoothness")

import json
import pickle

import pandas as pd
import yaml

from analysis.backtest import make_blocks, compute_metrics_fixed, compute_peak_relative_drawdown_pct
from analysis.validation import _cagr_dd_for_series, _block_passes
from data.ingestion import get_price_df

OUT_DIR = "research/momentum_smoothness_prereg_2026-08-10"
FX_ROUND_TRIP_FEE = 0.0030
START = pd.Timestamp("2007-01-01")
ARMS = ["A", "B1", "B2", "B3"]
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
        "mean_n_open_positions": round(float(panel["n_open_positions"].mean()), 2) if "n_open_positions" in panel.columns else None,
        "fx_drag_pct_per_year": round(fx_drag_pct_yr, 4),
        "idle_credit_pct_per_year": round(idle_credit_pct_yr, 4),
        "cost_adjusted_cagr_pct": cost_adj_cagr,
    }


def excess_vs_benchmark_cumulative(trades, starting_capital, sp500tr_close, end):
    strat_cagr = compute_metrics_fixed(trades.to_dict("records"), starting_capital)["cagr_pct"]
    bench_cagr, _ = _cagr_dd_for_series(sp500tr_close, START, end)
    return {"strategy_frictionless_cagr_pct": strat_cagr, "sp500tr_cagr_pct": round(bench_cagr, 2),
            "excess_frictionless_pct": round(strat_cagr - bench_cagr, 3)}


def excess_vs_benchmark_cost_adjusted(cost_adj_cagr, sp500tr_close, end):
    bench_cagr, _ = _cagr_dd_for_series(sp500tr_close, START, end)
    return {"sp500tr_cagr_pct": round(bench_cagr, 2), "excess_cost_adjusted_pct": round(cost_adj_cagr - bench_cagr, 3)}


def excess_per_block(trades, starting_capital, sp500tr_close, end):
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


def block_validation(trades, gspc_close_full, starting_capital, end):
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


def paired_exact(x, y, label_x, label_y):
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


def top_decile_overlap(x, y):
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




# --------------------------------------------------------------------------
# §6 filter-effect instruments — new for this study
# --------------------------------------------------------------------------

def filter_effect(res: dict, arm_a_trades: pd.DataFrame, threshold: float) -> dict:
    """§6: was the filter a meaningful test at this threshold?

    Two floors, both pre-registered, and the SECOND is the one that matters.
    The selection audit found only 7-8% of admissions sit outside their day's
    top quartile and 92-93% happen only because that day's own exits freed a
    slot -- so a filter can thin a large fraction of CANDIDATE-DAYS while
    touching almost no ADMISSIONS. Candidate-day exclusion alone would report
    such an arm as a genuine null; admission displacement catches it.
    """
    fs = res["filter_stats"]
    seen = fs["evaluated"] + fs["excluded_nan"]
    excluded = fs["excluded_threshold"] + fs["excluded_nan"]

    # Admission displacement: of arm A's actual admissions, how many did this
    # filter exclude? Keyed on (ticker, entry_date) -- an exclusion only counts
    # if arm A genuinely entered that ticker on that date.
    excl_keys = {(t, pd.Timestamp(d).date()) for t, d, _v in res["filter_excluded"]}
    a_keys = {(r["ticker"], pd.Timestamp(r["entry_date"]).date())
              for _, r in arm_a_trades.iterrows()}
    displaced = a_keys & excl_keys

    return {
        "threshold": threshold,
        "candidate_days_seen": seen,
        "candidate_days_excluded": excluded,
        "exclusion_pct": round(100.0 * excluded / max(seen, 1), 2),
        "excluded_nan": fs["excluded_nan"],
        "arm_a_admissions": len(a_keys),
        "arm_a_admissions_displaced": len(displaced),
        "displacement_pct": round(100.0 * len(displaced) / max(len(a_keys), 1), 2),
        "meaningful_test": (100.0 * excluded / max(seen, 1)) >= 5.0
                           and (100.0 * len(displaced) / max(len(a_keys), 1)) >= 5.0,
    }


if __name__ == "__main__":
    import yfinance as yf

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    starting_capital = float(config.get("backtest", {}).get("starting_capital_gbp", 10000))

    with open(f"{OUT_DIR}/_thresholds.pkl", "rb") as fh:
        TH = pickle.load(fh)
    THR = {"A": None, "B1": TH["T1"], "B2": TH["T2"], "B3": TH["T3"]}

    with open(f"{OUT_DIR}/_cache_pool2_clean.pkl", "rb") as fh:
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
    print(f"Mean ^IRX rate {START.date()}->{end.date()}: {mean_irx_pct:.4f}%/yr", flush=True)

    h = yf.Ticker("^SP500TR").history(start="2007-01-01", end=str((end + pd.Timedelta(days=1)).date()), auto_adjust=False)
    h.index = h.index.tz_localize(None)
    sp500tr_close = h["Close"].astype(float)
    sp500tr_close.index = pd.to_datetime(sp500tr_close.index)
    print(f"^SP500TR: {len(sp500tr_close)} rows, {sp500tr_close.index.min().date()} -> {sp500tr_close.index.max().date()}", flush=True)

    raw, results = {}, {}
    for pool in ("pool2_clean", "pool1"):
        for arm in ARMS:
            with open(f"{OUT_DIR}/_arm{arm}_{pool}.pkl", "rb") as fh:
                res = pickle.load(fh)
            raw[f"{pool}_{arm}"] = res
            trades = pd.DataFrame(res["trades"])
            trades["entry_date"] = pd.to_datetime(trades["entry_date"])
            trades["exit_date"] = pd.to_datetime(trades["exit_date"])
            panel = pd.DataFrame(res["panel"])
            trades.to_csv(f"{OUT_DIR}/trades_{pool}_{arm}.csv", index=False)
            panel.to_csv(f"{OUT_DIR}/panel_{pool}_{arm}.csv", index=False)

            ca = cost_adjustment(trades, panel, starting_capital, mean_irx_pct, total_years)
            ca.update(excess_vs_benchmark_cumulative(trades, starting_capital, sp500tr_close, end))
            ca.update(excess_vs_benchmark_cost_adjusted(ca["cost_adjusted_cagr_pct"], sp500tr_close, end))
            if pool == "pool2_clean":
                bv = block_validation(trades, gspc_close_full, starting_capital, end)
                ca["block_pass"] = f"{bv['n_blocks_pass']}/{bv['n_blocks_total']}"
            results[f"{pool}_{arm}"] = ca
            print(f"{pool}/{arm}: {json.dumps({k: v for k, v in ca.items() if k != 'excess_per_block'})}", flush=True)

    # §6 filter effect + §7 paired / overlap, every filtered arm vs A.
    for pool in ("pool2_clean", "pool1"):
        base = pd.DataFrame(raw[f"{pool}_A"]["trades"])
        base["entry_date"] = pd.to_datetime(base["entry_date"])
        base_d = base.copy(); base_d["entry_date"] = base_d["entry_date"].dt.date
        for arm in ("B1", "B2", "B3"):
            res = raw[f"{pool}_{arm}"]
            results[f"{pool}_filter_{arm}"] = filter_effect(res, base, THR[arm])
            print(f"\n{pool} filter effect {arm}: "
                  f"{json.dumps(results[f'{pool}_filter_{arm}'])}", flush=True)

            arm_df = pd.DataFrame(res["trades"])
            arm_df["entry_date"] = pd.to_datetime(arm_df["entry_date"])
            arm_d = arm_df.copy(); arm_d["entry_date"] = arm_d["entry_date"].dt.date
            results[f"{pool}_paired_A_vs_{arm}"] = paired_exact(base_d, arm_d, "A", arm)
            results[f"{pool}_overlap_A_vs_{arm}"] = top_decile_overlap(base, arm_df)
            print(f"{pool} paired A vs {arm}: {json.dumps(results[f'{pool}_paired_A_vs_{arm}'], default=str)}", flush=True)
            print(f"{pool} top-decile A vs {arm}: {json.dumps(results[f'{pool}_overlap_A_vs_{arm}'])}", flush=True)

    with open(f"{OUT_DIR}/step5_all_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nWrote {OUT_DIR}/step5_all_results.json")
