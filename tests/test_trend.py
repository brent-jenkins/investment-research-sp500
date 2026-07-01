"""Tests for the direction-aware SMA50/SMA200 cross vote in analysis/trend.py.

_ma_cross_vote() is a pure function over two pandas Series — no DB or
yfinance calls are involved, so these tests build synthetic SMA series
directly rather than going through compute_trend()/get_price_df().
"""

import numpy as np
import pandas as pd

from analysis.trend import _ma_cross_vote

SLOPE_WINDOW = 10
NEAR_PCT = 0.02


def _series(start: float, end: float, n: int = 60) -> pd.Series:
    """A linearly interpolated series from *start* to *end* over *n* points."""
    return pd.Series(np.linspace(start, end, n), index=pd.RangeIndex(n))


def test_confirmed_golden_cross_rising_sma50_votes_bullish():
    """SMA50 above SMA200 and still rising confirms the golden cross -> +1."""
    sma_50 = _series(15.0, 17.0)    # rising
    sma_200 = _series(15.0, 15.25)  # ~flat, stays below sma_50 throughout
    assert _ma_cross_vote(sma_50, sma_200, SLOPE_WINDOW, NEAR_PCT) == 1


def test_golden_cross_declining_sma50_wide_gap_votes_neutral():
    """A stale golden cross (SMA50 declining but still well above SMA200) is
    fading momentum, not a reversal yet, so it should not count as bullish."""
    sma_50 = _series(18.9, 17.64)   # declining, but still comfortably > sma_200
    sma_200 = _series(15.0, 15.25)  # gap stays wide (~14%+)
    assert _ma_cross_vote(sma_50, sma_200, SLOPE_WINDOW, NEAR_PCT) == 0


def test_golden_cross_declining_sma50_near_gap_votes_bearish():
    """A declining SMA50 that has converged to within near_pct of SMA200 is an
    imminent death cross — the vote should flip to bearish ahead of the flip."""
    sma_50 = _series(16.0, 15.4)    # declining, converging toward sma_200
    sma_200 = _series(15.0, 15.25)  # gap narrows to < 2%
    assert _ma_cross_vote(sma_50, sma_200, SLOPE_WINDOW, NEAR_PCT) == -1


def test_confirmed_death_cross_falling_sma50_votes_bearish():
    """SMA50 below SMA200 and still falling confirms the death cross -> -1."""
    sma_50 = _series(16.0, 13.0)    # falling
    sma_200 = _series(15.0, 15.0)   # flat, stays above sma_50 throughout
    assert _ma_cross_vote(sma_50, sma_200, SLOPE_WINDOW, NEAR_PCT) == -1


def test_death_cross_rising_sma50_wide_gap_votes_neutral():
    """Mirror of the stale-golden-cross case: SMA50 rising back up but still
    well below SMA200 is a fading death cross, not yet a reversal -> 0."""
    sma_50 = _series(13.0, 14.0)    # rising, but still comfortably < sma_200
    sma_200 = _series(15.0, 15.0)   # gap stays wide
    assert _ma_cross_vote(sma_50, sma_200, SLOPE_WINDOW, NEAR_PCT) == 0


def test_death_cross_rising_sma50_near_gap_votes_bullish():
    """Mirror of the near-cross bearish flip: a rising SMA50 that has closed
    to within near_pct of SMA200 signals an imminent golden cross -> +1."""
    sma_50 = _series(14.5, 14.8)    # rising, converging toward sma_200
    sma_200 = _series(15.0, 15.0)   # gap narrows to < 2%
    assert _ma_cross_vote(sma_50, sma_200, SLOPE_WINDOW, NEAR_PCT) == 1
