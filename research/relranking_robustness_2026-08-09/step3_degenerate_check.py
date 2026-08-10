"""One-time spot-check, under a real perturbation (drop01_CAG), that
relative_ranking_engine.py's _sort_key="momentum" mode still reproduces raw
production simulate_portfolio_compounding_mtm() byte-identical -- confirming
the degenerate-case proof from the original relative-ranking study
generalizes to perturbed inputs (expected by construction, since the sort-key
line is the only difference regardless of what data is fed in, but checked
directly rather than assumed).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app-relranking-robustness")
sys.path.insert(0, r"c:\GitHub\investment-app-relranking-robustness\research\relative_ranking_prereg_2026-08-09")

import pandas as pd
import yaml

from analysis.backtest import simulate_portfolio_compounding_mtm
from relative_ranking_engine import simulate_relative_ranking
from step2_run_paired_draw import build_perturbed_inputs

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

MAX_DEPLOYED_PCT = 0.70
PER_TRADE_PCT = 0.05


def _trades_df(trades):
    df = pd.DataFrame(trades)
    df["entry_date"] = df["entry_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    df["exit_date"] = df["exit_date"].apply(lambda d: d if not hasattr(d, "date") else d.date())
    return df


def main():
    draw = {"draw_id": "drop01_CAG", "ptype": "drop_ticker", "param": "CAG"}
    caches, gspc_close, start = build_perturbed_inputs(draw)
    print(f"Perturbed inputs built: start={start.date()}, n_tickers={len(caches)}", flush=True)

    raw = simulate_portfolio_compounding_mtm(caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT, collect_rejections=False)
    raw_df = _trades_df(raw)
    print(f"raw production: {len(raw_df)} trades, pnl={raw_df['pnl_gbp'].sum():.2f}", flush=True)

    deg = simulate_relative_ranking(caches, CONFIG, gspc_close, PER_TRADE_PCT, MAX_DEPLOYED_PCT,
                                     apply_rank_dropped_exit=False, _sort_key="momentum")
    deg_df = _trades_df(deg["trades"])
    print(f"degenerate (_sort_key=momentum): {len(deg_df)} trades, pnl={deg_df['pnl_gbp'].sum():.2f}", flush=True)

    if len(raw_df) != len(deg_df):
        print(f"FAIL: row count mismatch {len(raw_df)} vs {len(deg_df)}")
        return
    a2 = raw_df.sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    b2 = deg_df.sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    diffs = 0
    for col in ["exit_date", "exit_reason", "holding_days"]:
        mism = (a2[col].astype(str) != b2[col].astype(str)).sum()
        diffs += mism
    for col in ["pnl_gbp", "position_gbp", "entry_price", "exit_price"]:
        mism = (a2[col] - b2[col]).abs().gt(0.01).sum()
        diffs += mism
    print(f"RESULT: {'PASS -- byte-identical under perturbation' if diffs == 0 else f'FAIL -- {diffs} mismatches'}")


if __name__ == "__main__":
    main()
