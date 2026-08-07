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
import json
import logging
import re
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

# Staleness tracking (2026-08-02) -- fja05680/sp500 is now the SOLE live
# eligibility gate (see this module's own docstring). Before this, a fetch
# failure just logged a WARNING and returned silently -- no timestamp, no
# alert, no way to tell "refreshed today" from "silently failing since a
# deploy three weeks ago" short of grepping server logs. Mirrors the
# existing EOD OVERDUE badge pattern rather than inventing something new --
# see main.py:_eod_overdue() for the sibling implementation this one is
# modelled on.
_LAST_REFRESH_SUCCESS_KEY = "last_membership_refresh_success"

# The job (data/scheduler.py:_membership_refresh_job()) fires Mon-Fri only,
# once per weekday, at 21:15 UTC -- identical cadence to the EOD job
# _eod_overdue() already watches. "~2 reconciliation cycles" (looser than
# EOD's own ~1-cycle/26h bar) is a deliberate choice, not the same number
# reused: a missed EOD cycle risks trading on stale PRICE data the same
# day, which is urgent; the underlying membership source itself only
# changes every 1-5 months in practice (see this module's docstring), so
# one missed refresh is low-stakes and tolerating it before alerting cuts
# noise without meaningfully delaying detection of a genuine operational
# break (the source going unreachable, a renamed/moved repo, etc.).
# 48h is exactly 2 weekday cycles under normal Mon-Fri sequencing; across
# a weekend a single missed Friday->Monday refresh can also cross this
# line by Tuesday (a real, accepted approximation -- see is_membership_
# refresh_stale()'s own docstring -- the same class of imprecision the
# EOD badge's 26h/Monday-exemption design already accepts).
MEMBERSHIP_STALE_HOURS = 48.0
_MEMBERSHIP_JOB_HOUR, _MEMBERSHIP_JOB_MINUTE = 21, 15

# Wikipedia lag check (2026-08-02) -- NOT independent verification. See
# check_wikipedia_lag()'s own docstring for why: fja05680/sp500's own
# maintainer updates his data by diffing against this exact page (see this
# module's docstring), so Wikipedia is this data's own upstream source,
# not a second opinion. A genuine Wikipedia error would show up as
# agreement on both sides and go undetected here. What this actually
# detects: whether our local copy has fallen behind Wikipedia's *current*
# state, given the maintainer's own irregular 1-5 month update cadence.
_WIKI_LAG_METADATA_KEY = "membership_wikipedia_lag_check"
_WIKIPEDIA_URL = (
    "https://en.wikipedia.org/w/index.php"
    "?title=List_of_S%26P_500_companies&action=raw"
)
# Wikipedia's "List of S&P 500 companies" wikitable renders each row's
# ticker cell via an exchange-specific template -- {{NyseSymbol|MMM}},
# {{NasdaqSymbol|AAPL}}, etc. (confirmed directly against the live raw
# wikitext 2026-08-02: 502 tickers, all via {{NyseSymbol|...}}/
# {{NasdaqSymbol|...}}). Matched generically on "*Symbol" rather than
# hardcoding the two exchange names seen today, in case Wikipedia ever
# adds a third (e.g. a future Cboe-listed constituent) without changing
# the template naming convention.
_WIKI_TICKER_RE = re.compile(r"\{\{[A-Za-z]+Symbol\|([A-Z][A-Z.\-]*)\}\}")

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


def get_all_known_stock_tickers() -> set[str]:
    """Every distinct stock ticker this system has ever discovered as a
    genuine (former or current) S&P 500 constituent — the union of every
    ticker refresh_membership_from_source() has ever written to
    universe_membership plus STOCK_MEMBERSHIP's own keys (so this still
    returns something sensible on a fresh install before the first refresh
    has run). ETFs excluded — no membership concept, and structurally
    absent from live trading entirely since 2026-08-02 (see CLAUDE.md's
    "ETF sleeve removed from live trading").

    Shared by get_live_stock_candidates() below (today's eligible subset
    of this set) and analysis.validation.live_universe_snapshot() (which
    reports this full set alongside today's eligible subset, so the two
    can never silently drift apart by each re-deriving their own copy of
    "every known ticker").
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT ticker FROM universe_membership")).fetchall()
    all_known = {r[0] for r in rows} | set(STOCK_MEMBERSHIP.keys())
    all_known -= set(ETF_TICKERS)
    return all_known


def get_live_stock_candidates(as_of: Optional[date] = None) -> list[str]:
    """Return every ticker that is a genuine, currently-eligible LIVE
    trading candidate — the full, dynamically-refreshed S&P 500 stock
    membership, not the fixed STOCK_MEMBERSHIP/ALL_TICKERS constants above
    (those stay frozen for backtest/research reproducibility — see
    CLAUDE.md's "ETF sleeve removed from live trading" section for why the
    live and research universes are deliberately two separate systems, a
    split that predates this function and is unaffected by it).

    get_all_known_stock_tickers() (above), filtered to genuine current
    membership via is_member().

    There is no separate onboarding job: a brand-new S&P 500 entrant
    appears here automatically the next time the existing daily 21:15 UTC
    membership-refresh job runs and discovers it, with no code change and
    no manual step.
    """
    as_of = as_of or datetime.utcnow().date()
    return sorted(t for t in get_all_known_stock_tickers() if is_member(t, as_of))


def get_quarantined_tickers() -> set[str]:
    """Every ticker with status='QUARANTINED' in ticker_data_quality (see
    data/database.py:TickerDataQuality) -- confirmed recycled-ticker-
    symbol corruption or a real date-coverage impossibility (see
    data/ingestion.py:verify_ticker_identity()). Read fresh on every call,
    no caching (unlike get_membership_windows() above) -- this table is
    small and infrequently written, and staleness here would silently let
    a newly-quarantined ticker back into a research candidate pool."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ticker FROM ticker_data_quality WHERE status = 'QUARANTINED'")
        ).fetchall()
    return {r[0] for r in rows}


