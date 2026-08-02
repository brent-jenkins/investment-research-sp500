"""Tests for analysis/market_cap.py — point-in-time market-cap
reconstruction from (synthetic) SEC EDGAR shares-outstanding data joined
against stored close prices. No test in this file makes a live network
call — every SEC response is mocked, matching this project's own
"no test touches a real external service" convention (see
tests/test_universe.py's own _fetch_source_csv mocking for the same
pattern against a different external source).
"""
from datetime import date, datetime

import pytest

from analysis import market_cap as mc
from data.database import MarketCapHistory, PriceData


@pytest.fixture(autouse=True)
def _clear_market_cap_caches():
    """market_cap.py caches the CIK map and per-ticker shares series
    in-process for performance (see clear_caches()'s own docstring) --
    but db_engine gives each test its own fresh in-memory database, so a
    cache entry populated by an earlier test must never leak into a later
    one."""
    mc.clear_caches()
    yield
    mc.clear_caches()


def _seed_price(engine, ticker: str, d: date, close: float) -> None:
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(PriceData(ticker=ticker, date=datetime(d.year, d.month, d.day), close=close))
        session.commit()


def _seed_shares(
    engine, ticker: str, period_end: date, shares: float, source: str = "dei:test", filed_date: date = None,
) -> None:
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(
            MarketCapHistory(
                ticker=ticker, period_end_date=period_end,
                shares_outstanding=shares, source=source,
                filed_date=filed_date,
                fetched_at=datetime.utcnow(),
            )
        )
        session.commit()


# ---------------------------------------------------------------------------
# to_sec_ticker() -- dot -> dash conversion
# ---------------------------------------------------------------------------

def test_to_sec_ticker_converts_dot_to_dash():
    assert mc.to_sec_ticker("BRK.B") == "BRK-B"
    assert mc.to_sec_ticker("BF.B") == "BF-B"
    assert mc.to_sec_ticker("aapl") == "AAPL"  # also uppercases


# ---------------------------------------------------------------------------
# fetch_cik_map() -- CIK mapping, cached
# ---------------------------------------------------------------------------

def test_fetch_cik_map_parses_and_zero_pads(mocker):
    fake_json = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    }
    resp = mocker.Mock()
    resp.json.return_value = fake_json
    resp.raise_for_status.return_value = None
    mocker.patch.object(mc.requests, "get", return_value=resp)

    out = mc.fetch_cik_map()
    assert out["AAPL"] == "0000320193"
    assert out["NVDA"] == "0001045810"


def test_fetch_cik_map_is_cached_across_calls(mocker):
    fake_json = {"0": {"cik_str": 1, "ticker": "X", "title": "X"}}
    resp = mocker.Mock()
    resp.json.return_value = fake_json
    resp.raise_for_status.return_value = None
    spy = mocker.patch.object(mc.requests, "get", return_value=resp)

    mc.fetch_cik_map()
    mc.fetch_cik_map()
    assert spy.call_count == 1  # second call served from cache


def test_fetch_cik_map_force_refetches(mocker):
    fake_json = {"0": {"cik_str": 1, "ticker": "X", "title": "X"}}
    resp = mocker.Mock()
    resp.json.return_value = fake_json
    resp.raise_for_status.return_value = None
    spy = mocker.patch.object(mc.requests, "get", return_value=resp)

    mc.fetch_cik_map()
    mc.fetch_cik_map(force=True)
    assert spy.call_count == 2


# ---------------------------------------------------------------------------
# fetch_shares_outstanding() -- tag fallback + earliest-filed dedup
# ---------------------------------------------------------------------------

def test_fetch_shares_outstanding_uses_first_tag_with_data(mocker):
    """dei tag has data -- must not even try the us-gaap fallbacks."""
    dei_rows = [{"end": "2020-01-01", "val": 1000, "filed": "2020-01-15"}]

    def fake_fetch(cik, taxonomy, tag):
        if (taxonomy, tag) == ("dei", "EntityCommonStockSharesOutstanding"):
            return dei_rows
        raise AssertionError("should not reach fallback tags when dei has data")

    mocker.patch.object(mc, "_fetch_concept", side_effect=fake_fetch)
    rows, tag_used = mc.fetch_shares_outstanding("0000320193")
    assert tag_used == "dei:EntityCommonStockSharesOutstanding"
    assert rows == [{"end": "2020-01-01", "val": 1000, "filed": "2020-01-15"}]


def test_fetch_shares_outstanding_falls_back_when_dei_missing(mocker):
    """Mirrors the real, live-verified GOOGL case: the dei tag 404s
    (returns None/empty), the us-gaap fallback has real data."""
    gaap_rows = [{"end": "2021-12-31", "val": 662121000, "filed": "2022-02-02"}]

    def fake_fetch(cik, taxonomy, tag):
        if taxonomy == "dei":
            return None
        if (taxonomy, tag) == ("us-gaap", "CommonStockSharesOutstanding"):
            return gaap_rows
        return None

    mocker.patch.object(mc, "_fetch_concept", side_effect=fake_fetch)
    rows, tag_used = mc.fetch_shares_outstanding("0001652044")
    assert tag_used == "us-gaap:CommonStockSharesOutstanding"
    assert rows == gaap_rows


def test_fetch_concept_prefixes_cik_with_CIK(mocker):
    """Regression for a real bug caught live against SEC 2026-08-01: the
    company_tickers.json CIK map stores the bare zero-padded number
    (fetch_cik_map()'s own documented convention), but SEC's
    companyconcept endpoint 404s unless the CIK in the URL is prefixed
    with the literal "CIK" (e.g. "CIK0001045810", not "0001045810") --
    confirmed directly against a real NVDA request before this fix."""
    resp = mocker.Mock()
    resp.status_code = 200
    resp.json.return_value = {"units": {"shares": []}}
    spy = mocker.patch.object(mc.requests, "get", return_value=resp)

    mc._fetch_concept("0001045810", "dei", "EntityCommonStockSharesOutstanding")

    called_url = spy.call_args[0][0]
    assert "CIK0001045810" in called_url
    assert "/companyconcept/0001045810/" not in called_url


def test_fetch_shares_outstanding_returns_empty_when_no_tag_resolves(mocker):
    # _fetch_company_facts is mocked explicitly (not left to fall through to a
    # real SEC request) so this test stays self-contained -- the suite must
    # never make a live network call, per CLAUDE.md's "Running tests" contract.
    mocker.patch.object(mc, "_fetch_concept", return_value=None)
    mocker.patch.object(mc, "_fetch_company_facts", return_value=None)
    rows, tag_used = mc.fetch_shares_outstanding("0000000001")
    assert rows == []
    assert tag_used is None


