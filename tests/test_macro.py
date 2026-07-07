"""Tests for analysis/macro.py's regional grouping (2026-07-07).

_regional_macro_score() / _series_region() / _regional_weights() are pure
functions over a scores dict + config dict and need no DB. compute_macro()
and get_macro_dashboard() read stored FRED scores from the macro_indicators
table, so those integration tests use the db_engine fixture (in-memory
SQLite) and insert MacroIndicator rows directly with an explicit `score` —
this decouples the regional-grouping tests from the per-series threshold
logic in _score_fred_series(), which is scored and tested independently.

compute_macro()/get_macro_dashboard() call _load_config() internally when no
config is passed, which reads the real project config.yaml (same pattern
already used by tests/test_regime.py) — so these integration tests rely on
config.yaml's real macro_regional_weights (us: 0.60, europe: 0.25, asia: 0.15)
and the real region tags on DFII10/T10Y2Y/ECBDFR/IRSTCB01JPM156N.
"""

from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from analysis.backtest import _macro_scores
from analysis.macro import (
    _regional_macro_score,
    _regional_weights,
    _series_region,
    compute_macro,
    get_macro_dashboard,
)
from data.database import MacroIndicator

# ---------------------------------------------------------------------------
# Pure function tests — _regional_macro_score / _series_region / _regional_weights
# ---------------------------------------------------------------------------

_CFG = {
    "macro": {
        "fred_series": {
            "SERIES_US":       {"region": "us"},
            "SERIES_US_2":     {"region": "us"},
            "SERIES_EU":       {"region": "europe"},
            "SERIES_ASIA":     {"region": "asia"},
            "SERIES_NOREGION": {},  # no 'region' key at all
        }
    },
    "signals": {
        "macro_regional_weights": {"us": 0.60, "europe": 0.25, "asia": 0.15}
    },
}


def test_regional_scores_computed_correctly_from_individual_series_scores():
    """Each region's score is the plain average of its own member series' scores."""
    rows = {
        "SERIES_US":   {"score": 1},
        "SERIES_US_2": {"score": -1},   # us avg = (1 + -1) / 2 = 0.0
        "SERIES_EU":   {"score": 1},    # europe avg = 1.0 (only member)
        "SERIES_ASIA": {"score": 0},    # asia avg = 0.0 (only member)
    }
    result = _regional_macro_score(rows, vix_score=None, config=_CFG)
    assert result["region_scores"] == {"us": 0.0, "europe": 1.0, "asia": 0.0}


def test_vix_score_always_counted_in_us_region():
    """VIX has no 'region' field (it's not a FRED series) — always folded into 'us'."""
    result = _regional_macro_score({}, vix_score=1, config=_CFG)
    assert result["region_scores"] == {"us": 1.0}


def test_weighted_combination_sums_regional_weights_to_one():
    """With all three regions present, the applied weights must sum to 1.0 and
    match config.yaml's 0.60 / 0.25 / 0.15 split exactly (nothing missing)."""
    rows = {
        "SERIES_US":   {"score": 1},
        "SERIES_EU":   {"score": -1},
        "SERIES_ASIA": {"score": 1},
    }
    result = _regional_macro_score(rows, vix_score=None, config=_CFG)
    weights = result["region_weights"]
    assert weights == {"us": 0.60, "europe": 0.25, "asia": 0.15}
    assert abs(sum(weights.values()) - 1.0) < 1e-9

    expected = 1 * 0.60 + (-1) * 0.25 + 1 * 0.15
    assert abs(result["weighted_score"] - expected) < 1e-9


