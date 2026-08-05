"""Step 4 check 3 -- re-check research/concentration_risk_2026-08-05's own
indep_gap_60d joint-risk metric for baseline and each sizing-multiplier
schedule, using that investigation's own formula (analyze_concentration.py),
reused directly, not reinvented -- the identical adaptation pattern
research/deployment_cap_tradeoff_2026-08-05/joint_risk_engine.py already
used successfully for a sibling investigation, ported here:

    indep_gap_60d = P_real/P_ind - 1
    P_real = sqrt(w' Cov w)                 # realized 60d covariance of
                                             # open positions' daily log
                                             # returns, weighted by
                                             # entry-time position_gbp
    P_ind  = sqrt(sum(w_i^2 * sigma_i^2))   # zero-cross-correlation
                                             # (independence) assumption

Computed from EACH schedule's OWN real day-by-day open-position weights --
not extrapolated from baseline -- reconstructed from that schedule's own
reset-at-boundary trade list (reset_sweep_worker.py's output, test period
2020-01-01 to 2026-08-03, the SAME convention as the headline sweep table).
"""
import sys
import json
import pickle
import time

sys.path.insert(0, r'c:\GitHub\investment-research-sp500')

import numpy as np
import pandas as pd

RESULTS_DIR = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05\results'
CACHE_PATH = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05\caches.pkl'
WLEN = 60
MIN_OBS = 35  # matches concentration_risk_2026-08-05's own 60d MIN_OBS

ARMS = ["baseline", "modest", "aggressive", "whole_dist"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build_price_panel(caches, master_cal):
    log("Building price panel from cached full-history frames...")
    price_panel = pd.DataFrame(index=master_cal)
    for c in caches:
        if c is None:
            continue
        ticker = c["ticker"]
        frame = c["frame"]
        s = frame["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        price_panel[ticker] = s.reindex(master_cal).ffill(limit=5)
    log(f"Price panel shape: {price_panel.shape}")
    return price_panel


def dist(series):
    s = pd.Series(series).dropna()
    if s.empty:
        return None
    return {
        "n": int(len(s)), "mean": float(s.mean()), "std": float(s.std()),
        "p5": float(s.quantile(0.05)), "p25": float(s.quantile(0.25)),
        "p50": float(s.quantile(0.50)), "p75": float(s.quantile(0.75)),
        "p95": float(s.quantile(0.95)), "min": float(s.min()), "max": float(s.max()),
    }


def compute_indep_gap_for_trades(trades, log_ret_panel, master_cal, day_pos):
    if not trades:
        return {"error": "no trades"}, pd.DataFrame()

    tdf = pd.DataFrame(trades)
    tdf["entry_date"] = pd.to_datetime(tdf["entry_date"])
    tdf["exit_date"] = pd.to_datetime(tdf["exit_date"])
    tdf["trade_id"] = np.arange(len(tdf))

    opens_by_date, closes_by_date = {}, {}
    for row in tdf.itertuples():
        opens_by_date.setdefault(row.entry_date, []).append(row)
        closes_by_date.setdefault(row.exit_date, []).append(row)

    active = {}
    rows = []
    cal_start = tdf["entry_date"].min()
    cal_end = tdf["exit_date"].max()
    window_cal = master_cal[(master_cal >= cal_start) & (master_cal <= cal_end)]

    for day in window_cal:
        pos_idx = day_pos[day]
        if day in closes_by_date:
            for row in closes_by_date[day]:
                tk = row.ticker
                if tk in active and active[tk]["trade_id"] == row.trade_id:
                    del active[tk]
        if day in opens_by_date:
            for row in opens_by_date[day]:
                active[row.ticker] = {"position_gbp": row.position_gbp, "trade_id": row.trade_id}

        n_open = len(active)
        open_tickers = list(active.keys())
        indep_gap = None
        dr = None
        n_pairs = None
        if n_open >= 2:
            lo = max(0, pos_idx - WLEN)
            hi = pos_idx  # strictly before today
            if hi - lo >= MIN_OBS:
                sub = log_ret_panel.iloc[lo:hi][open_tickers]
                valid_cols = [c for c in open_tickers if c in sub.columns and sub[c].notna().sum() >= MIN_OBS]
                if len(valid_cols) >= 2:
                    weights = np.array([active[c]["position_gbp"] for c in valid_cols])
                    weights = weights / weights.sum()
                    vols = sub[valid_cols].std().values
                    cov = sub[valid_cols].cov().values
                    p_real = float(np.sqrt(weights @ cov @ weights))
                    p_ind = float(np.sqrt(np.sum((weights ** 2) * (vols ** 2))))
                    weighted_avg_vol = float(np.sum(weights * vols))
                    if p_real > 0 and p_ind > 0:
                        indep_gap = p_real / p_ind - 1.0
                        dr = weighted_avg_vol / p_real
                    n_pairs = len(valid_cols) * (len(valid_cols) - 1) // 2

        rows.append({"date": day, "n_open": n_open, "indep_gap_60d": indep_gap, "dr_60d": dr, "n_pairs_60d": n_pairs})

    panel = pd.DataFrame(rows)
    n_days = len(panel)
    freq = {f"pct_days_n_open_ge_{k}": round(100.0 * (panel["n_open"] >= k).sum() / n_days, 2) for k in [1, 2, 3, 4, 5]}
    summary = {
        "n_days_total": n_days,
        "frequency": freq,
        "indep_gap_60d_distribution_n_open_ge_2": dist(panel.loc[panel["n_open"] >= 2, "indep_gap_60d"]),
        "dr_60d_distribution_n_open_ge_2": dist(panel.loc[panel["n_open"] >= 2, "dr_60d"]),
        "mean_n_open": float(panel["n_open"].mean()),
        "max_n_open": int(panel["n_open"].max()),
    }
    return summary, panel


def main():
    with open(CACHE_PATH, 'rb') as f:
        cache_bundle = pickle.load(f)
    caches = cache_bundle["caches"]
    gspc_close_full = cache_bundle["gspc_close_full"]

    master_cal_full = gspc_close_full.index
    buffer_start = pd.Timestamp("2019-08-01")
    master_cal = master_cal_full[(master_cal_full >= buffer_start) & (master_cal_full <= pd.Timestamp("2026-08-03"))]
    day_pos = {d: i for i, d in enumerate(master_cal)}

    price_panel = build_price_panel(caches, master_cal)
    log_ret_panel = np.log(price_panel / price_panel.shift(1))

    all_results = {}
    for arm in ARMS:
        result_path = f"{RESULTS_DIR}\\reset_{arm}.json"
        try:
            with open(result_path) as f:
                trades = json.load(f)
        except FileNotFoundError:
            log(f"{arm}: result file not found yet, skipping")
            continue
        log(f"{arm}: {len(trades)} trades, computing indep_gap_60d...")
        t0 = time.time()
        summary, panel = compute_indep_gap_for_trades(trades, log_ret_panel, master_cal, day_pos)
        log(f"  done in {time.time()-t0:.0f}s: mean_indep_gap={summary.get('indep_gap_60d_distribution_n_open_ge_2', {}).get('mean')}")
        all_results[arm] = {"summary": summary}
        panel.to_csv(f"{RESULTS_DIR}\\{arm}_daily_panel.csv", index=False)

    out_path = r'c:\GitHub\investment-research-sp500\research\momentum_sizing_multiplier_2026-08-05\joint_risk_gap_results.json'
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    log(f"Saved {out_path}")
    log(json.dumps({k: v["summary"].get("indep_gap_60d_distribution_n_open_ge_2") for k, v in all_results.items()}, indent=2))


if __name__ == "__main__":
    main()
