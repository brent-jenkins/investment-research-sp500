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
and the real region tags on DFII10/T10Y2Y/ECBDFR/IRSTCI01JPM156N.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from analysis.backtest import _macro_scores
from analysis.macro import (
    _calendar_staleness,
    _CALENDAR_STALENESS_UNSUPPORTED,
    _next_expected_release,
    _regional_macro_score,
    _regional_weights,
    _release_cache_calendar_key,
    _release_cache_meta_key,
    _series_region,
    _series_staleness,
    backfill_macro_indicators,
    compute_macro,
    fetch_and_store_macro_indicators,
    get_macro_dashboard,
    refresh_release_calendars,
)
from data.database import MacroIndicator, set_metadata

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
    _seed_macro_row(db_engine, "IRSTCI01JPM156N", 1)      # asia

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
    _seed_macro_row(db_engine, "IRSTCI01JPM156N", -1)     # asia avg = -1.0

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


def test_chngdprapsmei_not_stale_despite_period_start_dating_lag(db_engine):
    """FRED dates each CHNGDPRAPSMEI annual observation at the start of the
    year it covers, not its publish date — a freshly-published observation
    can already look 500+ days "old" by simple date arithmetic. Verified in
    production 2026-07-10: a value published ~March 2026 (5.0% growth for
    2025) displayed as 555 days old under the original 400-day override,
    incorrectly flagging current data as stale. The override (raised to 800
    the same day) must not flag this normal publish-lag age."""
    with Session(db_engine) as session:
        session.add(MacroIndicator(
            series_id="CHNGDPRAPSMEI",
            date=datetime.utcnow() - timedelta(days=555),
            value=5.0, pct_change_30d=5.0, z_score_2y=0.0, score=1,
        ))
        session.commit()

    dash = get_macro_dashboard()
    row = next(s for s in dash["extra_series"] if s["series_id"] == "CHNGDPRAPSMEI")
    assert row["stale"] is False
    assert row["age_days"] == 555


def test_chngdprapsmei_still_flagged_stale_past_800_days(db_engine):
    """Genuine multi-year discontinuation (like the predecessor series this
    replaced, which went 1,105 days without an update before being swapped
    out) must still be caught — the 800-day override is generous, not
    unlimited."""
    with Session(db_engine) as session:
        session.add(MacroIndicator(
            series_id="CHNGDPRAPSMEI",
            date=datetime.utcnow() - timedelta(days=900),
            value=5.0, pct_change_30d=5.0, z_score_2y=0.0, score=1,
        ))
        session.commit()

    dash = get_macro_dashboard()
    row = next(s for s in dash["extra_series"] if s["series_id"] == "CHNGDPRAPSMEI")
    assert row["stale"] is True


def test_cfnai_m2sl_bogmbase_not_stale_at_74_days(db_engine):
    """CFNAI/M2SL/BOGMBASE are dated at the 1st of the month they cover and
    publish 3-5 weeks after month-end, then remain the latest value for
    another ~month until superseded — the same period-start-dating pattern
    as CHNGDPRAPSMEI, just at monthly scale. Verified live 2026-07-15
    against the FRED API directly: all three genuinely had no newer
    observation available at 74 days old (the flat 35-day US default,
    tuned for daily/weekly series, was flagging normal publish lag as
    stale). The 90-day override must not flag this age."""
    with Session(db_engine) as session:
        for sid in ("CFNAI", "M2SL", "BOGMBASE"):
            session.add(MacroIndicator(
                series_id=sid,
                date=datetime.utcnow() - timedelta(days=74),
                value=1.0, pct_change_30d=1.0, z_score_2y=0.0, score=1,
            ))
        session.commit()

    dash = get_macro_dashboard()
    for sid in ("CFNAI", "M2SL", "BOGMBASE"):
        row = next(s for s in dash["extra_series"] if s["series_id"] == sid)
        assert row["stale"] is False, f"{sid} incorrectly flagged stale at 74 days"


def test_cfnai_m2sl_bogmbase_still_flagged_stale_past_90_days(db_engine):
    """The 90-day override is generous relative to these series' normal
    ~77-86 day worst-case update gap, not unlimited — a genuine multi-month
    fetch failure or FRED-side discontinuation (as happened to the Asia
    series this codebase already replaced once) must still be caught."""
    with Session(db_engine) as session:
        for sid in ("CFNAI", "M2SL", "BOGMBASE"):
            session.add(MacroIndicator(
                series_id=sid,
                date=datetime.utcnow() - timedelta(days=120),
                value=1.0, pct_change_30d=1.0, z_score_2y=0.0, score=1,
            ))
        session.commit()

    dash = get_macro_dashboard()
    for sid in ("CFNAI", "M2SL", "BOGMBASE"):
        row = next(s for s in dash["extra_series"] if s["series_id"] == sid)
        assert row["stale"] is True, f"{sid} should be flagged stale at 120 days"


