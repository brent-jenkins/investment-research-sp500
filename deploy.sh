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
# Discard any local edits to the service template — the real installed file
# lives in /etc/systemd/system/, not in the repo directory.
git checkout -- investment-research.service 2>/dev/null || true
git pull origin main

echo "  Installing any new Python packages..."
python3.11 -m pip install -r requirements.txt --quiet
# TA-Lib (Linux) is already installed; re-running is a no-op
python3.11 -m pip install TA-Lib --quiet 2>/dev/null || true

echo "  Updating service file..."
cp ${APP_DIR}/investment-research.service /etc/systemd/system/${SERVICE}.service
systemctl daemon-reload

echo "  Restarting service..."
systemctl restart ${SERVICE}

echo "  Service status:"
systemctl status ${SERVICE} --no-pager -l
REMOTE

echo ""
echo "=== [3/3] Done ==="
echo "    Dashboard: https://$(ssh -i "$SSH_KEY" root@${VM_IP} 'grep -oP "(?<=^)[^ {]+" /etc/caddy/Caddyfile | head -1' 2>/dev/null || echo '<your-domain>')"
echo ""
