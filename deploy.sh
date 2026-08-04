#!/usr/bin/env bash
# deploy.sh — Run from Git Bash on Windows to push code and restart the VM.
#
# Prerequisites:
#   - SSH key at ssh-keys/deploy_key (relative to project root)
#   - Git remote 'origin' pointing to GitHub
#
# Usage:
#   bash deploy.sh
#
# Override the server IP at runtime if needed: VM_IP=x.x.x.x bash deploy.sh
VM_IP="${VM_IP:-203.0.113.10}"
APP_DIR="/root/investment-research"
SERVICE="investment-research"
SSH_KEY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ssh-keys/deploy_key"

set -euo pipefail

# --- Deployment blackout window check --------------------------------------
# Scheduled jobs (data pull + analysis + order placement) run at fixed UTC
# times — see "Deployment timing" in CLAUDE.md. Deploying inside one of these
# windows risks the job executing against half-updated code or config
# (partially applied git pull, service mid-restart) and can place broker
# orders on that transitional state. Warn and require explicit confirmation
# rather than blocking outright, since some deploys (e.g. urgent hotfixes)
# legitimately need to go out regardless.
#
# Re-verified against data/scheduler.py:start_scheduler()'s actual, current
# CronTrigger registrations 2026-08-04 -- not assumed from an old comment
# (the previous "re-verified 2026-08-02" pass had itself already gone stale
# twice over by this date: stock_order_submission moved 13:30 -> 14:30 UTC
# the same day it was last checked, and the Monday-only pre-market job was
# removed entirely 2026-08-03 -- see CLAUDE.md's "Monday Pre-Market removed
# entirely" and "Stock submission delayed past NYSE open" sections). This is
# the same class of drift this project has hit more than once tonight: a
# hand-maintained snapshot silently going wrong after the thing it tracked
# changed underneath it. Rather than re-encode a fresh snapshot and hope it
# stays current, `tests/test_deploy_blackout_windows.py::
# test_blackout_windows_match_real_scheduler_registrations` starts the REAL
# scheduler (data/scheduler.py:start_scheduler(), the exact same function
# that registers every job this actually runs) and cross-checks its live
# trigger fields against the WINDOW_LO/WINDOW_HI values below by name --
# any future scheduler change that isn't mirrored here fails that test, not
# silently drifts. Update the values below (and their PowerShell twins in
# deploy.ps1) together with that test whenever a job's schedule changes.
#
# A genuinely new finding from this pass, not present in the previous
# design at all: market_hours_check (CronTrigger(minute=0), no hour/day
# filter) and account_state_refresh (CronTrigger(minute=5), no hour/day
# filter) both fire EVERY HOUR, every day, unconditionally (split from the
# old single hourly _trade_alert_job() 2026-08-03 -- see CLAUDE.md's
# "Splitting the hourly trade-alert job") -- neither was covered by any
# window before. account_state_refresh in particular has no internal gate
# at all (unlike every other job here, which no-ops on a non-trading
# day/closed session) -- it does real work (position reconciliation, a
# T212 account snapshot fetch) on every single firing, so its own window
# below is deliberately NOT weekday-restricted, unlike the once-daily
# windows. Given both recur hourly, a wide buffer (matching the ~30-60min
# buffers used for the once-daily jobs below) would make this warning fire
# for roughly half of every day -- deliberately tight instead (+/-5/10min
# around the two fire minutes), reflecting that the actual at-risk period
# is however long this script's own git-pull-plus-restart sequence takes
# (observed well under two minutes end to end), not a generous margin
# around a rare, high-stakes event the way the once-daily windows are.
#
# The remaining three windows below were already correctly weekday-gated
# (a real, independent bug found and fixed 2026-08-02, unrelated to this
# pass) and their UTC bounds are unchanged from that fix except where noted
# -- only stale window content and the missing hourly coverage needed
# correcting this time.
now_hm="$(date -u +%H%M)"
now_min="$(date -u +%M)"
now_dow="$(date -u +%u)"  # ISO day of week: 1=Monday ... 7=Sunday
blackout_label=""

