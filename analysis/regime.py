"""Market regime detection: bull / bear / sideways classification."""

import pandas as pd


def classify_regime(df: pd.DataFrame, sma_short: int = 50, sma_long: int = 200) -> int:
    """Classify the current market regime based on price vs moving averages.

    Args:
        df: Price DataFrame with a 'close' column, sorted ascending by date.
        sma_short: Short-term SMA period (default 50).
        sma_long: Long-term SMA period (default 200).

    Returns:
        1 (bullish), -1 (bearish), or 0 (sideways / insufficient data).
    """
    if df.empty or len(df) < sma_long:
        return 0

    close = df["close"]
    short_ma = close.rolling(sma_short).mean().iloc[-1]
    long_ma = close.rolling(sma_long).mean().iloc[-1]
    last_close = close.iloc[-1]

    if last_close > short_ma > long_ma:
        return 1   # bullish regime
    if last_close < short_ma < long_ma:
        return -1  # bearish regime
    return 0       # sideways / mixed
