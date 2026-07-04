"""Hidden Markov Model regime confidence layer — validates, never replaces,
the rule-based regime detection in analysis/regime.py.

The rule-based system (ADX/VIX/SMA200 hard thresholds) is primary and always
runs first. This module fits a 3-state Gaussian HMM on ^GSPC's own daily
indicator history and reports how confident that model is that today sits in
a genuine bull state. analysis/regime.py:detect_regime() uses that confidence
purely as a veto on TRENDING_BULL calls — a rule-based TRENDING_BULL call with
low HMM bull-state confidence is downgraded to MIXED; every other regime call
is left untouched (HMM is informational only there). This exists because hard
thresholds (ADX 25, VIX 20/30) create sharp regime boundaries — ADX 24.9 is
MIXED, ADX 25.1 is TRENDING_BULL — while HMM state probabilities are
continuous and smooth that boundary out.

States are NOT labelled at fit time (hmmlearn has no notion of "bull"/"bear").
After fitting, _label_states() inspects the learned per-state feature means
and assigns BULL / BEAR / UNCERTAIN by inspection — see its docstring.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from data.database import get_metadata_str, set_metadata

logger = logging.getLogger(__name__)

_REFERENCE_TICKER = "^GSPC"

# Feature order is fixed — the same order is used for fitting, standardising,
# and every predict_proba() call, and is persisted alongside the model so a
# reloaded model always applies features in the order it was trained on.
FEATURE_NAMES: list[str] = ["close_sma200_ratio", "adx", "atr_pct", "rsi", "macd"]

_MIN_GARCH_LIKE_OBSERVATIONS_DEFAULT = 504  # ~2 trading years, matches hmm_min_training_days default

# In-process cache so repeated get_hmm_regime_confidence() calls within the
# same run (main.py alone calls detect_regime() several times per page in
# some flows) don't re-parse the persisted JSON and rebuild a GaussianHMM
# object every time — mirrors the (ticker, date, method) in-process cache
# analysis/volatility.py's change-point detection already uses for the same
# reason. Keyed on the hmm_last_fit timestamp string: any refit invalidates it.
_CACHED_MODEL: Optional["GaussianHMM"] = None  # noqa: F821
_CACHED_META: Optional[dict] = None
_CACHED_FIT_KEY: Optional[str] = None


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data loading & feature construction
# ---------------------------------------------------------------------------
#
# Features are computed vectorized directly from price_data's OHLC history
# (via get_price_df()), NOT read from the stored `indicators` table. The
# `indicators` table only ever holds one row per date on which run_analysis()
# actually executed for that ticker — in production that accumulates into a
# real history over time as the scheduler runs daily, but there is no
# guarantee it yet spans hmm_min_training_days (~2 years), and on a fresh
# install or a local dev DB it may hold only a handful of rows. price_data
# holds up to 10 years of OHLCV per asset from the very first pull (see
# CLAUDE.md's "Running the app"), so computing features from it directly —
# mirroring analysis/backtest.py:_regime_history()'s own vectorized,
# look-ahead-free approach — is what makes both a fresh install and this
# module's monthly backtest cache (analysis/backtest.py) workable at all.
#
# The four rolling/ewm indicator formulas below (_rsi/_macd/_adx/_atr) are
# deliberately duplicated from analysis/backtest.py rather than imported from
# it: analysis/backtest.py's monthly HMM cache needs to call back into this
# module's fit/label/standardise helpers, so importing backtest.py's helpers
# here would create a circular import. Small, pure vectorized formula
# duplication across analysis/backtest.py and analysis/volatility.py already
# exists in this codebase for the same reason (see e.g. both modules' own ATR
# implementations) — this follows that precedent.

def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    ema_fast  = close.ewm(span=fast, adjust=False).mean()
    ema_slow  = close.ewm(span=slow, adjust=False).mean()
    return ema_fast - ema_slow


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    alpha     = 1.0 / period
    smooth_tr = pd.Series(tr,       index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_p  = pd.Series(plus_dm,  index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    smooth_m  = pd.Series(minus_dm, index=close.index).ewm(alpha=alpha, adjust=False).mean() * period
    pdi = 100 * smooth_p / smooth_tr.replace(0, np.nan)
    mdi = 100 * smooth_m / smooth_tr.replace(0, np.nan)
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _load_gspc_price_history(days: int = 4000) -> pd.DataFrame:
    """Return ^GSPC's OHLC price history, date-indexed ascending."""
    from data.ingestion import get_price_df  # noqa: PLC0415  local import — keeps this module's import graph acyclic (see analysis/backtest.py's identical pattern)

    df = get_price_df(_REFERENCE_TICKER, days=days)
    if df.empty:
        return df
    df = df.set_index("date") if "date" in df.columns else df
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def _build_features(price_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Build the fixed 5-column feature matrix from OHLC price history.

    Entirely vectorized (rolling/ewm), so inherently look-ahead free — each
    row only ever depends on data up to and including that row, which is
    what lets analysis/backtest.py reuse this same function on a historical
    slice ending strictly before some past date without introducing any
    look-ahead bias.

    close_sma200_ratio = close / sma_200 (trend direction, continuous rather
    than the rule-based system's binary price>sma_200 check). The remaining
    four columns (adx, atr_pct, rsi, macd) mirror the live analysis modules'
    own definitions, read from config's existing signals.* periods rather
    than introducing HMM-specific ones.
    """
    if price_df.empty or len(price_df) < 210:
        return pd.DataFrame(columns=FEATURE_NAMES)

    sig = config.get("signals", {})
    close = price_df["close"].astype(float)
    high  = price_df["high"].astype(float)
    low   = price_df["low"].astype(float)

    sma_200 = close.rolling(int(sig.get("long_window", 200))).mean()
    adx_s   = _adx(high, low, close, int(sig.get("adx_period", 14)))
    atr_s   = _atr(high, low, close, int(sig.get("atr_period", 14)))
    rsi_s   = _rsi(close, int(sig.get("rsi_period", 14)))
    macd_s  = _macd(close, int(sig.get("macd_fast", 12)), int(sig.get("macd_slow", 26)), int(sig.get("macd_signal", 9)))

    out = pd.DataFrame(index=price_df.index)
    out["close_sma200_ratio"] = close / sma_200.replace(0, np.nan)
    out["adx"]     = adx_s
    out["atr_pct"] = atr_s / close.replace(0, np.nan)
    out["rsi"]     = rsi_s
    out["macd"]    = macd_s
    return out[FEATURE_NAMES].dropna()


def _standardize(features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zero-mean, unit-variance scale each column. HMM fitting is sensitive to
    feature scale (adx ~0-100, atr_pct ~0-0.1, close_sma200_ratio ~0.7-1.3) —
    without this the largest-magnitude feature would dominate the covariance
    structure regardless of its actual signal content.

    Returns (scaled_array, mean, std). std entries of 0 (a constant column,
    only possible on pathological/synthetic data) are replaced with 1 to
    avoid a division by zero — the column is then a genuine constant zero
    in the scaled output rather than NaN.
    """
    mean = features.mean(axis=0).to_numpy()
    std  = features.std(axis=0).to_numpy()
    std_safe = np.where(std == 0, 1.0, std)
    scaled = (features.to_numpy() - mean) / std_safe
    return scaled, mean, std_safe


# ---------------------------------------------------------------------------
# Pure HMM fit/label core — no DB access, shared by the live path here and
# analysis/backtest.py's monthly precompute cache (same shared-core pattern
# as analysis/volatility.py:_garch_daily_vol_core() / _icss_change_point()).
# ---------------------------------------------------------------------------

def _fit_hmm_core(X: np.ndarray, n_components: int = 3, n_iter: int = 100,
                   covariance_type: str = "full", random_state: int = 42):
    """Fit a GaussianHMM on already-standardised features. Returns the fitted
    model, or None if fitting raised (e.g. singular covariance on
    degenerate/too-short input). Never raises.
    """
    try:
        from hmmlearn.hmm import GaussianHMM  # noqa: PLC0415  optional heavy import, only needed for HMM regime confidence
        model = GaussianHMM(
            n_components=n_components,
            covariance_type=covariance_type,
            n_iter=n_iter,
            random_state=random_state,
        )
        model.fit(X)
    except Exception as exc:
        logger.warning("HMM: fit failed: %s", exc)
        return None

    if not getattr(model.monitor_, "converged", True):
        logger.warning(
            "HMM: EM did not converge within n_iter=%d — using the best fit found anyway.",
            n_iter,
        )
    return model


def _label_states(means: np.ndarray, feature_names: list[str]) -> dict[int, str]:
    """Label each of the 3 learned (unlabelled) states as BULL/BEAR/UNCERTAIN
    by inspecting the state means (in standardised units — comparable across
    states without further scaling since all states share one fit's scaler).

    - BULL: highest combined (close_sma200_ratio + adx) score — a state with
      both a high price-relative-to-trend ratio AND high trend strength,
      matching the task's "highest ratio and highest ADX" rule. Using the
      sum rather than requiring both to independently be the per-state max
      avoids leaving BULL undefined on the (rare, but possible with only 3
      states) case where the highest-ratio state isn't also the highest-ADX
      state.
    - BEAR: lowest close_sma200_ratio among the two states not already
      labelled BULL.
    - UNCERTAIN: whichever state is left.
    """
    ratio_idx = feature_names.index("close_sma200_ratio")
    adx_idx   = feature_names.index("adx")

    combined = means[:, ratio_idx] + means[:, adx_idx]
    bull_state = int(np.argmax(combined))

    remaining = [s for s in range(means.shape[0]) if s != bull_state]
    bear_state = min(remaining, key=lambda s: means[s, ratio_idx])
    uncertain_state = next(s for s in remaining if s != bear_state)

    return {bull_state: "BULL", bear_state: "BEAR", uncertain_state: "UNCERTAIN"}


# ---------------------------------------------------------------------------
# Model persistence — system_metadata JSON cache
# ---------------------------------------------------------------------------

def _params_to_json(model, feature_mean: np.ndarray, feature_std: np.ndarray,
                     state_labels: dict[int, str], n_training_days: int) -> str:
    return json.dumps({
        "means":        model.means_.tolist(),
        "covars":       np.asarray(model.covars_).tolist(),
        "transmat":     model.transmat_.tolist(),
        "startprob":    model.startprob_.tolist(),
        "feature_mean": feature_mean.tolist(),
        "feature_std":  feature_std.tolist(),
        "feature_names": FEATURE_NAMES,
        "state_labels": {str(k): v for k, v in state_labels.items()},
        "n_training_days": n_training_days,
        "n_components": model.means_.shape[0],
    })


def _model_from_json(params_json: str):
    """Reconstruct a GaussianHMM from persisted params without calling fit()
    — setting startprob_/transmat_/means_/covars_/n_features directly is
    sufficient for predict_proba() (verified: reconstructed predict_proba
    output is bit-identical to the original fitted model's).

    Returns (model, meta) where meta has feature_mean, feature_std,
    feature_names, state_labels (dict[int, str]), n_training_days.
    """
    from hmmlearn.hmm import GaussianHMM  # noqa: PLC0415

    params = json.loads(params_json)
    n_components = params["n_components"]
    model = GaussianHMM(n_components=n_components, covariance_type="full")
    model.startprob_ = np.asarray(params["startprob"])
    model.transmat_  = np.asarray(params["transmat"])
    model.means_     = np.asarray(params["means"])
    model.covars_    = np.asarray(params["covars"])
    model.n_features = len(params["feature_names"])

    meta = {
        "feature_mean":    np.asarray(params["feature_mean"]),
        "feature_std":     np.asarray(params["feature_std"]),
        "feature_names":   params["feature_names"],
        "state_labels":    {int(k): v for k, v in params["state_labels"].items()},
        "n_training_days": params["n_training_days"],
    }
    return model, meta


def fit_hmm(config: Optional[dict] = None):
    """Fit a 3-state Gaussian HMM on ^GSPC daily indicators and persist it.

    States correspond loosely to bull/bear/uncertain but are learned, not
    labelled — labelling happens post-fit via _label_states() inspecting
    model.means_.

    Persists the fitted parameters to system_metadata under
    'hmm_model_params' (JSON) and stamps 'hmm_last_fit' with the current UTC
    time. Returns the fitted GaussianHMM, or None if there isn't enough
    stored ^GSPC indicator history yet (fewer than signals.hmm_min_training_
    days rows) or the fit itself failed — logged as a warning either way,
    never raised (callers — the weekly scheduler job, the Status page's
    manual refit button, and get_hmm_regime_confidence()'s own fallback path
    — all treat "no model yet" as a normal, expected state on a fresh install).
    """
    if config is None:
        config = _load_config()
    sig = config.get("signals", {})
    min_days = int(sig.get("hmm_min_training_days", _MIN_GARCH_LIKE_OBSERVATIONS_DEFAULT))

    price_df = _load_gspc_price_history()  # all available history — see module docstring
    features = _build_features(price_df, config)

    if len(features) < min_days:
        logger.warning(
            "HMM: only %d usable ^GSPC feature rows available, need >= %d — skipping fit.",
            len(features), min_days,
        )
        return None

    X, feature_mean, feature_std = _standardize(features)
    model = _fit_hmm_core(X)
    if model is None:
        return None

    state_labels = _label_states(model.means_, FEATURE_NAMES)
    n_training_days = len(features)

    try:
        params_json = _params_to_json(model, feature_mean, feature_std, state_labels, n_training_days)
        set_metadata("hmm_model_params", params_json)
        set_metadata("hmm_last_fit", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("HMM: fit succeeded but persisting to system_metadata failed: %s", exc)

    # Warm the in-process cache so the immediately-following
    # get_hmm_regime_confidence() call (weekly job, manual refit button) does
    # not have to re-parse the JSON we just wrote.
    global _CACHED_MODEL, _CACHED_META, _CACHED_FIT_KEY
    _CACHED_MODEL = model
    _CACHED_META = {
        "feature_mean": feature_mean, "feature_std": feature_std,
        "feature_names": FEATURE_NAMES, "state_labels": state_labels,
        "n_training_days": n_training_days,
    }
    _CACHED_FIT_KEY = get_metadata_str("hmm_last_fit")

    logger.info(
        "HMM: fit complete on %d days — states labelled %s.",
        n_training_days, state_labels,
    )
    return model


def _load_or_refit_hmm(config: dict):
    """Return (model, meta), refitting only if the cached model is missing or
    older than signals.hmm_refit_interval_days. Never raises — returns
    (None, {}) if no model exists yet and fitting fails or there isn't enough
    data (see fit_hmm()'s docstring).
    """
    global _CACHED_MODEL, _CACHED_META, _CACHED_FIT_KEY

    sig = config.get("signals", {})
    refit_interval_days = int(sig.get("hmm_refit_interval_days", 7))

    last_fit_str = get_metadata_str("hmm_last_fit")
    stale_or_missing = True
    if last_fit_str:
        try:
            last_fit = pd.Timestamp(last_fit_str).tz_localize(None)
            age_days = (datetime.utcnow() - last_fit.to_pydatetime()).total_seconds() / 86400
            stale_or_missing = age_days > refit_interval_days
        except Exception:
            stale_or_missing = True

    if stale_or_missing:
        model = fit_hmm(config)
        if model is None:
            return None, {}
        return model, _CACHED_META

    if _CACHED_MODEL is not None and _CACHED_FIT_KEY == last_fit_str:
        return _CACHED_MODEL, _CACHED_META

    params_json = get_metadata_str("hmm_model_params")
    if not params_json:
        # hmm_last_fit exists without params — inconsistent state, refit.
        model = fit_hmm(config)
        if model is None:
            return None, {}
        return model, _CACHED_META

    try:
        model, meta = _model_from_json(params_json)
    except Exception as exc:
        logger.warning("HMM: failed to reconstruct cached model, refitting: %s", exc)
        model = fit_hmm(config)
        if model is None:
            return None, {}
        return model, _CACHED_META

    _CACHED_MODEL, _CACHED_META, _CACHED_FIT_KEY = model, meta, last_fit_str
    return model, meta


# ---------------------------------------------------------------------------
# Public confidence API
# ---------------------------------------------------------------------------

_UNAVAILABLE_RESULT: dict = {
    "hmm_bull_probability":      1 / 3,
    "hmm_bear_probability":      1 / 3,
    "hmm_uncertain_probability": 1 / 3,
    "hmm_dominant_state":        "UNCERTAIN",
    "hmm_confidence":            1 / 3,
    "hmm_agrees_with_rules":     False,
    "hmm_state_means":           [],
    "model_trained_on_days":     0,
    "hmm_model_available":       False,
}


def get_hmm_regime_confidence(config: Optional[dict] = None) -> dict:
    """Return today's HMM state-probability confidence for ^GSPC.

    Loads (or weekly-refits — see _load_or_refit_hmm()) the persisted model,
    then computes state probabilities for the full available ^GSPC feature
    history via predict_proba(), taking the last row as "today". Using the
    full history (not just a recent window) lets the forward-backward
    algorithm use the complete observed state sequence, which is what
    predict_proba() is designed for; this whole call sequence is cheap for a
    single ~10-year daily series (a few thousand rows) evaluated against a
    fixed, already-fitted model — no refitting happens here.

    Returns a dict (see module docstring / CLAUDE.md for the full field
    list); when no model can be loaded or fit yet (fresh install, insufficient
    history), returns a uniform 1/3-1/3-1/3 fallback with
    'hmm_model_available': False so callers (analysis/regime.py's confidence
    gate) can tell "no information yet" apart from "genuinely low confidence"
    — the gate must never veto a rule-based call just because the model
    hasn't been trained yet.
    """
    if config is None:
        config = _load_config()

    model, meta = _load_or_refit_hmm(config)
    if model is None:
        return dict(_UNAVAILABLE_RESULT)

    price_df = _load_gspc_price_history()
    features = _build_features(price_df, config)
    if features.empty:
        return dict(_UNAVAILABLE_RESULT)

    feature_mean = meta["feature_mean"]
    feature_std  = meta["feature_std"]
    state_labels = meta["state_labels"]

    X = (features.to_numpy() - feature_mean) / feature_std
    try:
        proba = model.predict_proba(X)
    except Exception as exc:
        logger.warning("HMM: predict_proba failed: %s", exc)
        return dict(_UNAVAILABLE_RESULT)

    latest = proba[-1]
    label_to_proba: dict[str, float] = {"BULL": 0.0, "BEAR": 0.0, "UNCERTAIN": 0.0}
    for state_idx, label in state_labels.items():
        if 0 <= state_idx < len(latest):
            label_to_proba[label] = float(latest[state_idx])

    dominant_state = max(label_to_proba, key=label_to_proba.get)
    confidence = label_to_proba[dominant_state]

    latest_row = features.iloc[-1]
    price_above_sma200 = bool(latest_row["close_sma200_ratio"] > 1.0)
    agrees = bool(
        (dominant_state == "BULL" and price_above_sma200)
        or (dominant_state == "BEAR" and not price_above_sma200)
    )

    return {
        "hmm_bull_probability":      label_to_proba["BULL"],
        "hmm_bear_probability":      label_to_proba["BEAR"],
        "hmm_uncertain_probability": label_to_proba["UNCERTAIN"],
        "hmm_dominant_state":        dominant_state,
        "hmm_confidence":            confidence,
        "hmm_agrees_with_rules":     agrees,
        "hmm_state_means":           model.means_.tolist(),
        "model_trained_on_days":     meta.get("n_training_days", 0),
        "hmm_model_available":       True,
    }
