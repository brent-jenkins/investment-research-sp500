"""Volatility indicators: ATR, Bollinger Band width, realised vol, VIX regime."""

import logging
import math
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Change point detection (CPD)
# ---------------------------------------------------------------------------
#
# Identifies when a ticker's return-variance structure has fundamentally
# shifted, so volatility scoring can distinguish "always been volatile" from
# "recently entered a new volatility regime". Structural context only for
# now — see compute_volatility()'s docstring for the score adjustment this
# feeds, and CLAUDE.md's "Change point detection" section for the planned
# GARCH stop-loss calibration this is laying groundwork for.

_CPD_EMPTY: dict = {
    "last_change_point": None,
    "days_since_change": None,
    "pre_change_vol": None,
    "post_change_vol": None,
    "vol_regime": "NORMAL",
    "structural_break": False,
}

# Cache keyed on (ticker, today's date, method) — detect_change_points() is
# called once from analyse_ticker() (writes indicators) and again from
# score_ticker() (confluence classification) for every ticker on every
# analysis run, so this avoids recomputing the same CUSUM/variance-ratio
# scan twice per ticker per run. Keyed on date (not run id) since there is
# no run-scoped context passed down to this module — a new calendar date
# naturally invalidates the cache since detect_change_points() would see
# fresh price_data rows anyway.
_cpd_cache: dict[tuple[str, str, str], dict] = {}


def _icss_change_point(log_returns: np.ndarray, threshold: float) -> Optional[int]:
    """Locate the most likely variance change point via cumulative-sum-of-squares.

    This is the Inclan & Tiao (1994) ICSS statistic: Dk = Ck/CT - k/T, where
    Ck is the cumulative sum of squared returns through observation k and CT
    is the total. Under constant variance Dk fluctuates near zero; a genuine
    variance break produces a sharp peak in |Dk|. The standardised statistic
    sqrt(T/2) * max|Dk| is compared against *threshold* (config
    signals.cpd_threshold, default 3.0 — deliberately stricter than the
    classical 95%/99% asymptotic critical values of ~1.36/~1.63, so only
    pronounced regime shifts qualify as a "structural break").

    Deliberately NOT a textbook CUSUM control chart applied to the rolling
    20-day variance series (the first design tried, matching a literal
    reading of "apply CUSUM on the [rolling] variance series"): a rolling
    window shares 19 of its 20 points with its neighbour, so the resulting
    series is heavily autocorrelated, and a control-chart CUSUM (designed
    for near-independent samples) drifts and false-triggers on that
    autocorrelation alone — verified empirically, it flagged a change point
    in a *stable*, no-break synthetic series on every trial and located true
    breaks nowhere near the actual break date. Cumulative sum of squared
    returns (this function) is the standard, well-behaved alternative for
    exactly this problem and is still fundamentally a cumulative-sum method,
    so the CUSUM name in the public API is kept.

    Returns the 0-based integer position within *log_returns* of the most
    likely change point, or None if no peak exceeds *threshold*.
    """
    n = len(log_returns)
    if n < 50:
        return None

    x = np.asarray(log_returns, dtype=float) ** 2
    total = x.sum()
    if total == 0:
        return None

    cumulative = np.cumsum(x)
    k = np.arange(1, n + 1)
    deviation = cumulative / total - k / n
    stat = np.sqrt(n / 2) * np.abs(deviation)

    peak_idx = int(np.argmax(stat))
    if stat[peak_idx] > threshold:
        return peak_idx
    return None


