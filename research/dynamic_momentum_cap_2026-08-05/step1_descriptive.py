"""Step 1 -- descriptive check: does the position's own momentum TREND
since entry, evaluated at/near the 30-day MAX_HOLDING mark, predict what
happens if the position were held longer?

This is deliberately different from the momentum-STRENGTH investigation
(research/momentum_horizon_2026-08-05/): that tested a static, entry-time
factor (how strong was momentum AT ENTRY). This tests an ongoing,
recalculated-while-held factor: has momentum been strengthening or
weakening SINCE ENTRY, as of the point the fixed 30-day cap would trigger
exit.

Backtest convention: CONTINUOUS WALK. Per CLAUDE.md's "Two backtest
conventions" standing requirement -- this is not a variant comparison, it
is "pull one real, already-fidelity-established trade set and look at
what happened next", purely retrospective, no look-ahead risk. Step 2 (if
warranted) uses RESET-AT-BOUNDARY instead, per the task brief.

Trade source: REUSED directly from the sibling momentum-strength
investigation's own real backtest run
(research/momentum_horizon_2026-08-05/all_trades_full_run.json) rather
than re-run from scratch. This is deliberate, not a shortcut around this
project's reproducibility standard: it is the same engine
(simulate_portfolio_compounding_mtm via run_full_validation(config,
mark_to_market=True, live_universe=True)), the same config.yaml, the same
date window (2007-01-01 to 2026-08-03), and the same code -- confirmed
directly before use: `git status --short` at the start of this task showed
no local modifications to any production file relative to the commit that
produced that trade set (e45e363, the very next commit after the sibling
run), so re-running would reproduce byte-identical trades at the cost of
several minutes of runtime for zero new information. The file itself is
verified below (trade count, exit-reason breakdown) to match the sibling
report's own stated baseline before anything new is computed on top of
it.

Engine confirmation (reproduced from the trade file, not re-asserted):
total_trades=3161, MAX_HOLDING=1796 (56.8%) -- matches both the task
brief's ~3,161-trade/56.8% baseline and the sibling report's Section 1
table exactly.

Momentum-trend-since-entry definition (exact code, not paraphrase):

    trend_since_entry(t) = momentum[t] - momentum[entry_date]

    strengthening_at_cap = momentum[exit_date] - momentum[entry_date] > 0
    decaying_at_cap      = momentum[exit_date] - momentum[entry_date] <= 0

Evaluated using each ticker's own trailing-252-trading-day momentum series
(analysis.signals.compute_momentum_series -- close/close.shift(252)-1.0,
the exact, unmodified production series), read directly from the same
precompute_ticker() frames the real engine used. This directly matches the
task's "since entry" phrasing (not the sibling script's own
`momentum_decaying_at_exit`, which compares to 20 trading days before
exit, not to entry -- a materially different question: recent slope vs.
cumulative change over the whole hold). Population: the 1796 real
MAX_HOLDING trades -- these are exactly the trades a dynamic cap
mechanism could have changed the outcome of, since every other exit
reason (STOP_LOSS/SIGNAL_OFF/CRISIS_EXIT/END_OF_DATA) already fired before
any cap-extension logic would ever get a chance to run.

Fitness-for-recalculated-daily-cadence check (not a second candidate
signal -- a validation of the one signal above): for each MAX_HOLDING
trade, the full daily trend_since_entry(t) trajectory is walked across
the whole hold, and (a) the number of times the sign flips is recorded,
(b) the fraction of the last 5 trading days before exit that agree with
the day-of-exit classification is recorded. This checks whether a
mechanism recalculating this measure every EOD cycle would see a stable
read near the cap boundary or a noisy, flip-flopping one -- directly
relevant to whether Step 2's mechanism needs a smoothing/hysteresis rule.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import time

import numpy as np
import pandas as pd
import yaml
from scipy import stats

from analysis.backtest import precompute_ticker

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

SRC_TRADES = r'c:\GitHub\investment-research-sp500\research\momentum_horizon_2026-08-05\all_trades_full_run.json'
OUT_DIR = r'c:\GitHub\investment-research-sp500\research\dynamic_momentum_cap_2026-08-05'

FWD_WINDOWS = (10, 20, 30)
STABILITY_LOOKBACK_DAYS = 5  # trading days before exit, for the flip-flop check


def era_label(entry_date: pd.Timestamp) -> str:
    """Coarse market-regime bucket, same style as the sibling report's
    era_pre2010 control, extended to cover the whole 2007-2026 span so
    every trade gets a bucket (not just a pre/post-2010 binary)."""
    y = entry_date.year
    if y < 2010:
        return "2007-2009_GFC"
    if y < 2020:
        return "2010-2019_bull"
    if y == 2020:
        return "2020_covid"
    return "2021-2026_recent"


def main():
    t0 = time.time()
    print("=== Step 1: dynamic momentum-trend-since-entry descriptive check ===", flush=True)

    # ------------------------------------------------------------------
    # 1. Load + verify the reused trade set.
    # ------------------------------------------------------------------
    with open(SRC_TRADES) as fh:
        all_trades = json.load(fh)
    trades_df = pd.DataFrame(all_trades)
    print(f"Loaded {len(trades_df)} trades from sibling investigation's real backtest run.", flush=True)
    exit_counts = trades_df["exit_reason"].value_counts()
    print("exit_reason breakdown:\n" + exit_counts.to_string(), flush=True)
    assert len(trades_df) == 3161, f"Trade count mismatch: {len(trades_df)} != 3161 baseline"
    max_hold_trades = trades_df[trades_df["exit_reason"] == "MAX_HOLDING"].copy()
    pct = 100 * len(max_hold_trades) / len(trades_df)
    print(f"MAX_HOLDING trades: {len(max_hold_trades)} ({pct:.1f}% of all trades)", flush=True)
    assert len(max_hold_trades) == 1796, f"MAX_HOLDING count mismatch: {len(max_hold_trades)} != 1796 baseline"
    assert abs(pct - 56.8) < 0.05, f"MAX_HOLDING pct mismatch: {pct:.2f} != 56.8 baseline"
    print("Baseline reproduction CONFIRMED (3161 trades, 1796 MAX_HOLDING, 56.8%).", flush=True)

    # ------------------------------------------------------------------
    # 2. Build precompute_ticker() frames only for tickers appearing in
    #    MAX_HOLDING trades (not all 503 live candidates -- faster, and
    #    this population is the only one Step 1 needs).
    # ------------------------------------------------------------------
    tickers = sorted(max_hold_trades["ticker"].unique())
    print(f"\nBuilding precompute_ticker() frames for {len(tickers)} tickers "
          f"(MAX_HOLDING population only)...", flush=True)
    frames = {}
    for i, ticker in enumerate(tickers):
        c = precompute_ticker(ticker, CONFIG)
        if c is not None:
            frames[ticker] = c["frame"]
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(tickers)} tickers cached ({time.time()-t0:.0f}s elapsed)", flush=True)
    print(f"  {len(frames)}/{len(tickers)} tickers have usable frames", flush=True)

    # ------------------------------------------------------------------
    # 3. For every MAX_HOLDING trade: trend_since_entry at cap, plus the
    #    stability/flip-flop diagnostics, plus forward returns.
    # ------------------------------------------------------------------
    print(f"\nProcessing {len(max_hold_trades)} MAX_HOLDING trades...", flush=True)
    records = []
    skipped = 0
    for idx, trade in enumerate(max_hold_trades.to_dict("records")):
        ticker = trade["ticker"]
        entry_date = pd.Timestamp(trade["entry_date"])
        exit_date = pd.Timestamp(trade["exit_date"])
        frame = frames.get(ticker)
        if frame is None or entry_date not in frame.index or exit_date not in frame.index:
            skipped += 1
            continue

        mom = frame["momentum"].astype(float)
        entry_loc = frame.index.get_loc(entry_date)
        exit_loc = frame.index.get_loc(exit_date)

        mom_at_entry = mom.iloc[entry_loc]
        mom_at_exit = mom.iloc[exit_loc]
        if pd.isna(mom_at_entry) or pd.isna(mom_at_exit):
            skipped += 1
            continue

        trend_since_entry = float(mom_at_exit - mom_at_entry)
        strengthening = trend_since_entry > 0

        # --- Stability / flip-flop diagnostics across the whole hold ---
        hold_mom = mom.iloc[entry_loc:exit_loc + 1]
        trend_traj = (hold_mom - mom_at_entry).dropna()
        sign_traj = np.sign(trend_traj.values)
        sign_traj_nz = sign_traj[sign_traj != 0]
        n_sign_flips = int(np.sum(np.diff(sign_traj_nz) != 0)) if len(sign_traj_nz) > 1 else 0

        last_n = min(STABILITY_LOOKBACK_DAYS, len(trend_traj))
        last_n_signs = np.sign(trend_traj.values[-last_n:]) if last_n > 0 else np.array([])
        cap_sign = np.sign(trend_since_entry)
        pct_last_n_agree = float(np.mean(last_n_signs == cap_sign)) if len(last_n_signs) > 0 else None

        # --- Forward returns past the actual cap exit (what happened if
        #     the position had been held longer than 30 days) ---
        close = frame["close"].astype(float)
        exit_price = float(trade["exit_price"])
        n_avail = len(close) - 1 - exit_loc
        fwd = {}
        for w in FWD_WINDOWS:
            fwd[f"fwd_ret_{w}d"] = float(close.iloc[exit_loc + w] / exit_price - 1.0) if n_avail >= w else None

        records.append({
            "ticker": ticker,
            "entry_date": entry_date.date().isoformat(),
            "exit_date": exit_date.date().isoformat(),
            "holding_days": trade["holding_days"],
            "pnl_pct_at_cap": trade["pnl_pct"],
            "momentum_at_entry": float(mom_at_entry),
            "momentum_at_cap": float(mom_at_exit),
            "trend_since_entry": trend_since_entry,
            "trend_class": "Strengthening" if strengthening else "Decaying",
            "n_sign_flips_during_hold": n_sign_flips,
            "pct_last_5d_agree_with_cap_classification": pct_last_n_agree,
            "era": era_label(entry_date),
            **fwd,
        })

        if (idx + 1) % 300 == 0:
            print(f"    {idx+1}/{len(max_hold_trades)} processed ({time.time()-t0:.0f}s elapsed)", flush=True)

    print(f"  skipped {skipped} trades (missing frame/date/momentum data)", flush=True)
    out_df = pd.DataFrame(records)
    out_df.to_csv(f"{OUT_DIR}/max_holding_trend_annotated.csv", index=False)
    print(f"\nWrote {len(out_df)} annotated trades -> max_holding_trend_annotated.csv", flush=True)

    # ------------------------------------------------------------------
    # 4. Group summary: Strengthening vs Decaying at the cap point.
    # ------------------------------------------------------------------
    print("\n=== Grouped by trend_class at the 30-day cap point ===", flush=True)
    summary_rows = []
    for cls in ["Strengthening", "Decaying"]:
        sub = out_df[out_df["trend_class"] == cls]
        row = {
            "trend_class": cls,
            "n": len(sub),
            "pct_of_max_holding": 100 * len(sub) / len(out_df),
            "mean_trend_since_entry": sub["trend_since_entry"].mean(),
            "mean_pnl_pct_at_cap": sub["pnl_pct_at_cap"].mean(),
            "mean_fwd_ret_10d": sub["fwd_ret_10d"].mean(skipna=True),
            "mean_fwd_ret_20d": sub["fwd_ret_20d"].mean(skipna=True),
            "mean_fwd_ret_30d": sub["fwd_ret_30d"].mean(skipna=True),
            "median_fwd_ret_30d": sub["fwd_ret_30d"].median(skipna=True),
            "pct_fwd_ret_30d_positive": (sub["fwd_ret_30d"] > 0).mean(skipna=True),
        }
        summary_rows.append(row)
        print(f"\n--- {cls} (n={len(sub)}, {row['pct_of_max_holding']:.1f}%) ---")
        for k, v in row.items():
            if k in ("trend_class", "n"):
                continue
            print(f"    {k}: {v}")

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(f"{OUT_DIR}/trend_class_summary.csv", index=False)

    # ------------------------------------------------------------------
    # 5. Regime confounding check -- era distribution by trend class.
    # ------------------------------------------------------------------
    print("\n=== Regime (era) distribution by trend class ===", flush=True)
    era_table = pd.crosstab(out_df["trend_class"], out_df["era"])
    era_table_pct = pd.crosstab(out_df["trend_class"], out_df["era"], normalize="index") * 100
    print(era_table.to_string(), flush=True)
    print("\n(row %):", flush=True)
    print(era_table_pct.round(1).to_string(), flush=True)
    era_table.to_csv(f"{OUT_DIR}/era_distribution_counts.csv")
    era_table_pct.to_csv(f"{OUT_DIR}/era_distribution_pct.csv")

    # ------------------------------------------------------------------
    # 6. Stability / flip-flop diagnostics summary.
    # ------------------------------------------------------------------
    print("\n=== Trend stability diagnostics (fitness for daily recalculation) ===", flush=True)
    print("n_sign_flips_during_hold distribution:")
    print(out_df["n_sign_flips_during_hold"].describe().to_string(), flush=True)
    print(out_df["n_sign_flips_during_hold"].value_counts().sort_index().head(15).to_string(), flush=True)
    print(f"\nmean pct_last_5d_agree_with_cap_classification: "
          f"{out_df['pct_last_5d_agree_with_cap_classification'].mean():.4f}", flush=True)
    print(f"median: {out_df['pct_last_5d_agree_with_cap_classification'].median():.4f}", flush=True)
    print((out_df['pct_last_5d_agree_with_cap_classification'] == 1.0).mean(), flush=True)

    # ------------------------------------------------------------------
    # 7. Statistical tests: Spearman + OLS with era control.
    # ------------------------------------------------------------------
    print("\n=== Statistical tests ===", flush=True)
    valid = out_df.dropna(subset=["trend_since_entry", "fwd_ret_30d"])
    rho, p = stats.spearmanr(valid["trend_since_entry"], valid["fwd_ret_30d"])
    print(f"Spearman(trend_since_entry, fwd_ret_30d), all: rho={rho:.4f}, p={p:.4f}, n={len(valid)}", flush=True)

    for w in (10, 20, 30):
        v = out_df.dropna(subset=["trend_since_entry", f"fwd_ret_{w}d"])
        r, pv = stats.spearmanr(v["trend_since_entry"], v[f"fwd_ret_{w}d"])
        print(f"Spearman(trend_since_entry, fwd_ret_{w}d): rho={r:.4f}, p={pv:.4f}, n={len(v)}", flush=True)

    # Binary trend_class vs fwd_ret_30d, Mann-Whitney (robust to non-normal)
    strong = valid[valid["trend_class"] == "Strengthening"]["fwd_ret_30d"]
    decay = valid[valid["trend_class"] == "Decaying"]["fwd_ret_30d"]
    u_stat, u_p = stats.mannwhitneyu(strong, decay, alternative="two-sided")
    print(f"\nMann-Whitney U (Strengthening vs Decaying, fwd_ret_30d): "
          f"U={u_stat:.1f}, p={u_p:.4f}, n_strong={len(strong)}, n_decay={len(decay)}", flush=True)

    # OLS with era dummies
    import statsmodels.api as sm
    era_dummies = pd.get_dummies(valid["era"], drop_first=True).astype(float)
    X = pd.concat([valid[["trend_since_entry"]].reset_index(drop=True),
                   era_dummies.reset_index(drop=True)], axis=1)
    X = sm.add_constant(X)
    y = valid["fwd_ret_30d"].reset_index(drop=True)
    model = sm.OLS(y.astype(float), X.astype(float)).fit()
    print("\nOLS: fwd_ret_30d ~ trend_since_entry + era dummies")
    print(model.summary().as_text(), flush=True)

    with open(f"{OUT_DIR}/ols_summary.txt", "w") as fh:
        fh.write(model.summary().as_text())

    stats_out = {
        "spearman_trend_fwd30_rho": float(rho),
        "spearman_trend_fwd30_p": float(p),
        "n_spearman": int(len(valid)),
        "mannwhitney_u": float(u_stat),
        "mannwhitney_p": float(u_p),
        "n_strengthening": int(len(strong)),
        "n_decaying": int(len(decay)),
        "ols_trend_coef": float(model.params.get("trend_since_entry", float("nan"))),
        "ols_trend_pvalue": float(model.pvalues.get("trend_since_entry", float("nan"))),
        "ols_r_squared": float(model.rsquared),
        "skipped_trades": skipped,
        "total_max_holding": len(out_df),
    }
    with open(f"{OUT_DIR}/step1_stats_summary.json", "w") as fh:
        json.dump(stats_out, fh, indent=2)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
