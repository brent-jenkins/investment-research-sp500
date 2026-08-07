"""Step 2: sector-relative momentum. Build a momentum time series for all
503 live candidates (reusing production precompute_ticker() verbatim --
no monkeypatching, no custom universe), assign each a sector, and for
every date compute each sector's own cross-sectional average momentum
(across every candidate with a valid momentum value that date, not just
ones this system happened to trade). sector_relative_momentum = a
ticker's own momentum minus its sector's average momentum on that date --
tests whether beating your sector peers predicts outcome better than
absolute momentum alone (which the strategy already uses for entry/
priority).
"""
import sys
sys.path.insert(0, r"c:\GitHub\investment-app")

import time

import numpy as np
import pandas as pd
from scipy import stats

from analysis import universe
from analysis.backtest import precompute_ticker, slice_ticker
import yaml

OUT_DIR = "research/entry_volume_sector_2026-08-07"
END = pd.Timestamp("2026-08-06")
START = pd.Timestamp("2007-01-01")

with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

sector_map = pd.read_csv(f"{OUT_DIR}/sector_map.csv").set_index("ticker")["sector"].to_dict()

candidates = universe.get_live_stock_candidates(as_of=END.date())
print(f"Building momentum series for {len(candidates)} live candidates...", flush=True)
t0 = time.time()
momentum_series = {}
for i, ticker in enumerate(candidates):
    cached = precompute_ticker(ticker, CONFIG)
    if cached is None:
        continue
    cached = slice_ticker(cached, START, END)
    if cached is None:
        continue
    momentum_series[ticker] = cached["frame"]["momentum"]
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(candidates)} done ({time.time()-t0:.0f}s)", flush=True)
print(f"  {len(momentum_series)}/{len(candidates)} tickers have usable momentum series ({time.time()-t0:.0f}s)\n", flush=True)

# Wide matrix: date x ticker.
wide = pd.DataFrame(momentum_series)
print(f"Wide momentum matrix: {wide.shape}", flush=True)

# Sector-average momentum per date, per sector -- cross-sectional mean
# across every candidate with a valid momentum value that date.
tickers_by_sector: dict[str, list[str]] = {}
for t in wide.columns:
    s = sector_map.get(t)
    if pd.notna(s):
        tickers_by_sector.setdefault(s, []).append(t)

sector_avg = {}
for sector, tickers in tickers_by_sector.items():
    sector_avg[sector] = wide[tickers].mean(axis=1, skipna=True)
sector_avg_df = pd.DataFrame(sector_avg)
sector_avg_df.to_csv(f"{OUT_DIR}/sector_avg_momentum.csv")
print(f"Sector-average momentum matrix: {sector_avg_df.shape}, sectors: {list(sector_avg_df.columns)}\n", flush=True)

# ----------------------------------------------------------------------
# Join to the real baseline trades.
# ----------------------------------------------------------------------
trades = pd.read_csv("research/vix_ma_giveback_2026-08-07/trades_baseline.csv", parse_dates=["entry_date", "exit_date"])

rows = []
for _, tr in trades.iterrows():
    ticker = tr["ticker"]
    entry_date = tr["entry_date"]
    sector = sector_map.get(ticker)
    if pd.isna(sector) or ticker not in wide.columns:
        continue
    if entry_date not in wide.index:
        continue
    own_mom = wide.at[entry_date, ticker]
    if pd.isna(own_mom):
        continue
    if sector not in sector_avg_df.columns or entry_date not in sector_avg_df.index:
        continue
    sec_avg_mom = sector_avg_df.at[entry_date, sector]
    if pd.isna(sec_avg_mom):
        continue
    rows.append({
        "ticker": ticker, "entry_date": entry_date, "exit_reason": tr["exit_reason"],
        "pnl_pct": tr["pnl_pct"], "sector": sector,
        "own_momentum": own_mom, "sector_avg_momentum": sec_avg_mom,
        "sector_relative_momentum": own_mom - sec_avg_mom,
    })

df = pd.DataFrame(rows)
df.to_csv(f"{OUT_DIR}/trades_with_sector_relative_momentum.csv", index=False)
print(f"Joined population: {len(df)}/{len(trades)} trades\n", flush=True)

