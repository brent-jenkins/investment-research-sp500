"""Tests for data/ingestion.py:_upsert_price_rows() and backfill_price_history().

A single NaN/infinite/zero/negative close price in the rolling window breaks
every subsequent SMA computation, so _upsert_price_rows() must never write
such a row to price_data. Uses the db_engine fixture (in-memory SQLite) —
no live database or yfinance call is involved.
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from data.ingestion import (
    _DATE_OVERRUN_TOLERANCE_DAYS,
    _fetch_ticker,
    _find_retroactive_restatement,
    _upsert_price_rows,
    _yf_symbol,
    backfill_price_history,
    check_boundary_contiguity,
    detect_price_cliffs,
    fetch_ticker_sector,
    filter_known_price_cliff_exceptions,
    get_or_fetch_sector,
    scan_unverified_tickers,
    verify_ticker_identity,
)

_BASE_DATE = datetime(2026, 1, 1)


def _ohlcv_df(closes: dict) -> pd.DataFrame:
    """Build a yfinance-shaped OHLCV DataFrame from {date: close} pairs.

    Open/High/Low/Adj Close mirror Close for simplicity — only the Close
    column is exercised by the skip logic under test.
    """
    dates = list(closes.keys())
    values = list(closes.values())
    return pd.DataFrame(
        {
            "Open": values,
            "High": values,
            "Low": values,
            "Close": values,
            "Adj Close": values,
            "Volume": [1_000_000] * len(values),
        },
        index=pd.DatetimeIndex(dates),
    )


def _stored_closes(engine, ticker: str) -> list[float]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT close FROM price_data WHERE ticker = :t ORDER BY date"),
            {"t": ticker},
        ).fetchall()
    return [r[0] for r in rows]


def test_nan_close_row_is_skipped(db_engine):
    """A NaN close (incomplete intraday bar) must not reach price_data."""
    df = _ohlcv_df({
        _BASE_DATE: 100.0,
        _BASE_DATE + timedelta(days=1): float("nan"),
        _BASE_DATE + timedelta(days=2): 102.0,
    })
    written = _upsert_price_rows(df, "TESTNAN")
    assert written == 2
    assert _stored_closes(db_engine, "TESTNAN") == [100.0, 102.0]


def test_infinite_close_row_is_skipped(db_engine):
    """An infinite close (a bad division upstream, or a data-provider glitch)
    must be filtered the same way NaN is — both make rolling means unusable."""
    df = _ohlcv_df({
        _BASE_DATE: 100.0,
        _BASE_DATE + timedelta(days=1): float("inf"),
        _BASE_DATE + timedelta(days=2): float("-inf"),
        _BASE_DATE + timedelta(days=3): 103.0,
    })
    written = _upsert_price_rows(df, "TESTINF")
    assert written == 2
    assert _stored_closes(db_engine, "TESTINF") == [100.0, 103.0]


def test_zero_close_row_is_skipped(db_engine):
    """A zero close is an incomplete/unopened session bar, not a real price."""
    df = _ohlcv_df({
        _BASE_DATE: 100.0,
        _BASE_DATE + timedelta(days=1): 0.0,
        _BASE_DATE + timedelta(days=2): 101.0,
    })
    written = _upsert_price_rows(df, "TESTZERO")
    assert written == 2
    assert _stored_closes(db_engine, "TESTZERO") == [100.0, 101.0]


def test_negative_close_row_is_skipped(db_engine):
    """A negative close is never a valid equity/ETF/FX price and must be dropped."""
    df = _ohlcv_df({
        _BASE_DATE: 100.0,
        _BASE_DATE + timedelta(days=1): -5.0,
        _BASE_DATE + timedelta(days=2): 99.0,
    })
    written = _upsert_price_rows(df, "TESTNEG")
    assert written == 2
    assert _stored_closes(db_engine, "TESTNEG") == [100.0, 99.0]


def test_all_bad_values_skipped_together(db_engine):
    """NaN, +inf, -inf, zero, and negative all in one pull — only the two
    genuinely valid rows should survive, in original chronological order."""
    df = _ohlcv_df({
        _BASE_DATE:                    100.0,
        _BASE_DATE + timedelta(days=1): float("nan"),
        _BASE_DATE + timedelta(days=2): float("inf"),
        _BASE_DATE + timedelta(days=3): float("-inf"),
        _BASE_DATE + timedelta(days=4): 0.0,
        _BASE_DATE + timedelta(days=5): -1.0,
        _BASE_DATE + timedelta(days=6): 105.0,
    })
    written = _upsert_price_rows(df, "TESTALL")
    assert written == 2
    assert _stored_closes(db_engine, "TESTALL") == [100.0, 105.0]


def test_valid_rows_are_written_unmodified(db_engine):
    """Sanity check: normal positive finite closes are written through as-is,
    so the skip logic isn't accidentally dropping good data too."""
    df = _ohlcv_df({
        _BASE_DATE:                    100.0,
        _BASE_DATE + timedelta(days=1): 101.5,
        _BASE_DATE + timedelta(days=2): 99.25,
    })
    written = _upsert_price_rows(df, "TESTOK")
    assert written == 3
    assert _stored_closes(db_engine, "TESTOK") == [100.0, 101.5, 99.25]


# ---------------------------------------------------------------------------
# backfill_price_history() — additive-only backward backfill
# ---------------------------------------------------------------------------

def _insert_rows(engine, ticker: str, closes: dict) -> None:
    """Directly seed price_data rows, bypassing _upsert_price_rows's own
    filtering (this is test setup for "existing" data, not the code under
    test in these cases)."""
    df = _ohlcv_df(closes)
    _upsert_price_rows(df, ticker)


def test_backfill_skips_ticker_already_covering_start_date(db_engine, mocker):
    """A ticker whose earliest stored row is already <= start_date has
    nothing to backfill — _fetch_ticker must never be called for it."""
    mocker.patch("data.ingestion.init_db")  # avoid a redundant real init_db() call -- db_engine fixture already created the schema
    _insert_rows(db_engine, "COVERED", {datetime(2006, 1, 3): 50.0})
    fetch = mocker.patch("data.ingestion._fetch_ticker")

    results = backfill_price_history(["COVERED"], start_date="2007-01-01")

    fetch.assert_not_called()
    assert results["COVERED"] == 0