def _variance_ratio_change_point(
    log_returns: np.ndarray,
    min_samples: int = 30,
    stride: int = 5,
) -> Optional[int]:
    """Secondary/validation change-point detector via a scanning F-test.

    Splits *log_returns* at each candidate point (stepping by *stride* for
    speed), computes the variance on each side, and runs a two-sided F-test
    for a significant variance difference (p < 0.05). Selects the split with
    the STRONGEST evidence (largest F-statistic among significant splits) —
    not simply the most recent qualifying split. A literal "most recent
    significant split" (the first design tried) turns out to pick a point
    near the very end of the scan range almost every time a real break
    exists: once volatility has genuinely shifted, nearly every later split
    still tests significant against an increasingly break-dominated "before"
    segment, so "most recent" stops meaning "where the break happened" and
    just means "near the end of the scannable range" — verified empirically
    against synthetic data with a known break location. Selecting the
    strongest (max F-statistic) split instead is the standard binary-
    segmentation approach and locates the true break precisely.

    Requires scipy (added to requirements.txt for this feature).

    Returns the 0-based integer position within *log_returns* of the
    highest-confidence change point, or None if no split is significant.
    """
    from scipy import stats  # noqa: PLC0415  optional heavy import, only needed for this method

    n = len(log_returns)
    if n < 2 * min_samples:
        return None

    values = np.asarray(log_returns, dtype=float)
    best_idx: Optional[int] = None
    best_f = 0.0
    for split in range(min_samples, n - min_samples, stride):
        before = values[:split]
        after = values[split:]
        var_before = before.var(ddof=1)
        var_after = after.var(ddof=1)
        if var_before == 0 or var_after == 0:
            continue
        f_stat = max(var_before, var_after) / min(var_before, var_after)
        dfn = (len(before) if var_before >= var_after else len(after)) - 1
        dfd = (len(after) if var_before >= var_after else len(before)) - 1
        p_value = 2 * min(stats.f.sf(f_stat, dfn, dfd), 0.5)
        if p_value < 0.05 and f_stat > best_f:
            best_f = f_stat
            best_idx = split

    return best_idx


def _classify_vol_regime(log_returns: pd.Series) -> str:
    """Classify the most recent 20-day annualised realised vol against its
    own history over the same lookback window — LOW (<25th pctile), NORMAL
    (25th-75th), HIGH (75th-90th), or ELEVATED (>90th)."""
    vol_series = (log_returns.rolling(20).std() * np.sqrt(252)).dropna()
    if len(vol_series) < 30:
        return "NORMAL"
    current = vol_series.iloc[-1]
    p25 = np.percentile(vol_series, 25)
    p75 = np.percentile(vol_series, 75)
    p90 = np.percentile(vol_series, 90)
    if current >= p90:
        return "ELEVATED"
    if current >= p75:
        return "HIGH"
    if current <= p25:
        return "LOW"
    return "NORMAL"


def detect_change_points(
    ticker: str,
    config: dict,
    method: str = "cusum",
) -> dict:
    """Detect the most recent structural break in *ticker*'s return variance.

    Reads the last signals.cpd_lookback_days (default 504, ~2 trading years)
    of daily closes from price_data, computes daily log returns, and locates
    the most recent variance change point via *method*:

    - "cusum" (default, primary): cumulative-sum-of-squares (ICSS-style) —
      see _icss_change_point(). Fast (O(n)), no distributional assumptions.
    - "variance_ratio" (secondary/validation): scanning F-test binary
      segmentation — see _variance_ratio_change_point(). Use to cross-check
      a "cusum" result; not used automatically by compute_volatility().

    A change point within signals.cpd_recent_window days (default 90) of the
    most recent stored date sets structural_break=True, which is what
    compute_volatility() acts on. Results are cached in-process per
    (ticker, today's date, method) since this is called once from
    analyse_ticker() and again from score_ticker() for every ticker on every
    analysis run — see _cpd_cache above.

    Returns a dict:
        last_change_point       — date of the most recent structural break, or None
        days_since_change       — trading days since that break, or None
        pre_change_vol          — annualised vol before the break, or None
        post_change_vol         — annualised vol after the break, or None
        vol_regime              — "LOW" / "NORMAL" / "HIGH" / "ELEVATED", relative to history
        structural_break        — True if a break falls within cpd_recent_window days
    """
    sig           = config.get("signals", {})
    lookback_days = int(sig.get("cpd_lookback_days", 504))
    threshold     = float(sig.get("cpd_threshold", 3.0))
    recent_window = int(sig.get("cpd_recent_window", 90))

    cache_key = (ticker, datetime.utcnow().strftime("%Y-%m-%d"), method)
    if cache_key in _cpd_cache:
        return _cpd_cache[cache_key]

    from data.ingestion import get_price_df  # noqa: PLC0415  local import — keeps this module DB-free otherwise

    # Fetch a generous calendar-day window (1.6x plus a buffer) so weekends/
    # holidays don't shrink the trading-day count below lookback_days, then
    # trim to exactly what's needed.
    df = get_price_df(ticker, days=int(lookback_days * 1.6) + 30)
    if df.empty or len(df) < 60:
        _cpd_cache[cache_key] = dict(_CPD_EMPTY)
        return _cpd_cache[cache_key]

    df = df.sort_values("date").tail(lookback_days + 1).reset_index(drop=True)
    close = df["close"].astype(float)

    log_returns = np.log(close / close.shift(1)).iloc[1:].reset_index(drop=True)
    ret_dates   = df["date"].iloc[1:].reset_index(drop=True)

    if len(log_returns) < 60:
        _cpd_cache[cache_key] = dict(_CPD_EMPTY)
        return _cpd_cache[cache_key]

    if method == "variance_ratio":
        change_idx = _variance_ratio_change_point(log_returns.to_numpy())
    else:
        change_idx = _icss_change_point(log_returns.to_numpy(), threshold)

    vol_regime = _classify_vol_regime(log_returns)

    if change_idx is None:
        result = dict(_CPD_EMPTY)
        result["vol_regime"] = vol_regime
        _cpd_cache[cache_key] = result
        return result

    pre  = log_returns.iloc[:change_idx]
    post = log_returns.iloc[change_idx:]
    pre_vol  = float(pre.std() * np.sqrt(252)) if len(pre) >= 5 else None
    post_vol = float(post.std() * np.sqrt(252)) if len(post) >= 5 else None

    days_since_change = len(log_returns) - 1 - change_idx
    structural_break = days_since_change <= recent_window

    change_date = ret_dates.iloc[change_idx]
    if hasattr(change_date, "date"):
        change_date = change_date.date()

    result = {
        "last_change_point": change_date,
        "days_since_change": int(days_since_change),
        "pre_change_vol":    pre_vol,
        "post_change_vol":   post_vol,
        "vol_regime":        vol_regime,
        "structural_break":  bool(structural_break),
    }
    _cpd_cache[cache_key] = result
    return result