print("=== Spearman: sector_relative_momentum vs pnl_pct ===", flush=True)
rho_rel, p_rel = stats.spearmanr(df["sector_relative_momentum"], df["pnl_pct"])
print(f"  ALL: rho={rho_rel:.4f} p={p_rel:.4f} n={len(df)}", flush=True)
mh = df[df["exit_reason"] == "MAX_HOLDING"]
rho_rel_mh, p_rel_mh = stats.spearmanr(mh["sector_relative_momentum"], mh["pnl_pct"])
print(f"  MAX_HOLDING only: rho={rho_rel_mh:.4f} p={p_rel_mh:.4f} n={len(mh)}", flush=True)

print("\n=== Spearman: own_momentum (absolute, what the strategy already uses) vs pnl_pct -- for comparison ===", flush=True)
rho_abs, p_abs = stats.spearmanr(df["own_momentum"], df["pnl_pct"])
print(f"  ALL: rho={rho_abs:.4f} p={p_abs:.4f} n={len(df)}", flush=True)
rho_abs_mh, p_abs_mh = stats.spearmanr(mh["own_momentum"], mh["pnl_pct"])
print(f"  MAX_HOLDING only: rho={rho_abs_mh:.4f} p={p_abs_mh:.4f} n={len(mh)}", flush=True)

# Does sector-relative momentum add anything BEYOND absolute momentum?
# Partial Spearman controlling for own_momentum (rank-residual method).
def partial_spearman(x, y, z):
    rx, ry, rz = stats.rankdata(x), stats.rankdata(y), stats.rankdata(z)
    bx = np.polyfit(rz, rx, 1)
    by = np.polyfit(rz, ry, 1)
    resid_x = rx - np.polyval(bx, rz)
    resid_y = ry - np.polyval(by, rz)
    return stats.pearsonr(resid_x, resid_y)

r_partial, p_partial = partial_spearman(df["sector_relative_momentum"], df["pnl_pct"], df["own_momentum"])
print(f"\nPartial Spearman(sector_relative_momentum, pnl_pct | own_momentum), ALL: rho={r_partial:.4f} p={p_partial:.4f} n={len(df)}", flush=True)
r_partial_mh, p_partial_mh = partial_spearman(mh["sector_relative_momentum"], mh["pnl_pct"], mh["own_momentum"])
print(f"Partial Spearman(sector_relative_momentum, pnl_pct | own_momentum), MAX_HOLDING: rho={r_partial_mh:.4f} p={p_partial_mh:.4f} n={len(mh)}", flush=True)

# Group comparison: beats-sector vs lags-sector.
df["beats_sector"] = df["sector_relative_momentum"] > 0
beats = df[df["beats_sector"]]["pnl_pct"]
lags = df[~df["beats_sector"]]["pnl_pct"]
u, p = stats.mannwhitneyu(beats, lags)
print(f"\nBeats-sector (n={len(beats)}) mean pnl%={beats.mean()*100:.2f} vs Lags-sector (n={len(lags)}) mean pnl%={lags.mean()*100:.2f} p={p:.4f}", flush=True)

# Outlier robustness.
lo, hi = df["pnl_pct"].quantile([0.01, 0.99])
trimmed = df[(df["pnl_pct"] >= lo) & (df["pnl_pct"] <= hi)]
rho_trim, p_trim = stats.spearmanr(trimmed["sector_relative_momentum"], trimmed["pnl_pct"])
print(f"\nOutlier-trimmed (1%/99%) Spearman ALL: rho={rho_trim:.4f} p={p_trim:.4f} n={len(trimmed)}", flush=True)

# Regime decomposition.
print("\n=== Regime decomposition (sector_relative_momentum) ===", flush=True)
df["era"] = pd.cut(
    df["entry_date"],
    bins=[pd.Timestamp("2007-01-01"), pd.Timestamp("2010-01-01"), pd.Timestamp("2020-01-01"), pd.Timestamp("2026-12-31")],
    labels=["2007-2009 GFC", "2010-2019 bull", "2020-2026 recent"],
)
for era, g in df.groupby("era", observed=True):
    rho_e, p_e = stats.spearmanr(g["sector_relative_momentum"], g["pnl_pct"])
    print(f"  {era}: n={len(g)} rho={rho_e:.4f} p={p_e:.4f}", flush=True)
