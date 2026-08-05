#!/usr/bin/env bash
# deploy.sh — the ONE canonical implementation of "deploy this app". Runs
# directly on the VM — either invoked by webhook_listener.py in response to
# a verified GitHub push webhook (the normal path: push to main, the
# webhook does the rest), or run manually over SSH as the break-glass
# fallback if the webhook path itself is ever unavailable (listener down,
# GitHub delivery failing, etc.):
#
#   ssh -i ssh-keys/deploy_key root@203.0.113.10 \
#     "cd /root/investment-research && bash deploy.sh"
#
# There used to also be a "local/dev mode" here (and a PowerShell twin,
# deploy.ps1) that pushed from the developer's own machine and then SSHed
# in to invoke this same script — removed 2026-08-05 once the GitHub
# webhook made it redundant for routine deploys (any `git push origin
# main` already triggers one) and its own interactive blackout-window
# prompt added little the webhook's defer-then-proceed handling didn't
# already cover for the cases that matter. The one thing that wrapper gave
# up was mattering less than the maintenance cost of a second, rarely-used
# path — see CLAUDE.md's "GitHub webhook deploy" for the reasoning. A
# manual emergency deploy is still just as possible: SSH in and run this
# script directly, as above.
#
# This further replaced an even earlier design (pre-2026-08-05) where
# deploy.sh, deploy.ps1, AND the git pre-push hook each carried their own
# independent copy of the same remote command chain — three hand-maintained
# implementations of "deploy this" that kept drifting apart (see CLAUDE.md's
# "Deploy pipeline systemd parity audit"). deploy_steps() below is now the
# only place any of that logic lives, called from exactly one place
# (webhook_listener.py) or manually as shown above.
#
# Usage:
#   bash deploy.sh    # always runs directly, here, right now -- see above
#
# Override the app dir / service name / systemd dir at runtime if needed:
#   APP_DIR=/root/other SERVICE=other-name bash deploy.sh
APP_DIR="${APP_DIR:-/root/investment-research}"
SERVICE="${SERVICE:-investment-research}"
# Overridable so tests can point deploy_steps() at a scratch directory
# instead of the real /etc/systemd/system -- defaults to the real path in
# every actual deploy.
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

set -euo pipefail

# --- Concurrency guard -------------------------------------------------------
# There are now two independent ways a deploy can be triggered for the same
# push (GitHub's own native webhook, and -- once added -- a GitHub Actions
# workflow calling the same listener endpoint after tests pass), plus the
# manual break-glass SSH path. deploy_steps() has no other mutual exclusion,
# so two overlapping runs could otherwise both `git pull`/copy service files/
# restart services concurrently -- individually idempotent-ish, but racing
# `systemctl daemon-reload` against a restart, or two `systemctl restart`
# calls for the same unit overlapping, is not something to rely on being
# harmless. A lock ensures a second trigger waits for the first to finish
# (not silently dropped -- it still runs afterward, which is normally just a
# fast no-op since the first run already pulled the same commit) rather than
# running in parallel with it. Held for the whole script (blackout wait
# included), not just deploy_steps(), so a second trigger doesn't
# independently poll/defer through its own blackout wait in parallel with
# the first.
#
# mkdir-based, not flock: `mkdir` succeeding-or-failing atomically as a
# mutex is a portable shell idiom that works identically on the real Ubuntu
# server AND in Git Bash on Windows (where this suite's own tests run --
# see CLAUDE.md's "runs identically on Windows and Ubuntu" requirement).
# `flock` was tried first and does not exist in Git Bash at all -- confirmed
# directly (tests failed with "flock: command not found"), not assumed.
LOCK_DIR="${DEPLOY_LOCK_DIR:-${APP_DIR}/.deploy.lock.d}"
LOCK_WAIT_INTERVAL_SECONDS="${DEPLOY_LOCK_WAIT_INTERVAL_SECONDS:-1}"
# Bounded, same "never wait forever" philosophy as the blackout defer loop
# below -- if a prior run crashed without cleaning up its lock directory,
# this breaks it rather than hanging every future deploy indefinitely.
#
# MUST be comfortably longer than the blackout defer loop's own
# DEPLOY_MAX_BLACKOUT_WAIT_SECONDS cap (6000s default, see below) plus real
# deploy_steps() runtime -- a legitimate first holder can hold this lock
# for up to that entire duration. Confirmed as a REAL bug, not
# theoretical: an earlier default of 600s here (shorter than the
# then-1800s blackout cap) meant a second, still-legitimately-waiting
# process would hit ITS OWN 600s patience limit before the first
# process's blackout wait finished, wrongly conclude the lock was stale,
# forcibly break it, and start running concurrently with the
# still-very-much-alive first process -- observed live in production as
# five separate deploy.sh processes all running at once, each having
# stolen the lock from the last, every one independently polling its own
# blackout-defer loop. Raised again 2026-08-05, in step with
# DEPLOY_MAX_BLACKOUT_WAIT_SECONDS below, to preserve this same ordering
# after that cap itself was raised -- see that constant's own comment for
# why. tests/test_deploy_server_side_blackout.py's
# test_lock_max_wait_default_exceeds_blackout_max_wait_default statically
# guards the ordering so the two can't silently drift apart again.
LOCK_MAX_WAIT_SECONDS="${DEPLOY_LOCK_MAX_WAIT_SECONDS:-7200}"