def test_fetch_shares_outstanding_keeps_earliest_filed_value_on_restatement(mocker):
    """The real, live-verified GOOGL data-quality issue: a later filing
    can restate an earlier period_end's share count (e.g. applying a
    stock split retroactively for comparability). The earliest-filed
    value -- what was true and knowable as of that period_end date -- must
    win, never the later restated one."""
    raw = [
        {"end": "2021-12-31", "val": 662121000, "filed": "2022-02-02"},   # genuine, pre-split
        {"end": "2021-12-31", "val": 13242000000, "filed": "2022-10-26"},  # later restatement (post-split)
        {"end": "2022-06-30", "val": 13078000000, "filed": "2022-07-27"},
    ]

    def fake_fetch(cik, taxonomy, tag):
        return raw if (taxonomy, tag) == ("dei", "EntityCommonStockSharesOutstanding") else None

    mocker.patch.object(mc, "_fetch_concept", side_effect=fake_fetch)
    rows, _ = mc.fetch_shares_outstanding("0001652044")

    by_end = {r["end"]: r["val"] for r in rows}
    assert by_end["2021-12-31"] == 662121000  # earliest-filed wins, not the 13.2B restatement
    assert by_end["2022-06-30"] == 13078000000


def test_fetch_shares_outstanding_skips_rows_missing_end_or_val(mocker):
    raw = [
        {"end": "2020-01-01", "val": 1000, "filed": "2020-01-15"},
        {"end": None, "val": 500, "filed": "2020-02-01"},
        {"end": "2020-04-01", "val": None, "filed": "2020-04-15"},
    ]

    def fake_fetch(cik, taxonomy, tag):
        return raw if taxonomy == "dei" else None

    mocker.patch.object(mc, "_fetch_concept", side_effect=fake_fetch)
    rows, _ = mc.fetch_shares_outstanding("x")
    assert len(rows) == 1
    assert rows[0]["end"] == "2020-01-01"


def test_fetch_shares_outstanding_isolates_one_tag_failure(mocker):
    """An exception fetching one tag must not prevent a later tag in the
    fallback list from being tried -- matches this codebase's
    "never raises" bulk-fetch convention."""
    def fake_fetch(cik, taxonomy, tag):
        if taxonomy == "dei":
            raise RuntimeError("SEC rate limited")
        if (taxonomy, tag) == ("us-gaap", "CommonStockSharesOutstanding"):
            return [{"end": "2020-01-01", "val": 1000, "filed": "2020-01-15"}]
        return None

    mocker.patch.object(mc, "_fetch_concept", side_effect=fake_fetch)
    rows, tag_used = mc.fetch_shares_outstanding("x")
    assert tag_used == "us-gaap:CommonStockSharesOutstanding"
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# backfill_shares_outstanding() -- bulk fetch + DB write, never raises
# ---------------------------------------------------------------------------

def test_backfill_writes_rows_and_reports_success(db_engine, mocker):
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )

    summary = mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    assert summary["succeeded"] == ["AAPL"]
    assert summary["rows_written"]["AAPL"] == 1
    assert summary["tag_used"]["AAPL"] == "dei:test"
    assert summary["no_cik"] == []
    assert summary["no_data"] == []
    assert summary["errors"] == {}

    mc.clear_caches()
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 6, 1)) == 1000.0


def test_backfill_reports_missing_cik_without_raising(db_engine, mocker):
    mocker.patch.object(mc, "fetch_cik_map", return_value={})  # no ticker resolves
    summary = mc.backfill_shares_outstanding(["ZZZZ"], sleep=0)
    assert summary["no_cik"] == ["ZZZZ"]
    assert summary["succeeded"] == []


def test_backfill_reports_no_data_when_all_tags_empty(db_engine, mocker):
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(mc, "fetch_shares_outstanding", return_value=([], None))
    summary = mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    assert summary["no_data"] == ["AAPL"]
    assert summary["succeeded"] == []


def test_backfill_isolates_one_ticker_failure_from_the_rest(db_engine, mocker):
    """One ticker raising must not stop the remaining tickers in the same
    batch from being processed."""
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "1", "MSFT": "2"})

    def fake_fetch(cik):
        if cik == "1":
            raise RuntimeError("boom")
        return [{"end": "2020-01-01", "val": 500.0, "filed": "2020-01-15"}], "dei:test"

    mocker.patch.object(mc, "fetch_shares_outstanding", side_effect=fake_fetch)
    summary = mc.backfill_shares_outstanding(["AAPL", "MSFT"], sleep=0)
    assert "AAPL" in summary["errors"]
    assert summary["succeeded"] == ["MSFT"]


def test_backfill_is_idempotent_upsert(db_engine, mocker):
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)  # re-run, same period_end
    mc.clear_caches()
    series = mc._get_shares_series("AAPL")
    assert len(series) == 1  # upserted, not duplicated


def test_backfill_updates_value_on_conflict(db_engine, mocker):
    """A re-run with a revised figure for the same period_end must
    overwrite, not silently keep the stale first value -- unlike
    fetch_shares_outstanding()'s own earliest-filed rule (which resolves
    conflicting SEC data), this is a local re-fetch of the same
    already-resolved figure being refreshed."""
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)

    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1100.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    mc.clear_caches()
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 6, 1)) == 1100.0


# ---------------------------------------------------------------------------
# shares_outstanding_as_of() -- forward-fill, no look-ahead
# ---------------------------------------------------------------------------

def test_shares_outstanding_as_of_forward_fills(db_engine):
    _seed_shares(db_engine, "AAPL", date(2020, 1, 1), 1000.0)
    _seed_shares(db_engine, "AAPL", date(2020, 4, 1), 1010.0)
    _seed_shares(db_engine, "AAPL", date(2020, 7, 1), 4000.0)  # simulated split

    assert mc.shares_outstanding_as_of("AAPL", date(2020, 2, 15)) == 1000.0  # between Q1 and Q2 filings
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 4, 1)) == 1010.0   # exact filing date
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 6, 30)) == 1010.0  # day before the split filing
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 7, 1)) == 4000.0   # split filing date itself
    assert mc.shares_outstanding_as_of("AAPL", date(2021, 1, 1)) == 4000.0   # well after last filing


def test_shares_outstanding_as_of_no_look_ahead_before_first_filing(db_engine):
    """A date before the ticker's earliest stored filing must return None
    -- never the first-ever filing's value applied retroactively."""
    _seed_shares(db_engine, "AAPL", date(2020, 1, 1), 1000.0)
    assert mc.shares_outstanding_as_of("AAPL", date(2019, 12, 31)) is None


