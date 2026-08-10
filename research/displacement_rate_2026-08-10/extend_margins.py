"""Extended margin sweep — the 5/10/20/30% grid sits below the 25th percentile
of the observed gap distribution (p25 = 0.372, p50 = 0.602), so it cannot
locate an operating point. Sweeps far wider to find where firing stops and
whether the cost ever becomes tolerable. Read-only; matrices cached so this
need not be rebuilt again.
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"c:\GitHub\investment-app-displacement")
sys.path.insert(0, str(HERE))

import yaml  # noqa: E402
from analysis import universe  # noqa: E402
from analysis.decision import resolve_min_momentum_floor  # noqa: E402
from cross_sectional_signal import compute_cross_sectional_rank_series  # noqa: E402

REF = HERE.parent / "relative_ranking_prereg_2026-08-09"
with open(r"c:\GitHub\investment-app-displacement\config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)
FLOOR = resolve_min_momentum_floor(CONFIG)
FX = 0.0030
MARGINS = [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00, 5.00]

MAT = HERE / "_matrices.npz"
trades = pd.read_csv(REF / "trades_pool2_clean_B.csv", parse_dates=["entry_date", "exit_date"])
panel = pd.read_csv(REF / "panel_pool2_clean_B.csv", parse_dates=["date"])
all_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
n_days = len(all_dates)

if MAT.exists():
    z = np.load(MAT, allow_pickle=True)
    rank_m, gate_m, held_m = z["rank"], z["gate"], z["held"]
    tickers = list(z["tickers"])
    print("Loaded cached matrices", flush=True)
else:
    with open(HERE / "_cache_pool2_clean.pkl", "rb") as fh:
        blob = pickle.load(fh)
    caches = [c for c in blob["caches"] if c is not None]
    tickers = [c["ticker"] for c in caches]
    rank_m = np.full((len(tickers), n_days), np.nan)
    gate_m = np.zeros((len(tickers), n_days), dtype=bool)
    for i, c in enumerate(caches):
        fr = c["frame"]
        close = fr["close"].astype(float)
        rank_m[i, :] = compute_cross_sectional_rank_series(close).reindex(all_dates, method="ffill").to_numpy()
        sig = fr["signal"].reindex(all_dates).fillna(False).astype(bool).to_numpy()
        mom = fr["momentum"].reindex(all_dates).to_numpy().astype(float)
        gate_m[i, :] = sig & (mom > FLOOR)
        if (i + 1) % 200 == 0:
            print(f"  rank/gate {i+1}/{len(tickers)}", flush=True)
    for i, t in enumerate(tickers):
        gate_m[i, :] &= np.array([universe.is_member(t, d.date()) for d in all_dates], dtype=bool)
        if (i + 1) % 200 == 0:
            print(f"  membership {i+1}/{len(tickers)}", flush=True)
    t_idx = {t: i for i, t in enumerate(tickers)}
    date_pos = {d: i for i, d in enumerate(all_dates)}
    held_m = np.zeros((len(tickers), n_days), dtype=bool)
    for _, r in trades.iterrows():
        i = t_idx.get(r["ticker"])
        a = date_pos.get(pd.Timestamp(r["entry_date"]))
        if i is None or a is None:
            continue
        b = date_pos.get(pd.Timestamp(r["exit_date"]))
        held_m[i, a:(b if b is not None else n_days - 1)] = True
    np.savez_compressed(MAT, rank=rank_m, gate=gate_m, held=held_m, tickers=np.array(tickers))
    print("Built + cached matrices", flush=True)

deployed = panel.set_index("date")["current_deployed_pct"].reindex(all_dates).to_numpy()
near_full = deployed >= 65.0
mean_balance = float(panel["current_balance"].mean())
mean_position = float(trades["position_gbp"].mean())
years = pd.Series(all_dates.year)

fires = {m: np.zeros(n_days, dtype=int) for m in MARGINS}
valid = np.zeros(n_days, dtype=bool)

for j in range(n_days):
    hi = np.where(held_m[:, j])[0]
    if len(hi) == 0:
        continue
    hr = rank_m[hi, j]; hr = hr[~np.isnan(hr)]
    ci = np.where(gate_m[:, j] & ~held_m[:, j])[0]
    if len(ci) == 0 or len(hr) == 0:
        continue
    cr = rank_m[ci, j]; cr = cr[~np.isnan(cr)]
    if len(cr) == 0:
        continue
    valid[j] = True
    cs = np.sort(cr)[::-1]; hs = np.sort(hr)
    lim = min(len(cs), len(hs))
    diffs = cs[:lim] - hs[:lim]
    for m in MARGINS:
        k = 0
        while k < lim and diffs[k] > m:
            k += 1
        fires[m][j] = k

rows = []
for m in MARGINS:
    f = fires[m]
    anyd = (f > 0) & valid
    per_year = pd.Series(f).groupby(years.to_numpy()).sum()
    dpy = float(per_year.mean())
    cost = dpy * 2.0 * mean_position * FX
    rows.append({
        "margin": m,
        "pct_days_fire": round(100.0 * anyd.sum() / max(valid.sum(), 1), 2),
        "pct_nearfull_days_fire": round(100.0 * (anyd & near_full).sum() / max((valid & near_full).sum(), 1), 2),
        "displacements_per_year": round(dpy, 1),
        "median_per_year": int(per_year.median()),
        "max_year": int(per_year.max()),
        "drag_pct_per_year": round(100.0 * cost / mean_balance, 3),
    })

df = pd.DataFrame(rows)
df.to_csv(HERE / "margin_sweep.csv", index=False)
print("\n" + df.to_string(index=False))
print(f"\nmean balance GBP {mean_balance:,.2f}   mean position GBP {mean_position:,.2f}")
print("\nArm B cost-adjusted CAGR for reference: 5.433%  (excess vs ^SP500TR: -5.730pp)")
