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
# XSRF protection can interfere with Caddy's reverse proxy; disable here
# and rely on Caddy's TLS + basicauth for security.
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
echo "  *** ACTION REQUIRED — set your dashboard password: ***"
echo ""
echo "    1. Generate a bcrypt hash:"
echo "         caddy hash-password"
echo "       (enter your chosen password at the prompt)"
echo ""
echo "    2. Edit /etc/caddy/Caddyfile and replace:"
echo "         REPLACE_WITH_HASH  →  the hash output from step 1"
echo "         admin              →  your chosen username (if desired)"
echo ""
echo "    3. Reload Caddy:  systemctl reload caddy"
echo ""

# ── 7. Systemd service ────────────────────────────────────────────────────────

echo ">>> [7/7] Installing and starting systemd service..."
cp "$APP_DIR/investment-research.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable investment-research
systemctl start investment-research
echo "    investment-research service enabled and started."

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "  Setup complete!"
echo ""
echo "  Service status:"
systemctl status investment-research --no-pager -l || true
echo ""
echo "  Useful commands:"
echo "    journalctl -u investment-research -f    # live logs"
echo "    systemctl restart investment-research   # restart app"
echo "    systemctl reload caddy                  # reload Caddy config"
echo "============================================================"
echo ""