def test_shares_outstanding_as_of_returns_none_for_unknown_ticker(db_engine):
    assert mc.shares_outstanding_as_of("NOPE", date(2020, 1, 1)) is None


def test_shares_outstanding_as_of_is_cached_across_calls(db_engine, mocker):
    _seed_shares(db_engine, "AAPL", date(2020, 1, 1), 1000.0)
    mc.shares_outstanding_as_of("AAPL", date(2020, 6, 1))  # populate cache

    real_get_engine = mc.get_engine
    spy = mocker.patch.object(mc, "get_engine")
    mc.shares_outstanding_as_of("AAPL", date(2020, 6, 1))
    spy.assert_not_called()


def test_close_price_as_of_is_cached_across_calls(db_engine, mocker):
    """Mirrors test_shares_outstanding_as_of_is_cached_across_calls() --
    _close_price_as_of() must query price_data at most once per ticker
    (on its first lookup), never once per call. This is the regression
    guard for the 2026-08-01 connection-pooling fix (see
    _get_price_series()'s own docstring): confirmed to genuinely fail
    against the pre-fix implementation (a fresh get_engine() call on
    every _close_price_as_of() call), and to pass against the fix."""
    _seed_price(db_engine, "AAPL", date(2020, 1, 1), 10.0)
    mc._close_price_as_of("AAPL", date(2020, 6, 1))  # populate cache

    spy = mocker.patch.object(mc, "get_engine")
    mc._close_price_as_of("AAPL", date(2020, 6, 1))
    spy.assert_not_called()


def test_market_cap_bulk_ranking_creates_bounded_not_linear_connections(db_engine, mocker):
    """The exact real-world failure this fix closes: a bulk ranking
    operation (many market_cap() calls across a small set of tickers,
    each ticker looked up repeatedly across many reconstitution dates)
    must hit get_engine() a BOUNDED number of times -- at most twice per
    distinct ticker (once for its shares series, once for its price
    series) -- never once per market_cap() call. Confirmed (2026-08-01)
    against the real, pre-fix code: 100 market_cap() lookups across 5
    tickers produced 105 get_engine() calls (linear in lookup count, not
    ticker count) -- this test's own bound (<= 2 * n_tickers) would have
    failed against that, and passes against the fix."""
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
    dates = [date(2020, 1, d) for d in range(1, 21)]  # 20 dates
    for t in tickers:
        _seed_shares(db_engine, t, date(2019, 1, 1), 1000.0)
        _seed_price(db_engine, t, date(2019, 1, 1), 10.0)

    real_get_engine = mc.get_engine
    call_count = {"n": 0}

    def _counting_get_engine():
        call_count["n"] += 1
        return real_get_engine()

    mocker.patch.object(mc, "get_engine", side_effect=_counting_get_engine)

    for t in tickers:
        for d in dates:
            mc.market_cap(t, d)  # 100 total lookups (5 tickers x 20 dates)

    assert call_count["n"] <= 2 * len(tickers), (
        f"get_engine() was called {call_count['n']} times for "
        f"{len(tickers) * len(dates)} market_cap() lookups across only "
        f"{len(tickers)} distinct tickers -- expected at most "
        f"{2 * len(tickers)} (one shares-series query + one price-series "
        f"query per ticker), got a count that grows with lookup count "
        f"instead of ticker count."
    )


# ---------------------------------------------------------------------------
# market_cap() -- shares x price, no look-ahead, None when either is missing
# ---------------------------------------------------------------------------

def test_market_cap_multiplies_shares_by_asof_price(db_engine):
    _seed_shares(db_engine, "AAPL", date(2020, 1, 1), 1000.0)
    _seed_price(db_engine, "AAPL", date(2020, 1, 1), 10.0)
    _seed_price(db_engine, "AAPL", date(2020, 6, 1), 12.0)

    assert mc.market_cap("AAPL", date(2020, 1, 1)) == 10000.0
    # Forward-filled shares (still 1000 as of no later filing) x the
    # nearest price on/before the requested date.
    assert mc.market_cap("AAPL", date(2020, 6, 15)) == 12000.0


def test_market_cap_none_when_no_shares_data(db_engine):
    assert mc.market_cap("NOPE", date(2020, 1, 1)) is None


def test_market_cap_none_when_shares_exist_but_no_price(db_engine):
    _seed_shares(db_engine, "AAPL", date(2020, 1, 1), 1000.0)
    # No price row seeded at all.
    assert mc.market_cap("AAPL", date(2020, 6, 1)) is None


def test_market_cap_none_when_price_predates_shares_availability(db_engine):
    """Shares filing starts 2020-06-01; a price on 2020-01-01 exists but
    there is no shares figure available yet for that date -- must be
    None, never silently use a later filing's shares count for an earlier
    price date (that would be a look-ahead)."""
    _seed_price(db_engine, "AAPL", date(2020, 1, 1), 10.0)
    _seed_shares(db_engine, "AAPL", date(2020, 6, 1), 1000.0)
    assert mc.market_cap("AAPL", date(2020, 1, 1)) is None


def test_market_cap_ignores_null_or_nonpositive_close_prices(db_engine):
    """Mirrors the project-wide price_data quality guard (CLAUDE.md:
    "DELETE FROM price_data WHERE close IS NULL OR close <= 0") -- a
    zero/negative close row must not be treated as a valid as-of price."""
    _seed_shares(db_engine, "AAPL", date(2020, 1, 1), 1000.0)
    _seed_price(db_engine, "AAPL", date(2020, 1, 1), 10.0)
    _seed_price(db_engine, "AAPL", date(2020, 6, 1), 0.0)  # bad intraday row
    assert mc.market_cap("AAPL", date(2020, 6, 15)) == 10000.0  # falls back to the last good price


# ---------------------------------------------------------------------------
# Staleness check (Q2/Q6, added 2026-08-01) -- a candidate identified as
# eligible at one quarterly reconstitution can be delisted/acquired before
# the next one runs. A delisted company simply stops producing new price
# rows, so this is proactively detectable from price_data alone. Must be
# TRADING-DAY aware, not calendar-day aware, or an ordinary market holiday
# (Thanksgiving, Christmas) would misclassify a perfectly healthy ticker as
# stale -- verified directly against the real NYSE calendar below, not
# assumed.
# ---------------------------------------------------------------------------

