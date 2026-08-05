"""Tests for check_eod_job_in_progress.py -- the standalone script
deploy.sh polls to ask "is EOD (or its missed-EOD catch-up sibling)
genuinely still running right now?" (2026-08-05).

main() is the whole exit-code contract deploy.sh depends on (see
deploy.sh's own "job in progress" comment): 0 means safe to proceed, 1
means keep deferring. These tests call main() directly rather than
spawning a real subprocess, mirroring webhook_listener.py's own
testable-function-with-a-thin-__main__-guard convention -- see that
file's should_trigger_deploy()/verify_signature() for the same shape.
"""
import check_eod_job_in_progress as checker


def test_returns_0_when_no_job_marked(mocker):
    mocker.patch("data.scheduler.get_job_in_progress_status", return_value=None)
    assert checker.main() == 0


def test_returns_1_when_job_genuinely_in_progress(mocker, capsys):
    mocker.patch(
        "data.scheduler.get_job_in_progress_status",
        return_value={"job": "eod", "started_at": "2026-08-05T21:30:11", "elapsed_seconds": 300},
    )
    assert checker.main() == 1
    assert "eod" in capsys.readouterr().err


def test_returns_0_when_marker_is_stale(mocker, capsys):
    """A marker older than JOB_IN_PROGRESS_STALE_AFTER_SECONDS (3600s) is
    treated as left behind by a killed/crashed prior run, not a
    genuinely still-running job -- deploy.sh must not defer forever
    because a process died mid-run without clearing its own marker."""
    mocker.patch(
        "data.scheduler.get_job_in_progress_status",
        return_value={"job": "eod", "started_at": "2026-08-05T20:00:00", "elapsed_seconds": 5000},
    )
    assert checker.main() == 0
    assert "stale" in capsys.readouterr().err


def test_boundary_exactly_at_stale_ceiling_still_counts_as_in_progress(mocker):
    """elapsed_seconds == the ceiling exactly (not yet OVER it) must still
    block -- strictly-greater-than is the deliberate boundary, matching
    "> ceiling" in the implementation, not ">=".) """
    mocker.patch(
        "data.scheduler.get_job_in_progress_status",
        return_value={"job": "eod", "started_at": "x", "elapsed_seconds": 3600},
    )
    assert checker.main() == 1


def test_one_second_past_the_ceiling_is_stale(mocker):
    mocker.patch(
        "data.scheduler.get_job_in_progress_status",
        return_value={"job": "eod", "started_at": "x", "elapsed_seconds": 3601},
    )
    assert checker.main() == 0
