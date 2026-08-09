"""12-minus-1-month cross-sectional ranking signal — research-only, per
PREREGISTRATION.md Section 3. NOT part of analysis/signals.py (that module
is the live system's time-series signal; this is a different construction,
never read by any live or production-backtest path).

rank_signal(t) = p_1(t) / p_12(t) - 1, where:
  p_1  = close at (t - 21 trading days)   -- "1 month ago"
  p_12 = close at (t - 252 trading days)  -- "12 months ago"

21/252 are this project's standing trading-day approximations for 1/12
months (matching analysis/signals.py's own MOMENTUM_LOOKBACK_DAYS=252
convention). The 1-month skip excludes short-horizon reversal from the
ranking signal -- the standard Jegadeesh & Titman (1993) construction.

Staleness guard, mirroring analysis/signals.py's compute_momentum_series()
exactly for the same failure mode (see that function's own docstring): a
pure row-count .shift() has no way to know whether the anchor it reached
back to is genuinely ~1/~12 months ago in CALENDAR time, or a stale row on
the far side of a real gap (a trading halt, an ingestion outage, or a
recycled-ticker-symbol splice -- analysis/universe.py's own documented
failure mode). A stale anchor produces a REAL, non-NaN, wrong number,
silently, unless guarded against.

_MAX_ANCHOR_GAP_DAYS for the 252-day leg is imported directly from
analysis.signals -- identical lookback length, identical guard, not
re-derived. _MAX_1MO_ANCHOR_GAP_DAYS is new: a genuine gapless 21-trading-
day window spans roughly 30 calendar days (21 trading days / ~5 trading
days per week * 7 calendar days); 60 gives 2x slack for a holiday-heavy
month or a handful of individually-missing days without over-tolerating a
genuine multi-week gap.
"""
import pandas as pd

from analysis.signals import _MAX_ANCHOR_GAP_DAYS

RANK_LOOKBACK_DAYS = 252
RANK_SKIP_DAYS = 21
_MAX_1MO_ANCHOR_GAP_DAYS = 60


def compute_cross_sectional_rank_series(close: pd.Series) -> pd.Series:
    """Vectorized rank_signal for every date in *close*'s own index. NaN
    wherever either anchor is unavailable (insufficient history) or stale
    (a genuine calendar-time gap beyond the guard, in EITHER anchor) --
    callers must treat NaN as "not rankable this date", matching this
    project's own "NaN means no signal yet" convention throughout
    (compute_momentum_series(), compute_signal_series()).
    """
    p1 = close.shift(RANK_SKIP_DAYS)
    p12 = close.shift(RANK_LOOKBACK_DAYS)
    rank_signal = p1 / p12 - 1.0

    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        return rank_signal

    dates = close.index.to_series()
    anchor_1mo = dates.shift(RANK_SKIP_DAYS)
    anchor_12mo = dates.shift(RANK_LOOKBACK_DAYS)
    gap_1mo = (dates - anchor_1mo).dt.days
    gap_12mo = (dates - anchor_12mo).dt.days
    stale = (gap_1mo > _MAX_1MO_ANCHOR_GAP_DAYS).fillna(False) | (gap_12mo > _MAX_ANCHOR_GAP_DAYS).fillna(False)
    rank_signal[stale.to_numpy()] = float("nan")
    return rank_signal
