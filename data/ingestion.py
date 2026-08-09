"""Fetch daily OHLCV data from yfinance and persist it to SQLite."""

import json
import logging
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import pandas_market_calendars as mcal
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
    - Every stock ticker this system has ever discovered as a genuine S&P
      500 constituent (analysis/universe.py:get_all_known_stock_tickers())
      -- deliberately NOT narrowed to today's live candidate pool. Ingestion
      scope and trading eligibility are two different questions: this list
      controls what gets a price history; is_member() (via
      get_live_stock_candidates(), called separately at signal/decision
      time) still governs what is actually tradeable. Before 2026-08-06
      this used get_live_stock_candidates() directly, which meant a ticker
      stopped being ingested the moment it lost membership -- a real,
      still-growing gap (see CLAUDE.md's "Universe coverage remediation"):
      real, decades-long constituents (CAG, CPB, BK, MMC, K, IPG, EMN, WBA
      among them) went permanently missing from price_data purely because
      nobody ever asked for their history once they rolled off the index.
      Switching to the full ever-known set closes that gap going forward --
      it cannot recover what was never ingested, but it stops the hole from
      growing. get_all_known_stock_tickers() is a superset of
      get_live_stock_candidates() (the latter is the former filtered by
      is_member()), so this also makes the get_held_research_tickers()
      merge below redundant for any stock -- kept anyway because it still
      covers a held ETF position (ETF sleeve trading is off, not deleted;
      get_all_known_stock_tickers() deliberately excludes ETF_TICKERS).
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
      in this set and NOT in get_all_known_stock_tickers() at the same
      time (an ETF) -- that is exactly the case this merge exists for.

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
        from analysis.universe import get_all_known_stock_tickers
        for ticker in sorted(get_all_known_stock_tickers()):
            if ticker not in already:
                entries.append({"ticker": ticker, "name": ticker, "exchange": "NYSE"})
                already.add(ticker)
    except Exception as exc:
        logger.warning("Could not load known stock tickers for price ingestion: %s", exc)

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


# Relative divergence, on an overlapping (ticker, date) close, above which an
# incremental pull is treated as a retroactive restatement (stock split,
# reverse split, spin-off adjustment, or vendor correction) rather than
# ordinary data. yfinance's "Close" column is retroactively adjusted for
# stock splits even under auto_adjust=False (see module-level notes in
# analysis/signals.py) -- a ticker that splits after its history is already
# stored will return different Close values for dates already on disk. 1%
# comfortably exceeds ordinary vendor revision noise (observed to be sub-cent
# rounding, well under 0.1%) while comfortably catching even the smallest
# common split ratios (a 5:4 split changes the ratio by 20%, 3:2 by 33%, 2:1
# by 50%).
_RESTATEMENT_TOLERANCE = 0.01


def _find_retroactive_restatement(
    df: pd.DataFrame, ticker: str, tolerance: float = _RESTATEMENT_TOLERANCE
) -> list[tuple]:
    """Compare incoming close prices against whatever price_data already has
    stored for the same (ticker, date), for every date the incoming pull
    overlaps.

    pull_incremental()'s rolling 10-day window overlaps whatever was already
    stored from the previous run's incremental pulls -- this comparison is
    free: the signal already exists in every incremental pull, it was
    previously just discarded by the upsert instead of checked first.

    Returns a list of (date_iso, stored_close, incoming_close) for every
    overlapping date whose relative divergence exceeds `tolerance`. An empty
    list means no restatement was detected.
    """
    incoming: dict = {}
    for date_idx, row in df.iterrows():
        date_val = date_idx if isinstance(date_idx, datetime) else pd.Timestamp(date_idx).to_pydatetime()
        try:
            close_val = float(row.get("Close", None))
        except (TypeError, ValueError):
            continue
        if close_val <= 0 or not math.isfinite(close_val):
            continue
        incoming[date_val.date()] = close_val

    if not incoming:
        return []

    lo, hi = min(incoming), max(incoming)
    engine = get_engine()
    with engine.connect() as conn:
        stored_rows = conn.execute(
            text("SELECT date, close FROM price_data WHERE ticker = :t AND date >= :lo AND date <= :hi"),
            {
                "t": ticker,
                "lo": datetime.combine(lo, datetime.min.time()),
                "hi": datetime.combine(hi, datetime.max.time()),
            },
        ).fetchall()

    diverging = []
    for date_val, stored_close in stored_rows:
        # Stored `date` may come back as str or datetime depending on
        # driver/format -- format="mixed" matches the same normalisation
        # backfill_price_history() already applies elsewhere in this file.
        if isinstance(date_val, str):
            date_val = pd.to_datetime(date_val, format="mixed").to_pydatetime()
        key = date_val.date()
        if key not in incoming or stored_close is None or stored_close == 0:
            continue
        new_close = incoming[key]
        rel_diff = abs(new_close - stored_close) / abs(stored_close)
        if rel_diff > tolerance:
            diverging.append((key.isoformat(), stored_close, new_close))

    return diverging


_DATE_OVERRUN_TOLERANCE_DAYS = 90


def verify_ticker_identity(ticker: str, config: Optional[dict] = None) -> dict:
    """The ONE shared identity check for the historical/delisted stock
    population (2026-08-07) -- see research/recycled_ticker_investigation_
    2026-08-07/REPORT.md. Called identically by the one-off remediation
    script, the ingestion-time screen (backfill_price_history()/
    pull_incremental()'s first-time-download paths, below), and the
    periodic backstop scan (scan_unverified_tickers(), below) -- never
    re-implemented at a second call site.

    Two checks, deliberately NOT symmetric evidence (corrected during
    review of the investigation's own PALM spot-check, which the naive
    both-must-agree version would have missed):

    Check B (date-coverage overrun) -- the PRIMARY, discovering signal,
    not corroboration. Compares price_data's own stored MAX(date) for
    *ticker* against its last real index-membership end_date
    (analysis.universe.get_membership_windows()) -- excluding the
    _SENTINEL_END (2099-12-31) case, which means "still believed active,
    nothing to compare, Check A alone decides." Fails if stored data
    extends more than _DATE_OVERRUN_TOLERANCE_DAYS (90) past the real
    end_date -- a deliberate buffer for settlement/reporting noise, not
    swept (PALM's real overrun was ~4 years, never a close call). This is
    a positive proof of impossibility: if data exists years past a
    company's real delisting, those rows cannot be that company's,
    regardless of what .info currently says.

    Check A (quoteType) -- corroborating, not authoritative. yfinance
    .info's quote_type/exchange/long_name. Fails if quote_type != "EQUITY",
    or exchange == "YHD", or long_name is falsy. Can fail for benign
    reasons alone (a genuinely-delisted ticker whose .info is simply
    sparse today), so it is never sufficient on its own to quarantine.

    Verdict:
      - Check B fails, and Check A does NOT affirmatively confirm a real,
        named equity (quote_type == "EQUITY" AND a real, non-"YHD"
        exchange AND a non-empty long_name) -> QUARANTINED. This is the
        PALM case: Check A gives nothing to rescue it with.
      - Check B fails, but Check A DOES affirmatively confirm a real,
        named equity -> NEEDS_REVIEW, not an automatic QUARANTINE.
        Discovered directly during implementation testing (not
        theoretical): "left the S&P 500 index" and "stopped trading
        entirely" are different events, and Check B's own signal (index-
        membership end_date, the only delisting-adjacent date this DB
        has) conflates them. FNMA is the concrete counter-example --
        real S&P 500 membership ended 2008-09-08 (real conservatorship),
        but Fannie Mae kept trading OTC continuously under its own name
        for the ~18 years since. A naive "Check B fails -> QUARANTINED,
        full stop" would have quarantined a ticker this project's own
        investigation already confirmed genuine. The override is narrow
        and directional: it only ever moves a verdict FROM QUARANTINED TO
        NEEDS_REVIEW when Check A's evidence is unambiguously positive --
        it never touches the PALM-shaped case (Check A gives nothing to
        rescue it with there), and it never promotes anything all the way
        to CLEAN on its own.
      - Check B passes, Check A fails -> NEEDS_REVIEW (stays
        un-quarantined, needs a human).
      - Both pass -> CLEAN.
      - _SENTINEL_END case (Check B not applicable) -> decided by Check A
        alone: QUARANTINED if it fails, CLEAN if it passes. Reported
        separately by callers as a real coverage blind spot -- a ticker
        whose membership window never closed can only ever be judged by
        the weaker, corroborating-only check.
      - .info unavailable/errored after retries -> NEEDS_REVIEW.

    Never raises -- returns a dict with an "error" key on failure instead,
    matching this file's "one bad symbol doesn't block the rest of the
    batch" convention.

    Returns: {"ticker", "status", "reason", "evidence" (dict, not yet
    JSON-serialised -- callers decide that), "sentinel_case" (bool)}.
    """
    from analysis.universe import _SENTINEL_END, get_membership_windows  # noqa: PLC0415

    config = config or _load_config()
    rate_limit = config["data"]["rate_limit_seconds"]
    max_retries = config["data"]["max_retries"]

    # Check B: date-coverage overrun.
    windows = get_membership_windows(ticker)
    last_end = max((e for _, e in windows), default=None)
    sentinel_case = last_end == _SENTINEL_END

    with get_engine().connect() as conn:
        max_date_str = conn.execute(
            text("SELECT MAX(date) FROM price_data WHERE ticker = :t"), {"t": ticker}
        ).scalar()

    overrun_days = None
    boundary_gap_days = None
    check_b_applicable = last_end is not None and not sentinel_case and max_date_str is not None
    check_b_failed = False
    if check_b_applicable:
        stored_max = pd.to_datetime(max_date_str, format="mixed").date()
        overrun_days = (stored_max - last_end).days
        check_b_failed = overrun_days > _DATE_OVERRUN_TOLERANCE_DAYS
        if check_b_failed:
            # Contiguity across the membership-end boundary -- NOT used to
            # decide the verdict (that would be a third, unvalidated check
            # invented after the fact); recorded purely as adjudication
            # evidence for the NEEDS_REVIEW pile per the user's own
            # observation that FNMA's continuous-OTC-trading shape and a
            # genuine recycled-symbol's gap-then-different-series shape
            # look identical to the overrun boolean alone. A small gap
            # (normal weekend/holiday cadence) is consistent with the SAME
            # entity trading straight through the index-removal date
            # (the FNMA shape); a large gap is consistent with a genuine
            # pause followed by an unrelated series resuming under the
            # recycled symbol. Left unvalidated as a hypothesis for a
            # possible future Check C, not acted on here.
            with get_engine().connect() as conn:
                boundary_row = conn.execute(
                    text(
                        "SELECT date FROM price_data WHERE ticker = :t AND date <= :d "
                        "ORDER BY date DESC LIMIT 1"
                    ),
                    {"t": ticker, "d": datetime.combine(last_end, datetime.max.time())},
                ).scalar()
                next_row = conn.execute(
                    text(
                        "SELECT date FROM price_data WHERE ticker = :t AND date > :d "
                        "ORDER BY date ASC LIMIT 1"
                    ),
                    {"t": ticker, "d": datetime.combine(last_end, datetime.max.time())},
                ).scalar()
            if boundary_row is not None and next_row is not None:
                before_d = pd.to_datetime(boundary_row, format="mixed").date()
                after_d = pd.to_datetime(next_row, format="mixed").date()
                boundary_gap_days = (after_d - before_d).days

    # Check A: quoteType, via yfinance .info -- rate-limited/retried like
    # every other yfinance call in this file.
    quote_type = exchange = long_name = None
    info_error = None
    for attempt in range(1, max_retries + 1):
        try:
            info = yf.Ticker(_yf_symbol(ticker)).info
            quote_type = info.get("quoteType")
            exchange = info.get("exchange")
            long_name = info.get("longName")
            info_error = None
            break
        except Exception as exc:
            info_error = str(exc)
            time.sleep(rate_limit * attempt)
    time.sleep(rate_limit)

    # A gracefully-handled not-found lookup (yfinance logs its own HTTP 404
    # internally and returns an EMPTY dict rather than raising) is evidence-
    # free -- it is not the same thing as "quoteType affirmatively returned
    # a non-equity classification" (quote_type populated as 'ETF'/
    # 'MUTUALFUND'/etc, which IS a real, if degenerate, response). Conflating
    # the two meant a 404 was scored identically to positive evidence of a
    # different identity, and quarantined on no evidence at all (found via
    # IAC, 2026-08-08 -- all three fields None, info_error None, yet routed
    # to QUARANTINED before this fix). Only ALL THREE fields being empty
    # counts as "empty" -- a real response with a populated quote_type but a
    # placeholder exchange/name (PALM/PDG/RX's own already-validated
    # MUTUALFUND/YHD/None shape) is still positive evidence, not silence,
    # and must keep quarantining exactly as before.
    info_empty = not info_error and quote_type is None and exchange is None and not long_name

    check_a_failed = None if info_error else (
        quote_type != "EQUITY" or exchange == "YHD" or not long_name
    )
    # Affirmative confirmation is a STRICTER bar than "Check A passed" --
    # it's what's allowed to override a Check B failure (see the FNMA
    # case in this function's own docstring), so it's spelled out
    # separately rather than reusing check_a_failed's inverse.
    check_a_affirms_real_equity = bool(
        not info_error and quote_type == "EQUITY" and exchange and exchange != "YHD" and long_name
    )

    # Verdict.
    if info_error or info_empty:
        status = "NEEDS_REVIEW"
        reason = (
            f"yfinance .info unavailable after {max_retries} attempts: {info_error}"
            if info_error else
            "yfinance .info returned no data at all (quote_type/exchange/long_name all "
            "empty, no exception raised -- a gracefully-handled not-found lookup, not "
            "positive evidence of a different identity) -- treated the same as an "
            "unavailable/errored response, not scored as a quarantine."
        )
    elif check_b_applicable and check_b_failed and not check_a_affirms_real_equity:
        status = "QUARANTINED"
        reason = (
            f"price_data extends {overrun_days}d past real membership end_date "
            f"({last_end.isoformat()}) -- data cannot be this company's, and "
            "quoteType gives no affirmative real-equity identity to override it "
            f"(quote_type={quote_type!r}, exchange={exchange!r}, long_name={long_name!r})."
        )
    elif check_b_applicable and check_b_failed and check_a_affirms_real_equity:
        status = "NEEDS_REVIEW"
        reason = (
            f"price_data extends {overrun_days}d past real membership end_date "
            f"({last_end.isoformat()}), BUT quoteType affirmatively confirms a real, "
            f"named equity (quote_type={quote_type!r}, exchange={exchange!r}, "
            f"long_name={long_name!r}) -- plausibly a company that left the index "
            "without stopping trading (the FNMA case), not necessarily corruption. "
            "Needs a human."
        )
    elif not check_b_applicable:
        # Sentinel case (or no membership windows at all) -- Check A alone decides.
        status = "QUARANTINED" if check_a_failed else "CLEAN"
        reason = (
            "membership window never closed (or unknown) -- Check B not applicable, "
            f"decided by Check A alone (quote_type={quote_type!r}, exchange={exchange!r}, "
            f"long_name={long_name!r})"
        )
    elif check_a_failed:
        status = "NEEDS_REVIEW"
        reason = (
            f"quoteType check failed (quote_type={quote_type!r}, exchange={exchange!r}, "
            f"long_name={long_name!r}) but date-coverage check passed -- corroborating "
            "evidence only, needs a human."
        )
    else:
        status = "CLEAN"
        reason = "both checks passed"

    return {
        "ticker": ticker,
        "status": status,
        "reason": reason,
        "sentinel_case": sentinel_case,
        "evidence": {
            "quote_type": quote_type, "exchange": exchange, "long_name": long_name,
            "info_error": info_error,
            "date_overrun_days": overrun_days, "check_b_applicable": check_b_applicable,
            "last_membership_end": last_end.isoformat() if last_end else None,
            "boundary_gap_days": boundary_gap_days,
        },
    }


_CONTIGUITY_GAP_THRESHOLD_TRADING_DAYS = 100
_CONTIGUITY_MIN_GAP_CALENDAR_DAYS = 4  # below this, can never contain a full missing NYSE trading day -- not worth a calendar lookup


def check_boundary_contiguity(ticker: str, last_membership_end: Optional[str] = None) -> dict:
    """Check C (2026-08-07) -- NEEDS_REVIEW adjudication, not a first-pass
    identity check. Only meaningful for a ticker already sitting in
    NEEDS_REVIEW because verify_ticker_identity()'s Check B found
    price_data extending suspiciously past the ticker's real S&P
    membership end_date but Check A affirmatively confirmed a real, named
    equity (the FNMA pattern) -- this function is what actually tests
    whether that equity's OWN trading history reaches back across the
    boundary, or whether it only starts sometime after.

    Methodology, validated against controls before ANY threshold was
    chosen (research/data_quality_remediation_2026-08-07/
    PHASE1_CONTIGUITY_REPORT.md has the full distribution and control
    table -- summarised here):

    Searches price_data UNBOUNDED (the ticker's entire stored history, not
    a fixed window) for the nearest row at/before last_membership_end and
    the nearest row after it -- an earlier version capped the search to a
    fixed +/-window and silently mis-scored PALM and a confirmed-recycled
    control (NCC) as "insufficient data", because their real gaps (PALM:
    ~12 years; NCC: ~3.3 years) are simply bigger than any reasonable
    fixed window. The gap between those two rows, in NYSE trading days
    (pandas_market_calendars -- ordinary weekends/holidays never count),
    is the discriminating signal.

    >= _CONTIGUITY_GAP_THRESHOLD_TRADING_DAYS (100) -- large, structural
    gap. 100 is not fitted to the data; the observed distribution across
    the real 209-ticker NEEDS_REVIEW population is sharply bimodal (129
    tickers at exactly 0, one at 37, then a real void until the next value
    at 107) -- 100 sits in that empty [38,106] range, so anywhere in it
    produces an identical split. Validated with ZERO false positives
    against FNMA (gap=0, correctly never flagged) and PALM (gap=3,114,
    correctly flagged); of the 19 confirmed LIKELY_RECYCLED QUARANTINED
    controls, 10/19 show gap>=100 (all correctly flagged, zero
    contradictions) and 9/19 show gap<100 (indistinguishable from genuine
    continuity by this signal -- a real, reported limitation, not silently
    hidden).

    < 100 (including exactly 0) -- inconclusive, NOT evidence of
    genuineness. 9 of the 19 confirmed-recycled controls also land here.
    Callers must NOT treat this as grounds to promote a ticker to CLEAN --
    only to leave it exactly where it already was.

    Price-scale continuity (median close before/after the boundary) was
    tested as a second candidate signal and explicitly REJECTED: FNMA's
    own scale_ratio is 0.134 (a real ~7.5x apparent "collapse", driven by
    the genuine 2008 conservatorship price decline, not corruption) --
    using it would have false-positived the one control that mattered
    most. Not implemented here for that reason, not merely left out.

    Returns: {"ticker", "applicable" (bool -- False if no membership
    end_date or no price_data after it exists to compare), "last_
    membership_end", "gap_calendar_days", "gap_missing_trading_days",
    "no_data_before_end_date_at_all" (bool), "gap_exceeds_threshold"
    (bool or None if not applicable) -- callers decide what to do with a
    True/False verdict; this function never writes to the DB itself.
    """
    from analysis.universe import get_membership_windows  # noqa: PLC0415

    if last_membership_end is not None:
        last_end = pd.Timestamp(last_membership_end).date()
    else:
        windows = get_membership_windows(ticker)
        last_end = max((e for _, e in windows), default=None)
    if last_end is None:
        return {
            "ticker": ticker, "applicable": False, "last_membership_end": None,
            "gap_calendar_days": None, "gap_missing_trading_days": None,
            "no_data_before_end_date_at_all": None, "gap_exceeds_threshold": None,
        }

    boundary_dt = datetime.combine(last_end, datetime.max.time())
    with get_engine().connect() as conn:
        before_row = conn.execute(
            text("SELECT date FROM price_data WHERE ticker = :t AND date <= :d ORDER BY date DESC LIMIT 1"),
            {"t": ticker, "d": boundary_dt},
        ).scalar()
        after_row = conn.execute(
            text("SELECT date FROM price_data WHERE ticker = :t AND date > :d ORDER BY date ASC LIMIT 1"),
            {"t": ticker, "d": boundary_dt},
        ).scalar()

    no_data_before = before_row is None
    if after_row is None:
        return {
            "ticker": ticker, "applicable": False, "last_membership_end": last_end.isoformat(),
            "gap_calendar_days": None, "gap_missing_trading_days": None,
            "no_data_before_end_date_at_all": no_data_before, "gap_exceeds_threshold": None,
        }

    d1 = pd.to_datetime(after_row, format="mixed").date()
    # No row at/before end_date anywhere in this ticker's stored history --
    # the gap is at minimum "end_date to first known row" (a lower bound;
    # matches step5_contiguity_analysis.py's convention).
    d0 = pd.to_datetime(before_row, format="mixed").date() if before_row is not None else last_end

    gap_calendar_days = (d1 - d0).days
    if gap_calendar_days >= _CONTIGUITY_MIN_GAP_CALENDAR_DAYS:
        cal = mcal.get_calendar("NYSE")
        expected = cal.valid_days(
            start_date=(d0 + timedelta(days=1)).isoformat(), end_date=(d1 - timedelta(days=1)).isoformat()
        )
        gap_missing_trading_days = len(expected)
    else:
        gap_missing_trading_days = 0

    return {
        "ticker": ticker, "applicable": True, "last_membership_end": last_end.isoformat(),
        "gap_calendar_days": gap_calendar_days, "gap_missing_trading_days": gap_missing_trading_days,
        "no_data_before_end_date_at_all": no_data_before,
        "gap_exceeds_threshold": gap_missing_trading_days >= _CONTIGUITY_GAP_THRESHOLD_TRADING_DAYS,
    }


def _screen_newly_backfilled_ticker(ticker: str, config: dict) -> None:
    """Ingestion-time screen (2026-08-07) -- the permanent fix for the root
    cause research/recycled_ticker_investigation_2026-08-07/REPORT.md
    identified: the 2026-08-06 survivorship-bias remediation's own
    identity cross-check covered only a 28-ticker sample of the 331 it
    backfilled. Called from both backfill_price_history() and
    pull_incremental()'s first-time-download paths (below) the moment a
    ticker with NO prior price_data rows gets its first history stored --
    calls the exact same verify_ticker_identity() the one-off remediation
    script and the periodic backstop scan (scan_unverified_tickers())
    use, never a second implementation. Never raises -- a screening
    failure must not block ingestion itself; the ticker simply stays
    unscreened until the next backstop scan picks it up."""
    try:
        result = verify_ticker_identity(ticker, config)
        from data.database import write_data_quality_status  # noqa: PLC0415
        write_data_quality_status(
            ticker, result["status"], result["reason"], json.dumps(result["evidence"]),
        )
        if result["status"] != "CLEAN":
            logger.warning(
                "Newly-backfilled ticker %s flagged %s at ingestion time: %s",
                ticker, result["status"], result["reason"],
            )
    except Exception as exc:
        logger.warning("Identity screen failed for newly-backfilled %s (will retry via the "
                        "periodic backstop scan): %s", ticker, exc)


_SCAN_UNVERIFIED_BATCH_SIZE = 50  # ~2.5s/ticker rate limit -> ~2min/batch, safely inside an EOD cycle


def scan_unverified_tickers(config: Optional[dict] = None) -> dict:
    """Periodic backstop scan (2026-08-07, scoped/chunked/logged 2026-08-08
    after being found to have never completed successfully since deploy --
    see CLAUDE.md's backstop-fix entry) -- finds stock tickers with
    price_data rows but no ticker_data_quality row yet, and runs the same
    verify_ticker_identity() the one-off remediation script and the
    ingestion-time screen use. Covers (a) the pre-existing ~540-ticker
    "live" population that predated the identity-verification mechanism
    entirely and was never actually screened (assumed clean by virtue of
    being live-traded -- a reasonable heuristic, but "assumed clean" and
    "verified clean" were behaviourally identical under
    get_research_stock_candidates() until this ran), and (b) any future
    ticker whose data lands outside backfill_price_history()/
    pull_incremental()'s own screened first-time-download paths. Never
    raises per-ticker, matching this file's "one bad symbol doesn't block
    the rest of the batch" convention.

    STOCK-ONLY SCOPE (2026-08-08 fix): the query is intersected with
    analysis.universe.get_all_known_stock_tickers() -- verify_ticker_
    identity()'s Check A can only ever affirm quote_type == "EQUITY", so
    feeding it a non-stock ticker (an ETF, an FX pair, an index level
    series) isn't a corner case it handles conservatively, it's a check
    applied outside its own domain, and will always fail Check A by
    construction. Found live: the first real run of this function (pre-
    fix) wrote QUARANTINED for ^GSPC (the crisis-trigger reference series
    itself), both FX reporting pairs, and ~33 ETF tickers -- inert today
    only because nothing currently reads quarantine status on any of
    those paths, which is a property of today's callers, not a property
    of the data being safe to mislabel.

    CHUNKED (2026-08-08 fix): processes at most
    _SCAN_UNVERIFIED_BATCH_SIZE tickers per call rather than the full
    backlog. A ~460-ticker backlog at this project's standard
    rate_limit_seconds (2.5s/ticker) is a ~19-minute unbroken yfinance
    loop -- confirmed, not assumed, to be long enough that this backstop
    had not completed even once in the two EOD cycles since it was
    deployed (2026-08-06, 2026-08-07 -- both runs' completion records are
    missing this function's own results entirely, with no corresponding
    failure warning either, consistent with the process being restarted
    mid-loop rather than erroring). Chunking makes this self-healing
    across days with no separate progress table needed: once a ticker is
    written to ticker_data_quality (any status), it drops out of the
    next call's own "WHERE q.ticker IS NULL" query, so an interrupted
    scan resumes at the next unscreened ticker on its next invocation
    rather than restarting the whole backlog. Returns "remaining" so a
    caller (the EOD job, or a human) can see how much backlog is left.

    Same role as detect_price_cliffs() in this file: a full-table
    backstop, not the primary defense -- expected near-zero work on
    ordinary days once the backlog has actually been drained, since the
    primary defense (the ingestion-time screen) already covers every
    genuinely new ticker going forward.

    Returns {"checked", "remaining", "newly_quarantined": [...],
    "newly_needs_review": [...], "errors": [...]}, matching
    refresh_membership_from_source()'s contract shape plus "remaining".
    """
    config = config or _load_config()
    from data.database import write_data_quality_status  # noqa: PLC0415
    from analysis.universe import get_all_known_stock_tickers  # noqa: PLC0415

    stock_tickers = get_all_known_stock_tickers()
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT p.ticker FROM price_data p "
                "LEFT JOIN ticker_data_quality q ON p.ticker = q.ticker "
                "WHERE q.ticker IS NULL"
            )
        ).fetchall()
    unverified_stocks = sorted(r[0] for r in rows if r[0] in stock_tickers)
    batch = unverified_stocks[:_SCAN_UNVERIFIED_BATCH_SIZE]

    result: dict = {
        "checked": 0, "remaining": max(0, len(unverified_stocks) - len(batch)),
        "newly_quarantined": [], "newly_needs_review": [], "errors": [],
    }
    for ticker in batch:
        try:
            v = verify_ticker_identity(ticker, config)
            write_data_quality_status(ticker, v["status"], v["reason"], json.dumps(v["evidence"]))
            result["checked"] += 1
            if v["status"] == "QUARANTINED":
                result["newly_quarantined"].append(ticker)
            elif v["status"] == "NEEDS_REVIEW":
                result["newly_needs_review"].append(ticker)
        except Exception as exc:
            result["errors"].append(f"{ticker}: {exc}")

    # Explicit completion log, success path -- previously this function had
    # NO log output of its own at all on success, only a caller-side warning
    # on failure; since the failure warning also never fired (see docstring
    # above), the net effect was a backstop that could not be distinguished
    # from "silently working" by anyone reading the logs. Logged whether
    # checked is 0 or not, so "ran and found nothing to do" is visibly
    # different from "did not run."
    logger.info(
        "Data-quality backstop scan: checked=%d quarantined=%d needs_review=%d "
        "errors=%d remaining=%d",
        result["checked"], len(result["newly_quarantined"]), len(result["newly_needs_review"]),
        len(result["errors"]), result["remaining"],
    )
    return result


