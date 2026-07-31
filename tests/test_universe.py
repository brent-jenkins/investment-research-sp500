"""Tests for analysis/universe.py — point-in-time membership gating and
the ongoing refresh mechanism that keeps it from becoming a static,
decaying snapshot.
"""
from datetime import date

import pytest

from analysis import universe as v2u


@pytest.fixture(autouse=True)
def _clear_membership_cache():
    """get_membership_windows() caches per-process for performance (see
    universe.py's clear_membership_cache() docstring) -- but db_engine gives
    each test its own fresh in-memory database, so a cache entry populated
    by an earlier test must never leak into a later one."""
    v2u.clear_membership_cache()
    yield
    v2u.clear_membership_cache()


def test_etf_tickers_always_eligible():
    for t in v2u.ETF_TICKERS:
        assert v2u.is_member(t, date(2007, 1, 1))
        assert v2u.is_member(t, date(2026, 7, 30))


def test_gold_has_no_membership_window(db_engine):
    assert v2u.STOCK_MEMBERSHIP["GOLD"] == []
    # No DB seeded in this test -- falls back to the (empty) baseline.
    assert v2u.get_membership_windows("GOLD") == []


def test_amd_gap_correctly_excludes_the_gap_period(db_engine):
    v2u.seed_baseline_membership()
    # AMD: in S&P 500 until 2013-09-17, out until 2017-03-20.
    assert v2u.is_member("AMD", date(2010, 1, 1))
    assert not v2u.is_member("AMD", date(2015, 1, 1))
    assert v2u.is_member("AMD", date(2020, 1, 1))


def test_tsla_not_eligible_before_real_membership_date(db_engine):
    v2u.seed_baseline_membership()
    assert not v2u.is_member("TSLA", date(2019, 1, 1))
    assert v2u.is_member("TSLA", date(2021, 1, 1))


def test_seed_baseline_membership_is_idempotent(db_engine):
    n1 = v2u.seed_baseline_membership()
    n2 = v2u.seed_baseline_membership()
    assert n1 == n2 > 0
    # Re-seeding must not duplicate windows.
    windows = v2u.get_membership_windows("AAPL")
    assert len(windows) == 1


def test_get_membership_windows_falls_back_to_baseline_without_db_seed(db_engine):
    # No seed_baseline_membership() call -- table is empty.
    windows = v2u.get_membership_windows("SAP")  # not a real V2 ticker; not in STOCK_MEMBERSHIP either
    assert windows == []
    windows = v2u.get_membership_windows("AAPL")
    assert windows == [(date(1996, 1, 2), date(2026, 12, 31))]


_SYNTHETIC_CSV = (
    "date,tickers\n"
    '2007-01-02,"AAPL,NEWCO,ZZZ"\n'
    '2015-06-10,"AAPL,NEWCO,ZZZ"\n'
    '2015-06-15,"AAPL,ZZZ"\n'
    '2020-01-10,"AAPL,NEWCO,ZZZ"\n'
)


def test_refresh_detects_removal_and_readdition(db_engine, mocker):
    """A ticker present at the start, absent from a later row, and present
    again in the latest row must reconstruct as two windows -- the same
    multi-window shape already verified for AMD/Beiersdorf in the research
    clone. The window's end date is the last row where the ticker was
    still confirmed present (2015-06-10), not the earlier row that opened
    the window -- matching how the real, densely-updated source dataset
    behaves (verified there against AMD/Infineon's real removal dates)."""
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"NEWCO": []})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value=_SYNTHETIC_CSV)

    summary = v2u.refresh_membership_from_source()

    assert summary["errors"] == []
    changed_tickers = [c["ticker"] for c in summary["changed"]]
    assert "NEWCO" in changed_tickers

    windows = v2u.get_membership_windows("NEWCO")
    assert windows == [(date(2007, 1, 2), date(2015, 6, 10)), (date(2020, 1, 10), date(2020, 1, 10))]

    # Point-in-time eligibility must reflect the gap correctly.
    assert v2u.is_member("NEWCO", date(2010, 1, 1))
    assert not v2u.is_member("NEWCO", date(2017, 1, 1))
    assert v2u.is_member("NEWCO", date(2020, 1, 10))


def test_refresh_is_a_noop_when_nothing_changed(db_engine, mocker):
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"AAPL": [("2007-01-02", "2020-01-10")]})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value=_SYNTHETIC_CSV)

    v2u.refresh_membership_from_source()
    summary2 = v2u.refresh_membership_from_source()
    assert summary2["changed"] == []


def test_refresh_handles_source_fetch_failure_without_raising(db_engine, mocker):
    mocker.patch.object(v2u, "_fetch_source_csv", side_effect=RuntimeError("network down"))
    summary = v2u.refresh_membership_from_source()
    assert summary["errors"]
    assert summary["changed"] == []


def test_get_membership_windows_is_cached_across_calls(db_engine, mocker):
    """A second call for the same ticker must not re-hit the DB -- this is
    what makes an 18-run backtest over ~5,000 days/ticker viable at all."""
    v2u.seed_baseline_membership()
    v2u.get_membership_windows("AAPL")  # populate cache

    real_connect = v2u.get_engine().connect
    spy = mocker.patch.object(v2u, "get_engine")
    windows = v2u.get_membership_windows("AAPL")
    spy.assert_not_called()
    assert windows == [(date(1996, 1, 2), date(2026, 12, 31))]


def test_refresh_invalidates_the_cache(db_engine, mocker):
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"NEWCO": []})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value=_SYNTHETIC_CSV)

    v2u.get_membership_windows("NEWCO")  # cache the pre-refresh (empty) state
    assert v2u.get_membership_windows("NEWCO") == []

    v2u.refresh_membership_from_source()
    # Must reflect the newly-written windows, not the stale empty cache entry.
    assert v2u.get_membership_windows("NEWCO") != []


def test_refresh_merges_known_ticker_rename(db_engine, mocker):
    """FB -> META must reconstruct as one continuous window, not a gap +
    a fresh window starting the day the new symbol appears."""
    csv = (
        "date,tickers\n"
        '2013-12-23,"AAPL,FB"\n'
        '2022-06-09,"AAPL,META"\n'
        '2026-01-05,"AAPL,META"\n'
    )
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"META": []})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value=csv)

    v2u.refresh_membership_from_source()
    windows = v2u.get_membership_windows("META")
    assert windows == [(date(2013, 12, 23), date(2026, 1, 5))]
    assert v2u.is_member("META", date(2018, 1, 1))
    assert v2u.is_member("META", date(2023, 1, 1))