def test_backfill_fetches_only_strictly_before_existing_earliest_date(db_engine, mocker):
    """The fetch window must end the day BEFORE the ticker's current
    earliest stored date, and never touch that date or anything after it —
    the additive-only guarantee this function exists to provide."""
    mocker.patch("data.ingestion.init_db")  # avoid a redundant real init_db() call -- db_engine fixture already created the schema
    _insert_rows(db_engine, "PARTIAL", {datetime(2016, 6, 15): 100.0, datetime(2016, 6, 16): 101.0})

    new_rows = _ohlcv_df({datetime(2007, 1, 3): 10.0, datetime(2007, 1, 4): 10.5})
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=new_rows)

    results = backfill_price_history(["PARTIAL"], start_date="2007-01-01")

    call_kwargs = fetch.call_args.kwargs
    assert call_kwargs["start"] == datetime(2007, 1, 1)
    assert call_kwargs["end"] == datetime(2016, 6, 14)  # existing earliest - 1 day
    assert results["PARTIAL"] == 2
    # Existing rows untouched, new rows added, nothing in between fabricated.
    assert _stored_closes(db_engine, "PARTIAL") == [10.0, 10.5, 100.0, 101.0]


def test_backfill_full_fetch_when_no_existing_data(db_engine, mocker):
    """A ticker with no stored data at all falls back to a full fetch from
    start_date (mirrors pull_incremental()'s first-time-download path)."""
    mocker.patch("data.ingestion.init_db")  # avoid a redundant real init_db() call -- db_engine fixture already created the schema
    new_rows = _ohlcv_df({datetime(2007, 1, 3): 5.0})
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=new_rows)

    results = backfill_price_history(["BRANDNEW"], start_date="2007-01-01")

    call_kwargs = fetch.call_args.kwargs
    assert call_kwargs["start"] == datetime(2007, 1, 1)
    assert results["BRANDNEW"] == 1


def test_backfill_never_overwrites_existing_rows(db_engine, mocker):
    """The core additive-only guarantee, checked end-to-end: existing rows
    must be byte-for-byte unchanged after a backfill run, even when the
    mocked fetch is (deliberately, unrealistically) asked to return data —
    the function's own end= boundary must prevent any overlap regardless."""
    mocker.patch("data.ingestion.init_db")  # avoid a redundant real init_db() call -- db_engine fixture already created the schema
    _insert_rows(db_engine, "GUARD", {datetime(2016, 6, 15): 100.0, datetime(2016, 6, 16): 101.0})
    before = _stored_closes(db_engine, "GUARD")

    new_rows = _ohlcv_df({datetime(2007, 1, 3): 999.0})  # strictly before existing range
    mocker.patch("data.ingestion._fetch_ticker", return_value=new_rows)

    backfill_price_history(["GUARD"], start_date="2007-01-01")

    with db_engine.connect() as conn:
        after = conn.execute(
            text("SELECT close FROM price_data WHERE ticker = 'GUARD' AND date >= :d ORDER BY date"),
            {"d": datetime(2016, 6, 15)},
        ).fetchall()
    assert [r[0] for r in after] == before


# ---------------------------------------------------------------------------
# _yf_symbol() / _fetch_ticker() -- dot vs. hyphen share-class notation
# ---------------------------------------------------------------------------
#
# yfinance expects hyphen notation (BRK-B) for share classes, not the dot
# notation (BRK.B) this codebase uses everywhere else to match
# universe_membership / the Wikipedia-style source CSV. Confirmed live
# (2026-08-05): yf.download("BRK.B") returns zero rows every time
# ("possibly delisted"); yf.download("BRK-B") returns real data. Both real
# S&P 500 constituents (BRK.B, BF.B) were silently frozen in production
# because pull_incremental()'s daily period="10d" fetch passed the raw dot
# ticker straight to yfinance and got nothing back every time -- not
# flagged unavailable (an empty 10-day window is treated as "market may be
# closed", by design), just permanently stale.

def test_yf_symbol_converts_dot_to_hyphen():
    assert _yf_symbol("BRK.B") == "BRK-B"
    assert _yf_symbol("BF.B") == "BF-B"


def test_yf_symbol_leaves_plain_tickers_unchanged():
    assert _yf_symbol("AAPL") == "AAPL"
    assert _yf_symbol("^GSPC") == "^GSPC"


def test_fetch_ticker_calls_yfinance_with_hyphenated_symbol(mocker):
    """The DB-facing ticker stays dot-notation; only the outgoing yfinance
    call is converted."""
    df = _ohlcv_df({_BASE_DATE: 400.0})
    download = mocker.patch("data.ingestion.yf.download", return_value=df)

    result = _fetch_ticker("BRK.B", period="10d", max_retries=1, rate_limit=0)

    assert download.call_args.args[0] == "BRK-B"
    assert result is not None and not result.empty


def test_pull_incremental_stores_hyphen_fetch_under_original_dot_ticker(db_engine, mocker):
    """End-to-end: pull_incremental() for a dot-notation ticker must call
    yfinance with the hyphenated symbol, but the resulting rows must land
    in price_data under the original dot ticker -- matching
    universe_membership and every other reference to this ticker."""
    from data.ingestion import pull_incremental

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    _insert_rows(db_engine, "BRK.B", {_BASE_DATE - timedelta(days=1): 390.0})  # existing history -> period="10d" path
    df = _ohlcv_df({_BASE_DATE: 400.0})
    download = mocker.patch("data.ingestion.yf.download", return_value=df)

    results = pull_incremental(["BRK.B"])

    assert download.call_args.kwargs.get("period") == "10d"
    assert download.call_args.args[0] == "BRK-B"
    assert results["BRK.B"] == 1
    assert _stored_closes(db_engine, "BRK.B") == [390.0, 400.0]


