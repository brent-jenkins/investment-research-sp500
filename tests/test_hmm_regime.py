"""Tests for analysis/hmm_regime.py and its confidence-gate integration into
analysis/regime.py:detect_regime() / analysis/backtest.py's monthly cache.

The HMM validates the rule-based regime call, it never replaces it — these
tests cover: state labelling, the confidence-gate veto (TRENDING_BULL only),
model persistence/refit-interval behaviour, and no-look-ahead in the
backtest's monthly precompute cache. Most tests seed a large synthetic OHLC
series via the db_engine fixture (in-memory SQLite — tests/conftest.py)
since analysis/hmm_regime.py reads price history through
data.ingestion.get_price_df(), never yfinance. Pure functions (_label_states,
_standardize, _build_monthly_hmm_cache) are tested without any DB at all.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from data.database import Indicator, PriceData

# A deliberately small training threshold so synthetic test series (a few
# hundred rows) can actually clear signals.hmm_min_training_days — the
# shipped config.yaml default (504, ~2 years) would need unreasonably large
# synthetic series for a fast unit test.
_HMM_CONFIG = {
    "signals": {
        "long_window": 200, "adx_period": 14, "atr_period": 14,
        "rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "hmm_enabled": True,
        "hmm_min_training_days": 100,
        "hmm_bull_confidence_threshold": 0.60,
        "hmm_refit_interval_days": 7,
    },
}


def _seed_ohlc_at(engine, ticker: str, closes: list[float], base: datetime,
                   wick_pct: float = 0.0) -> None:
    """Insert one OHLC PriceData row per close, dated base - (n-i) days.

    wick_pct > 0 adds a small +/-wick_pct intraday high/low band so ADX
    (which reads high/low, not just close) sees genuine directional
    movement rather than a perfectly flat high=low=close series.
    """
    n = len(closes)
    with Session(engine) as session:
        for i, close in enumerate(closes):
            days_ago = n - i
            session.add(PriceData(
                ticker=ticker,
                date=base - timedelta(days=days_ago),
                open=close, high=close * (1 + wick_pct), low=close * (1 - wick_pct),
                close=close, volume=1_000_000,
            ))
        session.commit()


def _trending_closes(seed: int, n: int, drift: float, vol: float) -> list[float]:
    """Synthetic close series with a constant daily drift + noise —
    drift > 0 produces a persistent uptrend (bull-like features: high
    close/sma200 ratio, high ADX), drift < 0 a persistent downtrend."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    return list(100 * np.exp(np.cumsum(rets)))