_release_deploy_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap _release_deploy_lock EXIT

lock_waited=0
while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  if [ "$lock_waited" -ge "$LOCK_MAX_WAIT_SECONDS" ]; then
    echo "[$(date -u +%FT%TZ)] Deploy lock still held after ${LOCK_MAX_WAIT_SECONDS}s -- assuming it's stale (a crashed prior run) and breaking it."
    rmdir "$LOCK_DIR" 2>/dev/null || true
    mkdir "$LOCK_DIR" 2>/dev/null || true
    break
  fi
  sleep "$LOCK_WAIT_INTERVAL_SECONDS"
  lock_waited=$((lock_waited + LOCK_WAIT_INTERVAL_SECONDS))
done
# --- End concurrency guard ----------------------------------------------------

# --- Deployment blackout window check --------------------------------------
# Scheduled jobs (data pull + analysis + order placement) run at fixed UTC
# times — see "Deployment timing" in CLAUDE.md. Deploying inside one of these
# windows risks the job executing against half-updated code or config
# (partially applied git pull, service mid-restart) and can place broker
# orders on that transitional state.
#
# Re-verified against data/scheduler.py:start_scheduler()'s actual, current
# CronTrigger registrations 2026-08-04 — see
# tests/test_deploy_blackout_windows.py, which starts the REAL scheduler and
# cross-checks its live trigger fields against the WINDOW_LO/WINDOW_HI
# values below by name, so a future scheduler change that isn't mirrored
# here fails that test rather than silently drifting.
STOCK_SUBMISSION_WINDOW_LO=1415
STOCK_SUBMISSION_WINDOW_HI=1445

EOD_WINDOW_LO=2100
EOD_WINDOW_HI=2200

WEEKLY_SUMMARY_WINDOW_LO=0745
WEEKLY_SUMMARY_WINDOW_HI=0915

# market_hours_check (:00) / account_state_refresh (:05) — expressed as
# minute-of-hour, wrapping across the hour boundary (>= LO or <= HI).
HOURLY_WINDOW_LO=55
HOURLY_WINDOW_HI=10

# Test-only overrides so this logic can be driven deterministically from
# pytest without depending on wall-clock time or sleeping for real minutes
# (see tests/test_deploy_server_side_blackout.py). Never set these in real
# operation — if unset, the real clock (`date -u`) is used.
_now_hm() { if [ -n "${DEPLOY_TEST_NOW_HM:-}" ]; then echo "$DEPLOY_TEST_NOW_HM"; else date -u +%H%M; fi; }
_now_min() { if [ -n "${DEPLOY_TEST_NOW_MIN:-}" ]; then echo "$DEPLOY_TEST_NOW_MIN"; else date -u +%M; fi; }
_now_dow() { if [ -n "${DEPLOY_TEST_NOW_DOW:-}" ]; then echo "$DEPLOY_TEST_NOW_DOW"; else date -u +%u; fi; }  # ISO: 1=Mon...7=Sun

