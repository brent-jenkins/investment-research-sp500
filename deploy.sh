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
now_hm="$(date -u +%H%M)"
now_dow="$(date -u +%u)"  # ISO day of week: 1=Monday ... 7=Sunday
blackout_label=""

in_range() {
  # in_range HHMM START END -> 0 if START <= HHMM <= END
  [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

if in_range "$now_hm" 0645 0715; then
  blackout_label="pre-market analysis job runs at 07:00 UTC"
elif in_range "$now_hm" 1315 1400; then
  blackout_label="US open analysis job runs at 13:30 UTC"
elif in_range "$now_hm" 2100 2200; then
  blackout_label="EOD job runs at 21:30 UTC"
elif [ "$now_dow" = "7" ] && in_range "$now_hm" 0945 1015; then
  blackout_label="Sunday weekly summary job runs at 10:00 UTC"
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
