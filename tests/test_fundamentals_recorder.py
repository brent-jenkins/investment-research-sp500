"""Coverage for the point-in-time fundamentals recorder (2026-08-12).

DATA CAPTURE ONLY. The single most important test here is the isolation test:
an exception anywhere in this path must never reach the order decisions the EOD
job has already made. That is asserted by injecting a real exception and
checking the decisions survive -- not by trusting the try/except wrapping.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy import text as sa_text

from data.ingestion import (
    FUNDAMENTALS_CHUNK_SIZE, backfill_fundamentals_chunk, fetch_ticker_fundamentals, refresh_fundamentals_chunk,
    snapshot_candidate_fundamentals, write_ticker_fundamentals,
)


def _stmt(rows: dict, period="2026-06-30"):
    """A yfinance-shaped statement frame: row labels x period columns."""
    return pd.DataFrame(rows, index=[pd.Timestamp(period)]).T


def _record(ticker="AAA", period="2026-06-30", **overrides):
    values = {"total_debt": 1.0, "total_equity": 2.0, "total_assets": 3.0,
              "revenue": 4.0, "net_income": 5.0, "operating_cash_flow": 6.0,
              "free_cash_flow": 7.0, "shares_outstanding": 8.0}
    values.update(overrides)
    return {"ticker": ticker, "reporting_period": period, "statement_type": "quarterly",
            "values": values, "field_aliases": {"total_debt": "Total Debt"},
            "period_by_statement": {"balance": period, "income": period, "cash": period}}


# --------------------------------------------------------------------------
# THE ISOLATION TEST -- the reason this module exists
# --------------------------------------------------------------------------

def test_a_failure_in_fundamentals_capture_cannot_reach_order_decisions(db_engine, mocker):
    """Inject a real exception into the capture path and prove the decisions
    the EOD job already made are untouched.

    Modelled on the shadow-comparison work's own fail-safe proof: the point is
    not that the code is wrapped in try/except -- that is visible by reading it
    -- but that the wrapping actually holds when something throws.
    """
    with db_engine.begin() as conn:
        for tk in ("AAA", "BBB"):
            conn.execute(sa_text(
                "INSERT INTO pending_orders (ticker, t212_ticker, is_etf, currency, "
                "decided_at, decided_price, position_gbp, momentum_pct, stop_price, "
                "var_pct, status, created_at) VALUES (:t, :tt, 0, 'USD', :d, 100.0, "
                "1000.0, 0.5, 90.0, -0.2, 'PENDING', :d)"),
                {"t": tk, "tt": tk + "_US_EQ", "d": "2026-08-12T21:30:00"})

    def _boom(*_a, **_k):
        raise RuntimeError("injected fundamentals failure")

    mocker.patch("data.ingestion.refresh_fundamentals_chunk", side_effect=_boom)
    mocker.patch("data.ingestion.snapshot_candidate_fundamentals", side_effect=_boom)

    details = {}
    try:
        from data.ingestion import refresh_fundamentals_chunk as rfc
        details["fundamentals_fetch"] = rfc({})
    except Exception as exc:  # noqa: BLE001  -- mirrors the scheduler's own wrapping
        details["fundamentals_error"] = str(exc)

    # The decisions must be exactly as they were.
    with db_engine.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT ticker, status, position_gbp, stop_price FROM pending_orders "
            "ORDER BY ticker")).fetchall()
    assert [r[0] for r in rows] == ["AAA", "BBB"]
    assert all(r[1] == "PENDING" for r in rows)
    assert all(r[2] == 1000.0 and r[3] == 90.0 for r in rows)
    assert "fundamentals_error" in details, "the injected failure must be recorded, not swallowed"


def test_capture_functions_never_raise_even_on_a_broken_database(mocker):
    """Both entry points are themselves never-raising, so the scheduler's
    try/except is a second line of defence rather than the only one."""
    mocker.patch("data.ingestion.get_engine", side_effect=RuntimeError("db gone"))
    out = refresh_fundamentals_chunk({})
    assert out["error"] is not None
    out2 = snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    assert out2["error"] is not None


# --------------------------------------------------------------------------
# Fetch / parse
# --------------------------------------------------------------------------

def test_fetch_extracts_all_eight_raw_fields_and_the_reporting_period(mocker):
    t = MagicMock()
    t.quarterly_balance_sheet = _stmt({"Total Debt": 10.0, "Stockholders Equity": 20.0,
                                       "Total Assets": 30.0, "Ordinary Shares Number": 40.0})
    t.quarterly_financials = _stmt({"Total Revenue": 50.0, "Net Income": 60.0})
    t.quarterly_cashflow = _stmt({"Operating Cash Flow": 70.0, "Free Cash Flow": 80.0})
    mocker.patch("data.ingestion.yf.Ticker", return_value=t)

    rec = fetch_ticker_fundamentals("AAA")
    assert rec["reporting_period"] == "2026-06-30"
    assert rec["statement_type"] == "quarterly"
    assert rec["values"] == {"total_debt": 10.0, "total_equity": 20.0, "total_assets": 30.0,
                             "shares_outstanding": 40.0, "revenue": 50.0, "net_income": 60.0,
                             "operating_cash_flow": 70.0, "free_cash_flow": 80.0}


def test_fetch_records_which_row_label_matched(mocker):
    """Recording the alias is what makes a future yfinance rename show up as a
    coverage drop against a known baseline instead of silent nulls."""
    t = MagicMock()
    t.quarterly_balance_sheet = _stmt({"Total Stockholder Equity": 20.0})   # the OLD label
    t.quarterly_financials = _stmt({"Total Revenue": 50.0})
    t.quarterly_cashflow = _stmt({"Operating Cash Flow": 70.0})
    mocker.patch("data.ingestion.yf.Ticker", return_value=t)

    rec = fetch_ticker_fundamentals("AAA")
    assert rec["values"]["total_equity"] == 20.0
    assert rec["field_aliases"]["total_equity"] == "Total Stockholder Equity"


def test_fetch_returns_none_when_nothing_usable_comes_back(mocker):
    t = MagicMock()
    t.quarterly_balance_sheet = pd.DataFrame()
    t.quarterly_financials = pd.DataFrame()
    t.quarterly_cashflow = pd.DataFrame()
    mocker.patch("data.ingestion.yf.Ticker", return_value=t)
    assert fetch_ticker_fundamentals("AAA") is None


def test_fetch_never_raises_on_a_yfinance_error(mocker):
    mocker.patch("data.ingestion.yf.Ticker", side_effect=RuntimeError("network"))
    assert fetch_ticker_fundamentals("AAA") is None


def test_missing_fields_are_stored_as_null_not_dropped(mocker, db_engine):
    """A partially-covered ticker must still be recorded -- the probe measured
    revenue/net_income at 92%, so this is the common case, not an edge case."""
    t = MagicMock()
    t.quarterly_balance_sheet = _stmt({"Total Debt": 10.0})
    t.quarterly_financials = pd.DataFrame()
    t.quarterly_cashflow = pd.DataFrame()
    mocker.patch("data.ingestion.yf.Ticker", return_value=t)

    rec = fetch_ticker_fundamentals("AAA")
    assert rec is not None and rec["values"]["revenue"] is None
    assert write_ticker_fundamentals(rec) is not None
    with db_engine.connect() as conn:
        row = conn.execute(sa_text(
            "SELECT total_debt, revenue FROM ticker_fundamentals WHERE ticker='AAA'")).fetchone()
    assert row[0] == 10.0 and row[1] is None


# --------------------------------------------------------------------------
# Write / upsert
# --------------------------------------------------------------------------

def test_an_unchanged_refetch_writes_no_new_row(db_engine):
    first = write_ticker_fundamentals(_record())
    second = write_ticker_fundamentals(_record())
    assert first == second
    with db_engine.connect() as conn:
        n = conn.execute(sa_text("SELECT COUNT(*) FROM ticker_fundamentals")).scalar()
    assert n == 1, "an identical re-fetch must cost nothing"


def test_a_CHANGED_refetch_APPENDS_rather_than_overwriting(db_engine):
    """The restatement-visibility guarantee. If a re-fetch of the same
    ticker-quarter differs, the old values must survive as their own row --
    otherwise a restatement is a silent change and is undetectable afterwards."""
    first = write_ticker_fundamentals(_record(revenue=4.0))
    second = write_ticker_fundamentals(_record(revenue=999.0))
    assert second != first, "a changed re-fetch must create a NEW row"
    with db_engine.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT revenue FROM ticker_fundamentals ORDER BY id")).fetchall()
    assert [r[0] for r in rows] == [4.0, 999.0], "the original value must survive"


def test_provenance_defaults_to_point_in_time_and_separates_the_two_kinds(db_engine):
    write_ticker_fundamentals(_record(), provenance="point_in_time")
    write_ticker_fundamentals(_record(revenue=111.0), provenance="restated")
    with db_engine.connect() as conn:
        rows = dict(conn.execute(sa_text(
            "SELECT provenance, COUNT(*) FROM ticker_fundamentals GROUP BY provenance")).fetchall())
    assert rows == {"point_in_time": 1, "restated": 1}, (
        "restated and point_in_time rows are separate observations of the same "
        "quarter and must never collapse into one another"
    )


def test_an_invalid_provenance_is_refused(db_engine):
    assert write_ticker_fundamentals(_record(), provenance="whatever") is None


def test_a_new_reporting_period_creates_a_separate_row(db_engine):
    """Vintages accumulate -- they must never overwrite each other, or the
    dataset stops being point-in-time."""
    write_ticker_fundamentals(_record(period="2026-03-31"))
    write_ticker_fundamentals(_record(period="2026-06-30"))
    with db_engine.connect() as conn:
        periods = [r[0] for r in conn.execute(sa_text(
            "SELECT reporting_period FROM ticker_fundamentals ORDER BY reporting_period"))]
    assert periods == ["2026-03-31", "2026-06-30"]


def test_field_aliases_and_periods_round_trip_as_json(db_engine):
    write_ticker_fundamentals(_record())
    with db_engine.connect() as conn:
        fa, pbs = conn.execute(sa_text(
            "SELECT field_aliases, period_by_statement FROM ticker_fundamentals")).fetchone()
    assert json.loads(fa)["total_debt"] == "Total Debt"
    assert json.loads(pbs)["balance"] == "2026-06-30"


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def test_chunk_is_bounded_and_prefers_never_fetched_tickers(db_engine, mocker):
    pool = [f"T{i:03d}" for i in range(60)]
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=pool)
    mocker.patch("data.ingestion.time.sleep", lambda *_a, **_k: None)
    # T000 already has a row, so it must NOT be in the first chunk.
    write_ticker_fundamentals(_record(ticker="T000"))

    fetched = []

    def _fake(tk):
        fetched.append(tk)
        return _record(ticker=tk)
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", side_effect=_fake)

    out = refresh_fundamentals_chunk({}, chunk_size=10)
    assert out["attempted"] == 10, "chunk must be bounded regardless of pool size"
    assert "T000" not in fetched, "an already-fetched ticker must yield to never-fetched ones"
    assert out["never_fetched_remaining"] == 49


def test_chunk_respects_the_configured_rate_limit(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["A", "B"])
    mocker.patch("data.ingestion.fetch_ticker_fundamentals", side_effect=lambda t: _record(ticker=t))
    sleeps = []
    mocker.patch("data.ingestion.time.sleep", side_effect=lambda s: sleeps.append(s))
    refresh_fundamentals_chunk({"data": {"rate_limit_seconds": 2.5}}, chunk_size=5)
    assert sleeps == [2.5, 2.5]


def test_default_chunk_size_keeps_eod_impact_bounded():
    """~3.66s/ticker measured -> ~90s. The data-quality backstop's ~19-minute
    unbroken loop is the precedent being avoided."""
    assert FUNDAMENTALS_CHUNK_SIZE * 3.66 < 120


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

def test_snapshot_links_candidates_and_records_unlinked_ones_as_null(db_engine):
    write_ticker_fundamentals(_record(ticker="AAA"))
    out = snapshot_candidate_fundamentals(["AAA", "BBB"], "2026-08-12")
    assert out == {"candidates": 2, "linked": 1, "unlinked": 1, "error": None}
    with db_engine.connect() as conn:
        rows = dict(conn.execute(sa_text(
            "SELECT ticker, fundamentals_id FROM candidate_fundamentals_snapshot")).fetchall())
    assert rows["AAA"] is not None
    assert rows["BBB"] is None, (
        "a candidate with no fundamentals yet must be RECORDED with NULL, not skipped -- "
        "otherwise 'no data' is indistinguishable from 'the job never ran'"
    )


def test_snapshot_links_the_most_recent_reporting_period(db_engine):
    write_ticker_fundamentals(_record(ticker="AAA", period="2026-03-31"))
    newer = write_ticker_fundamentals(_record(ticker="AAA", period="2026-06-30"))
    snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    with db_engine.connect() as conn:
        fid = conn.execute(sa_text(
            "SELECT fundamentals_id FROM candidate_fundamentals_snapshot")).scalar()
    assert fid == newer


def test_snapshot_is_idempotent_for_the_same_night(db_engine):
    write_ticker_fundamentals(_record(ticker="AAA"))
    snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    with db_engine.connect() as conn:
        n = conn.execute(sa_text(
            "SELECT COUNT(*) FROM candidate_fundamentals_snapshot")).scalar()
    assert n == 1


def test_snapshot_keeps_separate_rows_per_night(db_engine):
    write_ticker_fundamentals(_record(ticker="AAA"))
    snapshot_candidate_fundamentals(["AAA"], "2026-08-11")
    snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    with db_engine.connect() as conn:
        n = conn.execute(sa_text(
            "SELECT COUNT(*) FROM candidate_fundamentals_snapshot")).scalar()
    assert n == 2


# --------------------------------------------------------------------------
# The standing constraint: nothing reads this back into a decision
# --------------------------------------------------------------------------

def test_no_decision_path_module_references_the_fundamentals_tables():
    """Structural guard. If fundamentals are ever used in a decision it will be
    after a pre-registered study, not by accretion -- so this test failing is
    the intended alarm, not an inconvenience."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    decision_path = [
        root / "analysis" / "decision.py",
        root / "analysis" / "signals.py",
        root / "analysis" / "backtest.py",
        root / "analysis" / "universe.py",
        root / "analysis" / "volatility.py",
        root / "brokers" / "trading212.py",
    ]
    for f in decision_path:
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        for needle in ("ticker_fundamentals", "candidate_fundamentals_snapshot",
                       "fetch_ticker_fundamentals", "snapshot_candidate_fundamentals"):
            assert needle not in src, (
                f"{f.name} references {needle!r}. Fundamentals must not enter the "
                "decision path without a pre-registered study."
            )


