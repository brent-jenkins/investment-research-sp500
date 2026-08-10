"""Momentum-smoothness signal — research-only, per PREREGISTRATION.md §2.
NOT part of analysis/signals.py (that module is the live system's signal
layer; this is never read by any live or production-backtest path).

    smoothness(t) = sign(momentum_t) * (n_pos - n_neg) / N

over the N = 252 trading days ending at t, where n_pos/n_neg count strictly
positive/negative daily simple returns and zero-return days count toward N
but toward neither. Higher = smoother: a return accumulated across many
small up-days scores high; the same return delivered in one jump scores
near zero.

This is the negation of Da, Gurun & Warachka's information-discreteness
measure (ID = sgn(PRET) x (%neg - %pos), "Frog in the Pan: Continuous
Information and Momentum", RFS 2014), re-signed so larger means better,
matching every other ranking quantity in this codebase.

WHY NOT PATH EFFICIENCY (net / sum|daily|, Kaufman's Efficiency Ratio) --
rejected at specification time as ACTIVELY WRONG for this hypothesis, not
merely weaker. ER measures DIRECTNESS, not smoothness, and the two invert
on exactly the case this filter exists to catch:
  - a single +250% jump, flat every other day: net 250%, sum|daily| ~250%,
    ER ~= 1.0 -- the MAXIMUM score, because one jump is the most direct
    possible path;
  - a steady climb to the same total over 252 days: in log terms
    net == sum|daily|, ER ~= 1.0 -- also maximum.
ER cannot separate them. It would score the jumpiest possible candidate as
maximally smooth: a check reporting clean for a reason unrelated to what it
is checking, this project's named recurring failure class.

WINDOW: 252, matching analysis/signals.py:MOMENTUM_LOOKBACK_DAYS exactly,
so _MAX_ANCHOR_GAP_DAYS is reused with its existing calibration rather than
a new constant being invented. Stated inconsistency, per §2: arm A ranks on
the 12-minus-1 span (t-252 -> t-21), which excludes the most recent month,
while this window includes it. Accepted because the filter gates CANDIDACY
and the gate is the 252-day momentum; the aligned variant is deferred.

STALENESS GUARD: a day-counting computation across a genuine data gap
returns a REAL, WRONG number rather than NaN -- the identical failure mode
_MAX_ANCHOR_GAP_DAYS exists to prevent for momentum (a synthetic ~16-month
gap once produced a real +170% momentum reading, silently treated as valid).
A 252-row window that actually spans four calendar years counts real returns
from the wrong period and yields a perfectly plausible smoothness value.
"""
import numpy as np
import pandas as pd

from analysis.signals import MOMENTUM_LOOKBACK_DAYS, _MAX_ANCHOR_GAP_DAYS

SMOOTHNESS_WINDOW_DAYS = MOMENTUM_LOOKBACK_DAYS  # 252, deliberately identical


def compute_smoothness_series(close: pd.Series) -> pd.Series:
    """Vectorized smoothness for every date in *close*'s own index.

    NaN wherever the window is unavailable (fewer than
    SMOOTHNESS_WINDOW_DAYS prior rows) or calendar-stale (the window's first
    date more than _MAX_ANCHOR_GAP_DAYS before t). Callers must treat NaN as
    "not assessable this date", matching this project's "NaN means no signal
    yet" convention throughout.
    """
    n = SMOOTHNESS_WINDOW_DAYS

    daily = close.pct_change()
    # Rolling counts over the n daily returns ending at t. min_periods=n so
    # an incomplete window yields NaN rather than a count over fewer days --
    # a partial window would inflate the denominator's meaning silently.
    pos = (daily > 0).astype(float).rolling(n, min_periods=n).sum()
    neg = (daily < 0).astype(float).rolling(n, min_periods=n).sum()

    # The same 252-day momentum the live gate uses, for the sign only.
    momentum = close / close.shift(n) - 1.0
    smoothness = np.sign(momentum) * (pos - neg) / n

    # A flat move (momentum exactly 0) has no direction to sign by; sign()
    # returns 0, collapsing smoothness to 0 regardless of path. Real closes
    # make this vanishingly rare, but 0 is the honest value -- an
    # unsigned path carries no directional consistency information -- and
    # it is deliberately NOT NaN, which would conflate "no direction" with
    # "cannot compute".
    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        return smoothness

    dates = close.index.to_series()
    window_start = dates.shift(n)
    gap = (dates - window_start).dt.days
    stale = (gap > _MAX_ANCHOR_GAP_DAYS).fillna(False)
    smoothness[stale.to_numpy()] = float("nan")
    return smoothness
