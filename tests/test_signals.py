"""Tests for analysis/signals.py -- pure functions, no DB needed."""
import numpy as np
import pandas as pd

from analysis import signals as v2s


def _close_series(n=400, start=100.0, daily_drift=0.0):
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    vals = start * (1 + daily_drift) ** np.arange(n)
    return pd.Series(vals, index=idx)


def test_momentum_series_is_nan_before_lookback_window():
    close = _close_series(n=300)
    mom = v2s.compute_momentum_series(close)
    assert mom.iloc[:v2s.MOMENTUM_LOOKBACK_DAYS].isna().all()
    assert mom.iloc[v2s.MOMENTUM_LOOKBACK_DAYS:].notna().all()


def test_momentum_positive_for_rising_series():
    close = _close_series(n=400, daily_drift=0.001)
    mom = v2s.compute_momentum_series(close)
    assert mom.iloc[-1] > 0


def test_momentum_negative_for_falling_series():
    close = _close_series(n=400, daily_drift=-0.001)
    mom = v2s.compute_momentum_series(close)
    assert mom.iloc[-1] < 0


def test_signal_series_false_before_lookback_window_not_nan():
    close = _close_series(n=300, daily_drift=0.001)
    sig = v2s.compute_signal_series(close)
    assert sig.dtype == bool
    assert not sig.iloc[:v2s.MOMENTUM_LOOKBACK_DAYS].any()


def test_signal_series_matches_momentum_sign():
    close = _close_series(n=400, daily_drift=0.001)
    sig = v2s.compute_signal_series(close)
    mom = v2s.compute_momentum_series(close)
    valid = mom.notna()
    assert (sig[valid] == (mom[valid] > 0)).all()


def test_latest_signal_true_for_rising_series():
    close = _close_series(n=400, daily_drift=0.001)
    assert v2s.latest_signal(close) is True


def test_latest_signal_false_for_falling_series():
    close = _close_series(n=400, daily_drift=-0.001)
    assert v2s.latest_signal(close) is False


def test_latest_signal_false_for_empty_series():
    assert v2s.latest_signal(pd.Series(dtype=float)) is False


# ---------------------------------------------------------------------------
# Mid-history gap -- shift(252) shifts by ROW COUNT, not calendar time, so a
# genuine gap in the trailing window (a halted/delisted-then-resumed ticker,
# a past data-ingestion outage, or two unrelated companies' histories
# spliced under a reused symbol) must not silently compute a real momentum
# value against a stale, calendar-distant anchor row. Verified directly
# 2026-08-01 against the pre-fix code: a ~16-month gap produced a real
# +170% momentum reading with signal=True right at the seam.
# ---------------------------------------------------------------------------

def _gapped_close_series():
    """300 business days of a rising series, a ~16-month calendar gap
    (no rows at all -- not just a slow period), then 300 more rising days."""
    dates1 = pd.bdate_range("2015-01-01", periods=300)
    dates2 = pd.bdate_range("2017-06-01", periods=300)
    idx = pd.DatetimeIndex(list(dates1) + list(dates2))
    vals = list(range(100, 400)) + list(range(400, 700))
    return pd.Series(vals, index=idx)


def test_momentum_nan_immediately_after_a_real_gap():
    close = _gapped_close_series()
    mom = v2s.compute_momentum_series(close)
    # Row 300 is the first row after the gap -- its naive 252-rows-back
    # anchor (row 48) is calendar-stale (~2.25 years away, not ~12 months).
    assert pd.isna(mom.iloc[300])
    assert pd.isna(mom.iloc[310])


def test_signal_false_immediately_after_a_real_gap():
    close = _gapped_close_series()
    sig = v2s.compute_signal_series(close)
    assert sig.iloc[300] is False or sig.iloc[300] == False  # noqa: E712
    assert not sig.iloc[300:320].any()


def test_momentum_unaffected_by_a_short_gap_within_tolerance():
    """A brief halt (a handful of missing trading days) must not be treated
    as a genuine gap -- only a calendar gap beyond _MAX_ANCHOR_GAP_DAYS
    should suppress momentum."""
    dates1 = pd.bdate_range("2020-01-01", periods=300)
    dates2 = pd.bdate_range(dates1[-1] + pd.Timedelta(days=5), periods=100)  # 5-day halt
    idx = pd.DatetimeIndex(list(dates1) + list(dates2))
    vals = list(100.0 + i * 0.5 for i in range(400))
    close = pd.Series(vals, index=idx)
    mom = v2s.compute_momentum_series(close)
    # Row 300 (first row after the short halt): anchor is row 48, well
    # within one ordinary trading year plus a 5-day gap -- must still
    # compute a real value, not be suppressed.
    assert pd.notna(mom.iloc[300])


def test_momentum_recovers_once_a_full_gapless_window_exists_past_the_gap():
    close = _gapped_close_series()
    mom = v2s.compute_momentum_series(close)
    # 252 genuine trading days after the gap ends (row 300), the anchor
    # (row 300+i-252) is once again inside the post-gap, contiguous run --
    # real signal must resume.
    assert pd.notna(mom.iloc[300 + 252])