def test_trading_sessions_since_zero_when_as_of_not_after_last():
    assert mc.trading_sessions_since(date(2020, 1, 15), date(2020, 1, 15)) == 0
    assert mc.trading_sessions_since(date(2020, 1, 15), date(2020, 1, 10)) == 0


def test_trading_sessions_since_ordinary_weekdays():
    # A plain Mon->Fri span with no holiday: Tue, Wed, Thu, Fri = 4 sessions
    # strictly after Monday.
    assert mc.trading_sessions_since(date(2024, 1, 8), date(2024, 1, 12)) == 4


def test_trading_sessions_since_skips_weekend():
    # Friday -> the following Monday: only Monday itself counts (Sat/Sun
    # were never trading sessions to begin with).
    assert mc.trading_sessions_since(date(2024, 1, 5), date(2024, 1, 8)) == 1


def test_trading_sessions_since_thanksgiving_2025_not_counted():
    """Real NYSE holiday, verified directly (2026-08-01) against the live
    pandas_market_calendars NYSE schedule: Thursday 2025-11-27 (Thanksgiving)
    has no session at all. Wed 2025-11-26 -> Fri 2025-11-28 must count only
    Friday itself (1 session), not 2."""
    gap = mc.trading_sessions_since(date(2025, 11, 26), date(2025, 11, 28))
    assert gap == 1


def test_trading_sessions_since_christmas_holiday_gap_is_correctly_small():
    """The explicit calendar-day-vs-trading-day distinction Q6 asks for:
    2025-12-24 (Wed before Christmas) -> 2025-12-29 (the following Monday)
    spans Christmas Day (2025-12-25, a real NYSE holiday) plus a weekend.
    Naive calendar-day subtraction gives 5 days -- comfortably enough to
    misclassify a perfectly healthy ticker as stale under a threshold like
    3. The real trading-session count is only 2 (Dec 26, Dec 29) -- must
    NOT be flagged as stale at that same threshold."""
    naive_calendar_days = (date(2025, 12, 29) - date(2025, 12, 24)).days
    assert naive_calendar_days == 5  # confirms the naive count really would over-flag

    real_trading_gap = mc.trading_sessions_since(date(2025, 12, 24), date(2025, 12, 29))
    assert real_trading_gap == 2

    assert real_trading_gap <= 3 < naive_calendar_days  # the holiday-awareness point, made explicit


def test_latest_price_date_returns_most_recent_good_row(db_engine):
    _seed_price(db_engine, "AAPL", date(2024, 1, 5), 100.0)
    _seed_price(db_engine, "AAPL", date(2024, 1, 8), 101.0)
    _seed_price(db_engine, "AAPL", date(2024, 1, 9), 0.0)  # bad row, must be ignored
    assert mc.latest_price_date("AAPL") == date(2024, 1, 8)


def test_latest_price_date_none_for_unknown_ticker(db_engine):
    assert mc.latest_price_date("NOPE") is None


def test_is_price_data_stale_none_when_no_price_history(db_engine):
    assert mc.is_price_data_stale("NOPE", date(2024, 1, 1)) is None


def test_is_price_data_stale_false_for_a_current_ticker(db_engine):
    """Last price is one ordinary trading day behind as_of -- a perfectly
    healthy, actively-traded ticker (e.g. the day after an EOD pull) --
    must not be flagged."""
    _seed_price(db_engine, "AAPL", date(2024, 1, 8), 100.0)
    assert mc.is_price_data_stale("AAPL", date(2024, 1, 9)) is False


def test_is_price_data_stale_true_for_a_genuinely_delisted_ticker(db_engine):
    """Last price is ~3 real trading weeks behind as_of -- the signature of
    a genuine delisting/acquisition (the EOD pull job has had nothing to
    fetch for it for weeks), well past the default 5-trading-session
    threshold."""
    _seed_price(db_engine, "DELISTED", date(2024, 1, 8), 100.0)
    assert mc.is_price_data_stale("DELISTED", date(2024, 1, 29)) is True


def test_is_price_data_stale_respects_custom_threshold(db_engine):
    _seed_price(db_engine, "AAPL", date(2024, 1, 8), 100.0)
    # 4 trading sessions later (Jan 9, 10, 11, 12) -- stale under a tight
    # threshold of 2, not stale under the default of 5.
    assert mc.is_price_data_stale("AAPL", date(2024, 1, 12), max_stale_trading_days=2) is True
    assert mc.is_price_data_stale("AAPL", date(2024, 1, 12), max_stale_trading_days=5) is False


def test_is_price_data_stale_not_falsely_flagged_across_christmas(db_engine):
    """The core Q2+Q6 regression, end to end through the real public
    function (not just trading_sessions_since() in isolation): a ticker
    last priced the Wednesday before Christmas, checked the following
    Monday, must NOT be flagged stale at the default threshold -- even
    though the naive calendar-day gap (5 days) would exceed a much
    tighter, still-reasonable threshold."""
    _seed_price(db_engine, "AAPL", date(2025, 12, 24), 100.0)
    assert mc.is_price_data_stale("AAPL", date(2025, 12, 29)) is False
    assert mc.is_price_data_stale("AAPL", date(2025, 12, 29), max_stale_trading_days=3) is False