def _choppy_closes(seed: int, n: int, vol: float) -> list[float]:
    """Synthetic close series with zero drift — a directionless, choppy
    series with a low ADX and a close/sma200 ratio hovering near 1.0."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, vol, n)
    return list(100 * np.exp(np.cumsum(rets)))


# ---------------------------------------------------------------------------
# _label_states() — pure function, no DB
# ---------------------------------------------------------------------------

def test_label_states_identifies_bull_bear_uncertain_from_means():
    """State 0 has the highest ratio+ADX (BULL), state 1 the lowest ratio
    (BEAR), state 2 is left over (UNCERTAIN) — feature order is
    [close_sma200_ratio, adx, atr_pct, rsi, macd]."""
    from analysis.hmm_regime import _label_states, FEATURE_NAMES

    means = np.array([
        [1.2,  1.0,  -0.5,  0.8,  0.6],   # state 0: high ratio, high adx -> BULL
        [-1.3, 0.3,   0.9, -0.9, -0.7],   # state 1: lowest ratio -> BEAR
        [0.1, -0.8,  -0.1,  0.1,  0.05],  # state 2: leftover -> UNCERTAIN
    ])
    labels = _label_states(means, FEATURE_NAMES)
    assert labels[0] == "BULL"
    assert labels[1] == "BEAR"
    assert labels[2] == "UNCERTAIN"


def test_label_states_uses_combined_ratio_and_adx_score_for_bull():
    """When the highest-ratio state isn't also the highest-ADX state, BULL
    is the state with the highest (ratio + adx) combined score, not
    necessarily the single highest-ratio state."""
    from analysis.hmm_regime import _label_states, FEATURE_NAMES

    means = np.array([
        [1.5, -2.0, 0.0, 0.0, 0.0],   # state 0: highest ratio alone, but very low ADX -> combined 1.5-2.0=-0.5
        [1.0,  1.0, 0.0, 0.0, 0.0],   # state 1: slightly lower ratio, much higher ADX -> combined 1.0+1.0=2.0 (wins)
        [-1.0, 0.0, 0.0, 0.0, 0.0],   # state 2: lowest ratio -> BEAR
    ])
    labels = _label_states(means, FEATURE_NAMES)
    assert labels[1] == "BULL"
    assert labels[2] == "BEAR"
    assert labels[0] == "UNCERTAIN"


# ---------------------------------------------------------------------------
# fit_hmm() — end-to-end against seeded ^GSPC price history
# ---------------------------------------------------------------------------

def test_fit_hmm_returns_none_with_insufficient_history(db_engine):
    """Fewer than signals.hmm_min_training_days usable feature rows -> no
    fit attempted, returns None rather than fitting on a thin sample."""
    from analysis.hmm_regime import fit_hmm

    base = datetime.utcnow()
    closes = _choppy_closes(seed=1, n=150, vol=0.01)  # well below the warmup + min-days floor
    _seed_ohlc_at(db_engine, "^GSPC", closes, base, wick_pct=0.01)

    model = fit_hmm(_HMM_CONFIG)
    assert model is None


def test_fit_hmm_fits_three_states_and_persists(db_engine):
    """With enough history, fit_hmm() returns a 3-state GaussianHMM and
    persists it to system_metadata (hmm_model_params + hmm_last_fit)."""
    from analysis.hmm_regime import fit_hmm
    from data.database import get_metadata_str

    base = datetime.utcnow()
    n = 500
    closes = _trending_closes(seed=2, n=n, drift=0.0005, vol=0.01)
    _seed_ohlc_at(db_engine, "^GSPC", closes, base, wick_pct=0.01)

    model = fit_hmm(_HMM_CONFIG)
    assert model is not None
    assert model.means_.shape == (3, 5)
    assert get_metadata_str("hmm_model_params") is not None
    assert get_metadata_str("hmm_last_fit") is not None


# ---------------------------------------------------------------------------
# get_hmm_regime_confidence() — probabilities sum to 1.0
# ---------------------------------------------------------------------------

def test_get_hmm_regime_confidence_probabilities_sum_to_one(db_engine):
    from analysis.hmm_regime import get_hmm_regime_confidence

    base = datetime.utcnow()
    n = 500
    closes = _trending_closes(seed=3, n=n, drift=0.0006, vol=0.01)
    _seed_ohlc_at(db_engine, "^GSPC", closes, base, wick_pct=0.01)

    result = get_hmm_regime_confidence(_HMM_CONFIG)
    assert result["hmm_model_available"] is True
    total = (
        result["hmm_bull_probability"]
        + result["hmm_bear_probability"]
        + result["hmm_uncertain_probability"]
    )
    assert abs(total - 1.0) < 1e-6
    assert result["hmm_dominant_state"] in ("BULL", "BEAR", "UNCERTAIN")
    assert result["hmm_confidence"] == max(
        result["hmm_bull_probability"], result["hmm_bear_probability"], result["hmm_uncertain_probability"],
    )


def test_get_hmm_regime_confidence_unavailable_result_sums_to_one(db_engine):
    """The uniform 1/3-1/3-1/3 fallback (no model yet) must also satisfy the
    'always sums to 1.0' contract, and must flag hmm_model_available False so
    the gate never vetoes on missing information."""
    from analysis.hmm_regime import get_hmm_regime_confidence

    result = get_hmm_regime_confidence(_HMM_CONFIG)  # empty DB — no ^GSPC rows at all
    assert result["hmm_model_available"] is False
    total = (
        result["hmm_bull_probability"]
        + result["hmm_bear_probability"]
        + result["hmm_uncertain_probability"]
    )
    assert abs(total - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Confidence gate — analysis/regime.py:detect_regime()
# ---------------------------------------------------------------------------

def _seed_gspc_indicator_row(engine, *, price: float, sma_200: float, adx: float, vix: float) -> None:
    as_of = datetime(2026, 1, 15)
    with Session(engine) as session:
        session.add(PriceData(ticker="^GSPC", date=as_of, close=price))
        session.add(Indicator(ticker="^GSPC", date=as_of, sma_200=sma_200, adx=adx, vix_level=vix))
        session.commit()


def test_confidence_gate_downgrades_trending_bull_below_threshold(db_engine, monkeypatch):
    """A rule-based TRENDING_BULL call with HMM bull probability below
    hmm_bull_confidence_threshold must be downgraded to MIXED, with
    hmm_downgraded=True and rule_regime='TRENDING_BULL' recorded."""
    from analysis.regime import detect_regime, Regime

    monkeypatch.setattr("analysis.regime._load_config", lambda: _HMM_CONFIG)
    monkeypatch.setattr(
        "analysis.hmm_regime.get_hmm_regime_confidence",
        lambda config: {
            "hmm_bull_probability": 0.42, "hmm_bear_probability": 0.08,
            "hmm_uncertain_probability": 0.50, "hmm_dominant_state": "UNCERTAIN",
            "hmm_confidence": 0.50, "hmm_agrees_with_rules": False,
            "hmm_state_means": [], "model_trained_on_days": 500,
            "hmm_model_available": True,
        },
    )
    _seed_gspc_indicator_row(db_engine, price=110, sma_200=100, adx=30, vix=15)  # rule -> TRENDING_BULL

    result = detect_regime()
    assert result["regime"] == Regime.MIXED
    assert result["hmm"]["hmm_downgraded"] is True
    assert result["hmm"]["rule_regime"] == "TRENDING_BULL"


def test_confidence_gate_confirms_trending_bull_above_threshold(db_engine, monkeypatch):
    """A rule-based TRENDING_BULL call with HMM bull probability at/above
    the threshold is confirmed — regime stays TRENDING_BULL."""
    from analysis.regime import detect_regime, Regime

    monkeypatch.setattr("analysis.regime._load_config", lambda: _HMM_CONFIG)
    monkeypatch.setattr(
        "analysis.hmm_regime.get_hmm_regime_confidence",
        lambda config: {
            "hmm_bull_probability": 0.78, "hmm_bear_probability": 0.02,
            "hmm_uncertain_probability": 0.20, "hmm_dominant_state": "BULL",
            "hmm_confidence": 0.78, "hmm_agrees_with_rules": True,
            "hmm_state_means": [], "model_trained_on_days": 500,
            "hmm_model_available": True,
        },
    )
    _seed_gspc_indicator_row(db_engine, price=110, sma_200=100, adx=30, vix=15)

    result = detect_regime()
    assert result["regime"] == Regime.TRENDING_BULL
    assert result["hmm"]["hmm_downgraded"] is False


def test_confidence_gate_does_not_override_mixed(db_engine, monkeypatch):
    """A rule-based MIXED call is never touched by the HMM gate, even when
    HMM bull probability is (irrelevantly) reported low — the gate only
    ever fires on a rule-based TRENDING_BULL call."""
    from analysis.regime import detect_regime, Regime

    monkeypatch.setattr("analysis.regime._load_config", lambda: _HMM_CONFIG)
    monkeypatch.setattr(
        "analysis.hmm_regime.get_hmm_regime_confidence",
        lambda config: {
            "hmm_bull_probability": 0.10, "hmm_bear_probability": 0.10,
            "hmm_uncertain_probability": 0.80, "hmm_dominant_state": "UNCERTAIN",
            "hmm_confidence": 0.80, "hmm_agrees_with_rules": False,
            "hmm_state_means": [], "model_trained_on_days": 500,
            "hmm_model_available": True,
        },
    )
    _seed_gspc_indicator_row(db_engine, price=100, sma_200=100, adx=22, vix=18)  # rule -> MIXED

    result = detect_regime()
    assert result["regime"] == Regime.MIXED
    assert result["hmm"]["hmm_downgraded"] is False
    assert result["hmm"]["rule_regime"] == "MIXED"


def test_confidence_gate_does_not_override_trending_bear(db_engine, monkeypatch):
    """Same as MIXED above, but for TRENDING_BEAR — HMM is informational
    only outside TRENDING_BULL."""
    from analysis.regime import detect_regime, Regime

    monkeypatch.setattr("analysis.regime._load_config", lambda: _HMM_CONFIG)
    monkeypatch.setattr(
        "analysis.hmm_regime.get_hmm_regime_confidence",
        lambda config: {
            "hmm_bull_probability": 0.05, "hmm_bear_probability": 0.90,
            "hmm_uncertain_probability": 0.05, "hmm_dominant_state": "BEAR",
            "hmm_confidence": 0.90, "hmm_agrees_with_rules": True,
            "hmm_state_means": [], "model_trained_on_days": 500,
            "hmm_model_available": True,
        },
    )
    _seed_gspc_indicator_row(db_engine, price=90, sma_200=100, adx=30, vix=25)  # rule -> TRENDING_BEAR

    result = detect_regime()
    assert result["regime"] == Regime.TRENDING_BEAR
    assert result["hmm"]["hmm_downgraded"] is False


def test_hmm_enabled_false_bypasses_gate_entirely(db_engine, monkeypatch):
    """signals.hmm_enabled: false must skip the HMM check altogether —
    detect_regime() returns the rule-based regime unchanged, and no
    HMM-derived keys are populated in the returned 'hmm' dict."""
    from analysis.regime import detect_regime, Regime

    disabled_config = {"signals": {**_HMM_CONFIG["signals"], "hmm_enabled": False}}
    monkeypatch.setattr("analysis.regime._load_config", lambda: disabled_config)

    def _should_not_be_called(config):
        raise AssertionError("get_hmm_regime_confidence must not be called when hmm_enabled is False")

    monkeypatch.setattr("analysis.hmm_regime.get_hmm_regime_confidence", _should_not_be_called)
    _seed_gspc_indicator_row(db_engine, price=110, sma_200=100, adx=30, vix=15)  # rule -> TRENDING_BULL

    result = detect_regime()
    assert result["regime"] == Regime.TRENDING_BULL
    assert result["hmm"]["hmm_downgraded"] is False
    assert "hmm_bull_probability" not in result["hmm"]


# ---------------------------------------------------------------------------
# Model persistence — cache reuse vs refit-on-stale
# ---------------------------------------------------------------------------

def test_model_loaded_from_cache_when_fit_is_recent(db_engine, monkeypatch):
    """Immediately after a successful fit, a second _load_or_refit_hmm() call
    must reuse the in-process cached model rather than reconstructing from
    the persisted JSON — verified by making the JSON-reconstruction path
    raise if it's ever invoked."""
    from analysis import hmm_regime

    base = datetime.utcnow()
    n = 500
    closes = _trending_closes(seed=4, n=n, drift=0.0004, vol=0.01)
    _seed_ohlc_at(db_engine, "^GSPC", closes, base, wick_pct=0.01)

    model = hmm_regime.fit_hmm(_HMM_CONFIG)
    assert model is not None

    def _should_not_reconstruct(params_json):
        raise AssertionError("_model_from_json must not be called when the in-process cache is fresh")

    monkeypatch.setattr(hmm_regime, "_model_from_json", _should_not_reconstruct)

    cached_model, cached_meta = hmm_regime._load_or_refit_hmm(_HMM_CONFIG)
    assert cached_model is model
    assert cached_meta["n_training_days"] > 0


