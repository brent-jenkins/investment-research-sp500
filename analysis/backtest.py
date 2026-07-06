"""Vectorized backtesting engine.

Replays the full signal-generation pipeline over historical price data using
pandas rolling/ewm operations, which are inherently look-ahead free — each
computation at date T only uses data up to and including T.

Entry point: run_backtest()  — writes results to backtest_runs / backtest_signals
                                / backtest_trades tables and returns the run_id.

The engine replicates the scoring logic of the five main analysis modules
(trend, mean_reversion, volatility, volume, macro) as time-series operations
rather than per-date scalar computations.  The correlation module is omitted
because it requires pair-level data and contributes a fixed 0.10 weight; its
absence is compensated by normalising confluence over the five present modules.
"""

import json
import logging
import math
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import (
    BacktestRun,
    BacktestSignal,
    BacktestTrade,
    get_engine,
)
from data.ingestion import get_price_df

logger = logging.getLogger(__name__)

try:
    import talib as _talib
    _TALIB = True
except ImportError:
    _TALIB = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    p = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(p) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Vectorized indicator series
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_fast   = close.ewm(span=fast, adjust=False).mean()
    ema_slow   = close.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close  = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    alpha      = 1.0 / period
    smooth_tr  = pd.Series(tr,       index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_p   = pd.Series(plus_dm,  index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_m   = pd.Series(minus_dm, index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    pdi  = 100 * smooth_p / smooth_tr.replace(0, np.nan)
    mdi  = 100 * smooth_m / smooth_tr.replace(0, np.nan)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Vectorized module scoring — returns integer Series (-1 / 0 / 1) per date
# ---------------------------------------------------------------------------

def _ma_cross_vote(sma_50: pd.Series, sma_200: pd.Series, slope_window: int, near_pct: float) -> pd.Series:
    """Vectorized version of analysis/trend.py:_ma_cross_vote — see there for rationale.

    A pure positional SMA50-vs-SMA200 check treats a golden cross as bullish
    even while SMA50 is rolling over toward a death cross. This votes with
    the cross direction only while SMA50's slope agrees with it; otherwise
    the vote goes neutral, or flips if the lines have converged to within
    `near_pct` of each other (an imminent cross).
    """
    positional = pd.Series(np.where(sma_50 > sma_200, 1, -1), index=sma_50.index)
    prev_sma50 = sma_50.shift(slope_window)
    slope_agrees = ((sma_50 - prev_sma50) * positional) >= 0
    gap_pct = (sma_50 - sma_200).abs() / sma_200.replace(0, np.nan)
    imminent = gap_pct < near_pct

    vote = pd.Series(
        np.where(slope_agrees, positional, np.where(imminent, -positional, 0)),
        index=sma_50.index,
    )
    return vote.where(prev_sma50.notna(), positional).astype(int)


def _trend_scores(df: pd.DataFrame, config: dict) -> pd.Series:
    sig      = config["signals"]
    adx_thr  = sig["adx_trend_threshold"]
    slope_w  = int(sig.get("ma_cross_slope_window", 10))
    near_pct = float(sig.get("ma_cross_near_pct", 0.02))
    close    = df["close"].astype(float)
    high     = df["high"].astype(float)
    low      = df["low"].astype(float)

    sma_50  = close.rolling(50).mean()
    sma_200 = close.rolling(200).mean()
    macd_l, macd_s = _macd(close, sig["macd_fast"], sig["macd_slow"], sig["macd_signal"])
    adx_s   = _adx(high, low, close, sig["adx_period"])

    # Three votes
    v1 = pd.Series(np.where(close > sma_200, 1, -1), index=close.index)
    v2 = _ma_cross_vote(sma_50, sma_200, slope_w, near_pct)
    macd_vote = np.where(macd_l > macd_s, 1, -1)
    v3 = pd.Series(np.where(adx_s >= adx_thr, macd_vote, 0), index=close.index)

    valid   = sma_200.notna()
    bull    = (v1 == 1).astype(int) + (v2 == 1).astype(int) + (v3 == 1).astype(int)
    bear    = (v1 == -1).astype(int) + (v2 == -1).astype(int) + (v3 == -1).astype(int)
    raw     = pd.Series(np.where(bull >= 2, 1, np.where(bear >= 2, -1, 0)), index=close.index)
    return raw.where(valid, 0).astype(int)


def _mean_rev_scores(df: pd.DataFrame, config: dict) -> pd.Series:
    """50% RSI + 50% 52-week Z-score — mirrors compute_mean_reversion()'s live
    scoring. Bollinger %B is deliberately not computed here: a production
    correlation analysis (200 rows, 49 tickers, 2026-07-05) measured RSI vs
    Bollinger %B at 0.927, so it was dropped from the score in both the live
    and backtest paths — see analysis/mean_reversion.py's docstring."""
    sig     = config["signals"]
    close   = df["close"].astype(float)

    rsi_s   = _rsi(close, sig["rsi_period"])
    roll252 = close.rolling(252)
    z_score = (close - roll252.mean()) / roll252.std().replace(0, np.nan)

    rsi_sig = pd.Series(np.where(rsi_s < 30, 1, np.where(rsi_s > 70, -1, 0)), index=close.index)
    z_sig   = pd.Series(np.where(z_score < -1.5, 1, np.where(z_score > 1.5, -1, 0)), index=close.index)

    raw  = pd.Series(
        np.where((rsi_sig == 1) & (z_sig == 1), 1,
        np.where((rsi_sig == -1) & (z_sig == -1), -1, 0)),
        index=close.index,
    )
    return raw.astype(int)


def _vol_scores(df: pd.DataFrame, vix_s: Optional[pd.Series], config: dict) -> pd.Series:
    """ATR% percentile + VIX regime (equal-weight combination, mirroring
    compute_volatility()'s live scoring minus its change-point adjustment —
    see the "Scope note" in CLAUDE.md's Volatility module section for why
    CPD isn't vectorised into the backtest). hist_vol_20 was never a scoring
    input here to begin with (only ATR% and VIX ever were) — the production
    correlation analysis that dropped hist_vol_20 from the live score
    (ATR% vs hist_vol_20: 0.929, see analysis/volatility.py's docstring)
    changes nothing on this path."""
    sig     = config["signals"]
    close   = df["close"].astype(float)
    high    = df["high"].astype(float)
    low     = df["low"].astype(float)

    atr_s   = _atr(high, low, close, sig["atr_period"])
    atr_pct = atr_s / close.replace(0, np.nan)

    # Approximate 25th/75th percentile via rolling mean ± 0.674σ
    rm = atr_pct.rolling(252).mean()
    rs = atr_pct.rolling(252).std()
    lo_thr = rm - 0.674 * rs
    hi_thr = rm + 0.674 * rs
    atr_sc  = pd.Series(
        np.where(atr_pct <= lo_thr, 1, np.where(atr_pct >= hi_thr, -1, 0)),
        index=close.index,
    )

    vix_sc      = pd.Series(0, index=close.index, dtype=int)
    extreme_vix = pd.Series(False, index=close.index)
    if vix_s is not None:
        va = vix_s.reindex(close.index).ffill()
        vix_sc      = pd.Series(np.where(va < 12, 1, np.where(va >= 20, -1, 0)), index=close.index)
        extreme_vix = va >= 30

    raw = pd.Series(
        np.where(extreme_vix, -1,
        np.where((atr_sc == 1) & (vix_sc >= 0), 1,
        np.where((atr_sc == -1) | (vix_sc == -1), -1, 0))),
        index=close.index,
    )
    return raw.astype(int)


def _volume_scores(df: pd.DataFrame, config: dict) -> pd.Series:
    period  = config["signals"]["volume_avg_period"]
    close   = df["close"].astype(float)
    volume  = df["volume"].astype(float)

    direction = np.sign(close.diff().fillna(0))
    obv       = (direction * volume).cumsum()
    obv_trend = obv - obv.shift(period)
    vol_ratio = volume / volume.rolling(period).mean().replace(0, np.nan)
    price_ch  = close.diff()

    elevated = vol_ratio >= 1.5
    raw = pd.Series(
        np.where(elevated & (price_ch > 0) & (obv_trend > 0), 1,
        np.where(elevated & (price_ch < 0) & (obv_trend < 0), -1, 0)),
        index=close.index,
    )
    return raw.astype(int)


def _macro_scores(
    df: pd.DataFrame,
    ticker: str,
    vix_s: Optional[pd.Series],
    macro_df: Optional[pd.DataFrame],
) -> pd.Series:
    _MACRO_SKIP = frozenset({"^VIX", "TLT", "IEF", "HYG"})
    close = df["close"].astype(float)

    if ticker in _MACRO_SKIP:
        return pd.Series(0, index=close.index, dtype=int)

    vix_sc = pd.Series(0.0, index=close.index)
    if vix_s is not None:
        va = vix_s.reindex(close.index).ffill()
        vix_sc = pd.Series(np.where(va < 12, 1.0, np.where(va < 20, 0.0, -1.0)), index=close.index)

    components = [vix_sc]
    if macro_df is not None and not macro_df.empty:
        aligned = macro_df.reindex(close.index).ffill().fillna(0)
        for col in aligned.columns:
            components.append(aligned[col].astype(float))

    avg = pd.concat(components, axis=1).mean(axis=1)
    raw = pd.Series(np.where(avg >= 0.25, 1, np.where(avg <= -0.25, -1, 0)), index=close.index)
    return raw.astype(int)


# ---------------------------------------------------------------------------
# Regime history (vectorized, based on ^GSPC + VIX)
# ---------------------------------------------------------------------------

def _regime_history(gspc_df: pd.DataFrame, vix_s: Optional[pd.Series], config: dict) -> pd.DataFrame:
    """Return a date-indexed DataFrame with 'regime', 'hmm_bull_probability',
    'hmm_downgraded', and 'hmm_upgraded' columns.

    'regime' is the rule-based ADX/VIX/SMA200 classification (unchanged from
    before the HMM feature), gated afterward by _resolve_hmm_gate() exactly
    the way analysis.regime.detect_regime() gates its own live rule-based
    call in both directions (TRENDING_BULL -> MIXED downgrade, MIXED ->
    TRENDING_BULL upgrade) — see that function's docstring for the full
    rationale. Since this is computed ONCE per backtest run from the single
    shared ^GSPC series (not once per ticker — every ticker's
    compute_signal_history() call reuses this same DataFrame), the HMM gate
    here is also only ever evaluated once per run, not multiplied by the
    ticker count.
    """
    sig     = config["signals"]
    close   = gspc_df["close"].astype(float)
    high    = gspc_df["high"].astype(float)
    low     = gspc_df["low"].astype(float)

    sma_200 = close.rolling(200).mean()
    adx_s   = _adx(high, low, close, sig["adx_period"])

    vix = pd.Series(np.nan, index=close.index)
    if vix_s is not None:
        vix = vix_s.reindex(close.index).ffill()

    regime = np.where(
        vix > 30, "HIGH_VOLATILITY",
        np.where(
            (close > sma_200) & (adx_s > 25) & (vix < 20), "TRENDING_BULL",
            np.where(
                (close < sma_200) & (adx_s > 25) & (vix > 20), "TRENDING_BEAR",
                np.where(
                    (adx_s < 20) & (vix < 15), "RANGING_LOW_VOL",
                    "MIXED",
                ),
            ),
        ),
    )
    regime = np.where(sma_200.isna(), "MIXED", regime)

    hmm_bull_probability = np.full(len(close), np.nan)
    hmm_downgraded = np.zeros(len(close), dtype=bool)
    hmm_upgraded = np.zeros(len(close), dtype=bool)

    if sig.get("hmm_enabled", True):
        try:
            from analysis.hmm_regime import _build_features as _hmm_build_features
            full_features = _hmm_build_features(gspc_df, config)
            if not full_features.empty:
                regime, hmm_bull_probability, hmm_downgraded, hmm_upgraded = _resolve_hmm_gate(
                    regime, close.index, full_features, config,
                )
        except Exception as exc:
            logger.warning(
                "Backtest: HMM regime gate failed, falling back to rule-based regime only: %s", exc,
            )

    return pd.DataFrame(
        {
            "regime": regime,
            "hmm_bull_probability": hmm_bull_probability,
            "hmm_downgraded": hmm_downgraded,
            "hmm_upgraded": hmm_upgraded,
        },
        index=close.index,
    )


# ---------------------------------------------------------------------------
# Monthly HMM regime-confidence cache — backtest mirror of the live HMM gate
# ---------------------------------------------------------------------------
#
# Refitting a 3-state GaussianHMM at every date the rule-based regime says
# TRENDING_BULL would be needlessly expensive (each fit is tens of
# milliseconds of EM iteration) for what is, in a multi-year backtest,
# potentially hundreds of dates. As with the GARCH/CPD monthly vol cache
# above, the model is instead calibrated once per calendar month-end (using
# only ^GSPC feature data strictly before that month-end — no look-ahead)
# and every date within that month is checked against that same fixed model.
# Unlike the GARCH cache, this only ever runs once per backtest run (the
# regime series is shared across all tickers — see _regime_history()'s
# docstring), so there is no per-ticker multiplication to worry about.
_MONTHLY_HMM_CACHE: dict[date, dict] = {}


def _build_monthly_hmm_cache(
    full_features: pd.DataFrame, start_date: date, end_date: date, config: dict,
) -> dict[date, dict]:
    """Fit a GaussianHMM as of every calendar month-end between start_date
    and end_date, using only full_features rows strictly before that
    month-end. Reuses analysis.hmm_regime's pure, DB-free
    _standardize()/_fit_hmm_core()/_label_states() so a backtested gate
    calibrates identically to a live weekly refit computed from the same
    historical data.

    full_features must already be look-ahead free by construction (each row
    depends only on data up to and including that row) — see
    analysis.hmm_regime._build_features()'s docstring — so slicing it by
    date here introduces no additional look-ahead of its own.

    Returns {month_end_date: {"model": GaussianHMM | None, "feature_mean":
    np.ndarray | None, "feature_std": np.ndarray | None, "state_labels":
    dict[int, str] | None}}. A None model means there wasn't yet
    signals.hmm_min_training_days of pre-month-end history — the same
    "insufficient data" state get_hmm_regime_confidence() returns live —
    and _resolve_hmm_gate() below treats that as "no gate available",
    leaving the rule-based call as-is for those early dates.
    """
    from analysis.hmm_regime import _standardize, _fit_hmm_core, _label_states, FEATURE_NAMES

    sig = config.get("signals", {})
    min_days = int(sig.get("hmm_min_training_days", 504))

    month_ends = list(pd.date_range(start_date, end_date, freq="ME"))
    if not month_ends:
        month_ends = [pd.Timestamp(end_date)]

    if all((me.date() in _MONTHLY_HMM_CACHE) for me in month_ends):
        return {me.date(): _MONTHLY_HMM_CACHE[me.date()] for me in month_ends}

    empty_entry = {"model": None, "feature_mean": None, "feature_std": None, "state_labels": None}
    cache: dict[date, dict] = {}

    for me in month_ends:
        month_end_date = me.date()
        if month_end_date in _MONTHLY_HMM_CACHE:
            cache[month_end_date] = _MONTHLY_HMM_CACHE[month_end_date]
            continue

        prior = full_features[full_features.index < me]
        if len(prior) < min_days:
            cache[month_end_date] = empty_entry
            _MONTHLY_HMM_CACHE[month_end_date] = empty_entry
            continue

        X, feature_mean, feature_std = _standardize(prior)
        model = _fit_hmm_core(X)
        if model is None:
            cache[month_end_date] = empty_entry
            _MONTHLY_HMM_CACHE[month_end_date] = empty_entry
            continue

        state_labels = _label_states(model.means_, FEATURE_NAMES)
        entry = {
            "model": model, "feature_mean": feature_mean,
            "feature_std": feature_std, "state_labels": state_labels,
        }
        cache[month_end_date] = entry
        _MONTHLY_HMM_CACHE[month_end_date] = entry

    return cache


def _resolve_hmm_gate(
    regime_array: np.ndarray, dates: pd.DatetimeIndex, full_features: pd.DataFrame, config: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Apply the HMM confidence gate to a rule-based regime array — the
    backtest mirror of analysis.regime.detect_regime()'s live gate. Returns
    (gated_regime, hmm_bull_probability, hmm_downgraded, hmm_upgraded), all
    aligned to *dates*.

    Two independent directions are checked, mirroring the live gate exactly:

    - TRENDING_BULL dates: downgraded to MIXED when HMM bull probability
      falls below signals.hmm_bull_confidence_threshold.
    - MIXED dates (only when signals.hmm_upgrade_enabled): upgraded to
      TRENDING_BULL when HMM bull probability is at/above
      signals.hmm_upgrade_threshold.

    Every other regime (TRENDING_BEAR, RANGING_LOW_VOL, HIGH_VOLATILITY) is
    informational-only and left untouched, same as live. For each checked
    date, the observation array passed to predict_proba() ends exactly at
    that date — never later — even though dates within the same calendar
    month share one fitted model (see _build_monthly_hmm_cache()):
    hmmlearn's predict_proba() is a forward-BACKWARD smoother, not a pure
    forward filter, so an array extending past date d would let its
    reported probability for d be influenced by observations *after* d, a
    genuine look-ahead leak. Grouping only the model FIT by month (not the
    per-date predict_proba call) avoids this while still keeping the
    number of HMM fits bounded by month count rather than per-date checks.
    """
    sig = config.get("signals", {})
    threshold         = float(sig.get("hmm_bull_confidence_threshold", 0.60))
    upgrade_enabled   = bool(sig.get("hmm_upgrade_enabled", True))
    upgrade_threshold = float(sig.get("hmm_upgrade_threshold", 0.90))

    gated = regime_array.copy()
    bull_prob  = np.full(len(dates), np.nan)
    downgraded = np.zeros(len(dates), dtype=bool)
    upgraded   = np.zeros(len(dates), dtype=bool)

    bull_positions  = np.where(regime_array == "TRENDING_BULL")[0]
    mixed_positions = np.where(regime_array == "MIXED")[0] if upgrade_enabled else np.array([], dtype=int)
    check_positions = np.union1d(bull_positions, mixed_positions)
    if len(check_positions) == 0:
        return gated, bull_prob, downgraded, upgraded

    start_range = dates[check_positions[0]].date()
    end_range   = dates[check_positions[-1]].date()
    month_cache = _build_monthly_hmm_cache(full_features, start_range, end_range, config)

    for pos in check_positions:
        d = dates[pos]
        month_end_key = (pd.Timestamp(d) + pd.offsets.MonthEnd(-1)).date()
        entry = month_cache.get(month_end_key)
        if entry is None or entry.get("model") is None:
            continue  # insufficient pre-month-end history yet — leave the rule-based call as-is

        window = full_features[full_features.index <= d]
        if window.empty:
            continue

        X = (window.to_numpy() - entry["feature_mean"]) / entry["feature_std"]
        try:
            proba = entry["model"].predict_proba(X)
        except Exception:
            continue

        latest = proba[-1]
        p_bull = 0.0
        for state_idx, label in entry["state_labels"].items():
            if label == "BULL" and 0 <= state_idx < len(latest):
                p_bull = float(latest[state_idx])
                break

        bull_prob[pos] = p_bull
        if regime_array[pos] == "TRENDING_BULL":
            if p_bull < threshold:
                gated[pos] = "MIXED"
                downgraded[pos] = True
        elif regime_array[pos] == "MIXED":
            if p_bull >= upgrade_threshold:
                gated[pos] = "TRENDING_BULL"
                upgraded[pos] = True

    return gated, bull_prob, downgraded, upgraded


# ---------------------------------------------------------------------------
# Macro history from DB  (all stored rows, not just latest)
# ---------------------------------------------------------------------------

def _load_macro_history() -> Optional[pd.DataFrame]:
    """Return all stored macro_indicator scores as a wide date×series DataFrame."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT series_id, date, score FROM macro_indicators ORDER BY date")
            ).mappings().all()
    except Exception:
        return None

    if not rows:
        return None

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    try:
        wide = df.pivot(index="date", columns="series_id", values="score")
        return wide.astype(float)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Signal history for one ticker
# ---------------------------------------------------------------------------

def compute_signal_history(
    ticker: str,
    df: pd.DataFrame,
    vix_s: Optional[pd.Series],
    regime_hist: pd.DataFrame,
    macro_df: Optional[pd.DataFrame],
    config: dict,
) -> pd.DataFrame:
    """Compute a daily signal series for *ticker* over the full price history.

    Uses vectorized pandas rolling/ewm — inherently look-ahead free.

    Returns a DataFrame indexed by date with columns:
        close, atr, regime, confluence_score, signal,
        trend_score, mean_reversion_score, volatility_score,
        volume_score, macro_score
    """
    from analysis.regime import REGIME_WEIGHTS, Regime          # local to avoid circular
    from analysis.confluence import get_regime_thresholds       # regime-aware thresholds

    if df.empty or len(df) < 210:
        return pd.DataFrame()

    df = df.copy()
    if "date" in df.columns:
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)

    atr_s       = _atr(high, low, close, config["signals"]["atr_period"])
    trend_sc    = _trend_scores(df, config)
    mean_rev_sc = _mean_rev_scores(df, config)
    vol_sc      = _vol_scores(df, vix_s, config)
    volume_sc   = _volume_scores(df, config)
    macro_sc    = _macro_scores(df, ticker, vix_s, macro_df)

    regime_aligned = regime_hist["regime"].reindex(df.index).ffill().fillna("MIXED")
    # hmm_bull_probability/hmm_downgraded/hmm_upgraded are NOT forward-filled
    # like regime above: _resolve_hmm_gate() (analysis/backtest.py) only ever
    # populates hmm_bull_probability on dates the rule-based regime was
    # actually TRENDING_BULL or MIXED — every other date is a deliberate NaN
    # meaning "the gate was never evaluated here" (informational-only
    # regimes don't need an HMM opinion). Forward-filling would replace that
    # "not evaluated" NaN with a stale probability from the last time it
    # *was* evaluated, misrepresenting every subsequent BEAR/etc. date as if
    # the HMM had just given a fresh (but actually days- or weeks-old)
    # opinion on it — a plain reindex (only introduces NaN for the rare case
    # a ticker's own trading calendar has a date ^GSPC's doesn't) preserves
    # the sparse, accurate signal instead.
    if "hmm_bull_probability" in regime_hist.columns:
        hmm_bull_probability_aligned = regime_hist["hmm_bull_probability"].reindex(df.index)
    else:
        hmm_bull_probability_aligned = pd.Series(np.nan, index=df.index)
    if "hmm_downgraded" in regime_hist.columns:
        hmm_downgraded_aligned = regime_hist["hmm_downgraded"].reindex(df.index).fillna(False).astype(bool)
    else:
        hmm_downgraded_aligned = pd.Series(False, index=df.index)
    if "hmm_upgraded" in regime_hist.columns:
        hmm_upgraded_aligned = regime_hist["hmm_upgraded"].reindex(df.index).fillna(False).astype(bool)
    else:
        hmm_upgraded_aligned = pd.Series(False, index=df.index)

    # Build per-date weight series for the five modules included in backtest.
    # Correlation module (weight=0.10) is excluded; remaining weights are
    # re-normalised so they still sum to 1.0 per date.
    WEIGHT_KEYS = ["trend", "mean_reversion", "volatility", "volume", "macro"]
    regime_map  = {r.value: r for r in Regime}

    w = {k: pd.Series(np.nan, index=df.index) for k in WEIGHT_KEYS}
    for rname in regime_aligned.unique():
        mask = regime_aligned == rname
        r    = regime_map.get(rname, Regime.MIXED)
        rw   = REGIME_WEIGHTS[r]
        total = sum(rw[k] for k in WEIGHT_KEYS)
        for k in WEIGHT_KEYS:
            w[k][mask] = rw[k] / total  # normalise to 1.0 without correlation

    confluence = (
        trend_sc.astype(float) * w["trend"] +
        mean_rev_sc.astype(float) * w["mean_reversion"] +
        vol_sc.astype(float) * w["volatility"] +
        volume_sc.astype(float) * w["volume"] +
        macro_sc.astype(float) * w["macro"]
    )

    # Build per-date threshold series from regime — mirrors the weight computation above
    buy_thresh_s  = pd.Series(0.55, index=df.index, dtype=float)
    sell_thresh_s = pd.Series(0.55, index=df.index, dtype=float)
    for _rname in regime_aligned.unique():
        _mask = regime_aligned == _rname
        _bt, _st = get_regime_thresholds(_rname, config)
        buy_thresh_s[_mask]  = _bt
        sell_thresh_s[_mask] = _st

    def _sig(score: float, buy_th: float, sell_th: float) -> str:
        if score >= buy_th:   return "STRONG_BUY"
        if score >= 0.3:      return "BUY"
        if score <= -sell_th: return "STRONG_SELL"
        if score <= -0.3:     return "SELL"
        return "NEUTRAL"

    signal = pd.Series(
        [_sig(c, b, s) for c, b, s in zip(confluence, buy_thresh_s, sell_thresh_s)],
        index=confluence.index,
    )

    result = pd.DataFrame({
        "close":                close,
        "atr":                  atr_s,
        "regime":               regime_aligned,
        "confluence_score":     confluence,
        "signal":               signal,
        "trend_score":          trend_sc,
        "mean_reversion_score": mean_rev_sc,
        "volatility_score":     vol_sc,
        "volume_score":         volume_sc,
        "macro_score":          macro_sc,
        "hmm_bull_probability": hmm_bull_probability_aligned,
        "hmm_downgraded":       hmm_downgraded_aligned,
        "hmm_upgraded":         hmm_upgraded_aligned,
    })
    result.index.name = "date"
    return result.dropna(subset=["close", "confluence_score"])


# ---------------------------------------------------------------------------
# Monthly CPD/GARCH vol cache — backtest mirror of the live GARCH stop
# ---------------------------------------------------------------------------
#
# The live system (brokers/trading212.py, analysis/volatility.py) refits
# GARCH and reruns change point detection at every order placement — cheap
# there (one fit per ticker per EOD run). Doing the same per trade ENTRY in
# a backtest would be O(n^2): 64 tickers x 7 years x ~347 trades. Instead,
# GARCH/CPD state is calibrated at calendar month-end granularity and every
# trade entry looks up the state as of the end of its own preceding month —
# the same state a live order placed on that entry's date would have seen,
# with no look-ahead (a month-end's entry only ever uses data strictly
# before it, and simulate_trades() always looks up a month-end strictly
# before the entry date itself — see its MonthEnd(-1) usage below).
#
# simulate_trades() calls _build_monthly_vol_cache() one month at a time,
# only for months an actual trade entry falls in (a ticker with 5 entries
# across 7 years needs 5 GARCH fits, not ~84) — measured empirically at
# ~73ms/fit, so eagerly precomputing every month in a wide requested range
# would blow the whole run's performance budget across 64 tickers. Calling
# the function with start_date == end_date == the one needed month is a
# calling-pattern choice; the function's own contract (compute every
# month-end in [start_date, end_date], look-ahead-free) is unchanged and
# still exercised directly by its own tests with a wide date range.

# Cross-run cache keyed on (ticker, month_end_date) — if the same
# ticker/month combination is needed again later in the same process (a
# second backtest run in the same Streamlit session, for example), the
# already-computed GARCH fit / change point is reused rather than redone.
_MONTHLY_VOL_CACHE: dict[tuple[str, date], dict] = {}

# Per-ticker price-history cache, keyed on ticker alone — separate from
# _MONTHLY_VOL_CACHE above (which caches computed results, not raw prices).
# simulate_trades() calls _build_monthly_vol_cache() once per DISTINCT
# calendar month a trade enters (see that function's module docstring for
# why), which for an active ticker can be a dozen or more calls in one
# backtest run; without this, each of those calls would re-fetch the
# ticker's entire price history via get_price_df() — measured at ~150-500ms
# per fetch, which dominated the run time (136s for a 64-ticker/239-trade
# run) far more than the GARCH fits themselves (~73ms each) did.
_TICKER_PRICE_CACHE: dict[str, "pd.DataFrame"] = {}


def _get_cached_price_history(ticker: str, days: int) -> "pd.DataFrame":
    """get_price_df(ticker, days), cached per ticker for this process.

    Safe to reuse across calls with a smaller `days` than an earlier cached
    fetch used, since get_price_df's `days` only ever widens the window —
    a later call asking for fewer days than what's already cached still
    finds everything it needs in the cached (wider) frame. If a later call
    asks for MORE days than is cached, re-fetches with the larger window.
    """
    cached = _TICKER_PRICE_CACHE.get(ticker)
    if cached is not None and cached.attrs.get("_fetched_days", 0) >= days:
        return cached
    df = get_price_df(ticker, days=days)
    if not df.empty:
        df.attrs["_fetched_days"] = days
    _TICKER_PRICE_CACHE[ticker] = df
    return df


def _build_monthly_vol_cache(
    ticker: str,
    start_date: date,
    end_date: date,
    config: dict,
) -> dict[date, dict]:
    """Compute (change_point_date, garch_daily_vol, vol_source) for every
    calendar month-end between start_date and end_date, using ONLY price
    data strictly before that month-end — no look-ahead (verified by test).

    Mirrors the live calibration exactly by reusing the same pure,
    DB-free core functions detect_change_points()/compute_garch_stop() call
    internally: analysis.volatility._icss_change_point() for the change
    point and analysis.volatility._garch_daily_vol_core() for the GARCH
    fit — so a backtested stop and a live stop computed from the same
    historical data would calibrate identically.

    Fetches the ticker's full available price history via get_price_df()
    ONCE (that call has no upper-date bound, so a generous `days` window
    captures everything through "now" — safe for a historical backtest
    since the DB never contains rows beyond what's already elapsed by the
    time this runs), then slices it per month-end in memory — one DB round
    trip regardless of how many month-ends are requested in a single call.

    Returns {month_end_date: {"change_point_date": date|None,
                               "garch_daily_vol": float|None,
                               "vol_source": "garch" | "atr_fallback"}}
    ("atr_fallback" here just means "no GARCH forecast available for this
    month" — simulate_trades() is what actually falls back to the ATR
    stop formula using that signal.)
    """
    from analysis.volatility import _icss_change_point, _garch_daily_vol_core  # noqa: PLC0415

    sig               = config.get("signals", {})
    cpd_lookback      = int(sig.get("cpd_lookback_days", 504))
    cpd_threshold     = float(sig.get("cpd_threshold", 3.0))
    cpd_recent_window = int(sig.get("cpd_recent_window", 90))
    garch_lookback    = int(sig.get("garch_lookback_days", 504))
    garch_min_obs     = int(sig.get("garch_min_observations", 60))

    month_ends = list(pd.date_range(start_date, end_date, freq="ME"))
    if not month_ends:
        month_ends = [pd.Timestamp(end_date)]

    # Skip the price-history fetch entirely if every requested month is
    # already cached from an earlier call in this process.
    if all((ticker, me.date()) in _MONTHLY_VOL_CACHE for me in month_ends):
        return {me.date(): _MONTHLY_VOL_CACHE[(ticker, me.date())] for me in month_ends}

    empty_entry = {"change_point_date": None, "garch_daily_vol": None, "vol_source": "atr_fallback"}

    lookback_days_max = max(cpd_lookback, garch_lookback)
    df = _get_cached_price_history(ticker, int(lookback_days_max * 1.6) + 3650)
    cache: dict[date, dict] = {}
    if df.empty:
        for me in month_ends:
            cache[me.date()] = empty_entry
            _MONTHLY_VOL_CACHE[(ticker, me.date())] = empty_entry
        return cache

    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])

    for me in month_ends:
        month_end_date = me.date()
        cache_key = (ticker, month_end_date)
        if cache_key in _MONTHLY_VOL_CACHE:
            cache[month_end_date] = _MONTHLY_VOL_CACHE[cache_key]
            continue

        prior = df[df["date"] < me]
        entry = dict(empty_entry)

        if len(prior) >= 60:
            close_all = prior["close"].astype(float)

            # --- Change point detection (mirrors detect_change_points()) ---
            cpd_close     = close_all.tail(cpd_lookback + 1)
            cpd_dates     = prior["date"].tail(cpd_lookback + 1)
            log_ret_cpd   = np.log(cpd_close / cpd_close.shift(1)).iloc[1:].reset_index(drop=True)
            ret_dates_cpd = cpd_dates.iloc[1:].reset_index(drop=True)

            change_point_date = None
            if len(log_ret_cpd) >= 60:
                idx = _icss_change_point(log_ret_cpd.to_numpy(), cpd_threshold)
                if idx is not None:
                    cp_ts = ret_dates_cpd.iloc[idx]
                    change_point_date = cp_ts.date() if hasattr(cp_ts, "date") else cp_ts

            # --- GARCH (mirrors compute_garch_stop()'s post-break slicing) ---
            garch_close = close_all.tail(garch_lookback + 1)
            if change_point_date is not None:
                days_since_break = (month_end_date - change_point_date).days
                if 0 <= days_since_break <= cpd_recent_window:
                    post = prior.loc[prior["date"] >= pd.Timestamp(change_point_date), "close"].astype(float)
                    if len(post) >= garch_min_obs:
                        garch_close = post

            log_ret_garch = np.log(garch_close / garch_close.shift(1)).dropna()
            if len(log_ret_garch) >= garch_min_obs:
                daily_vol = _garch_daily_vol_core(log_ret_garch.to_numpy())
                if daily_vol is not None:
                    entry = {
                        "change_point_date": change_point_date,
                        "garch_daily_vol":   daily_vol,
                        "vol_source":        "garch",
                    }

        cache[month_end_date] = entry
        _MONTHLY_VOL_CACHE[cache_key] = entry

    return cache