in_range() {
  # in_range VALUE START END -> 0 if START <= VALUE <= END
  [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

is_weekday() {
  # Mon-Fri only -- an approximation of "NYSE is open" (every window below
  # except the hourly one and Sunday's guards a job that's a genuine no-op
  # outside NYSE trading days) -- bash has no market-calendar library, so
  # this can't distinguish a weekday NYSE holiday (e.g. Thanksgiving) from
  # a genuine trading day; see CLAUDE.md's "Market-calendar-aware
  # scheduling" section for the real, Python-side NYSE check this
  # approximates. A false-positive warning on a market holiday is a minor
  # inconvenience (just confirm and proceed); a missed warning on a
  # genuine trading day is the risk actually worth guarding against, so
  # this approximation errs toward the safer side.
  [ "$now_dow" -ge 1 ] && [ "$now_dow" -le 5 ]
}

# stock_order_submission: CronTrigger(hour=14, minute=30) -- moved from
# 13:30 2026-08-02 (see CLAUDE.md's "Stock submission delayed past NYSE
# open" section). WINDOW_LO/HI named for the drift-detection test.
STOCK_SUBMISSION_WINDOW_LO=1415
STOCK_SUBMISSION_WINDOW_HI=1445

# membership_refresh (21:15) + scheduled_eod (21:30, config.yaml
# scheduler.runs.eod) -- both still exactly where the 2026-08-02 pass left
# them; window bounds unchanged.
EOD_WINDOW_LO=2100
EOD_WINDOW_HI=2200

# weekly_summary: Sunday 09:00 Europe/London = 08:00 UTC (BST) or 09:00 UTC
# (GMT) -- unchanged from the 2026-08-02 pass; still correctly covers both.
WEEKLY_SUMMARY_WINDOW_LO=0745
WEEKLY_SUMMARY_WINDOW_HI=0915

# market_hours_check (:00) / account_state_refresh (:05) -- genuinely new
# coverage this pass, see the comment block above. Expressed as
# minute-of-hour, wrapping across the hour boundary (>= LO or <= HI).
HOURLY_WINDOW_LO=55
HOURLY_WINDOW_HI=10

if is_weekday && in_range "$now_hm" "$STOCK_SUBMISSION_WINDOW_LO" "$STOCK_SUBMISSION_WINDOW_HI"; then
  blackout_label="Stock order submission job runs at 14:30 UTC weekdays"
elif is_weekday && in_range "$now_hm" "$EOD_WINDOW_LO" "$EOD_WINDOW_HI"; then
  blackout_label="Membership refresh (21:15 UTC) and the EOD job (21:30 UTC) run in this window on weekdays — EOD is the one that places T212 orders"
elif [ "$now_dow" = "7" ] && in_range "$now_hm" "$WEEKLY_SUMMARY_WINDOW_LO" "$WEEKLY_SUMMARY_WINDOW_HI"; then
  blackout_label="Sunday weekly summary job runs at 09:00 Europe/London (08:00-09:00 UTC depending on BST/GMT)"
elif [ "$now_min" -ge "$HOURLY_WINDOW_LO" ] || [ "$now_min" -le "$HOURLY_WINDOW_HI" ]; then
  blackout_label="market_hours_check (:00) / account_state_refresh (:05) run every hour, every day — both make real T212 API calls (position poll/exit-check, account reconciliation/snapshot)"
fi

if [ -n "$blackout_label" ]; then
  echo ""
  echo "⚠️  WARNING: ${blackout_label} — deploying now risks order logic running on transitional state."
  echo "    Note: deploy restarts BOTH investment-research and investment-research-scheduler."
  read -r -p "Continue anyway? (y/N): " confirm
  case "$confirm" in
    y|Y) ;;
    *)
      echo "Deployment aborted."
      exit 1
      ;;
  esac
fi
# --- End blackout window check ----------------------------------------------

echo ""
echo "=== [1/3] Pushing latest code to GitHub ==="
git push origin main

echo ""
echo "=== [2/3] Pulling code on VM ($VM_IP) ==="
ssh -i "$SSH_KEY" "root@${VM_IP}" bash -s << REMOTE
set -e
cd ${APP_DIR}

echo "  Pulling latest code..."
# Discard any local edits to the service templates — the real installed files
# live in /etc/systemd/system/, not in the repo directory.
git checkout -- investment-research.service investment-research-scheduler.service 2>/dev/null || true
git pull origin main

echo "  Installing any new Python packages..."
python3.11 -m pip install -r requirements.txt --quiet
# TA-Lib (Linux) is already installed; re-running is a no-op
python3.11 -m pip install TA-Lib --quiet 2>/dev/null || true

echo "  Updating service files..."
cp ${APP_DIR}/investment-research.service /etc/systemd/system/${SERVICE}.service
cp ${APP_DIR}/investment-research-scheduler.service /etc/systemd/system/${SERVICE}-scheduler.service
# Ensure the secrets symlink exists (non-fatal if secrets.conf not yet created)
mkdir -p /etc/systemd/system/${SERVICE}-scheduler.service.d
ln -sf \
    /etc/systemd/system/${SERVICE}.service.d/secrets.conf \
    /etc/systemd/system/${SERVICE}-scheduler.service.d/secrets.conf \
    2>/dev/null || true
systemctl daemon-reload

echo "  Restarting services..."
systemctl restart ${SERVICE}
systemctl enable ${SERVICE}-scheduler 2>/dev/null || true
systemctl restart ${SERVICE}-scheduler

echo "  Service status:"
systemctl status ${SERVICE} --no-pager -l
systemctl status ${SERVICE}-scheduler --no-pager -l
REMOTE

echo ""
echo "=== [3/3] Done ==="
echo "    Dashboard: https://$(ssh -i "$SSH_KEY" root@${VM_IP} 'grep -oP "(?<=^)[^ {]+" /etc/caddy/Caddyfile | head -1' 2>/dev/null || echo '<your-domain>')"
echo ""
