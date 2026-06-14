"""Mean-reversion signals: RSI, Bollinger Bands."""

import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def score_mean_reversion(
    df: pd.DataFrame,
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
    bb_period: int = 20,
    bb_std: float = 2.0,
) -> int:
    """Compute a mean-reversion score based on RSI and Bollinger Band position.

    Args:
        df: Price DataFrame with a 'close' column.
        rsi_period: RSI look-back period.
        rsi_oversold: RSI level below which the asset is considered oversold.
        rsi_overbought: RSI level above which the asset is considered overbought.
        bb_period: Bollinger Band SMA period.
        bb_std: Number of standard deviations for the bands.

    Returns:
        1 (oversold / mean-reversion buy), -1 (overbought / sell), or 0 (neutral).
    """
    if df.empty or len(df) < bb_period + rsi_period:
        return 0

    close = df["close"].astype(float)

    if _TALIB_AVAILABLE:
        rsi = talib.RSI(close.values, rsi_period)[-1]
        bb_upper, bb_middle, bb_lower = talib.BBANDS(
            close.values, bb_period, bb_std, bb_std
        )
        upper = bb_upper[-1]
        lower = bb_lower[-1]
        last_close = close.iloc[-1]
    else:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = float((100 - 100 / (1 + rs)).iloc[-1])

        sma = close.rolling(bb_period).mean()
        std = close.rolling(bb_period).std()
        upper = float((sma + bb_std * std).iloc[-1])
        lower = float((sma - bb_std * std).iloc[-1])
        last_close = float(close.iloc[-1])

    if rsi != rsi:  # NaN
        return 0

    if rsi < rsi_oversold and last_close < lower:
        return 1   # oversold
    if rsi > rsi_overbought and last_close > upper:
        return -1  # overbought
    return 0