def compute_volatility(
    df: pd.DataFrame,
    config: dict,
    vix_close: float | None = None,
    ticker: Optional[str] = None,
) -> dict:
    """Compute volatility indicators for the latest date in *df*.

    Indicators calculated:
    - ATR (14-day) and ATR as % of price
    - Bollinger Band width (requires bb_period from config)
    - 20-day realised volatility (annualised, based on log returns)
    - VIX level and regime (low / normal / high / extreme)
    - Change-point-aware regime context (see detect_change_points()) when
      *ticker* is supplied

    Scoring:
    - Low ATR percentile (<25th over 252 days) + low VIX  → +1 (benign, trend-friendly)
    - Extreme ATR percentile (>75th) OR VIX in 'extreme'  → -1 (caution)
    - Otherwise 0
    - Structural-break adjustment (only when *ticker* is supplied — see
      detect_change_points()): a recent change point (within
      signals.cpd_recent_window days) with post_change_vol > pre_change_vol
      * 1.5 subtracts 1 from the score (elevated vol after a break is a
      warning); post_change_vol < pre_change_vol * 0.67 adds 1 (vol
      compression after a break is constructive). No recent change point,
      or a marginal one, leaves the score unchanged. The combined score is
      clamped back to [-1, 1].

    Args:
        df: Price DataFrame sorted ascending with high / low / close columns.
        config: Loaded config.yaml dict.
        vix_close: Latest VIX closing price if already loaded; avoids a DB
            round-trip when called from the analysis engine.
        ticker: Symbol to run change-point detection for. When omitted,
            change-point fields are returned as neutral defaults and ATR/vol
            are computed over the full window exactly as before this feature
            existed — this keeps compute_volatility() backward compatible
            for any caller that doesn't have a ticker handy.

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
        "vol_regime": "NORMAL", "structural_break": False,
        "days_since_change_point": None,
    }

    if df.empty or len(df) < atr_period + 5:
        return empty

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)

    # Change-point detection — run once up front so a recent break can swap
    # in a post-change-point slice of df for the ATR/vol calculations below.
    cpd = detect_change_points(ticker, config) if ticker else dict(_CPD_EMPTY)

    calc_df = df
    if cpd["structural_break"] and cpd.get("last_change_point") is not None:
        post_df = df[df["date"] >= pd.Timestamp(cpd["last_change_point"])]
        # hist_vol_20 needs 21 rows (a 20-day rolling window on returns, which
        # are themselves one shorter than the price count) — a stricter floor
        # than atr_period + 5 (19 at the default atr_period=14), so use
        # whichever is larger rather than silently returning a NaN hist_vol_20
        # for a break recent enough to clear the ATR floor but not this one.
        min_rows_needed = max(atr_period + 5, 21)
        if len(post_df) >= min_rows_needed:
            calc_df = post_df

    close = calc_df["close"].astype(float)
    high  = calc_df["high"].astype(float)
    low   = calc_df["low"].astype(float)

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

    # 20-day realised volatility (annualised log-return std). Note: this
    # value is mathematically invariant to the calc_df slice swap above
    # whenever the slice retains at least 21 rows (required for a non-NaN
    # result in the first place) — a trailing rolling(20) read only looks at
    # the last 20 rows, which are identical whether calc_df is the full
    # window or a post-change-point slice of it (slicing only trims rows
    # from the front). ATR (EWM-smoothed, below) is the indicator that
    # actually responds to the slice, since EWM carries decaying influence
    # from rows further back than its nominal period.
    log_ret     = np.log(close / close.shift(1))
    hist_vol_20 = log_ret.rolling(20).std() * np.sqrt(252)

    # ATR percentile over look-back (measured against the FULL window's own
    # ATR% history, regardless of whether calc_df was swapped to a
    # post-change-point slice above — percentile ranking needs the original
    # df's longer history to mean anything). Reuses atr_pct directly when no
    # slice swap happened (the common case); only recomputes ATR over the
    # full df when calc_df is actually a post-change-point subset of it.
    if calc_df is df:
        full_atr_pct = atr_pct
    else:
        full_close = df["close"].astype(float)
        full_high  = df["high"].astype(float)
        full_low   = df["low"].astype(float)
        if _TALIB_AVAILABLE:
            full_atr_series = pd.Series(
                talib.ATR(full_high.values, full_low.values, full_close.values, atr_period),
                index=full_close.index,
            )
        else:
            full_atr_series = _pandas_atr(full_high, full_low, full_close, atr_period)
        full_atr_pct = full_atr_series / full_close.replace(0, np.nan)
    history = full_atr_pct.dropna().tail(vol_lookback)
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

    # Structural-break score adjustment — see docstring above.
    pre_vol  = cpd.get("pre_change_vol")
    post_vol = cpd.get("post_change_vol")
    if cpd["structural_break"] and pre_vol is not None and post_vol is not None and pre_vol > 0:
        if post_vol > pre_vol * 1.5:
            score -= 1
        elif post_vol < pre_vol * 0.67:
            score += 1
        score = max(-1, min(1, score))

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
        "vol_regime":              cpd["vol_regime"],
        "structural_break":        cpd["structural_break"],
        "days_since_change_point": cpd["days_since_change"],
    }


# ---------------------------------------------------------------------------
# GARCH(1,1) dynamic stop loss
# ---------------------------------------------------------------------------
#
# Replaces the fixed ATR(14) x atr_stop_multiplier stop distance with a
# forward-looking 1-day GARCH volatility forecast, scaled to the position's
# holding period. GARCH models volatility clustering — after a high-vol
# period, volatility tends to remain elevated before mean-reverting — so it
# weights recent volatility more heavily than a simple trailing average.
# Falls back to the old ATR-based stop whenever GARCH can't be trusted (too
# little data, fitting failure). See compute_garch_stop()'s docstring.

# Minimum number of daily log returns required before a GARCH fit is
# attempted. Below this, compute_garch_stop() falls back to the ATR stop
# rather than fitting on a thin, noisy sample.
_MIN_GARCH_OBSERVATIONS_DEFAULT = 100


def _stop_price_and_distance(
    entry_price: float, direction: str, distance_frac: float, config: dict,
) -> dict:
    """Apply the min/max stop-distance bounds and compute the final stop price.

    distance_frac is a fraction of entry_price (e.g. 0.02 = 2%) BEFORE
    bounds are applied. signals.min_stop_pct / max_stop_pct are configured
    as percentage numbers (1.0 = 1%), not fractions, so both are divided by
    100 here before clamping.

    Shared by the GARCH path and the ATR fallback so both stops are bounded
    identically regardless of which one produced the raw distance.
    """
    sig          = config.get("signals", {})
    min_stop_pct = float(sig.get("min_stop_pct", 1.0)) / 100.0
    max_stop_pct = float(sig.get("max_stop_pct", 25.0)) / 100.0

    distance_frac = max(min_stop_pct, min(distance_frac, max_stop_pct))
    stop_distance = distance_frac * entry_price
    stop_price = (
        entry_price - stop_distance if direction == "LONG" else entry_price + stop_distance
    )
    return {
        "stop_price": round(stop_price, 6),
        "stop_distance_pct": round(distance_frac * 100, 4),
    }


def _atr_fallback_stop(
    ticker: str, entry_price: float, direction: str, config: dict,
) -> dict:
    """ATR-based stop — the pre-GARCH behaviour, and compute_garch_stop()'s
    fallback when GARCH can't be trusted (too little data, fitting failure).

    Also used directly by main.py to display a stop for positions opened
    before this feature existed (t212_invest_orders.stop_price is NULL).

    Sizing uses ATR as a PERCENTAGE of price (atr_pct = ATR / close) from
    *ticker*'s own OHLC history, not a raw price-unit ATR value — the same
    design choice compute_garch_stop() makes for its GARCH distance, and for
    the same reason: entry_price here may be a UCITS/stock instrument's
    fill price in a different currency/scale than the research ticker's own
    quotes (see CLAUDE.md's "Quantity sizing note"). A fractional ATR%
    applied to whatever entry_price actually is stays currency-agnostic;
    reusing a raw ATR value computed in the research ticker's own currency
    would reproduce that exact bug class.
    """
    sig        = config.get("signals", {})
    atr_period = int(sig.get("atr_period", 14))
    atr_stop_multiplier = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))

    result = {
        "stop_price": None,
        "stop_distance_pct": None,
        "garch_daily_vol": None,
        "garch_holding_vol": None,
        "vol_source": "atr_fallback",
    }

    try:
        from data.ingestion import get_price_df  # noqa: PLC0415  local import — keeps this module DB-free otherwise
        df = get_price_df(ticker, days=400)
        if df.empty or len(df) < atr_period + 5:
            # No usable ATR either — apply the floor distance so a stop
            # still exists rather than returning None outright.
            bounds = _stop_price_and_distance(entry_price, direction, 0.0, config)
            result.update(bounds)
            return result

        close = df["close"].astype(float)
        high  = df["high"].astype(float)
        low   = df["low"].astype(float)
        if _TALIB_AVAILABLE:
            atr_series = pd.Series(
                talib.ATR(high.values, low.values, close.values, atr_period), index=close.index,
            )
        else:
            atr_series = _pandas_atr(high, low, close, atr_period)
        atr_pct = (atr_series / close.replace(0, np.nan)).iloc[-1]
        distance_frac = atr_stop_multiplier * (float(atr_pct) if pd.notna(atr_pct) else 0.0)
    except Exception as exc:
        logger.warning("_atr_fallback_stop: ATR computation failed for %s: %s", ticker, exc)
        distance_frac = 0.0

    bounds = _stop_price_and_distance(entry_price, direction, distance_frac, config)
    result.update(bounds)
    return result


def _garch_daily_vol_core(log_returns: np.ndarray) -> Optional[float]:
    """Fit GARCH(1,1) on already-computed log returns and return the 1-day-
    ahead volatility forecast (a fraction, e.g. 0.02 = 2%), or None if
    fitting fails or doesn't converge.

    Pure — no DB access — so it's safe to call both from compute_garch_stop()
    below (live path, log returns from get_price_df() relative to today) and
    from analysis/backtest.py's monthly vol cache (log returns sliced
    strictly before a historical month-end, to stay look-ahead-free). This
    is the same shared-core pattern _var_position_size_core() below uses for
    VaR position sizing, for the same reason: the DB-fetching wrapper reads
    relative to "now", which a backtest can never use directly.
    """
    try:
        from arch import arch_model  # noqa: PLC0415  optional heavy import, only needed for GARCH stops
        # arch's optimizer performs best when the input scale is roughly
        # between 1 and 1000. A single fixed multiplier can't hit that target
        # for every ticker (a calm bond ETF and a volatile stock need very
        # different factors), so rescale=True lets arch pick its own factor
        # per fit — recorded on the result as res.scale — rather than a
        # hardcoded x100 that still triggered DataScaleWarning for calm
        # tickers (verified empirically against SHY/IEF/MUB). forecast()
        # returns variance in that internally-rescaled space, so it must be
        # divided by res.scale (not 100) to get back to a decimal fraction.
        am = arch_model(np.asarray(log_returns, dtype=float), vol="Garch", p=1, q=1, dist="normal", rescale=True)
        res = am.fit(disp="off", show_warning=False)
        if res.convergence_flag != 0:
            return None
        forecast = res.forecast(horizon=1)
        scale = res.scale if res.scale else 1.0
        daily_vol = float(forecast.variance.iloc[-1, 0] ** 0.5) / scale
    except Exception:
        return None

    if not np.isfinite(daily_vol) or daily_vol <= 0:
        return None
    return daily_vol


def compute_garch_stop(
    ticker: str,
    entry_price: float,
    direction: str,
    config: dict,
    change_point_date: Optional[date] = None,
) -> dict:
    """Compute a GARCH(1,1)-based dynamic stop loss for *ticker*.

    Stop distance = garch_stop_multiplier x GARCH 1-day-ahead volatility
    forecast x sqrt(holding_days) x entry_price — a forward-looking estimate
    that weights recent volatility more heavily during volatile periods and
    less during calm ones, unlike a flat trailing ATR(14) average.

    Args:
        ticker: Research ticker to calibrate GARCH on (its own OHLC history
            — entry_price may be a different instrument's price, e.g. a
            UCITS wrapper; only the ticker's fractional/percentage return
            volatility is used, which stays valid regardless of entry_price's
            currency or scale — see _atr_fallback_stop()'s docstring for the
            same reasoning applied to the ATR fallback).
        entry_price: Actual fill price (native instrument currency) — pass
            the confirmed fill price when known, not an estimate, since this
            becomes the stop's reference point.
        direction: "LONG" or "SHORT".
        config: Loaded config.yaml dict.
        change_point_date: A structural-break date (detect_change_points()'s
            last_change_point) to calibrate GARCH on post-break data only,
            when that break is recent (within signals.cpd_recent_window
            days of the latest stored price). This is the integration point
            with change point detection: a ticker that just entered a new
            volatility regime should have its GARCH stop reflect that new
            regime, not blend in the stale pre-break history.

    Returns a dict:
        stop_price          — absolute stop loss price, same currency/scale as entry_price
        stop_distance_pct   — stop distance as % of entry_price (a percentage number, e.g. 3.5 = 3.5%)
        garch_daily_vol     — GARCH 1-day-ahead volatility forecast (fraction), or None on fallback
        garch_holding_vol    — daily_vol scaled by sqrt(holding_days), or None on fallback
        vol_source          — "garch" or "atr_fallback"

    Falls back to _atr_fallback_stop() (vol_source: "atr_fallback") when
    fewer than signals.garch_min_observations daily returns are available,
    or when the GARCH fit itself fails (non-convergence, numerical error) —
    logged as a warning either way, never raised.
    """
    sig               = config.get("signals", {})
    lookback_days     = int(sig.get("garch_lookback_days", 504))
    stop_multiplier   = float(sig.get("garch_stop_multiplier", 2.0))
    min_observations  = int(sig.get("garch_min_observations", _MIN_GARCH_OBSERVATIONS_DEFAULT))
    recent_window     = int(sig.get("cpd_recent_window", 90))

    from analysis.regime import get_max_holding_days  # noqa: PLC0415  local import — avoid circular import
    holding_days = get_max_holding_days(ticker, config)

    def _fallback(reason: str) -> dict:
        logger.warning(
            "compute_garch_stop: %s for %s — falling back to ATR stop.", reason, ticker,
        )
        return _atr_fallback_stop(ticker, entry_price, direction, config)

    from data.ingestion import get_price_df  # noqa: PLC0415  local import — keeps this module DB-free otherwise
    df = get_price_df(ticker, days=int(lookback_days * 1.6) + 30)
    if df.empty:
        return _fallback("no price history")

    df = df.sort_values("date").tail(lookback_days + 1).reset_index(drop=True)

    if change_point_date is not None:
        latest_date = df["date"].iloc[-1]
        latest_date_only = latest_date.date() if hasattr(latest_date, "date") else latest_date
        days_since_break = (latest_date_only - change_point_date).days
        if 0 <= days_since_break <= recent_window:
            post_df = df[df["date"] >= pd.Timestamp(change_point_date)]
            if len(post_df) >= min_observations:
                df = post_df
            # else: not enough post-break data to calibrate on alone — keep
            # the full lookback window rather than shrinking below the
            # minimum observation count. With the shipped defaults
            # (cpd_recent_window: 90, garch_min_observations: 60) a break
            # within the 90-day recent window can supply up to ~91 post-break
            # rows, comfortably above the 60-row minimum, so this branch
            # activates in the normal case rather than being the fallback —
            # garch_min_observations was originally 100, which made this
            # branch effectively unreachable (a "recent" break could never
            # supply 100 rows); lowered to 60 to fix that.

    close = df["close"].astype(float)
    log_returns = np.log(close / close.shift(1)).dropna()

    if len(log_returns) < min_observations:
        return _fallback(f"only {len(log_returns)} return observations, need >= {min_observations}")

    daily_vol = _garch_daily_vol_core(log_returns.to_numpy())
    if daily_vol is None:
        return _fallback("GARCH fitting failed or did not converge")

    holding_vol = daily_vol * math.sqrt(max(holding_days, 1))
    distance_frac = stop_multiplier * holding_vol

    result = _stop_price_and_distance(entry_price, direction, distance_frac, config)
    result["garch_daily_vol"] = daily_vol
    result["garch_holding_vol"] = holding_vol
    result["vol_source"] = "garch"
    return result


# ---------------------------------------------------------------------------
# VaR-based position sizing
# ---------------------------------------------------------------------------

# Minimum number of daily log returns required before a VaR estimate is
# trusted. Below this, compute_var_position_size() falls back to a flat
# fraction of max_position_gbp rather than sizing off a thin sample.
_MIN_VAR_RETURNS = 60


def _var_position_size_core(
    log_returns: "pd.Series | np.ndarray",
    account_value_gbp: float,
    max_position_gbp: float,
    holding_days: int,
    var_confidence: float,
    max_risk_pct: float,
    min_position_gbp: float,
) -> tuple[float, float, float]:
    """Pure VaR position-sizing math — no DB access, no look-ahead risk.

    Historical VaR: the (1 - var_confidence) percentile of the daily
    log-return distribution (e.g. the 5th percentile at 95% confidence).
    Historical (percentile-based) rather than Parametric VaR because
    day-to-day equity/ETF returns are fat-tailed — a normal-distribution
    assumption understates tail risk. Not Monte Carlo VaR — needlessly
    expensive for a sizing calculation that has to run for ~64 tickers on
    every EOD pass.

    Scaled to the position's actual holding period via the square-root-of-time
    rule (holding_var = daily_var * sqrt(holding_days)) since a longer hold
    has more time to accumulate an adverse move. Position size is then set so
    that a holding-period move of that magnitude would lose at most
    max_risk_pct of account_value_gbp: position_gbp = (account_value_gbp *
    max_risk_pct) / abs(holding_var). Floored at min_position_gbp, capped at
    max_position_gbp — max_position_gbp remains a hard ceiling regardless of
    how low volatility gets.

    Shared by compute_var_position_size() below (the live-trading path, which
    reads price_data via get_price_df()) and analysis/backtest.py:
    simulate_trades() (which cannot use get_price_df() directly, since
    that always fetches relative to *today* — a historical backtest instead
    passes in log returns computed from prices strictly before the entry
    date, to avoid look-ahead bias). Keeping the formula in one place means
    live and backtested position sizing can never quietly drift apart.

    Returns (position_gbp, daily_var, holding_var). daily_var and holding_var
    are returned so callers can log/display them without recomputing.
    """
    daily_var = float(np.percentile(log_returns, (1 - var_confidence) * 100))
    holding_var = daily_var * math.sqrt(max(holding_days, 1))

    if holding_var == 0:
        # No observed downside at all in the sample (e.g. a perfectly flat
        # series) — nothing to constrain against, so use the full ceiling.
        return max_position_gbp, daily_var, holding_var

    position_gbp = (account_value_gbp * max_risk_pct) / abs(holding_var)
    position_gbp = max(min_position_gbp, min(position_gbp, max_position_gbp))
    return round(position_gbp, 2), daily_var, holding_var


def compute_var_position_size_detailed(
    ticker: str,
    account_value_gbp: float,
    max_position_gbp: float,
    config: dict,
    confidence: float = 0.95,
    section: str = "trading212_invest",
) -> tuple[float, "float | None", "float | None"]:
    """Return (position_gbp, daily_var, holding_var) for *ticker* (live-trading path).

    Reads the last var_lookback_days of daily closes for *ticker* from the
    stored price_data table (no yfinance call — must stay fast enough for
    ~64 tickers on every EOD run) and sizes the position so its expected
    holding-period loss at *confidence* (default 95%) stays within
    config[section]["max_risk_pct"] of account_value_gbp. See
    _var_position_size_core() above for the full formula.

    config[section] (either "trading212_invest" or "trading212_stocks" — both
    carry an identical set of keys, sized independently since ETF and stock
    risk tolerance can differ) supplies:
      var_confidence     (default: the *confidence* parameter, 0.95)
      var_lookback_days  (default 252 — ~1 trading year)
      max_risk_pct       (default 0.01 — 1% of account value)
      min_position_gbp   (default 50)

    Falls back to max_position_gbp * 0.5 (logged as a warning) when fewer
    than _MIN_VAR_RETURNS (60) daily returns are available — a newly-listed
    ticker or a data gap shouldn't block sizing entirely, but also shouldn't
    be sized off a handful of noisy observations. daily_var/holding_var are
    None in the fallback case, since no VaR was actually computed.

    This "detailed" variant exists (rather than making compute_var_position_size()
    itself return a tuple) so brokers/trading212.py can log and persist the
    holding-period VaR alongside the order (t212_invest_orders.var_pct, shown
    on the Performance page) without a second DB round-trip to recompute it —
    see that module's BUY-phase call sites. compute_var_position_size() below
    is a thin wrapper for callers that only need the position size.
    """
    sect_cfg = config.get(section, {})
    var_confidence   = float(sect_cfg.get("var_confidence", confidence))
    var_lookback     = int(sect_cfg.get("var_lookback_days", 252))
    max_risk_pct     = float(sect_cfg.get("max_risk_pct", 0.01))
    min_position_gbp = float(sect_cfg.get("min_position_gbp", 50))

    fallback = round(max_position_gbp * 0.5, 2)

    from analysis.regime import get_max_holding_days  # noqa: PLC0415  local import — avoid circular import
    from data.ingestion import get_price_df  # noqa: PLC0415  local import — keeps this module DB-free otherwise

    # var_lookback_days is a count of *trading* days; fetch a generous
    # calendar-day window (1.6x plus a small buffer) so that many trading
    # days are comfortably included after weekends/holidays, then trim with
    # tail() below to exactly what's needed.
    df = get_price_df(ticker, days=int(var_lookback * 1.6) + 30)
    if df.empty or len(df) < _MIN_VAR_RETURNS:
        logger.warning(
            "compute_var_position_size: insufficient price history for %s "
            "(%d rows, need >= %d) — falling back to max_position_gbp * 0.5 (£%.0f).",
            ticker, len(df), _MIN_VAR_RETURNS, fallback,
        )
        return fallback, None, None

    close = df["close"].astype(float).tail(var_lookback + 1)
    log_returns = np.log(close / close.shift(1)).dropna()
    if len(log_returns) < _MIN_VAR_RETURNS:
        logger.warning(
            "compute_var_position_size: insufficient return history for %s "
            "(%d returns, need >= %d) — falling back to max_position_gbp * 0.5 (£%.0f).",
            ticker, len(log_returns), _MIN_VAR_RETURNS, fallback,
        )
        return fallback, None, None

    holding_days = get_max_holding_days(ticker, config)
    position_gbp, daily_var, holding_var = _var_position_size_core(
        log_returns, account_value_gbp, max_position_gbp, holding_days,
        var_confidence, max_risk_pct, min_position_gbp,
    )

    log_prefix = "T212 Invest" if section == "trading212_invest" else "T212 Stocks"
    logger.info(
        "%s: VaR sizing %s — daily VaR=%.2f%%, holding VaR=%.2f%%, "
        "position=£%.0f (account=£%.0f, max=£%.0f)",
        log_prefix, ticker, daily_var * 100, holding_var * 100,
        position_gbp, account_value_gbp, max_position_gbp,
    )

    return position_gbp, daily_var, holding_var


def compute_var_position_size(
    ticker: str,
    account_value_gbp: float,
    max_position_gbp: float,
    config: dict,
    confidence: float = 0.95,
    section: str = "trading212_invest",
) -> float:
    """Return just the VaR-scaled GBP position size for *ticker*.

    Thin wrapper around compute_var_position_size_detailed() for callers that
    don't need the daily/holding VaR values themselves.
    """
    position_gbp, _daily_var, _holding_var = compute_var_position_size_detailed(
        ticker, account_value_gbp, max_position_gbp, config, confidence, section,
    )
    return position_gbp
