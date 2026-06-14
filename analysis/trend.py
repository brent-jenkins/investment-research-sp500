"""Trend-following indicators: SMAs, EMA, MACD, ADX, golden/death cross."""

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


def _pandas_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Pure-pandas MACD fallback."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _pandas_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Pure-pandas ADX / +DI / -DI using Wilder smoothing (ewm approximation)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    alpha = 1.0 / period
    smooth_tr    = pd.Series(tr).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_plus  = pd.Series(plus_dm, index=high.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_minus = pd.Series(minus_dm, index=high.index).ewm(alpha=alpha, adjust=False).mean() * period

    plus_di  = 100 * smooth_plus  / smooth_tr.replace(0, np.nan)
    minus_di = 100 * smooth_minus / smooth_tr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return adx, plus_di, minus_di


def compute_trend(df: pd.DataFrame, config: dict) -> dict:
    """Compute trend indicators for the latest date in *df*.

    Indicators calculated:
    - SMA 20 / 50 / 200, EMA 20
    - MACD line, signal line, histogram (12/26/9)
    - ADX, +DI, -DI (14-day)
    - Golden / death cross flag (SMA50 vs SMA200)

    Scoring logic (needs 2-of-3 agreement):
    - Price vs SMA200: above → bullish, below → bearish
    - SMA50 vs SMA200: above → bullish (golden), below → bearish (death)
    - MACD vs signal line (only counted when ADX ≥ threshold — trending market)

    Args:
        df: Price DataFrame sorted ascending with columns
            [date, open, high, low, close, volume].
        config: Loaded config.yaml dict.

    Returns:
        Dict with 'score' (-1/0/1) and raw indicator values for the latest row.
    """
    sig = config["signals"]
    macd_fast    = sig["macd_fast"]
    macd_slow    = sig["macd_slow"]
    macd_sig_p   = sig["macd_signal"]
    adx_period   = sig["adx_period"]
    adx_thr      = sig["adx_trend_threshold"]
    min_rows     = macd_slow + macd_sig_p + 5

    empty = {
        "score": 0,
        "sma_20": None, "sma_50": None, "sma_200": None, "ema_20": None,
        "macd": None, "macd_signal": None, "macd_hist": None,
        "adx": None, "plus_di": None, "minus_di": None,
        "golden_cross": 0,
    }

    if df.empty or len(df) < min_rows:
        return empty

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)

    # Moving averages
    sma_20  = close.rolling(20).mean()
    sma_50  = close.rolling(50).mean()
    sma_200 = close.rolling(200).mean()
    ema_20  = close.ewm(span=20, adjust=False).mean()

    # MACD
    if _TALIB_AVAILABLE:
        macd_arr, sig_arr, hist_arr = talib.MACD(
            close.values, macd_fast, macd_slow, macd_sig_p
        )
        macd_s   = pd.Series(macd_arr, index=close.index)
        sig_s    = pd.Series(sig_arr,  index=close.index)
        hist_s   = pd.Series(hist_arr, index=close.index)
        adx_s      = pd.Series(talib.ADX(high.values, low.values, close.values, adx_period), index=close.index)
        plus_di_s  = pd.Series(talib.PLUS_DI(high.values, low.values, close.values, adx_period), index=close.index)
        minus_di_s = pd.Series(talib.MINUS_DI(high.values, low.values, close.values, adx_period), index=close.index)
    else:
        macd_s, sig_s, hist_s        = _pandas_macd(close, macd_fast, macd_slow, macd_sig_p)
        adx_s, plus_di_s, minus_di_s = _pandas_adx(high, low, close, adx_period)

    # Golden / death cross (SMA50 vs SMA200, last 20 days)
    golden_cross = 0
    if len(sma_50.dropna()) >= 20 and len(sma_200.dropna()) >= 20:
        above_now  = sma_50.iloc[-1]  > sma_200.iloc[-1]
        above_prev = sma_50.iloc[-20] > sma_200.iloc[-20]
        if above_now and not above_prev:
            golden_cross = 1   # just crossed up
        elif not above_now and above_prev:
            golden_cross = -1  # just crossed down
        elif above_now:
            golden_cross = 1   # sustained golden cross
        else:
            golden_cross = -1  # sustained death cross

    # Latest scalar values
    def _last(s: pd.Series):
        v = s.iloc[-1]
        return None if (v != v) else float(v)

    last_close   = float(close.iloc[-1])
    last_sma50   = _last(sma_50)
    last_sma200  = _last(sma_200)
    last_macd    = _last(macd_s)
    last_sig     = _last(sig_s)
    last_adx     = _last(adx_s)

    # Scoring
    votes = []

    # Vote 1: price vs SMA200
    if last_sma200 is not None:
        votes.append(1 if last_close > last_sma200 else -1)

    # Vote 2: SMA50 vs SMA200
    if last_sma50 is not None and last_sma200 is not None:
        votes.append(1 if last_sma50 > last_sma200 else -1)

    # Vote 3: MACD (only in trending market)
    if last_macd is not None and last_sig is not None:
        if last_adx is None or last_adx >= adx_thr:
            votes.append(1 if last_macd > last_sig else -1)

    bullish = votes.count(1)
    bearish = votes.count(-1)
    score = 1 if bullish >= 2 else (-1 if bearish >= 2 else 0)

    return {
        "score": score,
        "sma_20":       _last(sma_20),
        "sma_50":       last_sma50,
        "sma_200":      last_sma200,
        "ema_20":       _last(ema_20),
        "macd":         last_macd,
        "macd_signal":  last_sig,
        "macd_hist":    _last(hist_s),
        "adx":          last_adx,
        "plus_di":      _last(plus_di_s),
        "minus_di":     _last(minus_di_s),
        "golden_cross": golden_cross,
    }
