"""Tests for catchup_fundamentals.py -- the one-off operator-driven pass that
brings ticker_fundamentals to full roster coverage without changing the nightly
rotation's own bounded chunk size.

catchup_fundamentals() is called directly rather than spawning a subprocess,
mirroring prewarm_sector_data.py's and check_eod_job_in_progress.py's own
convention.

THE TERMINATION TEST IS THE POINT OF THIS FILE. "Loop until
never_fetched_remaining == 0" does not terminate: a ticker returning no data has
no row written, so it stays never-fetched and sorts to the front of the next
chunk forever. BF.B and BRK.B are exactly this case in production (dotted form
stored, yfinance wants BF-B/BRK-B) and have been consuming 2 of the 25 nightly
slots since the recorder shipped. test_stops_when_a_chunk_covers_nothing_new is
the regression guard for that.
"""
from datetime import datetime, timezone

import pytest

from catchup_fundamentals import (_BLACKOUT_POLL_SECONDS, _in_blackout,
                                  catchup_fundamentals)


def _config():
    return {"data": {"rate_limit_seconds": 0.0}}  # no real sleep in tests


def _seed(db_engine, tickers):
    """Give each ticker one point_in_time row, so it is no longer never-fetched."""
    from data.ingestion import write_ticker_fundamentals
    for t in tickers:
        write_ticker_fundamentals(
            {"ticker": t, "reporting_period": "2026-06-30", "statement_type": "quarterly",
             "values": {"revenue": 1.0}, "field_aliases": {"revenue": "Total Revenue"},
             "period_by_statement": {"income": "2026-06-30"}},
            provenance="point_in_time")


# --------------------------------------------------------------------------
# Termination
# --------------------------------------------------------------------------

def test_stops_when_a_chunk_covers_nothing_new(db_engine, mocker):
    """The BF.B/BRK.B case: a ticker that returns no data writes no row, so it
    is selected again every chunk. Without the no-progress guard this loop runs
    until max_chunks, re-fetching the same dead tickers ~100 times."""
    mocker.patch("analysis.universe.get_live_stock_candidates",
                 return_value=["BF.B", "BRK.B"])
    fetch = mocker.patch("data.ingestion.fetch_ticker_fundamentals", return_value=None)

    r = catchup_fundamentals(_config(), max_chunks=50, verbose=False)

    assert r["chunks_run"] == 1, "must stop after the first chunk that gains nothing"
    assert r["stalled"] is True
    assert sorted(r["unresolvable"]) == ["BF.B", "BRK.B"]
    assert r["never_fetched_remaining"] == 2
    assert r["newly_covered"] == 0
    assert fetch.call_count == 2, "each dead ticker attempted exactly once, not once per chunk"


def test_runs_to_completion_when_every_ticker_resolves(db_engine, mocker):
    tickers = [f"T{i:03d}" for i in range(12)]
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=tickers)
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", side_effect=lambda t: {
        "ticker": t, "reporting_period": "2026-06-30", "statement_type": "quarterly",
        "values": {"revenue": 1.0}, "field_aliases": {}, "period_by_statement": {}})

    r = catchup_fundamentals(_config(), chunk_size=5, verbose=False)

    assert r["never_fetched_remaining"] == 0
    assert r["newly_covered"] == 12
    assert r["chunks_run"] == 3, "12 tickers at 5/chunk"
    assert r["stalled"] is False


def test_partial_no_data_still_makes_progress_and_finishes(db_engine, mocker):
    """A mix -- the real production shape. The dead tickers are attempted every
    chunk (they sort first) but the loop still terminates, because each chunk
    covers new ground until only the dead ones remain."""
    tickers = ["AA.B", "BB.B"] + [f"T{i:02d}" for i in range(6)]
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=tickers)
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", side_effect=lambda t: None
                 if t.endswith(".B") else {
                     "ticker": t, "reporting_period": "2026-06-30",
                     "statement_type": "quarterly", "values": {"revenue": 1.0},
                     "field_aliases": {}, "period_by_statement": {}})

    r = catchup_fundamentals(_config(), chunk_size=4, max_chunks=20, verbose=False)

    assert r["newly_covered"] == 6
    assert r["never_fetched_remaining"] == 2
    assert r["stalled"] is True
    assert sorted(r["unresolvable"]) == ["AA.B", "BB.B"]
    assert r["chunks_run"] <= 5, "must not spin on the two dead tickers"


