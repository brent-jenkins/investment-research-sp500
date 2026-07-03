"""Volatility indicators: ATR, Bollinger Band width, realised vol, VIX regime."""

import logging
import math

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
