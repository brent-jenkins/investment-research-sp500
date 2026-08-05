"""Concentration-risk investigation, Step 2: reconstruct simultaneously-open
positions day by day from the real trade list produced by
run_full_backtest.py (the REAL, unmodified simulate_portfolio_compounding_mtm(),
continuous-walk convention, full 2007-2026 live-universe history), and
quantify:
  (a) how often / how severely the system held 2+ correlated positions at once
  (b) whether that concentration carried a measurable, ISOLATED cost beyond
      what contemporaneous market volatility/drawdown alone would predict.

CONVENTIONS, stated explicitly (do not leave implicit, per CLAUDE.md):
- Backtest convention for the underlying trade list: CONTINUOUS WALK (this is
  a track-record / historical-frequency question -- "how did concentration
  actually behave for the one real account this system represents" -- not a
  variant comparison, so reset-at-boundary would be the WRONG convention
  here; see CLAUDE.md's "Two backtest conventions" section).
- "Open" for concentration/correlation purposes: entry_date <= day < exit_date
  (half-open interval). The exit day's own return is already realized into
  the trade's recorded pnl_gbp/exit_price and credited to cash that day, so
  counting the exit day itself as still "open exposure" would double-count
  that day's return in both the correlation-window sample AND the realized
  P&L. This is a deliberate, stated modeling choice, not an oversight.
- All rolling windows (correlation, volatility) use data STRICTLY BEFORE the
  day being evaluated (window ends at day t-1) -- no look-ahead.
- No custom candidate pool, no monkeypatched decision logic anywhere in this
  script -- it only reads the trade list already produced by the real,
  unmodified production engine and independently-sourced daily closing
  prices (data.ingestion.get_price_df, the same function every engine in
  this codebase uses). There is no decision-engine parity concern for this
  script under CLAUDE.md's "Research scripts must verify decision-engine
  parity" rule, since that rule is scoped to scripts that build a custom
  universe or monkeypatch is_member()/entry/exit/sizing logic -- this one
  does neither; it is pure downstream analysis of the real engine's own
  already-produced output.

Outputs (all under results/, full trade-level / day-level output preserved,
not just aggregates, per CLAUDE.md's reproducibility standard):
  - daily_panel.csv: one row per trading day with n_open, correlation stats,
    diversification ratio, equity, returns, market regime variables.
  - concentration_summary.json: the headline frequency/severity numbers.
  - isolation_analysis.json: the regression/matched-bucket isolation results.
  - worst_episodes.csv: the 25 worst single-day strategy losses with their
    concentration/market context.
  - semiconductor_sanity_check.json: the small hardcoded-group secondary
    check (illustrative only, not the primary tool).
"""
import sys
import json
import time
import itertools

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

from data.ingestion import get_price_df

RESULTS_DIR = r'c:\GitHub\investment-research-sp500\research\concentration_risk_2026-08-05\results'
TRADES_PATH = RESULTS_DIR + r'\full_trades_continuous.json'
STARTING_CAPITAL = 50000.0

WINDOWS = {"20d": 20, "60d": 60, "120d": 120}
PRIMARY_WINDOW = "60d"
MIN_OBS = {"20d": 12, "60d": 35, "120d": 70}  # minimum overlapping obs to trust a pairwise corr

