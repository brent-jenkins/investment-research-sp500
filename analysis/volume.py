"""Volume-based signals: OBV trend, volume spike detection."""

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def score_volume(
    df: pd.DataFrame,
    avg_period: int = 20,
    spike_multiplier: float = 2.0,
) -> int:
    """Score volume action relative to its moving average.

    Bullish: price up on elevated volume (accumulation).
    Bearish: price down on elevated volume (distribution).

    Args:
        df: Price DataFrame with 'close' and 'volume' columns.
        avg_period: Period for the volume moving average.
        spike_multiplier: Volume is considered elevated if > avg * this factor.

    Returns:
        1 (bullish volume), -1 (bearish volume), or 0 (neutral).
    """
    if df.empty or len(df) < avg_period + 1:
        return 0

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    vol_avg = volume.rolling(avg_period).mean()
    last_vol = volume.iloc[-1]
    last_avg = vol_avg.iloc[-1]

    if last_avg == 0 or last_avg != last_avg:
        return 0

    price_change = close.iloc[-1] - close.iloc[-2]
    vol_ratio = last_vol / last_avg

    # Only signal on elevated volume to filter noise
    if vol_ratio < spike_multiplier:
        return 0

    if _TALIB_AVAILABLE:
        obv = talib.OBV(close.values, volume.values)
        obv_trend = obv[-1] - obv[-avg_period] if len(obv) >= avg_period else 0
    else:
        direction = np.sign(close.diff().fillna(0))
        obv = (direction * volume).cumsum()
        obv_trend = float(obv.iloc[-1] - obv.iloc[-avg_period])

    if price_change > 0 and obv_trend > 0:
        return 1   # price up, OBV up, high volume → accumulation
    if price_change < 0 and obv_trend < 0:
        return -1  # price down, OBV down, high volume → distribution
    return 0
