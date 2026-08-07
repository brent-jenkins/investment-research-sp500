"""Addendum: is Pool 2's worse-than-Pool-1 drawdown (and the A/B drawdown
reversal between pools) driven by the ~130 still-unadjudicated NEEDS_REVIEW
tickers? Two tests, both cheap since trade-level output already exists:

1. Identify the worst peak-relative drawdown EPISODE for pool2_A and
   pool2_B (the peak date and the trough date of the single worst
   drawdown), find every trade open at any point during that episode, and
   report how many/how much P&L came from NEEDS_REVIEW-tagged tickers vs
   CLEAN ones.
2. Recompute peak-relative max DD for pool2_A/pool2_B using ONLY CLEAN
   tickers (NEEDS_REVIEW trades excluded entirely) and compare to the
   full-population DD already reported -- if excluding NEEDS_REVIEW
   materially improves the drawdown, that's direct evidence the
   disagreement is a residual-contamination artifact, not a genuine
   property of the strategy.
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import json
import sqlite3

import pandas as pd

from analysis.backtest import compute_peak_relative_drawdown_pct, equity_curve_gbp

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"
STARTING_CAPITAL = 50000.0


def get_needs_review_tickers() -> set:
    conn = sqlite3.connect("database/investment_research.db")
    rows = conn.execute("SELECT ticker FROM ticker_data_quality WHERE status = 'NEEDS_REVIEW'").fetchall()
    return {r[0] for r in rows}


def worst_episode(trades_df: pd.DataFrame) -> tuple:
    ec = equity_curve_gbp(trades_df, starting_capital_gbp=STARTING_CAPITAL)
    ec["peak"] = ec["equity_gbp"].cummax()
    ec["dd_pct"] = (ec["equity_gbp"] - ec["peak"]) / ec["peak"] * 100
    trough_idx = ec["dd_pct"].idxmin()
    trough_date = ec.loc[trough_idx, "date"]
    # peak date = the most recent date at/before trough where equity == peak (i.e. dd_pct == 0)
    pre_trough = ec[ec["date"] <= trough_date]
    peak_date = pre_trough[pre_trough["dd_pct"] == 0]["date"].max()
    return peak_date, trough_date, float(ec.loc[trough_idx, "dd_pct"])


def episode_breakdown(trades_df: pd.DataFrame, needs_review: set, peak_date, trough_date) -> dict:
    df = trades_df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    open_during = df[(df["entry_date"] <= trough_date) & (df["exit_date"] >= peak_date)]

    nr = open_during[open_during["ticker"].isin(needs_review)]
    clean = open_during[~open_during["ticker"].isin(needs_review)]

    return {
        "peak_date": peak_date.date().isoformat(), "trough_date": trough_date.date().isoformat(),
        "n_open_during_episode": len(open_during),
        "n_needs_review": len(nr), "n_clean_or_quarantined_universe": len(clean),
        "needs_review_pnl_gbp": round(nr["pnl_gbp"].sum(), 2) if not nr.empty else 0.0,
        "clean_pnl_gbp": round(clean["pnl_gbp"].sum(), 2) if not clean.empty else 0.0,
        "needs_review_tickers_involved": sorted(nr["ticker"].unique().tolist()),
    }


def clean_only_drawdown(trades_df: pd.DataFrame, needs_review: set) -> float:
    clean_trades = trades_df[~trades_df["ticker"].isin(needs_review)]
    trades = clean_trades.to_dict("records")
    return compute_peak_relative_drawdown_pct(trades, STARTING_CAPITAL)


def main():
    needs_review = get_needs_review_tickers()
    print(f"NEEDS_REVIEW population: {len(needs_review)} tickers\n", flush=True)

    out = {}
    for arm in ["A", "B"]:
        label = f"pool2_{arm}"
        df_v = pd.read_csv(f"{OUT_DIR}/trades_{label}.csv")
        df_v["exit_date"] = pd.to_datetime(df_v["exit_date"])
        df_v["entry_date"] = pd.to_datetime(df_v["entry_date"])

        peak_date, trough_date, worst_dd = worst_episode(df_v)
        print(f"[{label}] worst drawdown episode: {peak_date.date()} -> {trough_date.date()} "
              f"({worst_dd:.2f}%)", flush=True)

        eb = episode_breakdown(df_v, needs_review, peak_date, trough_date)
        print(f"  open during episode: {eb['n_open_during_episode']} trades "
              f"({eb['n_needs_review']} NEEDS_REVIEW, {eb['n_clean_or_quarantined_universe']} clean)", flush=True)
        print(f"  P&L during episode: NEEDS_REVIEW={eb['needs_review_pnl_gbp']}  "
              f"clean={eb['clean_pnl_gbp']}", flush=True)

        clean_dd = clean_only_drawdown(df_v, needs_review)
        full_dd = worst_dd
        print(f"  full-population peak DD: {full_dd:.2f}%   CLEAN-only peak DD: {clean_dd:.2f}%  "
              f"(delta: {clean_dd - full_dd:+.2f}pp)\n", flush=True)

        out[label] = {
            "worst_episode": eb, "full_population_peak_dd_pct": round(full_dd, 2),
            "clean_only_peak_dd_pct": clean_dd, "delta_pp": round(clean_dd - full_dd, 2),
        }

    with open(f"{OUT_DIR}/step6_drawdown_contamination_check.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print("Wrote step6_drawdown_contamination_check.json", flush=True)


if __name__ == "__main__":
    main()
