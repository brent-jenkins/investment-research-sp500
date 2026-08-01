"""Momentum signal — trailing 12-month total return, per ticker, no blended
score and no separate regime classifier. Long when positive, flat
otherwise. Validated across the phased rebuild (Phase 1) as the stronger
of two externally-grounded candidates tested (12-month momentum vs.
200-day SMA); adopted as the system's sole entry/exit signal.
"""
import pandas as pd

MOMENTUM_LOOKBACK_DAYS = 252

# compute_momentum_series() shifts by ROW COUNT, not calendar time -- correct
# and necessary for an ordinary gapless trading-day series (weekends/holidays
# never have rows at all, so a 252-row shift lands ~365 calendar days back,
# which is exactly "12 months"). But a GENUINE gap in the trailing window --
# a ticker halted for an extended stretch, a past data-ingestion outage that
# left a hole mid-series, or (see analysis/universe.py's symbol-recycling
# note) an unrelated company's history silently following a delisted one's
# under a reused symbol -- means fewer than MOMENTUM_LOOKBACK_DAYS genuine
# trading days actually occurred between the anchor row and today. shift()
# has no way to know this: it silently reaches back to a stale row far
# outside any reasonable "12 months ago" window and returns a REAL, non-NaN
# number computed against that wrong anchor -- not the intended "insufficient
# contiguous history, no signal yet" NaN. Verified directly (2026-08-01): a
# synthetic ~16-month gap mid-series produced a real +170% momentum reading,
# silently treated as a valid signal, right at the seam.
#
# _MAX_ANCHOR_GAP_DAYS guards against this: a genuine, gapless
# MOMENTUM_LOOKBACK_DAYS-trading-day window spans roughly 365 calendar days
# (252 trading days/year). 400 gives generous slack for a heavy-holiday
# year or a handful of individually-missing days without over-tolerating a
# real multi-month gap.
_MAX_ANCHOR_GAP_DAYS = 400


def compute_momentum_series(close: pd.Series) -> pd.Series:
    """Trailing 12-month (252 trading day) total return, per date.

    NaN for the first MOMENTUM_LOOKBACK_DAYS rows (insufficient history) —
    callers must treat NaN as "no signal yet", not as False. Also NaN for
    any row whose 252-rows-back anchor is calendar-stale (more than
    _MAX_ANCHOR_GAP_DAYS away) — see _MAX_ANCHOR_GAP_DAYS's own docstring
    for why a pure row-count shift can silently misfire across a real gap
    in the trailing window.
    """
    momentum = close / close.shift(MOMENTUM_LOOKBACK_DAYS) - 1.0

    if close.empty or not isinstance(close.index, pd.DatetimeIndex):
        # Nothing to gap-check against (empty series, or a caller passing a
        # non-date-indexed series e.g. in an isolated unit test) -- the
        # row-count shift above is the only signal available either way.
        return momentum

    anchor_dates = close.index.to_series().shift(MOMENTUM_LOOKBACK_DAYS)
    gap_days = (close.index.to_series() - anchor_dates).dt.days
    stale = gap_days > _MAX_ANCHOR_GAP_DAYS
    momentum[stale.fillna(False).to_numpy()] = float("nan")
    return momentum


def compute_signal_series(close: pd.Series) -> pd.Series:
    """Boolean long/flat signal: True when trailing 12-month return > 0.

    NaN momentum rows resolve to False (flat) — matches the backtest
    convention used throughout the phased validation (no position taken
    until enough history exists to compute the signal at all).
    """
    momentum = compute_momentum_series(close)
    return (momentum > 0.0).fillna(False)


def latest_signal(close: pd.Series) -> bool:
    """Convenience wrapper: today's (last row's) signal value."""
    series = compute_signal_series(close)
    if series.empty:
        return False
    return bool(series.iloc[-1])