def test_permit_stale_flag_computed_despite_specialty_rendering(db_engine):
    """PERMIT is in main.py's _SPECIALTY set (bespoke tile, not the generic
    extra_series loop) and shares CFNAI/M2SL/BOGMBASE's period-start-dating
    + monthly publish-lag pattern, but had no staleness computation at all
    until this was flagged 2026-07-15 — the bespoke render path skipped the
    extra_series loop that computes age_days/stale entirely, silently. Must
    behave the same as the extra_series-driven series: not stale at 74 days,
    stale past the 90-day override."""
    with Session(db_engine) as session:
        session.add(MacroIndicator(
            series_id="PERMIT",
            date=datetime.utcnow() - timedelta(days=74),
            value=1400.0, pct_change_30d=1.0, z_score_2y=0.0, score=1,
        ))
        session.commit()

    dash = get_macro_dashboard()
    assert dash["permit_stale"] is False
    assert dash["permit_age_days"] == 74

    with Session(db_engine) as session:
        row = session.query(MacroIndicator).filter_by(series_id="PERMIT").one()
        row.date = datetime.utcnow() - timedelta(days=120)
        session.commit()

    dash = get_macro_dashboard()
    assert dash["permit_stale"] is True


# ---------------------------------------------------------------------------
# The other 7 _SPECIALTY series (DFII10, T10YIE, BAMLH0A0HYM2, BAMLC0A0CM,
# T10Y2Y, T10Y3M, DFEDTARU) had the identical gap PERMIT had: bespoke
# rendering in main.py, no age_days/stale computed anywhere. Unlike
# CFNAI/M2SL/BOGMBASE/PERMIT, these are all genuinely-daily FRED series
# (verified live against the FRED API 2026-07-15 — all confirmed
# "frequency": "Daily" or "Daily, Close"/"Daily, 7-Day", latest observation
# dated today or the prior business day) with no period-start-dating
# mismatch, so they use the plain 35-day "us" default — no per-series
# override needed, unlike the monthly series above. This was a latent
# monitoring gap rather than an active bug: all 7 were confirmed fresh at
# the time this was found, but a future silent failure on any of them
# would have had no way to surface on the dashboard at all.
# ---------------------------------------------------------------------------

_OTHER_SPECIALTY_SERIES = {
    "DFII10":       "real_yield",
    "T10YIE":       "breakeven",
    "BAMLH0A0HYM2": "hy_oas",
    "BAMLC0A0CM":   "ig_oas",
    "T10Y2Y":       "t10y2y",
    "T10Y3M":       "t10y3m",
    "DFEDTARU":     "fed_rate",
}


def test_other_specialty_series_not_stale_at_30_days(db_engine):
    """30 days is comfortably inside the 35-day "us" default for these
    genuinely-daily series — none should be flagged."""
    with Session(db_engine) as session:
        for sid in _OTHER_SPECIALTY_SERIES:
            session.add(MacroIndicator(
                series_id=sid,
                date=datetime.utcnow() - timedelta(days=30),
                value=1.0, pct_change_30d=1.0, z_score_2y=0.0, score=1,
            ))
        session.commit()

    dash = get_macro_dashboard()
    for sid, key in _OTHER_SPECIALTY_SERIES.items():
        assert dash[f"{key}_stale"] is False, f"{sid} incorrectly flagged stale at 30 days"
        assert dash[f"{key}_age_days"] == 30


def test_other_specialty_series_flagged_stale_past_35_days(db_engine):
    """40 days is past the 35-day "us" default — a genuine gap (fetch
    failure, FRED-side issue) in one of these daily series must still be
    caught, now that each has a staleness computation at all."""
    with Session(db_engine) as session:
        for sid in _OTHER_SPECIALTY_SERIES:
            session.add(MacroIndicator(
                series_id=sid,
                date=datetime.utcnow() - timedelta(days=40),
                value=1.0, pct_change_30d=1.0, z_score_2y=0.0, score=1,
            ))
        session.commit()

    dash = get_macro_dashboard()
    for sid, key in _OTHER_SPECIALTY_SERIES.items():
        assert dash[f"{key}_stale"] is True, f"{sid} should be flagged stale at 40 days"