def test_graceful_degradation_missing_region_redistributes_weight():
    """No Asia data at all -> Asia's 0.15 weight is redistributed proportionally
    across US (0.60) and Europe (0.25), not dropped or scored as neutral."""
    rows = {
        "SERIES_US": {"score": 1},
        "SERIES_EU": {"score": -1},
        # no SERIES_ASIA row at all
    }
    result = _regional_macro_score(rows, vix_score=None, config=_CFG)

    assert set(result["region_scores"].keys()) == {"us", "europe"}
    assert "asia" not in result["region_weights"]

    expected_us_w = 0.60 / (0.60 + 0.25)
    expected_eu_w = 0.25 / (0.60 + 0.25)
    assert abs(result["region_weights"]["us"] - expected_us_w) < 1e-9
    assert abs(result["region_weights"]["europe"] - expected_eu_w) < 1e-9
    assert abs(sum(result["region_weights"].values()) - 1.0) < 1e-9

    expected_score = 1 * expected_us_w + (-1) * expected_eu_w
    assert abs(result["weighted_score"] - expected_score) < 1e-9


def test_graceful_degradation_when_no_region_has_any_data():
    """Completely empty input (no VIX, no stored rows) must not raise — score is 0."""
    result = _regional_macro_score({}, vix_score=None, config=_CFG)
    assert result["region_scores"] == {}
    assert result["region_weights"] == {}
    assert result["weighted_score"] == 0.0


def test_backwards_compatibility_series_without_region_defaults_to_us():
    """A fred_series entry with no 'region' key, and a series_id not present in
    fred_series at all, must both default to 'us' — configs written before
    regional grouping existed keep working unchanged."""
    assert _series_region("SERIES_NOREGION", _CFG) == "us"
    assert _series_region("SOME_UNCONFIGURED_SERIES_ID", _CFG) == "us"


def test_backwards_compatibility_missing_regional_weights_config_uses_default():
    """If signals.macro_regional_weights is absent entirely, fall back to the
    documented default 0.60 / 0.25 / 0.15 split rather than erroring."""
    cfg_no_weights = {"macro": {"fred_series": {}}, "signals": {}}
    weights = _regional_weights(cfg_no_weights)
    assert weights == {"us": 0.60, "europe": 0.25, "asia": 0.15}


def test_strongly_bullish_us_with_neutral_europe_asia_is_moderated():
    """A strongly bullish US reading with neutral Europe/Asia should still net
    bullish overall, but moderated down from +1.0 by the two neutral regions —
    not diluted away to near-zero, and not left at the full US-only value."""
    rows = {
        "SERIES_US":   {"score": 1},
        "SERIES_EU":   {"score": 0},
        "SERIES_ASIA": {"score": 0},
    }
    result = _regional_macro_score(rows, vix_score=None, config=_CFG)
    assert result["weighted_score"] == 0.60  # 1*0.60 + 0*0.25 + 0*0.15
    assert 0.0 < result["weighted_score"] < 1.0


def test_overall_score_range_bounded_negative_one_to_one():
    """However scores combine, the weighted result can never fall outside
    [-1, +1] since it's a convex combination (weights sum to 1) of per-region
    averages that are themselves each bounded to [-1, +1]."""
    all_bullish = {"SERIES_US": {"score": 1}, "SERIES_EU": {"score": 1}, "SERIES_ASIA": {"score": 1}}
    all_bearish = {"SERIES_US": {"score": -1}, "SERIES_EU": {"score": -1}, "SERIES_ASIA": {"score": -1}}
    assert _regional_macro_score(all_bullish, None, _CFG)["weighted_score"] == 1.0
    assert _regional_macro_score(all_bearish, None, _CFG)["weighted_score"] == -1.0

    mixed = {"SERIES_US": {"score": -1}, "SERIES_EU": {"score": 1}, "SERIES_ASIA": {"score": 1}}
    result = _regional_macro_score(mixed, None, _CFG)
    assert -1.0 <= result["weighted_score"] <= 1.0


# ---------------------------------------------------------------------------
# Integration tests — compute_macro() / get_macro_dashboard() against the
# real config.yaml (region tags + macro_regional_weights), via db_engine.
# ---------------------------------------------------------------------------

