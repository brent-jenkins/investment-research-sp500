"""Tests for analysis/universe.py — point-in-time membership gating and
the ongoing refresh mechanism that keeps it from becoming a static,
decaying snapshot.
"""
from datetime import date, datetime, timedelta, timezone

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


# ---------------------------------------------------------------------------
# get_all_known_stock_tickers() / get_live_stock_candidates() (2026-08-02
# refactor -- the raw "every discovered ticker" set was extracted out of
# get_live_stock_candidates() into its own function so
# analysis.validation.live_universe_snapshot() can report from the exact
# same underlying set, not a second, independently re-derived copy).
# ---------------------------------------------------------------------------

def test_get_all_known_stock_tickers_excludes_etfs(db_engine):
    v2u.seed_baseline_membership()
    known = v2u.get_all_known_stock_tickers()
    assert not (known & set(v2u.ETF_TICKERS))
    assert "GOLD" in known  # still a known candidate despite its empty window
    assert set(v2u.STOCK_MEMBERSHIP.keys()) <= known


def test_get_live_stock_candidates_is_all_known_filtered_by_membership(db_engine):
    """get_live_stock_candidates() must be exactly get_all_known_stock_
    tickers() filtered through is_member() for the given date -- the
    refactor's whole point was to make this an explicit filter over a
    shared set, not two independent computations that could drift."""
    v2u.seed_baseline_membership()
    as_of = date(2026, 7, 30)
    known = v2u.get_all_known_stock_tickers()
    expected = sorted(t for t in known if v2u.is_member(t, as_of))
    assert v2u.get_live_stock_candidates(as_of=as_of) == expected
    assert "GOLD" not in v2u.get_live_stock_candidates(as_of=as_of)  # empty window -> never eligible
    assert not (set(v2u.get_live_stock_candidates(as_of=as_of)) & set(v2u.ETF_TICKERS))


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
# ETF_TICKERS categorization, never from whether a ticker happens to have a
# live-execution `instruments:` entry yet. Today ETF_TICKERS and
# `instruments:` happen to match exactly (every current ticker has both),
# which is what let a registration-based dispatch look correct -- but a
# future universe-broadening candidate can exist in ETF_TICKERS before it's
# ever wired into `instruments:` for live trading, and must still get its
# real 90-day treatment rather than the flat stock default.
#
# Consolidated 2026-08-02: STOCK_MEMBERSHIP membership no longer affects
# this function's dispatch at all -- every non-ETF ticker (curated or not)
# now gets the single stock_max_holding_days value. See
# get_max_holding_days()'s own docstring for why the prior three-way split
# (ETF / curated stock / untracked-ticker fallback) was collapsed: real
# rolling-universe research found 71% of a real research universe's
# tickers hit what used to be the "fallback" case as their everyday path,
# not an edge case, and the two values were only coincidentally equal.
# ---------------------------------------------------------------------------

def _holding_days_config(**t212_overrides) -> dict:
    cfg = {
        "instruments": {},  # deliberately empty -- nothing registered for live execution
        "trading212_invest": {"max_holding_days": 90, "stock_max_holding_days": 30},
    }
    cfg["trading212_invest"].update(t212_overrides)
    return cfg


def test_get_max_holding_days_unregistered_etf_candidate_gets_etf_treatment(mocker):
    """A candidate in ETF_TICKERS with NO `instruments:` entry at all must
    still get the real 90-day ETF backstop, not the flat stock default --
    proves is_etf is resolved from ETF_TICKERS, not from live-execution
    registration."""
    mocker.patch.object(v2u, "ETF_TICKERS", ("NEWETF",))
    assert v2u.get_max_holding_days("NEWETF", _holding_days_config()) == 90


def test_get_max_holding_days_unregistered_stock_candidate_gets_stock_treatment(mocker):
    """Same guarantee for a candidate in STOCK_MEMBERSHIP with no
    `instruments:` entry -- must get the 30-day stock backstop."""
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


