"""Step 1 -- does the wide-stop-distance-vs-outcome pattern found in the
prior submission_risk_veto_2026-08-06 investigation (real and
significant only within the 2007-2009 GFC era, absent/reversed
elsewhere) hold under a genuinely REAL-TIME-COMPUTABLE stress
condition, rather than a hindsight "was this the GFC era" label a live
system could never know?

STRESS INDICATOR, stated explicitly and reasoned, not an open search:
S&P 500 trailing drawdown from its own 60-day rolling high.

    rolling_60d_high(t) = gspc_close.rolling(60).max()  -- no lookahead,
                           genuinely computable in real time
    drawdown_pct(t)     = (gspc_close(t) / rolling_60d_high(t) - 1) * 100
    STRESS(t)            = drawdown_pct(t) <= -10.0   (>=10% below the
                           trailing 60-day high)

Directly related to the existing crisis override's own S&P-decline
logic (analysis/crisis.py: a single-day >=7% drop) but captures a
SUSTAINED stressed period rather than requiring one extreme single day
-- exactly the gap between "the market crashed today" and "the market
has been under real stress for a while," which a single-day trigger by
construction cannot see. 60 trading days ~= one calendar quarter, a
standard drawdown-measurement window; -10% is the conventional
"correction" threshold distinct from a full bear market (-20%).

REDUNDANCY CHECK: the existing crisis override (analysis/crisis.py:
extend_trigger(), a single >=7% day plus a 5-trading-day cooldown)
already blocks ALL new entries on its own trigger days. This script
measures directly how much of the new STRESS window actually falls
OUTSIDE days the crisis override already covers -- the genuinely new
ground, not assumed either way.

CANDIDATE-SPECIFICITY, confirmed structurally, not just described: this
is NOT a blanket gate. STRESS(t) alone does not veto anything -- the
combination checked below is STRESS(t) AND (that specific candidate's
own next-day-recomputed stop distance > the already-established fixed
threshold). A normal-volatility candidate on a stress day is not
touched by this check.

Population and next-day-recomputed stop distance: REUSED directly from
research/submission_risk_veto_2026-08-06/trades_with_next_day_stop_
distance.csv -- the prior investigation's own already-validated data
(3,424 trades, ATR reconstruction consistency-checked 0 mismatches
there). Not recomputed here. Veto threshold reused unchanged from that
same investigation (90th percentile of next-day-recomputed
stop_distance_pct, whole-population, fixed = 10.95%) -- not re-tuned
for this specific slice, to avoid data-snooping a new threshold to fit
this narrower question.

Backtest convention: purely retrospective / CONTINUOUS WALK -- not a
variant comparison, "take the real, already-computed data and ask
whether a genuinely computable stress condition changes the picture."
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from analysis import crisis
from data.ingestion import get_price_df

TRADES_CSV = r'c:\GitHub\investment-research-sp500\research\submission_risk_veto_2026-08-06\trades_with_next_day_stop_distance.csv'
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\crisis_conditional_veto_2026-08-06'
DRAWDOWN_WINDOW = 60
DRAWDOWN_THRESHOLD_PCT = -10.0
VETO_THRESHOLD_PCT = 10.950  # reused unchanged from the prior investigation


def main():
    print("=== Step 1: crisis-conditional volatility veto -- real-time-computable stress test ===", flush=True)

    df = pd.read_csv(TRADES_CSV)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["next_trading_day"] = pd.to_datetime(df["next_trading_day"])
    print(f"Loaded {len(df)} trades with next-day-recomputed stop distance (reused, already validated).",
          flush=True)

    # ------------------------------------------------------------------
    # 1. Build the stress indicator -- full S&P 500 history, no lookahead.
    # ------------------------------------------------------------------
    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close = gspc_df["close"].astype(float).sort_index()

    rolling_high = gspc_close.rolling(DRAWDOWN_WINDOW, min_periods=DRAWDOWN_WINDOW).max()
    drawdown_pct = (gspc_close / rolling_high - 1.0) * 100.0
    stress_flag = drawdown_pct <= DRAWDOWN_THRESHOLD_PCT

    print(f"\nStress indicator: >= {abs(DRAWDOWN_THRESHOLD_PCT):.0f}% below the trailing "
          f"{DRAWDOWN_WINDOW}-day S&P 500 high.", flush=True)
    print(f"  Stress-flagged trading days: {int(stress_flag.sum())}/{len(stress_flag)} "
          f"({100*stress_flag.mean():.1f}% of all trading days 2007-2026)", flush=True)

    # ------------------------------------------------------------------
    # 2. Existing crisis-override blocking window, for the redundancy check.
    # ------------------------------------------------------------------
    raw_trigger = crisis.compute_raw_trigger(gspc_close)
    entry_blocked = crisis.extend_trigger(raw_trigger)
    print(f"  Existing crisis-override blocked days: {int(entry_blocked.sum())}/{len(entry_blocked)} "
          f"({100*entry_blocked.mean():.2f}% of all trading days) -- raw trigger days: {int(raw_trigger.sum())}",
          flush=True)

    # ------------------------------------------------------------------
    # 3. Redundancy: overlap between the new stress window and the
    #    existing crisis-override window.
    # ------------------------------------------------------------------
    common_idx = stress_flag.index.intersection(entry_blocked.index)
    stress_aligned = stress_flag.reindex(common_idx).fillna(False)
    blocked_aligned = entry_blocked.reindex(common_idx).fillna(False)

    n_stress = int(stress_aligned.sum())
    n_blocked = int(blocked_aligned.sum())
    n_both = int((stress_aligned & blocked_aligned).sum())
    n_stress_only = int((stress_aligned & ~blocked_aligned).sum())
    n_blocked_only = int((~stress_aligned & blocked_aligned).sum())

    print(f"\n=== Redundancy check: stress window vs. existing crisis-override window ===")
    print(f"  Stress-flagged days: {n_stress}")
    print(f"  Crisis-override-blocked days: {n_blocked}")
    print(f"  Days flagged by BOTH: {n_both}")
    print(f"  Days flagged by stress indicator ONLY (genuinely new ground, not already blocked): "
          f"{n_stress_only} ({100*n_stress_only/n_stress:.1f}% of all stress-flagged days)" if n_stress else "N/A")
    print(f"  Days blocked by crisis override but NOT stress-flagged: {n_blocked_only}")
    print(f"  -> Of the crisis override's own blocked days, {100*n_both/n_blocked:.1f}% are ALSO stress-flagged "
          f"(the crisis override sits inside the broader stress window, as expected by construction)"
          if n_blocked else "N/A")

    # ------------------------------------------------------------------
    # 4. Attach stress flag (at each trade's own next_trading_day, matching
    #    the point in time the stop-distance recomputation itself uses) and
    #    the existing crisis-blocked flag, to every trade.
    # ------------------------------------------------------------------
    df["stress_at_next_day"] = df["next_trading_day"].map(stress_flag).fillna(False).astype(bool)
    df["crisis_blocked_at_next_day"] = df["next_trading_day"].map(entry_blocked).fillna(False).astype(bool)
    df["would_be_vetoed_blanket"] = df["stop_distance_next_pct"] > VETO_THRESHOLD_PCT
    # The actual candidate-specific mechanism under test: BOTH conditions.
    df["would_be_vetoed_stress_conditional"] = df["stress_at_next_day"] & df["would_be_vetoed_blanket"]

    df.to_csv(f"{OUT_DIR}/trades_with_stress_flag.csv", index=False)

    n_trades_stress = int(df["stress_at_next_day"].sum())
    print(f"\nOf {len(df)} admitted trades, {n_trades_stress} "
          f"({100*n_trades_stress/len(df):.1f}%) had their next-trading-day fall inside a stress-flagged period.",
          flush=True)
    n_stress_conditional_veto = int(df["would_be_vetoed_stress_conditional"].sum())
    n_blanket_veto = int(df["would_be_vetoed_blanket"].sum())
    print(f"Blanket veto (stop-distance alone, from the prior investigation): {n_blanket_veto} trades "
          f"({100*n_blanket_veto/len(df):.1f}%)", flush=True)
    print(f"Stress-CONDITIONAL veto (stress AND excessive stop distance): {n_stress_conditional_veto} trades "
          f"({100*n_stress_conditional_veto/len(df):.1f}%) -- confirms this is a narrower, candidate-specific "
          f"subset of the blanket veto, not a blanket gate itself.", flush=True)

    # Confirm candidate-specificity directly: on stress-flagged next-days,
    # how many admitted trades are NOT vetoed (normal stop distance)?
    stress_days_trades = df[df["stress_at_next_day"]]
    n_not_vetoed_on_stress_days = int((~stress_days_trades["would_be_vetoed_blanket"]).sum())
    print(f"  Of the {len(stress_days_trades)} trades whose next-day fell in a stress window, "
          f"{n_not_vetoed_on_stress_days} ({100*n_not_vetoed_on_stress_days/len(stress_days_trades):.1f}%) "
          f"had a NORMAL stop distance and would NOT be touched -- confirms candidate-specificity.", flush=True)

    # ------------------------------------------------------------------
    # 5. Re-examine the wide-stop-distance-vs-outcome pattern SPECIFICALLY
    #    within the stress-flagged subpopulation (the actual computable
    #    condition, not a hindsight "was this the GFC era" label).
    # ------------------------------------------------------------------
    print("\n=== Performance within the STRESS-FLAGGED subpopulation only ===")
    stress_pop = df[df["stress_at_next_day"]]
    veto_in_stress = stress_pop[stress_pop["would_be_vetoed_blanket"]]
    rest_in_stress = stress_pop[~stress_pop["would_be_vetoed_blanket"]]

    print(f"  n stress-flagged trades total: {len(stress_pop)}")
    print(f"  n would-be-vetoed within stress: {len(veto_in_stress)}  "
          f"n rest within stress: {len(rest_in_stress)}")

    if len(veto_in_stress) >= 2 and len(rest_in_stress) >= 2:
        print(f"  mean pnl_pct, vetoed-within-stress: {veto_in_stress['pnl_pct'].mean():.2f}%  "
              f"median: {veto_in_stress['pnl_pct'].median():.2f}%  "
              f"win_rate: {100*veto_in_stress['is_win'].mean():.1f}%")
        print(f"  mean pnl_pct, rest-within-stress:   {rest_in_stress['pnl_pct'].mean():.2f}%  "
              f"median: {rest_in_stress['pnl_pct'].median():.2f}%  "
              f"win_rate: {100*rest_in_stress['is_win'].mean():.1f}%")
        u, p = sp_stats.mannwhitneyu(veto_in_stress["pnl_pct"], rest_in_stress["pnl_pct"], alternative="two-sided")
        print(f"  Mann-Whitney U={u:.1f} p={p:.6f}")

        print(f"\n  exit_reason breakdown, vetoed-within-stress:\n"
              f"{veto_in_stress['exit_reason'].value_counts().to_string()}")

        stress_comparison = pd.DataFrame([
            {"group": "vetoed_within_stress", "n": len(veto_in_stress),
             "mean_pnl_pct": veto_in_stress["pnl_pct"].mean(), "median_pnl_pct": veto_in_stress["pnl_pct"].median(),
             "win_rate": veto_in_stress["is_win"].mean()},
            {"group": "rest_within_stress", "n": len(rest_in_stress),
             "mean_pnl_pct": rest_in_stress["pnl_pct"].mean(), "median_pnl_pct": rest_in_stress["pnl_pct"].median(),
             "win_rate": rest_in_stress["is_win"].mean()},
        ])
        stress_comparison.to_csv(f"{OUT_DIR}/stress_conditional_comparison.csv", index=False)
    else:
        print("  Insufficient sample for a group comparison.")

    # Comparison for context: performance for the SAME veto condition
    # OUTSIDE stress periods (i.e. wide stop distance, but calm market).
    print("\n=== For context: wide-stop-distance trades OUTSIDE stress periods ===")
    non_stress_pop = df[~df["stress_at_next_day"]]
    veto_non_stress = non_stress_pop[non_stress_pop["would_be_vetoed_blanket"]]
    rest_non_stress = non_stress_pop[~non_stress_pop["would_be_vetoed_blanket"]]
    if len(veto_non_stress) >= 2 and len(rest_non_stress) >= 2:
        print(f"  n vetoed (non-stress): {len(veto_non_stress)}  mean pnl_pct: {veto_non_stress['pnl_pct'].mean():.2f}%")
        print(f"  n rest (non-stress): {len(rest_non_stress)}  mean pnl_pct: {rest_non_stress['pnl_pct'].mean():.2f}%")
        u2, p2 = sp_stats.mannwhitneyu(veto_non_stress["pnl_pct"], rest_non_stress["pnl_pct"], alternative="two-sided")
        print(f"  Mann-Whitney U={u2:.1f} p={p2:.6f}")

    # ------------------------------------------------------------------
    # 6. Robustness: sample size already shown above; outlier sensitivity
    #    (trim top/bottom 1% of pnl_pct within the stress-flagged pop).
    # ------------------------------------------------------------------
    print("\n=== Outlier robustness (trim top/bottom 1% of pnl_pct within stress-flagged population) ===")
    if len(stress_pop) >= 20:
        lo, hi = stress_pop["pnl_pct"].quantile(0.01), stress_pop["pnl_pct"].quantile(0.99)
        trimmed = stress_pop[(stress_pop["pnl_pct"] >= lo) & (stress_pop["pnl_pct"] <= hi)]
        v_t = trimmed[trimmed["would_be_vetoed_blanket"]]
        r_t = trimmed[~trimmed["would_be_vetoed_blanket"]]
        if len(v_t) >= 2 and len(r_t) >= 2:
            u3, p3 = sp_stats.mannwhitneyu(v_t["pnl_pct"], r_t["pnl_pct"], alternative="two-sided")
            print(f"  TRIMMED: n_vetoed={len(v_t)} mean={v_t['pnl_pct'].mean():.2f}%  "
                  f"n_rest={len(r_t)} mean={r_t['pnl_pct'].mean():.2f}%  MW p={p3:.6f}")
    else:
        print("  Stress-flagged population too small for a meaningful trim.")

    # ------------------------------------------------------------------
    # 7. Era distribution of the stress-flagged population (sanity check
    #    that this isn't ALSO secretly just "the GFC era" under another name).
    # ------------------------------------------------------------------
    print("\n=== Era distribution of the stress-flagged trade population ===")
    print((100 * stress_pop["era"].value_counts(normalize=True)).round(1).to_string())
    print("\n(For comparison, whole-population era distribution:)")
    print((100 * df["era"].value_counts(normalize=True)).round(1).to_string())

    print("\n=== Step 1 done ===", flush=True)


if __name__ == "__main__":
    main()