# ---------------------------------------------------------------------------
# Release-calendar-based staleness (2026-07-16) — replaces the guessed
# per-series day-count overrides above with a check against FRED's actual
# publish schedule. refresh_release_calendars() is the only function under
# test here that calls the (mocked) FRED API at all; _calendar_staleness()/
# _next_expected_release()/_series_staleness() only ever read the cache
# (system_metadata), matching the "never block a page render on a live
# HTTP call" design — so those tests need no network mocking at all, only
# db_engine to back system_metadata.
# ---------------------------------------------------------------------------

def _fake_requests_get(mocker):
    """A requests.get stand-in that routes by endpoint path, returning the
    same response shapes verified live against the real FRED API for this
    feature (series/release -> releases[0].id, series -> seriess[0].
    frequency_short, release/dates -> release_dates[].date)."""
    def _get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        params = params or {}
        if url.endswith("/series/release"):
            sid = params.get("series_id")
            resp.json.return_value = {"releases": [{"id": _FAKE_RELEASE_IDS[sid], "name": "fake"}]}
        elif url.endswith("/series"):
            sid = params.get("series_id")
            resp.json.return_value = {"seriess": [{"frequency_short": _FAKE_FREQUENCIES[sid]}]}
        elif url.endswith("/release/dates"):
            rid = params.get("release_id")
            resp.json.return_value = {"release_dates": [{"date": d} for d in _FAKE_CALENDARS[rid]]}
        else:
            raise AssertionError(f"Unexpected URL in test: {url}")
        return resp
    return mocker.patch("analysis.macro.requests.get", side_effect=_get)


# Mirrors the real release_id/frequency/calendar this feature was verified
# against live (CFNAI on 219, M2SL+BOGMBASE sharing 21) — see the module
# docstring in analysis/macro.py for the full verified mapping.
_FAKE_RELEASE_IDS = {"CFNAI": 219, "M2SL": 21, "BOGMBASE": 21}
_FAKE_FREQUENCIES = {"CFNAI": "M", "M2SL": "M", "BOGMBASE": "M"}
_FAKE_CALENDARS = {
    219: ["2026-01-26", "2026-02-23", "2026-03-23", "2026-04-23", "2026-05-26", "2026-06-25", "2026-07-23", "2026-08-24"],
    21:  ["2026-01-27", "2026-02-24", "2026-03-24", "2026-04-28", "2026-05-26", "2026-06-23", "2026-07-28", "2026-08-25"],
}

_RELEASE_CAL_CFG = {
    "macro": {
        "fred_api_key": "test-key",
        "fred_series": {
            "CFNAI":    {"region": "us"},
            "M2SL":     {"region": "us"},
            "BOGMBASE": {"region": "us"},
        },
    },
}


def test_refresh_release_calendars_caches_release_id_and_frequency(db_engine, mocker):
    """A successful refresh stores (release_id, frequency_short) per series
    in system_metadata, readable back via the cache-key helpers."""
    _fake_requests_get(mocker)

    refresh_release_calendars(_RELEASE_CAL_CFG)

    from data.database import get_metadata_str
    meta = json.loads(get_metadata_str(_release_cache_meta_key("CFNAI")))
    assert meta["release_id"] == 219
    assert meta["frequency_short"] == "M"


def test_refresh_release_calendars_shares_calendar_across_series_on_same_release(db_engine, mocker):
    """M2SL and BOGMBASE both resolve to release_id 21 (H.6) — the calendar
    for release_id 21 must only be fetched once, not once per series, per
    this feature's explicit 'don't make redundant API calls for series on
    the same schedule' requirement."""
    get_mock = _fake_requests_get(mocker)

    refresh_release_calendars(_RELEASE_CAL_CFG)

    release_dates_calls = [
        c for c in get_mock.call_args_list if c.args[0].endswith("/release/dates")
    ]
    release_ids_requested = [c.kwargs["params"]["release_id"] for c in release_dates_calls]
    assert release_ids_requested.count(21) == 1, "release_id 21 (shared by M2SL/BOGMBASE) fetched more than once"
    assert sorted(set(release_ids_requested)) == [21, 219]

    from data.database import get_metadata_str
    for sid in ("M2SL", "BOGMBASE"):
        meta = json.loads(get_metadata_str(_release_cache_meta_key(sid)))
        assert meta["release_id"] == 21
    cal = json.loads(get_metadata_str(_release_cache_calendar_key(21)))
    assert cal["dates"] == _FAKE_CALENDARS[21]


