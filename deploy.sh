#!/usr/bin/env bash
# deploy.sh — Run from Git Bash on Windows to push code and restart the VM.
#
# Prerequisites:
#   - SSH key added to the VM (ssh-copy-id root@YOUR_SERVER_IP)
#   - Git remote 'origin' pointing to GitHub
#
# Usage:
#   bash deploy.sh
#
# Set your server IP here (or export VM_IP=x.x.x.x before running)
VM_IP="${VM_IP:-YOUR_SERVER_IP}"
APP_DIR="/root/investment-research"
SERVICE="investment-research"

set -euo pipefail

# Validate configuration
if [ "$VM_IP" = "YOUR_SERVER_IP" ]; then
    echo "ERROR: Set your server IP."
    echo "  Either edit VM_IP in deploy.sh, or run:"
    echo "    VM_IP=1.2.3.4 bash deploy.sh"
    exit 1
fi

echo ""
echo "=== [1/3] Pushing latest code to GitHub ==="
git push origin main

echo ""
echo "=== [2/3] Pulling code on VM ($VM_IP) ==="
ssh "root@${VM_IP}" bash -s << REMOTE
set -e
cd ${APP_DIR}

echo "  Pulling latest code..."
git pull origin main

echo "  Installing any new Python packages..."
source venv/bin/activate
pip install -r requirements.txt --quiet
# TA-Lib (Linux) is already installed; re-running is a no-op
pip install TA-Lib --quiet 2>/dev/null || true
deactivate

echo "  Restarting service..."
systemctl restart ${SERVICE}

echo "  Service status:"
systemctl status ${SERVICE} --no-pager -l
REMOTE

echo ""
echo "=== [3/3] Done ==="
echo "    Dashboard: https://$(ssh root@${VM_IP} 'grep -oP "(?<=^)[^ {]+" /etc/caddy/Caddyfile | head -1' 2>/dev/null || echo '<your-domain>')"
echo ""