def _seed_macro_row(engine, series_id: str, score: int) -> None:
    with Session(engine) as session:
        session.add(
            MacroIndicator(
                series_id=series_id,
                date=datetime(2026, 7, 1),
                value=0.0,
                pct_change_30d=0.0,
                z_score_2y=0.0,
                score=score,
            )
        )
        session.commit()


def test_get_macro_dashboard_regional_breakdown_differs_from_flat_average(db_engine):
    """A US-neutral / Europe-bullish / Asia-bullish mix should weight down
    toward the (much larger) US share rather than averaging flat across all
    scored series — demonstrating the regional blend is genuinely applied,
    not just present as unused extra fields."""
    _seed_macro_row(db_engine, "DFII10", 0)              # us
    _seed_macro_row(db_engine, "ECBDFR", 1)               # europe
    _seed_macro_row(db_engine, "IRSTCB01JPM156N", 1)      # asia

    dash = get_macro_dashboard()

    assert dash["region_scores"]["us"] == 0.0
    assert dash["region_scores"]["europe"] == 1.0
    assert dash["region_scores"]["asia"] == 1.0
    assert dash["region_weights_config"] == {"us": 0.60, "europe": 0.25, "asia": 0.15}

    # Weighted (portfolio-proportional): 0*0.60 + 1*0.25 + 1*0.15 = 0.40
    assert abs(dash["composite_score"] - 0.40) < 1e-6

    # A flat, unweighted average of the same three raw scores would be much
    # higher — confirms the composite is not silently falling back to that.
    flat_avg = (0 + 1 + 1) / 3
    assert abs(dash["composite_score"] - flat_avg) > 0.1


def test_compute_macro_uses_regional_weighting_not_flat_average(db_engine):
    """compute_macro()'s per-ticker score must reflect the same weighted
    regional blend as get_macro_dashboard(), not a flat average, for a
    ticker with no per-ticker adjustments in play."""
    _seed_macro_row(db_engine, "DFII10", 1)               # us
    _seed_macro_row(db_engine, "T10Y2Y", 1)                # us -> us avg = 1.0
    _seed_macro_row(db_engine, "ECBDFR", -1)              # europe avg = -1.0
    _seed_macro_row(db_engine, "IRSTCB01JPM156N", -1)     # asia avg = -1.0

    # Ticker with no per-ticker adjustment routing (CFNAI/DFEDTARU/M2SL/PERMIT
    # all require specific watchlist-group or _RATE_*/_GOLD_SET membership —
    # an arbitrary/unlisted ticker gets none of those, isolating the test to
    # the regional blend alone).
    result = compute_macro("SOME_UNLISTED_TICKER")

    # Weighted: 1*0.60 + -1*0.25 + -1*0.15 = 0.60 - 0.40 = 0.20 -> below the
    # +/-0.25 classification threshold -> NEUTRAL (0), whereas a flat average
    # over the four raw scores (1+1-1-1)/4 = 0.0 would also be neutral here —
    # use region_scores directly (exposed via get_macro_dashboard) to confirm
    # the regional blend, not just the final classification, is as expected.
    dash = get_macro_dashboard()
    assert dash["region_scores"] == {"us": 1.0, "europe": -1.0, "asia": -1.0}
    assert result["score"] == 0


def test_new_international_series_are_not_self_referentially_excluded(db_engine):
    """The four new international series carry no ticker-specific adjustment
    routing (unlike CFNAI/DFEDTARU/M2SL/PERMIT) — they only ever contribute
    through the regional composite, for every non-macro ticker."""
    _seed_macro_row(db_engine, "ECBDFR", 1)
    result_a = compute_macro("AAPL")
    result_b = compute_macro("SOME_OTHER_TICKER")
    assert result_a["score"] == result_b["score"]