# ---------------------------------------------------------------------------
# pull_history() / pull_incremental() -- fixed history_start_date anchor,
# not a rolling "now - N days" window (2026-08-05)
# ---------------------------------------------------------------------------
#
# now - history_days only approximated a fixed historical anchor (2007-01-01)
# at the moment history_days was chosen -- every day that passes after that
# moment, the same config value reaches back to a LATER date, not the same
# one. A fresh backfill run today must reach exactly as far back as one run
# a year from now, with no config change required; only a fixed
# data.history_start_date (not a day count) can guarantee that.

def test_pull_history_default_uses_fixed_start_date_not_relative_days(db_engine, mocker):
    """No explicit `days`/`start_date` override: pull_history() must anchor
    to config's fixed history_start_date, regardless of what "today" is —
    not compute a start via timedelta from datetime.utcnow()."""
    from data.ingestion import pull_history

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=None)

    pull_history(["AAPL"])

    assert fetch.call_args.args[1] == datetime(2007, 1, 1)  # start


def test_pull_history_explicit_days_override_still_relative(db_engine, mocker):
    """An explicit `days=` override (e.g. the DAX research script's
    `days=8000`) is a deliberate, genuinely relative request and must keep
    working exactly as before -- only the *default* (no override) changed."""
    from data.ingestion import pull_history

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    fixed_now = datetime(2030, 3, 15)
    mocker.patch("data.ingestion.datetime", wraps=datetime, **{"utcnow.return_value": fixed_now})
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=None)

    pull_history(["ADS.DE"], days=8000)

    assert fetch.call_args.args[1] == fixed_now - timedelta(days=8000)


def test_pull_history_explicit_start_date_override(db_engine, mocker):
    """An explicit start_date= override takes precedence over config's
    default history_start_date."""
    from data.ingestion import pull_history

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=None)

    pull_history(["AAPL"], start_date="1999-06-01")

    assert fetch.call_args.args[1] == datetime(1999, 6, 1)


def test_pull_incremental_first_time_download_uses_fixed_start_date(db_engine, mocker):
    """A brand-new ticker with no stored data at all (e.g. a freshly
    discovered S&P 500 entrant) must be fetched from the fixed
    history_start_date, not from a rolling now-minus-days window that
    reaches less far back the later this code happens to run."""
    from data.ingestion import pull_incremental

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=None)

    pull_incremental(["BRANDNEWCO"])

    assert fetch.call_args.kwargs["start"] == datetime(2007, 1, 1)


# ---------------------------------------------------------------------------
# init_db() no longer purges non-watchlist tickers from price_data
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _find_retroactive_restatement() / pull_incremental() -- split-adjustment
# cliff detection (2026-08-06)
# ---------------------------------------------------------------------------
#
# yfinance's Close column is retroactively adjusted for stock splits even
# under auto_adjust=False. pull_incremental()'s rolling 10-day window means
# a ticker that splits after its history is already stored will bring in
# post-split-basis rows for the last ~10 days while everything older on disk
# stays on the pre-split basis -- a permanent, phantom cliff in the series
# that (unfixed) silently corrupts momentum/ATR for a year. These tests
# cover the fix: comparing incoming rows against what's already stored for
# the same (ticker, date) before upserting, and triggering a full-history
# re-fetch when they diverge.

def test_find_retroactive_restatement_returns_empty_when_no_overlap(db_engine):
    """No overlapping dates between incoming and stored -- nothing to compare."""
    _insert_rows(db_engine, "NOOVERLAP", {datetime(2026, 1, 1): 100.0})
    incoming = _ohlcv_df({datetime(2026, 2, 1): 105.0})
    assert _find_retroactive_restatement(incoming, "NOOVERLAP") == []


def test_find_retroactive_restatement_returns_empty_within_tolerance(db_engine):
    """A small vendor-revision-sized divergence (well under 1%) must not
    trigger a false-positive restatement."""
    _insert_rows(db_engine, "TINYDIFF", {datetime(2026, 1, 1): 100.00})
    incoming = _ohlcv_df({datetime(2026, 1, 1): 100.05})  # 0.05% diff
    assert _find_retroactive_restatement(incoming, "TINYDIFF") == []


def test_find_retroactive_restatement_detects_split_sized_divergence(db_engine):
    """A stored pre-split close vs. an incoming post-split-basis close for
    the SAME date -- the exact signature of the bug -- must be flagged."""
    _insert_rows(db_engine, "SPLIT4TO1", {datetime(2026, 1, 1): 400.0})
    incoming = _ohlcv_df({datetime(2026, 1, 1): 100.0})  # 4:1 split retroactively applied
    hits = _find_retroactive_restatement(incoming, "SPLIT4TO1")
    assert len(hits) == 1
    date_iso, stored_close, new_close = hits[0]
    assert date_iso == "2026-01-01"
    assert stored_close == 400.0
    assert new_close == 100.0


def test_find_retroactive_restatement_ignores_non_overlapping_dates_in_same_call(db_engine):
    """Only dates present in BOTH stored and incoming are compared -- a
    genuinely new date in the incoming pull is not itself a "divergence"."""
    _insert_rows(db_engine, "MIXED", {datetime(2026, 1, 1): 100.0})
    incoming = _ohlcv_df({
        datetime(2026, 1, 1): 100.02,   # overlapping, within tolerance
        datetime(2026, 1, 2): 999.0,    # new date, no stored value to compare against
    })
    assert _find_retroactive_restatement(incoming, "MIXED") == []


