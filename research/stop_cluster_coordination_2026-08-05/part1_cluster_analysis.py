"""
Part 1 -- retrospective quantification of correlated, uncoordinated
STOP_LOSS clustering. No engine modification. Reuses the real,
unmodified full-history trade set from the momentum-horizon sibling
investigation (research/momentum_horizon_2026-08-05/all_trades_full_run.json,
3,161 trades from simulate_portfolio_compounding_mtm() via
run_full_validation(config, mark_to_market=True, live_universe=True),
continuous walk 2007-01-01 to 2026-08-03, live universe). Reproduction
of its own stated totals (3,161 trades, 1,169 STOP_LOSS) is confirmed
directly before trusting it -- see step0 output below.

Cluster definition (exact, causal, non-lookahead):

For a given window size W (trading days) and minimum cluster size N:

  1. Build the real NYSE trading-day calendar from stored ^GSPC
     price_data (2007-01-01..2026-08-03).
  2. For every STOP_LOSS trade i (sorted by exit_date), compute
     trailing_count_i = the number of STOP_LOSS trades (including i
     itself) whose exit_date falls in the trailing window of W
     trading days ending at trade i's own exit_date (inclusive):
         window_i = [trading_day_index(exit_date_i) - (W-1),
                     trading_day_index(exit_date_i)]
     This uses only information available up to and including trade
     i's own exit -- genuinely causal, no lookahead.
  3. Flag every trade i where trailing_count_i >= N. This is the
     first, non-lookahead moment a real system could have recognized
     "N stops have now fired within the last W trading days."
  4. Merge the trailing windows of all flagged trades that overlap or
     touch into maximal cluster spans (standard interval union).
  5. A cluster's member trades = every STOP_LOSS trade (flagged or
     not) whose exit_date falls inside the merged span.
  6. A cluster's detection moment = the exit_date of the chronologically
     EARLIEST flagged trade in the span (i.e. the exact trading day the
     Nth stop within a trailing W-day window first fired).
  7. A cluster is DISCARDED (not counted as evidence of the "gradual
     decline, no crisis override" gap) if analysis.crisis.compute_raw_trigger()
     is True on any trading day in [merged_span_start, merged_span_end]
     -- a cluster overlapping a real crisis day is the already-handled,
     already-coordinated case.

Hypothetical coordinated-exit cost (per cluster, primary W/N only):
  - Detection moment = as defined in step 6 above.
  - For a member trade whose real exit_date <= detection moment: the
    real exit stands unchanged (no lookahead -- you cannot un-fire an
    already-triggered stop).
  - For a member trade whose real exit_date > detection moment: only
    if its entry_date <= detection moment (the position was genuinely
    open at the detection moment -- otherwise it wasn't there yet to
    be force-closed, and the real exit stands instead, flagged
    separately): compute a hypothetical exit at the REAL stored close
    price of that ticker on the detection-moment trading day (or, if
    that ticker has no bar that exact day, the nearest later trading
    day price_data actually has, capped to the trade's own real exit
    date), instead of the later, worse price its own stop eventually
    fired at.
        hyp_pnl_pct = hyp_exit_price / entry_price - 1
        hyp_pnl_gbp = position_gbp * hyp_pnl_pct
"""
import json
import sys
from pathlib import Path
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data.database import get_engine
from analysis.crisis import compute_raw_trigger

OUT_DIR = Path(__file__).resolve().parent
TRADES_PATH = Path(__file__).resolve().parent.parent / "momentum_horizon_2026-08-05" / "all_trades_full_run.json"

WINDOWS = [3, 5, 10]
MIN_SIZES = [2, 3, 4]


def log(msg):
    print(msg)
    with open(OUT_DIR / "part1_run.log", "a") as f:
        f.write(msg + "\n")


def step0_load_and_confirm():
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    n_total = len(df)
    counts = df["exit_reason"].value_counts().to_dict()
    log(f"[step0] Loaded {n_total} trades from {TRADES_PATH.name}")
    log(f"[step0] exit_reason counts: {counts}")
    assert n_total == 3161, f"Expected 3161 trades, got {n_total}"
    assert counts.get("STOP_LOSS") == 1169, f"Expected 1169 STOP_LOSS, got {counts.get('STOP_LOSS')}"
    log("[step0] CONFIRMED: reproduces stated totals (3161 trades, 1169 STOP_LOSS). "
        "Using this trade set directly -- no fresh run_full_validation() needed.")
    return df


