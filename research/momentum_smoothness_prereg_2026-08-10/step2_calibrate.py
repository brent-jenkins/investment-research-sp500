"""Step 2 — threshold calibration, per PREREGISTRATION.md §3.

Measures the distribution of the INDEPENDENT VARIABLE ONLY across every
gate-passing candidate-day. No backtest is run; no outcome, P&L, or trade
data is touched. This is why it does not compromise the pre-registration.

Writes CALIBRATION.md with T1/T2/T3 frozen at the 10th/25th/40th percentiles,
rounded to 3dp. They are absolute thresholds thereafter.
"""
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"c:\GitHub\investment-app-smoothness")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from analysis.decision import resolve_min_momentum_floor  # noqa: E402
from smoothness_signal import compute_smoothness_series  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
with open(r"c:\GitHub\investment-app-smoothness\config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)
FLOOR = resolve_min_momentum_floor(CONFIG)


def collect(label):
    with open(OUT_DIR / f"_cache_{label}.pkl", "rb") as fh:
        blob = pickle.load(fh)
    caches = [c for c in blob["caches"] if c is not None]
    print(f"{label}: {len(caches)} usable tickers", flush=True)

    vals, nan_count, gate_days = [], 0, 0
    per_year = {}
    t0 = time.time()
    for c in caches:
        fr = c["frame"]
        close = fr["close"]
        mom = fr["momentum"]
        sig = fr["signal"]
        sm = compute_smoothness_series(close)
        # Gate-passing candidate-days: signal on AND momentum clears the 2%
        # floor -- the same population the live gate admits from. Membership
        # is applied inside the engine per-date; here we take the signal/floor
        # pair, which is what determines whether a smoothness value is ever
        # consulted at all.
        eligible = sig.fillna(False).to_numpy() & (mom.to_numpy() > FLOOR)
        gate_days += int(eligible.sum())
        s = sm.to_numpy()[eligible]
        nan_count += int(np.isnan(s).sum())
        good = s[~np.isnan(s)]
        vals.append(good)
        yrs = close.index.year.to_numpy()[eligible][~np.isnan(s)]
        for y, v in zip(yrs, good):
            per_year.setdefault(int(y), []).append(v)

    allv = np.concatenate(vals) if vals else np.array([])
    print(f"  gate-passing candidate-days: {gate_days:,}", flush=True)
    print(f"  NaN smoothness among them  : {nan_count:,} ({100*nan_count/max(gate_days,1):.3f}%)", flush=True)
    print(f"  usable smoothness values   : {len(allv):,}  ({time.time()-t0:.0f}s)", flush=True)
    return allv, per_year, gate_days, nan_count


print("=" * 78)
print("STEP 2 — THRESHOLD CALIBRATION (independent variable only)")
print("=" * 78)

p2, p2_yr, p2_days, p2_nan = collect("pool2_clean")
p1, p1_yr, p1_days, p1_nan = collect("pool1")

pooled = np.concatenate([p2, p1])
print(f"\nPooled usable values: {len(pooled):,}")

qs = [1, 5, 10, 25, 40, 50, 60, 75, 90, 99]
print("\nPooled distribution:")
for q in qs:
    print(f"  p{q:<3d} = {np.percentile(pooled, q):+.4f}")
print(f"  mean = {pooled.mean():+.4f}   std = {pooled.std():+.4f}")
print(f"  min  = {pooled.min():+.4f}   max = {pooled.max():+.4f}")

T1 = round(float(np.percentile(pooled, 10)), 3)
T2 = round(float(np.percentile(pooled, 25)), 3)
T3 = round(float(np.percentile(pooled, 40)), 3)
print(f"\nFROZEN THRESHOLDS:  T1={T1:+.3f}  T2={T2:+.3f}  T3={T3:+.3f}")

lines = [
    "# Threshold calibration — FROZEN (2026-08-10)",
    "",
    "Per PREREGISTRATION.md §3. Distribution of the independent variable only —",
    "no backtest was run and no outcome, P&L, or trade data was touched to produce",
    "these numbers. Frozen before any arm ran.",
    "",
    f"- Pool 2 CLEAN gate-passing candidate-days: **{p2_days:,}** (NaN smoothness: {p2_nan:,}, {100*p2_nan/max(p2_days,1):.3f}%)",
    f"- Pool 1 gate-passing candidate-days: **{p1_days:,}** (NaN smoothness: {p1_nan:,}, {100*p1_nan/max(p1_days,1):.3f}%)",
    f"- Pooled usable smoothness values: **{len(pooled):,}**",
    "",
    "## Pooled distribution",
    "",
    "| quantile | value |",
    "|---|---|",
]
for q in qs:
    lines.append(f"| p{q} | {np.percentile(pooled, q):+.4f} |")
lines += [
    f"| mean | {pooled.mean():+.4f} |",
    f"| std | {pooled.std():+.4f} |",
    "",
    "## Frozen thresholds (absolute from here on)",
    "",
    "| arm | nominal percentile | threshold |",
    "|---|---|---|",
    f"| B1 | p10 | **{T1:+.3f}** |",
    f"| B2 | p25 | **{T2:+.3f}** |",
    f"| B3 | p40 | **{T3:+.3f}** |",
    "",
    "A candidate is excluded from candidacy when `smoothness < T`. Realised",
    "exclusion will drift from the nominal 10/25/40% across eras — that drift is a",
    "reported outcome (§6), not a defect.",
    "",
    "## Exclusion rate by year (pooled, at each frozen threshold)",
    "",
    "| year | n | < T1 | < T2 | < T3 |",
    "|---|---|---|---|---|",
]
years = sorted(set(p2_yr) | set(p1_yr))
for y in years:
    v = np.array(p2_yr.get(y, []) + p1_yr.get(y, []))
    if len(v) == 0:
        continue
    lines.append(f"| {y} | {len(v):,} | {100*(v<T1).mean():.1f}% | {100*(v<T2).mean():.1f}% | {100*(v<T3).mean():.1f}% |")

(OUT_DIR / "CALIBRATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nWrote {OUT_DIR / 'CALIBRATION.md'}")

with open(OUT_DIR / "_thresholds.pkl", "wb") as fh:
    pickle.dump({"T1": T1, "T2": T2, "T3": T3}, fh)
print("Frozen to _thresholds.pkl")