def test_pull_incremental_triggers_full_refetch_on_restatement(db_engine, mocker):
    """End-to-end: a 10-day pull whose overlapping dates diverge from stored
    price_data by more than the tolerance must trigger a full-history
    re-fetch and replace, not the normal upsert of just the 10-day window."""
    from data.ingestion import pull_incremental

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    # Existing stored history on the OLD (pre-split) basis.
    _insert_rows(db_engine, "SPLITTER", {
        datetime(2007, 1, 3): 4000.0,
        datetime(2025, 12, 20): 400.0,
    })

    ten_day_df = _ohlcv_df({datetime(2025, 12, 20): 100.0, datetime(2025, 12, 22): 101.0})
    full_history_df = _ohlcv_df({
        datetime(2007, 1, 3): 1000.0,   # whole history re-based post-split
        datetime(2025, 12, 20): 100.0,
        datetime(2025, 12, 22): 101.0,
    })

    def fetch_side_effect(ticker, *args, **kwargs):
        if kwargs.get("period") == "10d":
            return ten_day_df
        return full_history_df

    fetch = mocker.patch("data.ingestion._fetch_ticker", side_effect=fetch_side_effect)

    results = pull_incremental(["SPLITTER"])

    # Both the 10-day probe AND a full-history re-fetch must have happened.
    assert fetch.call_count == 2
    full_calls = [c for c in fetch.call_args_list if c.kwargs.get("period") != "10d"]
    assert len(full_calls) == 1
    assert full_calls[0].kwargs["start"] == datetime(2007, 1, 1)

    # The entire stored history must now be on the new (post-split) basis --
    # not just the last 10 days, and the old pre-split value must be gone.
    assert results["SPLITTER"] == 3
    assert _stored_closes(db_engine, "SPLITTER") == [1000.0, 100.0, 101.0]


def test_pull_incremental_no_restatement_uses_normal_10day_upsert(db_engine, mocker):
    """The common case (no restatement) must still take the cheap path --
    only the 10-day fetch, no full re-fetch triggered."""
    from data.ingestion import pull_incremental

    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value={
        "data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"},
    })
    _insert_rows(db_engine, "NORMAL", {datetime(2025, 12, 20): 100.0})
    ten_day_df = _ohlcv_df({datetime(2025, 12, 20): 100.01, datetime(2025, 12, 22): 101.0})
    fetch = mocker.patch("data.ingestion._fetch_ticker", return_value=ten_day_df)

    results = pull_incremental(["NORMAL"])

    fetch.assert_called_once()  # no second, full-history call
    assert results["NORMAL"] == 2
    assert _stored_closes(db_engine, "NORMAL") == [100.01, 101.0]


# ---------------------------------------------------------------------------
# detect_price_cliffs() -- the full-table backstop scan (2026-08-06),
# called from the eod scheduled job. Read-only; a different function from
# _find_retroactive_restatement() (which compares an incoming pull against
# what's already stored), this one re-scans whatever is ALREADY in
# price_data end to end -- catches a cliff introduced by a write path other
# than pull_incremental(), e.g. backfill_price_history() or a manual script.
# ---------------------------------------------------------------------------

def test_detect_price_cliffs_empty_table_returns_empty(db_engine):
    assert detect_price_cliffs() == []


def test_detect_price_cliffs_no_hits_on_ordinary_series(db_engine):
    _insert_rows(db_engine, "NORMAL", {
        datetime(2026, 1, 1): 100.0,
        datetime(2026, 1, 2): 101.0,
        datetime(2026, 1, 3): 99.5,
    })
    assert detect_price_cliffs() == []


def test_detect_price_cliffs_flags_a_split_sized_ratio_break(db_engine):
    _insert_rows(db_engine, "CLIFFED", {
        datetime(2026, 1, 1): 400.0,
        datetime(2026, 1, 2): 100.0,   # a 4:1-sized overnight cliff
        datetime(2026, 1, 3): 101.0,
    })
    hits = detect_price_cliffs()
    assert len(hits) == 1
    hit = hits[0]
    assert hit["ticker"] == "CLIFFED"
    assert hit["date_prev"] == "2026-01-01"
    assert hit["date"] == "2026-01-02"
    assert hit["close_prev"] == 400.0
    assert hit["close"] == 100.0
    assert hit["ratio"] == pytest.approx(0.25)


def test_detect_price_cliffs_ignores_moves_within_the_ratio_band(db_engine):
    """A real, large-but-plausible single-day move (e.g. a genuine ~40%
    crash) inside [0.5, 2.0] must not be flagged -- only breaks OUTSIDE the
    band are split-sized."""
    _insert_rows(db_engine, "BIGMOVE", {
        datetime(2026, 1, 1): 100.0,
        datetime(2026, 1, 2): 60.0,  # ratio 0.6 -- large but within band
    })
    assert detect_price_cliffs() == []


def test_detect_price_cliffs_scans_across_multiple_tickers_independently(db_engine):
    _insert_rows(db_engine, "CLEAN", {datetime(2026, 1, 1): 50.0, datetime(2026, 1, 2): 51.0})
    _insert_rows(db_engine, "DIRTY", {datetime(2026, 1, 1): 200.0, datetime(2026, 1, 2): 50.0})
    hits = detect_price_cliffs()
    assert [h["ticker"] for h in hits] == ["DIRTY"]


# ---------------------------------------------------------------------------
# filter_known_price_cliff_exceptions() (2026-08-08) -- suppresses the 14
# individually-classified (ticker, date) pairs from research/split_cliff_
# remediation_2026-08-08/ so an already-investigated cliff stops re-firing
# the daily alert. Does NOT touch detect_price_cliffs() itself or the
# live-scoping filter -- called by the EOD job strictly after both.
# ---------------------------------------------------------------------------

def test_filter_known_exceptions_removes_a_known_pair():
    hits = [{"ticker": "AIG", "date_prev": "2008-09-12", "date": "2008-09-15",
             "close_prev": 242.80, "close": 95.20, "ratio": 0.392}]
    assert filter_known_price_cliff_exceptions(hits) == []


def test_filter_known_exceptions_covers_all_four_categories():
    """One representative from each of the four classification buckets --
    all four must be suppressed, not just the largest bucket."""
    hits = [
        {"ticker": "AIG", "date": "2008-09-15"},          # GENUINE_MARKET_MOVE
        {"ticker": "KDP", "date": "2018-07-10"},           # CORPORATE_ACTION_UNREPAIRABLE
        {"ticker": "EXPE", "date": "2011-12-21"},          # PARTIALLY_RESOLVED
        {"ticker": "JCI", "date": "2007-07-02"},           # UNRESOLVED
    ]
    assert filter_known_price_cliff_exceptions(hits) == []