def load_trading_calendar():
    """Real NYSE trading-day calendar from stored ^GSPC price_data."""
    engine = get_engine()
    gspc = pd.read_sql(
        "SELECT date, close FROM price_data WHERE ticker = '^GSPC' ORDER BY date",
        engine,
        parse_dates=["date"],
    )
    gspc = gspc.drop_duplicates(subset="date").set_index("date").sort_index()
    log(f"[calendar] ^GSPC trading calendar: {gspc.index.min()} -> {gspc.index.max()}, {len(gspc)} days")
    return gspc


def load_all_price_data():
    engine = get_engine()
    px = pd.read_sql("SELECT ticker, date, close FROM price_data", engine, parse_dates=["date"])
    px = px.drop_duplicates(subset=["ticker", "date"])
    log(f"[prices] Loaded {len(px)} price_data rows for {px['ticker'].nunique()} tickers")
    return px


def find_clusters(stop_df: pd.DataFrame, calendar: pd.DatetimeIndex, W: int, N: int):
    """Return list of cluster dicts for this (W, N) combination.
    stop_df must have a 'day_idx' column (trading-day integer index of exit_date)."""
    sdf = stop_df.sort_values("day_idx").reset_index(drop=True)
    day_idx = sdf["day_idx"].values

    # trailing_count_i: count of STOP_LOSS trades with day_idx in [day_idx_i-(W-1), day_idx_i]
    trailing_counts = np.zeros(len(sdf), dtype=int)
    windows = []
    for i in range(len(sdf)):
        lo = day_idx[i] - (W - 1)
        hi = day_idx[i]
        cnt = int(np.sum((day_idx >= lo) & (day_idx <= hi)))
        trailing_counts[i] = cnt
        windows.append((lo, hi))

    flagged = trailing_counts >= N
    if not flagged.any():
        return []

    # merge overlapping/touching flagged windows into spans
    flagged_windows = [w for w, f in zip(windows, flagged) if f]
    flagged_windows.sort()
    merged = []
    cur_lo, cur_hi = flagged_windows[0]
    for lo, hi in flagged_windows[1:]:
        if lo <= cur_hi + 1:  # overlap or touch
            cur_hi = max(cur_hi, hi)
        else:
            merged.append((cur_lo, cur_hi))
            cur_lo, cur_hi = lo, hi
    merged.append((cur_lo, cur_hi))

    clusters = []
    for span_lo, span_hi in merged:
        members_mask = (day_idx >= span_lo) & (day_idx <= span_hi)
        member_rows = sdf[members_mask].copy()
        # detection moment = earliest flagged trade's exit_date within this span
        flagged_in_span = sdf[members_mask & flagged]
        detection_idx = int(flagged_in_span["day_idx"].min())
        clusters.append({
            "W": W, "N": N,
            "span_lo_idx": span_lo, "span_hi_idx": span_hi,
            "detection_day_idx": detection_idx,
            "members": member_rows,
        })
    return clusters