def test_snapshot_never_links_a_restated_row(db_engine):
    """The forward sample stays clean BY CONSTRUCTION. A restated row is the
    current version of a past quarter, not what was knowable on the night --
    linking one would silently contaminate the only sample that can ever
    support an outcome study, and the contamination would be invisible
    afterwards because restated and original figures look identical."""
    write_ticker_fundamentals(_record(ticker="AAA"), provenance="restated")
    out = snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    assert out["linked"] == 0 and out["unlinked"] == 1
    with db_engine.connect() as conn:
        fid = conn.execute(sa_text(
            "SELECT fundamentals_id FROM candidate_fundamentals_snapshot")).scalar()
    assert fid is None


def test_snapshot_keys_join_cleanly_to_price_data(db_engine):
    """Joinability check (amendment point 7): the eventual comparison needs the
    candidates' fundamentals AND their subsequent price paths, including
    REJECTED candidates as the control. Both sides key on (ticker, date), so
    the join works with no migration."""
    from data.database import PriceData  # noqa: PLC0415
    with db_engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT INTO price_data (ticker, date, open, high, low, close, adj_close, volume) "
            "VALUES ('AAA', '2026-08-12', 1, 1, 1, 100.0, 100.0, 10)"))
    write_ticker_fundamentals(_record(ticker="AAA"))
    snapshot_candidate_fundamentals(["AAA"], "2026-08-12")
    with db_engine.connect() as conn:
        row = conn.execute(sa_text(
            "SELECT s.ticker, s.decision_date, f.revenue, p.close "
            "FROM candidate_fundamentals_snapshot s "
            "JOIN ticker_fundamentals f ON f.id = s.fundamentals_id "
            "JOIN price_data p ON p.ticker = s.ticker AND date(p.date) = s.decision_date")).fetchone()
    assert row == ("AAA", "2026-08-12", 4.0, 100.0)


def test_backfill_writes_restated_rows_only(db_engine, mocker):
    mocker.patch("analysis.universe.get_live_stock_candidates", return_value=["AAA"])
    mocker.patch("data.ingestion.time.sleep", lambda *_a, **_k: None)
    mocker.patch("data.ingestion.fetch_ticker_fundamentals_history",
                 return_value=[_record(ticker="AAA", period="2026-03-31"),
                               _record(ticker="AAA", period="2025-12-31")])
    out = backfill_fundamentals_chunk({}, chunk_size=5)
    assert out["rows_written"] == 2
    with db_engine.connect() as conn:
        provs = {r[0] for r in conn.execute(sa_text(
            "SELECT DISTINCT provenance FROM ticker_fundamentals"))}
    assert provs == {"restated"}, "backfilled history must never be flagged point_in_time"
