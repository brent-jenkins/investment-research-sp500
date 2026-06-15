"""Fetch daily OHLCV data from yfinance and persist it to SQLite."""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
import yfinance as yf
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import PriceData, get_engine, get_metadata_str, get_session, init_db, set_metadata

logger = logging.getLogger(__name__)

# How far back to go on the initial (full) history download.
# yfinance returns data from the asset's inception date when this start date
# precedes the listing, so newer ETFs are handled gracefully without errors.
_INITIAL_HISTORY_DAYS = 3652  # 10 years, accounting for leap years


def _load_config() -> dict:
    """Load config.yaml from the project root."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_all_tickers(config: Optional[dict] = None) -> list[str]:
    """Return a flat list of every ticker symbol in the watchlist."""
    if config is None:
        config = _load_config()
    tickers: list[str] = []
    for group in config["watchlist"].values():
        for entry in group:
            tickers.append(entry["ticker"])
    return tickers


def _ticker_unavailable_key(ticker: str) -> str:
    return f"unavailable_ticker:{ticker}"


def _is_ticker_unavailable(ticker: str) -> bool:
    return get_metadata_str(_ticker_unavailable_key(ticker)) == "true"


def _mark_ticker_unavailable(ticker: str) -> None:
    try:
        set_metadata(_ticker_unavailable_key(ticker), "true")
    except Exception as exc:
        logger.warning("Could not flag %s as unavailable in metadata: %s", ticker, exc)


def _fetch_ticker(
    ticker: str,
    start: datetime,
    end: datetime,
    max_retries: int = 3,
    rate_limit: float = 1.5,
) -> Optional[pd.DataFrame]:
    """Download OHLCV history for a single ticker with retry logic.

    Returns a DataFrame with columns [Open, High, Low, Close, Adj Close, Volume]
    or None if all attempts fail.
    """
    for attempt in range(1, max_retries + 1):
        try:
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if df.empty:
                logger.warning("No data returned for %s (attempt %d)", ticker, attempt)
            else:
                time.sleep(rate_limit)
                return df
        except Exception as exc:
            logger.error("Error fetching %s (attempt %d): %s", ticker, attempt, exc)
            time.sleep(rate_limit * attempt)  # back-off on consecutive failures

    return None


def _upsert_price_rows(df: pd.DataFrame, ticker: str) -> int:
    """Write price rows to the database using INSERT OR REPLACE semantics.

    Returns the number of rows written.
    """
    engine = get_engine()
    rows = []
    for date_idx, row in df.iterrows():
        # yfinance may return a MultiIndex when downloading a single ticker
        date_val = date_idx if isinstance(date_idx, datetime) else pd.Timestamp(date_idx).to_pydatetime()
        rows.append(
            {
                "ticker": ticker,
                "date": date_val,
                "open": float(row.get("Open", 0) or 0),
                "high": float(row.get("High", 0) or 0),
                "low": float(row.get("Low", 0) or 0),
                "close": float(row.get("Close", 0) or 0),
                "adj_close": float(row.get("Adj Close", row.get("Close", 0)) or 0),
                "volume": float(row.get("Volume", 0) or 0),
            }
        )

    if not rows:
        return 0

    stmt = sqlite_insert(PriceData).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adj_close": stmt.excluded.adj_close,
            "volume": stmt.excluded.volume,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)

    return len(rows)


def pull_history(
    tickers: Optional[list[str]] = None,
    days: Optional[int] = None,
) -> dict[str, int]:
    """Fetch full price history for all (or specified) tickers and store in DB.

    Downloads up to 10 years of daily OHLCV data per ticker.  If an asset was
    listed less than 10 years ago (e.g. a newer ETF), yfinance returns whatever
    history is available from the listing date — no error is raised for the
    missing period.

    Args:
        tickers: List of ticker symbols. Defaults to the full watchlist.
        days:    Number of calendar days of history to fetch.
                 Defaults to _INITIAL_HISTORY_DAYS (10 years).

    Returns:
        Dict mapping ticker -> number of rows written.
    """
    init_db()
    config = _load_config()

    if tickers is None:
        tickers = get_all_tickers(config)
    if days is None:
        days = _INITIAL_HISTORY_DAYS

    rate_limit = config["data"]["rate_limit_seconds"]
    max_retries = config["data"]["max_retries"]

    end = datetime.utcnow()
    start = end - timedelta(days=days)

    results: dict[str, int] = {}
    total = len(tickers)
    for idx, ticker in enumerate(tickers, 1):
        if _is_ticker_unavailable(ticker):
            logger.info("Ticker %s marked unavailable, skipping.", ticker)
            results[ticker] = 0
            continue

        logger.info("Fetching %s (%d/%d)…", ticker, idx, total)
        df = _fetch_ticker(ticker, start, end, max_retries=max_retries, rate_limit=rate_limit)
        if df is not None and not df.empty:
            # Flatten MultiIndex columns produced by yfinance ≥0.2.x
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            count = _upsert_price_rows(df, ticker)
            results[ticker] = count
            logger.info("  Stored %d rows for %s", count, ticker)
        else:
            results[ticker] = 0
            logger.warning("  No data for %s after all retries — marking unavailable.", ticker)
            _mark_ticker_unavailable(ticker)

    try:
        set_metadata("last_refresh_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Could not stamp last_refresh_run metadata: %s", exc)

    return results


def pull_incremental(tickers: Optional[list[str]] = None) -> dict[str, int]:
    """Fetch only missing data since the last stored date for each ticker.

    Useful for the daily scheduled refresh — avoids re-downloading history
    that is already in the database.
    """
    init_db()
    config = _load_config()

    if tickers is None:
        tickers = get_all_tickers(config)

    rate_limit = config["data"]["rate_limit_seconds"]
    max_retries = config["data"]["max_retries"]

    session = get_session()
    results: dict[str, int] = {}

    try:
        for ticker in tickers:
            if _is_ticker_unavailable(ticker):
                logger.info("Ticker %s marked unavailable, skipping.", ticker)
                results[ticker] = 0
                continue

            # Find the most recent stored date for this ticker
            latest = (
                session.query(PriceData.date)
                .filter(PriceData.ticker == ticker)
                .order_by(PriceData.date.desc())
                .first()
            )
            if latest:
                start = latest[0] + timedelta(days=1)
            else:
                # No stored data — download the full 10-year history for this
                # ticker.  yfinance returns from the asset's inception date if
                # the start date precedes the listing, so newer ETFs are fine.
                start = datetime.utcnow() - timedelta(days=_INITIAL_HISTORY_DAYS)

            end = datetime.utcnow()
            if start >= end:
                logger.info("  %s is up-to-date, skipping.", ticker)
                results[ticker] = 0
                continue

            logger.info("Fetching %s from %s…", ticker, start.date())
            df = _fetch_ticker(ticker, start, end, max_retries=max_retries, rate_limit=rate_limit)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [col[0] for col in df.columns]
                count = _upsert_price_rows(df, ticker)
                results[ticker] = count
            else:
                results[ticker] = 0
                logger.warning("  No data for %s after all retries — marking unavailable.", ticker)
                _mark_ticker_unavailable(ticker)
    finally:
        session.close()

    try:
        set_metadata("last_refresh_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Could not stamp last_refresh_run metadata: %s", exc)

    return results


def get_last_update_timestamp() -> Optional[datetime]:
    """Return the most recent date for which any price data exists in the DB."""
    session = get_session()
    try:
        result = session.query(PriceData.date).order_by(PriceData.date.desc()).first()
        return result[0] if result else None
    finally:
        session.close()


def get_price_df(ticker: str, days: int = 365) -> pd.DataFrame:
    """Load stored price data for a ticker into a pandas DataFrame.

    Args:
        ticker: Ticker symbol to load.
        days: Number of calendar days of history to return.

    Returns:
        DataFrame with columns [date, open, high, low, close, adj_close, volume],
        sorted ascending by date. Empty DataFrame if no data exists.
    """
    session = get_session()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            session.query(PriceData)
            .filter(PriceData.ticker == ticker, PriceData.date >= since)
            .order_by(PriceData.date.asc())
            .all()
        )
        if not rows:
            return pd.DataFrame()

        records = [
            {
                "date": r.date,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "adj_close": r.adj_close,
                "volume": r.volume,
            }
            for r in rows
        ]
        return pd.DataFrame(records)
    finally:
        session.close()