def get_research_stock_candidates() -> set[str]:
    """get_all_known_stock_tickers() with quarantined tickers excluded --
    the single admission gate for research/backtest candidate-pool
    construction (2026-08-07, see research/recycled_ticker_investigation_
    2026-08-07/REPORT.md and its follow-on remediation).

    Deliberately NOT wired into get_all_known_stock_tickers() or
    get_live_stock_candidates() above -- neither existing function is
    modified by this addition. The investigation confirmed zero
    quarantined tickers are, or can be, reachable via
    get_live_stock_candidates() today (every quarantined ticker is
    delisted-before-today by construction, since the corruption this
    guards against was only ever found in the historical/delisted
    population added by the 2026-08-06 survivorship-bias remediation), so
    there is nothing for a live-path gate to protect against -- leaving
    both functions completely untouched keeps this a strictly additive,
    zero-blast-radius change to live order placement. ALL_TICKERS/
    STOCK_MEMBERSHIP (the frozen research universe) are structurally
    unrelated to this too -- confirmed neither function reads them.

    Any research/backtest driver building a full historical candidate
    pool (as opposed to today's live-eligible subset) should call this
    instead of get_all_known_stock_tickers() directly.
    """
    return get_all_known_stock_tickers() - get_quarantined_tickers()


def get_max_holding_days(ticker: str, config: dict) -> int:
    """Return the max holding days for *ticker*, matching live automation limits.

    is_etf is resolved from the universe's OWN categorization — ETF_TICKERS
    (not STOCK_MEMBERSHIP membership, and not whether *ticker* happens to
    have a live-execution `instruments:` entry yet — config.yaml,
    consolidated 2026-07-31 from watchlist.trading +
    trading212_invest.instrument_mapping, see CLAUDE.md's "Unified
    instrument structure" section). "Is this ticker currently wired for
    live execution" and "is this ticker an ETF" are independent questions;
    dispatching on `instruments:` registration used to look correct only
    because every registered ticker happened to also be in ETF_TICKERS/
    STOCK_MEMBERSHIP — a future universe-broadening candidate can exist in
    ETF_TICKERS before it's ever wired into `instruments:` for live
    trading, and must still get its real 90-day ETF treatment rather than
    silently falling through to the non-ETF default.

    ETFs get trading212_invest.max_holding_days (e.g. 90 — no leverage/swap
    costs, held longer). EVERY OTHER TICKER — a curated STOCK_MEMBERSHIP
    name, a market-cap-ranked rolling-universe research candidate not in
    STOCK_MEMBERSHIP, or an ad-hoc ticker exercised only inside a one-off
    backtest study — gets the single trading212_invest.stock_max_holding_days
    value (e.g. 30 — higher idiosyncratic risk than an ETF).

    Consolidated from a three-way split 2026-08-02: this used to route
    STOCK_MEMBERSHIP tickers through stock_max_holding_days and every other
    non-ETF ticker through a SEPARATE backtest.fallback_max_holding_days —
    two independently-set config values with nothing structurally linking
    them, coincidentally equal (both 30) rather than actually the same
    number. Confirmed via real rolling-universe research (2026-08-02) that
    this was not an edge case: 82 of rolling-40's 115 ever-admitted tickers
    (71%) are real stocks outside STOCK_MEMBERSHIP and therefore hit what
    was the "fallback" path as their DOMINANT, everyday case — the exact
    same "coincidentally equal, not structurally linked" fragility already
    found and fixed once this same day for is_etf inference (see this
    function's own history above). Every real call site's ticker universe
    is stocks and ETFs only (no bond/forex/crypto dispatch exists in this
    codebase post-V2-consolidation), so there was no genuine remaining case
    that needed a distinct, separately-configurable "unknown ticker type"
    value — collapsing to one number removes the fragility with no loss of
    real behaviour.

    This is the single source of truth for per-asset holding limits, used
    by simulate_trades() (analysis/backtest.py) and the live exit checks /
    trade-alert job (data/scheduler.py) so backtested and live holding
    limits always agree for the same ticker.
    """
    t212_cfg = config.get("trading212_invest", {})
    stock_cap = int(t212_cfg.get("stock_max_holding_days", 30))

    if ticker in ETF_TICKERS:
        return int(t212_cfg.get("max_holding_days", stock_cap))
    return stock_cap


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
        windows.append((start, _SENTINEL_END))
    return windows


