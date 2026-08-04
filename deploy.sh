#!/usr/bin/env bash
# deploy.sh — the ONE canonical implementation of "deploy this app". Every
# deploy path in this repo ends up calling this same script, in one of two
# modes, rather than each path hand-rolling its own copy of the git-pull /
# pip-install / systemd-restart sequence:
#
#   1. Local/dev mode (`bash deploy.sh`, from Git Bash on Windows or any
#      POSIX shell) — for a human running a manual deploy. Prompts on a
#      blackout window, pushes to GitHub, then SSHes to the VM and invokes
#      THIS SAME SCRIPT there in server-side mode (see below). deploy.ps1
#      (PowerShell) does the equivalent for Windows users who prefer it,
#      and also just delegates to server-side mode over SSH — neither
#      wrapper reimplements the deploy steps themselves.
#   2. Server-side mode (`bash deploy.sh --server-side`) — runs ON the VM,
#      either invoked remotely by mode 1 above, or invoked locally by
#      webhook_listener.py in response to a verified GitHub push webhook
#      (see "GitHub webhook deploy" in CLAUDE.md). Does the actual git
#      pull / pip install / systemd restart / verify sequence, via the
#      single deploy_steps() function below — nowhere else in the repo
#      duplicates these steps.
#
# This replaced an earlier design (pre-2026-08-05) where deploy.sh, deploy.ps1,
# AND the git pre-push hook each carried their own independent copy of the
# same remote command chain — three hand-maintained implementations of
# "deploy this" that kept drifting apart (see CLAUDE.md's "Deploy pipeline
# systemd parity audit" and the migration to a GitHub webhook that replaced
# the pre-push hook's SSH deploy entirely). deploy_steps() below is now the
# only place any of that logic lives.
#
# Usage:
#   bash deploy.sh                  # local/dev mode — push + remote deploy
#   bash deploy.sh --server-side    # server-side mode — run the deploy
#                                    # steps directly, here, right now
#
# Override the server IP / app dir / service name at runtime if needed:
#   VM_IP=x.x.x.x APP_DIR=/root/other SERVICE=other-name bash deploy.sh
VM_IP="${VM_IP:-203.0.113.10}"
APP_DIR="${APP_DIR:-/root/investment-research}"
SERVICE="${SERVICE:-investment-research}"
SSH_KEY="${SSH_KEY:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ssh-keys/deploy_key}"
# Overridable so tests can point deploy_steps() at a scratch directory
# instead of the real /etc/systemd/system -- defaults to the real path in
# every actual deploy (local, remote, and server-side/webhook).
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

set -euo pipefail

SERVER_SIDE=0
if [ "${1:-}" = "--server-side" ]; then
  SERVER_SIDE=1
fi

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
# here fails that test rather than silently drifting. Update the values
# below (and their PowerShell twins in deploy.ps1) together with that test
# whenever a job's schedule changes.
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
# operation — if unset, the real clock (`date -u`) is used, exactly as
# before this override mechanism existed.
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
# Runs directly on the VM. Called either by this script's own server-side
# mode (see bottom of file) or, indirectly, by webhook_listener.py invoking
# `bash deploy.sh --server-side`. No other file in this repo should contain
# any of these steps.
deploy_steps() {
  cd "$APP_DIR"

  echo "  Pulling latest code..."
  # Discard any local edits to the service templates — the real installed
  # files live in /etc/systemd/system/, not in the repo directory.
  (git checkout -- investment-research.service investment-research-scheduler.service 2>/dev/null || true)
  git pull origin main

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

if [ "$SERVER_SIDE" = "1" ]; then
  # Invoked either over SSH by this same script's local/dev mode (a human
  # already confirmed past any blackout warning interactively — see below),
  # or directly by webhook_listener.py with no human present at all. In the
  # latter case there is nobody to prompt, so instead of blocking or
  # silently ignoring the blackout window, defer: poll until the window
  # clears, up to a bounded cap, then proceed regardless so a real push
  # is never lost or silently dropped forever. See CLAUDE.md's "GitHub
  # webhook deploy — blackout window handling" for the reasoning.
  if [ "${DEPLOY_SKIP_BLACKOUT_CHECK:-0}" != "1" ]; then
    MAX_WAIT_SECONDS="${DEPLOY_MAX_BLACKOUT_WAIT_SECONDS:-1800}"
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
  echo "[$(date -u +%FT%TZ)] Server-side deploy complete."
  exit 0
fi

# --- Local/dev mode: interactive blackout prompt, push, then delegate ------
compute_blackout_label
if [ -n "$blackout_label" ]; then
  echo ""
  echo "⚠️  WARNING: ${blackout_label} — deploying now risks order logic running on transitional state."
  echo "    Note: deploy restarts investment-research, investment-research-scheduler, and investment-research-webhook."
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
echo "=== [1/2] Pushing latest code to GitHub ==="
git push origin main

echo ""
echo "=== [2/2] Deploying on VM ($VM_IP) ==="
# A human already confirmed past the blackout warning above (if any) — skip
# the redundant non-interactive re-check server-side.
ssh -i "$SSH_KEY" "root@${VM_IP}" "cd ${APP_DIR} && DEPLOY_SKIP_BLACKOUT_CHECK=1 bash deploy.sh --server-side"

echo ""
echo "=== Done ==="
echo "    Dashboard: https://$(ssh -i "$SSH_KEY" root@${VM_IP} 'grep -oP "(?<=^)[^ {]+" /etc/caddy/Caddyfile | head -1' 2>/dev/null || echo '<your-domain>')"
echo ""