def test_is_price_data_stale_logs_a_clear_reason_when_stale(db_engine, caplog):
    """Per the task's own requirement -- "mark it ineligible and log
    clearly why" -- the warning must name the ticker and be genuinely
    informative, not a bare boolean with no context."""
    import logging
    _seed_price(db_engine, "DELISTED", date(2024, 1, 8), 100.0)
    with caplog.at_level(logging.WARNING, logger="analysis.market_cap"):
        result = mc.is_price_data_stale("DELISTED", date(2024, 1, 29))
    assert result is True
    assert any("DELISTED" in r.message and "stale" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Ticker symbol recycling (Q4, added 2026-08-01) -- distinct from
# _KNOWN_TICKER_RENAMES (same company, symbol changes, e.g. FB->META): an
# unrelated company later trading under a symbol a delisted company used to
# hold. SEC EDGAR filings are genuinely CIK-scoped, so a single backfill run
# can never itself blend two companies' shares data together -- but nothing
# previously compared today's resolved CIK against what was stored for that
# ticker last time. detect_cik_change() / stored_cik() close that gap for
# any recycling event that happens BETWEEN two backfill runs (it cannot
# retroactively detect one that already happened before this project's
# first-ever backfill of a ticker -- a stated, permanent limitation).
# ---------------------------------------------------------------------------

def test_stored_cik_none_when_nothing_stored(db_engine):
    assert mc.stored_cik("AAPL") is None


def test_stored_cik_returns_the_one_cik_on_record(db_engine, mocker):
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    assert mc.stored_cik("AAPL") == "0000320193"


def test_detect_cik_change_none_when_no_prior_data(db_engine):
    assert mc.detect_cik_change("AAPL", "0000320193") is None


def test_detect_cik_change_none_when_cik_matches(db_engine, mocker):
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    assert mc.detect_cik_change("AAPL", "0000320193") is None


def test_detect_cik_change_flags_a_genuine_symbol_reassignment(db_engine, mocker):
    """The core Q4 regression: a ticker previously mapped to one CIK
    (Company A) now resolves to a different CIK (Company B) -- a real,
    confirmed recycling event, not a rename."""
    mocker.patch.object(mc, "fetch_cik_map", return_value={"OLDCO": "0000111111"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["OLDCO"], sleep=0)

    assert mc.detect_cik_change("OLDCO", "0000222222") == "0000111111"


def test_backfill_records_and_logs_a_cik_change(db_engine, mocker, caplog):
    """End to end through the real public backfill function, not just
    detect_cik_change() in isolation -- confirms the summary dict records
    it AND a clear, informative warning is logged (per the same "mark it
    and log clearly why" standard Q2's staleness check follows)."""
    import logging

    mocker.patch.object(mc, "fetch_cik_map", return_value={"TICKR": "0000111111"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    mc.backfill_shares_outstanding(["TICKR"], sleep=0)

    # Second run -- the ticker now resolves to a different CIK.
    mocker.patch.object(mc, "fetch_cik_map", return_value={"TICKR": "0000222222"})
    with caplog.at_level(logging.WARNING, logger="analysis.market_cap"):
        summary = mc.backfill_shares_outstanding(["TICKR"], sleep=0)

    assert summary["cik_changed"] == {"TICKR": {"old": "0000111111", "new": "0000222222"}}
    assert any(
        "TICKR" in r.message and "0000111111" in r.message and "0000222222" in r.message
        for r in caplog.records
    )
    # The new (genuinely correct, current) data must still be written --
    # this is detect-and-flag, not a block.
    assert "TICKR" in summary["succeeded"]
    mc.clear_caches()
    assert mc.stored_cik("TICKR") == "0000222222"


def test_backfill_does_not_flag_a_ticker_backfilled_for_the_first_time(db_engine, mocker):
    """No prior CIK on record at all -- must not be misreported as a
    change; this is simply the ticker's first-ever backfill."""
    mocker.patch.object(mc, "fetch_cik_map", return_value={"NEWCO": "0000333333"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=([{"end": "2020-01-01", "val": 1000.0, "filed": "2020-01-15"}], "dei:test"),
    )
    summary = mc.backfill_shares_outstanding(["NEWCO"], sleep=0)
    assert summary["cik_changed"] == {}


# ---------------------------------------------------------------------------
# Look-ahead bias in filing timing (Q5, found and fixed 2026-08-01) --
# shares_outstanding_as_of() used to gate on period_end_date (the period a
# filing DESCRIBES) rather than filed_date (when it actually became
# public). Real 10-Q/10-K filings lag their own period-end by 4-8 weeks, so
# gating on period_end_date let a point-in-time query "know" a filing's
# contents before it was ever public -- the exact hindsight-bias mistake
# this whole project's point-in-time membership work exists to eliminate,
# reintroduced at the filing-lag layer.
# ---------------------------------------------------------------------------

def test_shares_outstanding_as_of_does_not_look_ahead_of_the_filed_date(db_engine):
    """The core Q5 regression: a Q3 filing describes the period ending
    2020-09-30 but wasn't actually filed until 2020-11-05 (a realistic
    ~5-week lag). Querying shares_outstanding_as_of() on a date between
    the period end and the real filing date must NOT see this filing --
    only the PRIOR filing (filed well before) is genuinely knowable then."""
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 6, 30), shares=1000.0, filed_date=date(2020, 8, 1))
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 9, 30), shares=1100.0, filed_date=date(2020, 11, 5))

    # 2020-10-15: after the Q3 period ended, but well before the Q3 filing
    # was actually public. Point-in-time-correct answer is still the Q2
    # figure (1000.0) -- the Q3 filing (1100.0) is not yet knowable.
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 10, 15)) == 1000.0

    # The moment it's actually filed, it becomes the correct answer.
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 11, 5)) == 1100.0
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 11, 6)) == 1100.0


def test_shares_outstanding_as_of_pre_fix_behaviour_would_have_been_wrong(db_engine):
    """Direct, explicit demonstration that the OLD (period_end_date-gated)
    behaviour was genuinely wrong for this scenario -- not just that the
    new behaviour differs abstractly. Confirms the specific number a
    period_end_date-only read would have returned (1100.0, the Q3 figure)
    is NOT what the fixed function returns (1000.0, the Q2 figure) on a
    date where only the Q2 filing was actually public."""
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 6, 30), shares=1000.0, filed_date=date(2020, 8, 1))
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 9, 30), shares=1100.0, filed_date=date(2020, 11, 5))

    as_of = date(2020, 10, 15)  # period_end_date (Sep 30) <= as_of, but filed_date (Nov 5) > as_of
    old_buggy_answer = 1100.0  # what a period_end_date-gated read would incorrectly return
    fixed_answer = mc.shares_outstanding_as_of("AAPL", as_of)
    assert fixed_answer != old_buggy_answer
    assert fixed_answer == 1000.0


def test_shares_outstanding_as_of_falls_back_to_period_end_when_filed_date_unknown(db_engine):
    """Backward compatibility: a row with no filed_date on record (legacy
    data, or a synthetic fixture -- see _seed_shares()'s own default)
    falls back to gating on period_end_date, matching this function's
    pre-fix behaviour for that one row rather than silently dropping it."""
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 6, 30), shares=1000.0)  # no filed_date
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 7, 15)) == 1000.0


def test_backfill_persists_the_real_filed_date(db_engine, mocker):
    """End to end through the real public backfill function: the filed
    date SEC actually reports must be what gets stored and later read
    back, not silently dropped in favour of period_end_date."""
    mocker.patch.object(mc, "fetch_cik_map", return_value={"AAPL": "0000320193"})
    mocker.patch.object(
        mc, "fetch_shares_outstanding",
        return_value=(
            [{"end": "2020-09-30", "val": 1100.0, "filed": "2020-11-05"}],
            "dei:test",
        ),
    )
    mc.backfill_shares_outstanding(["AAPL"], sleep=0)
    mc.clear_caches()

    # Point-in-time-correct: not yet knowable on 2020-10-15 (before the
    # real filed_date), correctly knowable from 2020-11-05 onward.
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 10, 15)) is None
    assert mc.shares_outstanding_as_of("AAPL", date(2020, 11, 5)) == 1100.0


