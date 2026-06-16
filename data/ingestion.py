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

# Initial history window is read from config.yaml (data.history_days).
# Defined there so it can be changed without a code deploy.


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
    for assets in config.get("watchlist", {}).values():
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if isinstance(asset, dict) and "ticker" in asset:
                tickers.append(asset["ticker"])
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
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    period: Optional[str] = None,
    max_retries: int = 3,
    rate_limit: float = 1.5,
) -> Optional[pd.DataFrame]:
    """Download OHLCV history for a single ticker with retry logic.

    Pass either *period* (e.g. "10d") or *start*/*end* dates.  *period* takes
    precedence and delegates date arithmetic to yfinance, which skips holidays
    and gaps automatically.

    Returns a DataFrame with columns [Open, High, Low, Close, Adj Close, Volume]
    or None if all attempts fail.
    """
    for attempt in range(1, max_retries + 1):
        try:
            kwargs: dict = dict(auto_adjust=False, progress=False, threads=False)
            if period:
                kwargs["period"] = period
            elif start is not None and end is not None:
                kwargs["start"] = start.strftime("%Y-%m-%d")
                kwargs["end"]   = end.strftime("%Y-%m-%d")
            else:
                raise ValueError("Provide either 'period' or both 'start' and 'end'.")
            df = yf.download(ticker, **kwargs)
            if df.empty:
                logger.warning("No data returned for %s (attempt %d)", ticker, attempt)
            else:
                time.sleep(rate_limit)
                return df
        except Exception as exc:
            logger.error("Error fetching %s (attempt %d): %s", ticker, attempt, exc)
            time.sleep(rate_limit * attempt)

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

    Downloads daily OHLCV data going back data.history_days calendar days
    (default 3650, ~10 years).  If an asset was listed more recently, yfinance
    returns whatever is available from the listing date — no error is raised.

    Args:
        tickers: List of ticker symbols. Defaults to the full watchlist.
        days:    Override for calendar days of history to fetch.
                 Defaults to config.yaml data.history_days.

    Returns:
        Dict mapping ticker -> number of rows written.
    """
    init_db()
    config = _load_config()

    if tickers is None:
        tickers = get_all_tickers(config)
    if days is None:
        days = config["data"]["history_days"]

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
    """Backfill recent price data for each ticker using a rolling 10-day window.

    For tickers that already have stored data, yfinance is called with
    period='10d' rather than a calculated start date.  This approach:
      - Handles bank-holiday gaps transparently (yfinance skips non-trading days)
      - Avoids the risk of marking a ticker unavailable just because today's
        session hasn't closed yet or a holiday left a one-day gap
      - Is idempotent: rows already in the DB are silently overwritten via upsert

    Tickers with no stored data at all fall back to a full-history download
    (as in pull_history) and ARE marked unavailable if the fetch fails, since
    that indicates the ticker itself is broken rather than a calendar gap.
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

            has_history = (
                session.query(PriceData.date)
                .filter(PriceData.ticker == ticker)
                .first()
            ) is not None

            if has_history:
                # Rolling 10-day window: yfinance returns whatever trading days
                # are available, skipping holidays and weekends automatically.
                logger.info("Fetching %s (period=10d)…", ticker)
                df = _fetch_ticker(
                    ticker, period="10d",
                    max_retries=max_retries, rate_limit=rate_limit,
                )
                if df is not None and not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]
                    count = _upsert_price_rows(df, ticker)
                    results[ticker] = count
                else:
                    # Empty is normal when the exchange was closed for all 10 days
                    # (e.g. extended holiday); do NOT mark unavailable.
                    results[ticker] = 0
                    logger.info(
                        "  No new data for %s in last 10d — market may be closed.", ticker
                    )
            else:
                # First-time download: fetch full history.
                start = datetime.utcnow() - timedelta(days=config["data"]["history_days"])
                end   = datetime.utcnow()
                logger.info("Fetching %s (full history from %s)…", ticker, start.date())
                df = _fetch_ticker(
                    ticker, start=start, end=end,
                    max_retries=max_retries, rate_limit=rate_limit,
                )
                if df is not None and not df.empty:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]
                    count = _upsert_price_rows(df, ticker)
                    results[ticker] = count
                else:
                    results[ticker] = 0
                    logger.warning(
                        "  No data for %s after all retries — marking unavailable.", ticker
                    )
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
