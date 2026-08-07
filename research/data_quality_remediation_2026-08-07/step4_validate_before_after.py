"""Item 9 -- before/after 16-block validation for the recycled-ticker
quarantine remediation. Single changed variable: candidate pool.
  BEFORE = universe.get_all_known_stock_tickers() (unrestricted --
           the exact mechanism the invalidated re-verification used;
           already-computed trades from research/earnings_dampener_
           reverify_2026-08-07/trades_cap0{5,7}_baseline.csv)
  AFTER  = universe.get_research_stock_candidates() (quarantine-excluded;
           trades_cap0{5,7}_after.csv, this directory)
Same engine (simulate_portfolio_compounding_mtm), same caps (0.50/0.70),
same config, same 2007-01-01..2026-08-06 date range, no earnings dampener
or other new mechanism (baseline-only, per the remediation task's scope).
Reset-at-boundary vs continuous-walk: N/A -- this is a single continuous
2007-2026 walk per variant (matches the "before" run's own convention),
not a train/test split; both arms use the identical convention so no
contamination risk applies here.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-data-quality")

import json

import pandas as pd
import yaml

from analysis.backtest import compute_metrics_fixed, make_blocks
from analysis.validation import _block_passes, _cagr_dd_for_series
from data.ingestion import get_price_df

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

BEFORE_DIR = "research/earnings_dampener_reverify_2026-08-07"
AFTER_DIR = "research/data_quality_remediation_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")


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
    return {"label": label, "n_blocks_pass": n_pass, "n_blocks_total": len(blocks_raw), "blocks": block_results}


def main():
    starting_capital = float(CONFIG.get("backtest", {}).get("starting_capital_gbp", 50000))
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)

    pairs = [
        ("cap05", f"{BEFORE_DIR}/trades_cap05_baseline.csv", f"{AFTER_DIR}/trades_cap05_after.csv"),
        ("cap07", f"{BEFORE_DIR}/trades_cap07_baseline.csv", f"{AFTER_DIR}/trades_cap07_after.csv"),
    ]

    all_results = {}
    summary_rows = []
    for cap_tag, before_path, after_path in pairs:
        for side, path in [("before", before_path), ("after", after_path)]:
            df_v = pd.read_csv(path)
            df_v["exit_date"] = pd.to_datetime(df_v["exit_date"])
            df_v["entry_date"] = pd.to_datetime(df_v["entry_date"])
            label = f"{cap_tag}_{side}"
            res = block_validate(df_v, gspc_close_full, starting_capital, label)
            all_results[label] = res
            summary_rows.append({
                "variant": label, "n_trades": len(df_v), "n_distinct_tickers": df_v["ticker"].nunique(),
                "total_pnl_gbp": round(df_v["pnl_gbp"].sum(), 2),
                "n_blocks_pass": res["n_blocks_pass"], "n_blocks_total": res["n_blocks_total"],
            })
            print(f"  [{label}] n_trades={len(df_v)} total_pnl_gbp={df_v['pnl_gbp'].sum():.2f} "
                  f"blocks_pass={res['n_blocks_pass']}/16", flush=True)

    print("\n=== SUMMARY: before -> after, per cap ===", flush=True)
    for cap_tag, _, _ in pairs:
        b = all_results[f"{cap_tag}_before"]
        a = all_results[f"{cap_tag}_after"]
        print(f"  {cap_tag}: before={b['n_blocks_pass']}/16  after={a['n_blocks_pass']}/16", flush=True)

    with open(f"{AFTER_DIR}/before_after_block_validation_results.json", "w") as fh:
        json.dump(all_results, fh, default=str, indent=2)
    pd.DataFrame(summary_rows).to_csv(f"{AFTER_DIR}/before_after_whole_run_summary.csv", index=False)
    print(f"\nWrote before_after_block_validation_results.json and before_after_whole_run_summary.csv", flush=True)


if __name__ == "__main__":
    main()
