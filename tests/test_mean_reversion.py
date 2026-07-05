"""Tests for analysis/mean_reversion.py: compute_mean_reversion() scoring.

compute_mean_reversion() scores mean-reversion as a 50% RSI / 50% 52-week
Z-score combination — both must agree for a non-zero score. Bollinger %B
(bb_pct_b) is deliberately excluded from the score (see that module's
docstring): a production correlation analysis (200 rows, 49 tickers,
2026-07-05) measured RSI vs Bollinger %B at 0.927, meaning the two are
redundant measurements of the same overbought/oversold signal. bb_pct_b and
bb_width are still computed and returned for storage/display (Charts page
indicator panel) — only their contribution to the score was removed.

Pure-function tests — compute_mean_reversion() takes a price DataFrame
directly and makes no DB or network calls, so no fixtures are needed.
"""

import numpy as np
import pandas as pd

from analysis.mean_reversion import compute_mean_reversion

_CONFIG = {
    "signals": {
        "rsi_period": 14,
        "bollinger_period": 20,
        "bollinger_std": 2.0,
    }
}


def _flat_df(n: int, price: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2015-01-01", periods=n, freq="B")
    closes = np.full(n, price)
    return pd.DataFrame({"date": dates, "close": closes, "high": closes, "low": closes, "open": closes})


def _decline_bounce_df(n_decline: int, decline_end: float, n_flat: int, bounce: float) -> pd.DataFrame:
    """A long decline from 200 down to decline_end, a flat stretch at
    decline_end, then a single final-day bounce of *bounce* fraction.

    Empirically tuned (n_decline=240, n_flat=15, bounce=0.01) so that, with
    the default bollinger_std=2.0, RSI (15.7) and the 52-week Z-score
    (-1.52) both classify oversold/bullish while the final day's close sits
    ABOVE its own 20-day SMA (bb_pct_b=0.64) — i.e. Bollinger %B is not
    corroborating the oversold read the way it usually would (hence the high
    real-world correlation the removal is based on). Shrinking
    bollinger_std pushes bb_pct_b past 1.0 (price outside the upper band),
    which is what the old 3-vote scoring treated as a bearish signal
    directly opposing RSI/Z-score's bullish read.
    """
    decline = np.linspace(200, decline_end, n_decline)
    flat = np.full(n_flat, decline_end)
    last = np.array([decline_end * (1 + bounce)])
    closes = np.concatenate([decline, flat, last])
    dates = pd.date_range("2015-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"date": dates, "close": closes, "high": closes, "low": closes, "open": closes})


def test_flat_series_scores_neutral():
    df = _flat_df(300)
    result = compute_mean_reversion(df, _CONFIG)
    assert result["score"] == 0


def test_insufficient_history_returns_empty_neutral_result():
    df = _flat_df(10)
    result = compute_mean_reversion(df, _CONFIG)
    assert result["score"] == 0
    assert result["rsi"] is None
    assert result["z_score_52w"] is None


def test_score_is_bullish_only_when_rsi_and_zscore_both_agree():
    """The oversold RSI + Z-score combination from _decline_bounce_df must
    score bullish (+1) — confirming the 50/50 RSI+Z scoring actually fires
    when both components agree, independent of whatever bb_pct_b reads."""
    df = _decline_bounce_df(n_decline=240, decline_end=50, n_flat=15, bounce=0.01)
    result = compute_mean_reversion(df, _CONFIG)

    assert result["rsi"] is not None and result["rsi"] < 30
    assert result["z_score_52w"] is not None and result["z_score_52w"] < -1.5
    assert result["score"] == 1


def test_bollinger_pct_b_disagreement_has_no_effect_on_score():
    """Feed a synthetic case where RSI and the 52-week Z-score agree
    (bullish/oversold) but Bollinger %B disagrees (price above its own
    20-day SMA, and — under a tightened bollinger_std — clearly outside the
    upper band, which the old 3-vote scoring treated as a bearish signal).
    Only bollinger_std is varied between the two calls, which changes
    bb_pct_b dramatically (from "inside the band" to "outside the upper
    band") without touching the underlying close series at all — so RSI and
    the Z-score, which don't depend on the Bollinger config, are identical
    in both runs. The score must be identical too, proving bb_pct_b has no
    effect on it."""
    df = _decline_bounce_df(n_decline=240, decline_end=50, n_flat=15, bounce=0.01)

    config_wide_bb = {"signals": {**_CONFIG["signals"], "bollinger_std": 2.0}}
    config_tight_bb = {"signals": {**_CONFIG["signals"], "bollinger_std": 0.2}}

    result_wide = compute_mean_reversion(df, config_wide_bb)
    result_tight = compute_mean_reversion(df, config_tight_bb)

    # Sanity: bb_pct_b actually swings from "inside the band" to "outside
    # the upper band" (a genuine directional disagreement with the bullish
    # RSI/Z read) between the two configs.
    assert result_wide["bb_pct_b"] < 1.0
    assert result_tight["bb_pct_b"] > 1.0

    # RSI/Z-score (and therefore the score) are unaffected by bollinger_std.
    assert result_wide["rsi"] == result_tight["rsi"]
    assert result_wide["z_score_52w"] == result_tight["z_score_52w"]
    assert result_wide["score"] == result_tight["score"] == 1


def test_bb_pct_b_and_bb_width_still_returned_for_display():
    """bb_pct_b/bb_width must remain in the returned dict (Charts page
    indicator panel reads them) even though they no longer feed the score."""
    df = _decline_bounce_df(n_decline=240, decline_end=50, n_flat=15, bounce=0.01)
    result = compute_mean_reversion(df, _CONFIG)

    assert result["bb_pct_b"] is not None
    assert result["bb_width"] is not None
    assert result["bb_upper"] is not None
    assert result["bb_lower"] is not None