def test_refresh_release_calendars_never_calls_fred_for_chngdprapsmei(db_engine, mocker):
    """CHNGDPRAPSMEI is permanently excluded from the calendar mechanism
    (see _CALENDAR_STALENESS_UNSUPPORTED) — refresh must never even attempt
    a series/release lookup for it."""
    get_mock = _fake_requests_get(mocker)
    cfg = {
        "macro": {
            "fred_api_key": "test-key",
            "fred_series": {"CFNAI": {"region": "us"}, "CHNGDPRAPSMEI": {"region": "asia"}},
        },
    }
    refresh_release_calendars(cfg)

    requested_series_ids = {
        c.kwargs["params"]["series_id"]
        for c in get_mock.call_args_list
        if c.args[0].endswith("/series/release") or c.args[0].endswith("/series")
    }
    assert "CHNGDPRAPSMEI" not in requested_series_ids


def test_refresh_release_calendars_one_series_failure_does_not_abort_remaining(db_engine, mocker):
    """A malformed/failed response for one series must not prevent the
    others from being cached — matches backfill_macro_indicators()'s own
    per-series isolation."""
    def _get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if params.get("series_id") == "CFNAI" and url.endswith("/series/release"):
            resp.json.return_value = {"releases": []}  # malformed — no release found
        elif url.endswith("/series/release"):
            resp.json.return_value = {"releases": [{"id": _FAKE_RELEASE_IDS[params["series_id"]]}]}
        elif url.endswith("/series"):
            resp.json.return_value = {"seriess": [{"frequency_short": _FAKE_FREQUENCIES[params["series_id"]]}]}
        elif url.endswith("/release/dates"):
            resp.json.return_value = {"release_dates": [{"date": d} for d in _FAKE_CALENDARS[params["release_id"]]]}
        return resp
    mocker.patch("analysis.macro.requests.get", side_effect=_get)

    results = refresh_release_calendars(_RELEASE_CAL_CFG)

    assert results["CFNAI"] is False
    assert results["M2SL"] is True
    assert results["BOGMBASE"] is True

    from data.database import get_metadata_str
    assert get_metadata_str(_release_cache_meta_key("CFNAI")) is None
    assert get_metadata_str(_release_cache_meta_key("M2SL")) is not None


def _seed_release_cache(sid: str, release_id: int, frequency_short: str, dates: list[str], *, fresh: bool = True):
    age = timedelta(hours=1) if fresh else timedelta(hours=100)
    set_metadata(_release_cache_meta_key(sid), json.dumps({
        "release_id": release_id, "frequency_short": frequency_short,
        "fetched_at": (datetime.utcnow() - age).isoformat(),
    }))
    set_metadata(_release_cache_calendar_key(release_id), json.dumps({
        "dates": dates, "fetched_at": (datetime.utcnow() - age).isoformat(),
    }))


def test_next_expected_release_matches_verified_fred_vintage_date(db_engine):
    """CFNAI's May 2026 observation (dated 2026-05-01) was verified live
    against FRED's own realtime_start to have first become known on
    2026-06-25 — meaning the *next* new observation (June's) is expected
    at the following release, 2026-07-23. This is the exact case documented
    in analysis/macro.py's module comment."""
    _seed_release_cache("CFNAI", 219, "M", _FAKE_CALENDARS[219])
    assert _next_expected_release("CFNAI", "2026-05-01") == datetime(2026, 7, 23).date()


def test_next_expected_release_stays_short_for_daily_meeting_driven_releases(db_engine):
    """DFEDTARU (release_id 101, 'FOMC Press Release') and ECBDFR
    (release_id 484, 'Key ECB Interest Rates') have meeting-driven release
    *names* that could suggest a sparse (~8/year) calendar — which would
    push next_release out to the next policy meeting even though both
    series report a value every business day, the mirror-image risk to
    CHNGDPRAPSMEI's (there the release fired too *often* relative to the
    data; here the concern was firing too *rarely*). Verified live against
    real vintage data: individual daily DFEDTARU/ECBDFR observations each
    have a realtime_start matching their own date, and both release_id
    calendars independently confirmed daily/business-day cadence (101: 365
    dates/year, 484: ~251/year) — see the _CALENDAR_STALENESS_UNSUPPORTED
    comment. This pins that both compute a short (single-digit-day) next
    expected release, not a multi-week jump to the next meeting."""
    dfedtaru_dates = ["2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13", "2026-08-05"]
    _seed_release_cache("DFEDTARU", 101, "D", dfedtaru_dates)
    assert _next_expected_release("DFEDTARU", "2026-07-10") == datetime(2026, 7, 12).date()

    ecbdfr_dates = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13", "2026-09-01"]
    _seed_release_cache("ECBDFR", 484, "D", ecbdfr_dates)
    assert _next_expected_release("ECBDFR", "2026-07-10") == datetime(2026, 7, 13).date()


