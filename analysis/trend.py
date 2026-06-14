"""Trend-following signals: MACD, ADX, moving average crossovers."""

import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def score_trend(
    df: pd.DataFrame,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    adx_period: int = 14,
    adx_threshold: int = 25,
) -> int:
    """Compute a composite trend score using MACD and ADX.

    Args:
        df: Price DataFrame with 'close', 'high', 'low' columns.
        macd_fast: Fast EMA period for MACD.
        macd_slow: Slow EMA period for MACD.
        macd_signal: Signal line EMA period.
        adx_period: ADX look-back period.
        adx_threshold: ADX level above which a trend is considered strong.

    Returns:
        1 (bullish trend), -1 (bearish trend), or 0 (no clear trend).
    """
    if df.empty or len(df) < macd_slow + macd_signal:
        return 0

    close = df["close"].astype(float).values

    if _TALIB_AVAILABLE:
        macd_line, signal_line, _ = talib.MACD(close, macd_fast, macd_slow, macd_signal)
        adx = talib.ADX(
            df["high"].astype(float).values,
            df["low"].astype(float).values,
            close,
            adx_period,
        )
        last_macd = macd_line[-1]
        last_signal = signal_line[-1]
        last_adx = adx[-1]
    else:
        # Pure-pandas fallback (less accurate than TA-Lib C implementation)
        ema_fast = df["close"].ewm(span=macd_fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=macd_slow, adjust=False).mean()
        macd_series = ema_fast - ema_slow
        signal_series = macd_series.ewm(span=macd_signal, adjust=False).mean()
        last_macd = macd_series.iloc[-1]
        last_signal = signal_series.iloc[-1]
        last_adx = adx_threshold  # neutral fallback when TA-Lib unavailable

    if any(v != v for v in (last_macd, last_signal, last_adx)):  # NaN check
        return 0

    trend_is_strong = last_adx >= adx_threshold
    macd_bullish = last_macd > last_signal

    if trend_is_strong and macd_bullish:
        return 1
    if trend_is_strong and not macd_bullish:
        return -1
    return 0
