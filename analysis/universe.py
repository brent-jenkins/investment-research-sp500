"""Universe management — point-in-time S&P 500 stock membership plus a fixed
ETF sleeve, and the per-ticker max-holding-days classification used by both
the backtest engine and live order placement. No discretionary exclusions: a
stock is eligible on any date it was a genuine index constituent, nothing
else.

Two tiers:
  - ETF_TICKERS: 33 broad sector/index/bond/commodity ETFs. These have no
    "membership" concept — always eligible (see is_member()'s default).
  - STOCK_MEMBERSHIP: 40 US large-cap stocks (39 with at least one real,
    non-empty membership window; GOLD has none — see below), each gated to
    the exact date range(s) it was actually an S&P 500 constituent.

Source and verification (2026-07-31): fja05680/sp500 on GitHub
("S&P 500 Historical Components & Changes (Updated).csv") — 2,718 rows,
1996-01-02 to 2026-06-30, base data from Andreas Clenow's *Trading Evolved*
(sourced from Norgate Data) plus ongoing Wikipedia-cross-referenced
updates. Spot-checked directly against real corporate/index history before
being trusted: V added 2009-12-21 (public record: Visa added to the S&P
500 Dec 2009), MA added 2008-07-18 (Mastercard, July 2008), RTX added
2020-04-03 (Raytheon Technologies merger, April 2020), TSLA added
2020-12-21 (widely publicised) — all four matched exactly.

TSLA, META, and GOOGL are included from their genuine membership dates —
an earlier design excluded these names (plus TSM) for "socio-political/
geopolitical risk," a subjective, present-day judgment call rather than an
S&P 500 eligibility question, and that exclusion is deliberately not
reproduced here. TSM is not included at all: it has no membership window
in the source data at any date, because Taiwan Semiconductor is a foreign
private issuer and foreign companies are structurally ineligible for
S&P 500 inclusion — an objective reason, unlike the earlier exclusion.
GOLD (Barrick Gold) is likewise absent from every row of the source
data — Barrick is Canadian-domiciled, never a genuine S&P 500 member —
so it is listed here with an empty window (kept as a fixed candidate for
the ongoing refresh below, but permanently zero eligible days unless the
source is ever proven wrong).

META's own window merges two ticker-symbol eras into one continuous
membership span (see _KNOWN_TICKER_RENAMES below): FB, added 2013-12-23,
renamed 2022-06-09; the underlying company's index membership never
lapsed, only its ticker symbol changed.
"""
import logging
from datetime import date, datetime
from typing import Optional

import requests
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import UniverseMembership, get_engine

logger = logging.getLogger(__name__)

_SOURCE_NAME = "fja05680/sp500"
_SOURCE_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
_SENTINEL_END = date(2099, 12, 31)  # "still a member as of the latest known data"

ETF_TICKERS: tuple[str, ...] = (
    "AMLP", "EEM", "EMB", "EWG", "EWJ", "EWZ", "FXI", "GLD", "HYG", "IBB",
    "IEF", "INDA", "IWM", "KBE", "LQD", "MUB", "PDBC", "SKYY", "SLV", "SOXX",
    "TIP", "TLT", "VGK", "VOO", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
    "XLU", "XLV", "XLY",
)

# Baseline membership windows, as verified 2026-07-31 (see module docstring).
# This is the seed loaded into universe_membership on first use — the live
# source of truth after that is the DB table, kept current by
# refresh_membership_from_source() below, not this dict.
STOCK_MEMBERSHIP: dict[str, list[tuple[str, str]]] = {
    "AAPL": [("1996-01-02", "2026-12-31")], "ADBE": [("1997-05-06", "2026-12-31")],
    "AMD": [("1996-01-02", "2013-09-17"), ("2017-03-20", "2026-12-31")],
    "AMZN": [("2005-11-21", "2026-12-31")], "AVGO": [("2014-05-08", "2026-12-31")],
    "BAC": [("1996-01-02", "2026-12-31")], "CAT": [("1996-01-02", "2026-12-31")],
    "COST": [("1996-01-02", "2026-12-31")], "CRM": [("2008-09-15", "2026-12-31")],
    "CVX": [("1996-01-02", "2026-12-31")], "DIS": [("1996-01-02", "2026-12-31")],
    "GE": [("1996-01-02", "2026-12-31")], "GOLD": [],
    "GS": [("2002-07-22", "2026-12-31")], "HD": [("1996-01-02", "2026-12-31")],
    "ISRG": [("2008-06-02", "2026-12-31")], "JNJ": [("1996-01-02", "2026-12-31")],
    "JPM": [("1996-01-02", "2026-12-31")], "LIN": [("2018-11-06", "2026-12-31")],
    "LLY": [("1996-01-02", "2026-12-31")], "MA": [("2008-07-18", "2026-12-31")],
    "MRK": [("1996-01-02", "2026-12-31")], "MSFT": [("1996-01-02", "2026-12-31")],
    "NEM": [("1996-01-02", "2026-12-31")], "NFLX": [("2010-12-20", "2026-12-31")],
    "NVDA": [("2001-11-30", "2026-12-31")], "ORCL": [("1996-01-02", "2026-12-31")],
    "PFE": [("1996-01-02", "2026-12-31")], "PG": [("1996-01-02", "2026-12-31")],
    "QCOM": [("1999-07-22", "2026-12-31")], "RTX": [("2020-04-03", "2026-12-31")],
    "SPGI": [("1996-01-02", "2026-12-31")], "TMO": [("1997-01-02", "2026-12-31")],
    "UNH": [("1996-01-02", "2026-12-31")], "V": [("2009-12-21", "2026-12-31")],
    "WMT": [("1996-01-02", "2026-12-31")], "XOM": [("1996-01-02", "2026-12-31")],
    "TSLA": [("2020-12-21", "2026-12-31")],
    "META": [("2013-12-23", "2026-12-31")],
    "GOOGL": [("2006-04-03", "2026-12-31")],
}