def test_get_max_holding_days_untracked_ticker_gets_the_same_stock_treatment_as_any_non_etf(mocker):
    """A ticker in neither ETF_TICKERS nor STOCK_MEMBERSHIP -- e.g. a
    market-cap-ranked rolling-universe research candidate, or an ad-hoc
    ticker only ever exercised inside a one-off backtest study -- is no
    longer a distinct case: post-consolidation it gets the exact same
    stock_max_holding_days value any other non-ETF ticker would, proving
    there is no separate "untracked ticker" fallback left to configure
    independently."""
    mocker.patch.object(v2u, "ETF_TICKERS", ())
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {})
    cfg = _holding_days_config(stock_max_holding_days=45)
    assert v2u.get_max_holding_days("RESEARCH_ONLY_TICKER", cfg) == 45
    # And a real STOCK_MEMBERSHIP ticker gets the identical value from the
    # identical config key -- proving both cases now share one dispatch.
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"CURATED": []})
    assert v2u.get_max_holding_days("CURATED", cfg) == 45


def test_get_max_holding_days_stock_default_is_30_when_config_key_absent(mocker):
    """stock_max_holding_days defaults to 30 when absent from config
    entirely -- matches the function's own hardcoded default, and applies
    uniformly to any non-ETF ticker, tracked or not."""
    mocker.patch.object(v2u, "ETF_TICKERS", ())
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {})
    assert v2u.get_max_holding_days("UNKNOWN_TICKER", {}) == 30


# ---------------------------------------------------------------------------
# Membership refresh staleness tracking (2026-08-02) -- fja05680/sp500 is
# now the sole live eligibility gate and previously had no staleness
# timestamp at all: a fetch failure just logged a WARNING and returned
# silently. Mirrors main.py:_eod_overdue()'s exact weekday/Monday-exemption
# shape -- see is_membership_refresh_stale()'s own docstring for why.
# 2026-08-03 (Monday), 2026-08-04 (Tuesday), 2026-08-01 (Saturday) below are
# real, verified weekdays for the dates used, not assumed.
# ---------------------------------------------------------------------------

def test_refresh_from_source_stamps_success_timestamp(db_engine, mocker):
    assert v2u.get_last_membership_refresh_success() is None  # nothing recorded yet

    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value="date,tickers\n2020-01-01,\"ZZZ\"\n")

    before = datetime.utcnow()
    v2u.refresh_membership_from_source()
    after = datetime.utcnow()

    stamped = v2u.get_last_membership_refresh_success()
    assert stamped is not None
    assert before - timedelta(seconds=2) <= stamped <= after + timedelta(seconds=2)


def test_refresh_from_source_does_not_stamp_on_fetch_failure(db_engine, mocker):
    mocker.patch.object(v2u, "_fetch_source_csv", side_effect=RuntimeError("network down"))
    v2u.refresh_membership_from_source()
    assert v2u.get_last_membership_refresh_success() is None


def test_refresh_from_source_stamps_even_with_per_ticker_reconstruction_errors(db_engine, mocker):
    """A malformed row causing one ticker's reconstruction to fail is a
    different, already-visible problem (summary['errors']) from the source
    itself being unreachable -- it must not suppress the success stamp,
    since the mechanism genuinely did talk to the source successfully."""
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"AAPL": []})
    mocker.patch.object(v2u, "_fetch_source_csv", return_value="date,tickers\n2020-01-01,\"AAPL\"\n")
    mocker.patch.object(v2u, "_reconstruct_windows", side_effect=RuntimeError("malformed row"))

    summary = v2u.refresh_membership_from_source()

    assert summary["errors"]
    assert v2u.get_last_membership_refresh_success() is not None


def test_is_membership_refresh_stale_false_when_fresh():
    now = datetime(2026, 8, 4, 12, 0)  # Tuesday
    last_success = now - timedelta(hours=1)
    assert not v2u.is_membership_refresh_stale(last_success, now=now)


def test_is_membership_refresh_stale_true_past_threshold_on_weekday():
    now = datetime(2026, 8, 4, 12, 0)  # Tuesday
    last_success = now - timedelta(hours=49)
    assert v2u.is_membership_refresh_stale(last_success, now=now)


def test_is_membership_refresh_stale_false_just_under_threshold():
    now = datetime(2026, 8, 4, 12, 0)  # Tuesday
    last_success = now - timedelta(hours=47)
    assert not v2u.is_membership_refresh_stale(last_success, now=now)