def test_model_refit_when_cache_older_than_refit_interval(db_engine):
    """A stale hmm_last_fit (older than signals.hmm_refit_interval_days)
    must trigger a genuine refit, not a reuse of the stale cached params."""
    from analysis import hmm_regime
    from data.database import set_metadata, get_metadata_str

    base = datetime.utcnow()
    n = 500
    closes = _trending_closes(seed=5, n=n, drift=0.0005, vol=0.01)
    _seed_ohlc_at(db_engine, "^GSPC", closes, base, wick_pct=0.01)

    # Seed a deliberately stale hmm_last_fit (10 days ago, beyond the 7-day
    # refit_interval_days in _HMM_CONFIG) with a bogus params blob — if the
    # stale-cache path were incorrectly reused, the bogus blob would either
    # fail to parse or clearly not match a freshly fit model.
    stale_fit_time = (datetime.utcnow() - timedelta(days=10)).isoformat()
    set_metadata("hmm_last_fit", stale_fit_time)
    set_metadata("hmm_model_params", "not valid json")
    hmm_regime._CACHED_MODEL = None
    hmm_regime._CACHED_META = None
    hmm_regime._CACHED_FIT_KEY = None

    model, meta = hmm_regime._load_or_refit_hmm(_HMM_CONFIG)
    assert model is not None
    new_fit_time = get_metadata_str("hmm_last_fit")
    assert new_fit_time != stale_fit_time