# ---------------------------------------------------------------------------
# Sector classification (2026-08-09) -- reporting-only, unrelated to and
# deliberately never routed through verify_ticker_identity()/
# scan_unverified_tickers() above. Those exist to catch recycled-ticker-
# symbol identity fraud in the historical/delisted population; sector is a
# different, unrelated question (what business a ticker is in), and the two
# concerns must not be conflated -- see TickerSector's own docstring in
# data/database.py for the full reasoning.
# ---------------------------------------------------------------------------

def fetch_ticker_sector(ticker: str) -> Optional[str]:
    """One yfinance `.info` call, returning the 'sector' field or None --
    never raises. A ticker with no resolvable sector (roughly 40% of the
    historical/delisted population, per research/selection_audit_2026-08-08's
    own sector-map coverage check) is a normal, expected outcome, not an
    error -- the caller (get_or_fetch_sector()) caches None just as
    durably as a real value, so this is never re-attempted on every call."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    sector = info.get("sector")
    return str(sector) if sector else None


def get_or_fetch_sector(ticker: str) -> Optional[str]:
    """Cached sector lookup -- checks ticker_sector first; fetches from
    yfinance only if this ticker has never been looked up at all (a prior
    row with sector=NULL means "already tried, nothing found", not "not
    yet attempted" -- see TickerSector's own docstring). This is the only
    function that calls fetch_ticker_sector(); output.performance.
    compute_sector_breakdown() is the only caller of this function, so a
    genuinely new ticker triggers at most one real yfinance call, ever."""
    from data.database import get_ticker_sector, write_ticker_sector

    cached = get_ticker_sector(ticker)
    if cached is not None:
        return cached["sector"]
    sector = fetch_ticker_sector(ticker)
    write_ticker_sector(ticker, sector)
    return sector


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

    Before upserting, every pull for a ticker with existing history is
    checked via _find_retroactive_restatement() against what's already
    stored for the same overlapping dates. A divergence beyond
    _RESTATEMENT_TOLERANCE triggers a full-history re-fetch and replace
    instead of the normal 10-day upsert -- see that function's docstring.
    This is deliberately NOT gated on yfinance's own .splits metadata: the
    overlap comparison catches any retroactive restatement (reverse splits,
    spin-off adjustments, vendor corrections), not just an ordinary forward
    split.
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
                restatement = _find_retroactive_restatement(df, ticker)
                if restatement:
                    logger.warning(
                        "RETROACTIVE PRICE RESTATEMENT detected for %s: %d overlapping "
                        "date(s) diverge from stored price_data by more than %.1f%% "
                        "(e.g. %s: stored=%.4f incoming=%.4f) — likely a stock split, "
                        "reverse split, or spin-off adjustment. Triggering a full-history "
                        "re-fetch and replace instead of the normal 10-day upsert.",
                        ticker, len(restatement), _RESTATEMENT_TOLERANCE * 100, *restatement[0],
                    )
                    full_start = datetime.strptime(config["data"]["history_start_date"], "%Y-%m-%d")
                    full_end = datetime.utcnow()
                    full_df = _fetch_ticker(
                        ticker, start=full_start, end=full_end,
                        max_retries=max_retries, rate_limit=rate_limit,
                    )
                    if full_df is not None and not full_df.empty:
                        if isinstance(full_df.columns, pd.MultiIndex):
                            full_df.columns = [col[0] for col in full_df.columns]
                        count = _upsert_price_rows(full_df, ticker)
                        results[ticker] = count
                        logger.warning(
                            "  %s: full re-fetch replaced %d rows across entire stored "
                            "history after restatement detection.", ticker, count,
                        )
                    else:
                        results[ticker] = 0
                        logger.error(
                            "  %s: restatement detected but full re-fetch failed — "
                            "stored history may still contain a cliff artefact until "
                            "the next run retries.", ticker,
                        )
                else:
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
                _screen_newly_backfilled_ticker(ticker, config)
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
            is_new_ticker = False
        else:
            fetch_end = datetime.utcnow()
            is_new_ticker = True

        logger.info("Backfilling %s: %s -> %s", ticker, start_dt.date(), fetch_end.date())
        df = _fetch_ticker(ticker, start=start_dt, end=fetch_end, max_retries=max_retries, rate_limit=rate_limit)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            count = _upsert_price_rows(df, ticker)
            results[ticker] = count
            logger.info("  %s: backfilled %d rows.", ticker, count)
            if is_new_ticker:
                # Ingestion-time identity screen (2026-08-07) -- only for a
                # ticker with NO prior price_data rows (the exact shape of
                # the 2026-08-06 survivorship-bias remediation's own
                # backfill, and the root cause this closes). A ticker that
                # already had data and is merely being extended further
                # back was already screened, if at all, when it was first
                # added -- re-screening here would just re-run the same
                # check against unchanged evidence.
                _screen_newly_backfilled_ticker(ticker, config)
        else:
            results[ticker] = 0
            logger.info("  No data available for %s that far back.", ticker)

    return results


_CLIFF_RATIO_LOW = 0.5
_CLIFF_RATIO_HIGH = 2.0


def detect_price_cliffs(
    ratio_low: float = _CLIFF_RATIO_LOW, ratio_high: float = _CLIFF_RATIO_HIGH
) -> list[dict]:
    """Scan the full price_data table for consecutive-trading-day close
    ratio breaks outside [ratio_low, ratio_high] -- the signature of a
    split-adjustment cliff artefact (see _find_retroactive_restatement()'s
    docstring for the root cause). This is a backstop, not the primary
    defense: pull_incremental() now guards against this at ingest time for
    its own rolling-10-day-window write path; this function re-scans the
    whole table so a different write path (backfill_price_history(), a
    manual script) or a future bug in the ingest-time guard doesn't sit
    silently uncaught. Called by the eod scheduled job (data/scheduler.py)
    and by research/split_adjustment_audit_2026-08-06/detect_cliffs.py's
    one-off deeper audit (yfinance-cross-referenced classification), which
    this function deliberately does not attempt -- a network call per hit
    is too slow/fragile for a scheduled job, and the expected hit rate
    after the ingest-time fix is near-zero. Flags for manual review.

    Returns a list of dicts (ticker, date, date_prev, close, close_prev,
    ratio), one per flagged consecutive-day break, empty if none found.
    """
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql_query(
            text("SELECT ticker, date, close FROM price_data ORDER BY ticker, date"), conn
        )
    if df.empty:
        return []
    df["date"] = pd.to_datetime(df["date"], format="mixed")

    hits: list[dict] = []
    for ticker, g in df.groupby("ticker", sort=False):
        close = g["close"].to_numpy()
        dates = g["date"].to_numpy()
        if len(close) < 2:
            continue
        ratio = close[1:] / close[:-1]
        bad = (ratio < ratio_low) | (ratio > ratio_high)
        for i in bad.nonzero()[0]:
            hits.append(
                {
                    "ticker": ticker,
                    "date_prev": pd.Timestamp(dates[i]).date().isoformat(),
                    "date": pd.Timestamp(dates[i + 1]).date().isoformat(),
                    "close_prev": float(close[i]),
                    "close": float(close[i + 1]),
                    "ratio": float(ratio[i]),
                }
            )
    return hits


# Known, individually-classified price-cliff exceptions (2026-08-08) -- see
# research/split_cliff_remediation_2026-08-08/REPORT.md for the full
# per-ticker evidence. Deliberately NOT a change to detect_price_cliffs()'s
# own thresholds or to its (or the EOD job's) live scoping -- this is a
# separate, explicit suppression list applied by the caller (data/
# scheduler.py's EOD job) AFTER live-scoping and BEFORE alerting, so a
# specific already-investigated (ticker, date) pair stops re-firing the
# email every day while the detector itself, and everything upstream of
# this filter, stays exactly as it was. Keyed on (ticker, the "date" field
# -- the later day of the flagged pair, matching how each cliff was
# reported and investigated).
#
# Four categories, not a flat "safe to ignore" bucket -- the classification
# and evidence are attached so a future reader (or an eventual repair
# effort) does not have to re-derive them:
#   GENUINE_MARKET_MOVE          -- close and adj_close moved in lockstep;
#                                    a real, dated market event, not an
#                                    adjustment issue at all.
#   CORPORATE_ACTION_UNREPAIRABLE -- a confirmed real corporate action
#                                    (adj_close smooth, close jumps), but a
#                                    fresh yfinance pull reproduces the
#                                    identical cliff -- yfinance
#                                    retroactively smooths ORDINARY STOCK
#                                    SPLITS in `close` over time, but never
#                                    smooths mergers/spinoffs/special
#                                    dividends in `close`, only in
#                                    `adj_close`. Re-fetch-and-replace
#                                    (this project's only existing repair
#                                    mechanism, _find_retroactive_
#                                    restatement()) cannot fix this class --
#                                    confirmed directly, not assumed; see
#                                    CLAUDE.md's "close vs adj_close
#                                    smoothing" standing fact. Deliberately
#                                    NOT repaired -- see the REPORT.md's
#                                    "decision, based on the Phase 1
#                                    finding" for the full reasoning
#                                    (fabrication risk, adj_close already
#                                    holds the corrected series, and every
#                                    affected event is 2007-2018, far
#                                    outside any current momentum window).
#   PARTIALLY_RESOLVED            -- a real corporate action where `close`
#                                    already appears to correctly reflect
#                                    what happened, just not cleanly
#                                    smooth in `adj_close` either (yfinance
#                                    handles a combined split+spinoff
#                                    inconsistently) -- not a repair
#                                    candidate.
#   UNRESOLVED                    -- a real yfinance event exists near the
#                                    date but does not explain the observed
#                                    move, and the underlying cause was not
#                                    identified. Recorded as genuinely
#                                    unresolved, not forced into a bucket.
_KNOWN_PRICE_CLIFF_EXCEPTIONS: dict[tuple[str, str], dict] = {
    ("AIG", "2008-09-15"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "Lehman Brothers collapse weekend; AIG's own near-collapse and Fed bailout. "
                    "close_ratio=adj_close_ratio=0.392 (moved in lockstep, no split match).",
    },
    ("APA", "2020-03-09"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "COVID/Saudi-Russia oil price war crash. close_ratio=adj_close_ratio=0.461 "
                    "(moved in lockstep, no split match).",
    },
    ("GL", "2024-04-11"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "Fuzzy Panda Research short-seller report alleging insurance fraud; shares "
                    "closed down ~53% that session (confirmed via CNBC/Hedgeweek reporting, not "
                    "assumed). close_ratio=adj_close_ratio=0.469 (moved in lockstep, no split "
                    "match) -- contradicts the prior assumption this was a corporate action.",
    },
    ("HIG", "2008-10-30"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "2008 banking-crisis volatility (Hartford Financial). close_ratio="
                    "adj_close_ratio=0.484 (moved in lockstep, no split match).",
    },
    ("HIG", "2008-12-05"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "2008 banking-crisis volatility (Hartford Financial); an extreme 2.024x "
                    "overnight move, checked with particular care given the magnitude. "
                    "close_ratio=adj_close_ratio=2.024 (moved in lockstep, no split match) -- the "
                    "specific catalyst for this exact date was not independently pinned down, but "
                    "adj_close agreement is strong evidence against an adjustment artefact.",
    },
    ("OXY", "2020-03-09"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "COVID/Saudi-Russia oil price war crash. close_ratio=0.466, "
                    "adj_close_ratio=0.480 (moved together, no split match).",
    },
    ("PCG", "2019-01-14"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "PG&E wildfire-liability bankruptcy filing. close_ratio=adj_close_ratio=0.476 "
                    "(moved in lockstep, no split match).",
    },
    ("STT", "2009-01-20"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "2008-09 banking-crisis volatility (State Street). close_ratio="
                    "adj_close_ratio=0.410 (moved in lockstep, no split match).",
    },
    ("TRGP", "2020-03-09"): {
        "classification": "GENUINE_MARKET_MOVE",
        "evidence": "COVID/Saudi-Russia oil price war crash. close_ratio=adj_close_ratio=0.471 "
                    "(moved in lockstep, no split match).",
    },
    ("KDP", "2018-07-10"): {
        "classification": "CORPORATE_ACTION_UNREPAIRABLE",
        "evidence": "Keurig/Dr Pepper Snapple merger. adj_close smooth (ratio 1.115) while close "
                    "jumps (ratio 0.179) -- confirmed genuine corporate action. A fresh yfinance "
                    "pull reproduces the identical cliff (checked directly, not assumed): yfinance "
                    "never retroactively smooths a merger in `close`, only in `adj_close`.",
    },
    ("MO", "2008-03-31"): {
        "classification": "CORPORATE_ACTION_UNREPAIRABLE",
        "evidence": "Philip Morris International spinoff. adj_close smooth (ratio 0.975) while "
                    "close jumps (ratio 0.301) -- confirmed genuine corporate action. A fresh "
                    "yfinance pull reproduces the identical cliff: yfinance never retroactively "
                    "smooths a spinoff in `close`, only in `adj_close`.",
    },
    ("WY", "2010-07-20"): {
        "classification": "CORPORATE_ACTION_UNREPAIRABLE",
        "evidence": "REIT-conversion special dividend (Weyerhaeuser). adj_close smooth (ratio "
                    "1.034) while close jumps (ratio 0.381) -- confirmed genuine corporate action. "
                    "A fresh yfinance pull reproduces the identical cliff: yfinance never "
                    "retroactively smooths a special dividend in `close`, only in `adj_close`.",
    },
    ("EXPE", "2011-12-21"): {
        "classification": "PARTIALLY_RESOLVED",
        "evidence": "TripAdvisor spinoff plus a same-day 1-for-2 stock split (confirmed via "
                    "TechCrunch reporting). close_ratio=0.488 closely matches the real reported "
                    "price action; adj_close is not smooth (ratio 0.664), but that reflects "
                    "yfinance's inconsistent handling of a combined split+spinoff, not an error in "
                    "close. close appears to already correctly reflect what happened.",
    },
    ("JCI", "2007-07-02"): {
        "classification": "UNRESOLVED",
        "evidence": "A real yfinance split event exists on this exact date (factor 0.25) but its "
                    "direction (should raise price ~4x under a reverse-split reading) contradicts "
                    "the observed ~60% drop. The underlying corporate event was not identified via "
                    "research. A fresh pull reproduces the identical cliff (same class as the "
                    "confirmed corporate actions), but the classification is not forced.",
    },
}


def filter_known_price_cliff_exceptions(hits: list[dict]) -> list[dict]:
    """Remove any hit matching a known, individually-classified (ticker,
    date) pair in _KNOWN_PRICE_CLIFF_EXCEPTIONS -- called by the EOD job
    after live-scoping and before alerting, so an already-investigated
    cliff stops re-triggering the daily email. Does not modify
    detect_price_cliffs() itself or the live-scoping filter -- a hit for
    the same ticker on a DIFFERENT date, or a new ticker entirely, is
    unaffected and still alerts normally. Never raises; an unrecognised
    hit shape is passed through unfiltered rather than dropped silently."""
    return [h for h in hits if (h.get("ticker"), h.get("date")) not in _KNOWN_PRICE_CLIFF_EXCEPTIONS]


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