def test_already_complete_roster_does_no_work_at_all(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAA", "BBB"])
    _seed(db_engine, ["AAA", "BBB"])
    fetch = mocker.patch("data.ingestion.fetch_ticker_fundamentals")

    r = catchup_fundamentals(_config(), verbose=False)

    assert r["chunks_run"] == 0
    assert r["never_fetched_at_start"] == 0
    fetch.assert_not_called()


def test_max_chunks_is_a_hard_cap(db_engine, mocker):
    tickers = [f"T{i:03d}" for i in range(40)]
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=tickers)
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", side_effect=lambda t: {
        "ticker": t, "reporting_period": "2026-06-30", "statement_type": "quarterly",
        "values": {"revenue": 1.0}, "field_aliases": {}, "period_by_statement": {}})

    r = catchup_fundamentals(_config(), chunk_size=5, max_chunks=2, verbose=False)

    assert r["chunks_run"] == 2
    assert r["newly_covered"] == 10
    assert r["never_fetched_remaining"] == 30


def test_a_chunk_error_stops_the_loop_and_is_reported(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates",
                 return_value=[f"T{i:03d}" for i in range(20)])
    mocker.patch("data.ingestion.refresh_fundamentals_chunk",
                 return_value={"attempted": 0, "written": 0, "no_data": 0,
                               "error": "RuntimeError: boom"})

    r = catchup_fundamentals(_config(), chunk_size=5, max_chunks=20, verbose=False)

    assert r["chunks_run"] == 1
    assert r["error"] == "RuntimeError: boom"
    assert r["never_fetched_remaining"] == 20


# --------------------------------------------------------------------------
# Provenance -- this script must never produce 'restated' rows
# --------------------------------------------------------------------------

def test_only_point_in_time_rows_are_written(db_engine, mocker):
    from sqlalchemy import text

    from data.database import get_engine
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAA", "BBB"])
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", side_effect=lambda t: {
        "ticker": t, "reporting_period": "2026-06-30", "statement_type": "quarterly",
        "values": {"revenue": 1.0}, "field_aliases": {}, "period_by_statement": {}})

    catchup_fundamentals(_config(), verbose=False)

    with get_engine().connect() as conn:
        provs = {r[0] for r in conn.execute(text(
            "SELECT DISTINCT provenance FROM ticker_fundamentals"))}
    assert provs == {"point_in_time"}


def test_the_backfill_entry_point_is_never_called(db_engine, mocker):
    """Backfill writes 'restated' rows, which can never be pooled with forward
    rows in an analysis. Accelerating coverage must not quietly do it."""
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAA"])
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", return_value={
        "ticker": "AAA", "reporting_period": "2026-06-30", "statement_type": "quarterly",
        "values": {"revenue": 1.0}, "field_aliases": {}, "period_by_statement": {}})
    backfill = mocker.patch("data.ingestion.backfill_fundamentals_chunk")

    catchup_fundamentals(_config(), verbose=False)

    backfill.assert_not_called()


# --------------------------------------------------------------------------
# Blackout windows
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hh,mm,expected", [
    (20, 59, None), (21, 0, "21:00-22:00 UTC"), (21, 30, "21:00-22:00 UTC"),
    (21, 59, "21:00-22:00 UTC"), (22, 0, None),
    (14, 14, None), (14, 15, "14:15-14:45 UTC"), (14, 44, "14:15-14:45 UTC"), (14, 45, None),
    (3, 0, None), (12, 0, None),
])
def test_blackout_window_boundaries(hh, mm, expected):
    assert _in_blackout(datetime(2026, 8, 24, hh, mm, tzinfo=timezone.utc)) == expected


def test_a_chunk_is_not_started_inside_a_blackout_window(db_engine, mocker):
    """The EOD window is the only other thing in this system that fetches from
    yfinance, so overlapping it is the one genuine contention risk. The script
    waits rather than aborting, so an overnight run survives one."""
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAA"])
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", return_value={
        "ticker": "AAA", "reporting_period": "2026-06-30", "statement_type": "quarterly",
        "values": {"revenue": 1.0}, "field_aliases": {}, "period_by_statement": {}})
    # blackout for the first two checks, then clear
    mocker.patch("catchup_fundamentals._in_blackout",
                 side_effect=["21:00-22:00 UTC", "21:00-22:00 UTC", None, None, None])
    sleep = mocker.patch("catchup_fundamentals.time.sleep")

    r = catchup_fundamentals(_config(), verbose=False)

    assert r["blackout_waits"] == 2
    # Count the BLACKOUT sleeps specifically: patching catchup_fundamentals.time.sleep
    # patches the shared time module, so refresh_fundamentals_chunk()'s own per-ticker
    # rate-limit sleep is intercepted here too and a bare call_count would conflate them.
    blackout_sleeps = [c for c in sleep.call_args_list if c.args == (_BLACKOUT_POLL_SECONDS,)]
    assert len(blackout_sleeps) == 2
    assert r["newly_covered"] == 1, "work still completes once the window clears"