def _all_source_tickers(csv_text: str) -> set[str]:
    """Every unique ticker symbol that has ever appeared in any row of the
    source CSV -- the full historical roster, not just the tickers already
    known to STOCK_MEMBERSHIP.

    Added 2026-08-02 so refresh_membership_from_source() can discover
    genuinely NEW S&P 500 entrants automatically. Before this, the refresh
    only ever reconciled windows for tickers already present in the fixed,
    hand-curated STOCK_MEMBERSHIP dict -- a company joining the index for
    the first time was never picked up by any scheduled job, however
    recently it ran, because nothing was ever watching for a ticker that
    ISN'T already on the list. This is what actually makes "the daily
    membership refresh already exists, no new job needed" true for live
    trading against the full index rather than a fixed 40-name subset.
    """
    all_syms: set[str] = set()
    for line in csv_text.splitlines()[1:]:
        if not line.strip():
            continue
        comma = line.find(",")
        if comma == -1:
            continue
        tickers_s = line[comma + 1:].strip().strip('"')
        all_syms |= set(tickers_s.split(","))
    all_syms.discard("")
    return all_syms


def refresh_membership_from_source(config: Optional[dict] = None) -> dict:
    """Re-fetch the source dataset and reconcile stored membership windows
    for every candidate stock against it -- STOCK_MEMBERSHIP's original 40
    curated tickers, PLUS (2026-08-02) every other ticker ever seen in the
    source CSV (via _all_source_tickers() above), so a genuinely new S&P
    500 entrant gets tracked automatically the same day it first appears
    in the source, not just the 40 names this project happened to hand-pick
    back in July. Detects both new removals (a ticker's most recent
    window's end_date moves earlier / becomes closed) and new re-additions
    (a new window opens after a prior close).

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

    # Stamped here, not at the end -- this is "did we genuinely talk to the
    # source successfully", the actual thing last_membership_refresh_success
    # exists to answer. A per-ticker reconstruction failure below is a
    # different, already-visible problem (summary["errors"]/a WARNING log)
    # and must not gate this timestamp -- doing so would conflate "the
    # source itself is unreachable" with "one row's CSV was malformed",
    # two failure modes with different remediation.
    try:
        from data.database import set_metadata
        set_metadata(_LAST_REFRESH_SUCCESS_KEY, datetime.utcnow().isoformat())
    except Exception as exc:
        logger.warning("refresh_membership_from_source: could not record success timestamp — %s", exc)

    engine = get_engine()
    fetched_at = datetime.utcnow()

    # Legacy symbols (e.g. "FB") merge into their rename target ("META")
    # via _reconstruct_windows()'s own _KNOWN_TICKER_RENAMES lookup -- they
    # must never be tracked as a separate, standalone ticker here, or the
    # old symbol would show up as its own permanently-closed candidate.
    # ETF_TICKERS never appears in this stock-membership source at all in
    # practice, but excluded defensively for the same reason.
    legacy_symbols = set(_KNOWN_TICKER_RENAMES.keys())
    discovered = _all_source_tickers(csv_text) - legacy_symbols - set(ETF_TICKERS)
    tracked_tickers = sorted(set(STOCK_MEMBERSHIP.keys()) | discovered)

    for ticker in tracked_tickers:
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


def get_last_membership_refresh_success() -> Optional[datetime]:
    """Read last_membership_refresh_success from system_metadata, or None
    if the refresh mechanism has never once completed a fetch successfully
    (fresh install, or every attempt so far has failed)."""
    from data.database import get_metadata
    return get_metadata(_LAST_REFRESH_SUCCESS_KEY)


def is_membership_refresh_stale(last_success: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """True if the point-in-time membership refresh has gone unsuccessful
    for more than MEMBERSHIP_STALE_HOURS (~2 reconciliation cycles).

    Pure -- takes last_success/now explicitly rather than reading
    system_metadata itself, so the Status page and the scheduler job's own
    alert check share this one implementation without either needing a DB
    round-trip inside the function (mirrors main.py:_eod_overdue()'s exact
    shape, including its weekday-only + Monday-before-this-job's-own-time
    exemption -- this job shares the identical Mon-Fri-only, no-weekend-run
    cadence as the EOD job, so the identical false-positive class the
    Monday exemption was built to close applies here too: with no
    exemption, last_success would always be >48h old every Monday until
    that day's own 21:15 UTC run completes, purely from the weekend gap,
    not a genuine problem).

    A None last_success (never once succeeded) is treated as infinitely
    stale, same convention _eod_overdue() uses for a None last_refresh.
    """
    now = now or datetime.utcnow()
    if last_success is not None and getattr(last_success, "tzinfo", None):
        last_success = last_success.replace(tzinfo=None)
    age_hours = (
        (now - last_success).total_seconds() / 3600
        if last_success else float("inf")
    )
    monday_before_job = (
        now.weekday() == 0
        and (now.hour, now.minute) < (_MEMBERSHIP_JOB_HOUR, _MEMBERSHIP_JOB_MINUTE)
    )
    return age_hours > MEMBERSHIP_STALE_HOURS and now.weekday() < 5 and not monday_before_job


def _fetch_wikipedia_current_members(timeout: int = 15) -> set:
    resp = requests.get(
        _WIKIPEDIA_URL, timeout=timeout,
        headers={"User-Agent": "sp500invest-research/1.0 (hello@example.com)"},
    )
    resp.raise_for_status()
    return set(_WIKI_TICKER_RE.findall(resp.text))


def check_wikipedia_lag(check_date: Optional[date] = None) -> dict:
    """Compare today's reconstructed STOCK_MEMBERSHIP eligibility against
    Wikipedia's live current-constituents table and flag any disagreement.

    THIS IS A LAG DETECTOR, NOT INDEPENDENT VERIFICATION. fja05680/sp500's
    own maintainer updates his data by diffing against this exact Wikipedia
    page (see this module's own docstring for the full provenance) -- so
    Wikipedia is this data's own upstream source, not a second opinion. If
    Wikipedia's own current-members table is ever wrong, both sides would
    agree on the same wrong answer and this check would find nothing.
    What it actually detects is narrower and still useful: whether our
    local copy has fallen behind Wikipedia's *current* state, given the
    maintainer's own irregular 1-5 month update cadence (documented in
    this module's docstring) -- i.e. "our stored data may be stale
    relative to its own upstream source", never "verified independently".

    Flags, never blocks -- this is not a new eligibility gate, only an
    operational signal. Never raises; a fetch/parse failure is recorded in
    the returned dict and logged, not propagated. Persists its result to
    system_metadata (_WIKI_LAG_METADATA_KEY) for Status page display.
    """
    from data.database import set_metadata

    check_date = check_date or date.today()
    result: dict = {
        "checked_at": datetime.utcnow().isoformat(),
        "checked": 0,
        "disagreements": [],
        "error": None,
        "note": (
            "Lag detector only, NOT independent verification -- Wikipedia "
            "is fja05680/sp500's own upstream source (its maintainer "
            "updates by diffing against this exact page). See "
            "analysis/universe.py:check_wikipedia_lag()."
        ),
    }
    try:
        wiki_members = _fetch_wikipedia_current_members()
    except Exception as exc:
        logger.warning("check_wikipedia_lag: could not fetch Wikipedia — %s", exc)
        result["error"] = str(exc)
        try:
            set_metadata(_WIKI_LAG_METADATA_KEY, json.dumps(result))
        except Exception as meta_exc:
            logger.warning("check_wikipedia_lag: could not persist result — %s", meta_exc)
        return result

    for ticker in STOCK_MEMBERSHIP:
        result["checked"] += 1
        ours = is_member(ticker, check_date)
        theirs = ticker in wiki_members
        if ours != theirs:
            result["disagreements"].append(
                {"ticker": ticker, "our_member": ours, "wikipedia_member": theirs}
            )

    if result["disagreements"]:
        logger.warning(
            "Wikipedia lag check (lag detector only, NOT independent verification): "
            "%d ticker(s) disagree with our stored membership — %s",
            len(result["disagreements"]), result["disagreements"],
        )
    else:
        logger.info(
            "Wikipedia lag check: %d ticker(s) checked, no disagreement "
            "(lag detector only, not independent verification).",
            result["checked"],
        )

    try:
        set_metadata(_WIKI_LAG_METADATA_KEY, json.dumps(result))
    except Exception as exc:
        logger.warning("check_wikipedia_lag: could not persist result — %s", exc)

    return result