# ---------------------------------------------------------------------------
# No look-ahead bias — analysis/backtest.py's monthly HMM cache
# ---------------------------------------------------------------------------

def _synthetic_price_frame(seed: int, n: int, base: pd.Timestamp, diverge_after: int) -> pd.DataFrame:
    """Build an in-memory OHLC DataFrame (no DB) identical for the first
    diverge_after rows regardless of seed, then diverging sharply after —
    mirrors tests/test_backtest.py's test_monthly_vol_cache_no_look_ahead
    pattern for the GARCH cache, applied to the HMM one instead."""
    rng_common = np.random.default_rng(1)
    common_rets = rng_common.normal(0.0003, 0.01, diverge_after)
    rng_tail = np.random.default_rng(seed)
    tail_rets = rng_tail.normal(0.0, 0.05, n - diverge_after)
    rets = np.concatenate([common_rets, tail_rets])
    closes = 100 * np.exp(np.cumsum(rets))
    dates = pd.date_range(end=base, periods=n, freq="D")
    return pd.DataFrame({
        "close": closes, "high": closes * 1.01, "low": closes * 0.99, "open": closes,
    }, index=dates)


def test_monthly_hmm_cache_no_look_ahead():
    """Two price series identical up to a month-end but diverging sharply
    afterward must produce IDENTICAL cached model parameters for that
    month-end — proving the fit never saw the post-cutoff divergence.
    Entirely in-memory (no DB): _build_monthly_hmm_cache() takes an
    already-built features DataFrame, so this needs no db_engine fixture."""
    from analysis.backtest import _build_monthly_hmm_cache, _MONTHLY_HMM_CACHE
    from analysis.hmm_regime import _build_features

    _MONTHLY_HMM_CACHE.clear()

    base = pd.Timestamp(datetime.utcnow())
    n = 500
    month_end = (base - pd.offsets.MonthEnd(2))
    diverge_after = int((month_end - pd.date_range(end=base, periods=n, freq="D")[0]).days)

    price_a = _synthetic_price_frame(seed=10, n=n, base=base, diverge_after=diverge_after)
    price_b = _synthetic_price_frame(seed=99, n=n, base=base, diverge_after=diverge_after)

    features_a = _build_features(price_a, _HMM_CONFIG)
    features_b = _build_features(price_b, _HMM_CONFIG)

    cache_a = _build_monthly_hmm_cache(features_a, month_end.date(), month_end.date(), _HMM_CONFIG)
    _MONTHLY_HMM_CACHE.clear()
    cache_b = _build_monthly_hmm_cache(features_b, month_end.date(), month_end.date(), _HMM_CONFIG)

    entry_a = cache_a[month_end.date()]
    entry_b = cache_b[month_end.date()]
    assert entry_a["model"] is not None
    assert entry_b["model"] is not None
    np.testing.assert_array_almost_equal(entry_a["feature_mean"], entry_b["feature_mean"])
    np.testing.assert_array_almost_equal(entry_a["feature_std"], entry_b["feature_std"])
    np.testing.assert_array_almost_equal(entry_a["model"].means_, entry_b["model"].means_)


def test_monthly_hmm_cache_returns_none_model_with_insufficient_history():
    """A month-end too early in the series to have hmm_min_training_days of
    prior data must cache a None model — no gate applied for those dates,
    the same 'insufficient data, leave rule-based call as-is' fallback the
    live path uses."""
    from analysis.backtest import _build_monthly_hmm_cache, _MONTHLY_HMM_CACHE
    from analysis.hmm_regime import _build_features

    _MONTHLY_HMM_CACHE.clear()
    base = pd.Timestamp(datetime.utcnow())
    n = 250  # only ~50 usable feature rows after the 200-day SMA warmup — below the 100-day min
    price_df = _synthetic_price_frame(seed=20, n=n, base=base, diverge_after=n)
    features = _build_features(price_df, _HMM_CONFIG)

    early_month_end = (base - pd.offsets.MonthEnd(1))
    cache = _build_monthly_hmm_cache(features, early_month_end.date(), early_month_end.date(), _HMM_CONFIG)
    assert cache[early_month_end.date()]["model"] is None
