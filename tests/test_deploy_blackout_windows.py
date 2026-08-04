"""Drift-detection guard for deploy.sh's deployment blackout windows
(2026-08-04).

`deploy.sh` (and its gitignored, local-only PowerShell twin, deploy.ps1)
warn before a deploy if it lands close to a real, scheduled, order-touching
job — see deploy.sh's own "Deployment blackout window check" comment for
the full rationale. Those windows are a hand-maintained bash/PowerShell
snapshot of data/scheduler.py:start_scheduler()'s real CronTrigger
registrations, and have already gone stale twice tonight (stock submission
moved 13:30 -> 14:30 UTC without the window being updated; the Monday
pre-market job was removed entirely while its window stayed behind) --
the same class of drift this project has hit more than once (see
CLAUDE.md's "one-sided decision rule" Layer 4 and its own precedent for
this exact "test the snapshot against the real source" pattern).

Rather than trust the bash snapshot is still correct, this module starts
the REAL scheduler (the exact same start_scheduler() call every other
scheduler-registration test in this suite already uses, and the one
function that actually determines the live schedule) and cross-checks its
real trigger fields against deploy.sh's WINDOW_LO/WINDOW_HI shell variables
-- parsed directly out of the file, not re-typed by hand here, so this
test itself can't silently drift from what deploy.sh actually contains.

A fully dynamic redesign (deploy.sh shelling out to Python at deploy time
to compute the blackout check live) was considered and rejected in favour
of this recompute-and-guard approach -- it would require either running
data/scheduler.py:start_scheduler() itself from a one-off deploy-time
script (a real side effect: it starts a genuine background scheduler
thread and writes a heartbeat to system_metadata, neither of which
deploy.sh should ever trigger just to check the clock) or a materially
larger refactor extracting a new, pure "describe the schedule" function
out of that safety-critical file. A test-time guard achieves the same
"never silently stale" goal with a much smaller, lower-risk footprint,
matching the explicit precedent this task cited (the Layer 4 exemption
list is checked the same way: computed once, then compared against a
live-computed value, in a test -- never re-derived live inside the thing
being guarded).
"""
import re
from pathlib import Path

import pytest

import data.scheduler as scheduler

_DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"
_DEPLOY_PS1 = Path(__file__).resolve().parent.parent / "deploy.ps1"


def _parse_window(text: str, name: str) -> tuple[int, int]:
    """Extract `NAME=1234` / `NAME_LO=1234` + `NAME_HI=1234`-style bash
    variable assignments for a given window name and return (lo, hi)."""
    lo = re.search(rf"^{name}_LO=(\d+)", text, re.MULTILINE)
    hi = re.search(rf"^{name}_HI=(\d+)", text, re.MULTILINE)
    assert lo, f"{name}_LO= not found in deploy.sh -- window renamed or removed?"
    assert hi, f"{name}_HI= not found in deploy.sh -- window renamed or removed?"
    return int(lo.group(1)), int(hi.group(1))


def _in_window(hhmm: int, lo: int, hi: int) -> bool:
    return lo <= hhmm <= hi


def _in_wrapping_window(minute: int, lo: int, hi: int) -> bool:
    """For the hourly window, which wraps across the hour boundary
    (minute >= lo OR minute <= hi, e.g. lo=55 hi=10)."""
    return minute >= lo or minute <= hi


def _real_scheduler_jobs():
    """Starts the real scheduler (DB/config side effects mocked exactly
    like every other scheduler-registration test in this suite) and
    returns the live sched object. Caller must call
    scheduler.stop_scheduler() when done -- mirrors every existing
    registration test's own try/finally shape.

    Deliberately does NOT mock _load_config() -- the EOD hour/minute this
    test checks against deploy.sh's EOD window comes from the REAL,
    current config.yaml, not a hardcoded fake value that could itself
    drift from production. Only the DB-touching calls (get_metadata_str/
    set_metadata, used for the heartbeat write) are mocked, matching
    every other test that calls start_scheduler() directly.
    """
    return scheduler.start_scheduler()


def _field(sched, job_id: str, name: str) -> str:
    job = sched.get_job(job_id)
    assert job is not None, f"job '{job_id}' was not registered -- id renamed or removed?"
    return next(str(f) for f in job.trigger.fields if f.name == name)