def test_is_membership_refresh_stale_none_last_success_is_infinitely_stale():
    now = datetime(2026, 8, 4, 12, 0)  # Tuesday
    assert v2u.is_membership_refresh_stale(None, now=now)


def test_is_membership_refresh_stale_monday_before_job_time_is_exempted():
    """Friday 21:15 -> Monday 07:00 is ~57.75h old (> 48h) purely from the
    weekend gap, with nothing actually wrong -- must not be flagged before
    Monday's own 21:15 UTC run has had a chance to succeed."""
    last_success = datetime(2026, 7, 31, 21, 15)  # Friday
    now = datetime(2026, 8, 3, 7, 0)  # Monday, before 21:15
    assert not v2u.is_membership_refresh_stale(last_success, now=now)


def test_is_membership_refresh_stale_monday_after_job_time_is_not_exempted():
    """Once Monday's own 21:15 UTC run time has passed without a fresh
    success, the exemption no longer applies -- a genuinely missed Monday
    cycle must surface, not be silently forgiven all day."""
    last_success = datetime(2026, 7, 31, 21, 15)  # Friday
    now = datetime(2026, 8, 3, 22, 0)  # Monday, after 21:15
    assert v2u.is_membership_refresh_stale(last_success, now=now)


def test_is_membership_refresh_stale_weekend_never_flagged():
    last_success = datetime(2026, 7, 27, 12, 0)  # the prior Monday -- way over 48h old
    now = datetime(2026, 8, 1, 12, 0)  # Saturday
    assert not v2u.is_membership_refresh_stale(last_success, now=now)


def test_is_membership_refresh_stale_handles_tz_aware_last_success():
    now = datetime(2026, 8, 4, 12, 0)  # Tuesday
    last_success = (now - timedelta(hours=1)).replace(tzinfo=timezone.utc)
    assert not v2u.is_membership_refresh_stale(last_success, now=now)


# ---------------------------------------------------------------------------
# Wikipedia lag check (2026-08-02) -- a lag detector against the data's own
# upstream source, NOT independent verification (fja05680/sp500's own
# maintainer updates by diffing against this exact Wikipedia page). Must
# flag disagreement, never raise, never gate eligibility.
# ---------------------------------------------------------------------------

def test_check_wikipedia_lag_flags_genuine_disagreement(db_engine, mocker):
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"AAPL": [], "MISSING": []})
    mocker.patch.object(v2u, "_fetch_wikipedia_current_members", return_value={"AAPL"})
    mocker.patch.object(v2u, "is_member", side_effect=lambda t, d: True)  # our data: both are members

    result = v2u.check_wikipedia_lag()

    assert result["checked"] == 2
    assert result["error"] is None
    assert [d["ticker"] for d in result["disagreements"]] == ["MISSING"]
    assert result["disagreements"][0] == {"ticker": "MISSING", "our_member": True, "wikipedia_member": False}


def test_check_wikipedia_lag_no_disagreement_when_they_match(db_engine, mocker):
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"AAPL": [], "GONE": []})
    mocker.patch.object(v2u, "_fetch_wikipedia_current_members", return_value={"AAPL"})
    mocker.patch.object(v2u, "is_member", side_effect=lambda t, d: t == "AAPL")

    result = v2u.check_wikipedia_lag()

    assert result["disagreements"] == []


def test_check_wikipedia_lag_never_raises_on_fetch_failure(db_engine, mocker):
    mocker.patch.object(v2u, "_fetch_wikipedia_current_members", side_effect=RuntimeError("wikipedia unreachable"))

    result = v2u.check_wikipedia_lag()  # must not raise

    assert result["error"] is not None
    assert result["disagreements"] == []


def test_check_wikipedia_lag_note_labels_it_as_a_lag_detector_not_verification(db_engine, mocker):
    """Critical framing requirement -- must never read as independent
    verification, since agreement here proves nothing about whether
    Wikipedia's own current-members list is itself correct."""
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"AAPL": []})
    mocker.patch.object(v2u, "_fetch_wikipedia_current_members", return_value={"AAPL"})
    mocker.patch.object(v2u, "is_member", side_effect=lambda t, d: True)

    result = v2u.check_wikipedia_lag()

    note = result["note"].lower()
    assert "lag detector" in note
    assert "not independent verification" in note


