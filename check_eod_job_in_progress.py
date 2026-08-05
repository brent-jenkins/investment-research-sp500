"""Standalone check for deploy.sh: is an order-relevant EOD job genuinely
still running right now?

Added 2026-08-05 after a real incident: deploy.sh's blackout-window
mechanism only ever inferred "EOD might still be running" from a static
wall-clock window (21:00-22:00 UTC), sized generously enough for the
schedule the windows themselves were derived from, but never actually
checked against the job's real, current runtime. Once the live universe
grew to 506 tickers, a deploy that started deferring early in that window
could still hit its own wait cap and force a service restart while EOD
was genuinely mid-run -- see CLAUDE.md's "GitHub webhook deploy" for the
full incident. Real-time detection closes that gap directly rather than
just widening the cap further (deploy.sh's own cap was ALSO raised, as a
backstop for jobs without this instrumentation -- the two are
complementary, not either/or).

Exit code is the contract deploy.sh's compute_blackout_label() reads:
  0 -- safe to proceed: no job is marked in progress, or its marker is
       old enough to treat as left behind by a killed/crashed prior run
       rather than trusted forever (see
       data.scheduler.JOB_IN_PROGRESS_STALE_AFTER_SECONDS).
  1 -- genuinely still running -- deploy.sh should keep deferring.

Deliberately reuses data.scheduler's real marker accessors
(get_job_in_progress_status(), written by _mark_job_started()/
_mark_job_finished() inside _eod_job() and the missed-EOD catch-up
thread) rather than re-deriving the DB path or the metadata schema a
second time here -- this file is the ONLY thing outside data/scheduler.py
that needs to know the marker exists.

Usage (invoked by deploy.sh once per blackout-defer poll):
    python3.11 check_eod_job_in_progress.py
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path regardless of working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    """Returns the process exit code (0/1) -- see module docstring for the
    contract. Kept separate from the __main__ guard so tests can call this
    directly rather than spawning a real subprocess for every case."""
    from data.scheduler import JOB_IN_PROGRESS_STALE_AFTER_SECONDS, get_job_in_progress_status

    status = get_job_in_progress_status()
    if status is None:
        return 0

    elapsed = status.get("elapsed_seconds", 0)
    if elapsed > JOB_IN_PROGRESS_STALE_AFTER_SECONDS:
        print(
            f"job-in-progress marker for '{status.get('job', '?')}' is {elapsed:.0f}s old "
            f"(> {JOB_IN_PROGRESS_STALE_AFTER_SECONDS}s ceiling) -- treating as stale, likely "
            f"left behind by a killed/crashed prior run, not a genuinely still-running job.",
            file=sys.stderr,
        )
        return 0

    print(
        f"'{status.get('job', '?')}' has been running since {status.get('started_at', '?')} UTC "
        f"({elapsed:.0f}s so far)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
