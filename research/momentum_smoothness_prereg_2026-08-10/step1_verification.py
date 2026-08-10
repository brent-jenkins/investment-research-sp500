"""Step 1 — verification, per PREREGISTRATION.md §2 and §9.

Runs BEFORE any arm. Every check must pass before a single result is
trusted. Staleness-guard checks use the same synthetic-gap fixture style
tests/test_signals.py already uses for compute_momentum_series().
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from smoothness_signal import (  # noqa: E402
    SMOOTHNESS_WINDOW_DAYS, compute_smoothness_series,
)

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(name)
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _gapped_close_series():
    """Identical construction to tests/test_signals.py:_gapped_close_series()
    — 300 business days, a ~2.4-year gap, then 300 more."""
    dates1 = pd.bdate_range("2015-01-01", periods=300)
    dates2 = pd.bdate_range("2017-06-01", periods=300)
    idx = pd.DatetimeIndex(list(dates1) + list(dates2))
    vals = list(range(100, 400)) + list(range(400, 700))
    return pd.Series(vals, index=idx, dtype=float)


print("=" * 78)
print("STEP 1 — VERIFICATION (smoothness signal)")
print("=" * 78)

# ---------------------------------------------------------------- staleness
print("\n§2 staleness guard")
close = _gapped_close_series()
sm = compute_smoothness_series(close)

check("NaN immediately after a real gap (row 300)", bool(pd.isna(sm.iloc[300])))
check("NaN still 10 rows past the gap (row 310)", bool(pd.isna(sm.iloc[310])))

# Short halt within tolerance must NOT suppress.
dates1 = pd.bdate_range("2020-01-01", periods=300)
dates2 = pd.bdate_range(dates1[-1] + pd.Timedelta(days=5), periods=100)
idx = pd.DatetimeIndex(list(dates1) + list(dates2))
short = pd.Series([100.0 + i * 0.5 for i in range(400)], index=idx)
sm_short = compute_smoothness_series(short)
check("short halt within tolerance still computes (row 300)",
      bool(pd.notna(sm_short.iloc[300])),
      f"value={sm_short.iloc[300]:.4f}")

# Recovery once a full gapless window exists past the gap.
check("recovers once a full gapless 252-day window exists past the gap",
      bool(pd.notna(sm.iloc[300 + SMOOTHNESS_WINDOW_DAYS])),
      f"value={sm.iloc[300 + SMOOTHNESS_WINDOW_DAYS]:.4f}")

# ------------------------------------------------------------- correctness
print("\n§2 metric correctness — the two cases that decided (i) over (ii)")

n = SMOOTHNESS_WINDOW_DAYS
dates = pd.bdate_range("2020-01-01", periods=n + 1)

# Case 1: a single huge jump, flat every other day.
vals = [100.0] * (n + 1)
for i in range(130, n + 1):
    vals[i] = 350.0                      # one +250% day at index 130
jump = pd.Series(vals, index=dates)
sm_jump = compute_smoothness_series(jump).iloc[-1]

# Case 2: a steady climb to a comparable total.
steady = pd.Series([100.0 * (1.0048 ** i) for i in range(n + 1)], index=dates)
sm_steady = compute_smoothness_series(steady).iloc[-1]

print(f"    single-jump smoothness  = {sm_jump:+.4f}")
print(f"    steady-climb smoothness = {sm_steady:+.4f}")
check("steady climb scores far above a single jump", sm_steady > sm_jump + 0.5,
      f"gap={sm_steady - sm_jump:+.4f}")
check("single jump scores near zero", abs(sm_jump) < 0.05)
check("steady climb scores near the +1.0 ceiling", sm_steady > 0.95)

# Bounds.
check("smoothness bounded within [-1, 1]",
      bool(np.nanmax(np.abs(compute_smoothness_series(steady).to_numpy())) <= 1.0 + 1e-9))

# Sign handling: a steady DOWN move should also score high (consistently
# downward), since the metric is signed by direction. The live gate never
# admits negative momentum, but the metric must be well-defined.
down = pd.Series([100.0 * (0.9952 ** i) for i in range(n + 1)], index=dates)
sm_down = compute_smoothness_series(down).iloc[-1]
check("steady DOWN move also scores high (signed by direction)", sm_down > 0.95,
      f"value={sm_down:+.4f}")

# Insufficient history.
check("NaN before a full window exists",
      bool(pd.isna(compute_smoothness_series(steady).iloc[n - 1])))

print("\n" + "=" * 78)
if FAILURES:
    print(f"VERIFICATION FAILED — {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("ALL VERIFICATION CHECKS PASSED")
print("=" * 78)
