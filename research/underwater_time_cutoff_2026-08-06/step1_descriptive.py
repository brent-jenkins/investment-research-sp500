"""Step 1 -- rigorous test of the "underwater time-cutoff" hypothesis:
exit a position early if it's still below entry after some threshold
(proposed: 15 calendar days), to "cut dead money without touching the
winners." Treated as an unverified hypothesis, not an established fact
-- the two things this script must check directly, not assume:

1. Does it actually spare winners? Among positions genuinely still
   underwater at the day-15 checkpoint, how many went on to recover to
   a real win anyway under the ACTUAL system? If a meaningful share
   recovers, "without touching the winners" is false, and the true
   benefit of the rule must net the cost of prematurely cutting those
   recoveries against the benefit of cutting the ones that lose anyway.
2. Is this a genuinely NEW, additive mechanism, or mostly redundant
   with the existing ATR-based stop-loss, just slower? For the specific
   "sat 20+ days before eventually being stopped out" population, how
   close was price to the existing stop level already at day 15?

Population and fields: REUSED from research/full_trade_export_enriched_
2026-08-06/full_trade_export_enriched_2007-2026.csv -- direct,
already-fidelity-verified engine fields only (ticker, entry_date,
exit_date, entry_price, exit_price, pnl_pct, pnl_gbp, exit_reason,
holding_days, stop_distance_pct, position_gbp_at_entry). Reused rather
than re-running the backtest because the environment is confirmed
unchanged since that export ran earlier this session (git status/latest
commit/price_data coverage all re-checked before starting this task).

Fresh, independent data needed beyond the trade list itself: each
trade's own DAILY closing price path from entry to the day-15
checkpoint (to determine whether it was actually still underwater at
day 15, and at what price) -- built here directly from stored
price_data via analysis.backtest.precompute_ticker() (the real,
unmodified production function), for exactly the ~331 tickers that
appear in the trade list.

Backtest convention: purely retrospective / CONTINUOUS WALK -- this is
not a variant comparison, it is "take the real, already-generated trade
list and ask what a different EXIT RULE would have done to each trade
already admitted," the same convention every other Step 1 tonight has
used for descriptive characterization.

Era buckets: 3-way split, matching tonight's established convention
(2007-2009 GFC / 2010-2019 bull / 2020-2026 recent).

THRESHOLD_DAY = 15 calendar days, matching holding_days' own definition
in the real engine: holding_days = (dt - entry_date).days (calendar
days, not trading days) -- see analysis/backtest.py's
simulate_portfolio_compounding_mtm(). The "day-15 checkpoint" for a
given trade is the first trading day dt in that ticker's own price
series where (dt - entry_date).days >= 15.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd
import yaml
from scipy import stats as sp_stats

from analysis.backtest import precompute_ticker

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

TRADES_CSV = r'c:\GitHub\investment-research-sp500\research\full_trade_export_enriched_2026-08-06\full_trade_export_enriched_2007-2026.csv'
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\underwater_time_cutoff_2026-08-06'
THRESHOLD_DAY = 15


def _era(entry_date: pd.Timestamp) -> str:
    y = entry_date.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def main():
    print("=== Step 1: underwater time-cutoff (day-15) -- descriptive test ===", flush=True)

    df = pd.read_csv(TRADES_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    print(f"Loaded {len(df)} trades (direct engine fields only).", flush=True)
    exit_counts = df["exit_reason"].value_counts()
    print("Exit-reason breakdown (cross-check against established baseline):")
    print(exit_counts.to_string())
    assert len(df) == 3161, f"Expected 3161 trades, got {len(df)}"
    assert exit_counts.get("MAX_HOLDING", 0) == 1796, "MAX_HOLDING count drifted from baseline"
    print("Baseline reproduction confirmed exactly.\n", flush=True)

    df["era"] = df["entry_date"].apply(_era)
    # stop_price for a LONG position: entry_price * (1 - stop_distance_pct/100)
    # -- exact inverse of analysis/volatility.py:_resolve_entry_stop()'s own
    # stop = price - distance_frac*price, distance_frac = stop_distance_pct/100.
    df["stop_price"] = df["entry_price"] * (1.0 - df["stop_distance_pct"] / 100.0)

    # ------------------------------------------------------------------
    # Cross-check the motivating claim's own headline count: "284
    # historical trades sat for 20+ days before eventually being stopped
    # out."
    # ------------------------------------------------------------------
    slow_bleed = df[(df["exit_reason"] == "STOP_LOSS") & (df["holding_days"] >= 20)]
    print(f"STOP_LOSS trades with holding_days >= 20: {len(slow_bleed)} "
          f"(motivating claim's own figure: 284)", flush=True)

    # ------------------------------------------------------------------
    # Build fresh close-price frames for every unique traded ticker (the
    # real, unmodified precompute_ticker(), for exactly the tickers that
    # appear in this trade list).
    # ------------------------------------------------------------------
    unique_tickers = sorted(df["ticker"].unique())
    print(f"\nBuilding price frames for {len(unique_tickers)} unique traded tickers...", flush=True)
    frames = {}
    for i, ticker in enumerate(unique_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            frames[ticker] = c["frame"]
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(unique_tickers)} done", flush=True)
    print(f"  {len(frames)}/{len(unique_tickers)} tickers have usable frames\n", flush=True)

    # ------------------------------------------------------------------
    # For every trade with holding_days >= THRESHOLD_DAY (i.e. the
    # position was still genuinely open, under the REAL system, at the
    # day-15 mark -- otherwise the rule never gets a chance to fire),
    # find the day-15 checkpoint price and pnl.
    # ------------------------------------------------------------------
    print(f"Locating day-{THRESHOLD_DAY} checkpoint for every eligible trade...", flush=True)
    records = []
    n_resolved_before_threshold = 0
    n_no_frame = 0
    for row in df.itertuples():
        if row.holding_days < THRESHOLD_DAY:
            n_resolved_before_threshold += 1
            continue
        frame = frames.get(row.ticker)
        if frame is None or row.entry_date not in frame.index:
            n_no_frame += 1
            continue
        close = frame["close"].astype(float)
        idx_after_entry = close.loc[close.index >= row.entry_date]
        # first trading day where the CALENDAR gap from entry >= THRESHOLD_DAY
        gap_days = (idx_after_entry.index - row.entry_date).days
        checkpoint_mask = gap_days >= THRESHOLD_DAY
        if not checkpoint_mask.any():
            n_no_frame += 1
            continue
        checkpoint_date = idx_after_entry.index[checkpoint_mask][0]
        day15_price = float(idx_after_entry.loc[checkpoint_date])
        day15_pnl_pct = day15_price / row.entry_price - 1.0
        underwater_at_day15 = day15_pnl_pct < 0

        # hypothetical £ P&L if force-exited exactly at the day-15 checkpoint
        hypo_pnl_gbp = row.position_gbp_at_entry * day15_pnl_pct
        delta_gbp = hypo_pnl_gbp - row.pnl_gbp  # +ve: cutoff would have been BETTER; -ve: WORSE

        stop_proximity_pct = (day15_price / row.stop_price - 1.0) * 100.0  # % of room above stop remaining at day 15
        days_checkpoint_to_real_exit = (row.exit_date - checkpoint_date).days

        records.append({
            "ticker": row.ticker, "entry_date": row.entry_date, "exit_date": row.exit_date,
            "era": row.era, "exit_reason": row.exit_reason, "holding_days": row.holding_days,
            "real_pnl_pct": row.pnl_pct, "real_pnl_gbp": row.pnl_gbp, "real_is_win": row.pnl_pct > 0,
            "position_gbp_at_entry": row.position_gbp_at_entry,
            "checkpoint_date": checkpoint_date, "day15_price": day15_price,
            "day15_pnl_pct": day15_pnl_pct * 100.0, "underwater_at_day15": underwater_at_day15,
            "hypo_pnl_gbp_at_day15": hypo_pnl_gbp, "delta_gbp_vs_real": delta_gbp,
            "stop_price": row.stop_price, "stop_proximity_pct_at_day15": stop_proximity_pct,
            "days_checkpoint_to_real_exit": days_checkpoint_to_real_exit,
            "is_slow_bleed_284": (row.exit_reason == "STOP_LOSS" and row.holding_days >= 20),
        })

    out_df = pd.DataFrame(records)
    out_df.to_csv(f"{OUT_DIR}/day15_checkpoint_trades.csv", index=False)
    print(f"  {len(out_df)} trades reached the day-{THRESHOLD_DAY} checkpoint "
          f"({n_resolved_before_threshold} resolved before day {THRESHOLD_DAY}, "
          f"{n_no_frame} skipped for missing price data)", flush=True)
    print(f"  Wrote day15_checkpoint_trades.csv\n", flush=True)

    underwater = out_df[out_df["underwater_at_day15"]]
    not_underwater = out_df[~out_df["underwater_at_day15"]]
    print(f"Of {len(out_df)} trades still open at day {THRESHOLD_DAY}: "
          f"{len(underwater)} underwater ({100*len(underwater)/len(out_df):.1f}%), "
          f"{len(not_underwater)} at/above breakeven ({100*len(not_underwater)/len(out_df):.1f}%)\n", flush=True)

    # ------------------------------------------------------------------
    # CHECK 1: does the rule spare winners? Real eventual outcome of the
    # underwater-at-day15 population.
    # ------------------------------------------------------------------
    print("=== CHECK 1: real eventual outcome of underwater-at-day15 trades ===")
    real_outcome = underwater["exit_reason"].value_counts()
    print(real_outcome.to_string())
    n_recover_win = int((underwater["real_is_win"]).sum())
    n_lose_anyway = len(underwater) - n_recover_win
    print(f"\n  Eventually WIN anyway (real_pnl_pct > 0): {n_recover_win} "
          f"({100*n_recover_win/len(underwater):.1f}% of underwater-at-day15)")
    print(f"  Eventually LOSE anyway (real_pnl_pct <= 0): {n_lose_anyway} "
          f"({100*n_lose_anyway/len(underwater):.1f}% of underwater-at-day15)")
    recover_by_reason = underwater[underwater["real_is_win"]]["exit_reason"].value_counts()
    print(f"\n  Breakdown of the {n_recover_win} eventual-winners by real exit_reason:")
    print(recover_by_reason.to_string())

    check1 = pd.DataFrame([{
        "n_underwater_at_day15": len(underwater),
        "n_recover_to_win": n_recover_win, "pct_recover": 100*n_recover_win/len(underwater),
        "n_lose_anyway": n_lose_anyway, "pct_lose_anyway": 100*n_lose_anyway/len(underwater),
    }])
    check1.to_csv(f"{OUT_DIR}/check1_recovery_rate.csv", index=False)

    # ------------------------------------------------------------------
    # NET £ IMPACT of force-exiting the whole underwater-at-day15
    # population at the day-15 price, vs. what really happened.
    # ------------------------------------------------------------------
    print("\n=== NET £ IMPACT of a day-15 force-exit rule on the underwater population ===")
    total_delta = underwater["delta_gbp_vs_real"].sum()
    delta_on_winners = underwater[underwater["real_is_win"]]["delta_gbp_vs_real"].sum()
    delta_on_losers = underwater[~underwater["real_is_win"]]["delta_gbp_vs_real"].sum()
    print(f"  Total NET delta (hypo day-15-exit £ minus real £, summed): £{total_delta:,.2f}")
    print(f"    Of which, cost inflicted on eventual WINNERS (cut early): £{delta_on_winners:,.2f}")
    print(f"    Of which, benefit from eventual LOSERS (cut earlier/better): £{delta_on_losers:,.2f}")
    print(f"  (Positive = day-15 cutoff net BETTER than real system; negative = net WORSE)")

    net_impact = pd.DataFrame([{
        "total_net_delta_gbp": total_delta,
        "delta_on_eventual_winners_gbp": delta_on_winners,
        "delta_on_eventual_losers_gbp": delta_on_losers,
        "n_underwater": len(underwater),
    }])
    net_impact.to_csv(f"{OUT_DIR}/net_pnl_impact.csv", index=False)

    # ------------------------------------------------------------------
    # CHECK 2: stop-proximity for the specific 284-style slow-bleed
    # population (STOP_LOSS, holding_days>=20) at their own day-15
    # checkpoint.
    # ------------------------------------------------------------------
    print(f"\n=== CHECK 2: stop-proximity at day-15 for the slow-bleed (STOP_LOSS, holding_days>=20) population ===")
    slow_bleed_checkpointed = out_df[out_df["is_slow_bleed_284"]]
    print(f"  n={len(slow_bleed_checkpointed)} slow-bleed trades reached the day-15 checkpoint "
          f"(of {len(slow_bleed)} total STOP_LOSS/holding>=20 trades)")
    print(slow_bleed_checkpointed["stop_proximity_pct_at_day15"].describe().to_string())
    print(f"\n  days from day-15 checkpoint to the REAL eventual STOP_LOSS exit:")
    print(slow_bleed_checkpointed["days_checkpoint_to_real_exit"].describe().to_string())
    n_within_5pct_of_stop = (slow_bleed_checkpointed["stop_proximity_pct_at_day15"] <= 5.0).sum()
    n_within_10pct_of_stop = (slow_bleed_checkpointed["stop_proximity_pct_at_day15"] <= 10.0).sum()
    print(f"\n  Already within 5% of their own stop at day 15: {n_within_5pct_of_stop} "
          f"({100*n_within_5pct_of_stop/len(slow_bleed_checkpointed):.1f}%)")
    print(f"  Already within 10% of their own stop at day 15: {n_within_10pct_of_stop} "
          f"({100*n_within_10pct_of_stop/len(slow_bleed_checkpointed):.1f}%)")

    slow_bleed_checkpointed.to_csv(f"{OUT_DIR}/slow_bleed_284_stop_proximity.csv", index=False)
    check2_summary = pd.DataFrame([{
        "n_slow_bleed_checkpointed": len(slow_bleed_checkpointed),
        "mean_stop_proximity_pct": slow_bleed_checkpointed["stop_proximity_pct_at_day15"].mean(),
        "median_stop_proximity_pct": slow_bleed_checkpointed["stop_proximity_pct_at_day15"].median(),
        "mean_days_checkpoint_to_exit": slow_bleed_checkpointed["days_checkpoint_to_real_exit"].mean(),
        "median_days_checkpoint_to_exit": slow_bleed_checkpointed["days_checkpoint_to_real_exit"].median(),
        "n_within_5pct_of_stop": n_within_5pct_of_stop, "n_within_10pct_of_stop": n_within_10pct_of_stop,
    }])
    check2_summary.to_csv(f"{OUT_DIR}/check2_stop_proximity_summary.csv", index=False)

    # Attempt to reconcile the cited "£127k" figure: incremental loss
    # incurred BETWEEN the day-15 checkpoint and the real eventual
    # STOP_LOSS exit, for the slow-bleed population specifically (a
    # narrower, different quantity than the whole-population NET impact
    # above, which nets against harmed winners too).
    slow_bleed_incremental_loss = (
        slow_bleed_checkpointed["real_pnl_gbp"] - slow_bleed_checkpointed["hypo_pnl_gbp_at_day15"]
    ).sum()
    print(f"\n  Incremental £ lost BETWEEN day-15 and the real STOP_LOSS exit, "
          f"slow-bleed population only (a candidate reconciliation of the cited ~£127k): "
          f"£{-slow_bleed_incremental_loss:,.2f} (positive = extra money genuinely lost by waiting)")

    # ------------------------------------------------------------------
    # Regime confounding.
    # ------------------------------------------------------------------
    print("\n=== Regime confounding: recovery rate + net £ impact, by era ===")
    era_rows = []
    for era_name in ["2007-2009_GFC", "2010-2019_bull", "2020-2026_recent"]:
        sub = underwater[underwater["era"] == era_name]
        if sub.empty:
            continue
        n_win = int(sub["real_is_win"].sum())
        n_lose = len(sub) - n_win
        net = sub["delta_gbp_vs_real"].sum()
        print(f"  {era_name}: n_underwater={len(sub)}  recover_to_win={n_win} "
              f"({100*n_win/len(sub):.1f}%)  lose_anyway={n_lose} ({100*n_lose/len(sub):.1f}%)  "
              f"net_delta=£{net:,.2f}")
        era_rows.append({
            "era": era_name, "n_underwater": len(sub), "n_recover_win": n_win,
            "pct_recover_win": 100*n_win/len(sub), "n_lose_anyway": n_lose,
            "pct_lose_anyway": 100*n_lose/len(sub), "net_delta_gbp": net,
        })
    pd.DataFrame(era_rows).to_csv(f"{OUT_DIR}/regime_decomposition.csv", index=False)

    print("\n=== Step 1 done ===", flush=True)


if __name__ == "__main__":
    main()
