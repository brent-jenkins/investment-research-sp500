"""Volatility-based signals: ATR regime, realised vol percentile."""

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def score_volatility(
    df: pd.DataFrame,
    atr_period: int = 14,
    vol_lookback: int = 252,
    low_vol_pct: float = 0.25,
    high_vol_pct: float = 0.75,
) -> int:
    """Score the current volatility environment.

    Low volatility relative to history is slightly bullish (benign environment).
    Extreme high volatility signals caution / potential reversal.

    Args:
        df: Price DataFrame with 'high', 'low', 'close' columns.
        atr_period: ATR look-back period.
        vol_lookback: Days of history used to compute vol percentile.
        low_vol_pct: Percentile below which vol is considered low.
        high_vol_pct: Percentile above which vol is considered high.

    Returns:
        1 (low-vol bullish), -1 (high-vol caution), or 0 (neutral).
    """
    if df.empty or len(df) < atr_period + 5:
        return 0

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    if _TALIB_AVAILABLE:
        atr_series = pd.Series(
            talib.ATR(high.values, low.values, close.values, atr_period),
            index=df.index,
        )
    else:
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(atr_period).mean()

    # Express ATR as a percentage of price (normalised volatility)
    atr_pct = atr_series / close

    history = atr_pct.dropna().tail(vol_lookback)
    if len(history) < 30:
        return 0

    current_vol = atr_pct.iloc[-1]
    low_threshold = np.percentile(history, low_vol_pct * 100)
    high_threshold = np.percentile(history, high_vol_pct * 100)

    if current_vol <= low_threshold:
        return 1
    if current_vol >= high_threshold:
        return -1
    return 0
