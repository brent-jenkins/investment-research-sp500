"""Displacement-rate analysis — READ-ONLY. No engine, no arms, no adoption.

Measures how often a displacement mechanism WOULD have had an opportunity to
fire on Arm B's real, preserved trade history, and what it would have cost.

SOURCES, and what had to be recomputed:
  - Held positions per day  : Arm B trades (preserved) -- entry_date/exit_date
                              spans, used directly.
  - Deployment per day      : Arm B panel (preserved) -- used directly.
  - Rank per ticker per day : GENUINELY ABSENT from the preserved output and
                              recomputed deterministically from the price cache
                              via the unmodified cross_sectional_signal.py. This
                              is not a backtest -- no admission, sizing, capacity
                              or exit logic runs; it is the same pure function
                              the engine calls, evaluated over the same frames.
  - Gate-passing per day    : likewise recomputed (signal AND momentum > floor
                              AND is_member), from the same cached frames.

MARGIN UNITS, stated explicitly: the ranking signal is the 12-minus-1 return
(0.45 = +45%), so a "margin of 10%" means the candidate's rank signal exceeds
the held position's by more than 0.10 in absolute terms -- percentage POINTS of
trailing return, the same units as the existing 2% momentum floor. Not a
relative ratio and not a rank-position difference.
"""
import json
import pickle
import sys
from collections import defaultdict
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
FX_ROUND_TRIP_FEE = 0.0030          # established template
MARGINS = [0.05, 0.10, 0.20, 0.30]
NEAR_FULL_PCT = 65.0                # "at or near full deployment", see report

print("Loading Arm B preserved output + price cache...", flush=True)
trades = pd.read_csv(REF / "trades_pool2_clean_B.csv", parse_dates=["entry_date", "exit_date"])
panel = pd.read_csv(REF / "panel_pool2_clean_B.csv", parse_dates=["date"])
with open(HERE / "_cache_pool2_clean.pkl", "rb") as fh:
    blob = pickle.load(fh)
caches = [c for c in blob["caches"] if c is not None]
print(f"  {len(trades)} Arm B trades, {len(panel)} panel days, {len(caches)} tickers", flush=True)

all_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
date_pos = {d: i for i, d in enumerate(all_dates)}
n_days = len(all_dates)

# ---- rank + gate matrices (tickers x days) -------------------------------
print("Recomputing rank + gate matrices (pure functions, no backtest)...", flush=True)
tickers = [c["ticker"] for c in caches]
rank_m = np.full((len(tickers), n_days), np.nan)
gate_m = np.zeros((len(tickers), n_days), dtype=bool)

for i, c in enumerate(caches):
    fr = c["frame"]
    close = fr["close"].astype(float)
    rk = compute_cross_sectional_rank_series(close).reindex(all_dates, method="ffill")
    rank_m[i, :] = rk.to_numpy()
    sig = fr["signal"].reindex(all_dates).fillna(False).astype(bool).to_numpy()
    mom = fr["momentum"].reindex(all_dates).to_numpy().astype(float)
    gate_m[i, :] = sig & (mom > FLOOR)
    if (i + 1) % 200 == 0:
        print(f"  {i+1}/{len(tickers)}", flush=True)

# Membership gate, per ticker per day (ETFs exempt; none in this pool).
print("Applying membership gate...", flush=True)
for i, t in enumerate(tickers):
    active = np.array([universe.is_member(t, d.date()) for d in all_dates], dtype=bool)
    gate_m[i, :] &= active

t_idx = {t: i for i, t in enumerate(tickers)}

# ---- held matrix from the preserved trades -------------------------------
held_m = np.zeros((len(tickers), n_days), dtype=bool)
for _, r in trades.iterrows():
    i = t_idx.get(r["ticker"])
    if i is None:
        continue
    a = date_pos.get(pd.Timestamp(r["entry_date"]))
    b = date_pos.get(pd.Timestamp(r["exit_date"]))
    if a is None:
        continue
    b = b if b is not None else n_days - 1
    held_m[i, a:b] = True          # held from entry up to (not incl.) exit day