def test_market_cap_end_to_end_respects_filing_lag(db_engine):
    """market_cap() itself (shares x price) must reflect the same
    point-in-time discipline, not just shares_outstanding_as_of() in
    isolation."""
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 6, 30), shares=1000.0, filed_date=date(2020, 8, 1))
    _seed_shares(db_engine, "AAPL", period_end=date(2020, 9, 30), shares=1100.0, filed_date=date(2020, 11, 5))
    _seed_price(db_engine, "AAPL", date(2020, 10, 15), 10.0)

    # 1000 shares (Q2, the only genuinely public figure on 2020-10-15) x
    # £10 price = £10,000 -- not 1100 x 10 = £11,000, which would use the
    # not-yet-public Q3 figure.
    assert mc.market_cap("AAPL", date(2020, 10, 15)) == 10000.0


# ---------------------------------------------------------------------------
# companyfacts fallback -- the real, live-verified gap (2026-08-01)
#
# The three tags in SHARES_TAGS were never the problem: SEC's per-concept
# companyconcept endpoint is simply unreliable. For 27 of the 44
# previously-unresolvable S&P 500 candidates (KO, WFC, ABT, AMT, CB, COF,
# CTAS, ...) it returns HTTP 200 with an EMPTY units.shares array for a tag
# that companyfacts, for the identical CIK and identical tag, serves in full
# (KO: dei:EntityCommonStockSharesOutstanding, 71 rows in companyfacts vs 0
# from companyconcept). Reproduced in a clean, unloaded environment, so it is
# a genuine SEC-side endpoint inconsistency, not rate-limiting.
#
# Why the pre-existing tests did not catch this: they all mock _fetch_concept
# directly, so they exercise the fallback CHAIN LOGIC but never the endpoint's
# real behaviour -- and, critically, they model a missing tag as None, which
# collapses SEC's genuine 404 ("concept absent") and its 200-with-empty-array
# ("concept present, no rows returned") into the same value. The production
# code collapsed them the same way, so the tests agreed with the bug.
# ---------------------------------------------------------------------------

def test_companyfacts_fallback_resolves_when_every_concept_tag_is_empty(mocker):
    """The real KO/WFC case: every companyconcept tag yields nothing, but
    companyfacts has full history for the very first tag."""
    facts = {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [
                    {"end": "2012-07-23", "val": 2250961597, "filed": "2012-07-26"},
                    {"end": "2012-10-22", "val": 4485161506, "filed": "2012-10-25"},
                ]}
            }
        }
    }
    mocker.patch.object(mc, "_fetch_concept", return_value=None)
    mocker.patch.object(mc, "_fetch_company_facts", return_value=facts)

    rows, tag_used = mc.fetch_shares_outstanding("0000021344")  # KO's real CIK

    assert tag_used == "dei:EntityCommonStockSharesOutstanding (companyfacts)"
    assert [r["val"] for r in rows] == [2250961597, 4485161506]


def test_companyconcept_200_with_empty_array_falls_through_like_a_404(mocker):
    """SEC returns 200-with-empty-units for many real filers. _fetch_concept
    surfaces that as an empty list; it must fall through to the next tag and
    ultimately to companyfacts exactly as a hard 404 does -- this specific
    conflation is what hid the gap in the first place."""
    facts = {"us-gaap": {"CommonStockSharesIssued": {"units": {"shares": [
        {"end": "2020-12-31", "val": 555, "filed": "2021-01-15"},
    ]}}}}
    # [] is what a 200-with-empty-shares response yields, not None.
    mocker.patch.object(mc, "_fetch_concept", return_value=[])
    mocker.patch.object(mc, "_fetch_company_facts", return_value=facts)

    rows, tag_used = mc.fetch_shares_outstanding("0000072971")

    assert tag_used == "us-gaap:CommonStockSharesIssued (companyfacts)"
    assert rows == [{"end": "2020-12-31", "val": 555, "filed": "2021-01-15"}]


def test_companyfacts_is_not_consulted_when_a_concept_tag_has_data(mocker):
    """Strict fallback chain: companyfacts is an extra step AFTER the existing
    three tags, never a reordering of them. A filer that already resolves via
    companyconcept must not incur a second, much larger request."""
    mocker.patch.object(
        mc, "_fetch_concept",
        return_value=[{"end": "2020-01-01", "val": 1000, "filed": "2020-01-15"}],
    )
    facts_spy = mocker.patch.object(mc, "_fetch_company_facts")

    rows, tag_used = mc.fetch_shares_outstanding("0000320193")

    assert tag_used == "dei:EntityCommonStockSharesOutstanding"  # no suffix
    assert rows
    facts_spy.assert_not_called()


def test_companyfacts_path_respects_shares_tags_order(mocker):
    """When companyfacts carries more than one of the three tags, the same
    SHARES_TAGS precedence applies as on the companyconcept path."""
    facts = {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2021-01-01", "val": 111, "filed": "2021-02-01"}]}}},
        "us-gaap": {"CommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2021-01-01", "val": 999, "filed": "2021-02-01"}]}}},
    }
    mocker.patch.object(mc, "_fetch_concept", return_value=None)
    mocker.patch.object(mc, "_fetch_company_facts", return_value=facts)

    rows, tag_used = mc.fetch_shares_outstanding("0000000002")

    assert tag_used == "dei:EntityCommonStockSharesOutstanding (companyfacts)"
    assert rows[0]["val"] == 111


def test_companyfacts_path_applies_earliest_filed_dedup(mocker):
    """The restatement rule (keep the EARLIEST-filed value per period_end)
    is shared by both paths via _dedupe_earliest_filed() -- it must not be
    silently skipped on the companyfacts path."""
    facts = {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2021-12-31", "val": 662121000, "filed": "2022-02-02"},
        {"end": "2021-12-31", "val": 13242000000, "filed": "2022-10-26"},
    ]}}}}
    mocker.patch.object(mc, "_fetch_concept", return_value=None)
    mocker.patch.object(mc, "_fetch_company_facts", return_value=facts)

    rows, _ = mc.fetch_shares_outstanding("0001652044")

    assert len(rows) == 1
    assert rows[0]["val"] == 662121000  # pre-split original, not the restatement