def _resolve_entry_stop(
    direction: str,
    price: float,
    atr: float,
    holding_days: int,
    month_end_key: Optional[date],
    monthly_cache: dict[date, dict],
    config: dict,
) -> tuple[float, float, str]:
    """Return (stop_price, stop_distance_pct, vol_source) for a new entry.

    When signals.use_garch_stops is true and the cached GARCH vol for the
    nearest prior month-end is available, sizes the stop the same way
    compute_garch_stop() does live: a holding-period-bucketed stop
    multiplier (see analysis.volatility._garch_stop_multiplier_for_holding_
    days() — shared with the live path so the two never calibrate
    differently for the same holding_days) x holding_vol (daily vol scaled
    by sqrt(holding_days)), clamped to [min_stop_pct, max_stop_pct] of entry
    price. Otherwise (feature disabled, or no GARCH vol cached for this
    month — e.g. too little history that early in the backtest) falls back
    to the pre-existing atr_stop_multiplier x ATR formula, UNCHANGED from
    before this feature — unbounded by min/max_stop_pct, matching "retain
    existing ATR behaviour exactly" for the use_garch_stops: false case.
    """
    sig = config.get("signals", {})
    use_garch = bool(sig.get("use_garch_stops", False))

    cached = monthly_cache.get(month_end_key) if month_end_key is not None else None
    daily_vol = cached.get("garch_daily_vol") if cached else None

    if use_garch and daily_vol is not None:
        from analysis.volatility import _garch_stop_multiplier_for_holding_days  # noqa: PLC0415  local import — avoid circular import
        stop_multiplier = _garch_stop_multiplier_for_holding_days(holding_days, config)
        min_stop_pct = float(sig.get("min_stop_pct", 1.0)) / 100.0
        max_stop_pct = float(sig.get("max_stop_pct", 25.0)) / 100.0
        holding_vol = daily_vol * math.sqrt(max(holding_days, 1))
        distance_frac = max(min_stop_pct, min(stop_multiplier * holding_vol, max_stop_pct))
        vol_source = "garch"
    else:
        atr_mult = float(config.get("backtest", {}).get("atr_stop_multiplier", 2.0))
        distance_frac = (atr_mult * atr / price) if price else 0.0
        vol_source = "atr_fallback"

    stop_distance = distance_frac * price
    stop_price = price - stop_distance if direction == "LONG" else price + stop_distance
    return stop_price, round(distance_frac * 100, 4), vol_source


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trades(signals: pd.DataFrame, ticker: str, config: dict) -> list[dict]:
    """Walk forward through *signals* and generate simulated trades.

    Entry: gated per-date in two steps against config["backtest"]'s own
    entry-gating keys (entry_signal, entry_signal_overrides,
    allowed_entry_regimes) — kept separate from trading212_invest /
    trading212_stocks because this backtest, unlike those two long-only
    cash-account automation paths, can open both LONG and SHORT positions:
      1. analysis.regime.is_entry_allowed() — a regime not in
         backtest.allowed_entry_regimes blocks entry entirely for that
         date, before the signal is even considered (e.g. MIXED has no
         demonstrable edge, so no position is opened regardless of signal
         strength).
      2. analysis.regime.resolve_entry_signal() — for an allowed regime, the
         resolved entry_signal_overrides threshold ("STRONG_BUY" only enters
         on STRONG_BUY (LONG) / STRONG_SELL (SHORT); "BUY" also enters on
         plain BUY / SELL) gates the signal itself.
    Exit (in priority order, unaffected by either entry gate — an open
    position always continues to be monitored and closed regardless of what
    the regime does next):
      1. Stop loss — GARCH-based when signals.use_garch_stops is true (the
         same calibration the live system uses, via a monthly precomputed
         cache — see _build_monthly_vol_cache() / _resolve_entry_stop()
         above), falling back to the pre-existing
         entry ± backtest.atr_stop_multiplier × ATR formula when GARCH
         data isn't available for that month or the feature is disabled.
      2. Signal reversal, gated by trading212_invest.exit_signal — "SELL"
         (default) reverses on SELL/STRONG_SELL for LONG (BUY/STRONG_BUY for
         SHORT); "STRONG_SELL" requires the strong variant only. Mirrors the
         live exit_signal threshold used by maybe_place_t212_orders() so the
         backtest's exit behaviour matches production.
      3. Maximum holding period — per-ticker via
         analysis.regime.get_max_holding_days() (ETF/stock/bond/forex/crypto
         asset-class limits read from config).
    """
    from analysis.regime import get_max_holding_days, is_entry_allowed, resolve_entry_signal  # noqa: PLC0415  local import — mirrors compute_signal_history()'s pattern above
    from analysis.risk import _drawdown_tier_from_pct  # noqa: PLC0415  shared drawdown-tier math — see that module's docstring
    from analysis.volatility import _MIN_VAR_RETURNS, _var_position_size_core  # noqa: PLC0415  shared VaR math — see that module's docstring for why this is imported rather than reimplemented

    bt_cfg   = config.get("backtest", {})
    max_hold = get_max_holding_days(ticker, config)

    # VaR position sizing — reads risk parameters from trading212_invest (the
    # backtest has no config block of its own for these; entry-gating alone
    # is kept separate under backtest: because it can go LONG or SHORT, but
    # risk tolerance is not direction-specific, so trading212_invest's
    # values are reused directly here). starting_capital_gbp is a notional
    # account size, not a real compounding balance: simulate_trades() runs
    # one ticker at a time with no shared cross-ticker portfolio, so this
    # value is constant for every entry across the whole run rather than
    # growing/shrinking as a real account would — see CLAUDE.md's "VaR
    # position sizing" section for the full rationale.
    var_cfg           = config.get("trading212_invest", {})
    starting_capital  = float(bt_cfg.get("starting_capital_gbp", 10000))
    var_confidence    = float(var_cfg.get("var_confidence", 0.95))
    var_lookback      = int(var_cfg.get("var_lookback_days", 252))
    max_risk_pct      = float(var_cfg.get("max_risk_pct", 0.01))
    min_position_gbp  = float(var_cfg.get("min_position_gbp", 50))
    max_position_gbp  = float(var_cfg.get("max_position_gbp", 500))
    close_series      = signals["close"].astype(float)

    # Drawdown-based position scaling — mirrors the live mechanism (see
    # analysis/risk.py:compute_drawdown_scale_factor()), reusing
    # trading212_invest.drawdown_scaling for the same reason var_cfg above
    # reuses trading212_invest for VaR risk parameters: entry-gating is kept
    # separate under backtest: because it can go LONG or SHORT, but risk
    # tolerance is not direction-specific. Unlike the live path (one shared
    # T212 account), simulate_trades() runs one ticker at a time with no
    # cross-ticker portfolio (see the VaR position sizing note above) — so
    # "drawdown" here is tracked against a running per-ticker notional
    # equity curve (starting_capital + this ticker's own realised pnl_gbp so
    # far in this run), not a real shared account balance. This is the same
    # scoping limitation starting_capital_gbp already has; it lets the
    # backtest show whether the mechanism would have helped or hurt on a
    # per-ticker basis, not a true multi-asset portfolio effect.
    dd_cfg      = var_cfg.get("drawdown_scaling", {})
    dd_enabled  = bool(dd_cfg.get("enabled", False))
    dd_equity   = starting_capital
    dd_peak     = starting_capital
    dd_tier     = "NORMAL"

    _BULL   = frozenset({"BUY", "STRONG_BUY"})
    _BEAR   = frozenset({"SELL", "STRONG_SELL"})
    _STRONG = frozenset({"STRONG_BUY", "STRONG_SELL"})
    _ALL    = _BULL | _BEAR

    exit_threshold = config.get("trading212_invest", {}).get("exit_signal", "SELL")
    _EXIT_BEAR = _BEAR if exit_threshold == "SELL" else frozenset({"STRONG_SELL"})
    _EXIT_BULL = _BULL if exit_threshold == "SELL" else frozenset({"STRONG_BUY"})

    trades: list[dict] = []
    open_trade: Optional[dict] = None

    # Monthly CPD/GARCH vol cache — computed lazily, one calendar month at a
    # time, only for months an actual trade entry falls in. A ticker with
    # (say) 5 entries over a 7-year run needs 5 GARCH fits, not ~84 —
    # eagerly precomputing every month-end in the full requested range was
    # measured at ~73ms/fit x 84 months = over 6s for a SINGLE ticker, which
    # would blow the whole run's performance budget across 64 tickers.
    # _build_monthly_vol_cache() is still called with a genuine
    # (start_date, end_date) range each time — here just narrowed to the one
    # month actually needed — so its module-level cross-run cache and
    # look-ahead-free slicing behave identically either way; this is a
    # calling-pattern choice, not a change to that function's contract. A
    # ticker that never enters a trade never triggers any of this at all.
    use_garch_stops = bool(config.get("signals", {}).get("use_garch_stops", False))
    monthly_cache: dict[date, dict] = {}

    for dt, row in signals.iterrows():
        price  = float(row["close"])
        sig    = str(row["signal"])
        atr    = float(row["atr"]) if pd.notna(row["atr"]) else 0.0

        if open_trade is None:
            regime_at_row = str(row["regime"])
            if not is_entry_allowed(config, regime_at_row, "backtest"):
                continue
            entry_threshold = resolve_entry_signal(bt_cfg, regime_at_row)
            entry_signals   = _ALL if entry_threshold == "BUY" else _STRONG
            if sig not in entry_signals:
                continue
            direction = "LONG" if sig in _BULL else "SHORT"

            entry_date_only = dt.date() if hasattr(dt, "date") else dt
            month_end_key: Optional[date] = None
            if use_garch_stops:
                # MonthEnd(-1) always lands strictly before dt (even when dt
                # is itself a month-end) — the calibration state as of the
                # end of the last fully-completed month before this entry,
                # never data from dt's own (still in-progress) month.
                month_end_ts = pd.Timestamp(dt) + pd.offsets.MonthEnd(-1)
                month_end_key = month_end_ts.date()
                if month_end_key not in monthly_cache:
                    monthly_cache.update(
                        _build_monthly_vol_cache(ticker, month_end_key, month_end_key, config)
                    )

            stop, stop_distance_pct, vol_source = _resolve_entry_stop(
                direction, price, atr, max_hold, month_end_key, monthly_cache or {}, config,
            )

            # VaR position sizing — uses only prices strictly BEFORE dt (no
            # look-ahead). Falls back to max_position_gbp * 0.5 when fewer
            # than _MIN_VAR_RETURNS (60) prior daily returns are available
            # within the signals range passed to this call — the same
            # fallback threshold and value analysis/volatility.py:
            # compute_var_position_size() uses live, so an entry early in a
            # short requested backtest window degrades safely rather than
            # erroring or silently reusing future data.
            prior_closes = close_series.loc[close_series.index < dt].tail(var_lookback + 1)
            log_returns  = np.log(prior_closes / prior_closes.shift(1)).dropna()
            if len(log_returns) >= _MIN_VAR_RETURNS:
                position_gbp, _daily_var, _holding_var = _var_position_size_core(
                    log_returns, starting_capital, max_position_gbp, max_hold,
                    var_confidence, max_risk_pct, min_position_gbp,
                )
            else:
                position_gbp = round(max_position_gbp * 0.5, 2)

            dd_pct_at_entry = max(0.0, (dd_peak - dd_equity) / dd_peak) if dd_peak > 0 else 0.0
            if dd_enabled:
                dd_tier, dd_scale = _drawdown_tier_from_pct(dd_pct_at_entry, dd_cfg, dd_tier)
                if dd_scale < 1.0:
                    position_gbp = max(position_gbp * dd_scale, min_position_gbp)
            else:
                dd_scale = 1.0

            open_trade = {
                "ticker":                   ticker,
                "direction":                direction,
                "entry_date":               dt,
                "entry_price":              price,
                "entry_signal":             sig,
                "stop_price":               stop,
                "stop_distance_pct":        stop_distance_pct,
                "vol_source":               vol_source,
                "position_gbp":             position_gbp,
                "drawdown_scale_factor":    dd_scale,
                "drawdown_pct_at_entry":    dd_pct_at_entry,
                "regime_at_entry":          str(row["regime"]),
                "trend_at_entry":           int(row["trend_score"]),
                "mean_reversion_at_entry":  int(row["mean_reversion_score"]),
                "volatility_at_entry":      int(row["volatility_score"]),
                "volume_at_entry":          int(row["volume_score"]),
                "macro_at_entry":           int(row["macro_score"]),
            }
            continue

        direction    = open_trade["direction"]
        entry_price  = open_trade["entry_price"]
        stop_price   = open_trade["stop_price"]
        holding_days = (dt - open_trade["entry_date"]).days

        exit_reason: Optional[str] = None
        exit_price = price

        # 1. Stop loss
        if direction == "LONG"  and price <= stop_price:
            exit_reason = "STOP_LOSS"
            exit_price  = stop_price  # assume filled at stop
        elif direction == "SHORT" and price >= stop_price:
            exit_reason = "STOP_LOSS"
            exit_price  = stop_price
        # 2. Signal reversal (gated by trading212_invest.exit_signal)
        elif direction == "LONG"  and sig in _EXIT_BEAR:
            exit_reason = "SIGNAL_REVERSAL"
        elif direction == "SHORT" and sig in _EXIT_BULL:
            exit_reason = "SIGNAL_REVERSAL"
        # 3. Max holding period
        elif holding_days >= max_hold:
            exit_reason = "MAX_HOLDING"

        if exit_reason:
            pnl_pct = (exit_price - entry_price) / entry_price
            if direction == "SHORT":
                pnl_pct = -pnl_pct

            trades.append({
                "ticker":                   ticker,
                "direction":                direction,
                "entry_date":               open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
                "exit_date":                dt.date() if hasattr(dt, "date") else dt,
                "entry_price":              entry_price,
                "exit_price":               float(exit_price),
                "entry_signal":             open_trade["entry_signal"],
                "exit_reason":              exit_reason,
                "pnl_pct":                  float(pnl_pct),
                "position_gbp":             open_trade["position_gbp"],
                "pnl_gbp":                  open_trade["position_gbp"] * float(pnl_pct),
                "stop_distance_pct":        open_trade["stop_distance_pct"],
                "vol_source":               open_trade["vol_source"],
                "drawdown_scale_factor":    open_trade["drawdown_scale_factor"],
                "drawdown_pct_at_entry":    open_trade["drawdown_pct_at_entry"],
                "regime_at_entry":          open_trade["regime_at_entry"],
                "trend_at_entry":           open_trade["trend_at_entry"],
                "mean_reversion_at_entry":  open_trade["mean_reversion_at_entry"],
                "volatility_at_entry":      open_trade["volatility_at_entry"],
                "volume_at_entry":          open_trade["volume_at_entry"],
                "macro_at_entry":           open_trade["macro_at_entry"],
                "holding_days":             holding_days,
                "is_win":                   pnl_pct > 0,
            })
            dd_equity += open_trade["position_gbp"] * float(pnl_pct)
            dd_peak    = max(dd_peak, dd_equity)
            open_trade = None

    # Close any still-open position at end of data
    if open_trade is not None:
        last      = signals.iloc[-1]
        exit_p    = float(last["close"])
        pnl_pct   = (exit_p - open_trade["entry_price"]) / open_trade["entry_price"]
        if open_trade["direction"] == "SHORT":
            pnl_pct = -pnl_pct
        hd = (signals.index[-1] - open_trade["entry_date"]).days
        trades.append({
            "ticker":                   ticker,
            "direction":                open_trade["direction"],
            "entry_date":               open_trade["entry_date"].date() if hasattr(open_trade["entry_date"], "date") else open_trade["entry_date"],
            "exit_date":                signals.index[-1].date(),
            "entry_price":              open_trade["entry_price"],
            "exit_price":               exit_p,
            "entry_signal":             open_trade["entry_signal"],
            "exit_reason":              "END_OF_DATA",
            "pnl_pct":                  float(pnl_pct),
            "position_gbp":             open_trade["position_gbp"],
            "pnl_gbp":                  open_trade["position_gbp"] * float(pnl_pct),
            "stop_distance_pct":        open_trade["stop_distance_pct"],
            "vol_source":               open_trade["vol_source"],
            "drawdown_scale_factor":    open_trade["drawdown_scale_factor"],
            "drawdown_pct_at_entry":    open_trade["drawdown_pct_at_entry"],
            "regime_at_entry":          open_trade["regime_at_entry"],
            "trend_at_entry":           open_trade["trend_at_entry"],
            "mean_reversion_at_entry":  open_trade["mean_reversion_at_entry"],
            "volatility_at_entry":      open_trade["volatility_at_entry"],
            "volume_at_entry":          open_trade["volume_at_entry"],
            "macro_at_entry":           open_trade["macro_at_entry"],
            "holding_days":             hd,
            "is_win":                   pnl_pct > 0,
        })

    return trades


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _create_run(engine, name: str, start: datetime, end: datetime,
                tickers: list[str], config: dict) -> int:
    bt_cfg = config.get("backtest", {})
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO backtest_runs "
                "(name, created_at, start_date, end_date, tickers_json, params_json, status) "
                "VALUES (:name, :ts, :sd, :ed, :tj, :pj, 'pending')"
            ),
            {
                "name": name or f"Backtest {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
                "ts":   datetime.utcnow(),
                "sd":   start.date() if isinstance(start, datetime) else start,
                "ed":   end.date()   if isinstance(end,   datetime) else end,
                "tj":   json.dumps(tickers),
                "pj":   json.dumps(bt_cfg),
            },
        )
        return result.lastrowid