def test_blackout_windows_match_real_scheduler_registrations(monkeypatch):
    """The decisive drift check: every real, currently-registered
    order-touching job's fire time must fall inside deploy.sh's
    corresponding blackout window, and vice versa (the window bounds must
    correspond to a real job, not a stale leftover)."""
    monkeypatch.setattr(scheduler, "get_metadata_str", lambda key: None)
    monkeypatch.setattr(scheduler, "set_metadata", lambda *a, **k: None)

    deploy_sh_text = _DEPLOY_SH.read_text(encoding="utf-8")

    sched = _real_scheduler_jobs()
    try:
        # --- scheduled_eod (config-driven, real config.yaml) ---------------
        eod_hour = int(_field(sched, "scheduled_eod", "hour"))
        eod_minute = int(_field(sched, "scheduled_eod", "minute"))
        eod_hhmm = eod_hour * 100 + eod_minute
        assert _field(sched, "scheduled_eod", "day_of_week") == "*", (
            "scheduled_eod must have no day-of-week cron filter (fires every day, "
            "gated internally) -- deploy.sh's weekday-only approximation for this "
            "window assumes that; if this ever changes, the approximation needs "
            "revisiting too, not just the window bounds."
        )
        eod_lo, eod_hi = _parse_window(deploy_sh_text, "EOD_WINDOW")
        assert _in_window(eod_hhmm, eod_lo, eod_hi), (
            f"scheduled_eod fires at {eod_hhmm:04d} UTC but deploy.sh's EOD_WINDOW "
            f"is {eod_lo:04d}-{eod_hi:04d} -- deploy.sh has drifted from the real "
            f"scheduled_eod registration (config.yaml scheduler.runs.eod)."
        )

        # --- membership_refresh (hardcoded 21:15) ---------------------------
        mr_hour = int(_field(sched, "membership_refresh", "hour"))
        mr_minute = int(_field(sched, "membership_refresh", "minute"))
        mr_hhmm = mr_hour * 100 + mr_minute
        assert _in_window(mr_hhmm, eod_lo, eod_hi), (
            f"membership_refresh fires at {mr_hhmm:04d} UTC but deploy.sh's "
            f"EOD_WINDOW ({eod_lo:04d}-{eod_hi:04d}) no longer covers it -- "
            f"deploy.sh has drifted from the real membership_refresh registration."
        )

        # --- stock_order_submission (hardcoded 14:30) ------------------------
        ss_hour = int(_field(sched, "stock_order_submission", "hour"))
        ss_minute = int(_field(sched, "stock_order_submission", "minute"))
        ss_hhmm = ss_hour * 100 + ss_minute
        assert _field(sched, "stock_order_submission", "day_of_week") == "*", (
            "stock_order_submission must have no day-of-week cron filter -- "
            "deploy.sh's weekday-only approximation assumes that."
        )
        ss_lo, ss_hi = _parse_window(deploy_sh_text, "STOCK_SUBMISSION_WINDOW")
        assert _in_window(ss_hhmm, ss_lo, ss_hi), (
            f"stock_order_submission fires at {ss_hhmm:04d} UTC but deploy.sh's "
            f"STOCK_SUBMISSION_WINDOW is {ss_lo:04d}-{ss_hi:04d} -- deploy.sh has "
            f"drifted from the real stock_order_submission registration "
            f"(data/scheduler.py:start_scheduler())."
        )

        # --- weekly_summary (Sunday 09:00 Europe/London) ---------------------
        assert _field(sched, "weekly_summary", "day_of_week") == "sun"
        assert _field(sched, "weekly_summary", "hour") == "9"
        assert _field(sched, "weekly_summary", "minute") == "0"
        assert str(sched.get_job("weekly_summary").trigger.timezone) == "Europe/London", (
            "weekly_summary's real trigger timezone changed -- deploy.sh's "
            "WEEKLY_SUMMARY_WINDOW hardcodes both possible UTC times "
            "(08:00 BST / 09:00 GMT) for Sunday 09:00 Europe/London specifically; "
            "a different local time or timezone needs deploy.sh's window recomputed."
        )
        ws_lo, ws_hi = _parse_window(deploy_sh_text, "WEEKLY_SUMMARY_WINDOW")
        # Both real UTC equivalents of Sunday 09:00 Europe/London must fall
        # inside the window -- not just one, since the window exists
        # specifically to cover both without computing today's DST state.
        assert _in_window(800, ws_lo, ws_hi), "BST (08:00 UTC) equivalent not covered"
        assert _in_window(900, ws_lo, ws_hi), "GMT (09:00 UTC) equivalent not covered"

        # --- market_hours_check (every hour at :00) ---------------------------
        assert _field(sched, "market_hours_check", "hour") == "*", (
            "market_hours_check must fire every hour (no hour cron filter) -- "
            "deploy.sh's HOURLY_WINDOW assumes that; a change to a fixed hour "
            "would make this an ordinary once-daily window instead."
        )
        assert _field(sched, "market_hours_check", "day_of_week") == "*"
        mh_minute = int(_field(sched, "market_hours_check", "minute"))
        hourly_lo, hourly_hi = _parse_window(deploy_sh_text, "HOURLY_WINDOW")
        assert _in_wrapping_window(mh_minute, hourly_lo, hourly_hi), (
            f"market_hours_check fires at minute {mh_minute} of every hour but "
            f"deploy.sh's HOURLY_WINDOW ({hourly_lo}-{hourly_hi}, wrapping) does "
            f"not cover it."
        )

        # --- account_state_refresh (every hour at :05) -------------------------
        assert _field(sched, "account_state_refresh", "hour") == "*"
        assert _field(sched, "account_state_refresh", "day_of_week") == "*"
        as_minute = int(_field(sched, "account_state_refresh", "minute"))
        assert _in_wrapping_window(as_minute, hourly_lo, hourly_hi), (
            f"account_state_refresh fires at minute {as_minute} of every hour but "
            f"deploy.sh's HOURLY_WINDOW ({hourly_lo}-{hourly_hi}, wrapping) does "
            f"not cover it."
        )
        # The two hourly jobs must not share a minute (a real 2026-08-03
        # production incident -- see CLAUDE.md's "structural safeguards
        # against future T212 API scheduling collisions" section). Not this
        # test's primary purpose (already covered elsewhere), but cheap to
        # reassert here since both values are already in hand.
        assert mh_minute != as_minute

        # --- confirmed-removed jobs must not have snuck back in --------------
        assert sched.get_job("monday_premarket") is None, (
            "monday_premarket exists again -- deploy.sh's blackout windows "
            "deliberately no longer cover it (removed 2026-08-03); if this job "
            "genuinely came back, deploy.sh needs a window for it added back too."
        )
        assert sched.get_job("etf_order_submission") is None
    finally:
        scheduler.stop_scheduler()


