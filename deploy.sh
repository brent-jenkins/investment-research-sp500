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
# Re-verified against data/scheduler.py:start_scheduler()'s actual
# CronTrigger registrations 2026-08-02 (not assumed from an old comment) --
# the 07:00 and 13:30 windows previously named jobs removed 2026-07-31
# ("pre-market analysis" / "US open analysis" -- see CLAUDE.md's
# "Pre-market/US-open scheduled runs removed"). Both time slots are still
# genuinely real order-placing windows today, just for different, newer
# jobs (etf_order_submission / stock_order_submission, added the same day
# under "Exchange-aware order submission") -- the risk these windows guard
# against never went away, only the job names changed. The Sunday window
# was also stale for a second, independent reason: weekly_summary now
# fires at 09:00 *Europe/London* (data/scheduler.py, "genuinely UK local
# time"), not a fixed 10:00 UTC -- that's 08:00 UTC in BST (late Mar-late
# Oct) or 09:00 UTC in GMT. Rather than compute DST at runtime in bash
# (this environment's tzdata for Europe/London was found to not apply BST
# correctly when checked directly, so a bash `TZ=` conversion can't be
# trusted here), the window is simply widened to statically cover both
# possible UTC times with a buffer -- a slightly wider "are you sure?"
# net twice a year around the DST boundary, never a missed one.
#
# A second, independent bug found while re-verifying (not present before
# either, not something this fix introduced): none of the three weekday
# windows below ever checked day-of-week at all, so they fired on
# Saturday/Sunday too -- every job they guard (etf_order_submission,
# stock_order_submission, membership_refresh, scheduled_eod) is Mon-Fri
# only, so a weekend "are you sure?" prompt for any of them was always a
# false positive. Confirmed directly by dry-running the range logic
# against injected times/weekdays before trusting this fix, not assumed.
now_hm="$(date -u +%H%M)"
now_dow="$(date -u +%u)"  # ISO day of week: 1=Monday ... 7=Sunday
blackout_label=""

in_range() {
  # in_range HHMM START END -> 0 if START <= HHMM <= END
  [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

is_weekday() {
  # Mon-Fri only -- every job these three windows guard is Mon-Fri; only
  # the Sunday weekly-summary window (below) should ever match a weekend.
  [ "$now_dow" -ge 1 ] && [ "$now_dow" -le 5 ]
}

if is_weekday && in_range "$now_hm" 0645 0715; then
  blackout_label="Monday pre-market job runs at 07:00 UTC on Mondays (cancels stale weekend orders, then re-runs order logic) -- the ETF order submission job that used to also run in this window was removed 2026-08-02 alongside the rest of the ETF sleeve, see CLAUDE.md's \"ETF sleeve removed from live trading\""
elif is_weekday && in_range "$now_hm" 1315 1400; then
  blackout_label="Stock order submission job runs at 13:30 UTC weekdays"
elif is_weekday && in_range "$now_hm" 2100 2200; then
  blackout_label="Membership refresh (21:15 UTC) and the EOD job (21:30 UTC) run in this window on weekdays — EOD is the one that places T212 orders"
elif [ "$now_dow" = "7" ] && in_range "$now_hm" 0745 0915; then
  blackout_label="Sunday weekly summary job runs at 09:00 Europe/London (08:00-09:00 UTC depending on BST/GMT)"
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