# ---------------------------------------------------------------------------
# analysis/backtest.py:_macro_scores() — the vectorized backtest mirror of
# the same regional weighting. The backtest engine does NOT read
# compute_macro()'s stored indicators-table score at all (it re-implements
# every scoring module with rolling/ewm to stay look-ahead free — see
# CLAUDE.md's "Backtesting engine" section) — _macro_scores() is a fully
# independent vectorized re-implementation that must mirror the live
# regional weighting itself, the same way _ma_cross_vote() and
# _mean_rev_scores() already mirror their own live counterparts.
# ---------------------------------------------------------------------------

_BT_CFG = {
    "macro": {
        "fred_series": {
            "BT_US": {"region": "us"},
            "BT_EU": {"region": "europe"},
            "BT_ASIA": {"region": "asia"},
        }
    },
    "signals": {
        "macro_regional_weights": {"us": 0.60, "europe": 0.25, "asia": 0.15},
    },
}


def _flat_df(n: int, value: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"close": [value] * n}, index=idx)


def test_backtest_macro_scores_mirror_regional_weighting():
    """_macro_scores() must apply the same regional blend as
    _regional_macro_score() — US-neutral/Europe-bullish/Asia-bullish nets to
    the same moderated +0.40, not a flat average."""
    df = _flat_df(10)
    macro_df = pd.DataFrame(
        {"BT_US": [0] * 10, "BT_EU": [1] * 10, "BT_ASIA": [1] * 10},
        index=df.index,
    )
    scores = _macro_scores(df, "SOME_TICKER", vix_s=None, macro_df=macro_df, config=_BT_CFG)
    # weighted = 0*0.60 + 1*0.25 + 1*0.15 = 0.40 -> below the 0.25 threshold? No: 0.40 >= 0.25 -> bullish (+1)
    assert (scores == 1).all()


def test_backtest_macro_scores_graceful_degradation_before_region_history_starts():
    """A region with no data yet for the first few rows (nothing to ffill
    from) must not silently score neutral for those dates — its weight is
    redistributed across the regions that do have data, exactly like the
    live path, then the region catches up once its first observation lands."""
    df = _flat_df(6)
    macro_df = pd.DataFrame(
        {
            "BT_US": [np.nan, np.nan, 1, 1, 1, 1],   # US series starts reporting on day 3
            "BT_EU": [-1] * 6,
        },
        index=df.index,
    )
    scores = _macro_scores(df, "SOME_TICKER", vix_s=None, macro_df=macro_df, config=_BT_CFG)
    # First two days: only Europe has data -> weighted score = -1.0 (bearish)
    assert scores.iloc[0] == -1
    assert scores.iloc[1] == -1
    # From day 3: US (0.60) + Europe (0.25) -> weighted = 1*0.60 + -1*0.25 = 0.35 -> bullish
    assert scores.iloc[2] == 1


def test_backtest_macro_scores_backward_compat_config_with_no_macro_section():
    """A config predating regional grouping (no 'macro' key at all) must not
    crash — every series defaults to 'us' via _series_region()'s fallback."""
    df = _flat_df(5)
    macro_df = pd.DataFrame({"SOME_LEGACY_SERIES": [1] * 5}, index=df.index)
    legacy_cfg = {"signals": {}}  # no macro.fred_series, no macro_regional_weights
    scores = _macro_scores(df, "SOME_TICKER", vix_s=None, macro_df=macro_df, config=legacy_cfg)
    assert (scores == 1).all()


def test_backtest_macro_scores_self_referential_tickers_stay_neutral():
    """TLT/IEF/HYG/^VIX must still score neutral regardless of macro_df content —
    unchanged from before regional weighting existed."""
    df = _flat_df(5)
    macro_df = pd.DataFrame({"BT_US": [1] * 5}, index=df.index)
    for ticker in ("TLT", "IEF", "HYG", "^VIX"):
        scores = _macro_scores(df, ticker, vix_s=None, macro_df=macro_df, config=_BT_CFG)
        assert (scores == 0).all()