def test_deploy_sh_no_longer_references_removed_monday_premarket_job():
    """A stale window referencing a job that no longer exists is exactly
    the kind of silent drift this guard exists to catch -- checked
    directly against the file text, not just "no window matches its old
    time slot" (which the main test above already covers structurally)."""
    text = _DEPLOY_SH.read_text(encoding="utf-8")
    assert "0645" not in text and "0715" not in text, (
        "deploy.sh still references the old Monday pre-market window bounds "
        "(06:45-07:15) -- that job was removed entirely 2026-08-03."
    )


def test_deploy_sh_hourly_window_bounds_are_a_genuine_wrap():
    """HOURLY_WINDOW_LO must be greater than HOURLY_WINDOW_HI (e.g. 55/10)
    for the wrapping in_range check in deploy.sh to behave as intended --
    if someone "fixes" this to a non-wrapping pair (e.g. swaps the values,
    or sets lo < hi) the bash `-ge LO || -le HI` check would silently
    become almost-always-true (covering nearly the whole hour) instead of
    the intended tight band around the two real fire minutes."""
    text = _DEPLOY_SH.read_text(encoding="utf-8")
    lo, hi = _parse_window(text, "HOURLY_WINDOW")
    assert lo > hi, (
        f"HOURLY_WINDOW_LO ({lo}) must be greater than HOURLY_WINDOW_HI ({hi}) "
        "for deploy.sh's wrapping in_range check to work as intended."
    )


def test_deploy_ps1_windows_match_deploy_sh_if_present():
    """deploy.ps1 is gitignored (local-only, contains an SSH key path) --
    it does not exist in a fresh clone or a git worktree, so this check
    skips gracefully rather than failing when it's simply absent. When it
    IS present (this developer's own machine, right now), its window
    bounds must match deploy.sh's exactly -- deploy.ps1 is not covered by
    the main scheduler-registration test above (parsing PowerShell from
    Python is not worth the complexity for a single local file), so this
    is the only guard against deploy.sh and deploy.ps1 silently drifting
    apart from each other after one is updated and the other isn't."""
    if not _DEPLOY_PS1.exists():
        pytest.skip("deploy.ps1 is gitignored/local-only and not present in this checkout")

    sh_text = _DEPLOY_SH.read_text(encoding="utf-8")
    ps1_text = _DEPLOY_PS1.read_text(encoding="utf-8")

    sh_windows = {
        name: _parse_window(sh_text, name)
        for name in ("STOCK_SUBMISSION_WINDOW", "EOD_WINDOW", "WEEKLY_SUMMARY_WINDOW", "HOURLY_WINDOW")
    }

    def _parse_ps1_window(name: str) -> tuple[int, int]:
        lo = re.search(rf"\${name}Lo\s*=\s*(\d+)", ps1_text)
        hi = re.search(rf"\${name}Hi\s*=\s*(\d+)", ps1_text)
        assert lo, f"${name}Lo not found in deploy.ps1"
        assert hi, f"${name}Hi not found in deploy.ps1"
        return int(lo.group(1)), int(hi.group(1))

    ps1_name_map = {
        "STOCK_SUBMISSION_WINDOW": "StockSubmissionWindow",
        "EOD_WINDOW": "EodWindow",
        "WEEKLY_SUMMARY_WINDOW": "WeeklySummaryWindow",
        "HOURLY_WINDOW": "HourlyWindow",
    }
    for sh_name, ps1_name in ps1_name_map.items():
        assert _parse_ps1_window(ps1_name) == sh_windows[sh_name], (
            f"deploy.ps1's {ps1_name} bounds don't match deploy.sh's {sh_name} bounds "
            "-- the two scripts have drifted apart."
        )
