"""Historical, point-in-time market-cap reconstruction — SEC EDGAR XBRL
quarterly shares-outstanding history joined against stored close prices
(price_data, already fetched by data/ingestion.py — no new price fetch
needed here).

Mirrors analysis/universe.py's own shape deliberately: a free, public,
point-in-time source; an in-process cache so a multi-year, multi-ticker
backtest doesn't pay a DB round-trip per lookup; a "never raises on one
ticker's failure" bulk-fetch entry point; and pure, DB-cache-only read
functions (market_cap()/shares_outstanding_as_of()) that never make a
live network call, the same "read path never calls the external source"
contract analysis/macro.py's release-calendar staleness mechanism and
analysis/universe.py's is_member() both already follow.

Data source: SEC EDGAR's free, no-key XBRL company-concept API
(https://data.sec.gov/api/xbrl/companyconcept/{cik}/{taxonomy}/{tag}.json),
sourced from real 10-Q/10-K cover-page disclosures. Requires a compliant
`User-Agent` header identifying the requester — SEC blocks generic/missing
User-Agents (a plain, unheadered request gets a 403); format follows SEC's
own documented guidance ("Sample Company Name AdminContact@domain.com").
Ticker -> CIK mapping via SEC's free company_tickers.json.

Verified live, directly, before being trusted (2026-08-01) — not assumed
from any prior estimate:
  - NVDA: shares outstanding jumps 623,000,000 -> 2,500,000,000 (4.01x)
    exactly at the 2021 Q2 filing boundary (July 2021 4:1 split), and
    2,460,000,000 -> 24,530,000,000 (9.97x) at the 2024 Q2 filing boundary
    (June 2024 10:1 split). Earliest record: 2009-08-17.
  - AAPL: 861,381,000 -> 5,987,867,000 (6.95x, the real June 2014 7:1
    split) and 4,275,634,000 -> 17,001,802,000 (3.98x, the real August
    2020 4:1 split). Earliest record: 2009-06-27.
  - TSLA: 186,361,726 -> 947,900,733 (5.09x, the real August 2020 5:1
    split) and 1,044,490,015 -> 3,157,752,449 (3.02x, the real August
    2022 3:1 split). Earliest record: 2011-07-31.
  - GOOGL: only resolves via the us-gaap:CommonStockSharesOutstanding
    fallback tag (the dei tag 404s for this filer) — 662,121,000 (10-Q
    filed 2022-04-27, pre-split) -> 13,078,000,000 as of 2022-06-30 (10-Q
    filed 2022-07-27), a ~19.75x jump matching the real July 2022 20:1
    split. This is the live-data case that proved the tag-fallback path
    is genuinely load-bearing, not just a theoretical fallback — GOOGL
    would resolve to zero rows without it.

A real data-quality issue surfaced while verifying GOOGL, not anticipated
up front: XBRL history for a given period_end can be reported more than
once, at different values, across later filings that RESTATE an earlier
balance-sheet date's share count for comparability (most visibly across a
stock split). GOOGL's own 2021-12-31 date appears as both 662,121,000
(10-K filed 2022-02-02, genuinely pre-split) and 13,242,000,000 (10-Q
filed 2022-10-26, restated to post-split terms nine months after the
split actually happened). Point-in-time market-cap ranking needs what was
true — and knowable — as of that date, not a later restatement, so
fetch_shares_outstanding() keeps the EARLIEST-FILED value for each
period_end_date, never the latest. Taking the latest value would silently
apply a stock split to historical dates months before it actually
happened.

Coverage: SEC's XBRL mandate phased in by filer size, so structured data
does not reach back to 2007 uniformly. Directly observed earliest records
above (2009-06-27 AAPL, 2009-08-17 NVDA, 2011-07-31 TSLA — TSLA's later
start is expected, it IPO'd mid-2010) are consistent with the
~mid-2009-for-large-filers / mid-2011-for-smaller-filers phase-in
already documented in CLAUDE.md's "Present-day market-cap ranking bias"
investigation. See that section, and this module's own caller in the
rolling-universe reconstitution research, for the exact coverage-start
date actually used for any given study — determined from the real fetch
results for the study's full candidate pool, not assumed from these four
tickers alone.
"""
import logging
import statistics
import time
from datetime import date, datetime
from typing import Optional

import pandas_market_calendars as mcal
import requests
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data.database import MarketCapHistory, get_engine

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/{cik}/{taxonomy}/{tag}.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC requires a compliant User-Agent identifying the requester -- verified
# live 2026-08-01 that an unheadered request is refused; this follows SEC's
# own documented guidance format ("Sample Company Name AdminContact@sample
# company domain.com").
_USER_AGENT = "SP500Invest Research Data Pipeline contact@example.com"

# SEC's own fair-access guidance caps automated requests at ~10 req/sec.
# This project already has a precedent for a conservative default well
# inside an API's documented ceiling -- analysis/macro.py's FRED backfill
# throttles at ~0.5s between calls -- reused here for consistency rather
# than pushing toward SEC's documented limit.
_SLEEP_BETWEEN_CALLS = 0.5