# Secondary/illustrative-only hardcoded group (per task instructions -- never
# the primary tool). The five 2026-08-02/03 incident tickers plus a handful
# of other widely-known semiconductor/storage names.
SEMI_GROUP = {"SNDK", "WDC", "MU", "LITE", "STX", "NVDA", "AMD", "INTC", "TXN",
              "AVGO", "QCOM", "AMAT", "LRCX", "KLAC", "ON", "MCHP", "ADI", "MPWR"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("Loading trade list...")
    with open(TRADES_PATH) as f:
        run = json.load(f)
    trades = run["trades"]
    log(f"{len(trades)} trades loaded. Convention: {run['backtest_convention']}")

    tdf = pd.DataFrame(trades)
    tdf["entry_date"] = pd.to_datetime(tdf["entry_date"])
    tdf["exit_date"] = pd.to_datetime(tdf["exit_date"])
    tdf["trade_id"] = np.arange(len(tdf))

    # Sanity: at most one open trade per ticker at any instant (engine
    # invariant) -- checked directly, not assumed.
    overlap_found = False
    for ticker, g in tdf.groupby("ticker"):
        g = g.sort_values("entry_date")
        prev_exit = None
        for _, row in g.iterrows():
            if prev_exit is not None and row["entry_date"] < prev_exit:
                overlap_found = True
                log(f"  OVERLAP WARNING: {ticker} entry {row['entry_date']} before prior exit {prev_exit}")
            prev_exit = row["exit_date"]
    log(f"Overlapping same-ticker trades found: {overlap_found} (expected False -- engine invariant)")

    tickers = sorted(tdf["ticker"].unique())
    log(f"{len(tickers)} distinct tickers traded.")

    gspc_df = get_price_df("^GSPC", days=8000).set_index("date")
    gspc_df.index = pd.to_datetime(gspc_df.index)
    gspc_close = gspc_df["close"].astype(float).sort_index()

    cal_start = tdf["entry_date"].min()
    cal_end = tdf["exit_date"].max()
    master_cal = gspc_close.loc[(gspc_close.index >= cal_start) & (gspc_close.index <= cal_end)].index
    log(f"Master calendar: {len(master_cal)} trading days, {master_cal.min().date()} - {master_cal.max().date()}")

    # ---- Load & align price panel for all traded tickers ----------------
    log("Loading price series for all traded tickers...")
    price_panel = pd.DataFrame(index=master_cal)
    for i, ticker in enumerate(tickers):
        df = get_price_df(ticker, days=8000)
        if df.empty:
            continue
        df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        s = df["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        price_panel[ticker] = s.reindex(master_cal).ffill(limit=5)
        if (i + 1) % 100 == 0:
            log(f"  loaded {i+1}/{len(tickers)}")
    log(f"Price panel shape: {price_panel.shape}")

    log_ret_panel = np.log(price_panel / price_panel.shift(1))

    # ---- Sweep-line reconstruction: daily active set, cash, mtm ---------
    # Built as plain dicts (not groupby().apply()) -- deliberately, to avoid
    # pandas' version-sensitive ambiguity around whether an applied function
    # returning a list-like per group gets stored as a single object per
    # group or expanded/broadcast; a plain dict keyed by date has no such
    # ambiguity.
    opens_by_date = {}
    closes_by_date = {}
    for row in tdf.itertuples():
        opens_by_date.setdefault(row.entry_date, []).append(row)
        closes_by_date.setdefault(row.exit_date, []).append(row)

    active = {}  # ticker -> dict(entry_price, position_gbp, trade_id)
    cash = STARTING_CAPITAL

    rows = []
    day_pos = {d: i for i, d in enumerate(master_cal)}  # date -> integer position in master_cal

    mkt_log_ret = np.log(gspc_close / gspc_close.shift(1))
    mkt_vol20 = mkt_log_ret.rolling(20).std() * np.sqrt(252) * 100.0
    mkt_roll_high = gspc_close.rolling(252, min_periods=60).max()
    mkt_dd = (gspc_close / mkt_roll_high - 1.0) * 100.0

    prev_equity = STARTING_CAPITAL
    log("Sweeping master calendar day by day...")
    t0 = time.time()
    for pos_idx, day in enumerate(master_cal):
        # 1) closes dated today -> remove from active, credit cash
        if day in closes_by_date:
            for row in closes_by_date[day]:
                tk = row.ticker
                if tk in active and active[tk]["trade_id"] == row.trade_id:
                    cash += row.position_gbp + row.pnl_gbp
                    del active[tk]
                # else: defensive -- should not happen given engine invariant

        # 2) opens dated today -> add to active, debit cash
        if day in opens_by_date:
            for row in opens_by_date[day]:
                cash -= row.position_gbp
                active[row.ticker] = {
                    "entry_price": row.entry_price,
                    "position_gbp": row.position_gbp,
                    "trade_id": row.trade_id,
                    "entry_date": row.entry_date,
                }

        # 3) evaluate today's snapshot
        n_open = len(active)
        mtm = 0.0
        open_tickers = list(active.keys())
        for tk, info in active.items():
            price_today = price_panel.at[day, tk] if tk in price_panel.columns else np.nan
            if pd.isna(price_today):
                mtm += info["position_gbp"]  # fallback to cost basis, defensive only
            else:
                mtm += info["position_gbp"] * (price_today / info["entry_price"])
        equity = cash + mtm
        r_t = (equity / prev_equity - 1.0) if prev_equity else 0.0
        prev_equity = equity if equity > 0 else prev_equity

        corr_stats = {}
        if n_open >= 2:
            for wname, wlen in WINDOWS.items():
                lo = max(0, pos_idx - wlen)
                hi = pos_idx  # exclusive of today -- strictly before
                if hi - lo < MIN_OBS[wname]:
                    corr_stats[wname] = None
                    continue
                sub = log_ret_panel.iloc[lo:hi][open_tickers]
                valid_cols = [c for c in open_tickers if sub[c].notna().sum() >= MIN_OBS[wname]]
                if len(valid_cols) < 2:
                    corr_stats[wname] = None
                    continue
                cm = sub[valid_cols].corr(min_periods=MIN_OBS[wname])
                iu = np.triu_indices_from(cm.values, k=1)
                pair_vals = cm.values[iu]
                pair_vals = pair_vals[~np.isnan(pair_vals)]
                if len(pair_vals) == 0:
                    corr_stats[wname] = None
                    continue
                avg_c = float(np.mean(pair_vals))
                min_c = float(np.min(pair_vals))
                max_c = float(np.max(pair_vals))

                dr = None
                indep_gap = None
                if wname == PRIMARY_WINDOW:
                    weights = np.array([active[c]["position_gbp"] for c in valid_cols])
                    weights = weights / weights.sum()
                    vols = sub[valid_cols].std().values
                    cov = sub[valid_cols].cov().values
                    p_real = float(np.sqrt(weights @ cov @ weights))
                    p_ind = float(np.sqrt(np.sum((weights ** 2) * (vols ** 2))))
                    weighted_avg_vol = float(np.sum(weights * vols))
                    if p_real > 0:
                        dr = weighted_avg_vol / p_real
                        if p_ind > 0:
                            indep_gap = p_real / p_ind - 1.0
                corr_stats[wname] = {"avg": avg_c, "min": min_c, "max": max_c,
                                      "n_pairs": len(pair_vals),
                                      "dr": dr, "indep_gap": indep_gap}

        rows.append({
            "date": day, "n_open": n_open, "open_tickers": ",".join(open_tickers),
            "cash": cash, "mtm": mtm, "equity": equity, "r_t": r_t,
            "avg_corr_20d": (corr_stats.get("20d") or {}).get("avg"),
            "avg_corr_60d": (corr_stats.get("60d") or {}).get("avg"),
            "avg_corr_120d": (corr_stats.get("120d") or {}).get("avg"),
            "n_pairs_60d": (corr_stats.get("60d") or {}).get("n_pairs"),
            "dr_60d": (corr_stats.get("60d") or {}).get("dr"),
            "indep_gap_60d": (corr_stats.get("60d") or {}).get("indep_gap"),
            "mkt_ret": mkt_log_ret.get(day, np.nan),
            "mkt_vol20": mkt_vol20.get(day, np.nan),
            "mkt_dd": mkt_dd.get(day, np.nan),
        })
        if (pos_idx + 1) % 500 == 0:
            log(f"  swept {pos_idx+1}/{len(master_cal)} days ({time.time()-t0:.0f}s)")

    log(f"Sweep done, {time.time()-t0:.0f}s. Final equity={prev_equity:.2f}")
    panel = pd.DataFrame(rows)
    panel.to_csv(RESULTS_DIR + r"\daily_panel.csv", index=False)
    log(f"Saved daily_panel.csv ({len(panel)} rows)")

    # ======================================================================
    # PART 1 -- frequency / severity of concentration
    # ======================================================================
    n_days = len(panel)
    freq = {}
    for k in [1, 2, 3, 4, 5]:
        cnt = int((panel["n_open"] >= k).sum())
        freq[f"pct_days_n_open_ge_{k}"] = round(100.0 * cnt / n_days, 2)
        freq[f"n_days_n_open_ge_{k}"] = cnt
    freq["n_days_total"] = n_days
    freq["n_days_n_open_eq_0"] = int((panel["n_open"] == 0).sum())

    def dist(series):
        s = series.dropna()
        if s.empty:
            return None
        return {
            "n": int(len(s)), "mean": float(s.mean()), "std": float(s.std()),
            "p5": float(s.quantile(0.05)), "p25": float(s.quantile(0.25)),
            "p50": float(s.quantile(0.50)), "p75": float(s.quantile(0.75)),
            "p95": float(s.quantile(0.95)), "min": float(s.min()), "max": float(s.max()),
        }

    corr_dist = {}
    for wname in WINDOWS:
        col = f"avg_corr_{wname}"
        corr_dist[f"{wname}_all_2plus"] = dist(panel.loc[panel["n_open"] >= 2, col])
        corr_dist[f"{wname}_3plus"] = dist(panel.loc[panel["n_open"] >= 3, col])
        corr_dist[f"{wname}_4plus"] = dist(panel.loc[panel["n_open"] >= 4, col])

    dr_dist = dist(panel.loc[panel["n_open"] >= 2, "dr_60d"])
    indep_gap_dist = dist(panel.loc[panel["n_open"] >= 2, "indep_gap_60d"])

    summary = {
        "n_days_total": n_days,
        "frequency": freq,
        "avg_pairwise_correlation_distribution": corr_dist,
        "diversification_ratio_60d_distribution_n_open_ge_2": dr_dist,
        "independence_gap_60d_distribution_n_open_ge_2": indep_gap_dist,
        "note_independence_gap": "indep_gap_60d = P_real/P_ind - 1, where P_real = sqrt(w'Cov w) using "
                                  "realized 60d covariance of open positions' log returns and P_ind = "
                                  "sqrt(sum(w_i^2 * sigma_i^2)) assumes zero cross-correlation (independence). "
                                  "Positive => realized joint risk is worse than an independence-assuming VaR "
                                  "sizing scheme would imply.",
    }
    with open(RESULTS_DIR + r"\concentration_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log("Saved concentration_summary.json")
    log(json.dumps(freq, indent=2))

    # Worst episodes
    worst = panel.dropna(subset=["r_t"]).sort_values("r_t").head(25).copy()
    worst.to_csv(RESULTS_DIR + r"\worst_episodes.csv", index=False)
    log("Saved worst_episodes.csv")

    # ======================================================================
    # PART 2 -- isolate the cost, controlling for market regime
    # ======================================================================
    sub = panel[panel["n_open"] >= 2].dropna(
        subset=["r_t", "avg_corr_60d", "mkt_vol20", "mkt_dd", "n_open"]
    ).copy()
    log(f"Isolation-analysis sample: {len(sub)} days with n_open>=2 and complete regressors")

    sub["abs_r"] = sub["r_t"].abs()
    sub["downside_r"] = (-sub["r_t"]).clip(lower=0.0)

    isolation = {"n_obs": int(len(sub))}

    def run_isolation(dep_col, label):
        y = sub[dep_col].values
        X_ctrl = sm.add_constant(sub[["mkt_vol20"]].values)
        model_ctrl = sm.OLS(y, X_ctrl).fit()
        resid = y - model_ctrl.predict(X_ctrl)

        pearson_r, pearson_p = stats.pearsonr(resid, sub["avg_corr_60d"].values)
        spearman_r, spearman_p = stats.spearmanr(resid, sub["avg_corr_60d"].values)

        X_full = sm.add_constant(sub[["mkt_vol20", "mkt_dd", "n_open", "avg_corr_60d"]].values)
        model_full = sm.OLS(y, X_full).fit()

        raw_r, raw_p = stats.pearsonr(y, sub["avg_corr_60d"].values)

        return {
            "label": label,
            "raw_correlation_dep_vs_avgcorr60d": {"r": float(raw_r), "p": float(raw_p)},
            "residual_after_controlling_mktvol20_correlation_vs_avgcorr60d": {
                "pearson_r": float(pearson_r), "pearson_p": float(pearson_p),
                "spearman_r": float(spearman_r), "spearman_p": float(spearman_p),
            },
            "multivariate_ols": {
                "dep": dep_col,
                "predictors": ["const", "mkt_vol20", "mkt_dd", "n_open", "avg_corr_60d"],
                "coefs": [float(c) for c in model_full.params],
                "pvalues": [float(p) for p in model_full.pvalues],
                "r_squared": float(model_full.rsquared),
                "avg_corr_60d_coef": float(model_full.params[-1]),
                "avg_corr_60d_pvalue": float(model_full.pvalues[-1]),
            },
            "control_only_ols": {
                "dep": dep_col, "predictors": ["const", "mkt_vol20"],
                "coefs": [float(c) for c in model_ctrl.params],
                "r_squared": float(model_ctrl.rsquared),
            },
        }

    isolation["abs_return"] = run_isolation("abs_r", "absolute daily strategy return")
    isolation["downside_return"] = run_isolation("downside_r", "downside-only daily strategy return")

    # Matched tercile bucket comparison
    sub["vol_tercile"] = pd.qcut(sub["mkt_vol20"], 3, labels=["low_vol", "mid_vol", "high_vol"])
    bucket_rows = []
    for tercile, g in sub.groupby("vol_tercile", observed=True):
        med = g["avg_corr_60d"].median()
        hi = g[g["avg_corr_60d"] >= med]
        lo = g[g["avg_corr_60d"] < med]
        if len(hi) < 5 or len(lo) < 5:
            continue
        t_abs, p_abs = stats.ttest_ind(hi["abs_r"], lo["abs_r"], equal_var=False)
        t_down, p_down = stats.ttest_ind(hi["downside_r"], lo["downside_r"], equal_var=False)
        bucket_rows.append({
            "mkt_vol_tercile": str(tercile),
            "n_high_corr": int(len(hi)), "n_low_corr": int(len(lo)),
            "median_corr_split": float(med),
            "mean_abs_r_high_corr": float(hi["abs_r"].mean()), "mean_abs_r_low_corr": float(lo["abs_r"].mean()),
            "abs_r_ttest_t": float(t_abs), "abs_r_ttest_p": float(p_abs),
            "mean_downside_r_high_corr": float(hi["downside_r"].mean()),
            "mean_downside_r_low_corr": float(lo["downside_r"].mean()),
            "downside_r_ttest_t": float(t_down), "downside_r_ttest_p": float(p_down),
        })
    isolation["matched_tercile_buckets"] = bucket_rows

    with open(RESULTS_DIR + r"\isolation_analysis.json", "w") as f:
        json.dump(isolation, f, indent=2, default=str)
    log("Saved isolation_analysis.json")
    log(json.dumps({k: v for k, v in isolation.items() if k != "matched_tercile_buckets"}, indent=2)[:3000])
    log(json.dumps(bucket_rows, indent=2))

    # ======================================================================
    # Secondary/illustrative: semiconductor group sanity check
    # ======================================================================
    semi_present = SEMI_GROUP & set(tickers)
    semi_rows = panel.copy()
    semi_rows["semi_open"] = semi_rows["open_tickers"].apply(
        lambda s: [t for t in s.split(",") if t in semi_present] if s else []
    )
    semi_rows["n_semi_open"] = semi_rows["semi_open"].apply(len)
    semi_2plus = semi_rows[semi_rows["n_semi_open"] >= 2]
    semi_check = {
        "semi_group_tickers_present_in_universe": sorted(semi_present),
        "n_days_2plus_semi_simultaneously_open": int(len(semi_2plus)),
        "pct_of_all_days": round(100.0 * len(semi_2plus) / n_days, 3),
        "avg_corr_60d_on_those_days": dist(semi_2plus["avg_corr_60d"]) if len(semi_2plus) else None,
        "avg_corr_60d_baseline_all_2plus_days": corr_dist["60d_all_2plus"],
    }
    with open(RESULTS_DIR + r"\semiconductor_sanity_check.json", "w") as f:
        json.dump(semi_check, f, indent=2, default=str)
    log("Saved semiconductor_sanity_check.json")
    log(json.dumps(semi_check, indent=2, default=str))

    log("DONE.")


if __name__ == "__main__":
    main()
