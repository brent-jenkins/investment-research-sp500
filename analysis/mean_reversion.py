"""Mean-reversion indicators: RSI, Bollinger Bands, 52-week Z-score."""

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def _pandas_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-smoothed RSI fallback."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_mean_reversion(df: pd.DataFrame, config: dict) -> dict:
    """Compute mean-reversion indicators for the latest date in *df*.

    Indicators calculated:
    - RSI (14-day, Wilder smoothing)
    - Bollinger Bands (20-day SMA ± 2 std dev): upper, middle, lower, %B
    - Bollinger Band width (upper - lower)
    - 52-week Z-score: (close - 252-day mean) / 252-day std

    Scoring (RSI + Z-score must agree for a signal — see the correlation
    note below for why Bollinger %B was dropped from this calculation):
    - RSI oversold (<30) → bullish; overbought (>70) → bearish
    - 52-week Z-score < -1.5 → bullish; > +1.5 → bearish

    bb_pct_b and bb_width are still computed and returned for storage/display
    (Charts page indicator panel) but no longer feed the score: a production
    correlation analysis (200 rows, 49 tickers, 2026-07-05) measured RSI vs
    Bollinger %B at 0.927 — the two are redundant measurements of the same
    overbought/oversold signal, so keeping both as scoring votes double-
    counted one underlying signal rather than adding an independent one.
    Dropping %B rebalances the module to two genuinely distinct components:
    momentum extremity (RSI) and long-run price dislocation (52-week Z).

    Args:
        df: Price DataFrame sorted ascending with a 'close' column.
        config: Loaded config.yaml dict.

    Returns:
        Dict with 'score' (-1/0/1) and raw indicator values for the latest row.
    """
    sig = config["signals"]
    rsi_period  = sig["rsi_period"]
    bb_period   = sig["bollinger_period"]
    bb_std      = sig["bollinger_std"]
    rsi_oversold   = 30.0
    rsi_overbought = 70.0
    z_thr = 1.5

    empty = {
        "score": 0,
        "rsi": None,
        "bb_upper": None, "bb_middle": None, "bb_lower": None,
        "bb_pct_b": None, "bb_width": None,
        "z_score_52w": None,
    }

    if df.empty or len(df) < bb_period + rsi_period:
        return empty

    close = df["close"].astype(float)

    # RSI
    if _TALIB_AVAILABLE:
        rsi_series = pd.Series(talib.RSI(close.values, rsi_period), index=close.index)
    else:
        rsi_series = _pandas_rsi(close, rsi_period)

    # Bollinger Bands
    if _TALIB_AVAILABLE:
        upper_arr, mid_arr, lower_arr = talib.BBANDS(
            close.values, bb_period, bb_std, bb_std
        )
        bb_upper  = pd.Series(upper_arr, index=close.index)
        bb_middle = pd.Series(mid_arr,   index=close.index)
        bb_lower  = pd.Series(lower_arr, index=close.index)
    else:
        bb_middle = close.rolling(bb_period).mean()
        std       = close.rolling(bb_period).std()
        bb_upper  = bb_middle + bb_std * std
        bb_lower  = bb_middle - bb_std * std

    bb_width = bb_upper - bb_lower
    band_range = bb_upper - bb_lower
    bb_pct_b = (close - bb_lower) / band_range.replace(0, np.nan)

    # 52-week Z-score (252 trading days)
    roll_mean = close.rolling(252).mean()
    roll_std  = close.rolling(252).std()
    z_score   = (close - roll_mean) / roll_std.replace(0, np.nan)

    def _last(s: pd.Series):
        v = s.iloc[-1]
        return None if (v != v) else float(v)

    last_rsi    = _last(rsi_series)
    last_close  = float(close.iloc[-1])
    last_upper  = _last(bb_upper)
    last_lower  = _last(bb_lower)
    last_z      = _last(z_score)

    # Scoring — 50% RSI + 50% 52-week Z-score; both must agree for a signal.
    # bb_signal is intentionally not computed here — see the docstring above
    # for the correlation rationale (bb_pct_b is still returned below for
    # storage/display, just excluded from the score).
    rsi_signal  = 0
    z_signal    = 0

    if last_rsi is not None:
        if last_rsi < rsi_oversold:
            rsi_signal = 1
        elif last_rsi > rsi_overbought:
            rsi_signal = -1

    if last_z is not None:
        if last_z < -z_thr:
            z_signal = 1
        elif last_z > z_thr:
            z_signal = -1

    if rsi_signal == 1 and z_signal == 1:
        score = 1
    elif rsi_signal == -1 and z_signal == -1:
        score = -1
    else:
        score = 0

    return {
        "score":      score,
        "rsi":        last_rsi,
        "bb_upper":   last_upper,
        "bb_middle":  _last(bb_middle),
        "bb_lower":   last_lower,
        "bb_pct_b":   _last(bb_pct_b),
        "bb_width":   _last(bb_width),
        "z_score_52w": last_z,
    }