deployed_pct = panel.set_index("date")["current_deployed_pct"].reindex(all_dates).to_numpy()
mean_balance = float(panel["current_balance"].mean())
mean_position = float(trades["position_gbp"].mean())
near_full = deployed_pct >= NEAR_FULL_PCT

# ---- per-day scan ---------------------------------------------------------
print("Scanning days...", flush=True)
day_rows = []
gap_samples = []          # (best unadmitted - worst held) on days with both
all_pair_gaps = []        # best unadmitted - each held, sampled
fire_counts = {m: np.zeros(n_days, dtype=int) for m in MARGINS}
first_displaceable = defaultdict(list)   # margin -> days-from-entry

for j in range(n_days):
    held_i = np.where(held_m[:, j])[0]
    if len(held_i) == 0:
        day_rows.append((all_dates[j], 0, 0, np.nan, np.nan))
        continue
    cand_i = np.where(gate_m[:, j] & ~held_m[:, j])[0]
    held_r = rank_m[held_i, j]
    ok_h = ~np.isnan(held_r)
    held_i, held_r = held_i[ok_h], held_r[ok_h]
    if len(cand_i) == 0 or len(held_r) == 0:
        day_rows.append((all_dates[j], len(held_i), len(cand_i), np.nan, np.nan))
        continue
    cand_r = rank_m[cand_i, j]
    cand_r = cand_r[~np.isnan(cand_r)]
    if len(cand_r) == 0:
        day_rows.append((all_dates[j], len(held_i), 0, np.nan, np.nan))
        continue

    best_cand = float(np.max(cand_r))
    worst_held = float(np.min(held_r))
    gap_samples.append((all_dates[j], best_cand - worst_held, best_cand, worst_held,
                        len(held_i), len(cand_r), deployed_pct[j]))
    if j % 25 == 0:
        all_pair_gaps.extend((best_cand - held_r).tolist())

    # Greedy max displacements: pair the strongest spare candidates against the
    # weakest holdings while the gap clears the margin. This is an UPPER BOUND
    # on how many could fire that day, not a policy proposal.
    cs = np.sort(cand_r)[::-1]
    hs = np.sort(held_r)
    for m in MARGINS:
        k = 0
        while k < len(cs) and k < len(hs) and (cs[k] - hs[k]) > m:
            k += 1
        fire_counts[m][j] = k

    day_rows.append((all_dates[j], len(held_i), len(cand_r), best_cand, worst_held))

days = pd.DataFrame(day_rows, columns=["date", "n_held", "n_cand", "best_cand", "worst_held"])
days["deployed_pct"] = deployed_pct
days["near_full"] = near_full

# ---- Q5: how long does a position stay competitive? ----------------------
print("Computing competitiveness duration...", flush=True)
for _, r in trades.iterrows():
    i = t_idx.get(r["ticker"])
    a = date_pos.get(pd.Timestamp(r["entry_date"]))
    b = date_pos.get(pd.Timestamp(r["exit_date"]))
    if i is None or a is None:
        continue
    b = b if b is not None else n_days - 1
    for m in MARGINS:
        hit = None
        for j in range(a, min(b, n_days)):
            bc = days["best_cand"].iloc[j]
            hr = rank_m[i, j]
            if np.isnan(bc) or np.isnan(hr):
                continue
            if (bc - hr) > m:
                hit = j - a
                break
        first_displaceable[m].append(hit if hit is not None else -1)

# ---- results --------------------------------------------------------------
out = {"meta": {
    "arm": "Arm B (relative-rank admission), Pool 2 CLEAN",
    "trading_days": int(n_days), "trades": int(len(trades)),
    "mean_balance_gbp": round(mean_balance, 2),
    "mean_position_gbp": round(mean_position, 2),
    "fx_round_trip_fee": FX_ROUND_TRIP_FEE,
    "near_full_threshold_pct": NEAR_FULL_PCT,
    "near_full_days": int(near_full.sum()),
    "margin_units": "absolute difference in the 12-minus-1 rank signal (percentage points of trailing return)",
}}