def test_filter_known_exceptions_leaves_unrelated_hits_untouched():
    """A hit for a known ticker on a DIFFERENT date, or an entirely
    different ticker, must still alert normally -- the suppression is
    scoped to the exact (ticker, date) pair, not the whole ticker."""
    hits = [
        {"ticker": "AIG", "date": "2008-09-15"},   # known exception -- suppressed
        {"ticker": "AIG", "date": "2026-01-05"},   # same ticker, different date -- NOT suppressed
        {"ticker": "NEWCO", "date": "2026-01-05"},  # unrelated ticker -- NOT suppressed
    ]
    result = filter_known_price_cliff_exceptions(hits)
    assert result == [
        {"ticker": "AIG", "date": "2026-01-05"},
        {"ticker": "NEWCO", "date": "2026-01-05"},
    ]


def test_filter_known_exceptions_all_14_are_present():
    """Every ticker+date from the Phase 1 classification is actually
    registered -- guards against a typo silently leaving one of the 14
    still alerting."""
    expected = {
        ("AIG", "2008-09-15"), ("APA", "2020-03-09"), ("GL", "2024-04-11"),
        ("HIG", "2008-10-30"), ("HIG", "2008-12-05"), ("OXY", "2020-03-09"),
        ("PCG", "2019-01-14"), ("STT", "2009-01-20"), ("TRGP", "2020-03-09"),
        ("KDP", "2018-07-10"), ("MO", "2008-03-31"), ("WY", "2010-07-20"),
        ("EXPE", "2011-12-21"), ("JCI", "2007-07-02"),
    }
    hits = [{"ticker": t, "date": d} for t, d in expected]
    assert filter_known_price_cliff_exceptions(hits) == []
    assert len(expected) == 14


def test_filter_known_exceptions_empty_input_returns_empty():
    assert filter_known_price_cliff_exceptions([]) == []


def test_init_db_does_not_delete_non_watchlist_ticker_price_data(db_engine):
    """_purge_unlisted_tickers() was removed 2026-08-01 (see CLAUDE.md's
    "price_data purge removed" section) — permanent, traceable storage is
    now the deliberate design, so a research-only ticker's price history
    must survive init_db() indefinitely, not just until the next
    restart/deploy. This is the direct, positive guarantee the removal
    exists to provide — a ticker with no config.yaml/ALL_TICKERS presence
    at all is exactly the case the old purge would have deleted."""
    from data.database import init_db

    _insert_rows(db_engine, "NOTINWATCHLIST", {datetime(2020, 1, 2): 42.0, datetime(2020, 1, 3): 43.0})

    init_db()

    assert _stored_closes(db_engine, "NOTINWATCHLIST") == [42.0, 43.0]


# ---------------------------------------------------------------------------
# verify_ticker_identity() (2026-08-07) -- see research/recycled_ticker_
# investigation_2026-08-07/REPORT.md and its follow-on remediation. Two
# checks, NOT symmetric evidence: Check B (date-coverage overrun) is
# authoritative on its own UNLESS Check A affirmatively confirms a real,
# named equity (the FNMA fix -- see verify_ticker_identity()'s own
# docstring for why the naive "Check B alone is always sufficient" version
# was wrong, discovered directly against FNMA during implementation).
# ---------------------------------------------------------------------------

_CONFIG = {"data": {"rate_limit_seconds": 0, "max_retries": 1, "history_start_date": "2007-01-01"}}


def _insert_membership_window(engine, ticker: str, start, end) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO universe_membership (ticker, start_date, end_date, source, fetched_at) "
                "VALUES (:t, :s, :e, 'test', :fa)"
            ),
            {"t": ticker, "s": start.isoformat(), "e": end.isoformat(), "fa": datetime.utcnow().isoformat()},
        )


def _mock_yf_info(mocker, quote_type, exchange, long_name, raises=False):
    """Mock yf.Ticker(...).info -- matches the .info-lookup shape
    verify_ticker_identity() actually calls."""
    if raises:
        mocker.patch("data.ingestion.yf.Ticker", side_effect=RuntimeError("possibly delisted"))
        return
    mock_ticker = mocker.Mock()
    mock_ticker.info = {"quoteType": quote_type, "exchange": exchange, "longName": long_name}
    mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)


def test_verify_identity_clean_when_both_checks_pass(db_engine, mocker):
    """Real equity, membership window never closed (still an active
    candidate) -- Check B not applicable, Check A passes -> CLEAN."""
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_membership_window(db_engine, "GOODCO", datetime(2007, 1, 1).date(), _SENTINEL_END)
    _mock_yf_info(mocker, "EQUITY", "NMS", "Good Company Inc.")

    result = verify_ticker_identity("GOODCO", _CONFIG)

    assert result["status"] == "CLEAN"
    assert result["sentinel_case"] is True