def _update_run_status(engine, run_id: int, status: str, error: str = "") -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE backtest_runs SET status = :s, error_message = :e WHERE id = :id"),
            {"s": status, "e": error, "id": run_id},
        )


_SIGNALS_UPSERT_SQL = text("""
    INSERT INTO backtest_signals
        (run_id, ticker, date, regime, confluence_score, signal, trend_score,
         mean_reversion_score, volatility_score, volume_score, macro_score, close_price, atr,
         hmm_bull_probability, hmm_downgraded, hmm_upgraded)
    VALUES
        (:run_id, :ticker, :date, :regime, :confluence_score, :signal, :trend_score,
         :mean_reversion_score, :volatility_score, :volume_score, :macro_score, :close_price, :atr,
         :hmm_bull_probability, :hmm_downgraded, :hmm_upgraded)
    ON CONFLICT(run_id, ticker, date) DO UPDATE SET
        confluence_score = excluded.confluence_score,
        signal            = excluded.signal
""")


def _store_signals_batch(engine, run_id: int, ticker: str, signals: pd.DataFrame) -> None:
    """Upsert one backtest_signals row per date in *signals*.

    Uses raw SQL with a list-of-dicts parameter set (conn.execute(text(...),
    rows)) rather than sqlite_insert(BacktestSignal).values(chunk) — the
    original ORM-expression-builder approach measured at ~0.6s/ticker (~38s
    across a 64-ticker run) purely from SQLAlchemy's Core/ORM query-building
    overhead for a multi-row VALUES + ON CONFLICT construct, unrelated to
    the actual row count or SQLite itself (a Core-Table-object variant of
    the same `.values([...])` pattern measured identically slow). Passing a
    list of dicts to conn.execute() here instead dispatches through the
    DBAPI's executemany() with a single prepared statement reused across
    all rows — measured ~12x faster for the same data (0.43s vs 5.2s for 12
    tickers' worth of rows in isolation). Discovered while adding GARCH/CPD
    support to the backtest (see CLAUDE.md's "Backtesting engine
    performance" note) — this fix is unrelated to that feature but was
    necessary to keep a full 64-ticker run within its performance budget.
    """
    rows = []
    for dt, row in signals.iterrows():
        d = dt.date() if hasattr(dt, "date") else dt
        rows.append({
            "run_id":               run_id,
            "ticker":               ticker,
            "date":                 d,
            "regime":               str(row["regime"]),
            "confluence_score":     float(row["confluence_score"]),
            "signal":               str(row["signal"]),
            "trend_score":          int(row["trend_score"]),
            "mean_reversion_score": int(row["mean_reversion_score"]),
            "volatility_score":     int(row["volatility_score"]),
            "volume_score":         int(row["volume_score"]),
            "macro_score":          int(row["macro_score"]),
            "close_price":          float(row["close"]),
            "atr":                  float(row["atr"]) if pd.notna(row["atr"]) else None,
            "hmm_bull_probability": float(row["hmm_bull_probability"]) if "hmm_bull_probability" in row and pd.notna(row["hmm_bull_probability"]) else None,
            "hmm_downgraded":       int(bool(row["hmm_downgraded"])) if "hmm_downgraded" in row else 0,
            "hmm_upgraded":         int(bool(row["hmm_upgraded"])) if "hmm_upgraded" in row else 0,
        })
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(_SIGNALS_UPSERT_SQL, rows)


