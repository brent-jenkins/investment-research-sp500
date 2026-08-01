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
    clone. The first window's end date is the last row where the ticker
    was still confirmed present (2015-06-10), not the earlier row that
    opened the window -- matching how the real, densely-updated source
    dataset behaves (verified there against AMD/Infineon's real removal
    dates). The second window is still open as of the source's own last
    row (2020-01-10) -- it must close at _SENTINEL_END, not at that same
    row's own date; see test_reconstruct_windows_still_active_at_source_end
    for the dedicated regression covering why (2026-07-31 bug: closing an
    in-progress window at the source's last-row date instead of the
    sentinel falsely marks every still-active ticker as removed the
    moment "today" passes that date)."""
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"NEWCO": []})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value=_SYNTHETIC_CSV)

    summary = v2u.refresh_membership_from_source()

    assert summary["errors"] == []
    changed_tickers = [c["ticker"] for c in summary["changed"]]
    assert "NEWCO" in changed_tickers

    windows = v2u.get_membership_windows("NEWCO")
    assert windows == [(date(2007, 1, 2), date(2015, 6, 10)), (date(2020, 1, 10), v2u._SENTINEL_END)]

    # Point-in-time eligibility must reflect the gap correctly.
    assert v2u.is_member("NEWCO", date(2010, 1, 1))
    assert not v2u.is_member("NEWCO", date(2017, 1, 1))
    assert v2u.is_member("NEWCO", date(2020, 1, 10))
    # Still a member long after the source's last confirming row -- the
    # exact real-world shape of the 2026-07-31 bug.
    assert v2u.is_member("NEWCO", date(2026, 7, 31))
    assert v2u.is_member("NEWCO", date(2099, 1, 1))


def test_reconstruct_windows_still_active_at_source_end(db_engine, mocker):
    """Dedicated regression for the 2026-07-31 incident: a ticker present
    in the source's most recent (final) row must resolve as a still-open
    membership window (closing at _SENTINEL_END), not one that silently
    closes at that final row's own date. Closing at the row's own date is
    exactly the bug that misclassified all 39 real stock candidates as
    "removed" the moment the evaluation date passed the source's last
    update (2026-06-30 at the time), producing 6 real false INDEX_REMOVAL
    exits (BAC, AMD, AMZN, AAPL, AVGO, CAT) against genuinely-still-active
    S&P 500 constituents."""
    csv_text = (
        "date,tickers\n"
        '2020-01-01,"STILLIN,ZZZ"\n'
        '2026-06-30,"STILLIN,ZZZ"\n'  # source's last available row
    )
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"STILLIN": []})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value=csv_text)

    v2u.refresh_membership_from_source()
    windows = v2u.get_membership_windows("STILLIN")
    assert windows == [(date(2020, 1, 1), v2u._SENTINEL_END)]

    # The core regression: eligibility must still hold for any date after
    # the source's last row, not just up to it.
    assert v2u.is_member("STILLIN", date(2026, 6, 30))
    assert v2u.is_member("STILLIN", date(2026, 7, 31))
    assert v2u.is_member("STILLIN", date(2030, 1, 1))


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
    a fresh window starting the day the new symbol appears. META is still
    present in the CSV's final row, so the window stays open (closes at
    _SENTINEL_END, not that row's own date -- see
    test_reconstruct_windows_still_active_at_source_end)."""
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
    assert windows == [(date(2013, 12, 23), v2u._SENTINEL_END)]
    assert v2u.is_member("META", date(2018, 1, 1))
    assert v2u.is_member("META", date(2023, 1, 1))
    assert v2u.is_member("META", date(2026, 7, 31))


# ---------------------------------------------------------------------------
# get_max_holding_days() -- is_etf must be resolved from the universe's own
# ETF_TICKERS / STOCK_MEMBERSHIP categorization, never from whether a
# ticker happens to have a live-execution `instruments:` entry yet. Today
# ETF_TICKERS/STOCK_MEMBERSHIP and `instruments:` happen to match exactly
# (every current ticker has both), which is what let a registration-based
# dispatch look correct -- but a future universe-broadening candidate can
# exist in ETF_TICKERS/STOCK_MEMBERSHIP before it's ever wired into
# `instruments:` for live trading, and must still get its real 90/30
# treatment rather than the flat fallback.
# ---------------------------------------------------------------------------

def _holding_days_config(**t212_overrides) -> dict:
    cfg = {
        "instruments": {},  # deliberately empty -- nothing registered for live execution
        "trading212_invest": {"max_holding_days": 90, "stock_max_holding_days": 30},
        "backtest": {"fallback_max_holding_days": 7},
    }
    cfg["trading212_invest"].update(t212_overrides)
    return cfg


def test_get_max_holding_days_unregistered_etf_candidate_gets_etf_treatment(mocker):
    """A candidate in ETF_TICKERS with NO `instruments:` entry at all must
    still get the real 90-day ETF backstop, not the flat fallback -- proves
    is_etf is resolved from ETF_TICKERS, not from live-execution
    registration."""
    mocker.patch.object(v2u, "ETF_TICKERS", ("NEWETF",))
    assert v2u.get_max_holding_days("NEWETF", _holding_days_config()) == 90


def test_get_max_holding_days_unregistered_stock_candidate_gets_stock_treatment(mocker):
    """Same guarantee for a candidate in STOCK_MEMBERSHIP with no
    `instruments:` entry -- must get the 30-day stock backstop, not the
    flat fallback."""
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"NEWSTOCK": []})
    assert v2u.get_max_holding_days("NEWSTOCK", _holding_days_config()) == 30


def test_get_max_holding_days_ignores_a_stale_instruments_entry(mocker):
    """A ticker WITH an `instruments:` entry whose own is_etf flag disagrees
    with its real ETF_TICKERS categorization must still be dispatched by
    the real categorization -- `instruments:` registration is no longer
    consulted for this decision at all, so a stale/wrong flag there can't
    silently produce the wrong holding limit."""
    mocker.patch.object(v2u, "ETF_TICKERS", ("REALETF",))
    cfg = _holding_days_config()
    cfg["instruments"] = {"REALETF": {"is_etf": False, "t212_instrument_id": "X", "currency": "USD"}}
    assert v2u.get_max_holding_days("REALETF", cfg) == 90  # real ETF treatment, not the stale flag


def test_get_max_holding_days_truly_untracked_ticker_uses_fallback(mocker):
    """A ticker in neither ETF_TICKERS nor STOCK_MEMBERSHIP -- e.g. an
    ad-hoc ticker only ever exercised inside a one-off backtest research
    study -- has no real categorization to dispatch on, so it correctly
    falls back to backtest.fallback_max_holding_days."""
    mocker.patch.object(v2u, "ETF_TICKERS", ())
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {})
    assert v2u.get_max_holding_days("RESEARCH_ONLY_TICKER", _holding_days_config()) == 7


def test_get_max_holding_days_fallback_defaults_to_30_when_config_key_absent(mocker):
    """fallback_max_holding_days itself defaults to 30 when absent from
    config entirely -- matches the function's own hardcoded default."""
    mocker.patch.object(v2u, "ETF_TICKERS", ())
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {})
    assert v2u.get_max_holding_days("UNKNOWN_TICKER", {}) == 30
