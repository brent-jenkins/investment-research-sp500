"""Step 1 -- descriptive, retrospective check of a tighter momentum/volume
re-evaluation gate applied AT the existing 30-day MAX_HOLDING cap.

Question (per task brief, a third, distinct attempt at the same hard
problem two sibling investigations already tried and rejected):
does re-checking two signals -- momentum-strength band (existing,
already-validated definition) and relative trading volume (new, not used
anywhere in this codebase before tonight) -- AT THE MOMENT the 30-day cap
would normally fire, predict what happens to price AFTERWARD? Unlike the
two rejected sibling investigations (entry-time momentum strength;
momentum trend since entry), this tests signals read AT THE CAP POINT
ITSELF, which is the actual moment a real extend/close mechanism would
have to decide.

Relation to sibling investigations, stated plainly (per task instruction
-- do not conflate "phenomenon is real" with "this mechanism predicts it"):
  - momentum_horizon_2026-08-05: entry-time momentum-strength band vs.
    post-cap continuation -- REJECTED (no monotonic gradient, near-zero
    Spearman rho in aggregate AND within the dominant Very Strong band).
  - dynamic_momentum_cap_2026-08-05: momentum TREND since entry vs.
    post-cap continuation -- REJECTED (real only in the small 2007-2009
    GFC window, 8.8% of population; zero effect in the dominant 2010-2019
    regime, 52.6% of population).
  - THIS investigation: momentum-strength band AT THE CAP (not at entry,
    not a trend) + relative volume AT THE CAP -- a genuinely different
    signal in both cases. Both prior investigations' near-zero finding on
    momentum-adjacent signals is a real, relevant prior; this script
    reports directly on whether re-checking the SAME kind of information
    at a different point (the cap, not entry) or combined with a
    structurally distinct signal (volume) behaves any differently.

Backtest convention (per CLAUDE.md's "Two backtest conventions" standing
requirement): Step 1 is a CONTINUOUS WALK -- not a variant comparison,
purely "pull one real, already-fidelity-established trade set and look at
what happened next," identical framing to both sibling investigations.

Engine: analysis.backtest.simulate_portfolio_compounding_mtm() via
analysis.validation.run_full_validation(config, mark_to_market=True,
live_universe=True) -- the one engine confirmed to genuinely match live
execution. Run FRESH here (not reused from the sibling investigations'
saved JSON) because production code changed since those runs (notably
3496224, "Fix silent BRK.B/BF.B ticker bug and rolling-window start-date
drift", committed 2026-08-05 19:31, AFTER both sibling investigations'
runs at 12:xx/14:xx) -- reusing stale trade output would risk silently
re-testing against a bug that has since been fixed, exactly what CLAUDE.md's
"verify the local environment is current, don't assume it" standing
practice warns against.

Momentum-strength bands: EXACT reuse of main.py's own
_compute_momentum_strength_bands()/_momentum_strength_label() definitions
(q25/q75/q90 percentile split of the entry-gate-eligible pool), applied at
each trade's EXIT date rather than its entry date -- this is the "tighter
bar, re-run at the cap" the task specifies, using the SAME thresholds, not
a new one.

Relative volume: NEW signal, not used anywhere in production. Defined as
trailing relative volume, structurally distinct from momentum (a
liquidity/participation measure, not a price-direction measure):

    avg_vol_20(t) = mean(volume[t-20 .. t-1])   # trailing 20 days, EXCLUDING day t itself
    rel_volume(t) = volume[t] / avg_vol_20(t)

Excluding day t from its own average avoids the ratio being self-diluted
by the very day being measured. Banded the same way as momentum -- q25/q75/q90
percentile split of the SAME entry-gate-eligible pool's rel_volume
distribution on that date -- for direct comparability with the momentum
bands (both use "Strong"/"Very Strong" to mean "top quartile/decile of
that day's eligible pool", not an arbitrary fixed multiplier).

"Eligible candidate pool" on a given date: identical definition to both
sibling investigations -- every live candidate ticker whose row on that
date passes passes_entry_gate() (signal on AND genuine index member AND
not crisis-blocked) AND passes_momentum_floor() against the real,
currently-configured floor. Re-entry cooldown is NOT applied to the pool
definition (production reentry_cooldown_days=0, a no-op), stated as a
scope decision, same as both sibling investigations.

Note on why ALL MAX_HOLDING trades still have signal on at exit: exit
priority is CRISIS_EXIT > STOP_LOSS > SIGNAL_OFF > MAX_HOLDING (analysis/
decision.py:evaluate_exit_reason(), checked in that order every day of the
hold) -- so if a position's signal had turned off by day 30, it would
already have exited via SIGNAL_OFF before MAX_HOLDING could ever fire.
Every MAX_HOLDING trade is, by construction, still signal-on (though not
necessarily still above the 2% entry floor -- that floor gates entry only,
never continued holding) and not crisis-blocked at its own exit date.
"""
import sys
sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import json
import pickle
import time

