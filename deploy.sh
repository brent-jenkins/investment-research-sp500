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
