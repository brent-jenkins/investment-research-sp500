"""Per the pre-registration: capacity-turnover effect (A vs B, per pool,
kept separate from the direct effect) and, for Arm A only, the whipsaw
quantification (stopped-out positions re-entered within 30 calendar days,
and the P&L of those round trips).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import json

import pandas as pd

OUT_DIR = "research/stop_loss_reconfiguration_2026-08-07"


def capacity_turnover(pool):
    trades_a = pd.read_csv(f"{OUT_DIR}/trades_{pool}_A.csv")
    trades_b = pd.read_csv(f"{OUT_DIR}/trades_{pool}_B.csv")
    admitted_a = set(zip(trades_a["ticker"], trades_a["entry_date"]))
    admitted_b = set(zip(trades_b["ticker"], trades_b["entry_date"]))

    only_a = admitted_a - admitted_b
    only_b = admitted_b - admitted_a
    common = admitted_a & admitted_b

    rej_b = pd.read_csv(f"{OUT_DIR}/rejections_{pool}_B.csv")
    rej_b_capacity = rej_b[rej_b["reason"] == "REJECTED_CAPACITY"]
    confirmed_displaced = rej_b_capacity[
        rej_b_capacity.apply(lambda r: (r["ticker"], r["date"]) in only_a, axis=1)
    ] if not rej_b_capacity.empty else rej_b_capacity

    return {
        "pool": pool,
        "n_trades_a": len(trades_a), "n_trades_b": len(trades_b),
        "trade_count_delta": len(trades_b) - len(trades_a),
        "trade_count_delta_pct": round((len(trades_b) - len(trades_a)) / len(trades_a) * 100, 2),
        "admitted_a_only": len(only_a), "admitted_b_only": len(only_b), "admitted_both": len(common),
        "b_capacity_rejections_total": len(rej_b_capacity),
        "b_capacity_rejections_of_a_only_candidates": len(confirmed_displaced),
    }


def whipsaw_analysis(pool):
    """Arm A only: STOP_LOSS exits, same ticker re-entered within 30
    calendar days -- and the combined P&L of stop-exit + re-entry pair."""
    trades_a = pd.read_csv(f"{OUT_DIR}/trades_{pool}_A.csv")
    trades_a["exit_date"] = pd.to_datetime(trades_a["exit_date"])
    trades_a["entry_date"] = pd.to_datetime(trades_a["entry_date"])

    stops = trades_a[trades_a["exit_reason"] == "STOP_LOSS"].copy()
    by_ticker = trades_a.groupby("ticker")

    pairs = []
    for _, stop_row in stops.iterrows():
        ticker = stop_row["ticker"]
        same_ticker = by_ticker.get_group(ticker)
        reentries = same_ticker[
            (same_ticker["entry_date"] > stop_row["exit_date"]) &
            (same_ticker["entry_date"] <= stop_row["exit_date"] + pd.Timedelta(days=30))
        ]
        if not reentries.empty:
            reentry = reentries.sort_values("entry_date").iloc[0]
            pairs.append({
                "ticker": ticker, "stop_exit_date": stop_row["exit_date"].date().isoformat(),
                "stop_pnl_gbp": stop_row["pnl_gbp"],
                "reentry_date": reentry["entry_date"].date().isoformat(),
                "reentry_exit_date": reentry["exit_date"], "reentry_pnl_gbp": reentry["pnl_gbp"],
                "reentry_exit_reason": reentry["exit_reason"],
                "combined_pnl_gbp": stop_row["pnl_gbp"] + reentry["pnl_gbp"],
                "days_to_reentry": (reentry["entry_date"] - stop_row["exit_date"]).days,
            })

    pairs_df = pd.DataFrame(pairs)
    return {
        "pool": pool,
        "n_stop_loss_exits": len(stops),
        "n_reentered_within_30d": len(pairs_df),
        "pct_reentered_within_30d": round(len(pairs_df) / len(stops) * 100, 2) if len(stops) else 0.0,
        "total_stop_pnl_of_reentered_gbp": round(pairs_df["stop_pnl_gbp"].sum(), 2) if not pairs_df.empty else 0.0,
        "total_reentry_pnl_gbp": round(pairs_df["reentry_pnl_gbp"].sum(), 2) if not pairs_df.empty else 0.0,
        "total_combined_pnl_gbp": round(pairs_df["combined_pnl_gbp"].sum(), 2) if not pairs_df.empty else 0.0,
        "n_combined_pairs_net_positive": int((pairs_df["combined_pnl_gbp"] > 0).sum()) if not pairs_df.empty else 0,
        "n_combined_pairs_net_negative": int((pairs_df["combined_pnl_gbp"] < 0).sum()) if not pairs_df.empty else 0,
    }, pairs_df


def main():
    results = {"capacity_turnover": {}, "whipsaw": {}}
    for pool in ["pool1", "pool2"]:
        ct = capacity_turnover(pool)
        results["capacity_turnover"][pool] = ct
        print(f"[{pool}] capacity turnover: {json.dumps(ct, indent=2)}", flush=True)

        ws, pairs_df = whipsaw_analysis(pool)
        results["whipsaw"][pool] = ws
        print(f"[{pool}] whipsaw: {json.dumps(ws, indent=2)}", flush=True)
        pairs_df.to_csv(f"{OUT_DIR}/whipsaw_pairs_{pool}.csv", index=False)

    with open(f"{OUT_DIR}/step4_capacity_and_whipsaw.json", "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"\nWrote step4_capacity_and_whipsaw.json", flush=True)


if __name__ == "__main__":
    main()
