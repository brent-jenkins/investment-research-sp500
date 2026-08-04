#!/usr/bin/env bash
# setup.sh — Run once on a fresh Ubuntu 22.04 VM as root.
#
# Usage:
#   git clone https://github.com/YOUR/investment-research.git /root/investment-research
#   cd /root/investment-research
#   bash setup.sh research.yourdomain.com
#
# What it does:
#   1. Updates apt and installs system dependencies + Python 3.11
#   2. Compiles the TA-Lib C library from source (Linux native)
#   3. Installs Caddy web server via the official apt repo
#   4. Installs all Python packages system-wide via python3.11 -m pip
#   5. Creates .streamlit/config.toml for headless server mode
#   6. Copies Caddyfile to /etc/caddy/Caddyfile
#   7. Installs and starts the investment-research systemd service

set -euo pipefail

DOMAIN="${1:?Usage: bash setup.sh <your.domain.com>}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================================"
echo "  Investment Research — Server Setup"
echo "  Domain : $DOMAIN"
echo "  App dir: $APP_DIR"
echo "============================================================"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────

echo ">>> [1/7] Updating system packages..."
apt-get update -y
apt-get upgrade -y

apt-get install -y \
    build-essential \
    wget \
    git \
    curl \
    python3.11 \
    python3.11-dev \
    software-properties-common \
    debian-keyring \
    debian-archive-keyring \
    apt-transport-https

# Bootstrap pip for Python 3.11 (not bundled on Ubuntu 22.04)
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# ── 2. TA-Lib C library ───────────────────────────────────────────────────────

echo ""
echo ">>> [2/7] Building TA-Lib C library from source..."
cd /tmp
wget -q "https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz"
tar -xf ta-lib-0.4.0-src.tar.gz
cd ta-lib
./configure --prefix=/usr
make -j"$(nproc)"
make install
ldconfig          # update shared-library cache so Python wrapper can find litta_lib.so
cd /tmp
rm -rf ta-lib ta-lib-0.4.0-src.tar.gz
echo "    TA-Lib C library installed."

# ── 3. Caddy ──────────────────────────────────────────────────────────────────

echo ""
echo ">>> [3/7] Installing Caddy..."
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -y
apt-get install -y caddy
echo "    Caddy installed: $(caddy version)"

# ── 4. Python packages ────────────────────────────────────────────────────────

echo ""
echo ">>> [4/7] Installing Python packages system-wide..."
cd "$APP_DIR"

python3.11 -m pip install --upgrade pip wheel

# Install everything in requirements.txt (TA-Lib-Precompiled line is
# commented out on all platforms; we add the Linux TA-Lib wrapper below)
python3.11 -m pip install -r requirements.txt

# Linux TA-Lib Python wrapper — links against the C library compiled above
python3.11 -m pip install TA-Lib

echo "    Python packages installed."

# ── 5. App directories & Streamlit config ─────────────────────────────────────

echo ""
echo ">>> [5/7] Creating app directories and Streamlit config..."

mkdir -p "$APP_DIR/database"
chmod 750 "$APP_DIR/database"

mkdir -p "$APP_DIR/.streamlit"
cat > "$APP_DIR/.streamlit/config.toml" << TOML
[server]
# Only listen on loopback — Caddy handles the public-facing HTTPS port.
headless = true
port = 8501
address = "127.0.0.1"
enableCORS = false
# XSRF protection can interfere with Caddy's reverse proxy; disable here.
# Access control is enforced by the app's own per-user login (auth.py),
# not by Caddy — see the ADMIN_PASSWORD_HASH note below.
enableXsrfProtection = false

[browser]
gatherUsageStats = false
# Tell Streamlit the external URL so generated share-links are correct.
serverAddress = "$DOMAIN"
serverPort = 443

[client]
# "minimal" hides the Deploy button and reduces toolbar clutter.
toolbarMode = "minimal"
TOML
echo "    .streamlit/config.toml written."

# ── 6. Caddy config ───────────────────────────────────────────────────────────

