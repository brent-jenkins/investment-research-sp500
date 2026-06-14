"""Volume indicators: OBV, OBV trend, relative volume ratio."""

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def compute_volume(df: pd.DataFrame, config: dict) -> dict:
    """Compute volume indicators for the latest date in *df*.

    Indicators calculated:
    - OBV (On-Balance Volume)
    - OBV 20-day trend (OBV[-1] - OBV[-20], normalised by mean OBV magnitude)
    - 20-day average volume and ratio of latest volume to that average

    Scoring:
    - Price up + OBV trend rising + volume ratio ≥ 1.5 → +1 (accumulation)
    - Price down + OBV trend falling + volume ratio ≥ 1.5 → -1 (distribution)
    - Otherwise 0

    Args:
        df: Price DataFrame sorted ascending with 'close' and 'volume' columns.
        config: Loaded config.yaml dict.

    Returns:
        Dict with 'score' (-1/0/1) and raw indicator values for the latest row.
    """
    avg_period = config["signals"]["volume_avg_period"]

    empty = {
        "score": 0,
        "obv": None, "obv_trend": None,
        "volume_sma_20": None, "volume_ratio": None,
    }

    if df.empty or len(df) < avg_period + 2:
        return empty

    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)

    # OBV
    if _TALIB_AVAILABLE:
        obv_series = pd.Series(
            talib.OBV(close.values, volume.values), index=close.index
        )
    else:
        direction  = np.sign(close.diff().fillna(0))
        obv_series = (direction * volume).cumsum()

    # OBV 20-day trend (absolute change, sign matters)
    obv_trend = float(obv_series.iloc[-1] - obv_series.iloc[-avg_period]) \
        if len(obv_series) >= avg_period else None

    # Relative volume
    vol_sma = volume.rolling(avg_period).mean()
    last_vol = float(volume.iloc[-1])
    last_avg = float(vol_sma.iloc[-1])
    vol_ratio = (last_vol / last_avg) if last_avg > 0 else None

    # Price direction over last day
    price_change = float(close.iloc[-1] - close.iloc[-2])

    # Score
    score = 0
    elevated = vol_ratio is not None and vol_ratio >= 1.5
    obv_rising  = obv_trend is not None and obv_trend > 0
    obv_falling = obv_trend is not None and obv_trend < 0

    if elevated and price_change > 0 and obv_rising:
        score = 1
    elif elevated and price_change < 0 and obv_falling:
        score = -1

    return {
        "score":        score,
        "obv":          float(obv_series.iloc[-1]),
        "obv_trend":    obv_trend,
        "volume_sma_20": last_avg if last_avg > 0 else None,
        "volume_ratio":  vol_ratio,
    }