# Tried in order. Real XBRL filings are inconsistent about which tag a
# company populates for total shares outstanding: the dei cover-page tag
# is what most large filers use, but some (e.g. GOOGL/Alphabet -- verified
# live, see module docstring) never populate it at all and only resolve
# via a us-gaap fallback.
SHARES_TAGS: list[tuple[str, str]] = [
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesIssued"),
]

# Predecessor (pre-reorganisation) CIKs, keyed by the CURRENT CIK that SEC's
# company_tickers.json now resolves the ticker to. A holding-company
# reorganisation creates a genuinely NEW SEC filer that inherits the ticker
# but none of the predecessor's XBRL history, so the ticker resolves to an
# entity with essentially no filings -- and this module's whole purpose is
# historical, point-in-time reconstruction, which needs the predecessor.
#
# Deliberately an explicit, per-company, live-verified map rather than an
# inferred rule: mirroring analysis/universe.py's own _KNOWN_TICKER_RENAMES
# precedent. Auto-guessing a predecessor from a name match would be exactly
# the kind of unprincipled company-specific inference this project's
# research standard warns against, so each entry is verified by hand and
# recorded with the evidence that justified it.
#
# Verified live 2026-08-01:
#   XOM -- SEC now maps ticker XOM to ExxonMobil Holdings Corp
#   (CIK 0002115436, first filing 2026-07-01, 27 filings, zero XBRL shares
#   history). The predecessor EXXON MOBIL CORP (CIK 0000034088) filed a
#   Form 25-NSE (notification of delisting) on 2026-07-02 -- the classic
#   holdco-reorganisation signature -- and carries the full history
#   (dei:EntityCommonStockSharesOutstanding, 68 rows).
PREDECESSOR_CIKS: dict[str, str] = {
    "0002115436": "0000034088",  # ExxonMobil Holdings Corp -> Exxon Mobil Corp
}

_cik_cache: Optional[dict[str, str]] = None
_shares_cache: dict[str, list[tuple[date, float]]] = {}


def _headers() -> dict:
    return {"User-Agent": _USER_AGENT}


def clear_caches() -> None:
    """Drop both in-process caches (CIK map and per-ticker shares series).
    Call after a fresh backfill_shares_outstanding() run in the same
    process, mirroring analysis/universe.py:clear_membership_cache()'s
    rationale -- without this, a stale cache entry could outlive the DB
    write that was meant to replace it."""
    global _cik_cache
    _cik_cache = None
    _shares_cache.clear()