in_range() {
  # in_range VALUE START END -> 0 if START <= VALUE <= END
  [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

is_weekday() {
  local dow; dow="$(_now_dow)"
  [ "$dow" -ge 1 ] && [ "$dow" -le 5 ]
}

# Sets the global `blackout_label` var (empty string if no window is
# active). Re-invokes the clock (or its test override) each call, so a
# caller polling this in a loop genuinely rechecks, not just once.
compute_blackout_label() {
  local now_hm now_min now_dow
  now_hm="$(_now_hm)"; now_min="$(_now_min)"; now_dow="$(_now_dow)"
  blackout_label=""
  if is_weekday && in_range "$now_hm" "$STOCK_SUBMISSION_WINDOW_LO" "$STOCK_SUBMISSION_WINDOW_HI"; then
    blackout_label="Stock order submission job runs at 14:30 UTC weekdays"
  elif is_weekday && in_range "$now_hm" "$EOD_WINDOW_LO" "$EOD_WINDOW_HI"; then
    blackout_label="Membership refresh (21:15 UTC) and the EOD job (21:30 UTC) run in this window on weekdays — EOD is the one that places T212 orders"
  elif [ "$now_dow" = "7" ] && in_range "$now_hm" "$WEEKLY_SUMMARY_WINDOW_LO" "$WEEKLY_SUMMARY_WINDOW_HI"; then
    blackout_label="Sunday weekly summary job runs at 09:00 Europe/London (08:00-09:00 UTC depending on BST/GMT)"
  elif [ "$now_min" -ge "$HOURLY_WINDOW_LO" ] || [ "$now_min" -le "$HOURLY_WINDOW_HI" ]; then
    blackout_label="market_hours_check (:00) / account_state_refresh (:05) run every hour, every day — both make real T212 API calls (position poll/exit-check, account reconciliation/snapshot)"
  fi
}
# --- End blackout window check definitions ----------------------------------

# --- The one real deploy sequence -------------------------------------------
# Runs directly on the VM. Called either by webhook_listener.py (the normal
# path) or manually over SSH (the break-glass fallback). No other file in
# this repo should contain any of these steps.
deploy_steps() {
  cd "$APP_DIR"

  echo "  Pulling latest code..."
  # Discard any local edits to the service templates — the real installed
  # files live in /etc/systemd/system/, not in the repo directory.
  (git checkout -- investment-research.service investment-research-scheduler.service 2>/dev/null || true)
  git pull origin main

  echo "  Recording deployed commit hash..."
  # Written here, right after the pull, rather than left for the app to
  # derive from `git log`/`git rev-parse` at its own runtime -- this file
  # stays correct as "what was actually deployed" even if someone later runs
  # a manual `git pull` on the server without redeploying. Read by the
  # Status page (main.py:_deployed_commit_hash()) to show and link the
  # currently-running commit. Same "capture real state at deploy time"
  # discipline verify_deploy.sh already uses to check git state, just
  # persisted for the app to read rather than only used inline for
  # verification.
  git rev-parse HEAD > "${APP_DIR}/.deployed_commit"

  echo "  Installing any new Python packages..."
  python3.11 -m pip install -r requirements.txt --quiet
  # TA-Lib (Linux) is already installed; re-running is a no-op
  (python3.11 -m pip install TA-Lib --quiet 2>/dev/null || true)

  echo "  Updating service files..."
  cp "${APP_DIR}/investment-research.service" "${SYSTEMD_DIR}/${SERVICE}.service"
  cp "${APP_DIR}/investment-research-scheduler.service" "${SYSTEMD_DIR}/${SERVICE}-scheduler.service"
  if [ -f "${APP_DIR}/investment-research-webhook.service" ]; then
    cp "${APP_DIR}/investment-research-webhook.service" "${SYSTEMD_DIR}/${SERVICE}-webhook.service"
  fi
  # Ensure the secrets symlinks exist (non-fatal if secrets.conf not yet created)
  mkdir -p "${SYSTEMD_DIR}/${SERVICE}-scheduler.service.d"
  (ln -sf \
      "${SYSTEMD_DIR}/${SERVICE}.service.d/secrets.conf" \
      "${SYSTEMD_DIR}/${SERVICE}-scheduler.service.d/secrets.conf" \
      2>/dev/null || true)
  if [ -f "${APP_DIR}/investment-research-webhook.service" ]; then
    mkdir -p "${SYSTEMD_DIR}/${SERVICE}-webhook.service.d"
    (ln -sf \
        "${SYSTEMD_DIR}/${SERVICE}.service.d/secrets.conf" \
        "${SYSTEMD_DIR}/${SERVICE}-webhook.service.d/secrets.conf" \
        2>/dev/null || true)
  fi
  systemctl daemon-reload

  echo "  Restarting services..."
  systemctl restart "${SERVICE}"
  (systemctl enable "${SERVICE}-scheduler" 2>/dev/null || true)
  systemctl restart "${SERVICE}-scheduler"
  if [ -f "${SYSTEMD_DIR}/${SERVICE}-webhook.service" ]; then
    (systemctl enable "${SERVICE}-webhook" 2>/dev/null || true)
    systemctl restart "${SERVICE}-webhook"
  fi

  echo "  Service status:"
  systemctl status "${SERVICE}" --no-pager -l
  systemctl status "${SERVICE}-scheduler" --no-pager -l

  echo "  Verifying deploy..."
  bash "${APP_DIR}/verify_deploy.sh"
}
# --- End deploy sequence -----------------------------------------------------

# No human is necessarily present (webhook_listener.py runs this
# unattended), so instead of blocking or silently ignoring a blackout
# window, defer: poll until the window clears, up to a bounded cap, then
# proceed regardless so a real push is never lost or silently dropped
# forever. See CLAUDE.md's "GitHub webhook deploy — blackout window
# handling" for the reasoning. DEPLOY_SKIP_BLACKOUT_CHECK=1 remains
# available for a deliberate manual override (e.g. a genuinely urgent
# break-glass deploy) but nothing sets it automatically any more.
if [ "${DEPLOY_SKIP_BLACKOUT_CHECK:-0}" != "1" ]; then
  # MUST be comfortably longer than the WIDEST single blackout window
  # below -- otherwise a deploy that starts deferring near the *start* of
  # a window hits this cap and forces through while genuinely still
  # inside that same window, defeating the whole "wait for the window to
  # clear" point of deferring at all. That widest window is
  # WEEKLY_SUMMARY_WINDOW (0745-0915, 90 minutes / 5400s -- wider than
  # EOD's own 60-minute window; it's sized for Europe/London BST/GMT
  # ambiguity, not job runtime, but the cap still has to cover it).
  #
  # Confirmed as a REAL bug, not theoretical (2026-08-05): the old 1800s
  # default was shorter than EVERY window here except the tiny hourly
  # one. A deploy that began deferring at 21:23 UTC hit that 1800s cap at
  # 21:53 UTC and force-restarted investment-research-scheduler -- while
  # that night's 21:30 UTC EOD job was still genuinely mid-run (data pull
  # alone for the current 506-ticker live universe measured at ~23
  # minutes end to end that same week, 21:30:10-21:53:08 on a clean
  # uninterrupted run, 2026-08-03 -- plus further time for the
  # decide/candidate-queuing pipeline and the T212 snapshot afterward,
  # itself already close to the EOD window's own 60-minute width). The
  # restart killed the in-flight job before it reached
  # pull_incremental()'s own last_full_refresh stamp, silently leaving
  # price_data mixed-freshness (most tickers current, a handful still on
  # the prior close) and last_full_refresh never updated -- the Status
  # page kept showing EOD as OVERDUE even after the startup missed-EOD
  # catch-up gate (_check_missed_eod_run()) had already run a real
  # candidate-queuing pass against that incomplete data. This was a fresh
  # regression, not a longstanding gap: the EOD live universe grew from
  # ~112 tickers to the full 506-ticker S&P 500 set on 2026-08-02 (see
  # CLAUDE.md's "V2 stock universe broadening"), which is what pushed
  # EOD's real runtime close enough to the EOD window's own width for
  # this to bite for the first time -- the 1800s cap was never
  # re-checked against ANY window's width, EOD's or the wider
  # weekly-summary one.
  #
  # 6000s (100 minutes) is WEEKLY_SUMMARY_WINDOW's own 5400s width plus a
  # 10-minute margin -- comfortably enough that a deploy deferring from
  # the very start of ANY window here will see that window clear
  # naturally before ever reaching this cap, for as long as the window
  # widths in this file stay what they are today.
  # tests/test_deploy_server_side_blackout.py's
  # test_blackout_max_wait_default_covers_widest_window statically guards
  # this against the same drift.
  MAX_WAIT_SECONDS="${DEPLOY_MAX_BLACKOUT_WAIT_SECONDS:-6000}"
  POLL_INTERVAL_SECONDS="${DEPLOY_BLACKOUT_POLL_INTERVAL_SECONDS:-60}"
  waited=0
  compute_blackout_label
  while [ -n "$blackout_label" ] && [ "$waited" -lt "$MAX_WAIT_SECONDS" ]; do
    echo "[$(date -u +%FT%TZ)] Deploy deferred — ${blackout_label}. Rechecking in ${POLL_INTERVAL_SECONDS}s (waited ${waited}s of ${MAX_WAIT_SECONDS}s cap)."
    sleep "$POLL_INTERVAL_SECONDS"
    waited=$((waited + POLL_INTERVAL_SECONDS))
    compute_blackout_label
  done
  if [ -n "$blackout_label" ]; then
    echo "[$(date -u +%FT%TZ)] Blackout window still active after the ${MAX_WAIT_SECONDS}s cap — proceeding anyway (forward-progress guarantee: a push must never be silently lost)."
  fi
fi

deploy_steps
echo ""
echo "[$(date -u +%FT%TZ)] Deploy complete."
