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
    mocker.patch.object(mc, "_fetch_concept", return_value=None)
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