def fetch_cik_map(force: bool = False) -> dict[str, str]:
    """Ticker -> zero-padded 10-digit CIK string, from SEC's free
    company_tickers.json (~10,000 rows, ~800KB). Cached in-process after
    the first call -- this file never needs re-fetching within one
    process lifetime, the same reasoning analysis/universe.py caches
    membership windows per-process.
    """
    global _cik_cache
    if _cik_cache is not None and not force:
        return _cik_cache
    resp = requests.get(_TICKERS_URL, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    out: dict[str, str] = {}
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str")
        if not ticker or cik is None:
            continue
        out[ticker] = str(cik).zfill(10)
    _cik_cache = out
    return out


def to_sec_ticker(ticker: str) -> str:
    """yfinance-style dot tickers (BRK.B) -> SEC's own dash convention
    (BRK-B). Verified live 2026-08-01 that SEC's company_tickers.json
    already uses the dash form natively -- the identical convention this
    project's own yfinance layer already converts dot-tickers to (see
    CLAUDE.md's "V2 stock universe broadening" dot-ticker note), so no
    separate translation table is needed beyond a straight replace.
    """
    return ticker.replace(".", "-").upper()


def _fetch_concept(cik: str, taxonomy: str, tag: str) -> Optional[list[dict]]:
    # SEC's companyconcept endpoint requires the zero-padded CIK prefixed
    # with the literal "CIK" (e.g. "CIK0001045810") -- fetch_cik_map()
    # deliberately stores the bare zero-padded number (matching SEC's own
    # company_tickers.json convention and this project's fetch_cik_map()
    # docstring), so the prefix is added here, once, at the one call site
    # that actually builds a request URL from it.
    sec_cik = cik if cik.upper().startswith("CIK") else f"CIK{cik}"
    url = _CONCEPT_URL.format(cik=sec_cik, taxonomy=taxonomy, tag=tag)
    resp = requests.get(url, headers=_headers(), timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data.get("units", {}).get("shares", [])


def _is_amendment(row: dict) -> bool:
    """True if *row* came from an amended filing (10-Q/A, 10-K/A, ...)."""
    return str(row.get("form") or "").strip().upper().endswith("/A")


def _dedupe_earliest_filed(raw: list[dict]) -> list[dict]:
    """One row per `end` (period_end) date.

    Default rule: keep the EARLIEST-FILED value -- see this module's docstring
    for why (a later filing can RESTATE an earlier period's share count, most
    visibly across a stock split; the earliest-filed value is what was
    genuinely true, and knowable, as of that period_end date).

    Exception -- SAME-PERIOD AMENDMENTS: if a later filing is an amendment
    (form ending "/A") of the SAME fiscal period (same fy AND fp) as the
    earliest-filed row, the amendment wins. An amendment of the same period is
    the filer CORRECTING ITS OWN ERROR, which is categorically different from
    a later period's filing restating an earlier balance for comparability.
    Earliest-filed is right for the restatement case and WRONG for the
    correction case, and `form` + (fy, fp) is what separates them.

    Verified live 2026-08-01 against real SEC data across all 487 resolved
    S&P 500 candidates -- 14 real conflicts across 13 tickers, e.g.:
      - WFC 2023-07-21: original 10-Q (filed 2023-08-01) reported
        1,823,028,137; the filer's own 10-Q/A three days later (2023-08-04)
        corrected it to 3,658,783,337, which fits smoothly between the
        neighbouring quarters (3,752,223,519 -> 3,631,639,714). Confirmed by
        reading BOTH real filing cover pages, not inferred from the JSON.
      - MAR 2010-04-09: 361,332,043,000,000 corrected to 361,332,043 (a
        literal 1e6 unit error).
      - ROST 2010-11-18: 19,084,540 corrected to 119,084,540 (dropped digit).
      - ZTS 2019-08-02: 24,333,301 corrected to 477,557,942.

    The filed-date comparison is >=, not >, deliberately: a filer can file the
    correction on the SAME DAY as the original. Two of the 14 real conflicts
    do exactly that (ROST 2010-11-18 and BSX 2013-04-30, each with the 10-Q
    and its 10-Q/A both filed the same day), and a strict > silently left both
    on the erroneous original. >= also makes the outcome deterministic in that
    case, rather than depending on which same-dated row the earliest-filed
    min() happened to encounter first in the raw feed.

    The (fy, fp) equality requirement is load-bearing, not defensive: two of
    the 14 conflicts found (BF.B 2017-04-30, PPG 2014-12-31) are amendments
    filed YEARS later from a DIFFERENT fiscal period -- genuine retroactive
    restatements (PPG's is exactly 2x, its real 2:1 split). Those must keep
    the earliest-filed value, and this guard is what ensures they do.

    Deliberately NOT keyed on SEC's own `frame` field, which looks like a
    natural "canonical value" signal but is not: verified live that SEC
    assigns frame CY2021Q4I to GOOGL's RESTATED post-split 13,242,000,000,
    so a frame-based rule would silently reverse the documented,
    intentional GOOGL behaviour this function exists to protect.

    Extracted so the companyconcept and companyfacts paths below share ONE
    implementation of this rule rather than two copies that could drift --
    the same "exactly one implementation of any given decision" principle
    CLAUDE.md's live/backtest parity invariant already establishes.
    """
    best_by_end: dict[str, dict] = {}
    chosen_raw: dict[str, dict] = {}
    for row in raw:
        end = row.get("end")
        val = row.get("val")
        filed = row.get("filed") or "9999-99-99"  # missing filed date sorts last, never wins a tie
        # A non-positive share count is never a valid fact. Real filings do
        # contain them (verified live 2026-08-01: HBAN 2011-03-31, KO
        # 2009-10-23, RCL 2012-02-27, VRSK 2012-04-27 each carry a single 0
        # row) -- and because shares_outstanding_as_of() forward-fills, one
        # stray 0 would silently zero out market cap for every date from that
        # filing until the next one, ranking a mega-cap below every peer.
        if not end or val is None or float(val) <= 0:
            continue
        cur = best_by_end.get(end)
        if cur is None or filed < cur["filed"]:
            best_by_end[end] = {"end": end, "val": val, "filed": filed}
            # Keep the ORIGINATING raw row too: the deduped dict above is
            # deliberately narrow ({end, val, filed} -- the shape callers and
            # tests expect), but the same-period amendment check below needs
            # this row's own fy/fp, which that narrow shape does not carry.
            chosen_raw[end] = row
    # Same-period amendment override -- see this function's own docstring.
    by_end: dict[str, list[dict]] = {}
    for row in raw:
        end = row.get("end")
        val = row.get("val")
        if not end or val is None or float(val) <= 0 or end not in best_by_end:
            continue
        by_end.setdefault(end, []).append(row)

    for end, group in by_end.items():
        chosen = best_by_end[end]
        chosen_src = chosen_raw.get(end, {})
        amendments = [
            r for r in group
            if _is_amendment(r)
            and r.get("fy") == chosen_src.get("fy")
            and r.get("fp") == chosen_src.get("fp")
            and (r.get("filed") or "9999-99-99") >= chosen["filed"]
        ]
        if not amendments:
            continue
        # Most recent correction wins if a period was amended more than once.
        latest = max(amendments, key=lambda r: r.get("filed") or "0000-00-00")
        if float(latest["val"]) == float(chosen["val"]):
            continue
        logger.info(
            "market_cap: period_end %s superseded by same-period amendment (%s filed %s): "
            "%s -> %s (original %s filed %s).",
            end, latest.get("form"), latest.get("filed"),
            f'{float(chosen["val"]):,.0f}', f'{float(latest["val"]):,.0f}',
            chosen_src.get("form"), chosen.get("filed"),
        )
        best_by_end[end] = {"end": end, "val": latest["val"], "filed": latest["filed"]}

    return sorted(best_by_end.values(), key=lambda r: r["end"])


# A row whose value is more than this multiple of the series' own median (or
# less than its reciprocal) is treated as a data defect, not a corporate
# action. Chosen empirically, not guessed -- see _drop_magnitude_outliers().
_OUTLIER_MEDIAN_MULTIPLE = 100.0

# Below this many observations a median is not robust enough to test against;
# the check is skipped entirely rather than risk inverting (see the function's
# own docstring for the real tickers that motivated this).
_OUTLIER_MIN_ROWS = 5


def _drop_magnitude_outliers(rows: list[dict], cik: str = "") -> list[dict]:
    """Drop rows whose share count is implausible by orders of magnitude
    relative to the series' OWN median -- a defect class the zero/negative
    guard cannot catch, because these values are wrong rather than absent.

    Why this is needed at all, and why it is separate from the amendment fix
    above: a systemic scan of all 487 resolved S&P 500 candidates (live SEC
    data, 2026-08-01) found 45 spike-and-revert anomalies across 32 tickers.
    Only a minority were ever amended by the filer, so the amendment rule
    alone leaves most of them in place. The dominant signatures are unit-scale
    errors and truncation, e.g.:
      - ORCL 2012-09-17: 4,819,056,000,000,000 vs a ~4.09e9 median (1e6 too big)
      - AMD  2012-07-27:   707,555,106,000,000 vs a ~9.87e8 median
      - QCOM 2011-10-31:     1,680,984,426,000 vs a ~1.47e9 median (1e3 too big)
      - CRM  2011-04-30:                   134 vs a ~7.27e8 median (truncated)
      - ICE  2013-08-05 / CSX 2011-04-01:    1 (placeholder)
    Because shares_outstanding_as_of() forward-fills, a single retained 1e6
    error would poison market cap for every date from that filing until the
    next one -- ranking the affected company as the largest in the world.

    Rows are DROPPED, not corrected: unlike the amendment case there is no
    authoritative corrected value to substitute, so the honest action is to
    fall back to forward-filling the last good filing. This mirrors the
    zero-guard's existing drop-and-forward-fill behaviour.

    The 100x threshold was chosen empirically from the real distribution, not
    picked for roundness. Sweeping it across all 487 tickers, the flagged set
    is EXACTLY THE SAME (68 rows / 44 tickers) anywhere from 50x to 200x --
    a genuine gap in the data, so the result is insensitive to the precise
    value. It also clears every real corporate action by a wide margin: the
    largest genuine value/median ratio among this module's own documented
    split tickers is NVDA at 40.25x (its real 4:1 and 10:1 splits combined),
    with GOOGL's 20:1 split at 18.82x and TSLA at 22.87x -- all well inside
    100x, and none flagged.

    Skipped entirely for series shorter than _OUTLIER_MIN_ROWS, where the
    median is not robust and the test can INVERT -- flagging the one good row
    instead of the bad ones. Real cases found: PSKY (n=3, median 1,000, whose
    single genuine 1,071,666,977 row would otherwise be flagged) and FOX/FOXA
    (n=1, whole series is a placeholder `1`).
    """
    if len(rows) < _OUTLIER_MIN_ROWS:
        return rows
    values = [float(r["val"]) for r in rows]
    median = statistics.median(values)
    if median <= 0:
        return rows

    hi = median * _OUTLIER_MEDIAN_MULTIPLE
    lo = median / _OUTLIER_MEDIAN_MULTIPLE
    kept, dropped = [], []
    for r in rows:
        v = float(r["val"])
        (dropped if (v > hi or v < lo) else kept).append(r)

    for r in dropped:
        logger.warning(
            "market_cap: dropping implausible shares-outstanding row for CIK %s -- "
            "period_end %s value %s is %.1fx the series median (%s); treated as a "
            "filing/unit defect, not a corporate action. Forward-filling the prior "
            "good filing instead.",
            cik or "?", r.get("end"), f'{float(r["val"]):,.0f}',
            float(r["val"]) / median, f"{median:,.0f}",
        )
    # Never return an empty series purely because the median was itself skewed
    # by a majority-bad series -- if everything would be dropped, keep the
    # original rows and let the caller see the raw data rather than nothing.
    return kept if kept else rows


def _fetch_company_facts(cik: str) -> Optional[dict]:
    """Full companyfacts payload for *cik* -- every XBRL concept the filer
    has ever reported, across all taxonomies, in ONE request.

    Exists because SEC's per-concept companyconcept endpoint is not reliable:
    verified live 2026-08-01 that for a large set of real filers (KO, WFC,
    ABT, AMT, CB, ... 27 of the 44 previously-unresolvable S&P 500
    candidates) it returns HTTP 200 with an EMPTY `units.shares` array for a
    tag that companyfacts, for the identical CIK and identical tag, returns
    full history for (KO: dei:EntityCommonStockSharesOutstanding, 71 rows).
    Reproduced in a clean, unloaded environment, so this is a genuine
    endpoint inconsistency on SEC's side, not rate-limiting or a transient.
    """
    sec_cik = cik[3:] if cik.upper().startswith("CIK") else cik
    resp = requests.get(_FACTS_URL.format(cik=sec_cik), headers=_headers(), timeout=60)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("facts", {})


def _shares_from_facts(facts: dict, taxonomy: str, tag: str) -> list[dict]:
    """Rows for one taxonomy:tag out of an already-fetched companyfacts
    payload, in the same shape _fetch_concept() returns."""
    return facts.get(taxonomy, {}).get(tag, {}).get("units", {}).get("shares", []) or []


def fetch_shares_outstanding(cik: str) -> tuple[list[dict], Optional[str]]:
    """Try each tag in SHARES_TAGS in order against *cik*; return
    (rows, tag_used) for the first tag that returns any data.

    Resolution order -- a strict fallback chain, each step tried only when
    every earlier step yielded nothing:
      1. companyconcept, each tag in SHARES_TAGS order (the original path).
      2. companyfacts, re-checking the SAME tags in the SAME order. The tag
         set is unchanged -- this step exists because the ENDPOINT, not the
         tag list, was the gap: companyconcept returns 200-with-empty for
         many real filers whose data companyfacts serves in full (see
         _fetch_company_facts()'s own docstring for the live evidence).
         tag_used is suffixed " (companyfacts)" so the provenance of every
         stored row is visible in market_cap_history.source.
      3. If *cik* is a known post-reorganisation successor entity
         (PREDECESSOR_CIKS), retry the whole chain against the predecessor
         CIK, which is where the historical filings actually live.

    rows is deduped to one row per `end` (period_end) date, keeping the
    EARLIEST-FILED value for that date -- see _dedupe_earliest_filed().

    Returns ([], None) if nothing resolves -- callers treat this as "no XBRL
    shares-outstanding history available for this company," not an error.
    """
    for taxonomy, tag in SHARES_TAGS:
        try:
            raw = _fetch_concept(cik, taxonomy, tag)
        except Exception as exc:
            logger.warning("market_cap: fetch failed for CIK %s %s:%s -- %s", cik, taxonomy, tag, exc)
            raw = None
        if not raw:
            continue
        rows = _drop_magnitude_outliers(_dedupe_earliest_filed(raw), cik)
        if rows:
            return rows, f"{taxonomy}:{tag}"

    # 2. companyfacts fallback -- same tags, more reliable endpoint.
    try:
        facts = _fetch_company_facts(cik)
    except Exception as exc:
        logger.warning("market_cap: companyfacts fetch failed for CIK %s -- %s", cik, exc)
        facts = None
    if facts:
        for taxonomy, tag in SHARES_TAGS:
            raw = _shares_from_facts(facts, taxonomy, tag)
            if not raw:
                continue
            rows = _drop_magnitude_outliers(_dedupe_earliest_filed(raw), cik)
            if rows:
                logger.info(
                    "market_cap: CIK %s resolved via companyfacts (%s:%s, %d rows) after "
                    "companyconcept returned nothing for every tag.",
                    cik, taxonomy, tag, len(rows),
                )
                return rows, f"{taxonomy}:{tag} (companyfacts)"

    # 3. Known post-reorganisation successor -- retry against the predecessor.
    bare = cik[3:] if cik.upper().startswith("CIK") else cik
    predecessor = PREDECESSOR_CIKS.get(bare)
    if predecessor:
        logger.info(
            "market_cap: CIK %s has no XBRL shares history; retrying against known "
            "predecessor CIK %s (holding-company reorganisation).", cik, predecessor,
        )
        rows, tag_used = fetch_shares_outstanding(predecessor)
        if rows:
            return rows, f"{tag_used} (predecessor CIK {predecessor})"

    return [], None

def stored_cik(ticker: str) -> Optional[str]:
    """The CIK already stored against *ticker* in market_cap_history, if
    any -- read directly, no cache (called rarely, only during a backfill
    run's own per-ticker CIK-change check, never on the market_cap() hot
    read path)."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT DISTINCT cik FROM market_cap_history WHERE ticker = :t AND cik IS NOT NULL"),
            {"t": ticker},
        ).fetchall()
    ciks = {r[0] for r in row}
    if not ciks:
        return None
    # Every row written by a single backfill_shares_outstanding() call uses
    # one cik value, so more than one distinct value already stored means a
    # PRIOR run of this same detection already should have caught the
    # change -- return the most recently fetched one as "the" stored CIK
    # for comparison purposes, rather than raising, since acting on the
    # newest is more useful than refusing to compare at all.
    if len(ciks) > 1:
        return _most_recent_cik(ticker)
    return next(iter(ciks))


def _most_recent_cik(ticker: str) -> Optional[str]:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cik FROM market_cap_history WHERE ticker = :t AND cik IS NOT NULL "
                "ORDER BY fetched_at DESC LIMIT 1"
            ),
            {"t": ticker},
        ).fetchone()
    return row[0] if row else None


def detect_cik_change(ticker: str, new_cik: str) -> Optional[str]:
    """Compare *new_cik* (what fetch_cik_map() just resolved for *ticker*)
    against whatever CIK is already stored for it in market_cap_history.

    Returns the OLD cik if it differs from *new_cik* (a confirmed symbol-
    recycling event -- see MarketCapHistory's own docstring for why this
    is a real, distinct risk from a same-company rename), or None if there
    is no prior stored CIK to compare against, or it matches.

    This can only ever detect a change that happens BETWEEN two separate
    backfill_shares_outstanding() runs for the same ticker -- SEC EDGAR
    filings are genuinely CIK-scoped, so a single run can never itself
    blend two companies' shares data together. It cannot retroactively
    detect a recycling event that already happened before this project's
    first backfill of a given ticker (there is no prior stored CIK to
    compare against in that case) -- a stated, permanent limitation, not
    an oversight; see this module's own docstring section on ticker-symbol
    recycling for the fuller picture, including why the PRICE side of this
    risk (price_data has no company-identity concept at all) is not fully
    closed by this mechanism either.
    """
    old_cik = stored_cik(ticker)
    if old_cik is None or old_cik == new_cik:
        return None
    return old_cik


def backfill_shares_outstanding(tickers: list[str], sleep: float = _SLEEP_BETWEEN_CALLS) -> dict:
    """Fetch and store quarterly shares-outstanding history for every
    ticker in *tickers*, writing to market_cap_history. Never raises on a
    single ticker's failure -- logs and continues, matching this
    codebase's "never raises" bulk-fetch convention (see
    analysis/universe.py:refresh_membership_from_source() and
    analysis/macro.py:backfill_macro_indicators()).

    Before writing, compares the freshly-resolved CIK against whatever CIK
    is already stored for that ticker (detect_cik_change()) -- a genuine
    mismatch means the ticker symbol has been reassigned to a different
    SEC filer since the last backfill (ticker-symbol recycling, distinct
    from a same-company rename), logged loudly and recorded in the
    returned summary under "cik_changed" rather than silently overwritten.
    The new CIK's data is still written (it is, after all, the genuinely
    correct current mapping) -- this is a detect-and-flag mechanism, not a
    block, since refusing to store current, correct data would make the
    ticker permanently un-refreshable.

    Returns a summary dict:
      {"succeeded": [...], "no_cik": [...], "no_data": [...],
       "errors": {ticker: str(exc)}, "tag_used": {ticker: "dei:..."},
       "rows_written": {ticker: N}, "cik_changed": {ticker: {"old": ..., "new": ...}}}
    """
    cik_map = fetch_cik_map()
    summary: dict = {
        "succeeded": [], "no_cik": [], "no_data": [], "errors": {},
        "tag_used": {}, "rows_written": {}, "cik_changed": {},
    }
    engine = get_engine()

    for ticker in tickers:
        sec_ticker = to_sec_ticker(ticker)
        cik = cik_map.get(sec_ticker)
        if cik is None:
            summary["no_cik"].append(ticker)
            logger.warning("market_cap: no CIK found for %s (SEC ticker %s)", ticker, sec_ticker)
            continue

        old_cik = detect_cik_change(ticker, cik)
        if old_cik is not None:
            summary["cik_changed"][ticker] = {"old": old_cik, "new": cik}
            logger.warning(
                "market_cap: %s now resolves to CIK %s, previously CIK %s -- this ticker symbol has "
                "likely been reassigned to a different company (symbol recycling, not a rename). "
                "price_data has no company-identity concept, so this ticker's PRICE history may "
                "silently blend two unrelated companies together around the switchover date -- "
                "treat any momentum/market-cap reading spanning that boundary with caution.",
                ticker, cik, old_cik,
            )

        try:
            rows, tag_used = fetch_shares_outstanding(cik)
        except Exception as exc:
            summary["errors"][ticker] = str(exc)
            logger.warning("market_cap: fetch_shares_outstanding failed for %s (CIK %s) -- %s", ticker, cik, exc)
            time.sleep(sleep)
            continue

        if not rows:
            summary["no_data"].append(ticker)
            time.sleep(sleep)
            continue

        summary["tag_used"][ticker] = tag_used
        fetched_at = datetime.utcnow()
        n_written = 0
        with engine.begin() as conn:
            for r in rows:
                try:
                    period_end = datetime.strptime(r["end"], "%Y-%m-%d").date()
                except ValueError:
                    continue
                # r["filed"] is already fetched (used by fetch_shares_outstanding()'s
                # own earliest-filed-value dedup) -- persist it here too, since it's
                # what point-in-time reads must actually gate on (see
                # MarketCapHistory's own docstring and shares_outstanding_as_of()'s
                # below for why period_end_date alone is look-ahead-biased).
                filed_str = r.get("filed")
                try:
                    filed_dt = datetime.strptime(filed_str, "%Y-%m-%d").date() if filed_str else None
                except ValueError:
                    filed_dt = None
                stmt = sqlite_insert(MarketCapHistory).values(
                    ticker=ticker,
                    period_end_date=period_end,
                    shares_outstanding=float(r["val"]),
                    source=tag_used,
                    cik=cik,
                    filed_date=filed_dt,
                    fetched_at=fetched_at,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ticker", "period_end_date"],
                    set_={
                        "shares_outstanding": stmt.excluded.shares_outstanding,
                        "source": stmt.excluded.source,
                        "cik": stmt.excluded.cik,
                        "filed_date": stmt.excluded.filed_date,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                conn.execute(stmt)
                n_written += 1
        summary["rows_written"][ticker] = n_written
        summary["succeeded"].append(ticker)
        time.sleep(sleep)

    return summary


def _get_shares_series(ticker: str) -> list[tuple[date, float]]:
    """Read *ticker*'s stored quarterly shares-outstanding series, sorted
    by the date each observation actually became public (`filed_date`) --
    served from an in-process cache after the first call (see
    clear_caches()). Read-only, never calls SEC -- the read path must stay
    fast enough for a multi-year backtest's per-date lookups, the same
    "read path never calls the external source" contract
    analysis/universe.py:get_membership_windows() and
    analysis/macro.py's release-calendar cache both already follow.

    The "date" in each returned (date, shares) pair is `filed_date` when
    known, falling back to `period_end_date` only for a row where
    filed_date is genuinely unknown (rows written before this field
    existed, or a synthetic test fixture) -- see shares_outstanding_as_of()
    for why filed_date, not period_end_date, is the point-in-time-correct
    field to gate on. Sorted by that same effective date, since a real
    filing's own filed_date is not guaranteed to sort identically to its
    period_end_date across every row (restatement filings in particular
    can file well out of period-end order) — see this module's own
    docstring / MarketCapHistory's docstring for the full rationale.
    """
    if ticker in _shares_cache:
        return _shares_cache[ticker]

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT period_end_date, filed_date, shares_outstanding FROM market_cap_history "
                "WHERE ticker = :t"
            ),
            {"t": ticker},
        ).fetchall()

    def _to_date(raw) -> date:
        return raw if isinstance(raw, date) else datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()

    out: list[tuple[date, float]] = []
    for period_end_raw, filed_raw, shares in rows:
        effective = _to_date(filed_raw) if filed_raw is not None else _to_date(period_end_raw)
        out.append((effective, float(shares)))
    out.sort(key=lambda pair: pair[0])
    _shares_cache[ticker] = out
    return out


def shares_outstanding_as_of(ticker: str, as_of: date) -> Optional[float]:
    """Most recent shares-outstanding figure that was actually PUBLIC
    (filed_date) on or before *as_of* -- forward-filled from the most
    recent such filing, NEVER looking ahead to a later one.

    Deliberately gates on filed_date, not period_end_date (found and fixed
    2026-08-01 -- see MarketCapHistory's own docstring for the full
    rationale): a quarter's real shares-outstanding figure typically isn't
    filed until 4-8 weeks after the quarter itself ends, so gating on
    period_end_date alone would let a reconstitution query "know" a
    filing's contents before it was actually public -- a fresh instance of
    the exact hindsight-bias mistake this whole project's point-in-time
    membership work already exists to eliminate, just at the filing-lag
    layer instead of the index-membership layer. A row with no filed_date
    on record (see _get_shares_series()) falls back to its own
    period_end_date, matching this function's pre-fix behaviour for that
    one row rather than silently discarding it.

    None if no filing exists on or before that date (either no data at all
    for this ticker, or *as_of* predates the ticker's earliest stored,
    actually-public filing).
    """
    series = _get_shares_series(ticker)
    result: Optional[float] = None
    for d, shares in series:
        if d <= as_of:
            result = shares
        else:
            break
    return result


def _close_price_as_of(ticker: str, as_of: date) -> Optional[float]:
    """Most recent stored close price on or before *as_of*, from
    price_data (already fetched by data/ingestion.py -- no new price
    fetch needed here). Uses SQLite's date() function for the comparison,
    not a raw string `<=`, matching the convention documented in
    CLAUDE.md's "T212 P&L reconstruction" section (a naive-datetime
    Python string and a DateTime column's stringified form don't always
    compare correctly as raw text).
    """
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT close FROM price_data WHERE ticker = :t "
                "AND date(date) <= date(:d) AND close IS NOT NULL AND close > 0 "
                "ORDER BY date DESC LIMIT 1"
            ),
            {"t": ticker, "d": as_of.isoformat()},
        ).fetchone()
    return float(row[0]) if row is not None and row[0] is not None else None


def market_cap(ticker: str, as_of: date) -> Optional[float]:
    """Market cap for *ticker* as of *as_of* — shares_outstanding_as_of()
    (forward-filled from the most recent XBRL filing on or before as_of)
    times the most recent stored close price on or before as_of.

    Returns None whenever either input is unavailable for that date —
    never guesses. This is the single point-in-time-correct entry point
    this module exists to provide: neither input ever looks ahead of
    *as_of*, so a value returned here reflects only what was knowable on
    that date.
    """
    shares = shares_outstanding_as_of(ticker, as_of)
    if shares is None:
        return None
    price = _close_price_as_of(ticker, as_of)
    if price is None:
        return None
    return shares * price


# ---------------------------------------------------------------------------
# Staleness check — a candidate identified as eligible at one quarterly
# reconstitution can be delisted or acquired before the next one runs (up to
# ~3 months later for a genuinely quarterly cadence). Relying on the next
# scheduled reconstitution to notice is reactive: it leaves a real position
# open, and — in the skip-and-continue admission design this project's live
# order placement already uses (analysis/decision.py /
# brokers/trading212.py) — occupying a capacity slot that mechanism was
# specifically built not to waste, for as long as ~3 months. Relying on a
# broker-side order rejection is not sufficient either: purely reactive
# (only fires when an order is actually attempted), and this codebase
# already has one known rough edge in broker error-handling in this exact
# area (see the T212 error-alert cooldown/gating history elsewhere in this
# project). A delisted or acquired company simply stops producing new price
# rows — the EOD price-pull job has nothing left to fetch for it — so a
# genuine delisting is directly, proactively detectable from price_data
# alone, without waiting for any broker interaction at all.
# ---------------------------------------------------------------------------

# Default staleness threshold, in genuine NYSE TRADING sessions (not
# calendar days — see trading_sessions_since()'s own docstring for why a
# calendar-day threshold would misclassify an ordinary market holiday as
# staleness). 5 trading sessions (one trading week) is deliberately tight
# enough to catch a genuine delisting quickly relative to a ~3-month
# reconstitution cycle, while comfortably absorbing an isolated EOD-pull
# hiccup (a single missed fetch is 1 stale session, well under this) without
# false-flagging a perfectly healthy ticker.
DEFAULT_STALE_TRADING_DAYS = 5

_nyse_calendar_cache = None


def _nyse_calendar():
    """NYSE trading calendar, cached in-process after the first call — the
    same "expensive external resource, cheap to reuse" rationale
    analysis/universe.py and analysis/macro.py's own in-process caches
    already follow. All V2 stock candidates are US-listed (the S&P 500
    candidate pool this module's callers draw from), so NYSE is the correct
    single reference calendar — matching CLAUDE.md's own documented
    convention of using the NYSE calendar for every US-listed ticker
    (data/scheduler.py's exchange-calendar handling for the watchlist).
    """
    global _nyse_calendar_cache
    if _nyse_calendar_cache is None:
        _nyse_calendar_cache = mcal.get_calendar("NYSE")
    return _nyse_calendar_cache


def trading_sessions_since(last_date: date, as_of: date) -> int:
    """Count of genuine NYSE trading sessions strictly after *last_date*,
    up to and including *as_of*.

    Deliberately NOT a calendar-day subtraction: a market holiday (e.g.
    Thanksgiving, Christmas) means a perfectly healthy, actively-traded
    ticker has no new row for that day either — a naive
    `(as_of - last_date).days` threshold would misclassify that as
    staleness. Counting real trading sessions instead means a holiday (or a
    holiday-adjacent long weekend) never inflates the gap: only days the
    market was genuinely open, and the ticker still had no new data, count
    toward it.

    Returns 0 when *as_of* is on or before *last_date* (nothing to count).
    """
    if as_of <= last_date:
        return 0
    schedule = _nyse_calendar().schedule(start_date=last_date.isoformat(), end_date=as_of.isoformat())
    # schedule's index covers [last_date, as_of] inclusive on both ends when
    # both are themselves trading days -- exclude last_date itself so a
    # same-session read (last_date == as_of, already short-circuited above)
    # and a ticker priced exactly through the prior session both count 0.
    sessions_after = [d for d in schedule.index.date if d > last_date]
    return len(sessions_after)


def latest_price_date(ticker: str) -> Optional[date]:
    """Most recent stored price_data date for *ticker*, or None if the
    ticker has no price history at all. Read-only, no network call."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT MAX(date) FROM price_data WHERE ticker = :t "
                "AND close IS NOT NULL AND close > 0"
            ),
            {"t": ticker},
        ).fetchone()
    if row is None or row[0] is None:
        return None
    raw = row[0]
    return raw if isinstance(raw, date) else datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()


