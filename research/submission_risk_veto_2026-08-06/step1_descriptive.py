"""Step 1 -- descriptive characterization of a proposed submission-time
"excessive risk" veto: architecturally the same category as the
existing price-sanity check (analysis/decision.py:
is_submission_price_move_blocked()) -- a final, pre-trade due-diligence
gate applied at the moment an order is actually about to be placed
(~17 hours after the EOD decision, per the real decide-at-21:30-UTC /
submit-at-14:30-UTC-next-day gap), not a change to candidate selection
or admission ranking. Price-sanity re-checks PRICE; this investigates
re-checking VOLATILITY (and therefore stop distance, 2.0 x ATR(14)),
which can shift meaningfully overnight in a way a same-day decision
never sees.

THE BACKTEST-GAP LIMITATION, confirmed already true of price-sanity
(analysis/decision.py:492 -- "the backtest engine has no decide-then-
submit time gap at all... every engine enters a position at the SAME
moment the decision is made"): there is no live submission-time state
to check against in any backtest engine. This script does NOT attempt
to build one. Instead, per the task's own explicit instruction, it
APPROXIMATES the real gap using the closest available daily-bar proxy:
recomputing ATR/stop-distance using the ticker's own NEXT trading day's
data (close + ATR one day further into that ticker's own EWM series),
rather than the original decision day's. This is an approximation
(matching the same daily-vs-intraday granularity limitation noted
throughout tonight -- true submission happens ~1 hour into the next
session, not after its close, and cannot be reconstructed without real
intraday data), stated explicitly, not implied away.

Population: REUSED from research/realistic_stop_fill_permanent_2026-08-06/
new_default_trades_full.json -- the corrected engine's own real,
already-fidelity-verified 3,436-trade output (environment confirmed
current: same session, no code/DB change since that file was written).
Not re-run from scratch -- nothing about the engine or data has changed.

Backtest convention: purely retrospective / CONTINUOUS WALK -- this is
not a variant comparison, it is "take the real, already-admitted trade
list and ask what a next-day-recomputed volatility snapshot would have
looked like for each one," the same convention every other Step 1
tonight has used for descriptive characterization.

Era buckets: 3-way split, matching tonight's established convention.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json

import numpy as np
import pandas as pd
import yaml
from scipy import stats as sp_stats

from analysis.backtest import precompute_ticker

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

TRADES_JSON = r'c:\GitHub\investment-research-sp500\research\realistic_stop_fill_permanent_2026-08-06\new_default_trades_full.json'
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\submission_risk_veto_2026-08-06'
ATR_STOP_MULTIPLIER = float(CONFIG.get("backtest", {}).get("atr_stop_multiplier", 2.0))
VETO_PERCENTILE = 0.90  # top decile, matching this project's established percentile-band convention


def _era(entry_date: pd.Timestamp) -> str:
    y = entry_date.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    return "2020-2026_recent"


def main():
    print("=== Step 1: submission-time excessive-risk veto -- overnight ATR/stop-distance divergence ===",
          flush=True)

    with open(TRADES_JSON) as f:
        trades = json.load(f)
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    print(f"Loaded {len(df)} trades (corrected engine's own real output, reused).", flush=True)
    exit_counts = df["exit_reason"].value_counts()
    print("Exit-reason breakdown (cross-check against established corrected baseline):")
    print(exit_counts.to_string())
    assert len(df) == 3436, f"Expected 3436 trades, got {len(df)}"
    assert exit_counts.get("MAX_HOLDING", 0) == 1723, "Baseline drifted"
    print("Corrected-baseline reproduction confirmed exactly.\n", flush=True)

    df["era"] = df["entry_date"].apply(_era)

    # ------------------------------------------------------------------
    # 1. Build fresh close/ATR frames (real, unmodified precompute_ticker())
    #    for every unique traded ticker.
    # ------------------------------------------------------------------
    unique_tickers = sorted(df["ticker"].unique())
    print(f"Building precompute_ticker() frames for {len(unique_tickers)} unique traded tickers...", flush=True)
    frames = {}
    for i, ticker in enumerate(unique_tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            frames[ticker] = c["frame"]
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(unique_tickers)} done", flush=True)
    print(f"  {len(frames)}/{len(unique_tickers)} tickers have usable frames\n", flush=True)

    # ------------------------------------------------------------------
    # 2. For every trade, recompute stop distance using the NEXT trading
    #    day's data (approximating the real ~17h decide->submit gap).
    # ------------------------------------------------------------------
    records = []
    n_no_next_day = 0
    n_consistency_fail = 0
    for row in df.itertuples():
        ticker = row.ticker
        entry_date = row.entry_date
        frame = frames.get(ticker)
        if frame is None or entry_date not in frame.index:
            n_no_next_day += 1
            continue

        loc = frame.index.get_loc(entry_date)
        if loc + 1 >= len(frame.index):
            n_no_next_day += 1
            continue
        next_date = frame.index[loc + 1]

        atr_decision = frame.loc[entry_date, "atr"]
        atr_next = frame.loc[next_date, "atr"]
        price_next = frame.loc[next_date, "close"]
        if pd.isna(atr_decision) or pd.isna(atr_next) or pd.isna(price_next) or price_next <= 0:
            n_no_next_day += 1
            continue

        # Consistency check: recomputing from atr_decision/entry_price
        # must reproduce the trade's own already-recorded stop_distance_pct.
        recomputed_decision_pct = ATR_STOP_MULTIPLIER * float(atr_decision) / row.entry_price * 100.0
        if abs(recomputed_decision_pct - row.stop_distance_pct) > 0.01:
            n_consistency_fail += 1

        stop_distance_next_pct = ATR_STOP_MULTIPLIER * float(atr_next) / float(price_next) * 100.0
        delta_pct = stop_distance_next_pct - row.stop_distance_pct
        delta_ratio = stop_distance_next_pct / row.stop_distance_pct if row.stop_distance_pct else np.nan

        records.append({
            "ticker": ticker, "entry_date": entry_date.date().isoformat(),
            "exit_date": row.exit_date.date().isoformat(), "era": row.era,
            "exit_reason": row.exit_reason, "pnl_pct": row.pnl_pct * 100.0, "is_win": row.is_win,
            "holding_days": row.holding_days,
            "stop_distance_decision_pct": row.stop_distance_pct,
            "next_trading_day": next_date.date().isoformat(),
            "stop_distance_next_pct": stop_distance_next_pct,
            "delta_pct": delta_pct, "delta_ratio": delta_ratio,
            "widened": delta_pct > 0,
        })

    out_df = pd.DataFrame(records)
    out_df.to_csv(f"{OUT_DIR}/trades_with_next_day_stop_distance.csv", index=False)
    print(f"Recomputed next-day stop distance for {len(out_df)}/{len(df)} trades "
          f"({n_no_next_day} skipped for missing next-day data)", flush=True)
    print(f"Consistency check: {n_consistency_fail}/{len(out_df)} trades where the recomputed decision-day "
          f"stop_distance_pct did NOT match the trade's own recorded value (expect 0)", flush=True)
    assert n_consistency_fail == 0, "Consistency check failed -- ATR reconstruction is not faithful"
    print("  Consistency check PASSED -- ATR reconstruction is faithful to the real engine's own computation.\n",
          flush=True)

    # ------------------------------------------------------------------
    # 3. Quantify overnight divergence.
    # ------------------------------------------------------------------
    print("=== Overnight stop-distance divergence (decision-day vs. next-trading-day) ===")
    print(out_df["delta_pct"].describe().to_string())
    print(f"\n% widened (next-day stop distance > decision-day): {100*out_df['widened'].mean():.1f}%")
    print(f"% narrowed: {100*(~out_df['widened']).mean():.1f}%")
    print(f"Mean |delta_pct|: {out_df['delta_pct'].abs().mean():.3f} pp")
    print(f"Median |delta_pct|: {out_df['delta_pct'].abs().median():.3f} pp")
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"  {int(q*100)}th pct of delta_ratio (next/decision): {out_df['delta_ratio'].quantile(q):.3f}")
    out_df[["delta_pct", "delta_ratio", "widened"]].describe().to_csv(f"{OUT_DIR}/divergence_summary.csv")

    # ------------------------------------------------------------------
    # 4. Data-driven veto threshold: 90th percentile of the NEXT-DAY-
    #    RECOMPUTED stop distance, across the whole historical population
    #    (a single, fixed threshold -- same design convention as the
    #    price-sanity threshold itself, 3%, a fixed historical value, not
    #    recomputed fresh per day; stated explicitly as the interpretation
    #    chosen given the task's own "top decile of that day's
    #    distribution" phrasing could otherwise be read as a fresh
    #    per-day eligible-pool percentile -- not built here, a scope
    #    decision, not an oversight).
    # ------------------------------------------------------------------
    veto_threshold_pct = out_df["stop_distance_next_pct"].quantile(VETO_PERCENTILE)
    print(f"\n=== Veto threshold: {int(VETO_PERCENTILE*100)}th percentile of next-day-recomputed "
          f"stop_distance_pct across the whole 3,436-trade population = {veto_threshold_pct:.3f}% ===", flush=True)

    would_veto = out_df[out_df["stop_distance_next_pct"] > veto_threshold_pct]
    would_not = out_df[out_df["stop_distance_next_pct"] <= veto_threshold_pct]
    print(f"  n would-be-vetoed: {len(would_veto)} ({100*len(would_veto)/len(out_df):.1f}% of all admitted trades)")
    print(f"  n not vetoed: {len(would_not)}")

    # ------------------------------------------------------------------
    # 5. Descriptive performance of would-be-vetoed candidates vs. rest.
    # ------------------------------------------------------------------
    print("\n=== Performance: would-be-vetoed vs. rest of population ===")
    for label, sub in [("Would-be-vetoed", would_veto), ("Rest of population", would_not)]:
        print(f"  {label}: n={len(sub)}  mean_pnl_pct={sub['pnl_pct'].mean():.2f}%  "
              f"median_pnl_pct={sub['pnl_pct'].median():.2f}%  win_rate={100*sub['is_win'].mean():.1f}%")
        print(f"    exit_reason breakdown:\n{sub['exit_reason'].value_counts().to_string()}")

    u, p = sp_stats.mannwhitneyu(would_veto["pnl_pct"], would_not["pnl_pct"], alternative="two-sided")
    print(f"\n  Mann-Whitney U={u:.1f} p={p:.6f}")

    comparison = pd.DataFrame([
        {"group": "would_be_vetoed", "n": len(would_veto), "mean_pnl_pct": would_veto["pnl_pct"].mean(),
         "median_pnl_pct": would_veto["pnl_pct"].median(), "win_rate": would_veto["is_win"].mean()},
        {"group": "rest", "n": len(would_not), "mean_pnl_pct": would_not["pnl_pct"].mean(),
         "median_pnl_pct": would_not["pnl_pct"].median(), "win_rate": would_not["is_win"].mean()},
    ])
    comparison.to_csv(f"{OUT_DIR}/vetoed_vs_rest_comparison.csv", index=False)

    # ------------------------------------------------------------------
    # 6. Regime confounding.
    # ------------------------------------------------------------------
    print("\n=== Regime confounding: would-be-vetoed vs. rest, by era ===")
    era_rows = []
    for era_name in ["2007-2009_GFC", "2010-2019_bull", "2020-2026_recent"]:
        v = would_veto[would_veto["era"] == era_name]
        r = would_not[would_not["era"] == era_name]
        if len(v) < 2 or len(r) < 2:
            print(f"  {era_name}: insufficient n (vetoed={len(v)}, rest={len(r)}) -- skipped")
            continue
        uu, pp = sp_stats.mannwhitneyu(v["pnl_pct"], r["pnl_pct"], alternative="two-sided")
        print(f"  {era_name}: n_vetoed={len(v)} mean={v['pnl_pct'].mean():.2f}%  "
              f"n_rest={len(r)} mean={r['pnl_pct'].mean():.2f}%  MW p={pp:.4f}")
        era_rows.append({
            "era": era_name, "n_vetoed": len(v), "mean_pnl_vetoed": v["pnl_pct"].mean(),
            "n_rest": len(r), "mean_pnl_rest": r["pnl_pct"].mean(), "mw_p": pp,
        })
    pd.DataFrame(era_rows).to_csv(f"{OUT_DIR}/regime_decomposition.csv", index=False)

    # Also report the vetoed population's own era concentration, for the
    # same "is this a regime artifact" check every investigation tonight runs.
    print("\n  Vetoed-population era concentration:")
    print((100 * would_veto["era"].value_counts(normalize=True)).round(1).to_string())

    print("\n=== Step 1 done ===", flush=True)


if __name__ == "__main__":
    main()
