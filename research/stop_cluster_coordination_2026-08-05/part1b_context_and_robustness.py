"""
Part 1b -- context and robustness follow-up to part1_cluster_analysis.py.

1. Total strategy P&L across all 3,161 trades, to put the primary
   combination's aggregate delta_gbp in context.
2. Aggregate delta_gbp (hyp - real) and delta_pct_of_entry_value for
   EVERY (W, N) sensitivity combination, not just the chosen primary --
   confirms the finding's sign/direction is robust to the parameter
   choice, not an artifact of picking W=5, N=3 specifically.
3. A simple economic justification for why W=3,N=2 is noisier than
   W=5,N=3: expected stops-per-window under the empirical average
   STOP_LOSS daily rate (Poisson approximation), to support the
   "smallest N/W that still isolates genuine clustering from routine
   background activity" reasoning.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data.database import get_engine
from analysis.crisis import compute_raw_trigger

OUT_DIR = Path(__file__).resolve().parent
TRADES_PATH = Path(__file__).resolve().parent.parent / "momentum_horizon_2026-08-05" / "all_trades_full_run.json"

WINDOWS = [3, 5, 10]
MIN_SIZES = [2, 3, 4]


def log(msg):
    print(msg)
    with open(OUT_DIR / "part1b_run.log", "a") as f:
        f.write(msg + "\n")


def main():
    (OUT_DIR / "part1b_run.log").write_text("")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])

    total_pnl = df["pnl_gbp"].sum()
    total_entry_value = df["position_gbp"].sum()
    log(f"[context] Total strategy P&L, all {len(df)} trades, full history: £{total_pnl:,.2f}")
    log(f"[context] Total entry value (sum position_gbp), all trades: £{total_entry_value:,.2f}")

    stop_df = df[df["exit_reason"] == "STOP_LOSS"]
    stop_pnl = stop_df["pnl_gbp"].sum()
    log(f"[context] Total STOP_LOSS-only P&L, all {len(stop_df)} STOP_LOSS trades: £{stop_pnl:,.2f}")

    engine = get_engine()
    gspc = pd.read_sql(
        "SELECT date, close FROM price_data WHERE ticker = '^GSPC' ORDER BY date",
        engine, parse_dates=["date"],
    ).drop_duplicates(subset="date").set_index("date").sort_index()
    trading_days = gspc.index
    day_pos = {d: i for i, d in enumerate(trading_days)}
    n_trading_days = len(trading_days)
    daily_rate = len(stop_df) / n_trading_days
    log(f"[context] Empirical average STOP_LOSS rate: {len(stop_df)} / {n_trading_days} trading days = {daily_rate:.4f}/day")

    log("\n[Poisson null-rate sanity check -- NOT the real cluster count, "
        "a naive independence baseline for calibration only]")
    for W in WINDOWS:
        lam = daily_rate * W
        for N in MIN_SIZES:
            p_ge_n = 1 - poisson.cdf(N - 1, lam)
            log(f"  W={W:2d} N={N}: mean stops/window={lam:.3f}, "
                f"P(>=N stops in a random {W}-day window | independent Poisson)={p_ge_n:.4f}")

    # ------------------------------------------------------------------
    # Aggregate delta_gbp for ALL (W, N) combos, reusing part1's cluster
    # logic (re-imported, not re-derived, to avoid drift)
    # ------------------------------------------------------------------
    sys.path.insert(0, str(OUT_DIR))
    from part1_cluster_analysis import find_clusters, load_all_price_data

    raw_trigger = compute_raw_trigger(gspc["close"])
    crisis_days = set(gspc.index[raw_trigger.values])

    missing = [d for d in stop_df["exit_date"].unique() if d not in day_pos]
    stop_df = stop_df.copy()
    for d in missing:
        candidates = trading_days[trading_days <= d]
        day_pos[d] = day_pos[candidates[-1]] if len(candidates) else 0
    stop_df["day_idx"] = stop_df["exit_date"].map(day_pos)

    px = load_all_price_data()
    px_idx = px.set_index(["ticker", "date"]).sort_index()

    def get_price(ticker, date):
        try:
            sub = px_idx.loc[ticker]
        except KeyError:
            return None
        if date in sub.index:
            return float(sub.loc[date, "close"])
        later = sub.index[sub.index >= date]
        return float(sub.loc[later[0], "close"]) if len(later) else None

    robustness_rows = []
    for W in WINDOWS:
        for N in MIN_SIZES:
            clusters = find_clusters(stop_df, trading_days, W, N)
            kept = []
            for c in clusters:
                lo_date = trading_days[max(c["span_lo_idx"], 0)]
                hi_date = trading_days[min(c["span_hi_idx"], len(trading_days) - 1)]
                span_dates = trading_days[(trading_days >= lo_date) & (trading_days <= hi_date)]
                if not any(d in crisis_days for d in span_dates):
                    kept.append(c)

            agg_real = 0.0
            agg_hyp = 0.0
            agg_entry = 0.0
            for c in kept:
                detection_date = trading_days[c["detection_day_idx"]]
                for _, m in c["members"].iterrows():
                    agg_real += m["pnl_gbp"]
                    agg_entry += m["position_gbp"]
                    if m["exit_date"] <= detection_date or m["entry_date"] > detection_date:
                        agg_hyp += m["pnl_gbp"]
                    else:
                        hp = get_price(m["ticker"], detection_date)
                        if hp is None:
                            agg_hyp += m["pnl_gbp"]
                        else:
                            agg_hyp += m["position_gbp"] * (hp / m["entry_price"] - 1.0)

            delta = agg_hyp - agg_real
            robustness_rows.append({
                "W": W, "N": N, "n_clusters": len(kept),
                "real_total_gbp": agg_real, "hyp_total_gbp": agg_hyp,
                "delta_gbp": delta,
                "delta_pct_of_entry_value": delta / agg_entry * 100 if agg_entry else None,
                "delta_pct_of_total_strategy_pnl": delta / abs(total_pnl) * 100,
            })
            log(f"[robustness] W={W} N={N}: n_clusters={len(kept)} real=£{agg_real:,.0f} "
                f"hyp=£{agg_hyp:,.0f} delta=£{delta:,.0f} "
                f"({delta/agg_entry*100 if agg_entry else 0:.3f}% of entry value, "
                f"{delta/abs(total_pnl)*100:.3f}% of total strategy P&L)")

    rob_df = pd.DataFrame(robustness_rows)
    rob_df.to_csv(OUT_DIR / "robustness_all_combos.csv", index=False)
    log("\n[full robustness table]\n" + rob_df.to_string(index=False))


if __name__ == "__main__":
    main()
