"""Does risk-adjusted ranking reduce the concentration investigation's own
joint-risk measure, even though it was not explicitly designed to?

Reuses the EXACT indep_gap_60d / diversification-ratio formula from
research/concentration_risk_2026-08-05/analyze_concentration.py (lines
195-235), quoted here verbatim rather than paraphrased, per CLAUDE.md's
research-reproducibility standard:

    weights  = position_gbp per open ticker, normalized to sum 1
    vols     = sub[valid_cols].std().values           # 60d log-return window
    cov      = sub[valid_cols].cov().values
    p_real   = sqrt(weights @ cov @ weights)
    p_ind    = sqrt(sum(weights**2 * vols**2))
    dr       = sum(weights * vols) / p_real
    indep_gap = p_real / p_ind - 1.0

CONVENTIONS (stated explicitly, not left implicit):
- "Open" = entry_date <= day < exit_date (half-open), identical to the
  originating investigation's own stated choice.
- All windows use data STRICTLY BEFORE the evaluated day (no look-ahead).
- PRIMARY_WINDOW "60d", MIN_OBS 35 -- identical constants to the original.
- The underlying trade lists here are RESET-AT-BOUNDARY test-period runs
  (this is a variant comparison), which differs deliberately from the
  original investigation's continuous-walk trade list. The measure is
  therefore comparable BETWEEN the two arms here, but its absolute level is
  NOT directly comparable to the original's full-history 1.067 figure --
  different window, different convention. Stated so no one reads across.

This script builds no custom universe and monkeypatches nothing -- it is pure
downstream analysis of trade lists the engine already produced, so CLAUDE.md's
"research scripts must verify decision-engine parity" rule is satisfied by the
same reasoning the original investigation recorded for itself.

Usage: python joint_risk_compare.py <raw_json> <ra_json> <out_json>
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from data.ingestion import get_price_df

PRIMARY_WINDOW_LEN = 60
MIN_OBS = 35


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build_panel(trades, price_panel, log_ret_panel, master_cal):
    """One row per trading day: n_open, avg pairwise corr, dr, indep_gap."""
    for i, t in enumerate(trades):
        t["trade_id"] = i
    opens, closes = {}, {}
    for t in trades:
        opens.setdefault(pd.Timestamp(t["entry_date"]), []).append(t)
        closes.setdefault(pd.Timestamp(t["exit_date"]), []).append(t)

    active, rows = {}, []
    for pos_idx, day in enumerate(master_cal):
        for t in closes.get(day, []):
            if t["ticker"] in active and active[t["ticker"]]["trade_id"] == t["trade_id"]:
                del active[t["ticker"]]
        for t in opens.get(day, []):
            active[t["ticker"]] = {"position_gbp": float(t["position_gbp"]),
                                   "trade_id": t["trade_id"]}

        n_open = len(active)
        open_tickers = [t for t in active if t in log_ret_panel.columns]
        dr = indep_gap = avg_c = None

        if len(open_tickers) >= 2:
            lo = max(0, pos_idx - PRIMARY_WINDOW_LEN)
            hi = pos_idx  # strictly before today
            if hi - lo >= MIN_OBS:
                sub = log_ret_panel.iloc[lo:hi][open_tickers]
                valid = [c for c in open_tickers if sub[c].notna().sum() >= MIN_OBS]
                if len(valid) >= 2:
                    cm = sub[valid].corr(min_periods=MIN_OBS)
                    iu = np.triu_indices_from(cm.values, k=1)
                    pv = cm.values[iu]
                    pv = pv[~np.isnan(pv)]
                    if len(pv):
                        avg_c = float(np.mean(pv))
                    w = np.array([active[c]["position_gbp"] for c in valid], dtype=float)
                    w = w / w.sum()
                    vols = sub[valid].std().values
                    cov = sub[valid].cov().values
                    p_real = float(np.sqrt(w @ cov @ w))
                    p_ind = float(np.sqrt(np.sum((w ** 2) * (vols ** 2))))
                    if p_real > 0:
                        dr = float(np.sum(w * vols)) / p_real
                        if p_ind > 0:
                            indep_gap = p_real / p_ind - 1.0

        rows.append({"date": day, "n_open": n_open, "avg_corr_60d": avg_c,
                     "dr_60d": dr, "indep_gap_60d": indep_gap})
    return pd.DataFrame(rows)


def dist(s):
    s = pd.Series(s).dropna()
    if s.empty:
        return None
    return {"n": int(s.size), "mean": float(s.mean()), "median": float(s.median()),
            "p75": float(s.quantile(0.75)), "p95": float(s.quantile(0.95)),
            "max": float(s.max())}


def main():
    raw_path, ra_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    arms = {}
    for label, p in (("raw", raw_path), ("risk_adjusted", ra_path)):
        arms[label] = json.load(open(p))["trades"]
        log(f"{label}: {len(arms[label])} trades")

    tickers = sorted({t["ticker"] for a in arms.values() for t in a})
    log(f"loading prices for {len(tickers)} tickers")
    closes = {}
    for tk in tickers:
        df = get_price_df(tk, days=8000)
        if df is None or df.empty:
            continue
        s = df.set_index("date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        closes[tk] = s[~s.index.duplicated(keep="last")]
    price_panel = pd.DataFrame(closes).sort_index()
    log_ret_panel = np.log(price_panel / price_panel.shift(1))

    all_days = sorted({pd.Timestamp(t["entry_date"]) for a in arms.values() for t in a} |
                      {pd.Timestamp(t["exit_date"]) for a in arms.values() for t in a})
    master_cal = price_panel.loc[
        (price_panel.index >= min(all_days)) & (price_panel.index <= max(all_days))
    ].index
    log(f"master calendar: {len(master_cal)} days")

    out = {
        "measure": "indep_gap_60d = p_real/p_ind - 1 (formula quoted verbatim from "
                   "research/concentration_risk_2026-08-05/analyze_concentration.py)",
        "convention": "reset_at_boundary test-period trade lists; NOT comparable in "
                      "absolute level to the original continuous-walk 1.067 figure",
        "primary_window_len": PRIMARY_WINDOW_LEN, "min_obs": MIN_OBS,
        "arms": {},
    }
    panels = {}
    for label, trades in arms.items():
        log(f"building panel: {label}")
        panel = build_panel(trades, price_panel, log_ret_panel, master_cal)
        panels[label] = panel
        ge2 = panel[panel["n_open"] >= 2]
        out["arms"][label] = {
            "n_trades": len(trades),
            "days": int(len(panel)),
            "days_n_open_ge2": int(len(ge2)),
            "mean_n_open": float(panel["n_open"].mean()),
            "indep_gap_60d": dist(ge2["indep_gap_60d"]),
            "dr_60d": dist(ge2["dr_60d"]),
            "avg_corr_60d": dist(ge2["avg_corr_60d"]),
        }
        panel.to_csv(Path(out_path).parent / f"daily_panel_{label}.csv", index=False)

    a, b = out["arms"]["raw"], out["arms"]["risk_adjusted"]
    if a["indep_gap_60d"] and b["indep_gap_60d"]:
        out["delta"] = {
            "indep_gap_mean": b["indep_gap_60d"]["mean"] - a["indep_gap_60d"]["mean"],
            "indep_gap_mean_pct_change": (b["indep_gap_60d"]["mean"] / a["indep_gap_60d"]["mean"] - 1) * 100,
            "indep_gap_p95": b["indep_gap_60d"]["p95"] - a["indep_gap_60d"]["p95"],
            "avg_corr_mean": b["avg_corr_60d"]["mean"] - a["avg_corr_60d"]["mean"],
            "mean_n_open": b["mean_n_open"] - a["mean_n_open"],
        }
        # Paired day-by-day test: same calendar, both arms -> a paired
        # comparison is legitimate and far more powerful than comparing
        # two independent distribution summaries.
        m = panels["raw"][["date", "indep_gap_60d"]].merge(
            panels["risk_adjusted"][["date", "indep_gap_60d"]], on="date",
            suffixes=("_raw", "_ra")).dropna()
        d = m["indep_gap_60d_ra"] - m["indep_gap_60d_raw"]
        out["paired_daily"] = {
            "n_days_both_defined": int(len(d)),
            "mean_diff": float(d.mean()),
            "median_diff": float(d.median()),
            "pct_days_variant_lower": float((d < 0).mean() * 100),
        }

    json.dump(out, open(out_path, "w"), indent=2, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "arms"}, indent=2, default=str))
    for k, v in out["arms"].items():
        print(f"\n{k}: indep_gap={v['indep_gap_60d']}\n   corr={v['avg_corr_60d']}\n   mean_n_open={v['mean_n_open']:.2f}")


if __name__ == "__main__":
    main()