import numpy as np
import pandas as pd
import yaml
from scipy import stats as sp_stats

from analysis import crisis, universe
from analysis.backtest import _atr
from analysis import signals as signals_mod
from analysis.decision import (
    passes_entry_gate,
    passes_momentum_floor,
    resolve_min_momentum_floor,
)
from analysis.validation import run_full_validation
from data.ingestion import get_price_df

with open(r'c:\GitHub\investment-research-sp500\config.yaml', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

OUT_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_volume_reeval_cap_2026-08-06'
END = pd.Timestamp("2026-08-03")  # matches real DB coverage max date exactly
START = pd.Timestamp("2007-01-01")

FWD_WINDOWS = (10, 20, 30)
VOL_WINDOW = 20


def _bands(values: pd.Series) -> dict:
    """Byte-identical logic to main.py:_compute_momentum_strength_bands(),
    generalized to any values series (used for both momentum and rel_volume)."""
    vals = values.dropna()
    if len(vals) < 4:
        return {}
    return {
        "q25": vals.quantile(0.25),
        "q75": vals.quantile(0.75),
        "q90": vals.quantile(0.90),
    }


def _label(val, bands: dict) -> str:
    """Byte-identical logic to main.py:_momentum_strength_label()."""
    if not bands or pd.isna(val):
        return "-"
    if val >= bands["q90"]:
        return "Very Strong"
    if val >= bands["q75"]:
        return "Strong"
    if val >= bands["q25"]:
        return "Moderate"
    return "Weak"


def _era(entry_date: pd.Timestamp) -> str:
    y = entry_date.year
    if y <= 2009:
        return "2007-2009_GFC"
    if y <= 2019:
        return "2010-2019_bull"
    if y == 2020:
        return "2020_covid"
    return "2021-2026_recent"


def precompute_ticker_with_volume(ticker: str, config: dict):
    """Same as analysis.backtest.precompute_ticker() but keeps volume and
    rel_volume alongside close/atr/signal/momentum -- additive, does not
    change what the real engine itself computes or uses."""
    df = get_price_df(ticker, days=8000)
    if df.empty or len(df) < 300:
        return None
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    atr_s = _atr(high, low, close, config["signals"]["atr_period"])
    momentum_s = signals_mod.compute_momentum_series(close)
    sig = signals_mod.compute_signal_series(close)

    avg_vol_20 = volume.shift(1).rolling(VOL_WINDOW).mean()
    rel_volume = volume / avg_vol_20

    frame = pd.DataFrame({
        "close": close, "atr": atr_s, "signal": sig, "momentum": momentum_s,
        "volume": volume, "rel_volume": rel_volume,
    })
    return {"ticker": ticker, "frame": frame}


def main():
    t0 = time.time()
    print("=== Step 1: momentum/volume re-eval-at-cap descriptive check ===", flush=True)

    # ------------------------------------------------------------------
    # 1. Real backtest run -- FRESH (not reused), current code, source of
    #    truth for MAX_HOLDING trades.
    # ------------------------------------------------------------------
    print("Running real backtest (simulate_portfolio_compounding_mtm, "
          "live_universe=True, continuous walk 2007-2026)...", flush=True)
    result = run_full_validation(
        CONFIG, start=START, end=END, mark_to_market=True, live_universe=True,
    )
    print(f"  engine_used={result['engine_used']} deployment_basis={result['deployment_basis']} "
          f"backtest_convention={result['backtest_convention']}", flush=True)
    print(f"  total_trades={result['total_trades']}  n_tickers_with_data={result['n_tickers_with_data']}"
          f"/{result['n_tickers_total']}", flush=True)

    all_trades = result["trades"]
    trades_df = pd.DataFrame(all_trades)
    exit_reason_counts = trades_df["exit_reason"].value_counts()
    print("  exit_reason breakdown:\n" + exit_reason_counts.to_string(), flush=True)

    max_hold_trades = trades_df[trades_df["exit_reason"] == "MAX_HOLDING"].copy()
    print(f"  MAX_HOLDING trades: {len(max_hold_trades)} "
          f"({100*len(max_hold_trades)/len(trades_df):.1f}% of all trades)", flush=True)

    with open(f"{OUT_DIR}/all_trades_full_run.json", "w") as fh:
        json.dump(all_trades, fh, default=str)
    print(f"  full trade-level output preserved -> all_trades_full_run.json ({len(all_trades)} trades)",
          flush=True)

    # ------------------------------------------------------------------
    # 2. Build frames (with volume) for every live candidate ticker.
    # ------------------------------------------------------------------
    candidate_tickers = universe.get_live_stock_candidates(as_of=END.date())
    print(f"\nBuilding precompute_ticker_with_volume() caches for {len(candidate_tickers)} live candidates...",
          flush=True)
    frames = {}
    for i, ticker in enumerate(candidate_tickers):
        c = precompute_ticker_with_volume(ticker, CONFIG)
        if c is not None:
            frames[ticker] = c["frame"]
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{len(candidate_tickers)} tickers cached "
                  f"({time.time()-t0:.0f}s elapsed)", flush=True)
    print(f"  {len(frames)}/{len(candidate_tickers)} tickers have usable frames", flush=True)

    with open(f"{OUT_DIR}/_frames_cache.pkl", "wb") as fh:
        pickle.dump(frames, fh)

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close_full = gspc_df["close"].astype(float)
    raw_crisis_gspc = crisis.compute_raw_trigger(gspc_close_full)
    entry_blocked_gspc = crisis.extend_trigger(raw_crisis_gspc)

    min_momentum_floor = resolve_min_momentum_floor(CONFIG)
    print(f"  min_momentum_floor (production) = {min_momentum_floor}", flush=True)

    # ------------------------------------------------------------------
    # 3. For every MAX_HOLDING trade: recompute the eligible pool AT THE
    #    EXIT DATE (the cap point), band momentum + rel_volume against
    #    that pool, then measure real post-exit price behaviour.
    # ------------------------------------------------------------------
    print(f"\nProcessing {len(max_hold_trades)} MAX_HOLDING trades...", flush=True)
    records = []
    pool_cache = {}  # exit_date -> (mom_bands, vol_bands, pool_size)

    skipped_no_frame = 0
    for idx, trade in enumerate(max_hold_trades.to_dict("records")):
        ticker = trade["ticker"]
        entry_date = pd.Timestamp(trade["entry_date"])
        exit_date = pd.Timestamp(trade["exit_date"])
        frame = frames.get(ticker)
        if frame is None or exit_date not in frame.index:
            skipped_no_frame += 1
            continue

        momentum_at_exit = frame.loc[exit_date, "momentum"]
        rel_volume_at_exit = frame.loc[exit_date, "rel_volume"]

        # --- Eligible pool + bands AT THE EXIT DATE (memoised) ---
        if exit_date not in pool_cache:
            pool_momentum = []
            pool_relvol = []
            crisis_val = bool(entry_blocked_gspc.reindex([exit_date]).ffill().iloc[0]) \
                if len(entry_blocked_gspc) else False
            for t2, f2 in frames.items():
                if exit_date not in f2.index:
                    continue
                row2 = f2.loc[exit_date]
                sig_on2 = bool(row2["signal"]) if pd.notna(row2["signal"]) else False
                if not sig_on2:
                    continue
                is_mem2 = universe.is_member(t2, exit_date.date())
                mom2 = row2["momentum"]
                mom2 = float(mom2) if pd.notna(mom2) else None
                if not passes_entry_gate(sig_on=sig_on2, is_member=is_mem2, entry_blocked=crisis_val):
                    continue
                if not passes_momentum_floor(mom2, min_momentum_floor):
                    continue
                pool_momentum.append(mom2)
                rv2 = row2["rel_volume"]
                if pd.notna(rv2):
                    pool_relvol.append(float(rv2))
            mom_bands = _bands(pd.Series(pool_momentum))
            vol_bands = _bands(pd.Series(pool_relvol))
            pool_cache[exit_date] = (mom_bands, vol_bands, len(pool_momentum))
        mom_bands, vol_bands, pool_size = pool_cache[exit_date]
        mom_strength_at_cap = _label(momentum_at_exit, mom_bands)
        vol_strength_at_cap = _label(rel_volume_at_exit, vol_bands)

        mom_elevated = mom_strength_at_cap in ("Strong", "Very Strong")
        vol_elevated = vol_strength_at_cap in ("Strong", "Very Strong")
        both_elevated = mom_elevated and vol_elevated

        # --- Price behaviour around the exit (same as sibling reports) ---
        close = frame["close"].astype(float)
        exit_loc = frame.index.get_loc(exit_date)
        entry_loc = frame.index.get_loc(entry_date) if entry_date in frame.index else None
        exit_price = float(trade["exit_price"])

        fwd = {}
        n_avail = len(close) - 1 - exit_loc
        for w in FWD_WINDOWS:
            if n_avail >= w:
                fwd[f"fwd_ret_{w}d"] = float(close.iloc[exit_loc + w] / exit_price - 1.0)
            else:
                fwd[f"fwd_ret_{w}d"] = None

        during_hold_max = (
            float(close.iloc[entry_loc:exit_loc + 1].max()) if entry_loc is not None else np.nan
        )
        already_peaked_at_exit = (
            bool(exit_price < during_hold_max * 0.999) if pd.notna(during_hold_max) else None
        )

        records.append({
            "ticker": ticker,
            "entry_date": entry_date.date().isoformat(),
            "exit_date": exit_date.date().isoformat(),
            "era": _era(entry_date),
            "entry_price": trade["entry_price"],
            "exit_price": exit_price,
            "pnl_pct": trade["pnl_pct"],
            "momentum_at_exit": None if pd.isna(momentum_at_exit) else float(momentum_at_exit),
            "mom_strength_at_cap": mom_strength_at_cap,
            "rel_volume_at_exit": None if pd.isna(rel_volume_at_exit) else float(rel_volume_at_exit),
            "vol_strength_at_cap": vol_strength_at_cap,
            "mom_elevated": mom_elevated,
            "vol_elevated": vol_elevated,
            "both_elevated": both_elevated,
            "pool_size_at_exit": pool_size,
            "already_peaked_at_exit": already_peaked_at_exit,
            **fwd,
        })

        if (idx + 1) % 200 == 0:
            print(f"    {idx+1}/{len(max_hold_trades)} MAX_HOLDING trades processed "
                  f"({time.time()-t0:.0f}s elapsed)", flush=True)

    print(f"  skipped (no frame/exit_date not in index): {skipped_no_frame}", flush=True)

    out_df = pd.DataFrame(records)
    out_df.to_csv(f"{OUT_DIR}/max_holding_trades_annotated.csv", index=False)
    print(f"\nWrote {len(out_df)} annotated MAX_HOLDING trades -> max_holding_trades_annotated.csv",
          flush=True)

    # ------------------------------------------------------------------
    # 4. Band summaries for each signal independently.
    # ------------------------------------------------------------------
    band_order = ["Weak", "Moderate", "Strong", "Very Strong", "-"]

    def band_summary(col_strength: str, label: str) -> pd.DataFrame:
        rows = []
        for band in band_order:
            sub = out_df[out_df[col_strength] == band]
            if sub.empty:
                continue
            rows.append({
                "band": band, "n": len(sub),
                "pct_of_max_holding": 100 * len(sub) / len(out_df),
                "mean_fwd_ret_10d": sub["fwd_ret_10d"].mean(skipna=True),
                "mean_fwd_ret_20d": sub["fwd_ret_20d"].mean(skipna=True),
                "mean_fwd_ret_30d": sub["fwd_ret_30d"].mean(skipna=True),
                "median_fwd_ret_30d": sub["fwd_ret_30d"].median(skipna=True),
                "pct_fwd_ret_30d_positive": (sub["fwd_ret_30d"] > 0).mean(skipna=True),
                "pct_already_peaked": sub["already_peaked_at_exit"].mean(skipna=True),
            })
        df = pd.DataFrame(rows)
        print(f"\n=== {label} band summary ===")
        print(df.to_string(index=False))
        return df

    mom_summary = band_summary("mom_strength_at_cap", "Momentum-at-cap")
    mom_summary.to_csv(f"{OUT_DIR}/momentum_at_cap_band_summary.csv", index=False)
    vol_summary = band_summary("vol_strength_at_cap", "Rel-volume-at-cap")
    vol_summary.to_csv(f"{OUT_DIR}/relvolume_at_cap_band_summary.csv", index=False)

    # ------------------------------------------------------------------
    # 5. Elevated vs not-elevated group comparisons (mirrors "would a
    #    real extend/close gate have picked a materially different set").
    # ------------------------------------------------------------------
    def group_compare(flag_col: str, label: str):
        elev = out_df[out_df[flag_col] == True]["fwd_ret_30d"].dropna()
        not_elev = out_df[out_df[flag_col] == False]["fwd_ret_30d"].dropna()
        u, p = sp_stats.mannwhitneyu(elev, not_elev, alternative="two-sided")
        print(f"\n--- {label}: elevated (n={len(elev)}) vs not (n={len(not_elev)}) ---")
        print(f"  mean fwd_ret_30d: elevated={elev.mean():.4f}  not_elevated={not_elev.mean():.4f}")
        print(f"  median fwd_ret_30d: elevated={elev.median():.4f}  not_elevated={not_elev.median():.4f}")
        print(f"  % positive: elevated={(elev>0).mean():.3f}  not_elevated={(not_elev>0).mean():.3f}")
        print(f"  Mann-Whitney U={u:.1f} p={p:.4f}")
        return {"label": label, "n_elevated": len(elev), "n_not_elevated": len(not_elev),
                "mean_elevated": elev.mean(), "mean_not_elevated": not_elev.mean(),
                "median_elevated": elev.median(), "median_not_elevated": not_elev.median(),
                "pct_pos_elevated": (elev > 0).mean(), "pct_pos_not_elevated": (not_elev > 0).mean(),
                "mw_u": u, "mw_p": p}

    group_results = []
    group_results.append(group_compare("mom_elevated", "Momentum-at-cap elevated (Strong/Very Strong)"))
    group_results.append(group_compare("vol_elevated", "Rel-volume-at-cap elevated (Strong/Very Strong)"))
    group_results.append(group_compare("both_elevated", "BOTH elevated (momentum AND volume)"))

    pd.DataFrame(group_results).to_csv(f"{OUT_DIR}/group_comparison_results.csv", index=False)

    # ------------------------------------------------------------------
    # 6. Continuous-signal Spearman correlations (whole pop + dominant era).
    # ------------------------------------------------------------------
    print("\n=== Spearman correlations ===")
    corr_rows = []
    dominant = out_df[out_df["era"] == "2010-2019_bull"]
    for col, name in [("momentum_at_exit", "momentum_at_exit"), ("rel_volume_at_exit", "rel_volume_at_exit")]:
        sub = out_df[[col, "fwd_ret_30d"]].dropna()
        rho, p = sp_stats.spearmanr(sub[col], sub["fwd_ret_30d"])
        print(f"  {name} vs fwd_ret_30d, ALL: rho={rho:.4f} p={p:.4f} n={len(sub)}")
        corr_rows.append({"signal": name, "scope": "all", "rho": rho, "p": p, "n": len(sub)})

        sub_d = dominant[[col, "fwd_ret_30d"]].dropna()
        rho_d, p_d = sp_stats.spearmanr(sub_d[col], sub_d["fwd_ret_30d"])
        print(f"  {name} vs fwd_ret_30d, 2010-2019_bull ONLY: rho={rho_d:.4f} p={p_d:.4f} n={len(sub_d)}")
        corr_rows.append({"signal": name, "scope": "2010-2019_bull", "rho": rho_d, "p": p_d, "n": len(sub_d)})

    pd.DataFrame(corr_rows).to_csv(f"{OUT_DIR}/spearman_results.csv", index=False)

    # ------------------------------------------------------------------
    # 7. Regime confounding -- era distribution of elevated flags, and
    #    within-era group comparison for the dominant regime specifically.
    # ------------------------------------------------------------------
    print("\n=== Era distribution of elevated flags ===")
    era_order = ["2007-2009_GFC", "2010-2019_bull", "2020_covid", "2021-2026_recent"]
    era_rows = []
    for flag_col, label in [("mom_elevated", "momentum"), ("vol_elevated", "volume"), ("both_elevated", "both")]:
        for elev_val in (True, False):
            sub = out_df[out_df[flag_col] == elev_val]
            row = {"signal": label, "elevated": elev_val, "n": len(sub)}
            for era_name in era_order:
                row[era_name] = int((sub["era"] == era_name).sum())
            era_rows.append(row)
    era_df = pd.DataFrame(era_rows)
    print(era_df.to_string(index=False))
    era_df.to_csv(f"{OUT_DIR}/era_distribution.csv", index=False)

    print("\n=== Within-era group comparison (2010-2019_bull dominant regime only) ===")
    within_era_rows = []
    for flag_col, label in [("mom_elevated", "momentum"), ("vol_elevated", "volume"), ("both_elevated", "both")]:
        elev = dominant[dominant[flag_col] == True]["fwd_ret_30d"].dropna()
        not_elev = dominant[dominant[flag_col] == False]["fwd_ret_30d"].dropna()
        if len(elev) >= 2 and len(not_elev) >= 2:
            u, p = sp_stats.mannwhitneyu(elev, not_elev, alternative="two-sided")
        else:
            u, p = np.nan, np.nan
        print(f"  {label}: n_elevated={len(elev)} mean={elev.mean():.4f} | "
              f"n_not_elevated={len(not_elev)} mean={not_elev.mean():.4f} | MW p={p}")
        within_era_rows.append({
            "signal": label, "n_elevated": len(elev), "mean_elevated": elev.mean(),
            "n_not_elevated": len(not_elev), "mean_not_elevated": not_elev.mean(), "mw_p": p,
        })
    pd.DataFrame(within_era_rows).to_csv(f"{OUT_DIR}/within_era_dominant_regime.csv", index=False)

    # ------------------------------------------------------------------
    # 8. Outlier robustness -- trim top/bottom 1% of fwd_ret_30d.
    # ------------------------------------------------------------------
    print("\n=== Outlier robustness (trim top/bottom 1% of fwd_ret_30d) ===")
    trim_rows = []
    valid = out_df.dropna(subset=["fwd_ret_30d"])
    lo, hi = valid["fwd_ret_30d"].quantile(0.01), valid["fwd_ret_30d"].quantile(0.99)
    trimmed = valid[(valid["fwd_ret_30d"] >= lo) & (valid["fwd_ret_30d"] <= hi)]
    for col, name in [("momentum_at_exit", "momentum_at_exit"), ("rel_volume_at_exit", "rel_volume_at_exit")]:
        sub = trimmed[[col, "fwd_ret_30d"]].dropna()
        rho, p = sp_stats.spearmanr(sub[col], sub["fwd_ret_30d"])
        print(f"  {name} vs fwd_ret_30d, TRIMMED: rho={rho:.4f} p={p:.4f} n={len(sub)}")
        trim_rows.append({"signal": name, "rho": rho, "p": p, "n": len(sub)})
    pd.DataFrame(trim_rows).to_csv(f"{OUT_DIR}/outlier_robustness.csv", index=False)

    print(f"\nTotal runtime: {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