def test_companyfacts_failure_is_isolated_and_returns_empty(mocker):
    """A companyfacts request blowing up must degrade to 'no data', never
    propagate -- matching this module's existing per-tag failure isolation."""
    mocker.patch.object(mc, "_fetch_concept", return_value=None)
    mocker.patch.object(mc, "_fetch_company_facts", side_effect=RuntimeError("SEC 500"))

    rows, tag_used = mc.fetch_shares_outstanding("0000000003")

    assert rows == []
    assert tag_used is None


# ---------------------------------------------------------------------------
# Predecessor-CIK fallback -- the real XOM case (verified live 2026-08-01)
# ---------------------------------------------------------------------------

def test_predecessor_cik_resolves_after_holdco_reorganisation(mocker):
    """SEC now maps ticker XOM to 'ExxonMobil Holdings Corp' (CIK 0002115436,
    first filing 2026-07-01, zero XBRL shares history) following a July 2026
    holding-company reorganisation; the predecessor 'EXXON MOBIL CORP'
    (CIK 0000034088, which filed Form 25-NSE on 2026-07-02) holds the real
    history this module needs."""
    hist = [{"end": "2026-03-31", "val": 4144947162, "filed": "2026-05-04"}]

    def fake_concept(cik, taxonomy, tag):
        if cik == "0000034088" and (taxonomy, tag) == ("dei", "EntityCommonStockSharesOutstanding"):
            return hist
        return None

    mocker.patch.object(mc, "_fetch_concept", side_effect=fake_concept)
    mocker.patch.object(mc, "_fetch_company_facts", return_value=None)

    rows, tag_used = mc.fetch_shares_outstanding("0002115436")

    assert rows == hist
    assert "predecessor CIK 0000034088" in tag_used
    assert tag_used.startswith("dei:EntityCommonStockSharesOutstanding")


def test_predecessor_lookup_only_fires_for_known_successor_ciks(mocker):
    """An unknown CIK with no data must NOT trigger a speculative second
    lookup -- the map is a deliberate, hand-verified list, not an inferred
    rule (see PREDECESSOR_CIKS' own docstring)."""
    concept = mocker.patch.object(mc, "_fetch_concept", return_value=None)
    mocker.patch.object(mc, "_fetch_company_facts", return_value=None)

    rows, tag_used = mc.fetch_shares_outstanding("0000000009")

    assert rows == []
    assert tag_used is None
    # exactly the three SHARES_TAGS attempts, no retry against another CIK
    assert concept.call_count == len(mc.SHARES_TAGS)


def test_fetch_company_facts_strips_CIK_prefix_and_404s_cleanly(mocker):
    """companyfacts' URL already embeds the literal 'CIK' prefix, so a
    caller passing an already-prefixed value must not produce 'CIKCIK...'."""
    resp = mocker.Mock()
    resp.status_code = 404
    spy = mocker.patch.object(mc.requests, "get", return_value=resp)

    assert mc._fetch_company_facts("CIK0000021344") is None

    url = spy.call_args[0][0]
    assert "CIK0000021344.json" in url
    assert "CIKCIK" not in url


def test_non_positive_share_counts_are_dropped(mocker):
    """Real filings carry stray 0 share-count rows (verified live: HBAN
    2011-03-31, KO 2009-10-23, RCL 2012-02-27, VRSK 2012-04-27). Because
    shares_outstanding_as_of() forward-fills, one 0 would zero out market cap
    from that filing until the next one -- ranking a mega-cap below peers."""
    raw = [
        {"end": "2009-07-24", "val": 2317441658, "filed": "2009-07-30"},
        {"end": "2009-10-23", "val": 0, "filed": "2009-10-29"},
        {"end": "2010-01-22", "val": 2318000000, "filed": "2010-01-28"},
    ]
    mocker.patch.object(
        mc, "_fetch_concept",
        side_effect=lambda c, tax, tag: raw if tax == "dei" else None,
    )
    rows, _ = mc.fetch_shares_outstanding("0000021344")
    assert [r["end"] for r in rows] == ["2009-07-24", "2010-01-22"]
    assert all(r["val"] > 0 for r in rows)


def test_negative_share_count_is_dropped(mocker):
    mocker.patch.object(
        mc, "_fetch_concept",
        side_effect=lambda c, tax, tag: (
            [{"end": "2020-01-01", "val": -5, "filed": "2020-02-01"}] if tax == "dei" else None
        ),
    )
    mocker.patch.object(mc, "_fetch_company_facts", return_value=None)
    rows, tag_used = mc.fetch_shares_outstanding("0000000004")
    assert rows == []
    assert tag_used is None


# ---------------------------------------------------------------------------
# Same-period amendment preference -- the real WFC 2023-07-21 defect
#
# _dedupe_earliest_filed() keeps the EARLIEST-filed value per period_end,
# which is correct for RESTATEMENTS (GOOGL's split) but picks the filer's
# ERROR over the filer's own correction when the later filing is an
# amendment of the SAME period. Found via a systemic scan of all 487
# resolved S&P 500 candidates: 14 real conflicts across 13 tickers.
# ---------------------------------------------------------------------------

def _row(end, val, filed, form, fy, fp):
    return {"end": end, "val": val, "filed": filed, "form": form, "fy": fy, "fp": fp}