def test_verify_identity_palm_shape_quarantined(db_engine, mocker):
    """PALM's real shape: stored price_data extends years past the real
    membership end_date, AND quoteType gives nothing to rescue it with
    (MUTUALFUND/YHD/None) -- QUARANTINED, exactly the case this whole
    mechanism exists to catch."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_membership_window(db_engine, "PALMLIKE", datetime(2000, 1, 1).date(), datetime(2002, 8, 9).date())
    _insert_rows(db_engine, "PALMLIKE", {datetime(2014, 12, 22): 500.0, datetime(2020, 1, 8): 0.15})
    _mock_yf_info(mocker, "MUTUALFUND", "YHD", None)

    result = verify_ticker_identity("PALMLIKE", _CONFIG)

    assert result["status"] == "QUARANTINED"
    assert result["evidence"]["date_overrun_days"] > _DATE_OVERRUN_TOLERANCE_DAYS
    assert result["evidence"]["boundary_gap_days"] is None  # no data at all near the real boundary


def test_verify_identity_fnma_shape_needs_review_not_quarantined(db_engine, mocker):
    """FNMA's real shape: stored price_data extends years past the real
    membership end_date (real conservatorship, real index removal), BUT
    quoteType affirmatively confirms a real, named equity still trading
    (continuous OTC trading under its own name) -- NEEDS_REVIEW, not an
    automatic QUARANTINE. This is the exact case that was wrong before the
    override was added (discovered by testing the rule against a ticker it
    should NOT fire on -- see verify_ticker_identity()'s own docstring)."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_membership_window(db_engine, "FNMALIKE", datetime(2000, 1, 1).date(), datetime(2008, 9, 8).date())
    _insert_rows(db_engine, "FNMALIKE", {
        datetime(2008, 9, 8): 1.0,
        datetime(2008, 9, 9): 1.5,  # trading continues right through the boundary -- boundary_gap_days=1
        datetime(2026, 8, 6): 1.54,
    })
    _mock_yf_info(mocker, "EQUITY", "OQB", "Federal National Mortgage Association")

    result = verify_ticker_identity("FNMALIKE", _CONFIG)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["evidence"]["date_overrun_days"] > _DATE_OVERRUN_TOLERANCE_DAYS
    assert result["evidence"]["boundary_gap_days"] == 1  # continuous trading through the boundary


def test_verify_identity_date_overrun_boundary_inclusive_at_90_days(db_engine, mocker):
    """Exactly 90 days overrun does not fail Check B; 91 does. Check A is
    held constant (failing, MUTUALFUND/YHD/None -- no affirmative-equity
    override available) throughout so the status transition
    (NEEDS_REVIEW vs QUARANTINED) isolates Check B's own boundary rather
    than being muddied by the FNMA-override interacting with it too."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _mock_yf_info(mocker, "MUTUALFUND", "YHD", None)

    _insert_membership_window(db_engine, "EXACTLY90", datetime(2000, 1, 1).date(), datetime(2020, 1, 1).date())
    _insert_rows(db_engine, "EXACTLY90", {datetime(2020, 4, 1): 10.0})  # exactly 91 calendar days later
    result_91 = verify_ticker_identity("EXACTLY90", _CONFIG)
    assert result_91["evidence"]["date_overrun_days"] == 91
    assert result_91["status"] == "QUARANTINED"  # Check B fails -> QUARANTINED (Check A affirms nothing)

    clear_membership_cache()
    _insert_membership_window(db_engine, "EXACTLY89", datetime(2000, 1, 1).date(), datetime(2020, 1, 1).date())
    _insert_rows(db_engine, "EXACTLY89", {datetime(2020, 3, 31): 10.0})  # exactly 90 calendar days later
    result_90 = verify_ticker_identity("EXACTLY89", _CONFIG)
    assert result_90["evidence"]["date_overrun_days"] == 90
    assert result_90["status"] == "NEEDS_REVIEW"  # Check B passes (90 not > 90); Check A alone (failing) -> NEEDS_REVIEW, not QUARANTINED


def test_verify_identity_sentinel_case_decided_by_check_a_alone(db_engine, mocker):
    """A membership window that never closed -- Check B genuinely does not
    apply (nothing to compare against), so a bad quoteType alone is
    sufficient to quarantine even with zero date-overrun evidence."""
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_membership_window(db_engine, "STILLACTIVE", datetime(2007, 1, 1).date(), _SENTINEL_END)
    _mock_yf_info(mocker, "MUTUALFUND", "YHD", None)

    result = verify_ticker_identity("STILLACTIVE", _CONFIG)

    assert result["sentinel_case"] is True
    assert result["evidence"]["check_b_applicable"] is False
    assert result["status"] == "QUARANTINED"


def test_verify_identity_info_unavailable_is_needs_review_not_quarantined(db_engine, mocker):
    """yfinance failing outright must never auto-quarantine a ticker --
    an operational failure is not evidence of corruption."""
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_membership_window(db_engine, "APIFAIL", datetime(2007, 1, 1).date(), _SENTINEL_END)
    _mock_yf_info(mocker, None, None, None, raises=True)

    result = verify_ticker_identity("APIFAIL", _CONFIG)

    assert result["status"] == "NEEDS_REVIEW"


def test_verify_identity_empty_info_is_needs_review_not_quarantined(db_engine, mocker):
    """2026-08-08 fix, found via IAC: yfinance gracefully handling a
    not-found lookup (logging its own HTTP 404 internally, returning an
    EMPTY dict rather than raising) must be treated the same as an
    unavailable/errored .info call -- NOT scored as positive evidence of a
    non-equity identity. Before this fix, quote_type/exchange/long_name
    all being None (with info_error also None, since no exception was
    raised) fell through to the same branch as a real, populated,
    confirmed-different-entity response and auto-quarantined on zero
    evidence."""
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_membership_window(db_engine, "GONE404", datetime(2007, 1, 1).date(), _SENTINEL_END)
    _mock_yf_info(mocker, None, None, None)  # empty dict, no exception -- the real IAC shape

    result = verify_ticker_identity("GONE404", _CONFIG)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["evidence"]["info_error"] is None  # confirms this is the no-exception path, not APIFAIL's


def test_verify_identity_populated_non_equity_still_quarantines(db_engine, mocker):
    """The info_empty fix must be narrow: a REAL, populated response with a
    non-equity quote_type (PALM/PDG/RX's own already-validated MUTUALFUND/
    YHD/None shape -- exchange='YHD' specifically, not None) is positive
    evidence of a different identity, not silence, and must keep
    quarantining exactly as before the fix."""
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_membership_window(db_engine, "RECYCLED", datetime(2007, 1, 1).date(), _SENTINEL_END)
    _mock_yf_info(mocker, "MUTUALFUND", "YHD", None)

    result = verify_ticker_identity("RECYCLED", _CONFIG)

    assert result["status"] == "QUARANTINED"


def test_verify_identity_never_re_implemented_elsewhere():
    """Structural guard, matching this project's 'never a second
    implementation of the same threshold' discipline: backfill_price_
    history(), pull_incremental(), and scan_unverified_tickers() must all
    call verify_ticker_identity() itself, not re-derive the checks."""
    import inspect
    import data.ingestion as ing
    for fn in (ing._screen_newly_backfilled_ticker, ing.scan_unverified_tickers):
        src = inspect.getsource(fn)
        assert "verify_ticker_identity(" in src


# ---------------------------------------------------------------------------
# check_boundary_contiguity() (2026-08-07) -- Check C, NEEDS_REVIEW
# adjudication. Only meaningful for tickers verify_ticker_identity() has
# already routed to NEEDS_REVIEW (the FNMA pattern); see
# research/data_quality_remediation_2026-08-07/PHASE1_CONTIGUITY_REPORT.md
# for the full distribution and control validation this threshold (100
# missing NYSE trading days) came from.
# ---------------------------------------------------------------------------

def test_boundary_contiguity_fnma_shape_does_not_exceed_threshold(db_engine):
    """FNMA's real shape: trading continues right through the membership
    end_date (a 2008 conservatorship, not a stoppage) -- gap is tiny,
    must NOT exceed the threshold. This is the must-not-false-positive
    control the whole rule was validated against before being proposed."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_membership_window(db_engine, "FNMALIKE", datetime(2000, 1, 1).date(), datetime(2008, 9, 8).date())
    _insert_rows(db_engine, "FNMALIKE", {
        datetime(2008, 9, 8): 1.0,
        datetime(2008, 9, 9): 1.5,  # trading continues the very next session
        datetime(2026, 8, 6): 1.54,
    })

    result = check_boundary_contiguity("FNMALIKE")

    assert result["applicable"] is True
    assert result["gap_exceeds_threshold"] is False
    assert result["no_data_before_end_date_at_all"] is False


def test_boundary_contiguity_palm_shape_exceeds_threshold(db_engine):
    """PALM's real shape: the entire stored history postdates the real
    membership end_date by ~12 years -- no row at/before end_date exists
    at all. Must exceed the threshold; this is the must-catch control."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_membership_window(db_engine, "PALMLIKE", datetime(2000, 1, 1).date(), datetime(2002, 8, 9).date())
    _insert_rows(db_engine, "PALMLIKE", {datetime(2014, 12, 22): 500.0, datetime(2020, 1, 8): 0.15})

    result = check_boundary_contiguity("PALMLIKE")

    assert result["applicable"] is True
    assert result["gap_exceeds_threshold"] is True
    assert result["no_data_before_end_date_at_all"] is True
    assert result["gap_missing_trading_days"] > 100


def test_boundary_contiguity_not_applicable_when_no_data_after_end_date(db_engine):
    """A ticker with no price_data after its own membership end_date at
    all (nothing to compare the boundary against) -- not applicable, no
    verdict forced."""
    result = check_boundary_contiguity("NEVERINGESTED", "2010-01-01")
    assert result["applicable"] is False
    assert result["gap_exceeds_threshold"] is None


def test_boundary_contiguity_accepts_last_membership_end_directly(db_engine):
    """Callers (the Phase 2 adjudication script) pass the already-known
    last_membership_end from the existing evidence JSON rather than
    re-deriving it -- confirms the parameter is actually honoured, not
    silently ignored in favour of a fresh universe lookup."""
    _insert_rows(db_engine, "DIRECTARG", {datetime(2014, 12, 22): 500.0})
    result = check_boundary_contiguity("DIRECTARG", last_membership_end="2002-08-09")
    assert result["applicable"] is True
    assert result["last_membership_end"] == "2002-08-09"


def test_boundary_contiguity_never_writes_to_the_db(db_engine):
    """Pure read function -- callers decide what to do with the verdict
    (the Phase 2 script writes via write_data_quality_status() itself);
    this function must never touch ticker_data_quality directly."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_membership_window(db_engine, "READONLY", datetime(2000, 1, 1).date(), datetime(2002, 8, 9).date())
    _insert_rows(db_engine, "READONLY", {datetime(2014, 12, 22): 500.0})

    check_boundary_contiguity("READONLY")

    with db_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM ticker_data_quality WHERE ticker = 'READONLY'")).scalar()
    assert count == 0


# ---------------------------------------------------------------------------
# Ingestion-time screen -- backfill_price_history()'s no-prior-data branch
# and pull_incremental()'s first-time-download branch both call
# _screen_newly_backfilled_ticker() the moment a genuinely NEW ticker's
# full history is stored for the first time.
# ---------------------------------------------------------------------------

def test_backfill_screens_a_newly_added_ticker_and_quarantines_it(db_engine, mocker):
    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value=_CONFIG)
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_membership_window(db_engine, "NEWBAD", datetime(2000, 1, 1).date(), datetime(2010, 1, 1).date())
    df = _ohlcv_df({datetime(2007, 1, 3): 10.0, datetime(2026, 1, 1): 12.0})
    mocker.patch("data.ingestion._fetch_ticker", return_value=df)
    _mock_yf_info(mocker, "MUTUALFUND", "YHD", None)

    backfill_price_history(tickers=["NEWBAD"], start_date="2007-01-01")

    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM ticker_data_quality WHERE ticker = 'NEWBAD'")
        ).fetchone()
    assert row is not None and row[0] == "QUARANTINED"


