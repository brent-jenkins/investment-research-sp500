"""Tests for analysis/v2/signals.py -- pure functions, no DB needed."""
import numpy as np
import pandas as pd

from analysis.v2 import signals as v2s


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
