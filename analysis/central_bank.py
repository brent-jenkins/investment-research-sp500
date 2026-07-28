"""BoJ / ECB central bank calendar awareness.

Mirrors analysis/fomc.py's decision-day pattern for the two other central
banks this system tracks via dedicated correlation pairs (USDJPY=X/^GSPC —
carry-trade proxy for BoJ policy; VGK/ECBDFR — European equities vs the ECB
deposit rate; see analysis/correlations.py's RELATIONSHIP_MAP). Unlike FOMC,
whose decision/minutes dates are hardcoded in analysis/fomc.py and updated in
code, BoJ/ECB meeting dates are read from config.yaml's
`central_bank_events.boj.dates` / `.ecb.dates` — both calendars are published
well in advance and rarely move, so keeping them in config.yaml lets the
dates be corrected without a code deploy. See CLAUDE.md's central bank event
monitoring section for the annual (January) update note.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_EVENT_META: dict[str, tuple[str, str]] = {
    "boj": ("BoJ Decision", "Bank of Japan Policy Decision"),
    "ecb": ("ECB Decision", "ECB Monetary Policy Decision"),
}


def _parse_dates(date_strs: list[str], bank: str) -> list[date]:
    parsed: list[date] = []
    for s in date_strs or []:
        try:
            parsed.append(datetime.strptime(s, "%Y-%m-%d").date())
        except (ValueError, TypeError):
            logger.warning(
                "central_bank_events.%s: could not parse date %r — skipping.", bank, s
            )
    return parsed


def _bank_dates(config: dict, bank: str) -> list[date]:
    cfg = (config or {}).get("central_bank_events", {}).get(bank, {}) or {}
    return _parse_dates(cfg.get("dates", []), bank)


def is_boj_decision_day(config: dict, check_date: Optional[date] = None) -> bool:
    """Return True if ``check_date`` (default: today UTC) is a BoJ decision day.

    Dates come from config.yaml — no network dependency, safe for the scheduler.
    """
    if check_date is None:
        check_date = datetime.now(timezone.utc).date()
    return check_date in _bank_dates(config, "boj")


def is_ecb_decision_day(config: dict, check_date: Optional[date] = None) -> bool:
    """Return True if ``check_date`` (default: today UTC) is an ECB decision day.

    Dates come from config.yaml — no network dependency, safe for the scheduler.
    """
    if check_date is None:
        check_date = datetime.now(timezone.utc).date()
    return check_date in _bank_dates(config, "ecb")


def get_central_bank_events(config: dict, days_ahead: int = 7) -> list[dict]:
    """Return upcoming BoJ/ECB events within the next ``days_ahead`` days.

    Same dict shape as analysis.fomc.get_fomc_events(): date / title /
    event_type / days_away. Used by the Daily Briefing's Key Events strip
    (main.py) alongside the existing FOMC events, and by the Status page's
    Scheduler Health table via is_boj_decision_day()/is_ecb_decision_day().
    """
    today  = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=days_ahead)

    events: dict[tuple[str, date], dict] = {}
    for bank, (event_type, title) in _EVENT_META.items():
        for d in _bank_dates(config, bank):
            if today <= d <= cutoff:
                events[(bank, d)] = {
                    "date":       d,
                    "title":      title,
                    "event_type": event_type,
                    "days_away":  (d - today).days,
                }

    return sorted(events.values(), key=lambda e: e["date"])