def test_backfill_does_not_screen_a_ticker_that_already_had_data(db_engine, mocker):
    """Extending an already-tracked ticker's history further back is a
    different case from a genuinely new ticker -- must not re-screen (and
    must not require yfinance .info to be mocked at all, since it's never
    called)."""
    mocker.patch("data.ingestion.init_db")
    mocker.patch("data.ingestion._load_config", return_value=_CONFIG)
    _insert_rows(db_engine, "EXTEND", {datetime(2016, 6, 15): 100.0})
    df = _ohlcv_df({datetime(2007, 1, 3): 10.0})
    mocker.patch("data.ingestion._fetch_ticker", return_value=df)

    backfill_price_history(tickers=["EXTEND"], start_date="2007-01-01")

    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM ticker_data_quality WHERE ticker = 'EXTEND'")
        ).fetchone()
    assert row is None


# ---------------------------------------------------------------------------
# scan_unverified_tickers() -- the periodic backstop, folded into the eod
# job. Finds any ticker with price_data but no ticker_data_quality row.
# ---------------------------------------------------------------------------

def test_scan_unverified_tickers_checks_only_unscreened_tickers(db_engine, mocker):
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_rows(db_engine, "UNSCREENED", {datetime(2020, 1, 1): 10.0})
    _insert_membership_window(db_engine, "UNSCREENED", datetime(2007, 1, 1).date(), _SENTINEL_END)
    _insert_rows(db_engine, "ALREADYSCREENED", {datetime(2020, 1, 1): 10.0})
    from data.database import write_data_quality_status
    write_data_quality_status("ALREADYSCREENED", "CLEAN", "already checked", "{}")
    _mock_yf_info(mocker, "MUTUALFUND", "YHD", None)

    result = scan_unverified_tickers(_CONFIG)

    assert result["checked"] == 1  # only UNSCREENED, not ALREADYSCREENED
    assert "UNSCREENED" in result["newly_quarantined"]
    assert "ALREADYSCREENED" not in result["newly_quarantined"]


