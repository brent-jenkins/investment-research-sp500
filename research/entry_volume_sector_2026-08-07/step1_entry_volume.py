"""Step 1: entry-TIME relative volume vs. trade outcome -- a genuinely
different question from momentum_volume_reeval_cap_2026-08-06 (already
researched, rejected), which tested relative volume AT THE 30-DAY
MAX_HOLDING CAP POINT as a re-evaluation signal. This tests the classic
"volume confirms the breakout" heuristic: is the entry itself happening
on above-average volume, and does that predict a better trade?

rel_volume(entry) = volume[entry_date] / mean(volume[entry_date-20 .. entry_date-1])
(trailing 20 trading days, excluding entry day itself -- identical
windowing convention to the already-tested exit-point signal, for
comparability).

Population: the 5,397 real baseline trades (today's real config,
unmodified production engine).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import numpy as np
import pandas as pd
from scipy import stats

from data.database import get_engine
from sqlalchemy import text

OUT_DIR = "research/entry_volume_sector_2026-08-07"

trades = pd.read_csv("research/vix_ma_giveback_2026-08-07/trades_baseline.csv", parse_dates=["entry_date", "exit_date"])
engine = get_engine()


def rel_volume_at_entry(ticker: str, entry_date: pd.Timestamp):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT date, volume FROM price_data WHERE ticker=:t AND date<=:d ORDER BY date DESC LIMIT 21"),
            {"t": ticker, "d": entry_date.isoformat()},
        ).fetchall()
    if len(rows) < 21:
        return None
    rows = sorted(rows, key=lambda r: r[0])  # ascending
    entry_vol = rows[-1][1]
    prior_vols = [r[1] for r in rows[:-1]]
    if entry_vol is None or any(v is None for v in prior_vols):
        return None
    mean_prior = np.mean(prior_vols)
    if mean_prior <= 0:
        return None
    # Sanity: last row's date should equal entry_date (or the trading day
    # on/before it -- price_data is trading-day only, entry_date always
    # a real trading day for a real trade).
    return float(entry_vol) / float(mean_prior)


rel_vols = []
for _, tr in trades.iterrows():
    rv = rel_volume_at_entry(tr["ticker"], tr["entry_date"])
    rel_vols.append(rv)

trades["rel_volume_at_entry"] = rel_vols
n_missing = trades["rel_volume_at_entry"].isna().sum()
print(f"Trades: {len(trades)}  Missing rel_volume (insufficient history): {n_missing} ({n_missing/len(trades):.1%})", flush=True)
scoped = trades.dropna(subset=["rel_volume_at_entry"]).copy()
scoped.to_csv(f"{OUT_DIR}/trades_with_entry_volume.csv", index=False)
print(f"Scoped population: {len(scoped)}\n", flush=True)

print("=== Descriptive: rel_volume_at_entry distribution ===", flush=True)
print(scoped["rel_volume_at_entry"].describe(), flush=True)

# Band by quartile, same convention as prior investigations in this project.
scoped["vol_band"] = pd.qcut(scoped["rel_volume_at_entry"], 4, labels=["Q1_low", "Q2", "Q3", "Q4_high"])
print("\n=== Group summary by entry-volume quartile ===", flush=True)
print(scoped.groupby("vol_band", observed=True)["pnl_pct"].agg(["mean", "median", "count"]), flush=True)

# Two-group test: bottom quartile (thin volume) vs top quartile (heavy volume).
low = scoped[scoped["vol_band"] == "Q1_low"]["pnl_pct"]
high = scoped[scoped["vol_band"] == "Q4_high"]["pnl_pct"]
u, p = stats.mannwhitneyu(low, high)
print(f"\nQ1(low) vs Q4(high) Mann-Whitney: low mean={low.mean()*100:.2f}% high mean={high.mean()*100:.2f}% p={p:.4f}", flush=True)

# Spearman, whole population and MAX_HOLDING-only (cleanest test, no
# exit-reason-composition contamination -- same rationale ma_extension_
# ceiling_2026-08-06 used).
rho_all, p_all = stats.spearmanr(scoped["rel_volume_at_entry"], scoped["pnl_pct"])
print(f"\nSpearman(rel_volume_at_entry, pnl_pct), ALL: rho={rho_all:.4f} p={p_all:.4f} n={len(scoped)}", flush=True)

mh = scoped[scoped["exit_reason"] == "MAX_HOLDING"]
rho_mh, p_mh = stats.spearmanr(mh["rel_volume_at_entry"], mh["pnl_pct"])
print(f"Spearman(rel_volume_at_entry, pnl_pct), MAX_HOLDING only: rho={rho_mh:.4f} p={p_mh:.4f} n={len(mh)}", flush=True)

# Partial correlation controlling for momentum-at-entry, same rank-
# residual method ma_extension_ceiling_2026-08-06 used.
def partial_spearman(x, y, z):
    rx = stats.rankdata(x)
    ry = stats.rankdata(y)
    rz = stats.rankdata(z)
    bx = np.polyfit(rz, rx, 1)
    by = np.polyfit(rz, ry, 1)
    resid_x = rx - np.polyval(bx, rz)
    resid_y = ry - np.polyval(by, rz)
    return stats.pearsonr(resid_x, resid_y)

if "momentum_at_entry_pct" not in scoped.columns:
    # Not present in trades_baseline.csv -- momentum isn't carried on the
    # engine's own trade dict. Skipped; noted in the report.
    print("\n(momentum_at_entry_pct not available in trades_baseline.csv -- partial-correlation check skipped)", flush=True)

# Outlier robustness -- trim top/bottom 1% of pnl_pct.
lo, hi = scoped["pnl_pct"].quantile([0.01, 0.99])
trimmed = scoped[(scoped["pnl_pct"] >= lo) & (scoped["pnl_pct"] <= hi)]
rho_trim, p_trim = stats.spearmanr(trimmed["rel_volume_at_entry"], trimmed["pnl_pct"])
print(f"\nOutlier-trimmed (1%/99%) Spearman ALL: rho={rho_trim:.4f} p={p_trim:.4f} n={len(trimmed)}", flush=True)

# Regime decomposition.
print("\n=== Regime decomposition ===", flush=True)
scoped["era"] = pd.cut(
    scoped["entry_date"],
    bins=[pd.Timestamp("2007-01-01"), pd.Timestamp("2010-01-01"), pd.Timestamp("2020-01-01"), pd.Timestamp("2026-12-31")],
    labels=["2007-2009 GFC", "2010-2019 bull", "2020-2026 recent"],
)
for era, g in scoped.groupby("era", observed=True):
    rho_e, p_e = stats.spearmanr(g["rel_volume_at_entry"], g["pnl_pct"])
    print(f"  {era}: n={len(g)} rho={rho_e:.4f} p={p_e:.4f}", flush=True)