# Ticker-symbol renames the refresh mechanism must merge into one continuous
# span for the *current* symbol, rather than reporting a false gap/removal
# the day the old symbol disappears from the source data and a new one
# appears. Add an entry here whenever a candidate's listed symbol changes.
_KNOWN_TICKER_RENAMES: dict[str, str] = {"FB": "META"}

ALL_TICKERS: tuple[str, ...] = tuple(sorted(set(ETF_TICKERS) | set(STOCK_MEMBERSHIP.keys())))


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def seed_baseline_membership() -> int:
    """Upsert STOCK_MEMBERSHIP's baseline windows into universe_membership.

    Idempotent — safe to call on every startup. Does nothing to a ticker
    that already has rows from a later refresh_membership_from_source()
    call (upsert only touches the exact (ticker, start_date) pairs baked
    into STOCK_MEMBERSHIP, never deletes rows the refresh mechanism added).
    """
    engine = get_engine()
    n = 0
    with engine.begin() as conn:
        for ticker, windows in STOCK_MEMBERSHIP.items():
            for start_s, end_s in windows:
                stmt = sqlite_insert(UniverseMembership).values(
                    ticker=ticker,
                    start_date=_parse_date(start_s),
                    end_date=_parse_date(end_s),
                    source=f"{_SOURCE_NAME} (baseline, verified 2026-07-31)",
                    fetched_at=datetime.utcnow(),
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["ticker", "start_date"])
                conn.execute(stmt)
                n += 1
    clear_membership_cache()
    return n


_membership_cache: dict[str, list[tuple[date, date]]] = {}


def clear_membership_cache() -> None:
    """Drop the in-process membership cache. Called automatically whenever
    seed_baseline_membership() or refresh_membership_from_source() writes to
    the DB, so a stale cache entry can never outlive the write that
    invalidates it. Membership windows change at most a handful of times a
    month (real S&P 500 reconstitutions are rare) — caching them
    per-process is what makes a backtest over ~5,000 trading days per
    ticker viable at all; without it, every single day's is_member() check
    would be its own DB round-trip (verified directly: this made an
    18-run/73-ticker backtest project to take several hours instead of
    under a minute)."""
    _membership_cache.clear()


def get_membership_windows(ticker: str) -> list[tuple[date, date]]:
    """Read stored windows for *ticker*, oldest first — served from an
    in-process cache after the first call (see clear_membership_cache()).

    Falls back to STOCK_MEMBERSHIP's baseline (parsed inline) only if the
    DB has no rows at all for this ticker yet — i.e. before
    seed_baseline_membership() has ever run. ETF tickers (not in
    STOCK_MEMBERSHIP) always return an empty list; callers treat "no
    membership rows" as "always eligible" for those (see is_member()).
    """
    if ticker in _membership_cache:
        return _membership_cache[ticker]

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT start_date, end_date FROM universe_membership "
                "WHERE ticker = :t ORDER BY start_date"
            ),
            {"t": ticker},
        ).fetchall()
    if rows:
        out = []
        for start_raw, end_raw in rows:
            s = start_raw if isinstance(start_raw, date) else _parse_date(str(start_raw)[:10])
            e = end_raw if isinstance(end_raw, date) else _parse_date(str(end_raw)[:10])
            out.append((s, e))
    elif ticker in STOCK_MEMBERSHIP:
        out = [(_parse_date(s), _parse_date(e)) for s, e in STOCK_MEMBERSHIP[ticker]]
    else:
        out = []

    _membership_cache[ticker] = out
    return out


def is_member(ticker: str, check_date: date) -> bool:
    """True if *ticker* was a genuine index constituent on *check_date*.

    ETF tickers (no membership concept) are always eligible. A stock with
    zero stored/baseline windows (GOLD, or any candidate the source has
    never listed) is never eligible.
    """
    if ticker in ETF_TICKERS:
        return True
    windows = get_membership_windows(ticker)
    return any(start <= check_date <= end for start, end in windows)