def test_scan_unverified_tickers_excludes_non_stock_tickers(db_engine, mocker):
    """2026-08-08 fix: verify_ticker_identity()'s Check A can only ever
    affirm quote_type == 'EQUITY', so an ETF/FX-pair/index ticker fails it
    by construction -- feeding one through is applying the check outside
    its domain, not a corner case it handles conservatively. Found live:
    the first real run of the pre-fix scan wrote QUARANTINED for ^GSPC (the
    crisis-trigger reference series) and ~33 ETF tickers. A ticker with no
    universe_membership row at all (the real signature of a non-stock
    ticker -- ETFs/FX/index series never get one) must be excluded from
    the scan entirely, not merely scored leniently."""
    from analysis.universe import clear_membership_cache
    clear_membership_cache()
    _insert_rows(db_engine, "^GSPC", {datetime(2020, 1, 1): 4000.0})  # no membership window -- not a stock
    _mock_yf_info(mocker, "INDEX", None, None)

    result = scan_unverified_tickers(_CONFIG)

    assert result["checked"] == 0
    with db_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM ticker_data_quality WHERE ticker = '^GSPC'")
        ).fetchone()
    assert row is None  # never written -- excluded before verify_ticker_identity() is ever called


def test_scan_unverified_tickers_never_raises_on_a_single_ticker_error(db_engine, mocker):
    from analysis.universe import clear_membership_cache, _SENTINEL_END
    clear_membership_cache()
    _insert_rows(db_engine, "BROKEN", {datetime(2020, 1, 1): 10.0})
    _insert_membership_window(db_engine, "BROKEN", datetime(2007, 1, 1).date(), _SENTINEL_END)
    mocker.patch("data.ingestion.yf.Ticker", side_effect=RuntimeError("boom"))

    result = scan_unverified_tickers(_CONFIG)

    # An .info error resolves to NEEDS_REVIEW inside verify_ticker_identity()
    # itself (not an exception this function needs to catch) -- but confirm
    # the batch completes regardless and the ticker is accounted for either
    # way, never silently dropped.
    assert result["checked"] == 1 or "BROKEN" in [e.split(":")[0] for e in result["errors"]]


# ---------------------------------------------------------------------------
# Sector classification (2026-08-09) -- fetch_ticker_sector()/get_or_fetch_sector().
# Deliberately separate from verify_ticker_identity()/scan_unverified_tickers()
# above -- see data/database.py:TickerSector's own docstring for why.
# ---------------------------------------------------------------------------

def test_fetch_ticker_sector_returns_sector_string(mocker):
    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {"sector": "Technology", "longName": "Some Corp"}
    mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)

    assert fetch_ticker_sector("AAPL") == "Technology"


def test_fetch_ticker_sector_missing_field_returns_none(mocker):
    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {"longName": "Some Corp"}  # no 'sector' key at all
    mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)

    assert fetch_ticker_sector("OBSCURE") is None


def test_fetch_ticker_sector_empty_info_returns_none(mocker):
    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {}
    mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)

    assert fetch_ticker_sector("DELISTED") is None


def test_fetch_ticker_sector_never_raises_on_exception(mocker):
    mocker.patch("data.ingestion.yf.Ticker", side_effect=RuntimeError("network boom"))

    assert fetch_ticker_sector("ANYTHING") is None


def test_get_or_fetch_sector_fetches_and_caches_on_first_call(db_engine, mocker):
    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {"sector": "Healthcare"}
    mock_yf = mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)

    result = get_or_fetch_sector("JNJ")

    assert result == "Healthcare"
    mock_yf.assert_called_once()

    from data.database import get_ticker_sector
    cached = get_ticker_sector("JNJ")
    assert cached["sector"] == "Healthcare"


def test_get_or_fetch_sector_second_call_does_not_refetch(db_engine, mocker):
    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {"sector": "Energy"}
    mock_yf = mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)

    first = get_or_fetch_sector("XOM")
    second = get_or_fetch_sector("XOM")

    assert first == second == "Energy"
    mock_yf.assert_called_once()  # not called again on the second lookup


def test_get_or_fetch_sector_caches_unresolved_and_does_not_refetch(db_engine, mocker):
    """A ticker yfinance genuinely has no sector for must not be re-queried
    on every subsequent lookup -- caching None is itself the point."""
    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {}
    mock_yf = mocker.patch("data.ingestion.yf.Ticker", return_value=mock_ticker)

    first = get_or_fetch_sector("DELISTEDCO")
    second = get_or_fetch_sector("DELISTEDCO")

    assert first is None
    assert second is None
    mock_yf.assert_called_once()

    from data.database import get_ticker_sector
    cached = get_ticker_sector("DELISTEDCO")
    assert cached is not None  # a row exists -- "attempted", not "never looked up"
    assert cached["sector"] is None