def test_same_period_amendment_supersedes_erroneous_original(mocker):
    """The real WFC case: the original Q2-2023 10-Q (filed 2023-08-01)
    reported 1,823,028,137; WFC's own 10-Q/A three days later corrected it to
    3,658,783,337, which fits between the neighbouring quarters."""
    raw = [
        _row("2023-04-21", 3752223519, "2023-05-02", "10-Q", 2023, "Q1"),
        _row("2023-07-21", 1823028137, "2023-08-01", "10-Q", 2023, "Q2"),
        _row("2023-07-21", 3658783337, "2023-08-04", "10-Q/A", 2023, "Q2"),
        _row("2023-10-20", 3631639714, "2023-10-31", "10-Q", 2023, "Q3"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0000072971")
    got = {r["end"]: r["val"] for r in rows}
    assert got["2023-07-21"] == 3658783337


def test_same_day_amendment_still_supersedes(mocker):
    """ROST 2010-11-18 and BSX 2013-04-30 both filed the 10-Q and its 10-Q/A
    on the SAME DAY -- a strict filed-date > comparison silently left both on
    the erroneous original, so the comparison must be >=."""
    raw = [
        _row("2010-08-01", 120457145, "2010-09-01", "10-Q", 2011, "Q1"),
        _row("2010-11-18", 19084540, "2010-12-08", "10-Q", 2011, "Q2"),
        _row("2010-11-18", 119084540, "2010-12-08", "10-Q/A", 2011, "Q2"),
        _row("2011-02-01", 117599994, "2011-03-01", "10-Q", 2011, "Q3"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0000745732")
    got = {r["end"]: r["val"] for r in rows}
    assert got["2010-11-18"] == 119084540


def test_cross_period_amendment_does_not_override_restatement(mocker):
    """PPG 2014-12-31: a 10-K/A filed in 2018 (a DIFFERENT fiscal period)
    reports exactly 2x -- its real 2:1 split restated for comparability, NOT
    a correction. Earliest-filed must still win, or this silently applies a
    split years before it happened. This is what the (fy, fp) guard protects."""
    raw = [
        _row("2014-12-31", 135982283, "2015-02-19", "10-K", 2014, "FY"),
        _row("2014-12-31", 271964566, "2018-06-28", "10-K/A", 2017, "FY"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0000079879")
    assert rows[0]["val"] == 135982283


def test_latest_amendment_wins_when_a_period_is_amended_twice(mocker):
    raw = [
        _row("2020-06-30", 100, "2020-07-01", "10-Q", 2020, "Q2"),
        _row("2020-06-30", 200, "2020-07-05", "10-Q/A", 2020, "Q2"),
        _row("2020-06-30", 300, "2020-07-09", "10-Q/A", 2020, "Q2"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0000000010")
    assert rows[0]["val"] == 300


def test_amendment_matching_the_original_value_changes_nothing(mocker):
    raw = [
        _row("2020-06-30", 500_000_000, "2020-07-01", "10-Q", 2020, "Q2"),
        _row("2020-06-30", 500_000_000, "2020-07-05", "10-Q/A", 2020, "Q2"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0000000011")
    assert len(rows) == 1 and rows[0]["val"] == 500_000_000


# ---------------------------------------------------------------------------
# Magnitude-outlier guard -- distinct from the zero/negative guard, which
# cannot catch these because the values are WRONG rather than absent.
# ---------------------------------------------------------------------------

def _plain(end, val, filed):
    return {"end": end, "val": val, "filed": filed, "form": "10-Q", "fy": 2012, "fp": "Q1"}


def test_unit_scale_outlier_is_dropped(mocker):
    """The real ORCL/AMD/MAR signature: a single row 1e6 too large. Because
    shares_outstanding_as_of() forward-fills, keeping it would poison market
    cap for every date until the next filing."""
    raw = [
        _plain("2012-01-01", 4_000_000_000, "2012-02-01"),
        _plain("2012-04-01", 4_100_000_000, "2012-05-01"),
        _plain("2012-07-01", 4_819_056_000_000_000, "2012-08-01"),  # 1e6 too big
        _plain("2012-10-01", 4_200_000_000, "2012-11-01"),
        _plain("2013-01-01", 4_300_000_000, "2013-02-01"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0001341439")
    assert [r["end"] for r in rows] == ["2012-01-01", "2012-04-01", "2012-10-01", "2013-01-01"]


def test_truncated_outlier_is_dropped(mocker):
    """The real CRM/LH/ICE signature: a truncated or placeholder value."""
    raw = [
        _plain("2012-01-01", 700_000_000, "2012-02-01"),
        _plain("2012-04-01", 134, "2012-05-01"),  # truncated
        _plain("2012-07-01", 720_000_000, "2012-08-01"),
        _plain("2012-10-01", 730_000_000, "2012-11-01"),
        _plain("2013-01-01", 740_000_000, "2013-02-01"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0001108524")
    assert 134 not in [r["val"] for r in rows]
    assert len(rows) == 4


def test_real_stock_splits_are_never_dropped_as_outliers(mocker):
    """NVDA's genuine 4:1 then 10:1 splits reach 40.25x its own series median
    -- the largest real ratio in the whole 487-ticker universe. The threshold
    must clear that comfortably or the guard would destroy real data."""
    raw = [
        _plain("2020-01-01", 610_000_000, "2020-02-01"),
        _plain("2021-01-01", 620_000_000, "2021-02-01"),
        _plain("2021-10-01", 2_500_000_000, "2021-11-01"),   # 4:1 split
        _plain("2024-01-01", 2_460_000_000, "2024-02-01"),
        _plain("2024-10-01", 24_530_000_000, "2024-11-01"),  # 10:1 split
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0001045810")
    assert len(rows) == 5
    assert 24_530_000_000 in [r["val"] for r in rows]


def test_outlier_guard_skipped_for_short_series(mocker):
    """The real PSKY case (n=3, median 1,000): with too few rows the median is
    not robust and the test INVERTS, flagging the one genuine value. Below
    _OUTLIER_MIN_ROWS the check is skipped entirely."""
    raw = [
        _plain("2025-01-01", 1_000, "2025-02-01"),
        _plain("2025-06-01", 1_000, "2025-07-01"),
        _plain("2025-11-05", 1_071_666_977, "2025-12-01"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0000000012")
    assert 1_071_666_977 in [r["val"] for r in rows]
    assert len(rows) == 3


def test_outlier_guard_never_returns_an_empty_series(mocker):
    """If a skewed median would drop everything, return the raw rows rather
    than nothing -- the caller should see real data, not silence."""
    rows_in = [{"end": f"2020-0{i}-01", "val": 10 ** (i + 3), "filed": f"2020-0{i}-15"}
               for i in range(1, 6)]
    out = mc._drop_magnitude_outliers(list(rows_in), "0000000013")
    assert out


def test_amendment_correction_runs_before_the_outlier_guard(mocker):
    """MAR 2010-04-09 is BOTH an amendment conflict and a 1e6 outlier. Order
    matters: correcting it first preserves the real value; running the outlier
    guard first would discard the period entirely."""
    raw = [
        _row("2010-01-01", 358_522_350, "2010-02-01", "10-Q", 2010, "Q1"),
        _row("2010-04-09", 361_332_043_000_000, "2010-04-23", "10-Q", 2010, "Q2"),
        _row("2010-04-09", 361_332_043, "2010-04-28", "10-Q/A", 2010, "Q2"),
        _row("2010-07-01", 362_811_812, "2010-08-01", "10-Q", 2010, "Q3"),
        _row("2010-10-01", 363_000_000, "2010-11-01", "10-Q", 2010, "Q4"),
    ]
    mocker.patch.object(
        mc, "_fetch_concept", side_effect=lambda c, tax, tag: raw if tax == "dei" else None)
    rows, _ = mc.fetch_shares_outstanding("0001048286")
    got = {r["end"]: r["val"] for r in rows}
    assert got["2010-04-09"] == 361_332_043  # corrected, not dropped