gs = pd.DataFrame(gap_samples, columns=["date", "gap", "best_cand", "worst_held",
                                        "n_held", "n_cand", "deployed_pct"])
valid = days["best_cand"].notna()
years = days["date"].dt.year

q1, q2, q3 = {}, {}, {}
for m in MARGINS:
    fires = fire_counts[m]
    any_day = (fires > 0) & valid.to_numpy()
    q1[f"{m:.0%}"] = {
        "days_with_opportunity": int(any_day.sum()),
        "pct_of_all_days": round(100.0 * any_day.sum() / max(valid.sum(), 1), 2),
        "pct_of_near_full_days": round(
            100.0 * (any_day & near_full).sum() / max((valid.to_numpy() & near_full).sum(), 1), 2),
    }
    per_year = pd.Series(fires).groupby(years.to_numpy()).sum()
    q2[f"{m:.0%}"] = {
        "total_displacements": int(fires.sum()),
        "mean_per_year": round(float(per_year.mean()), 1),
        "median_per_year": round(float(per_year.median()), 1),
        "min_year": int(per_year.min()), "max_year": int(per_year.max()),
        "per_year": {int(y): int(v) for y, v in per_year.items()},
    }
    n_disp_yr = float(per_year.mean())
    cost_gbp_yr = n_disp_yr * 2.0 * mean_position * FX_ROUND_TRIP_FEE
    q3[f"{m:.0%}"] = {
        "displacements_per_year": round(n_disp_yr, 1),
        "extra_round_trips_per_year": round(2 * n_disp_yr, 1),
        "cost_gbp_per_year": round(cost_gbp_yr, 2),
        "drag_pct_per_year": round(100.0 * cost_gbp_yr / mean_balance, 3),
    }

out["Q1_opportunity_rate"] = q1
out["Q2_displacements_per_year"] = q2
out["Q3_turnover_cost"] = q3

pair = np.array(all_pair_gaps)
out["Q4_rank_gap_distribution"] = {
    "best_unadmitted_minus_worst_held": {
        f"p{q}": round(float(np.percentile(gs["gap"], q)), 4) for q in [1, 5, 10, 25, 50, 75, 90, 95, 99]
    } | {"mean": round(float(gs["gap"].mean()), 4),
         "pct_positive": round(float((gs["gap"] > 0).mean() * 100), 2)},
    "best_unadmitted_minus_each_held_sampled": {
        f"p{q}": round(float(np.percentile(pair, q)), 4) for q in [1, 5, 10, 25, 50, 75, 90, 95, 99]
    } | {"n_samples": int(len(pair)),
         "pct_positive": round(float((pair > 0).mean() * 100), 2)},
    "mean_n_held": round(float(days["n_held"].mean()), 2),
    "mean_n_eligible_unadmitted": round(float(days["n_cand"].mean()), 2),
}

q5 = {}
for m in MARGINS:
    v = np.array(first_displaceable[m])
    never = int((v == -1).sum())
    hit = v[v >= 0]
    q5[f"{m:.0%}"] = {
        "n_trades": int(len(v)),
        "never_displaceable": never,
        "never_pct": round(100.0 * never / max(len(v), 1), 2),
        "median_days_to_first_displaceable": (round(float(np.median(hit)), 1) if len(hit) else None),
        "pct_displaceable_within_1_day": (round(float((hit <= 1).mean() * 100), 2) if len(hit) else None),
        "pct_displaceable_within_5_days": (round(float((hit <= 5).mean() * 100), 2) if len(hit) else None),
    }
out["Q5_competitiveness_duration"] = q5

(HERE / "results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
gs.to_csv(HERE / "daily_gaps.csv", index=False)
days.to_csv(HERE / "daily_scan.csv", index=False)
print(json.dumps(out, indent=2))
print(f"\nWrote {HERE/'results.json'}")
