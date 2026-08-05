"""Fetch daily OHLCV data from yfinance and persist it to SQLite."""

import logging
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
import yfinance as yf
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import PriceData, clear_metadata, get_engine, get_metadata_str, get_session, init_db, set_metadata

logger = logging.getLogger(__name__)

# Initial history start date is read from config.yaml (data.history_start_date).
# Defined there so it can be changed without a code deploy.


def _load_config() -> dict:
    """Load config.yaml from the project root."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_all_watchlist_entries(config: Optional[dict] = None) -> list[dict]:
    """Return one dict per tracked asset, uniform shape, from three
    sources:

    - `reference_tickers:` (^GSPC, GBPUSD=X, EURUSD=X) -- ticker/name/
      exchange only; these never have an execution mapping.
    - The dynamic live candidate pool (analysis/universe.py:
      get_live_stock_candidates(), the daily-refreshed S&P 500 stock
      membership) -- merged in so price history exists for every genuine
      current index member before it's ever evaluated as a trading
      candidate. pull_incremental() already does a full-history fetch the
      first time a ticker has no stored data at all, so a brand-new index
      entrant needs nothing beyond appearing in this list.
    - Every ticker with a currently-open position (brokers/trading212.py:
      get_held_research_tickers()), regardless of today's membership
      status. Without this, a held ticker that drops out of the live
      candidate pool the moment it loses S&P 500 membership would also
      drop out of THIS list -- silently starving pull_incremental() of
      that ticker, so its price data (and therefore its momentum signal)
      would stop updating entirely for as long as the position stays
      open. That would make "let momentum, not index removal, govern an
      already-open position" (see analysis/decision.py) unimplementable:
      there would be nothing to compute momentum from. A ticker can be
      in this set and NOT in get_live_stock_candidates() at the same
      time -- that is exactly the case this merge exists for.

    Callers that only need the ticker symbols should use get_all_tickers()
    below; callers that need name/exchange should use this directly.
    """
    if config is None:
        config = _load_config()

    entries: list[dict] = [dict(e) for e in config.get("reference_tickers", [])]
    already = {e["ticker"] for e in entries}

    # Best-effort: a DB not yet initialised (very first startup, before
    # init_db() has run) must not break the merge for callers that only
    # need reference_tickers.
    try:
        from analysis.universe import get_live_stock_candidates
        for ticker in get_live_stock_candidates():
            if ticker not in already:
                entries.append({"ticker": ticker, "name": ticker, "exchange": "NYSE"})
                already.add(ticker)
    except Exception as exc:
        logger.warning("Could not load live stock candidates for price ingestion: %s", exc)

    try:
        from brokers.trading212 import get_held_research_tickers
        for ticker in get_held_research_tickers():
            if ticker not in already:
                entries.append({"ticker": ticker, "name": ticker, "exchange": "NYSE"})
                already.add(ticker)
    except Exception as exc:
        logger.warning("Could not load held-position tickers for price ingestion: %s", exc)

    return entries


def get_all_tickers(config: Optional[dict] = None) -> list[str]:
    """Return a flat list of every tracked ticker symbol (reference tickers + live candidates)."""
    return [e["ticker"] for e in get_all_watchlist_entries(config)]


def _ticker_unavailable_key(ticker: str) -> str:
    return f"unavailable_ticker:{ticker}"


def _is_ticker_unavailable(ticker: str) -> bool:
    return get_metadata_str(_ticker_unavailable_key(ticker)) == "true"


def _mark_ticker_unavailable(ticker: str) -> None:
    try:
        set_metadata(_ticker_unavailable_key(ticker), "true")
    except Exception as exc:
        logger.warning("Could not flag %s as unavailable in metadata: %s", ticker, exc)


def _yf_symbol(ticker: str) -> str:
    """Convert this codebase's dot share-class notation (BRK.B, BF.B --
    matching universe_membership / the Wikipedia-style source CSV) to the
    hyphen notation yfinance actually expects (BRK-B, BF-B). Confirmed
    directly (2026-08-05): yf.download("BRK.B") returns zero rows every
    time ("possibly delisted"), while yf.download("BRK-B") returns real
    data -- dot-notation tickers were silently untradeable via this path.
    Only the outgoing yfinance call is converted; every other use of the
    ticker (DB storage, logging, decision logic) keeps the dot form so it
    still matches universe_membership and existing stored rows.
    """
    return ticker.replace(".", "-")


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
            df = yf.download(_yf_symbol(ticker), **kwargs)
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
        close_raw = row.get("Close", None)
        # Skip rows where yfinance returned NaN, infinite, zero, or negative
        # close — these are incomplete intraday bars or data gaps that break
        # rolling-mean SMAs (a single bad value poisons the whole window).
        try:
            close_val = float(close_raw)
        except (TypeError, ValueError):
            close_val = 0.0
        if close_val <= 0 or not math.isfinite(close_val):
            continue
        rows.append(
            {
                "ticker": ticker,
                "date": date_val,
                "open": float(row.get("Open", 0) or 0),
                "high": float(row.get("High", 0) or 0),
                "low": float(row.get("Low", 0) or 0),
                "close": close_val,
                "adj_close": float(row.get("Adj Close", close_raw) or close_val),
                "volume": float(row.get("Volume", 0) or 0),
            }
        )

    if not rows:
        return 0

    # SQLite limits SQL variables to 999 per statement; each row has 8 columns,
    # so cap each batch at 100 rows (800 variables) to stay safely under the limit.
    _BATCH = 100
    with engine.begin() as conn:
        for i in range(0, len(rows), _BATCH):
            chunk = rows[i : i + _BATCH]
            stmt = sqlite_insert(PriceData).values(chunk)
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
            conn.execute(stmt)

    return len(rows)


def pull_history(
    tickers: Optional[list[str]] = None,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
) -> dict[str, int]:
    """Fetch full price history for all (or specified) tickers and store in DB.

    Downloads daily OHLCV data from a start point through today.  If an
    asset was listed more recently, yfinance returns whatever is available
    from the listing date — no error is raised.

    Args:
        tickers:    List of ticker symbols. Defaults to the full watchlist.
        days:       Explicit "N calendar days back from today" window --
                    genuinely relative to whenever this runs. Use this only
                    when that relative semantics is actually wanted (e.g. a
                    one-off "last ~22 years, however long that is today"
                    research fetch); it is NOT the default, and re-running
                    the same call on a later date will fetch a later start.
        start_date: Explicit fixed start date ("YYYY-MM-DD"), unaffected by
                    when this runs. Takes precedence over `days` if both are
                    given.

    Neither `days` nor `start_date` given: defaults to config.yaml's
    data.history_start_date, a fixed anchor (not "N days ago") -- this is
    the right default for "get full backtest history", which was the
    original motivation for this function and does not want an answer that
    silently drifts later every day past whenever a days-count was chosen.

    Returns:
        Dict mapping ticker -> number of rows written.
    """
    init_db()
    config = _load_config()

    if tickers is None:
        tickers = get_all_tickers(config)

    rate_limit = config["data"]["rate_limit_seconds"]
    max_retries = config["data"]["max_retries"]

    end = datetime.utcnow()
    if days is not None:
        start = end - timedelta(days=days)
    elif start_date is not None:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        start = datetime.strptime(config["data"]["history_start_date"], "%Y-%m-%d")

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

    results: dict[str, int] = {}

    for ticker in tickers:
        # Raw connection via context manager: guaranteed closed on exit so no
        # read transaction lingers when _upsert_price_rows() calls engine.begin().
        # Using get_session() / session.close() left the connection in the pool
        # and caused the write transaction to return 0 rows written.
        with get_engine().connect() as conn:
            _max_date = conn.execute(
                text("SELECT MAX(date) FROM price_data WHERE ticker = :t"),
                {"t": ticker},
            ).scalar()
        has_history = _max_date is not None

        if _is_ticker_unavailable(ticker):
            if has_history:
                # Previously failed transiently — clear the flag and retry.
                clear_metadata(_ticker_unavailable_key(ticker))
                logger.info("Cleared stale unavailable flag for %s — retrying.", ticker)
            else:
                logger.info("Ticker %s marked unavailable (no prior data), skipping.", ticker)
                results[ticker] = 0
                continue

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
                logger.debug(
                    "  %s: df.shape=%s, columns=%s", ticker, df.shape, list(df.columns)
                )
                count = _upsert_price_rows(df, ticker)
                results[ticker] = count
                logger.info("  %s: upserted %d rows.", ticker, count)
            else:
                # Empty is normal when the exchange was closed for all 10 days
                # (e.g. extended holiday); do NOT mark unavailable.
                results[ticker] = 0
                logger.info(
                    "  No new data for %s in last 10d — market may be closed.", ticker
                )
        else:
            # First-time download: fetch full history, anchored to a fixed
            # start date (not "N days back from today") so this doesn't
            # silently reach less far back the later this ever runs -- see
            # pull_history()'s own docstring for the full reasoning.
            start = datetime.strptime(config["data"]["history_start_date"], "%Y-%m-%d")
            end   = datetime.utcnow()
            logger.info("Fetching %s (full history from %s)…", ticker, start.date())
            df = _fetch_ticker(
                ticker, start=start, end=end,
                max_retries=max_retries, rate_limit=rate_limit,
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [col[0] for col in df.columns]
                logger.debug(
                    "  %s: df.shape=%s, columns=%s", ticker, df.shape, list(df.columns)
                )
                count = _upsert_price_rows(df, ticker)
                results[ticker] = count
                logger.info("  %s: upserted %d rows.", ticker, count)
            else:
                results[ticker] = 0
                logger.warning(
                    "  No data for %s after all retries — marking unavailable.", ticker
                )
                _mark_ticker_unavailable(ticker)

    try:
        set_metadata("last_refresh_run", datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("Could not stamp last_refresh_run metadata: %s", exc)

    return results


def backfill_price_history(
    tickers: Optional[list[str]] = None,
    start_date: str = "2007-01-01",
) -> dict[str, int]:
    """One-off backward backfill: extend price history deeper into the past
    for tickers that already have data, without ever re-fetching or
    re-writing a date that's already stored.

    pull_incremental()/pull_history() are both forward-only in effect — a
    ticker that already has data from (say) 2016 onward never gets asked
    for anything before 2016 again, no matter how early data.history_start_date
    is set, because incremental refreshes use a rolling 10-day window and
    pull_history() only ever fetches [start, today] once a ticker already
    has any stored data. This
    function is the missing backward direction: a one-off full-range fetch
    from start_date for price data specifically — but deliberately narrower
    than a full re-fetch-and-upsert of the whole range: it only ever
    requests [start_date, existing_earliest_date), strictly before whatever
    a ticker's own earliest stored row already is.

    That narrower range is a deliberate safety choice, not just the
    simplest implementation. yfinance's "Close" is split-adjusted
    retroactively — a stock that splits after the DB was first populated
    can cause a fresh fetch of its own already-stored historical range to
    return different values than what's currently on disk. Restricting
    every fetch to dates strictly before the existing floor makes altering
    an already-stored row impossible by construction, regardless of any
    split that has happened since — verified directly (2026-07-30): a
    backfill run followed by a byte-for-byte checksum of the pre-existing
    date range, and a full compute_signal_history()/simulate_trades() replay
    over the existing 2019-2026 backtest window, both showed zero change.

    A ticker with no stored data at all falls back to a full fetch from
    start_date through today (the same first-time-download path
    pull_incremental() already uses). A ticker whose earliest stored date
    is already <= start_date is skipped — nothing to backfill.
    _upsert_price_rows()'s own upsert-on-(ticker, date) semantics make this
    idempotent and safe to re-run.

    Args:
        tickers:    List of ticker symbols. Defaults to the full watchlist.
        start_date: Earliest date to backfill back to (YYYY-MM-DD).

    Returns:
        Dict mapping ticker -> number of rows written (0 for tickers
        skipped, or for which no data is available that far back).
    """
    init_db()
    config = _load_config()

    if tickers is None:
        tickers = get_all_tickers(config)

    rate_limit  = config["data"]["rate_limit_seconds"]
    max_retries = config["data"]["max_retries"]
    start_dt    = datetime.strptime(start_date, "%Y-%m-%d")

    results: dict[str, int] = {}
    for ticker in tickers:
        with get_engine().connect() as conn:
            min_date_str = conn.execute(
                text("SELECT MIN(date) FROM price_data WHERE ticker = :t"),
                {"t": ticker},
            ).scalar()

        if min_date_str is not None:
            # format="mixed": stored datetime strings can differ in exact
            # format between insert paths — see analysis/backtest.py:
            # _load_macro_history()'s docstring for the identical reason.
            min_date = pd.to_datetime(min_date_str, format="mixed").to_pydatetime()
            if min_date <= start_dt:
                logger.info("%s already covers %s — nothing to backfill.", ticker, min_date.date())
                results[ticker] = 0
                continue
            fetch_end = min_date - timedelta(days=1)
        else:
            fetch_end = datetime.utcnow()

        logger.info("Backfilling %s: %s -> %s", ticker, start_dt.date(), fetch_end.date())
        df = _fetch_ticker(ticker, start=start_dt, end=fetch_end, max_retries=max_retries, rate_limit=rate_limit)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            count = _upsert_price_rows(df, ticker)
            results[ticker] = count
            logger.info("  %s: backfilled %d rows.", ticker, count)
        else:
            results[ticker] = 0
            logger.info("  No data available for %s that far back.", ticker)

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