def test_check_wikipedia_lag_persists_result_to_metadata(db_engine, mocker):
    from data.database import get_metadata_str
    mocker.patch.object(v2u, "STOCK_MEMBERSHIP", {"AAPL": []})
    mocker.patch.object(v2u, "_fetch_wikipedia_current_members", return_value={"AAPL"})
    mocker.patch.object(v2u, "is_member", side_effect=lambda t, d: True)

    v2u.check_wikipedia_lag()

    stored = get_metadata_str(v2u._WIKI_LAG_METADATA_KEY)
    assert stored is not None
    import json
    parsed = json.loads(stored)
    assert parsed["checked"] == 1
    assert parsed["disagreements"] == []


# ---------------------------------------------------------------------------
# get_quarantined_tickers() / get_research_stock_candidates() (2026-08-07) --
# the single admission gate for research/backtest candidate-pool
# construction, per research/recycled_ticker_investigation_2026-08-07/
# REPORT.md and its follow-on remediation. Deliberately does NOT touch
# get_all_known_stock_tickers()/get_live_stock_candidates() themselves --
# the tests below prove that directly, not just by inspection.
# ---------------------------------------------------------------------------

def _seed_quarantine_row(engine, ticker: str, status: str = "QUARANTINED") -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ticker_data_quality (ticker, status, reason, evidence, detected_at) "
                "VALUES (:t, :s, 'test', '{}', :ts)"
            ),
            {"t": ticker, "s": status, "ts": datetime.utcnow().isoformat()},
        )


def test_get_quarantined_tickers_returns_only_quarantined_status(db_engine):
    _seed_quarantine_row(db_engine, "BADCO", "QUARANTINED")
    _seed_quarantine_row(db_engine, "OKCO", "CLEAN")
    _seed_quarantine_row(db_engine, "MAYBECO", "NEEDS_REVIEW")

    quarantined = v2u.get_quarantined_tickers()

    assert quarantined == {"BADCO"}


def test_get_research_stock_candidates_excludes_quarantined_only(db_engine):
    v2u.seed_baseline_membership()
    known = v2u.get_all_known_stock_tickers()
    a_known_ticker = sorted(known)[0]
    _seed_quarantine_row(db_engine, a_known_ticker, "QUARANTINED")

    research_candidates = v2u.get_research_stock_candidates()

    assert a_known_ticker not in research_candidates
    assert research_candidates == known - {a_known_ticker}


def test_quarantine_does_not_affect_get_all_known_stock_tickers_or_live_candidates(db_engine):
    """The core safety property this whole design hinges on: quarantining
    a ticker that is (hypothetically) still is_member()-eligible today
    must NOT remove it from get_all_known_stock_tickers() or
    get_live_stock_candidates() -- neither function reads
    ticker_data_quality at all. Only get_research_stock_candidates()
    (a separate, additive function) is gated."""
    v2u.seed_baseline_membership()
    as_of = date(2026, 7, 30)
    live_before = set(v2u.get_live_stock_candidates(as_of=as_of))
    known_before = v2u.get_all_known_stock_tickers()
    assert live_before, "fixture sanity check -- must have at least one live candidate to test against"
    a_live_ticker = sorted(live_before)[0]

    _seed_quarantine_row(db_engine, a_live_ticker, "QUARANTINED")

    live_after = set(v2u.get_live_stock_candidates(as_of=as_of))
    known_after = v2u.get_all_known_stock_tickers()

    assert live_after == live_before  # byte-identical -- quarantine changed nothing here
    assert known_after == known_before
    assert a_live_ticker in live_after  # still present despite being quarantined
    # ...but it IS excluded from the research-only gate:
    assert a_live_ticker not in v2u.get_research_stock_candidates()


def test_get_quarantined_tickers_empty_table_returns_empty_set(db_engine):
    assert v2u.get_quarantined_tickers() == set()
