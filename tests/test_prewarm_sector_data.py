"""Tests for prewarm_sector_data.py -- the standalone script that fetches
and caches sectors for the full live candidate pool, so the Dashboard/
weekly-email render path (a pure DB read, see output/performance.py:
compute_sector_breakdown()'s own docstring) never has to fetch from
yfinance itself.

main()/prewarm_sector_data() called directly rather than spawning a real
subprocess, mirroring check_eod_job_in_progress.py's own convention.
"""
from prewarm_sector_data import prewarm_sector_data


def _config():
    return {"data": {"rate_limit_seconds": 0.0}}  # no real sleep in tests


def test_already_cached_tickers_are_skipped_not_refetched(db_engine, mocker):
    from data.database import write_ticker_sector
    write_ticker_sector("AAA", "Technology")

    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAA"])
    mock_fetch = mocker.patch("data.ingestion.fetch_ticker_sector")

    result = prewarm_sector_data(_config())

    mock_fetch.assert_not_called()
    assert result["already_cached"] == 1
    assert result["fetched"] == 0


def test_new_ticker_is_fetched_and_written(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["BBB"])
    mocker.patch("data.ingestion.fetch_ticker_sector", return_value="Healthcare")

    result = prewarm_sector_data(_config())

    assert result["fetched"] == 1
    assert result["resolved"] == 1
    assert result["unresolved"] == 0

    from data.database import get_ticker_sector
    assert get_ticker_sector("BBB")["sector"] == "Healthcare"


def test_unresolvable_ticker_still_writes_a_row(db_engine, mocker):
    """A ticker yfinance has no sector for must still get a row written
    (sector=NULL) -- counted as 'unresolved', not an error, and not
    re-fetched on a subsequent run."""
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["CCC"])
    mocker.patch("data.ingestion.fetch_ticker_sector", return_value=None)

    result = prewarm_sector_data(_config())

    assert result["fetched"] == 1
    assert result["resolved"] == 0
    assert result["unresolved"] == 1
    assert result["errors"] == []

    from data.database import get_ticker_sector
    cached = get_ticker_sector("CCC")
    assert cached is not None
    assert cached["sector"] is None


def test_per_ticker_exception_does_not_stop_the_batch(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["BROKEN", "FINE"])

    def _fetch(ticker):
        if ticker == "BROKEN":
            raise RuntimeError("boom")
        return "Energy"

    mocker.patch("data.ingestion.fetch_ticker_sector", side_effect=_fetch)

    result = prewarm_sector_data(_config())

    assert len(result["errors"]) == 1
    assert "BROKEN" in result["errors"][0]
    assert result["fetched"] == 1  # FINE still processed
    assert result["resolved"] == 1

    from data.database import get_ticker_sector
    assert get_ticker_sector("FINE")["sector"] == "Energy"
    assert get_ticker_sector("BROKEN") is None  # never written -- the exception fired before the write


def test_rate_limit_sleep_only_spent_on_genuine_fetches(db_engine, mocker):
    """A rerun where most tickers are already cached must not spend the
    rate-limit sleep on the cache hits -- only on tickers actually fetched."""
    from data.database import write_ticker_sector
    write_ticker_sector("CACHED1", "Technology")
    write_ticker_sector("CACHED2", "Technology")

    mocker.patch(
        "analysis.universe.get_live_stock_candidates",
        return_value=["CACHED1", "CACHED2", "NEWONE"],
    )
    mocker.patch("data.ingestion.fetch_ticker_sector", return_value="Utilities")
    mock_sleep = mocker.patch("prewarm_sector_data.time.sleep")

    result = prewarm_sector_data({"data": {"rate_limit_seconds": 2.5}})

    assert result["already_cached"] == 2
    assert result["fetched"] == 1
    mock_sleep.assert_called_once_with(2.5)


def test_reports_total_matching_live_candidate_pool_size(db_engine, mocker):
    mocker.patch(
        "analysis.universe.get_live_stock_candidates",
        return_value=["A", "B", "C"],
    )
    mocker.patch("data.ingestion.fetch_ticker_sector", return_value="Industrials")

    result = prewarm_sector_data(_config())

    assert result["total"] == 3
    assert "elapsed_seconds" in result


def test_empty_candidate_pool_completes_cleanly(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=[])

    result = prewarm_sector_data(_config())

    assert result["total"] == 0
    assert result["fetched"] == 0
    assert result["errors"] == []
