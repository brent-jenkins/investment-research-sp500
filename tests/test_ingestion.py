"""Tests for data/ingestion.py:_upsert_price_rows().

A single NaN/infinite/zero/negative close price in the rolling window breaks
every subsequent SMA computation, so _upsert_price_rows() must never write
such a row to price_data. Uses the db_engine fixture (in-memory SQLite) —
no live database or yfinance call is involved.
"""

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import text

from data.ingestion import _upsert_price_rows

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