def main():
    (OUT_DIR / "part1_run.log").write_text("")
    log("=== Part 1: STOP_LOSS cluster analysis ===")

    df = step0_load_and_confirm()
    calendar = load_trading_calendar()
    trading_days = calendar.index  # DatetimeIndex, sorted
    day_pos = {d: i for i, d in enumerate(trading_days)}

    px = load_all_price_data()
    px_idx = px.set_index(["ticker", "date"]).sort_index()

    raw_trigger = compute_raw_trigger(calendar["close"])
    crisis_days = set(calendar.index[raw_trigger.values])
    log(f"[crisis] {len(crisis_days)} raw crisis-trigger days found in full history: "
        f"{sorted(d.date().isoformat() for d in crisis_days)}")

    stop_df = df[df["exit_reason"] == "STOP_LOSS"].copy()
    log(f"[step1] {len(stop_df)} STOP_LOSS trades total")

    # map exit_date -> trading day index; exit_date should always be a real trading day
    missing = [d for d in stop_df["exit_date"].unique() if d not in day_pos]
    if missing:
        log(f"[WARN] {len(missing)} exit dates not found in trading calendar (nearest-match fallback): {missing[:10]}")
        # fallback: map to nearest trading day <= exit_date
        for d in missing:
            candidates = trading_days[trading_days <= d]
            if len(candidates):
                day_pos[d] = day_pos[candidates[-1]]
            else:
                day_pos[d] = 0
    stop_df["day_idx"] = stop_df["exit_date"].map(day_pos)

    # ------------------------------------------------------------------
    # Sensitivity table across all (W, N)
    # ------------------------------------------------------------------
    sensitivity_rows = []
    all_clusters_by_combo = {}
    for W in WINDOWS:
        for N in MIN_SIZES:
            clusters = find_clusters(stop_df, trading_days, W, N)
            n_raw = len(clusters)
            # exclude crisis-overlapping clusters
            kept = []
            excluded_crisis = 0
            for c in clusters:
                lo_date = trading_days[max(c["span_lo_idx"], 0)]
                hi_date = trading_days[min(c["span_hi_idx"], len(trading_days) - 1)]
                span_dates = trading_days[(trading_days >= lo_date) & (trading_days <= hi_date)]
                if any(d in crisis_days for d in span_dates):
                    excluded_crisis += 1
                else:
                    kept.append(c)
            all_clusters_by_combo[(W, N)] = kept
            total_trades_in_kept = sum(len(c["members"]) for c in kept)
            sensitivity_rows.append({
                "W": W, "N": N,
                "raw_clusters": n_raw,
                "crisis_excluded": excluded_crisis,
                "genuine_clusters": len(kept),
                "trades_in_genuine_clusters": total_trades_in_kept,
            })
            log(f"[sensitivity] W={W} N={N}: raw={n_raw} crisis_excluded={excluded_crisis} "
                f"genuine={len(kept)} trades_involved={total_trades_in_kept}")

    sens_df = pd.DataFrame(sensitivity_rows)
    sens_df.to_csv(OUT_DIR / "sensitivity_table.csv", index=False)
    log("\n[sensitivity table]\n" + sens_df.to_string(index=False))

    # ------------------------------------------------------------------
    # Pick primary combination
    # ------------------------------------------------------------------
    PRIMARY_W, PRIMARY_N = 3, 3
    log(f"\n[primary] Selected primary combination: W={PRIMARY_W}, N={PRIMARY_N}")
    primary_clusters = all_clusters_by_combo[(PRIMARY_W, PRIMARY_N)]
    log(f"[primary] {len(primary_clusters)} genuine (non-crisis) clusters found")

    # ------------------------------------------------------------------
    # Per-cluster real vs hypothetical analysis (primary combo)
    # ------------------------------------------------------------------
    daily_panel = pd.read_csv(
        Path(__file__).resolve().parent.parent / "concentration_risk_2026-08-05" / "results" / "daily_panel.csv",
        parse_dates=["date"],
    ).set_index("date")

    def get_price(ticker, date):
        """Real stored close for ticker on date, or nearest later trading day if missing."""
        try:
            sub = px_idx.loc[ticker]
        except KeyError:
            return None
        if date in sub.index:
            return float(sub.loc[date, "close"])
        later = sub.index[sub.index >= date]
        if len(later):
            return float(sub.loc[later[0], "close"])
        return None

    cluster_results = []
    for ci, c in enumerate(primary_clusters):
        detection_date = trading_days[c["detection_day_idx"]]
        members = c["members"].copy()
        real_total = 0.0
        hyp_total = 0.0
        entry_value_total = 0.0
        row_details = []
        for _, m in members.iterrows():
            real_total += m["pnl_gbp"]
            entry_value_total += m["position_gbp"]
            if m["exit_date"] <= detection_date:
                hyp_pnl = m["pnl_gbp"]
                mode = "real_exit_stands (exited at/before detection)"
            elif m["entry_date"] > detection_date:
                hyp_pnl = m["pnl_gbp"]
                mode = "real_exit_stands (not yet open at detection)"
            else:
                hyp_price = get_price(m["ticker"], detection_date)
                if hyp_price is None:
                    hyp_pnl = m["pnl_gbp"]
                    mode = "real_exit_stands (no price data at detection date)"
                else:
                    hyp_pct = hyp_price / m["entry_price"] - 1.0
                    hyp_pnl = m["position_gbp"] * hyp_pct
                    mode = "hypothetical_forced_close_at_detection"
            hyp_total += hyp_pnl
            row_details.append({
                "ticker": m["ticker"], "entry_date": m["entry_date"].date().isoformat(),
                "exit_date": m["exit_date"].date().isoformat(),
                "entry_price": m["entry_price"], "exit_price": m["exit_price"],
                "position_gbp": m["position_gbp"], "real_pnl_gbp": m["pnl_gbp"],
                "hyp_pnl_gbp": hyp_pnl, "mode": mode,
            })

        n_open_at_detection = None
        if detection_date in daily_panel.index:
            n_open_at_detection = int(daily_panel.loc[detection_date, "n_open"])

        cluster_results.append({
            "cluster_id": ci,
            "W": PRIMARY_W, "N": PRIMARY_N,
            "detection_date": detection_date.date().isoformat(),
            "span_start": trading_days[c["span_lo_idx"]].date().isoformat(),
            "span_end": trading_days[c["span_hi_idx"]].date().isoformat(),
            "n_members": len(members),
            "tickers": members["ticker"].tolist(),
            "real_total_pnl_gbp": real_total,
            "hyp_total_pnl_gbp": hyp_total,
            "delta_gbp": hyp_total - real_total,
            "entry_value_total_gbp": entry_value_total,
            "delta_pct_of_entry_value": (hyp_total - real_total) / entry_value_total * 100 if entry_value_total else None,
            "n_open_at_detection": n_open_at_detection,
            "pct_of_open_book": len(members) / n_open_at_detection * 100 if n_open_at_detection else None,
            "members_detail": row_details,
        })

    with open(OUT_DIR / "primary_clusters_detail.json", "w") as f:
        json.dump(cluster_results, f, indent=2, default=str)

    # summary table (without members_detail, for readability)
    summary_rows = [{k: v for k, v in c.items() if k != "members_detail"} for c in cluster_results]
    summary_df = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        summary_df["tickers"] = summary_df["tickers"].apply(lambda ts: ",".join(ts))
    summary_df.to_csv(OUT_DIR / "primary_clusters_summary.csv", index=False)

    log(f"\n[primary summary]\n{summary_df.to_string(index=False) if not summary_df.empty else 'NO CLUSTERS FOUND'}")

    if not summary_df.empty:
        agg_real = summary_df["real_total_pnl_gbp"].sum()
        agg_hyp = summary_df["hyp_total_pnl_gbp"].sum()
        agg_entry = summary_df["entry_value_total_gbp"].sum()
        log(f"\n[AGGREGATE, primary W={PRIMARY_W} N={PRIMARY_N}, {len(summary_df)} clusters, "
            f"{summary_df['n_members'].sum()} member trades]")
        log(f"  Real total P&L:        £{agg_real:,.2f}")
        log(f"  Hypothetical total P&L: £{agg_hyp:,.2f}")
        log(f"  Delta (hyp - real):     £{agg_hyp - agg_real:,.2f}")
        log(f"  Delta as % of combined entry value: {(agg_hyp - agg_real) / agg_entry * 100:.3f}%")
        log(f"  Mean n_open_at_detection: {summary_df['n_open_at_detection'].mean():.2f}")
        log(f"  Mean pct_of_open_book: {summary_df['pct_of_open_book'].mean():.2f}%")

        # regime breakdown -- by year
        summary_df["detection_year"] = pd.to_datetime(summary_df["detection_date"]).dt.year
        by_year = summary_df.groupby("detection_year").agg(
            n_clusters=("cluster_id", "count"),
            total_members=("n_members", "sum"),
            delta_gbp=("delta_gbp", "sum"),
        )
        log(f"\n[regime breakdown by year]\n{by_year.to_string()}")
        by_year.to_csv(OUT_DIR / "primary_clusters_by_year.csv")

    log("\n=== Part 1 complete ===")


if __name__ == "__main__":
    main()
