"""FOMC/Fed calendar awareness.

The Fed's public press-release API (ne-press.json) only surfaces *published*
releases, not future scheduled meetings.  Upcoming FOMC dates therefore come
from a hardcoded 2025-2026 schedule (updated annually); the API is queried only
for today's releases so that real speeches or surprise announcements are caught.

Public API: https://www.federalreserve.gov/json/ne-press.json
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded 2025-2026 schedules
# All dates are the *decision day* (last day of each 2-day meeting, when the
# FOMC Statement is released at 2:00 PM US/Eastern).
# ---------------------------------------------------------------------------
_FOMC_DECISION_DATES: list[date] = [
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5,  7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 17),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 16),
]

# Minutes released exactly 3 weeks after each meeting.
_FOMC_MINUTES_DATES: list[date] = [
    # 2025 meetings → minutes
    date(2025, 2, 19), date(2025, 4,  9), date(2025, 5, 28), date(2025, 7,  9),
    date(2025, 8, 20), date(2025, 10, 8), date(2025, 11, 19), date(2026, 1,  7),
    # 2026 meetings → minutes
    date(2026, 2, 18), date(2026, 4,  8), date(2026, 5, 20), date(2026, 7,  8),
    date(2026, 8, 19), date(2026, 10, 7), date(2026, 11, 18), date(2027, 1,  6),
]

_FED_PRESS_API = "https://www.federalreserve.gov/json/ne-press.json"

# Module-level cache for today's API results
_today_cache_date: Optional[date] = None
_today_cache_events: list[dict] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_fed_date(d_str: str) -> Optional[date]:
    """Parse the Fed's date format 'M/D/YYYY h:MM:SS AM/PM' → date."""
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(d_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _classify_fed_event(title: str, press_type: str) -> Optional[str]:
    """Map (title, press_type) → event_type string, or None if not market-relevant."""
    if press_type != "Monetary Policy":
        return None
    tl = title.lower()
    if "statement" in tl or "federal open market committee" in tl:
        return "FOMC Decision"
    if "minutes" in tl:
        return "FOMC Minutes"
    if any(w in tl for w in ("speech", "remarks", "testimony", "address", "discussion")):
        return "Fed Speech"
    return "Fed Announcement"


def _fetch_todays_api_events() -> list[dict]:
    """Query the Fed API for any monetary-policy releases published today.

    Results are cached for the rest of the calendar day.
    """
    global _today_cache_date, _today_cache_events

    today = datetime.now(timezone.utc).date()
    if _today_cache_date == today:
        return _today_cache_events

    _today_cache_date  = today
    _today_cache_events = []

    try:
        resp = requests.get(_FED_PRESS_API, timeout=6)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.debug("Fed press API unavailable: %s", exc)
        return []

    seen_types: set[str] = set()
    for item in raw:
        ev_date = _parse_fed_date(item.get("d", ""))
        if ev_date != today:
            # API is newest-first; once we're past today stop scanning
            if ev_date is not None and ev_date < today:
                break
            continue
        ev_type = _classify_fed_event(item.get("t", ""), item.get("pt", ""))
        if ev_type is None or ev_type in seen_types:
            continue
        seen_types.add(ev_type)
        _today_cache_events.append({
            "date":       ev_date,
            "title":      item.get("t", ""),
            "event_type": ev_type,
            "days_away":  0,
        })

    return _today_cache_events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_fomc_events(days_ahead: int = 7) -> list[dict]:
    """Return Fed/FOMC calendar events within the next ``days_ahead`` days.

    Each item is a dict:
      date       — calendar date of the event
      title      — short description
      event_type — "FOMC Decision" | "FOMC Minutes" | "Fed Speech" | "Fed Announcement"
      days_away  — 0 = today, 1 = tomorrow, etc.

    Future dates come from the hardcoded 2025-2026 schedule; today's events are
    supplemented by the Fed's live press-release API (catches speeches, etc.).
    """
    today  = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=days_ahead)

    events: dict[date, dict] = {}

    # ── Hardcoded future decisions ─────────────────────────────────────────
    for d in _FOMC_DECISION_DATES:
        if today <= d <= cutoff:
            events[d] = {
                "date":       d,
                "title":      "FOMC Meeting — interest rate decision",
                "event_type": "FOMC Decision",
                "days_away":  (d - today).days,
            }

    # ── Hardcoded future minutes ───────────────────────────────────────────
    for d in _FOMC_MINUTES_DATES:
        if today <= d <= cutoff and d not in events:
            events[d] = {
                "date":       d,
                "title":      "FOMC Meeting Minutes release",
                "event_type": "FOMC Minutes",
                "days_away":  (d - today).days,
            }

    # ── Today's live API events (speeches, surprise announcements) ─────────
    for ev in _fetch_todays_api_events():
        d = ev["date"]
        if d not in events:
            events[d] = ev
        elif ev["event_type"] == "FOMC Decision":
            # Live confirmation takes priority on decision day
            events[d] = ev

    return sorted(events.values(), key=lambda e: e["date"])


def is_fomc_decision_day(check_date: Optional[date] = None) -> bool:
    """Return True if ``check_date`` (default: today UTC) is an FOMC decision day.

    Uses the hardcoded schedule — no network dependency, safe for the scheduler.
    """
    if check_date is None:
        check_date = datetime.now(timezone.utc).date()
    return check_date in _FOMC_DECISION_DATES
