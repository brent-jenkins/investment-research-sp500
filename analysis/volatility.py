"""Volatility indicators: ATR, Bollinger Band width, realised vol, VIX regime."""

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def _vix_regime(level: float) -> str:
    """Classify a VIX level into a named regime."""
    if level < 12:
        return "low"
    if level < 20:
        return "normal"
    if level < 30:
        return "high"
    return "extreme"


def _pandas_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """True Range → ATR via Wilder smoothing (ewm approximation)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_volatility(
    df: pd.DataFrame,
    config: dict,
    vix_close: float | None = None,
) -> dict:
    """Compute volatility indicators for the latest date in *df*.

    Indicators calculated:
    - ATR (14-day) and ATR as % of price
    - Bollinger Band width (requires bb_period from config)
    - 20-day realised volatility (annualised, based on log returns)
    - VIX level and regime (low / normal / high / extreme)

    Scoring:
    - Low ATR percentile (<25th over 252 days) + low VIX  → +1 (benign, trend-friendly)
    - Extreme ATR percentile (>75th) OR VIX in 'extreme'  → -1 (caution)
    - Otherwise 0

    Args:
        df: Price DataFrame sorted ascending with high / low / close columns.
        config: Loaded config.yaml dict.
        vix_close: Latest VIX closing price if already loaded; avoids a DB
            round-trip when called from the analysis engine.

    Returns:
        Dict with 'score' (-1/0/1) and raw indicator values for the latest row.
    """
    sig        = config["signals"]
    atr_period = sig["atr_period"]
    bb_period  = sig["bollinger_period"]
    bb_std_val = sig["bollinger_std"]
    vol_lookback = 252

    empty = {
        "score": 0,
        "atr": None, "atr_pct": None,
        "bb_width": None, "hist_vol_20": None,
        "vix_level": vix_close, "vix_regime": None,
    }

    if df.empty or len(df) < atr_period + 5:
        return empty

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)

    # ATR
    if _TALIB_AVAILABLE:
        atr_series = pd.Series(
            talib.ATR(high.values, low.values, close.values, atr_period),
            index=close.index,
        )
    else:
        atr_series = _pandas_atr(high, low, close, atr_period)

    atr_pct = atr_series / close.replace(0, np.nan)

    # Bollinger Band width (recomputed here; mean_reversion stores the bands)
    sma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()
    bb_width = 2 * bb_std_val * std  # upper - lower = 2 * k * std

    # 20-day realised volatility (annualised log-return std)
    log_ret     = np.log(close / close.shift(1))
    hist_vol_20 = log_ret.rolling(20).std() * np.sqrt(252)

    # ATR percentile over look-back
    history = atr_pct.dropna().tail(vol_lookback)
    current_atr_pct = float(atr_pct.iloc[-1]) if not np.isnan(atr_pct.iloc[-1]) else None

    atr_score = 0
    if current_atr_pct is not None and len(history) >= 30:
        low_thr  = np.percentile(history, 25)
        high_thr = np.percentile(history, 75)
        if current_atr_pct <= low_thr:
            atr_score = 1
        elif current_atr_pct >= high_thr:
            atr_score = -1

    # VIX scoring
    vix_score = 0
    regime_str = None
    if vix_close is not None and not np.isnan(vix_close):
        regime_str = _vix_regime(vix_close)
        if regime_str == "low":
            vix_score = 1
        elif regime_str in ("high", "extreme"):
            vix_score = -1

    # Combined: both must agree, or extreme VIX alone overrides
    if vix_close is not None and _vix_regime(vix_close) == "extreme":
        score = -1
    elif atr_score == 1 and vix_score >= 0:
        score = 1
    elif atr_score == -1 or vix_score == -1:
        score = -1
    else:
        score = 0

    def _last(s: pd.Series):
        v = s.iloc[-1]
        return None if (v != v) else float(v)

    return {
        "score":       score,
        "atr":         _last(atr_series),
        "atr_pct":     current_atr_pct,
        "bb_width":    _last(bb_width),
        "hist_vol_20": _last(hist_vol_20),
        "vix_level":   vix_close,
        "vix_regime":  regime_str,
    }
