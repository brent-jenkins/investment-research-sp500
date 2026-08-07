"""Step 6: whole-run summary, 16-block validation, and capacity-turnover
for baseline vs. earnings_shrink."""
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

OUT_DIR = "research/earnings_proximity_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")
LABELS = [
    "baseline", "earnings_shrink",
    "cap05_baseline", "cap05_shrink",
    "cap06_baseline", "cap06_shrink",
    "cap07_baseline", "cap07_shrink",
]


def block_validate(trades_df, gspc_close_full, starting_capital, label):
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
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 50000))
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    variants = {}
    for label in LABELS:
        df_v = pd.read_csv(f"{OUT_DIR}/trades_{label}.csv")
        df_v["exit_date"] = pd.to_datetime(df_v["exit_date"])
        df_v["entry_date"] = pd.to_datetime(df_v["entry_date"])
        variants[label] = df_v

    print("=== Whole-run summary (fixed-capital metrics, full 2007-2026 continuous walk) ===", flush=True)
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
    pd.DataFrame(summary_rows).to_csv(f"{OUT_DIR}/whole_run_summary.csv", index=False)

    print("\n=== 16-block validation ===", flush=True)
    all_block_results = {}
    for label, df_v in variants.items():
        all_block_results[label] = block_validate(df_v, gspc_close_full, starting_capital, label)
    with open(f"{OUT_DIR}/block_validation_results.json", "w") as fh:
        json.dump(all_block_results, fh, default=str, indent=2)

    base_pass = [b["passed"] for b in all_block_results["baseline"]["blocks"]]
    for label in LABELS:
        passing = [i for i, b in enumerate(all_block_results[label]["blocks"]) if b["passed"]]
        print(f"  {label}: {all_block_results[label]['n_blocks_pass']}/16 -> blocks {passing}")

    print("\n=== Capacity-turnover / admission interaction (each shrink variant vs its OWN same-cap baseline) ===", flush=True)
    pairs = [
        ("earnings_shrink", "baseline"),
        ("cap05_shrink", "cap05_baseline"),
        ("cap06_shrink", "cap06_baseline"),
        ("cap07_shrink", "cap07_baseline"),
    ]
    interaction_rows = []
    for shrink_label, base_label in pairs:
        df_base = variants[base_label]
        df_v = variants[shrink_label]
        base_set = set(zip(df_base["ticker"], df_base["entry_date"].astype(str)))
        var_set = set(zip(df_v["ticker"], df_v["entry_date"].astype(str)))
        removed = base_set - var_set
        added = var_set - base_set
        removed_dates = {d for _, d in removed}
        added_dates = {d for _, d in added}
        same_day_swap_dates = removed_dates & added_dates
        print(f"  {shrink_label} vs {base_label}: {len(removed)} removed, {len(added)} newly admitted, "
              f"{len(base_set & var_set)} unchanged, {len(same_day_swap_dates)} same-day contested-slot-swap dates")
        interaction_rows.append({
            "shrink_variant": shrink_label, "base_variant": base_label,
            "n_removed": len(removed), "n_added": len(added),
            "n_unchanged": len(base_set & var_set), "n_same_day_swap_dates": len(same_day_swap_dates),
            "n_trades_shrink": len(df_v), "n_trades_base": len(df_base),
        })
    pd.DataFrame(interaction_rows).to_csv(f"{OUT_DIR}/capacity_turnover.csv", index=False)


if __name__ == "__main__":
    main()