def _store_trades_batch(engine, run_id: int, trades: list[dict]) -> None:
    if not trades:
        return
    rows = [{"run_id": run_id, **t} for t in trades]
    _BATCH = 200
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH):
            conn.execute(sqlite_insert(BacktestTrade).values(rows[i : i + _BATCH]))


def _update_run_complete(engine, run_id: int, metrics: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE backtest_runs SET "
                "status='complete', total_trades=:tt, win_rate=:wr, "
                "avg_return_pct=:ar, sharpe_ratio=:sr, max_drawdown_pct=:md, "
                "total_return_pct=:tr, avg_holding_days=:ah "
                "WHERE id=:id"
            ),
            {
                "tt": metrics.get("total_trades", 0),
                "wr": metrics.get("win_rate"),
                "ar": metrics.get("avg_return_pct"),
                "sr": metrics.get("sharpe_ratio"),
                "md": metrics.get("max_drawdown_pct"),
                "tr": metrics.get("total_return_pct"),
                "ah": metrics.get("avg_holding_days"),
                "id": run_id,
            },
        )


# ---------------------------------------------------------------------------
# Quick metrics for run summary (full metrics in backtest_metrics.py)
# ---------------------------------------------------------------------------

def _quick_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"total_trades": 0}
    df = pd.DataFrame(trades)
    n  = len(df)
    wr = df["is_win"].mean()
    ar = df["pnl_pct"].mean() * 100
    tr = df["pnl_pct"].sum() * 100  # simple sum (not compounded)
    ah = df["holding_days"].mean()

    # Sharpe: annualised (assume each trade = independent daily observation)
    if n > 1 and df["pnl_pct"].std() > 0:
        sr = (df["pnl_pct"].mean() / df["pnl_pct"].std()) * np.sqrt(252 / ah if ah > 0 else 1)
    else:
        sr = 0.0

    # Max drawdown on cumulative equity curve
    cum = (1 + df["pnl_pct"]).cumprod()
    peak = cum.cummax()
    dd   = ((cum - peak) / peak).min() * 100

    return {
        "total_trades":    n,
        "win_rate":        round(float(wr), 4),
        "avg_return_pct":  round(float(ar), 4),
        "total_return_pct": round(float(tr), 4),
        "sharpe_ratio":    round(float(sr), 4),
        "max_drawdown_pct": round(float(dd), 4),
        "avg_holding_days": round(float(ah), 1),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backtest(
    tickers: list[str],
    start_date: datetime,
    end_date: datetime,
    config: Optional[dict] = None,
    name: str = "",
) -> int:
    """Run the full vectorized backtest and persist results.

    Returns:
        run_id — integer primary key of the BacktestRun row.
    """
    if config is None:
        config = _load_config()

    engine = get_engine()
    run_id = _create_run(engine, name, start_date, end_date, tickers, config)

    try:
        _update_run_status(engine, run_id, "running")
        logger.info("Backtest run %d starting: %d tickers, %s → %s",
                    run_id, len(tickers), start_date, end_date)

        # --- Shared series (loaded once for all tickers) ---------------------
        vix_df = get_price_df("^VIX", days=4000)
        if not vix_df.empty:
            vix_df = vix_df.set_index("date")
            vix_df.index = pd.to_datetime(vix_df.index)
            vix_s = vix_df["close"].astype(float)
        else:
            vix_s = None

        gspc_df = get_price_df("^GSPC", days=4000)
        if not gspc_df.empty:
            gspc_df = gspc_df.set_index("date")
            gspc_df.index = pd.to_datetime(gspc_df.index)
            reg_hist = _regime_history(gspc_df, vix_s, config)
        else:
            reg_hist = pd.DataFrame({
                "regime": pd.Series(dtype=str),
                "hmm_bull_probability": pd.Series(dtype=float),
                "hmm_downgraded": pd.Series(dtype=bool),
                "hmm_upgraded": pd.Series(dtype=bool),
            })

        macro_df = _load_macro_history()

        # Date bounds as Timestamps for filtering
        ts_start = pd.Timestamp(start_date)
        ts_end   = pd.Timestamp(end_date)

        # --- Per-ticker signal computation -----------------------------------
        all_trades: list[dict] = []
        for ticker in tickers:
            try:
                df = get_price_df(ticker, days=4000)
                if df.empty or len(df) < 250:
                    logger.debug("Skipping %s — insufficient price data (%d rows).", ticker, len(df))
                    continue

                df = df.set_index("date")
                df.index = pd.to_datetime(df.index)

                signals = compute_signal_history(ticker, df, vix_s, reg_hist, macro_df, config)
                if signals.empty:
                    continue

                # Store full signal history for attribution / decay analysis
                _store_signals_batch(engine, run_id, ticker, signals)

                # Filter to requested date range for trade simulation
                in_range = signals[(signals.index >= ts_start) & (signals.index <= ts_end)]
                if in_range.empty:
                    continue

                ticker_trades = simulate_trades(in_range, ticker, config)
                all_trades.extend(ticker_trades)
                logger.debug("%s: %d trades simulated.", ticker, len(ticker_trades))

            except Exception as exc:
                logger.warning("Backtest: error processing %s: %s", ticker, exc)
                continue

        # --- Store trades and finalize ---------------------------------------
        _store_trades_batch(engine, run_id, all_trades)
        metrics = _quick_metrics(all_trades)
        _update_run_complete(engine, run_id, metrics)

        logger.info(
            "Backtest run %d complete — %d trades, win_rate=%.1f%%, return=%.1f%%",
            run_id, metrics.get("total_trades", 0),
            (metrics.get("win_rate") or 0) * 100,
            metrics.get("total_return_pct") or 0,
        )
        return run_id

    except Exception as exc:
        logger.error("Backtest run %d failed: %s", run_id, exc, exc_info=True)
        _update_run_status(engine, run_id, "error", error=str(exc))
        raise


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_all_runs() -> list[dict]:
    """Return all backtest runs ordered newest first."""
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM backtest_runs ORDER BY created_at DESC")
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_run(run_id: int) -> Optional[dict]:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM backtest_runs WHERE id = :id"), {"id": run_id}
            ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def get_run_trades(run_id: int) -> pd.DataFrame:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM backtest_trades WHERE run_id = :id ORDER BY entry_date"),
                {"id": run_id},
            ).mappings().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        df["exit_date"]  = pd.to_datetime(df["exit_date"])
        return df
    except Exception:
        return pd.DataFrame()