def test_calendar_staleness_not_stale_when_release_date_in_future(db_engine):
    """A last_obs_date old enough to fail the plain 35-day US default, but
    whose computed next_release (2 reporting periods out, per the verified
    formula) still hasn't arrived — must not be flagged. Uses dates
    relative to the real test-run clock (not hardcoded calendar years) so
    this stays correct indefinitely rather than only until a fixed date."""
    today = datetime.utcnow().date()
    last_obs_date = (today - timedelta(days=50)).isoformat()          # well past the 35-day US default
    far_future_release = (today + timedelta(days=200)).isoformat()    # comfortably beyond 2x period + grace
    _seed_release_cache("CFNAI", 219, "M", [far_future_release])
    assert _calendar_staleness("CFNAI", last_obs_date) is False


def test_calendar_staleness_stale_when_release_passed_and_not_updated(db_engine):
    """A release date safely in the past (relative to the real test-run
    clock) with no newer stored observation must flag stale."""
    past_calendar = ["2019-01-01", "2019-02-01", "2019-03-01"]
    _seed_release_cache("CFNAI", 219, "M", past_calendar)
    assert _calendar_staleness("CFNAI", "2018-11-01") is True


def test_calendar_staleness_none_when_no_cache_present(db_engine):
    """No cached meta/calendar at all (cold cache, e.g. right after a fresh
    deploy before the first EOD refresh has run) -> _calendar_staleness
    returns None, signalling the caller to fall back."""
    assert _calendar_staleness("CFNAI", "2026-05-01") is None


def test_calendar_staleness_none_when_cache_expired(db_engine):
    """A cache older than _RELEASE_CACHE_TTL_HOURS must not be trusted,
    even though it's technically present — falls back."""
    _seed_release_cache("CFNAI", 219, "M", _FAKE_CALENDARS[219], fresh=False)
    assert _calendar_staleness("CFNAI", "2026-05-01") is None


def test_calendar_staleness_none_when_calendar_does_not_cover_needed_date(db_engine):
    """A cached calendar that doesn't reach far enough forward (e.g.
    release_id 205's real OECD calendar, confirmed live to have no dates
    past 2026-06-15) must not be silently treated as 'never stale' — it
    signals 'unknown, fall back' instead."""
    short_calendar = ["2026-01-15", "2026-02-16", "2026-03-16"]  # nothing near the needed threshold
    _seed_release_cache("IRSTCI01JPM156N", 205, "M", short_calendar)
    assert _next_expected_release("IRSTCI01JPM156N", "2026-05-01") is None
    assert _calendar_staleness("IRSTCI01JPM156N", "2026-05-01") is None


def test_chngdprapsmei_never_uses_calendar_check_even_with_cache_present(db_engine):
    """CHNGDPRAPSMEI is permanently excluded (_CALENDAR_STALENESS_UNSUPPORTED)
    regardless of whether a cache happens to exist for it — verified live
    that the calendar formula reproduces ~2 months/year of false staleness
    for this specific series (see the module comment). Seeding a cache
    anyway (as if refresh_release_calendars() had been changed to include
    it by mistake) must still be ignored."""
    assert "CHNGDPRAPSMEI" in _CALENDAR_STALENESS_UNSUPPORTED
    _seed_release_cache("CHNGDPRAPSMEI", 205, "A", ["2026-01-15", "2026-02-16"])
    assert _next_expected_release("CHNGDPRAPSMEI", "2025-01-01") is None
    assert _calendar_staleness("CHNGDPRAPSMEI", "2025-01-01") is None


def test_series_staleness_uses_calendar_result_over_day_count(db_engine):
    """When the calendar check is computable, it must win over the
    day-count threshold even when the two would disagree — 74 days old
    exceeds no override for CFNAI's plain "us" default (35d) but the real
    2026-06-25 vintage makes the June release (2026-07-23) the correct
    'not stale until' date."""
    _seed_release_cache("CFNAI", 219, "M", _FAKE_CALENDARS[219])
    row = {"date": "2026-05-01"}
    age_days, is_stale = _series_staleness(row, "CFNAI", "us")
    assert age_days is not None and age_days > 35  # would fail the flat US default alone
    next_release = _next_expected_release("CFNAI", "2026-05-01")
    assert is_stale == (datetime.utcnow().date() > next_release + timedelta(days=2))


def test_series_staleness_falls_back_to_day_count_when_calendar_unavailable(db_engine):
    """No release-calendar cache present at all (the state every existing
    pre-this-feature test runs in) must reproduce exactly the prior
    day-count-only behaviour — this is the regression guarantee for the
    32 staleness tests above, which were all written before this feature
    and never seed a release-calendar cache."""
    row = {"date": (datetime.utcnow() - timedelta(days=74)).strftime("%Y-%m-%d %H:%M:%S.%f")}
    age_days, is_stale = _series_staleness(row, "CFNAI", "us")
    assert age_days == 74
    assert is_stale is False  # CFNAI's day-count override is 90


