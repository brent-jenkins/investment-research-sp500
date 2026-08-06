"""APScheduler-based scheduled jobs: daily runs with per-exchange holiday awareness.

Run schedule (UTC unless noted):
  14:30  Stock order submission — every day, gated internally by
                       _is_nyse_open_now() (2026-08-03 fix — found still
                       using a bare day_of_week="mon-fri" cron filter with
                       no calendar/session check of its own at all, a real
                       gap on a US market holiday) — submit queued stock
                       entries, 60 minutes after NYSE's own regular open
                       (moved from 13:30 2026-08-02 — see
                       _stock_order_submission_job())
  21:15  Membership refresh — refresh point-in-time index-membership windows
                       15 minutes ahead of the EOD job's own trading decisions
  21:30  End of day  — full data refresh, then place/exit orders (all exchanges)
  Hourly (every day, all 24 hours) — two independently-scheduled jobs, each
                       self-gating internally rather than via a fixed cron
                       window (2026-08-03 — see _market_hours_check_job()/
                       _account_state_job() and CLAUDE.md's "Splitting the
                       hourly trade-alert job" section):
                         - market_hours_check — order-status poll + exit
                           check, real work only when
                           _is_nyse_extended_hours_open() (2026-08-04 —
                           widened from the regular-hours-only
                           _is_nyse_open_now() to also cover T212's
                           pre-market/after-hours/overnight sessions; see
                           "extend Open Trade Monitor..." below)
                         - account_state_refresh — position reconciliation +
                           account snapshot, unconditional every firing
  09:00  Weekly summary (Sunday, *Europe/London* — not UTC) — send the weekly
                       summary email (see output/briefing.py:send_weekly_summary_email())

The 07:00 (Tue-Fri) pre-market and 13:30 US-open "analysis only" runs that
used to sit between the old Monday-only job and the EOD run were removed
2026-07-31 — confirmed genuinely redundant, not just assumed. Both ever did
was call maybe_exit_positions() (no BUY phase — analysis_only always skips
it, and in the Tue-Fri pre-market job's case, its own data pull for
"exchanges that just closed" had already become a permanent no-op: the
current V2 universe is exclusively NYSE/FX-listed instruments, and NYSE
never satisfies "closed before 07:00 UTC" — confirmed directly by calling
_exchanges_closed_before() against the live config). At the time this was
removed, the hourly trade alert job (the single, unconditional
_trade_alert_job() this file used to register — split 2026-08-03 into
market_hours_check/account_state_refresh, see those functions' own
docstrings) already called maybe_exit_positions() at the exact same 07:00
minute and again at 13:00/14:00 either side of 13:30 — so neither run
added anything the hourly cadence didn't already cover. See CLAUDE.md's
"Pre-market/US-open scheduled runs removed" section for the full
investigation. (Correction, 2026-08-03: the successor market_hours_check
job now SKIPS real exit-check work at 07:00/13:00 UTC specifically,
since NYSE isn't open at either time — but this doesn't undermine the
original redundancy finding, since a 07:00/13:00 UTC exit check was
already evaluating against unchanged, stale price data either way; the
practical outcome — no meaningful exit decision happens at those hours —
is identical, only the mechanism changed from "an inert call" to "a
skipped call".)

(Second correction, 2026-08-04: the 2026-08-03 correction above described
the regular-hours-only mechanism as it stood that day; it no longer holds
as stated. Both 07:00 UTC and 13:00 UTC now fall WITHIN an extended
session year-round — verified directly, not assumed, across both DST
seasons: 07:00 UTC sits inside the overnight session in both summer and
winter, and 13:00 UTC sits inside pre-market in both (pre-market starts
at 04:00 ET, which is 08:00 UTC in summer/EDT and 09:00 UTC in
winter/EST — either way still before 13:00 UTC, and well before regular
open at 13:30 UTC/14:30 UTC respectively). So once market_hours_check_job()
moved onto the wider check, BOTH hours now trigger a real exit-check call,
not a skipped one, consistently across the year. This is expected, not a
regression: paired with the separate live-pricing fix (see "live pricing
for the exit check"), an exit decision at either hour can now genuinely
differ, since the price it evaluates against can genuinely have moved in
the corresponding extended session, which is the whole point of both
changes together. None of this affects the original 2026-07-31 redundancy
finding about the removed pre-market/US-open jobs themselves, which never
gained live pricing or extended-hours recognition and were removed on
their own, independent merits.)

The Monday-only 07:00 UTC "Monday Pre-Market" job (stale weekend BUY order
cancellation + an order-logic re-run) was removed entirely 2026-08-03 for
the identical reason, once checked directly rather than assumed:
_cancel_stale_weekend_orders() guarded against a Friday-21:30-UTC EOD order
sitting unfilled all weekend — a real risk under the old architecture,
where a decision queued at EOD was submitted immediately. Under the
current decide-then-submit architecture (queue at EOD, submit at 14:30 UTC
mid-session, hours before NYSE close), no order is ever placed anywhere
near market close or over a weekend in the first place, so the scenario
this job existed to guard against cannot occur. Its "then re-run the
order pipeline" half was, separately, confirmed genuinely redundant with
the hourly trade_alert_check job, which fires at this exact same 07:00 UTC
minute every Monday (see CLAUDE.md's "Monday Pre-Market removed" section
for the full investigation, including real production-log evidence from
five consecutive Mondays).

The 07:00 UTC ETF order submission job (LSE regular open) was removed
2026-08-02 alongside the rest of the ETF sleeve — see CLAUDE.md's "ETF
sleeve removed from live trading" section. `instruments:` (config.yaml) no
longer has any ETF entry, so the EOD decide phase can never queue an ETF
pending_orders row for this job to have submitted in the first place —
keeping a job registered with structurally nothing to ever do is dead
weight, not a harmless no-op.

Every scheduled trading run calls brokers/trading212.py:maybe_place_orders()
(which itself calls maybe_exit_positions() first) — there is no separate
"analysis pipeline" step: momentum signal, index membership, and the crisis
circuit breaker are all computed on demand, straight from price_data, by
brokers/trading212.py's own _ticker_state() at order-decision time.

Metadata keys written to system_metadata:
  last_full_refresh          — UTC timestamp of the last successful data pull (EOD run or manual)
  last_run_type               — 'full' or 'analysis_only' — which pipeline variant last ran
  t212_account_snapshot      — JSON of latest T212 account summary (written by _fetch_t212_snapshot)
  t212_positions_snapshot    — JSON of latest T212 positions list (written by _fetch_t212_snapshot)
  t212_snapshot_fetched_at   — UTC ISO timestamp of last successful T212 fetch
  pending_t212_fetch         — "1" when the UI has requested a refresh; cleared by _dispatch_pending_jobs
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas_market_calendars as mcal
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from data.database import clear_metadata, get_metadata_str, set_metadata
from data.ingestion import get_all_tickers, get_all_watchlist_entries, pull_incremental

logger = logging.getLogger(__name__)

RUN_FULL          = "full"
RUN_ANALYSIS_ONLY = "analysis_only"

_scheduler: BackgroundScheduler | None = None

# pandas_market_calendars does not recognise all exchange shorthands used in
# config.yaml.  Map the config name → the registered calendar name.
_CALENDAR_ALIASES: dict[str, str] = {
    "TSE": "JPX",   # Tokyo Stock Exchange → Japan Exchange Group
}

# Tickers that must always be pulled regardless of calendar status.  Includes
# European/Asian indices that close before the 21:30 UTC EOD run, and ^VIX
# which is on NYSE (closed for US federal holidays like Juneteenth).
_ALWAYS_PULL: frozenset[str] = frozenset({
    "^FTSE", "^GDAXI", "^HSI", "^N225", "^STOXX50E", "^VIX",
})

# Exchange types with no pandas_market_calendars mapping that trade continuously
# on weekdays.  The EOD job must not rely solely on _is_exchange_open() for
# these — an unrecognised calendar name could cause them to be skipped.
_ALWAYS_OPEN_EXCHANGES: frozenset[str] = frozenset({"FX", "Crypto"})

def _resolve_calendar(name: str) -> str:
    return _CALENDAR_ALIASES.get(name, name)


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _build_ticker_exchange_map(config: dict) -> dict[str, str]:
    """Return {ticker: exchange_calendar_name} for every tracked ticker --
    reference_tickers plus the full dynamic live candidate pool.

    Found and fixed 2026-08-02: this used to hand-roll its own, narrower
    merge (reading only config.get("watchlist", {}), which covered the 3
    reference tickers alone) instead of reusing
    data/ingestion.py:get_all_watchlist_entries() -- the SAME "one shared
    merge, not two independently hand-written copies" bug shape this
    project has hit repeatedly. The practical effect: _eod_job() below
    calls pull_incremental(tickers=open_tickers), where open_tickers came
    from this map -- so the EOD job's actual price pull was silently
    limited to ^GSPC/GBPUSD=X/EURUSD=X only, never refreshing price data
    for any of the dynamic S&P 500 candidates get_live_stock_candidates()
    returns. Reusing get_all_watchlist_entries() here closes that gap by
    construction -- every ticker it returns already carries a real
    "exchange" field (reference tickers explicitly, dynamic candidates as
    "NYSE" per data/ingestion.py's own default)."""
    return {e["ticker"]: e.get("exchange", "NYSE") for e in get_all_watchlist_entries(config)}


def _is_exchange_open(calendar_name: str, check_date: date) -> bool:
    """Return True if *calendar_name* has a trading session on *check_date*.

    Crypto and FX markets are treated as always open on weekdays.
    Fails open (returns True) on any calendar lookup error so assets are
    never silently skipped due to a misconfigured calendar name.
    """
    if calendar_name in ("Crypto", "FX"):
        return True
    name = _resolve_calendar(calendar_name)
    try:
        cal = mcal.get_calendar(name)
        schedule = cal.schedule(
            start_date=check_date.isoformat(),
            end_date=check_date.isoformat(),
        )
        return not schedule.empty
    except Exception as exc:
        logger.warning(
            "Calendar check failed for %s on %s: %s — assuming open.",
            calendar_name, check_date, exc,
        )
        return True


def _is_scheduled_trading_day(check_date: date) -> bool:
    """True if *check_date* is a genuine NYSE trading day — the market-
    calendar gate _eod_job()/_membership_refresh_job() each check first,
    before doing anything else, once they fire (2026-08-02).

    Both jobs are now registered to fire every single day (no day-of-week
    filter at the trigger level in start_scheduler() — see that function's
    own comments) specifically so this check runs and gets to make its own
    decision daily, rather than a cron day-of-week filter silently
    excluding Saturday/Sunday before either job ever has the chance to
    decide anything. A plain Mon-Fri filter also has no concept of a
    market holiday (Thanksgiving, Christmas, ...) at all — it would either
    fail or behave oddly running the full order pipeline against stale
    price data on a weekday NYSE closure. pandas_market_calendars (via the
    existing _is_exchange_open() helper, same NYSE calendar — every ETF
    was removed from live trading 2026-08-02, see CLAUDE.md's "ETF sleeve
    removed from live trading" section, so NYSE is the only calendar this
    system's live decisions genuinely depend on now) is the real,
    purpose-built source of truth here — the same "reuse a maintained,
    purpose-built source instead of a hand-rolled list that can go stale"
    lesson already applied to S&P 500 membership tracking itself
    (analysis/universe.py, the fja05680/sp500 dataset) rather than a
    manually-maintained holiday calendar.

    Deliberately kept as its own named function rather than inlining
    `_is_exchange_open("NYSE", check_date)` at each call site: this
    answers a different question from _is_exchange_open()'s own per-
    ticker "which exchanges are open today, so which tickers should be
    pulled" role further inside _eod_job() — this one decides whether the
    JOB should run AT ALL today. Keeping it separate and named is what
    makes room for a genuinely different answer in the future — some
    platforms already offer extended-hours trading with prices updating
    Sunday evening, and a job that already wakes up daily and asks this
    exact question is positioned to answer differently for that case
    (a different calendar, a session-type parameter, a config flag) with
    no change to the scheduler registration itself. Not built now — this
    only ever checks the standard NYSE regular-session calendar today —
    but the structure doesn't foreclose it the way a hardcoded weekday
    cron trigger would have.
    """
    return _is_exchange_open("NYSE", check_date)


def _is_nyse_open_now(now_utc: datetime) -> bool:
    """True if NYSE is genuinely inside its live regular trading session
    RIGHT NOW — a materially stricter question than
    _is_scheduled_trading_day() above, which only ever answers "is today
    a trading day at all" (day-level), not "is the market actually open
    at this exact time of day" (2026-08-03).

    Reads NYSE's own real market_open/market_close timestamps for today
    from pandas_market_calendars directly, rather than approximating
    with a fixed UTC hour range — this is what lets a job that calls
    this fire unconditionally every hour, every day, and still correctly
    track NYSE's own session boundary as it genuinely moves in UTC terms
    across the year (US and UK DST transitions land on different
    calendar dates most years, so a hand-maintained UTC window is
    guaranteed to be wrong for the few weeks each spring/autumn the two
    don't align — the same class of staleness risk that let the old
    "0-21,23" hourly trade-alert range go unrevisited for over a year
    after the multi-region system it was built for no longer existed;
    see CLAUDE.md's "Splitting the hourly trade-alert job" section for
    the full investigation).

    Fails OPEN (returns True) on any calendar-lookup error — matches
    _is_exchange_open()'s own established convention, deliberately in
    the safer direction for what this gates: a transient calendar
    lookup failure must never silently suppress a safety-critical exit-
    check/order-poll job for up to an hour. Running that job against a
    possibly-closed market is harmless (nothing to act on, completes
    quickly); skipping it while the market is genuinely open would not
    be.
    """
    try:
        cal = mcal.get_calendar("NYSE")
        schedule = cal.schedule(
            start_date=now_utc.date().isoformat(),
            end_date=now_utc.date().isoformat(),
        )
        if schedule.empty:
            return False
        market_open = schedule.iloc[0]["market_open"]
        market_close = schedule.iloc[0]["market_close"]
        return bool(market_open <= now_utc <= market_close)
    except Exception as exc:
        logger.warning("NYSE live-session check failed: %s — assuming open.", exc)
        return True


def _is_nyse_extended_hours_open(now_utc: datetime) -> bool:
    """True if now_utc falls within any of T212's real, documented
    extended-hours sessions for NYSE-listed US equities (2026-08-04):
    pre-market (04:00–09:30 ET), regular hours (09:30–16:00 ET),
    after-hours (16:00–20:00 ET), or overnight (20:00–04:00 ET, via Blue
    Ocean Technologies ATS — T212's own docs restrict this session to the
    most liquid securities only, a distinction this function does not
    attempt to model per-ticker; it answers "is SOME extended session
    live right now", not "is this specific ticker eligible for it").

    A deliberately SEPARATE function from _is_nyse_open_now() above — NOT
    a modification of it. _is_nyse_open_now() is also the gate
    _stock_order_submission_job() uses, and that job must stay
    regular-hours-only: submitting a brand-new entry into T212's own
    documented thinner extended-hours liquidity ("orders may be delayed
    or not executed at all") is a separate risk decision this task does
    not make. Only _market_hours_check_job() (Open Trade Monitor — order-
    status poll + exit check) calls this function; confirmed directly by
    checking every call site of _is_nyse_open_now() before writing this,
    not assumed.

    Mechanism, in two tiers:

    1. Pre-market + regular + after-hours are read directly from
       pandas_market_calendars' own `pre`/`post` schedule columns
       (market_times="all") — confirmed live these three sessions are
       genuinely contiguous (pre <= t <= post covers exactly and only
       04:00–20:00 ET, spanning all three at once), and — like the
       regular-session boundaries _is_nyse_open_now() already uses —
       genuinely DST-aware (checked directly: `pre` reads 08:00 UTC in
       August/EDT and 09:00 UTC in November/EST for the identical 04:00
       ET wall-clock time), never a hand-maintained UTC offset.
    2. Overnight has no equivalent in pandas_market_calendars (Blue Ocean
       ATS is an alternative trading system, not an exchange calendar the
       library tracks) — built instead from T212's own documented, fixed
       20:00–04:00 ET window, but still anchored to pandas_market_
       calendars' own `post`/`pre` timestamps rather than a hardcoded UTC
       offset, so it stays correctly DST-aware the same way tier 1 does.
       Recognized ONLY between two CALENDAR-ADJACENT trading days (row
       N's date + 1 day == row N+1's date) — confirmed directly against
       real data that this single adjacency rule correctly excludes both
       a genuine weekend (2026-08-07 Fri → 2026-08-10 Mon, not adjacent)
       and a holiday (2026-11-25 Wed → 2026-11-27 Fri around Thanksgiving,
       not adjacent) with no separate weekend/holiday special-casing
       needed. This is a deliberately conservative reading of "24/5" (not
       "24/7") given the real Blue Ocean ATS session's exact holiday-
       bridging behavior isn't independently confirmed one way or the
       other — stated here as an assumption this function makes, not a
       verified fact, matching the standing project convention of naming
       an unverified assumption explicitly rather than presenting it as
       confirmed.

    Fails OPEN (returns True) on any calendar-lookup error — the same
    convention _is_nyse_open_now() already uses, and for the identical
    reason: a transient lookup failure must never silently suppress a
    safety-critical exit-check job for up to an hour.
    """
    try:
        cal = mcal.get_calendar("NYSE")
        today = now_utc.date()
        sched = cal.schedule(
            start_date=(today - timedelta(days=5)).isoformat(),
            end_date=(today + timedelta(days=5)).isoformat(),
            market_times="all",
        )
        if sched.empty:
            return False

        # Tier 1 — pre-market/regular/after-hours: one contiguous window
        # per trading day.
        for _, row in sched.iterrows():
            if row["pre"] <= now_utc <= row["post"]:
                return True

        # Tier 2 — overnight: bridges row i's `post` to row i+1's `pre`,
        # only when the two trading days are genuinely calendar-adjacent.
        dates = [ts.date() for ts in sched.index]
        for i in range(len(sched) - 1):
            if dates[i + 1] == dates[i] + timedelta(days=1):
                this_post = sched.iloc[i]["post"]
                next_pre = sched.iloc[i + 1]["pre"]
                if this_post <= now_utc <= next_pre:
                    return True

        return False
    except Exception as exc:
        logger.warning("NYSE extended-hours session check failed: %s — assuming open.", exc)
        return True


def _has_upcoming_trading_day(from_date: date, lookahead_days: int = 10) -> bool:
    """True if a genuine NYSE trading day falls anywhere in the
    `lookahead_days` calendar days strictly after `from_date` (checks
    `from_date + 1` through `from_date + lookahead_days`).

    This is deliberately NOT "is `from_date + 1` a trading day" — every
    Friday's literal tomorrow is Saturday, so a single-day-ahead check
    would incorrectly say "nothing to do" on the single most common case
    (queuing Friday night for Monday), not just the holiday-shifted edge
    case. A short, bounded lookahead instead answers the real question —
    "is there an upcoming session worth queuing candidates for at all" —
    and naturally, uniformly covers every real gap NYSE actually has
    (an ordinary weekend, a single-day holiday, a holiday-adjacent long
    weekend) with no special-casing, since none of those ever run longer
    than a few consecutive calendar days. 10 days is a generous margin
    over the longest real NYSE closure on record, so in practice this
    resolves to True on every date except a genuinely pathological,
    extended shutdown — see `_eod_job()`'s own docstring for why that
    matters and what happens if it's ever False.
    """
    for offset in range(1, lookahead_days + 1):
        if _is_scheduled_trading_day(from_date + timedelta(days=offset)):
            return True
    return False


def _record_account_value_history(summary: dict) -> None:
    """Upsert one row into account_value_history for today's date, from an
    already-fetched T212 account summary dict — see AccountValueHistory's
    own docstring (data/database.py) for why this is called only from
    _fetch_t212_snapshot()'s record_history=True path (the EOD job's own
    call), never the 60-second dispatcher's or hourly job's calls to the
    same function.

    Never raises — a history-recording failure must not affect the
    account snapshot itself (already written to system_metadata by the
    time this runs) or block whatever called it.
    """
    try:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from data.database import AccountValueHistory, get_engine

        cash_obj = summary.get("cash", {}) if isinstance(summary.get("cash"), dict) else {}
        inv_obj  = summary.get("investments", {}) if isinstance(summary.get("investments"), dict) else {}
        total_value = float(summary.get("totalValue", 0) or 0)
        cash        = float(cash_obj.get("availableToTrade", 0) or 0)
        invested    = float(inv_obj.get("currentValue", 0) or 0)
        today = datetime.utcnow().date()
        now = datetime.utcnow()

        stmt = sqlite_insert(AccountValueHistory).values(
            date=today, total_value_gbp=total_value, cash_gbp=cash,
            invested_gbp=invested, captured_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["date"],
            set_={
                "total_value_gbp": total_value, "cash_gbp": cash,
                "invested_gbp": invested, "captured_at": now,
            },
        )
        with get_engine().begin() as conn:
            conn.execute(stmt)
        logger.info("Account value history recorded for %s: £%.2f total.", today, total_value)
    except Exception as exc:
        logger.warning("Could not record account value history: %s", exc)


def _fetch_t212_snapshot(config: dict, record_history: bool = False) -> None:
    """Fetch T212 account summary + positions and persist as JSON in system_metadata.

    Called by the 60-second dispatcher (on-demand UI requests) and by the EOD job
    so the snapshot stays reasonably fresh without manual refresh.  All T212 API
    calls are centralised here — the UI only reads from the DB.

    record_history=True (passed only by _eod_job()'s own call) additionally
    upserts a dated row into account_value_history from the same
    already-fetched summary — no second API call, just one more DB write
    right after a fetch that already happens daily. Deliberately NOT the
    default, and NOT passed by the 60-second dispatcher's or hourly job's
    own calls to this function, so the history table stays a genuine
    one-row-per-day series rather than accumulating many rows a day.
    """
    t212_cfg = config.get("trading212_invest", {})
    if not t212_cfg.get("enabled", False):
        return
    if not (os.environ.get("T212_API_KEY") and os.environ.get("T212_API_SECRET")):
        return

    live = t212_cfg.get("live", False)
    try:
        from brokers.trading212 import (  # noqa: PLC0415
            Trading212Client,
            poll_placed_orders as _poll,
        )
        client    = Trading212Client(live=live)
        summary   = client.get_account_summary()
        positions = client.get_positions()
        set_metadata("t212_account_snapshot",  json.dumps(summary))
        set_metadata("t212_positions_snapshot", json.dumps(positions or []))
        set_metadata("t212_snapshot_fetched_at", datetime.utcnow().isoformat())
        n_pos = len(positions) if isinstance(positions, list) else 0
        logger.info("T212 account snapshot updated (%d position(s)).", n_pos)
    except Exception as exc:
        logger.error("T212 snapshot fetch failed: %s", exc)
        set_metadata("t212_invest_error", str(exc))
        return

    if record_history:
        _record_account_value_history(summary)

    try:
        _poll(config)
    except Exception as exc:
        logger.warning("T212 order poll failed after snapshot: %s", exc)


def _dispatch_pending_jobs() -> None:
    """60-second job: execute any pending tasks queued by the UI.

    Currently handles a single flag: ``pending_t212_fetch``.  The flag is
    cleared *before* the fetch so a second UI click while a fetch is in
    progress queues another one rather than being silently swallowed.

    Also writes a heartbeat timestamp so the Streamlit UI can detect whether
    the standalone scheduler daemon is running (vs. the in-process scheduler).
    """
    try:
        set_metadata("scheduler_heartbeat", datetime.utcnow().isoformat())
    except Exception:
        pass

    config = _load_config()

    if get_metadata_str("pending_t212_fetch"):
        set_metadata("pending_t212_fetch", "")
        logger.info("Dispatching pending T212 snapshot fetch (UI-requested)…")
        _fetch_t212_snapshot(config)


def _run_analysis_pipeline(
    config: dict,
    all_tickers: list[str],
    run_type: str,
) -> None:
    """Run the momentum + crisis-override order logic for every scheduled slot.

    "all_tickers" is accepted for call-site symmetry with the previous
    six-module pipeline (which needed a ticker list to analyse), but the
    momentum signal, index membership, and crisis check are all computed
    per-ticker on demand inside maybe_place_orders()/_ticker_state() from
    the dynamic, DB-backed live candidate pool (analysis/universe.py:
    get_live_stock_candidates() — config.yaml's `instruments:` section is
    empty now, see CLAUDE.md's "wire the momentum-only, full-S&P-500
    selection into live execution" section) — this parameter is unused
    here.
    """
    try:
        set_metadata("last_run_type", run_type)
    except Exception as exc:
        logger.warning("Could not stamp last_run_type: %s", exc)

    try:
        from brokers.trading212 import maybe_place_orders  # noqa: PLC0415
        maybe_place_orders(config, run_type)
    except Exception as exc:
        logger.error("Order placement failed: %s", exc, exc_info=True)

    # Alert on any FAILED T212 instrument resolution (2026-08-02, dedup
    # fixed 2026-08-02) — without this, a genuinely tradeable candidate
    # excluded by resolve_and_cache_instrument()'s "flag for review, never
    # auto-retry a genuine mismatch" contract would just sit in
    # instrument_resolutions until someone happened to open the Status
    # page. get_failed_instrument_resolutions() always returns the FULL,
    # current set of failed tickers — checked on every scheduled run (not
    # just run_type == "full", the only run that can actually PRODUCE a
    # new failure) so a NEW failure alongside an already-known one is
    # always included and alerted on immediately, never held back by
    # timing. send_instrument_resolution_failure_alert() uses content-based
    # deduplication, not a time-based cooldown: it re-sends only when the
    # failed ticker set itself has genuinely changed (a new failure, or a
    # resolution), so an unchanged, persistent failure is never re-flagged
    # every check cycle, and a genuinely new failure is never suppressed
    # by a cooldown window regardless of when the last alert went out. Own
    # try/except so a failure here never blocks/masks the order placement
    # result above.
    try:
        from brokers.trading212 import get_failed_instrument_resolutions  # noqa: PLC0415
        failed = get_failed_instrument_resolutions()
        if failed:
            logger.warning(
                "%d ticker(s) have a FAILED instrument resolution — %s",
                len(failed), [r["ticker"] for r in failed],
            )
            from output.briefing import send_instrument_resolution_failure_alert  # noqa: PLC0415
            send_instrument_resolution_failure_alert(failed, config)
    except Exception as exc:
        logger.warning("Instrument resolution failure check/alert failed: %s", exc)

    logger.info("Order pipeline complete (run_type=%s).", run_type)


_EOD_COMPLETION_KEY = "eod_completion"


def _record_eod_completion(status: str, run_date: date, **details) -> None:
    """Write a definitive, directly-checkable verdict for tonight's EOD run
    to system_metadata under _EOD_COMPLETION_KEY — status is one of
    COMPLETED / FAILED / NOOP_NOT_A_TRADING_WINDOW. This is the "did EOD
    actually run" signal (2026-08-02): distinct from the specific
    downstream alerts already built (instrument-resolution failures,
    position reconciliation, membership staleness) and from the existing
    last_full_refresh-staleness-based OVERDUE badge, which only ever
    infers a problem from an old timestamp rather than stating a direct
    verdict for the most recent run. Never raises — a failure to record
    this signal must never mask whatever the real EOD outcome was.
    """
    try:
        payload = {"status": status, "date": run_date.isoformat(),
                   "recorded_at": datetime.utcnow().isoformat(), **details}
        set_metadata(_EOD_COMPLETION_KEY, json.dumps(payload))
    except Exception as exc:
        logger.warning("Could not record EOD completion status: %s", exc)


def get_eod_completion_status() -> "dict | None":
    """Read back the most recent _record_eod_completion() payload, or None
    if the EOD job has never run since this feature shipped. Never raises."""
    try:
        raw = get_metadata_str(_EOD_COMPLETION_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        return None


_JOB_IN_PROGRESS_KEY = "eod_job_in_progress"

# How long a stored "in progress" marker is trusted before deploy.sh's
# checker (check_eod_job_in_progress.py) treats it as stale -- i.e. left
# behind by a process that was killed (crash, OOM, a *previous*
# systemctl restart) rather than a genuinely still-running job. Must stay
# comfortably longer than any real observed run: the price pull alone for
# the current 506-ticker live universe measured ~23 minutes end to end on
# a clean run (2026-08-03, 21:30:10-21:53:08), plus the decide pipeline
# and T212 snapshot after it. 3600s (60 minutes) leaves real margin above
# that without leaving a genuinely crashed marker blocking deploys for
# the rest of the night. See deploy.sh's own "job in progress" comment
# for how this is used -- this constant is the single source of truth
# for the ceiling; the checker script imports it rather than
# re-deriving its own number.
JOB_IN_PROGRESS_STALE_AFTER_SECONDS = 3600


def _mark_job_started(job_name: str) -> None:
    """Record that *job_name* (e.g. "eod", "eod-catch-up") has started
    real work, for deploy.sh's check_eod_job_in_progress.py to detect
    (2026-08-05) -- lets a deploy defer on "is this job actually still
    running" rather than only a static wall-clock window, which is what
    let a deploy force through mid-EOD-run the night this was added (see
    CLAUDE.md's "GitHub webhook deploy" for the incident). Never raises:
    a failure to record this must never block or mask the job itself --
    worst case, deploy.sh falls back to its static-window behaviour."""
    try:
        set_metadata(_JOB_IN_PROGRESS_KEY, json.dumps({
            "job": job_name,
            "started_at": datetime.utcnow().isoformat(),
        }))
    except Exception as exc:
        logger.warning("Could not record job-in-progress marker for %s: %s", job_name, exc)


def _mark_job_finished() -> None:
    """Clear the marker _mark_job_started() wrote. Called from a
    try/finally around the job's real work, so it runs whether the job
    succeeded or raised -- the only case it does NOT run is the process
    itself being killed outright (e.g. a systemd restart), which is
    exactly the case JOB_IN_PROGRESS_STALE_AFTER_SECONDS's staleness
    ceiling exists to time out rather than block deploys forever. Never
    raises, matching every other metadata-write helper in this module."""
    try:
        clear_metadata(_JOB_IN_PROGRESS_KEY)
    except Exception as exc:
        logger.warning("Could not clear job-in-progress marker: %s", exc)


def get_job_in_progress_status() -> "dict | None":
    """Read back the current job-in-progress marker, with elapsed_seconds
    computed, or None if no job is currently marked as running (including
    right after a clean finish, since _mark_job_finished() clears it).
    Never raises. Does NOT itself apply the staleness ceiling -- that is
    the caller's decision (check_eod_job_in_progress.py applies it to
    decide whether to actually block a deploy; a future Status-page
    display might legitimately want the raw, unfiltered elapsed time
    instead, e.g. to surface "stuck for 90 minutes" as its own signal)."""
    try:
        raw = get_metadata_str(_JOB_IN_PROGRESS_KEY)
        if not raw:
            return None
        status = json.loads(raw)
        started_at = datetime.fromisoformat(status["started_at"])
        status["elapsed_seconds"] = (datetime.utcnow() - started_at).total_seconds()
        return status
    except Exception:
        return None


def _eod_job() -> None:
    """Daily, 21:30 UTC — market-calendar-gated (2026-08-02), not a Mon-Fri
    cron filter. Two independently-gated steps, not one combined gate:

    1. Price pull — gated on whether TODAY is a genuine NYSE trading day
       (_is_scheduled_trading_day(today_utc)). Correct as originally
       shipped: there is no new close to pull on a day NYSE was closed.
    2. Candidate selection / decision-queuing (the RUN_FULL decide phase,
       inside _run_analysis_pipeline()) — gated on whether an UPCOMING
       trading day exists (_has_upcoming_trading_day(today_utc)), NOT on
       whether today itself is one.

    Fixed 2026-08-02, same day as the original calendar-gating change,
    after realizing the single combined gate silently broke every
    non-trading-day night's candidate queuing — most urgently, a Sunday
    night no longer queued anything for Monday at all, since the whole
    job (price pull AND decide phase together) returned before either
    ran. The momentum signal is computed from whatever the latest STORED
    close already is (_ticker_state() reads price_data, not "today"), so
    it needs no new data from today to produce a valid decision for the
    next real session — Sunday night correctly uses Friday's close to
    decide what to queue for Monday, exactly as a Friday night already
    used Friday's own just-pulled close to decide what to queue for the
    following Monday. Both directions of this were already correct
    except for the specific case of a non-trading night preceding the
    very next trading day, which the old single gate excluded outright.

    Registered in start_scheduler() to fire every single day, no
    day_of_week restriction at the trigger level — see
    _is_scheduled_trading_day()'s own docstring for why the trigger
    itself must be unconditional. If NEITHER gate is satisfied (today is
    not a trading day AND no trading day falls within the next 10 days —
    a genuinely pathological, extended closure that essentially never
    happens for NYSE in practice), the whole job is a clean no-op, same
    as the original single-gate design was for any non-trading day.
    """
    today_utc: date = datetime.now(timezone.utc).date()
    today_is_trading_day = _is_scheduled_trading_day(today_utc)
    should_queue_candidates = _has_upcoming_trading_day(today_utc)

    if not today_is_trading_day and not should_queue_candidates:
        logger.info(
            "EOD job — %s is not a trading day and no trading day falls in the "
            "next 10 days — nothing to do.", today_utc,
        )
        _record_eod_completion(
            "NOOP_NOT_A_TRADING_WINDOW", today_utc,
            today_is_trading_day=today_is_trading_day, should_queue_candidates=should_queue_candidates,
        )
        return

    logger.info("Scheduled end-of-day refresh starting (21:30 UTC)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "End of Day")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)

    # From here on, real work happens (price pull and/or the decide
    # pipeline) -- mark it so deploy.sh can detect a genuinely in-progress
    # run rather than only inferring one from a static wall-clock window.
    # See _mark_job_started()'s own docstring for the incident this closes.
    _mark_job_started("eod")

    # A direct "did tonight's EOD job actually run to completion" signal
    # (2026-08-02) — distinct from and in addition to the specific
    # downstream alerts already built (instrument-resolution failures,
    # position reconciliation, membership staleness). Everything below
    # this point already has its own internal try/except per step, but
    # nothing at the OUTER level previously recorded a definitive
    # COMPLETED/FAILED verdict for the run as a whole — an unhandled
    # exception escaping pull_incremental() or _run_analysis_pipeline()
    # itself (neither wrapped at this level) would silently kill the job
    # with no explicit record anywhere, only the ABSENCE of a fresh
    # last_full_refresh stamp for a caller to infer from. This wraps the
    # whole body in one outer try/except purely to make that verdict
    # explicit and checkable first thing each morning — it does not change
    # any individual step's own existing error handling.
    details: dict = {
        "today_is_trading_day": today_is_trading_day,
        "should_queue_candidates": should_queue_candidates,
        "price_pull_ran": False, "price_pull_rows": None,
        "order_pipeline_ran": False, "snapshot_ran": False,
    }
    try:
        config = _load_config()

        if today_is_trading_day:
            ticker_exchange = _build_ticker_exchange_map(config)
            unique_exchanges = set(ticker_exchange.values())
            open_exchanges: dict[str, bool] = {
                exch: _is_exchange_open(exch, today_utc)
                for exch in unique_exchanges
            }

            open_tickers:    list[str] = []
            skipped_tickers: list[str] = []
            for ticker, exchange in ticker_exchange.items():
                if ticker in _ALWAYS_PULL or exchange in _ALWAYS_OPEN_EXCHANGES or open_exchanges[exchange]:
                    open_tickers.append(ticker)
                else:
                    logger.info("Skipping %s — %s closed today (%s).", ticker, exchange, today_utc)
                    skipped_tickers.append(ticker)

            if open_tickers:
                logger.info(
                    "EOD refresh — pulling %d assets (%d skipped, market closed).",
                    len(open_tickers), len(skipped_tickers),
                )
                results = pull_incremental(tickers=open_tickers)
                total_rows = sum(results.values())
                logger.info("Data pull complete — %d new rows across %d assets.", total_rows, len(open_tickers))
                details["price_pull_ran"] = True
                details["price_pull_rows"] = total_rows

                try:
                    set_metadata("last_full_refresh", datetime.utcnow().isoformat())
                except Exception as exc:
                    logger.warning("Could not stamp last_full_refresh: %s", exc)

                try:
                    set_metadata("last_pull_rows", str(total_rows))
                    set_metadata("last_pull_ticker_count", str(len([v for v in results.values() if v > 0])))
                except Exception as exc:
                    logger.warning("Could not stamp last_pull_rows: %s", exc)
            else:
                logger.info("All markets closed today (%s) — skipping data pull.", today_utc)
        else:
            logger.info(
                "EOD job — %s is not a scheduled NYSE trading day; skipping the price "
                "pull, still running candidate selection for the upcoming trading day.",
                today_utc,
            )

        run_type = RUN_FULL if should_queue_candidates else RUN_ANALYSIS_ONLY
        _run_analysis_pipeline(config, get_all_tickers(config), run_type)
        details["order_pipeline_ran"] = True

        try:
            # record_history=True (2026-08-02) — only this call, the EOD job's
            # own daily snapshot, also records a dated account_value_history
            # row. See _fetch_t212_snapshot()'s and AccountValueHistory's own
            # docstrings for why the dispatcher/hourly calls below don't.
            _fetch_t212_snapshot(config, record_history=True)
            details["snapshot_ran"] = True
        except Exception as exc:
            logger.error("T212 snapshot fetch after EOD failed: %s", exc)
            details["snapshot_error"] = str(exc)

        try:
            # Bounded retention for pending_orders (2026-08-02) -- a
            # deliberate, scoped exception to "flag, don't delete": this
            # table's resolved rows (SKIPPED_*/REJECTED_CAPACITY/SUPERSEDED)
            # are routine, high-volume queue noise, not meaningful trade
            # history -- see purge_old_pending_orders()'s own docstring.
            # Own try/except so a purge failure never blocks anything else
            # in this job, matching every other step here.
            from brokers.trading212 import purge_old_pending_orders  # noqa: PLC0415
            n_purged = purge_old_pending_orders(config)
            details["pending_orders_purged"] = n_purged
        except Exception as exc:
            logger.warning("pending_orders purge failed: %s", exc)

        try:
            # Recurring backstop for the split-adjustment cliff class of bug
            # (2026-08-06, see CLAUDE.md's "Split-adjustment corruption"
            # section) -- pull_incremental()'s own ingest-time restatement
            # check (_find_retroactive_restatement()) is the primary
            # defense and should make this near-zero-hit in practice; this
            # is a full-table backstop scan catching anything that slips
            # through a different write path (backfill_price_history(), a
            # manual script) or a future bug in the ingest-time guard
            # itself. Own try/except so a monitoring failure never blocks
            # anything else in this job.
            #
            # detect_price_cliffs() scans the FULL price_data table,
            # including the ~700 historical/delisted tickers ingestion was
            # broadened to cover (2026-08-06, "Universe coverage
            # remediation") -- most flagged "cliffs" there are genuine
            # crisis-era crashes or corporate actions in companies that no
            # longer trade, not corruption, and pose zero live-money risk
            # (confirmed directly: of 4,401 raw hits found the day this
            # scoping was added, only 14 touched a currently-live or
            # currently-held ticker, and every one of those 14 corresponded
            # to a real, dated market event -- see CLAUDE.md). The alert
            # itself is scoped to live-tradeable + currently-held tickers
            # only, matching the actual risk this alert exists to catch
            # (a poisoned momentum window on a position that can actually
            # be traded) -- the full raw count is still recorded below for
            # visibility, just not emailed about.
            from data.ingestion import detect_price_cliffs  # noqa: PLC0415
            from output.briefing import send_price_cliff_alert  # noqa: PLC0415
            from analysis.universe import get_live_stock_candidates  # noqa: PLC0415
            from brokers.trading212 import get_held_research_tickers  # noqa: PLC0415
            cliff_hits = detect_price_cliffs()
            _relevant = set(get_live_stock_candidates()) | set(get_held_research_tickers())
            live_cliff_hits = [h for h in cliff_hits if h["ticker"] in _relevant]
            details["price_cliff_hits_total"] = len(cliff_hits)
            details["price_cliff_hits"] = len(live_cliff_hits)
            send_price_cliff_alert(live_cliff_hits, config)
        except Exception as exc:
            logger.warning("Price cliff monitoring check failed: %s", exc)
    except Exception as exc:
        logger.error("EOD job failed with an unhandled exception: %s", exc, exc_info=True)
        _record_eod_completion("FAILED", today_utc, error=str(exc), **details)
        return
    finally:
        # Runs whether the job succeeded or raised above -- the only case
        # this does NOT run is the process being killed outright (e.g. a
        # systemd restart mid-run), which is exactly what
        # JOB_IN_PROGRESS_STALE_AFTER_SECONDS's staleness ceiling exists
        # to time out rather than leave deploys blocked indefinitely.
        _mark_job_finished()

    _record_eod_completion("COMPLETED", today_utc, **details)


def _market_hours_check_job() -> None:
    """Hourly, every single day (no cron-level day-of-week or hour-of-day
    filter at all — see start_scheduler()'s own registration comment)
    — T212 order-status poll and exit check ("Open Trade Monitor" on the
    Status page), gated internally on _is_nyse_extended_hours_open()
    (2026-08-04 — extended from the regular-hours-only
    _is_nyse_open_now(), see that function's own docstring and CLAUDE.md's
    "extend Open Trade Monitor to T212's extended-hours sessions" section)
    rather than a fixed UTC hour range.

    Why the wider window, specifically for THIS job and not
    _stock_order_submission_job() below (which deliberately keeps using
    the narrower _is_nyse_open_now()): T212 genuinely executes real
    trades — not just indicative quotes — across pre-market, after-hours,
    and (for the most liquid names, via Blue Ocean ATS) overnight
    sessions, so a position's real price can move meaningfully outside
    the old regular-13:30–20:00-UTC-only window this job used to check.
    The exit check (`maybe_exit_positions()`) — stop-loss/signal-off/
    crisis-exit/max-holding — is this system's own, continuously-running
    responsibility for an already-open position (as opposed to placing a
    brand-new entry, which _stock_order_submission_job() deliberately
    does NOT extend into T212's thinner extended-hours liquidity), so it
    should have the chance to see and react to that movement rather than
    only ever checking once regular hours reopen. This widens WHEN the
    job is allowed to fire; it does not, by itself, change WHAT price the
    exit check evaluates once it runs — that is the separate, complementary
    live-pricing fix (see CLAUDE.md's "live pricing for the exit check"
    section), which replaces the exit check's own once-daily stale close
    with a genuinely live read. The two are independent, cumulative fixes
    to the same underlying gap (see the CLAUDE.md sections for both): this
    one alone still leaves the exit check evaluating whatever price source
    is wired in at the time; that one alone still can't be checked outside
    whatever window this job fires in.

    Replaces the old, single _trade_alert_job() (2026-08-03 — see
    CLAUDE.md's "Splitting the hourly trade-alert job" section for the
    full investigation this split is based on). Split into this
    NYSE-hours-gated job (order polling + exit checks) and the
    unconditional _account_state_job() below (reconciliation + account
    snapshot) because those four responsibilities do not share one
    correct time window:

      - The exit check — see above.
      - T212 order-status polling (`poll_placed_orders()`) — confirmed
        directly (2026-08-03, see CLAUDE.md's "Tracing the '60+ hour
        fill-lag' claim's real origin" section) that T212 does not fill
        THIS system's own market orders outside their own regular-hours
        submission window at all — none of this system's orders ever set
        the extendedHours flag T212's API supports, so, unlike a genuine
        24/5-flagged order, they only ever fill during the regular
        session regardless of when they were placed — and every real fill
        on this account has resolved within single-digit seconds during
        its own session. Extending THIS specific check's own window
        alongside the exit check's (since both live in the one job, gated
        by the one shared condition) gains it no new informational value
        of its own — it is along for the ride on the exit check's real
        need, not a change made for polling's own sake — but doing so is
        harmless: polling more often against orders that were never going
        to have moved produces the identical answer, the same "no-op
        overhead, not a bug" shape this job's staleness reasoning already
        accepted for a narrower window. (A genuinely rare, anomalous
        resolution outside normal fill mechanics can't be fully ruled out
        from the evidence gathered — that residual, low-severity risk is
        accepted explicitly, not overlooked; see the same CLAUDE.md
        section.)

    Fires every hour regardless of NYSE state — the gate is evaluated
    fresh on every real firing via _is_nyse_extended_hours_open(), not
    approximated with a hardcoded UTC window, so it stays correct across
    both the US/UK DST-transition mismatch and the seasonal ET offset
    automatically, with no schedule to hand-maintain.
    """
    now_utc = datetime.now(timezone.utc)
    if not _is_nyse_extended_hours_open(now_utc):
        logger.debug("Market-hours check skipped — outside every NYSE extended-hours session.")
        return

    logger.info("Market-hours check starting (an NYSE extended-hours session is live)…")
    config = _load_config()

    try:
        from brokers.trading212 import poll_placed_orders  # noqa: PLC0415
        _n_updated = poll_placed_orders(config)
        if _n_updated:
            logger.info("T212 order poll: %d order(s) updated to terminal status.", _n_updated)
    except Exception as _poll_exc:
        logger.warning("T212 order poll failed: %s", _poll_exc)

    try:
        # Missing-stop retry (2026-08-04) — a separate, sibling step from
        # poll_placed_orders() above, not folded into it: catches a FILLED
        # BUY row whose broker-side stop-placement attempt never even ran
        # (e.g. this same process crashing/restarting between that
        # function's own DB-commit and its stop-placement call — see
        # brokers.trading212.retry_missing_broker_stops()'s own docstring
        # for the full "buy-then-stop sequencing" gap this closes). Run
        # right after the poll, before the exit check, so a stop placed
        # here is already active by the time maybe_exit_positions() and
        # check_stop_health() run later in this same cycle.
        from brokers.trading212 import retry_missing_broker_stops  # noqa: PLC0415
        _n_retried = retry_missing_broker_stops(config)
        if _n_retried:
            logger.warning(
                "Missing-stop retry: %d FILLED BUY position(s) had no broker-side "
                "stop ever attempted — placement retried this cycle.", _n_retried,
            )
    except Exception as exc:
        logger.error("Missing-stop retry failed: %s", exc, exc_info=True)

    try:
        from brokers.trading212 import maybe_exit_positions  # noqa: PLC0415
        maybe_exit_positions(config)
    except Exception as exc:
        logger.error("Exit check failed: %s", exc, exc_info=True)

    try:
        # Stop-health check (2026-08-04) — runs LAST in this job,
        # deliberately after every call above: poll_placed_orders() may
        # have just placed a fresh stop for a newly-FILLED BUY this same
        # cycle, retry_missing_broker_stops() may have just placed one for
        # an earlier cycle's missed attempt, and maybe_exit_positions()
        # may have just closed a position this same cycle (cancelling its
        # stop as part of that) — checking after all three avoids flagging
        # any same-cycle transition as a false "missing stop". See
        # brokers.trading212.check_stop_health()'s own docstring for the
        # incident this closes (tonight's silent stop-rejection, only
        # caught because a T212 notification happened to be seen
        # externally) and the API-efficiency/auto-remediation reasoning.
        from brokers.trading212 import check_stop_health  # noqa: PLC0415
        check_stop_health(config)
    except Exception as exc:
        logger.error("Stop-health check failed: %s", exc, exc_info=True)


def _account_state_job() -> None:
    """Hourly, every single day, unconditionally — no NYSE-hours gate at
    all, deliberately unlike _market_hours_check_job() above (2026-08-03
    — see that function's own docstring and CLAUDE.md's "Splitting the
    hourly trade-alert job" section for the full investigation).

    Registered at minute=5, not minute=0 like its sibling above — see the
    registration comment in start_scheduler() for why: running both jobs'
    T212 API calls concurrently at the identical minute caused a real
    rate-limit collision on their first joint firing in production.

      - Live position reconciliation — `brokers.trading212.reconcile_
        positions_with_broker()` — deliberately NOT NYSE-hours-gated:
        its relationship to corporate-action processing timing was
        explicitly left unverified by the investigation this split is
        based on (a cash-out acquisition or stock-swap could in
        principle be reflected by the broker at a time not obviously
        tied to NYSE's own regular session) — erring toward running it
        more often rather than risking a real gap on an unconfirmed
        assumption.
      - Account/positions snapshot refresh (`_fetch_t212_snapshot()`) —
        deliberately NOT gated either: this has real value independent
        of trading hours (Status/Dashboard page freshness for anyone
        checking outside market hours), so gating it to NYSE hours
        would work directly against its own purpose.
    """
    logger.info("Account state refresh starting…")
    config = _load_config()

    try:
        from brokers.trading212 import reconcile_positions_with_broker  # noqa: PLC0415
        reconcile_positions_with_broker(config)
    except Exception as exc:
        logger.error("Position reconciliation failed: %s", exc, exc_info=True)

    try:
        _fetch_t212_snapshot(config)
    except Exception as exc:
        logger.error("Account snapshot refresh failed: %s", exc)


def _membership_refresh_job() -> None:
    """Daily, 21:15 UTC — market-calendar-gated (2026-08-02), not a Mon-Fri
    cron filter, same NYSE calendar and same _is_scheduled_trading_day()
    gate as _eod_job() so the two jobs can never disagree about whether
    today counts. Refresh point-in-time S&P 500 membership windows from
    source 15 minutes before the 21:30 EOD job's own live trading
    decisions run, so ticker eligibility is never computed against stale
    membership data (a ticker's real S&P 500 addition/removal must reach
    the eligibility check before it can affect that day's entries/exits)
    — but only on a genuine trading day, matching EOD's own gate exactly.

    Registered in start_scheduler() to fire every single day now, with no
    day_of_week restriction at the trigger level — see
    _is_scheduled_trading_day()'s own docstring for the full reasoning.

    refresh_membership_from_source() is already internally "never raises"
    (per-ticker failures are caught and logged, not propagated) — the outer
    try/except here is an extra layer specifically because this job sits
    directly ahead of the EOD job in the schedule, and a refresh failure
    must never block that job from running.

    Two additional checks (2026-08-02), each in its own try/except so a
    failure in either never blocks the refresh above or the EOD job that
    follows this one 15 minutes later:

    1. Staleness — re-checks is_membership_refresh_stale() using whatever
       last_membership_refresh_success is now (freshly updated if this
       run's fetch just succeeded, unchanged/still old if it just failed
       or has been failing for a while) and sends an email alert (subject
       to its own cooldown) if the refresh has gone unsuccessful for more
       than ~2 reconciliation cycles. See analysis/universe.py's own
       MEMBERSHIP_STALE_HOURS docstring for why 48h was chosen.
    2. Wikipedia lag check — see analysis/universe.py:check_wikipedia_lag()
       for what this does and does not prove (a lag detector against the
       data's own upstream source, not independent verification). Flags
       only; never blocks anything.
    """
    today_utc: date = datetime.now(timezone.utc).date()
    if not _is_scheduled_trading_day(today_utc):
        logger.info("Membership refresh job — %s is not a scheduled NYSE trading day, nothing to do.", today_utc)
        return

    from analysis.universe import (  # noqa: PLC0415
        MEMBERSHIP_STALE_HOURS,
        check_wikipedia_lag,
        get_last_membership_refresh_success,
        is_membership_refresh_stale,
        refresh_membership_from_source,
    )

    try:
        summary = refresh_membership_from_source()
        if summary.get("changed"):
            logger.info(
                "Membership refresh: %d ticker(s) changed — %s",
                len(summary["changed"]), [c["ticker"] for c in summary["changed"]],
            )
        if summary.get("errors"):
            logger.warning(
                "Membership refresh: %d error(s) — %s",
                len(summary["errors"]), summary["errors"],
            )
    except Exception as exc:
        logger.warning("Membership refresh job failed: %s", exc)

    try:
        last_success = get_last_membership_refresh_success()
        if is_membership_refresh_stale(last_success):
            age_hours = (
                (datetime.utcnow() - last_success.replace(tzinfo=None)).total_seconds() / 3600
                if last_success else float("inf")
            )
            logger.warning(
                "Membership refresh stale — last success %s (%s), threshold %.0fh.",
                last_success.isoformat() if last_success else "never",
                f"{age_hours:.1f}h ago" if age_hours != float("inf") else "unknown age",
                MEMBERSHIP_STALE_HOURS,
            )
            from output.briefing import send_membership_staleness_alert  # noqa: PLC0415
            send_membership_staleness_alert(age_hours, MEMBERSHIP_STALE_HOURS, _load_config())
    except Exception as exc:
        logger.warning("Membership staleness check/alert failed: %s", exc)

    try:
        check_wikipedia_lag()
    except Exception as exc:
        logger.warning("Wikipedia membership lag check failed: %s", exc)


def _weekly_summary_job() -> None:
    """Sunday 09:00 Europe/London (genuinely UK local time — see
    start_scheduler()'s own CronTrigger for this job, which passes
    timezone="Europe/London" directly rather than the scheduler's default
    UTC, so this fires at 9am regardless of BST/GMT). Sends the weekly
    summary email — see output/briefing.py:send_weekly_summary_email() for
    the full content and why this uses its own trigger path, deliberately
    separate from brokers/trading212.py's error-alert cooldown.

    Stamps last_scheduled_run/last_scheduled_run_name (2026-08-03) — this
    job previously never touched either key, so the Status page's "Last
    Scheduled Run" card, the Pipeline "Order Logic" row, and the sidebar's
    "Last scheduled run" caption (all three read the same key — see
    CLAUDE.md's "Status page scheduler-health/job-recency fixes" section) could
    silently show a stale, out-of-date job as the most recent one even
    though this job had genuinely just fired. Matches the exact pattern
    _eod_job() already uses.
    """
    from output.briefing import send_weekly_summary_email  # noqa: PLC0415

    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "Weekly Summary")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)

    config = _load_config()
    try:
        sent, detail = send_weekly_summary_email(config)
        if sent:
            logger.info("Weekly summary email sent: %s", detail)
        else:
            logger.info("Weekly summary email not sent: %s", detail)
    except Exception as exc:
        logger.error("Weekly summary job failed: %s", exc, exc_info=True)


def _stock_order_submission_job() -> None:
    """14:30 UTC, every single day (no cron-level day-of-week filter — see
    start_scheduler()'s own registration comment) — submit queued stock
    entry decisions from pending_orders, gated internally on
    _is_nyse_open_now() rather than a fixed weekday cron filter
    (2026-08-03).

    FOUND AND FIXED as a real, live gap, not a hypothetical: this job —
    the one that places real orders — was still registered with
    day_of_week="mon-fri" and had NO internal calendar/session check of
    its own at all, unlike every other job touched the same day
    (scheduled_eod/membership_refresh: _is_scheduled_trading_day();
    market_hours_check: _is_nyse_open_now(), at the time). A plain
    weekday filter has no concept of a US market holiday — on a Monday
    that also happens to be a genuine NYSE closure (a federal holiday),
    this job would still have fired at 14:30 UTC believing it was a
    normal trading day, and attempted to submit real orders with the
    market closed. Confirmed directly by reading the actual registration
    before assuming either way — the registration's own prior comment
    explicitly stated this gap was deliberately left out of scope when
    submission timing was last changed (2026-08-02), and was never
    revisited until now.

    Uses _is_nyse_open_now() specifically — the STRICTER, regular-hours-
    only, live-session check ("is the market genuinely in its REGULAR
    session right now"), not _is_scheduled_trading_day()'s day-level
    check ("is today a trading day at all") — originally built for, and
    shared with, market_hours_check_job() (2026-08-03). That sibling job
    has since moved to the wider _is_nyse_extended_hours_open()
    (2026-08-04 — see CLAUDE.md's "extend Open Trade Monitor to T212's
    extended-hours sessions" section); THIS job deliberately did not move
    with it and still uses the original, narrower check — submitting a
    brand-new entry into T212's own documented thinner extended-hours
    liquidity ("orders may be delayed or not executed at all") is a
    separate risk decision that change explicitly declined to make by
    default, so regular-hours-only remains correct here even though the
    exit check's own window widened. This is also the correct check for
    what submission timing actually cares about: 14:30 UTC is chosen
    specifically to land 60 minutes into NYSE's live regular session (see
    below), so the question genuinely is "is the REGULAR market open
    right now", not merely "is today a trading day" and not "is any
    extended session live". A holiday (schedule.empty for today) and an
    ordinary Saturday/Sunday are both covered by this SAME check, with no
    separate weekday logic needed at all — NYSE's own real calendar is
    never in session on a Saturday or Sunday, so _is_nyse_open_now()
    naturally returns False then regardless of day-of-week, the same way
    every NYSE-calendar-derived check in this module does.

    Moved from 13:30 UTC (right at the open) 2026-08-02 — the opening hour
    is the most volatile window of the trading day (overnight orders and
    news all digesting at once); a market order placed there executes
    against whatever the first prints happen to look like. Confirmed
    empirically before moving this, not assumed: real intraday data (39
    tickers, 2 years of 60-minute bars — the deepest granularity with
    enough real history to test this meaningfully; see CLAUDE.md's
    dedicated section) showed the opening 60-minute bar's own intraday
    range averages 2.39x a later, settled hour's — a market order filling
    inside that first hour is exposed to roughly twice the price swing a
    later one is. 60 minutes (not a shorter point in the 30-60 minute
    range) was chosen specifically because that's the exact checkpoint the
    real data measured (the first 60m bar's own close), not a padded
    guess partway through it.

    Deliberately still timed to the regular session, not any after-hours
    window — see CLAUDE.md's "Exchange-aware order submission" section for
    why after-hours submission was rejected outright. The submission-time
    price-sanity check (`price_sanity_threshold_pct`) is unaffected by
    this fix — it already reads a genuinely live price
    (`_live_current_price()`, fixed the same day the timing change
    shipped — see CLAUDE.md) regardless of what time it runs; nor is the
    crisis re-check (`is_crisis_mode_active_now()`) or the capacity check
    inside `submit_pending_stock_orders()` — this fix only changes WHEN
    the job is willing to run, never what it does once it decides to.

    Stamps last_scheduled_run/last_scheduled_run_name (2026-08-03) — this
    is the job that actually places real orders, but until that fix it
    never touched either key, so the Status page's "Last Scheduled Run"
    card, the Pipeline "Order Logic" row, and the sidebar's "Last
    scheduled run" caption (all three read the same key) kept showing
    whatever earlier job had last stamped it, even hours after real
    positions had been opened by this run. Matches the exact pattern
    _eod_job() already uses; stamped unconditionally once the job
    genuinely fires, not only when an order was actually placed — "the
    job ran" is the signal this key represents, not "the job did
    something." Stamped AFTER the NYSE-session gate below (2026-08-03,
    this same fix) — a genuine no-op day (market closed) should not
    overwrite "Last Scheduled Run" with a run that did nothing, the same
    "the job ran" semantics this key already carries for every other
    job's own gate.
    """
    now_utc = datetime.now(timezone.utc)
    if not _is_nyse_open_now(now_utc):
        logger.debug("Stock order submission skipped — NYSE not in session.")
        return

    logger.info("Stock order submission job starting (14:30 UTC, 60min after NYSE open)…")
    try:
        set_metadata("last_scheduled_run",      datetime.utcnow().isoformat())
        set_metadata("last_scheduled_run_name", "Stock Order Submission")
    except Exception as _exc:
        logger.warning("Could not stamp last_scheduled_run: %s", _exc)

    config = _load_config()
    try:
        from brokers.trading212 import submit_pending_stock_orders  # noqa: PLC0415
        n = submit_pending_stock_orders(config)
        if n:
            logger.info("Stock submission: %d order(s) placed.", n)
    except Exception as exc:
        logger.error("Stock order submission failed: %s", exc, exc_info=True)


def _check_missed_eod_run(config: dict) -> None:
    """Trigger an immediate EOD catch-up if today's 21:30 UTC EOD run was missed.

    Called at scheduler startup, after all jobs are registered.  Both
    conditions below must hold before a catch-up fires:

    1. last_full_refresh is more than 20 hours old. A run from earlier the
       same day (e.g. a manual "Refresh Data" click, or the real EOD job
       having already fired minutes ago) does not count as missed — only a
       genuinely stale refresh does.
    2. Current UTC time is between 21:45 and 23:59 — the post-EOD window.
       21:45 gives the real 21:30 cron trigger a 15-minute grace period
       before concluding it didn't fire. Outside this window we skip
       silently: a restart at, say, 08:00 UTC has no reason to believe the
       EOD job was missed, and the next scheduled job (07:00 pre-market or
       tomorrow's 21:30 EOD) will naturally pick up stale data anyway.

    If last_full_refresh has never been recorded (fresh install, no data
    yet), skip silently — there's nothing to catch up on.

    Market-calendar gate (2026-08-02) — same _is_scheduled_trading_day()
    check _eod_job() itself uses, not a standalone weekday guard. This
    function calls _run_analysis_pipeline() directly rather than going
    through _eod_job() (it's a startup-time catch-up, not the real 21:30
    cron firing), so it does NOT automatically inherit _eod_job()'s own
    gate — it needs this check explicitly, or a service restart during
    the 21:45-23:59 UTC window on a genuine market holiday would see
    last_full_refresh stale (correctly so, since the real EOD job no-ops
    on a holiday) and incorrectly conclude a run was "missed", firing a
    real catch-up — including real order placement — against stale price
    data on a day the market is actually closed. This is a materially
    more likely failure mode after 2026-08-02 than before it: previously
    the EOD job ran its full decide phase unconditionally on every
    weekday regardless of holiday status (a separate bug in its own
    right, fixed the same day — see CLAUDE.md's "market-calendar-aware
    scheduling" section), so last_full_refresh rarely if ever went stale
    on a weekday holiday under the old behaviour. Replaces the previous
    plain Saturday/Sunday weekday() check — the calendar check already
    covers weekends too, so nothing is lost, only holidays gained.
    """
    now_utc = datetime.now(timezone.utc)

    if not _is_scheduled_trading_day(now_utc.date()):
        return

    last_refresh_str = get_metadata_str("last_full_refresh")
    if last_refresh_str is None:
        logger.debug(
            "Missed-EOD check: no last_full_refresh recorded yet — "
            "startup init handles the first run."
        )
        return

    try:
        last_dt = datetime.fromisoformat(last_refresh_str)
    except ValueError:
        logger.warning(
            "Missed-EOD check: could not parse last_full_refresh '%s' — skipping.",
            last_refresh_str,
        )
        return

    now_naive  = now_utc.replace(tzinfo=None)
    last_naive = last_dt.replace(tzinfo=None) if last_dt.tzinfo else last_dt
    hours_since = (now_naive - last_naive).total_seconds() / 3600

    window_start = now_naive.replace(hour=21, minute=45, second=0, microsecond=0)
    window_end   = now_naive.replace(hour=23, minute=59, second=59, microsecond=999999)
    if not (window_start <= now_naive <= window_end):
        logger.debug(
            "Missed-EOD check: outside 21:45-23:59 UTC catch-up window (now %s UTC) — "
            "next scheduled job will catch up naturally.",
            now_naive.strftime("%H:%M"),
        )
        return

    if hours_since <= 20:
        logger.debug(
            "Missed-EOD check: last refresh %.1fh ago (<=20h threshold) — "
            "no catch-up needed.", hours_since,
        )
        return

    logger.warning("Missed EOD run detected — running catch-up pipeline now")
    set_metadata("missed_eod_catch_up", now_utc.isoformat())

    import threading  # noqa: PLC0415

    def _run_catch_up_pipeline() -> None:
        # Marked the same way _eod_job() marks its own real work (2026-08-05)
        # -- this thread queues real candidates too, so deploy.sh's
        # check_eod_job_in_progress.py should see it as in-progress just
        # like a full EOD run, not just the scheduled 21:30 job.
        _mark_job_started("eod-catch-up")
        try:
            _run_analysis_pipeline(config, get_all_tickers(config), RUN_FULL)
        finally:
            _mark_job_finished()

    t = threading.Thread(
        target=_run_catch_up_pipeline,
        name="eod-catch-up",
        daemon=True,
    )
    t.start()


def start_scheduler() -> BackgroundScheduler:
    """Start the background scheduler with all scheduled jobs if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    config    = _load_config()
    sched_cfg = config["scheduler"]
    tz        = sched_cfg["timezone"]

    _scheduler = BackgroundScheduler(timezone=tz)

    # Suppress APScheduler's per-job "Running job / executed successfully" INFO spam.
    # Errors and warnings from job functions still surface via the root logger.
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

    # Only "eod" remains in config.yaml's scheduler.runs — the 07:00 Tue-Fri
    # pre-market and 13:30 US-open "analysis only" jobs were removed
    # 2026-07-31 as confirmed-redundant (see this module's own docstring
    # above and CLAUDE.md's "Pre-market/US-open scheduled runs removed"
    # section). This loop is kept generic (rather than hardcoding "eod"
    # directly) so a future genuinely-new scheduled run only needs a
    # config.yaml entry + a _JOB_FNS mapping, not a change here too.
    _JOB_FNS = {
        "eod": _eod_job,
    }

    # No day_of_week filter (2026-08-02, see _is_scheduled_trading_day()'s
    # own docstring) — every job registered through this loop now fires
    # daily and decides for itself, internally, whether today is a real
    # NYSE trading day. A cron day-of-week filter has no concept of a
    # market holiday and would silently exclude Saturday/Sunday before the
    # job ever got the chance to make that decision itself — the opposite
    # of what this loop's jobs (currently just "eod") need.
    for run_name, run_cfg in sched_cfg["runs"].items():
        job_fn = _JOB_FNS.get(run_name)
        if job_fn is None:
            logger.warning("Unknown scheduler run name '%s' — skipping.", run_name)
            continue
        _scheduler.add_job(
            job_fn,
            trigger=CronTrigger(
                hour=run_cfg["hour"],
                minute=run_cfg["minute"],
                timezone=tz,
            ),
            id=f"scheduled_{run_name}",
            replace_existing=True,
        )
        logger.info(
            "Scheduled '%s' job at %02d:%02d %s daily (NYSE-calendar-gated internally).",
            run_cfg.get("label", run_name), run_cfg["hour"], run_cfg["minute"], tz,
        )

    # The Monday-only 07:00 UTC "Monday Pre-Market" job (stale weekend BUY
    # order cancellation + an order-logic re-run) was removed entirely
    # 2026-08-03 — confirmed genuinely unnecessary on both halves: the
    # decide-then-submit architecture never places an order anywhere near
    # market close or over a weekend in the first place (submission
    # happens at 14:30 UTC mid-session, hours before NYSE close), so the
    # scenario the cancellation step guarded against cannot occur; its
    # order-logic half was separately confirmed redundant with the hourly
    # trade alert job below (at the time, the single unconditional
    # _trade_alert_job() this file used to register — split 2026-08-03
    # into market_hours_check/account_state_refresh, see those functions'
    # own docstrings), which fired at this exact same 07:00 UTC minute
    # every Monday. See CLAUDE.md's "Monday Pre-Market removed" section
    # for the full investigation.

    # 60-second dispatcher: executes pending UI-requested jobs (e.g. T212 refresh).
    _scheduler.add_job(
        _dispatch_pending_jobs,
        trigger=IntervalTrigger(seconds=60),
        id="job_dispatcher",
        replace_existing=True,
    )
    logger.info("Scheduled 60-second job dispatcher.")

    # Split 2026-08-03 from the old single, unconditional hourly
    # _trade_alert_job() (hardcoded "0-21,23 mon-fri" cron window) into two
    # genuinely hourly, every-day jobs that each decide internally whether
    # there's real work to do — see _market_hours_check_job() and
    # _account_state_job()'s own docstrings, and CLAUDE.md's "Splitting the
    # hourly trade-alert job" section, for the full investigation and
    # rationale. Neither job carries an hour= or day_of_week= cron filter —
    # both fire every hour, every day, and gate (or don't) on live,
    # real-time state rather than an approximated UTC window, the same
    # "fire unconditionally, decide internally" pattern already proven for
    # the EOD job's own dual-gate fix.
    _scheduler.add_job(
        _market_hours_check_job,
        trigger=CronTrigger(minute=0, timezone=tz),
        id="market_hours_check",
        replace_existing=True,
    )
    logger.info(
        "Scheduled market-hours check hourly, every day (%s) — internally "
        "gated on genuine, live NYSE session hours.", tz,
    )

    # Offset 5 minutes past market_hours_check's own minute=0 (2026-08-03
    # incident fix) — both jobs make several T212 API calls each
    # (market_hours_check: poll_placed_orders() + maybe_exit_positions()'s
    # get_positions(); account_state_refresh: reconcile_positions_with_
    # broker()'s get_positions() + _fetch_t212_snapshot()'s get_account_
    # summary()/get_positions()), and registering both at the identical
    # minute=0 let APScheduler run them concurrently — on their very first
    # joint firing after this split was deployed, the combined burst
    # exhausted T212's rate limit and maybe_exit_positions()'s own
    # get_positions() call gave up after 3 retries, skipping the exit
    # check for that entire cycle (see CLAUDE.md's investigation of the
    # resulting "T212 request failed after 3 attempts" alert). No open
    # position was actually at risk that cycle, but the collision is
    # structural and would recur every hour indefinitely. A 5-minute
    # offset is ample — the worst-case rate-limit backoff inside a single
    # _request() call is well under a minute — and preserves both jobs'
    # "hourly, every day" semantics exactly; only the wall-clock minute
    # within the hour changed, not the gating logic either job runs.
    _scheduler.add_job(
        _account_state_job,
        trigger=CronTrigger(minute=5, timezone=tz),
        id="account_state_refresh",
        replace_existing=True,
    )
    logger.info(
        "Scheduled account-state refresh hourly (5min after market-hours "
        "check, to avoid a T212 rate-limit collision), every day (%s) — "
        "unconditional, no NYSE-hours gate.", tz,
    )

    # Exchange-aware order submission (2026-07-31) — see CLAUDE.md's
    # "Exchange-aware order submission" section. Decision-queuing happens at
    # EOD (unchanged, folded into maybe_place_orders() via the job above).
    # The sibling 07:00 UTC / LSE-open ETF submission job was removed
    # entirely 2026-08-02 alongside the rest of the ETF sleeve — see
    # CLAUDE.md's "ETF sleeve removed from live trading" section — since
    # there is no longer any ETF ticker in `instruments:` for the EOD
    # decide phase to ever queue in the first place; a submission job with
    # nothing to submit is dead weight, not a harmless no-op worth keeping
    # registered.
    #
    # Moved from 13:30 to 14:30 UTC (2026-08-02) — 60 minutes after NYSE's
    # regular open rather than right at it, avoiding the most volatile
    # window of the trading day for a market order's actual fill. See
    # _stock_order_submission_job()'s own docstring for the empirical
    # basis (real intraday data, not assumed) and CLAUDE.md's dedicated
    # section.
    #
    # No day_of_week filter (2026-08-03 fix — found still using the old
    # day_of_week="mon-fri" cron pattern, with no internal calendar check
    # at all, a real gap: a plain weekday filter has no concept of a US
    # market holiday, so this job — the one that places real orders —
    # would have fired and attempted submission on a Monday that was also
    # a genuine NYSE closure). Fires every single day now, gated
    # internally by _is_nyse_open_now() inside the job itself, matching
    # the same "fire unconditionally, check the real calendar internally"
    # pattern scheduled_eod/membership_refresh already established —
    # except this one uses the stricter, live-session check
    # (_is_nyse_open_now(), the same mechanism market_hours_check_job()
    # uses), not the day-level _is_scheduled_trading_day() those two use,
    # since 14:30 UTC is deliberately timed to land inside NYSE's live
    # session, not merely on a trading day.
    _scheduler.add_job(
        _stock_order_submission_job,
        trigger=CronTrigger(hour=14, minute=30, timezone=tz),
        id="stock_order_submission",
        replace_existing=True,
    )
    logger.info(
        "Scheduled stock order submission at 14:30 %s daily "
        "(NYSE-hours-gated internally via _is_nyse_open_now()).", tz,
    )

    # Membership refresh — 15 minutes ahead of the EOD job's own 21:30 slot,
    # so any real S&P 500 addition/removal is reflected before that run's
    # trading decisions. Fixed at 21:15 rather than derived from
    # sched_cfg["runs"]["eod"] — this job's only requirement is "shortly
    # before EOD", not "exactly N minutes before whatever EOD currently
    # is". No day_of_week filter
    # (2026-08-02, matching the EOD loop above) — fires daily, gated
    # internally by the same _is_scheduled_trading_day() NYSE check EOD
    # itself uses, so the two jobs can never disagree about whether today
    # counts.
    _scheduler.add_job(
        _membership_refresh_job,
        trigger=CronTrigger(
            hour=21,
            minute=15,
            timezone=tz,
        ),
        id="membership_refresh",
        replace_existing=True,
    )
    logger.info("Scheduled membership refresh at 21:15 %s daily (NYSE-calendar-gated internally).", tz)

    # Weekly summary — Sunday 09:00 *Europe/London*, not this scheduler's
    # own default UTC timezone (`tz` above). CronTrigger's own `timezone`
    # parameter is independent of the scheduler's default and correctly
    # resolves real IANA DST transitions (verified directly: the same
    # trigger definition fires at 09:00 UTC in January/GMT and 08:00 UTC in
    # July/BST) — so this genuinely lands at 9am UK local time year-round,
    # not a fixed UTC offset that would drift an hour off across the
    # BST/GMT boundary.
    _scheduler.add_job(
        _weekly_summary_job,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=9,
            minute=0,
            timezone="Europe/London",
        ),
        id="weekly_summary",
        replace_existing=True,
    )
    logger.info("Scheduled weekly summary at 09:00 Europe/London (Sunday).")

    _scheduler.start()
    logger.info("Scheduler started with %d jobs.", len(_scheduler.get_jobs()))

    # Write an immediate heartbeat so the Streamlit UI detects a running
    # scheduler straight away (the dispatcher job writes subsequent heartbeats
    # every 60 seconds, but takes up to 60s to fire for the first time).
    try:
        set_metadata("scheduler_heartbeat", datetime.utcnow().isoformat())
    except Exception:
        pass

    # Check for missed EOD runs after starting, so the scheduler is already
    # accepting jobs before we potentially queue a catch-up run.
    _check_missed_eod_run(config)

    return _scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    _scheduler = None