def get_run_signals(run_id: int, ticker: Optional[str] = None) -> pd.DataFrame:
    engine = get_engine()
    sql = "SELECT * FROM backtest_signals WHERE run_id = :id"
    params: dict = {"id": run_id}
    if ticker:
        sql += " AND ticker = :t"
        params["t"] = ticker
    sql += " ORDER BY ticker, date"
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame()


def delete_run(run_id: int) -> None:
    """Delete a backtest run and all its associated signals and trades.

    Runs VACUUM after the delete transaction commits to reclaim the freed
    page space on disk — backtest_signals in particular can accumulate a
    large number of rows per run (one per ticker per date), so repeated
    deletes without a VACUUM leave the .db file bloated with empty pages.
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM backtest_signals WHERE run_id = :id"), {"id": run_id})
        conn.execute(text("DELETE FROM backtest_trades  WHERE run_id = :id"), {"id": run_id})
        conn.execute(text("DELETE FROM backtest_runs    WHERE id     = :id"), {"id": run_id})

    # VACUUM cannot run inside a transaction — must be on a fresh connection
    # after the DELETE transaction above has already committed.
    with get_engine().connect() as conn:
        conn.execute(text("VACUUM"))
        conn.commit()
    logger.info("VACUUM complete after deleting backtest run %s", run_id)