def is_price_data_stale(
    ticker: str, as_of: date, max_stale_trading_days: int = DEFAULT_STALE_TRADING_DAYS,
) -> Optional[bool]:
    """True if *ticker*'s price data has gone stale as of *as_of* — its most
    recent stored price date is more than *max_stale_trading_days* genuine
    NYSE trading sessions behind *as_of* (see trading_sessions_since() for
    why this is trading-session-based, not a naive calendar-day gap).

    A candidate that reaches this state between one quarterly reconstitution
    and the next has, in practice, stopped being fed by the EOD price-pull
    job — the direct, proactive signature of a genuine delisting or
    acquisition, detectable from price_data alone without waiting for a
    broker-side order rejection.

    Returns None — "unknown," never an affirmative staleness claim — when
    the ticker has no price history at all (nothing to compare against;
    a genuinely brand-new candidate with zero rows is a different, prior
    concern for whatever eligibility check calls this, not staleness).
    """
    last = latest_price_date(ticker)
    if last is None:
        return None
    gap = trading_sessions_since(last, as_of)
    is_stale = gap > max_stale_trading_days
    if is_stale:
        logger.warning(
            "market_cap: %s price data is stale as of %s -- last stored price %s, "
            "%d genuine NYSE trading session(s) behind (threshold %d). "
            "Marking ineligible -- likely delisted/acquired since the last refresh.",
            ticker, as_of.isoformat(), last.isoformat(), gap, max_stale_trading_days,
        )
    return is_stale