def test_series_staleness_fallback_logs_warning_for_computable_series(db_engine, caplog):
    """A series that *should* be calendar-checkable (not CHNGDPRAPSMEI) but
    has no usable cache yet must log a warning identifying the fallback —
    this is what makes a persistently-broken refresh visible in server
    logs rather than silently degrading forever."""
    import logging
    with caplog.at_level(logging.WARNING, logger="analysis.macro"):
        _series_staleness({"date": "2026-05-01"}, "CFNAI", "us")
    assert any("falling back to the day-count" in r.message for r in caplog.records)


def test_series_staleness_fallback_does_not_warn_for_chngdprapsmei(db_engine, caplog):
    """CHNGDPRAPSMEI always and deliberately uses the fallback — warning on
    every call would just be log noise for expected, permanent behaviour."""
    import logging
    with caplog.at_level(logging.WARNING, logger="analysis.macro"):
        _series_staleness({"date": "2025-01-01"}, "CHNGDPRAPSMEI", "asia")
    assert not any("falling back to the day-count" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# analysis/backtest.py:_macro_scores() — the vectorized backtest mirror of
# the same regional weighting. The backtest engine does NOT read
# compute_macro()'s stored indicators-table score at all (it re-implements
# every scoring module with rolling/ewm to stay look-ahead free — see
# CLAUDE.md's "Backtesting engine" section) — _macro_scores() is a fully
# independent vectorized re-implementation that must mirror the live
# regional weighting itself, the same way _ma_cross_vote() and
# _mean_rev_scores() already mirror their own live counterparts.
#
# Reverted 2026-07-08 to latest-observation-only (see CLAUDE.md's "Macro
# module regime-weight rebalance (2026-07-08)" section): each series'
# single most recent value is broadcast as a CONSTANT across the whole
# backtest date range, matching what analysis/macro.py:compute_macro() does
# live — rather than a time-varying per-date history. Feeding the backtest
# genuine historical macro variation (the natural next step once
# backfill_macro_indicators() existed) produced materially worse results
# than the documented baseline under every TRENDING_BULL macro weight
# tried, so this was rolled back pending a full weight re-tuning study.
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
    the same moderated +0.40, not a flat average. Every date in macro_df is
    identical here, so this doesn't yet distinguish latest-observation-only
    from time-varying — see the dedicated test below for that."""
    df = _flat_df(10)
    macro_df = pd.DataFrame(
        {"BT_US": [0] * 10, "BT_EU": [1] * 10, "BT_ASIA": [1] * 10},
        index=df.index,
    )
    scores = _macro_scores(df, "SOME_TICKER", vix_s=None, macro_df=macro_df, config=_BT_CFG)
    # weighted = 0*0.60 + 1*0.25 + 1*0.15 = 0.40 -> below the 0.25 threshold? No: 0.40 >= 0.25 -> bullish (+1)
    assert (scores == 1).all()


def test_backtest_macro_scores_uses_latest_observation_only_not_time_varying():
    """Reverted behaviour (2026-07-08): each series' single most recent
    value is broadcast as a CONSTANT across the whole backtest date range —
    not read as a time-varying history. A region whose data only starts
    partway through macro_df must still score using its one latest value
    for *every* date, including dates before that value's own timestamp,
    exactly matching how the live compute_macro() only ever reads the
    latest stored row regardless of which date is being analysed."""
    df = _flat_df(6)
    macro_df = pd.DataFrame(
        {
            "BT_US": [np.nan, np.nan, 1, 1, 1, -1],  # latest (last row) value is -1
            "BT_EU": [-1] * 6,                        # latest value is -1
        },
        index=df.index,
    )
    scores = _macro_scores(df, "SOME_TICKER", vix_s=None, macro_df=macro_df, config=_BT_CFG)
    # Latest US value -1, latest Europe value -1 -> every date scores the
    # same constant weighted blend, including day 0 (which the OLD
    # time-varying behaviour would have scored differently, since BT_US had
    # no data yet on day 0).
    assert (scores == -1).all()
    assert scores.iloc[0] == scores.iloc[-1]


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


# ---------------------------------------------------------------------------
# FRED historical backfill (2026-07-08) — backfill_macro_indicators() and
# fetch_and_store_macro_indicators()'s incremental behaviour.
#
# fredapi.Fred is mocked throughout (no network calls — see CLAUDE.md's
# testing conventions): backfill_macro_indicators()/fetch_and_store_macro_
# indicators() both do `from fredapi import Fred` *inside* the function, so
# patching the `Fred` class on the `fredapi` module itself (rather than a
# module-level name on analysis.macro, which doesn't exist) is what actually
# takes effect at call time.
# ---------------------------------------------------------------------------

def _fake_fred(dates, values) -> MagicMock:
    """A fredapi.Fred stand-in whose get_series() always returns the same
    fixed series regardless of the observation_start/observation_end it's
    called with — sufficient for testing this module's own storage/upsert
    logic, which doesn't depend on FRED performing the date filtering itself."""
    series = pd.Series(list(values), index=pd.DatetimeIndex(dates))
    fake = MagicMock()
    fake.get_series.return_value = series
    return fake


def _row_count(engine, series_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM macro_indicators WHERE series_id = :sid"),
            {"sid": series_id},
        ).scalar()


_BACKFILL_CFG = {
    "macro": {
        "fred_api_key": "test-key",
        "fred_series": {"DFII10": {"region": "us"}},
    },
}


def test_backfill_fetches_from_configured_start_date(db_engine, mocker):
    """backfill_macro_indicators() must call FRED with observation_start set
    to the requested start_date, and store one row per returned observation."""
    dates = pd.date_range("2019-01-01", periods=200, freq="B")
    fake = _fake_fred(dates, np.linspace(1.0, 2.0, len(dates)))
    mocker.patch("fredapi.Fred", return_value=fake)

    results = backfill_macro_indicators(_BACKFILL_CFG, start_date="2019-01-01")

    assert results == {"DFII10": len(dates)}
    fake.get_series.assert_called_once()
    _, kwargs = fake.get_series.call_args
    assert kwargs["observation_start"] == "2019-01-01"
    assert _row_count(db_engine, "DFII10") == len(dates)


def test_backfill_upsert_does_not_duplicate_rows_when_run_twice(db_engine, mocker):
    """Running the backfill twice against identical FRED data must leave the
    same row count — on_conflict_do_update, not a duplicate insert."""
    dates = pd.date_range("2019-01-01", periods=50, freq="B")
    fake = _fake_fred(dates, np.linspace(1.0, 2.0, len(dates)))
    mocker.patch("fredapi.Fred", return_value=fake)

    backfill_macro_indicators(_BACKFILL_CFG, start_date="2019-01-01")
    backfill_macro_indicators(_BACKFILL_CFG, start_date="2019-01-01")

    assert _row_count(db_engine, "DFII10") == len(dates)


def test_backfill_updates_values_on_second_run_not_just_leaves_rows_unchanged(db_engine, mocker):
    """The upsert must genuinely update stored values on conflict, not just
    silently no-op on an existing (series_id, date) row."""
    dates = pd.date_range("2019-01-01", periods=10, freq="B")
    fake = _fake_fred(dates, [1.0] * len(dates))
    mocker.patch("fredapi.Fred", return_value=fake)
    backfill_macro_indicators(_BACKFILL_CFG, start_date="2019-01-01")

    fake.get_series.return_value = pd.Series([9.0] * len(dates), index=pd.DatetimeIndex(dates))
    backfill_macro_indicators(_BACKFILL_CFG, start_date="2019-01-01")

    with db_engine.connect() as conn:
        value = conn.execute(
            text("SELECT value FROM macro_indicators WHERE series_id='DFII10' ORDER BY date LIMIT 1")
        ).scalar()
    assert value == 9.0


def test_backfill_one_series_failure_does_not_abort_remaining_series(db_engine, mocker):
    """A series whose FRED fetch raises must be logged and skipped, not
    prevent the rest of the configured series from backfilling."""
    dates = pd.date_range("2019-01-01", periods=30, freq="B")
    good_series = pd.Series(np.linspace(1.0, 2.0, len(dates)), index=dates)

    fake = MagicMock()

    def _get_series(series_id, **kwargs):
        if series_id == "BROKEN_SERIES":
            raise RuntimeError("simulated FRED API failure")
        return good_series

    fake.get_series.side_effect = _get_series
    mocker.patch("fredapi.Fred", return_value=fake)

    cfg = {
        "macro": {
            "fred_api_key": "test-key",
            "fred_series": {
                "BROKEN_SERIES": {"region": "us"},
                "DFII10": {"region": "us"},
            },
        },
    }
    results = backfill_macro_indicators(cfg, start_date="2019-01-01")

    assert results["BROKEN_SERIES"] == 0
    assert results["DFII10"] == len(dates)
    assert _row_count(db_engine, "BROKEN_SERIES") == 0
    assert _row_count(db_engine, "DFII10") == len(dates)


def test_backfill_insufficient_data_scores_zero_rows_not_a_crash(db_engine, mocker):
    """Fewer than 5 observations (matching the existing insufficient-data
    guard) must be reported as 0 rows, not raise."""
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    fake = _fake_fred(dates, [1.0, 1.1, 1.2])
    mocker.patch("fredapi.Fred", return_value=fake)

    results = backfill_macro_indicators(_BACKFILL_CFG, start_date="2026-01-01")
    assert results == {"DFII10": 0}


def test_incremental_fetch_first_time_with_no_prior_data_writes_whole_window(db_engine, mocker):
    """A series with nothing stored yet (fresh install, or newly added to
    config.yaml, no backfill run) falls back to writing every row in the
    fetched window — same as backfill's own behaviour."""
    dates = pd.date_range("2024-01-01", "2024-03-01", freq="D")
    fake = _fake_fred(dates, np.linspace(1.0, 2.0, len(dates)))
    mocker.patch("fredapi.Fred", return_value=fake)

    results = fetch_and_store_macro_indicators(_BACKFILL_CFG)

    assert results == {"DFII10": True}
    assert _row_count(db_engine, "DFII10") == len(dates)


def test_incremental_fetch_only_writes_since_last_stored_date_minus_buffer(db_engine, mocker):
    """With an existing stored row, the incremental fetch must only write
    observations from (last_stored_date - 7 days) onward — not silently
    rewrite the whole multi-year fetched window on every EOD run."""
    with Session(db_engine) as session:
        session.add(MacroIndicator(
            series_id="DFII10", date=datetime(2026, 6, 1),
            value=1.0, pct_change_30d=0.0, z_score_2y=0.0, score=0,
        ))
        session.commit()

    dates = pd.date_range("2024-01-01", "2026-07-01", freq="D")
    fake = _fake_fred(dates, np.linspace(1.0, 2.0, len(dates)))
    mocker.patch("fredapi.Fred", return_value=fake)

    fetch_and_store_macro_indicators(_BACKFILL_CFG)

    with db_engine.connect() as conn:
        min_date = conn.execute(
            text("SELECT MIN(date) FROM macro_indicators WHERE series_id='DFII10'")
        ).scalar()
        n_rows = _row_count(db_engine, "DFII10")

    # Cutoff = 2026-06-01 - 7 days = 2026-05-25; nothing before that should
    # have been (re)written, and the total row count must be far below the
    # full ~900-day fetched window (a handful of weeks, not years).
    assert pd.Timestamp(min_date) >= pd.Timestamp("2026-05-25")
    assert n_rows < 60


def test_incremental_fetch_no_new_observations_still_reports_success(db_engine, mocker):
    """A series whose fetched window has nothing on/after the cutoff (e.g. a
    quarterly series between reporting dates, already stored ahead of
    whatever the fetch happens to return) must still report True — the
    fetch itself succeeded, there was simply nothing new to write."""
    with Session(db_engine) as session:
        session.add(MacroIndicator(
            series_id="DFII10", date=datetime(2026, 7, 10),
            value=1.0, pct_change_30d=0.0, z_score_2y=0.0, score=0,
        ))
        session.commit()

    # Cutoff = 2026-07-10 - 7 days = 2026-07-03; fetched window ends 2026-07-01,
    # entirely before the cutoff -> nothing new to write.
    dates = pd.date_range("2024-01-01", "2026-07-01", freq="D")
    fake = _fake_fred(dates, np.linspace(1.0, 2.0, len(dates)))
    mocker.patch("fredapi.Fred", return_value=fake)

    results = fetch_and_store_macro_indicators(_BACKFILL_CFG)

    assert results == {"DFII10": True}
    assert _row_count(db_engine, "DFII10") == 1  # only the pre-seeded row


def test_backfilled_history_is_look_ahead_free_rolling_zscore():
    """z_score_2y for an early date must not be influenced by values that
    only exist later in the fetched window — i.e. it's a trailing rolling
    statistic, not the whole-window mean/std applied uniformly."""
    from analysis.macro import _fetch_scored_series

    dates = pd.date_range("2019-01-01", periods=800, freq="B")
    # Flat at 1.0 for the first ~300 obs, then a large permanent step up —
    # a whole-window mean/std would pull *every* early z-score toward
    # reflecting the future jump; a trailing window must not.
    values = [1.0] * 300 + [100.0] * (len(dates) - 300)
    fake = _fake_fred(dates, values)

    scored = _fetch_scored_series(
        fake, "DFII10", {"region": "us"},
        observation_start="2019-01-01", observation_end="2021-12-31",
    )
    early_z = scored["z_score_2y"].iloc[5]
    assert abs(early_z) < 1e-6  # flat, unchanging history so far -> ~0, not skewed by the future jump