echo ""
echo ">>> [6/7] Installing Caddyfile to /etc/caddy/Caddyfile..."
cp "$APP_DIR/Caddyfile" /etc/caddy/Caddyfile
# Substitute the domain placeholder
sed -i "s/YOUR\.DOMAIN\.COM/$DOMAIN/g" /etc/caddy/Caddyfile
echo "    /etc/caddy/Caddyfile written."
echo ""
echo "  *** ACTION REQUIRED — set your first admin account: ***"
echo ""
echo "    Login is handled by the app itself (per-user accounts, see"
echo "    CLAUDE.md's 'Authentication' section), not by Caddy. On first"
echo "    startup with an empty users table, an 'admin' account is"
echo "    created automatically:"
echo ""
echo "      - If ADMIN_PASSWORD_HASH is set in the systemd secrets"
echo "        drop-in (a bcrypt hash — generate one with"
echo "        'caddy hash-password' or 'python3 -c \"import bcrypt;"
echo "        print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt())."
echo "        decode())\"'), that becomes the admin password."
echo "      - Otherwise a random password is generated and logged ONCE"
echo "        — check the investment-research service log after first"
echo "        startup and change it immediately via the Users page."
echo ""

# ── 7. Systemd services ───────────────────────────────────────────────────────

echo ">>> [7/7] Installing and starting systemd services..."

# Main Streamlit web UI
cp "$APP_DIR/investment-research.service" /etc/systemd/system/

# Standalone scheduler daemon (fires jobs even when nobody visits the dashboard)
cp "$APP_DIR/investment-research-scheduler.service" /etc/systemd/system/

# GitHub webhook deploy listener (verifies a signed push webhook, then runs
# `bash deploy.sh --server-side` — see CLAUDE.md's "GitHub webhook deploy")
cp "$APP_DIR/investment-research-webhook.service" /etc/systemd/system/

# Symlink the secrets drop-in so all three services share the same credentials
# file. Users only need to create one drop-in
# (investment-research.service.d/secrets.conf) and the scheduler daemon and
# webhook listener automatically inherit the same env vars.
mkdir -p /etc/systemd/system/investment-research-scheduler.service.d
ln -sf \
    /etc/systemd/system/investment-research.service.d/secrets.conf \
    /etc/systemd/system/investment-research-scheduler.service.d/secrets.conf \
    2>/dev/null || true   # non-fatal if secrets.conf doesn't exist yet

mkdir -p /etc/systemd/system/investment-research-webhook.service.d
ln -sf \
    /etc/systemd/system/investment-research.service.d/secrets.conf \
    /etc/systemd/system/investment-research-webhook.service.d/secrets.conf \
    2>/dev/null || true   # non-fatal if secrets.conf doesn't exist yet

systemctl daemon-reload

systemctl enable investment-research
systemctl start investment-research
echo "    investment-research service enabled and started."

systemctl enable investment-research-scheduler
systemctl start investment-research-scheduler
echo "    investment-research-scheduler service enabled and started."

systemctl enable investment-research-webhook
systemctl start investment-research-webhook
echo "    investment-research-webhook service enabled and started."
echo ""
echo "  *** ACTION REQUIRED — set GITHUB_WEBHOOK_SECRET: ***"
echo ""
echo "    Generate a random secret (openssl rand -hex 32), add it as"
echo "    GITHUB_WEBHOOK_SECRET=<secret> to the systemd secrets drop-in"
echo "    (/etc/systemd/system/investment-research.service.d/secrets.conf),"
echo "    run 'systemctl daemon-reload && systemctl restart"
echo "    investment-research-webhook', then configure a webhook in the"
echo "    GitHub repo (Settings -> Webhooks -> Add webhook) pointing at"
echo "    https://$DOMAIN/webhook/deploy with the SAME secret, content type"
echo "    application/json, and the 'push' event only."
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "  Setup complete!"
echo ""
echo "  Service status:"
systemctl status investment-research --no-pager -l || true
echo ""
systemctl status investment-research-scheduler --no-pager -l || true
echo ""
echo "  Useful commands:"
echo "    journalctl -u investment-research -f            # web UI live logs"
echo "    journalctl -u investment-research-scheduler -f  # scheduler live logs"
echo "    systemctl restart investment-research           # restart web UI"
echo "    systemctl restart investment-research-scheduler # restart scheduler"
echo "    systemctl reload caddy                          # reload Caddy config"
echo "============================================================"
echo ""