def get_max_holding_days(ticker: str, config: dict) -> int:
    """Return the max holding days for *ticker*, matching live automation limits.

    Dispatches on the ticker's is_etf flag in trading212_invest's own
    instrument_mapping — ETFs get trading212_invest.max_holding_days (e.g.
    90 — no leverage/swap costs, held longer), stocks get
    trading212_invest.stock_max_holding_days (e.g. 30 — higher idiosyncratic
    risk than an ETF). A ticker not found in the mapping at all (shouldn't
    happen in practice — this is only ever called for tickers in that
    mapping) falls back to backtest.max_holding_days.

    This is the single source of truth for per-asset holding limits, used
    by simulate_trades() (analysis/backtest.py) and the live exit checks /
    trade-alert job (data/scheduler.py) so backtested and live holding
    limits always agree for the same ticker.
    """
    t212_cfg = config.get("trading212_invest", {})
    default = int(config.get("backtest", {}).get("max_holding_days", 30))

    mapping = t212_cfg.get("instrument_mapping") or {}
    entry = mapping.get(ticker)
    if entry is None:
        return default

    is_etf = bool(entry.get("is_etf", False)) if isinstance(entry, dict) else False
    if is_etf:
        return int(t212_cfg.get("max_holding_days", default))
    return int(t212_cfg.get("stock_max_holding_days", default))


# ---------------------------------------------------------------------------
# Ongoing refresh — the mechanism that keeps this from being a static,
# decaying snapshot. The baseline above was current through the source's
# 2026-06-30 data; every future addition/removal needs this to actually run
# periodically (see data/scheduler.py's membership_refresh job) for live
# eligibility to stay correct.
# ---------------------------------------------------------------------------

def _fetch_source_csv(timeout: int = 30) -> str:
    resp = requests.get(_SOURCE_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _reconstruct_windows(csv_text: str, ticker: str) -> list[tuple[date, date]]:
    """Rebuild ticker's full membership history from the raw CSV — the same
    logic used to build the verified baseline above, re-run against
    whatever the source currently contains. Cheap (a few seconds for the
    full ~30-year, ~2,700-row history), so it's simplest and safest to
    always reconstruct from scratch rather than maintain incremental diff
    state that could drift out of sync with the source.
    """
    lines = csv_text.splitlines()
    rows: list[tuple[date, set[str]]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        comma = line.find(",")
        if comma == -1:
            continue
        date_s = line[:comma]
        tickers_s = line[comma + 1:].strip().strip('"')
        try:
            d = _parse_date(date_s)
        except ValueError:
            continue
        rows.append((d, set(tickers_s.split(","))))
    rows.sort(key=lambda r: r[0])

    symbols = [ticker] + [k for k, v in _KNOWN_TICKER_RENAMES.items() if v == ticker]

    windows: list[tuple[date, date]] = []
    in_window = False
    start: Optional[date] = None
    prev_date: Optional[date] = None
    for d, members in rows:
        present = any(sym in members for sym in symbols)
        if present and not in_window:
            start = d
            in_window = True
        elif not present and in_window:
            windows.append((start, prev_date))
            in_window = False
        prev_date = d
    if in_window and rows:
        windows.append((start, rows[-1][0]))
    return windows


def refresh_membership_from_source(config: Optional[dict] = None) -> dict:
    """Re-fetch the source dataset and reconcile stored membership windows
    for every candidate stock against it. Detects both new removals (a
    ticker's most recent window's end_date moves earlier / becomes closed)
    and new re-additions (a new window opens after a prior close).

    Returns a summary dict: {"checked": N, "changed": [...], "errors": [...]}.
    Never raises on a per-ticker failure — one bad symbol doesn't block the
    rest, matching this codebase's "never raises" automation convention
    (see brokers/trading212.py's exit-check functions for the same pattern).
    """
    summary = {"checked": 0, "changed": [], "errors": []}
    try:
        csv_text = _fetch_source_csv()
    except Exception as exc:
        logger.warning("refresh_membership_from_source: could not fetch %s — %s", _SOURCE_URL, exc)
        summary["errors"].append(f"fetch failed: {exc}")
        return summary

    engine = get_engine()
    fetched_at = datetime.utcnow()

    for ticker in STOCK_MEMBERSHIP:
        summary["checked"] += 1
        try:
            new_windows = _reconstruct_windows(csv_text, ticker)
        except Exception as exc:
            logger.warning("refresh_membership_from_source: failed to reconstruct %s — %s", ticker, exc)
            summary["errors"].append(f"{ticker}: {exc}")
            continue

        existing = get_membership_windows(ticker)
        if new_windows == existing:
            continue

        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM universe_membership WHERE ticker = :t"),
                {"t": ticker},
            )
            for start_d, end_d in new_windows:
                conn.execute(
                    text(
                        "INSERT INTO universe_membership "
                        "(ticker, start_date, end_date, source, fetched_at) "
                        "VALUES (:t, :s, :e, :src, :fa)"
                    ),
                    {"t": ticker, "s": start_d.isoformat(), "e": end_d.isoformat(),
                     "src": _SOURCE_NAME, "fa": fetched_at.isoformat()},
                )
        logger.info("Universe membership changed for %s: %s -> %s", ticker, existing, new_windows)
        summary["changed"].append({"ticker": ticker, "old": existing, "new": new_windows})

    clear_membership_cache()  # always -- simplest way to guarantee no stale entries survive a refresh
    return summary
