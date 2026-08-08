"""16-block validation, BOTH frictionless and cost-adjusted, for the
corrected-universe baseline vs. earnings_shrink comparison at a given cap.

Cost adjustment applied at the TRADE level (not a post-hoc aggregate CAGR
tweak): every trade's pnl_gbp is debited by position_gbp * FX_ROUND_TRIP_FEE
(0.30%, T212's confirmed round-trip rate, per research/deployment_cap_
sweep_2026-08-06/cost_adjustment.py) and its pnl_pct is debited by the same
0.30 percentage points (proportional cost, same rate regardless of position
size), is_win recomputed from the adjusted pnl_pct. The adjusted trades are
then run through the exact same compute_metrics_fixed()/_block_passes()
pipeline as the frictionless numbers -- so PF, drawdown, and CAGR are ALL
genuinely cost-adjusted per block, not just an aggregate CAGR overlay.
Idle-cash interest credit is deliberately NOT included (conservative --
omitting a credit makes this a stricter test, not a more flattering one).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import json

import pandas as pd
import yaml

from analysis.backtest import compute_metrics_fixed, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = "research/earnings_dampener_reverify_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")
FX_ROUND_TRIP_FEE = 0.0030


def cost_adjust(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    fee_gbp = df["position_gbp"] * FX_ROUND_TRIP_FEE
    df["pnl_gbp"] = df["pnl_gbp"] - fee_gbp
    df["pnl_pct"] = df["pnl_pct"] - FX_ROUND_TRIP_FEE
    df["is_win"] = df["pnl_pct"] > 0
    return df


def block_validate(trades_df: pd.DataFrame, gspc_close_full: pd.Series, starting_capital: float, label: str) -> dict:
    blocks_raw = make_blocks(START, END, n_blocks=16)
    block_results = []
    n_pass = 0
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
            "n_trades": len(strat_trades), "strat_cagr": strat_m["cagr_pct"],
            "strat_dd": strat_m["max_drawdown_pct"], "strat_pf": strat_m.get("profit_factor", 0.0),
            "sp_cagr": round(sp_cagr, 2) if sp_cagr is not None else None,
            "sp_dd": round(sp_dd, 2) if sp_dd is not None else None,
            "passed": block_pass,
        })
    print(f"  [{label}] blocks passed: {n_pass}/{len(blocks_raw)}", flush=True)
    return {"label": label, "n_blocks_pass": n_pass, "n_blocks_total": len(blocks_raw), "blocks": block_results}


def main():
    cap_tag = sys.argv[1]  # e.g. "cap07" or "cap05"
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 50000))
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    labels = [f"{cap_tag}_baseline", f"{cap_tag}_shrink"]
    variants = {}
    for label in labels:
        df_v = pd.read_csv(f"{OUT_DIR}/trades_{label}.csv")
        df_v["exit_date"] = pd.to_datetime(df_v["exit_date"])
        df_v["entry_date"] = pd.to_datetime(df_v["entry_date"])
        variants[label] = df_v

    print(f"=== {cap_tag}: whole-run summary, FRICTIONLESS ===", flush=True)
    summary_rows = []
    for label, df_v in variants.items():
        trades = df_v.to_dict("records")
        m = compute_metrics_fixed(trades, starting_capital)
        row = {"variant": label, "n_trades": len(df_v), "n_distinct_tickers": df_v["ticker"].nunique(),
               "total_pnl_gbp": round(df_v["pnl_gbp"].sum(), 2),
               **{k: m.get(k) for k in ("win_rate", "profit_factor", "sharpe_ratio", "max_drawdown_pct",
                                          "cagr_pct", "total_return_pct")}}
        print(f"  {label}: {row}", flush=True)
        summary_rows.append(row)

    print(f"\n=== {cap_tag}: 16-block validation, FRICTIONLESS ===", flush=True)
    frictionless_blocks = {}
    for label, df_v in variants.items():
        frictionless_blocks[label] = block_validate(df_v, gspc_close_full, starting_capital, label)

    print(f"\n=== {cap_tag}: 16-block validation, COST-ADJUSTED (0.30% round-trip FX, no idle credit) ===", flush=True)
    cost_adjusted_blocks = {}
    for label, df_v in variants.items():
        adj = cost_adjust(df_v)
        cost_adjusted_blocks[label + "_cost_adj"] = block_validate(adj, gspc_close_full, starting_capital, label + "_cost_adj")
        adj.to_csv(f"{OUT_DIR}/trades_{label}_cost_adjusted.csv", index=False)

    print(f"\n=== {cap_tag}: SUMMARY ===", flush=True)
    print(f"  Frictionless: baseline={frictionless_blocks[labels[0]]['n_blocks_pass']}/16  "
          f"shrink={frictionless_blocks[labels[1]]['n_blocks_pass']}/16", flush=True)
    print(f"  Cost-adjusted: baseline={cost_adjusted_blocks[labels[0]+'_cost_adj']['n_blocks_pass']}/16  "
          f"shrink={cost_adjusted_blocks[labels[1]+'_cost_adj']['n_blocks_pass']}/16", flush=True)

    all_results = {**frictionless_blocks, **cost_adjusted_blocks}
    with open(f"{OUT_DIR}/{cap_tag}_block_validation_results.json", "w") as fh:
        json.dump(all_results, fh, default=str, indent=2)
    pd.DataFrame(summary_rows).to_csv(f"{OUT_DIR}/{cap_tag}_whole_run_summary.csv", index=False)


if __name__ == "__main__":
    main()
