"""Block validation (reset-at-boundary, per standing convention for an
arm-vs-arm comparison) + continuous-walk metrics, all 3 pools x 2 arms.
Same block_validate()/continuous_walk_metrics() template used throughout
this project's other studies.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-revalidation")

import json

import pandas as pd
import yaml

from analysis.backtest import compute_metrics_fixed, compute_peak_relative_drawdown_pct, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/adj_close_momentum_2026-08-08"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")
TOTAL_YEARS = (END - START).days / 365.25


def block_validate(trades_df, gspc_close_full, starting_capital, label):
    blocks_raw = make_blocks(START, END, n_blocks=16)
    block_results = []
    n_pass = 0
    per_block_winner = []
    for i, (b_start, b_end) in enumerate(blocks_raw):
        strat_trades = (
            [] if trades_df.empty else
            trades_df[(trades_df["exit_date"] >= b_start) & (trades_df["exit_date"] <= b_end)].to_dict("records")
        )
        strat_m = compute_metrics_fixed(strat_trades, starting_capital)
        sp_cagr, sp_dd = _cagr_dd_for_series(gspc_close_full, b_start, b_end)
        block_pass = _block_passes(strat_m, sp_dd)
        if block_pass:
            n_pass += 1
        block_results.append({
            "block": i, "start": b_start.date().isoformat(), "end": b_end.date().isoformat(),
            "n_trades": len(strat_trades), "cagr_pct": strat_m["cagr_pct"],
            "max_drawdown_pct": strat_m["max_drawdown_pct"], "profit_factor": strat_m.get("profit_factor", 0.0),
            "passed": block_pass,
        })
    return {"label": label, "n_blocks_pass": n_pass, "n_blocks_total": len(blocks_raw), "blocks": block_results}


def continuous_walk_metrics(trades_df, starting_capital):
    trades = trades_df.to_dict("records")
    total_pnl = trades_df["pnl_gbp"].sum()
    ending_equity = starting_capital + total_pnl
    cagr_pct = round((((ending_equity / starting_capital) ** (1 / TOTAL_YEARS)) - 1) * 100, 2)
    peak_dd_pct = compute_peak_relative_drawdown_pct(trades, starting_capital)
    m = compute_metrics_fixed(trades, starting_capital)
    return {
        "cagr_pct": cagr_pct, "ending_equity_gbp": round(ending_equity, 2),
        "peak_relative_max_dd_pct": peak_dd_pct,
        "total_return_pct": round((ending_equity / starting_capital - 1) * 100, 2),
        "profit_factor": m.get("profit_factor"), "win_rate": m.get("win_rate"),
    }


def main():
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 50000))
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    pools = ["pool1", "pool2_clean", "pool2_all"]
    all_block_results = {}
    summary_rows = []
    for pool in pools:
        for arm in ["A", "B"]:
            label = f"{pool}_{arm}"
            df_v = pd.read_csv(f"{OUT_DIR}/trades_{label}.csv")
            df_v["exit_date"] = pd.to_datetime(df_v["exit_date"])
            df_v["entry_date"] = pd.to_datetime(df_v["entry_date"])

            res = block_validate(df_v, gspc_close_full, starting_capital, label)
            all_block_results[label] = res
            cw = continuous_walk_metrics(df_v, starting_capital)
            total_pnl = df_v["pnl_gbp"].sum()
            mean_holding = df_v["holding_days"].mean()
            median_holding = df_v["holding_days"].median()
            exit_counts = df_v["exit_reason"].value_counts().to_dict()

            row = {
                "pool": pool, "arm": arm, "label": label,
                "n_trades": len(df_v), "n_distinct_tickers": df_v["ticker"].nunique(),
                "total_pnl_gbp": round(total_pnl, 2),
                "blocks_pass": res["n_blocks_pass"], "blocks_total": res["n_blocks_total"],
                "continuous_cagr_pct": cw["cagr_pct"], "continuous_ending_equity_gbp": cw["ending_equity_gbp"],
                "continuous_peak_relative_max_dd_pct": cw["peak_relative_max_dd_pct"],
                "continuous_total_return_pct": cw["total_return_pct"], "continuous_pf": cw["profit_factor"],
                "continuous_win_rate": cw["win_rate"],
                "mean_holding_days": round(mean_holding, 1), "median_holding_days": round(median_holding, 1),
                "exit_reasons": json.dumps(exit_counts),
            }
            summary_rows.append(row)
            print(f"[{label}] n_trades={len(df_v)} total_pnl_gbp={total_pnl:.2f} "
                  f"blocks_pass={res['n_blocks_pass']}/16 continuous_CAGR={cw['cagr_pct']}% "
                  f"continuous_peakDD={cw['peak_relative_max_dd_pct']}%", flush=True)

    with open(f"{OUT_DIR}/step7_block_validation_results.json", "w") as fh:
        json.dump(all_block_results, fh, default=str, indent=2)
    pd.DataFrame(summary_rows).to_csv(f"{OUT_DIR}/step7_summary.csv", index=False)
    print(f"\nWrote step7_block_validation_results.json and step7_summary.csv", flush=True)


if __name__ == "__main__":
    main()
