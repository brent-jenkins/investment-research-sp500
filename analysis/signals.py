"""Momentum signal — trailing 12-month total return, per ticker, no blended
score and no separate regime classifier. Long when positive, flat
otherwise. Validated across the phased rebuild (Phase 1) as the stronger
of two externally-grounded candidates tested (12-month momentum vs.
200-day SMA); adopted as the system's sole entry/exit signal.
"""
import pandas as pd

MOMENTUM_LOOKBACK_DAYS = 252


def compute_momentum_series(close: pd.Series) -> pd.Series:
    """Trailing 12-month (252 trading day) total return, per date.

    NaN for the first MOMENTUM_LOOKBACK_DAYS rows (insufficient history) —
    callers must treat NaN as "no signal yet", not as False.
    """
    return close / close.shift(MOMENTUM_LOOKBACK_DAYS) - 1.0


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
