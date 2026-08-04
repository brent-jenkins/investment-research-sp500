#!/usr/bin/env bash
# verify_deploy.sh — Post-deploy verification, run on the VM at the end of
# every deploy path (deploy.sh, deploy.ps1, and the pre-push hook's
# background deploy) so all three confirm the SAME real, current state
# rather than each trusting its own command chain's exit code alone.
#
# Why this exists (2026-08-04): two separate, independently-discovered gaps
# in the SAME category — the automated pre-push-hook deploy not copying
# updated .service files into /etc/systemd/system/ (found via the FRED
# credential cleanup), and that same path not running `systemctl
# daemon-reload` before restarting (found via a real incident during the
# per-user-accounts rollout, which briefly seeded a random admin password
# instead of the migrated one) — see CLAUDE_HISTORY.md's "Deploy pipeline
# systemd parity audit" for the full investigation. A command chain
# reporting exit code 0 is NOT sufficient evidence a deploy actually left
# the VM in the intended state: `systemctl restart` returning 0 confirms
# the restart request was accepted, not that the service is still running
# a few seconds later, and a `cp` succeeding says nothing about whether the
# file it copied is what the deploy actually intended to install. This
# script checks the real, current state directly instead of trusting any
# upstream command's own exit code as a proxy for it.
#
# Extended 2026-08-05 for the GitHub webhook deploy migration: the webhook
# listener (investment-research-webhook.service) is now one of the things
# every deploy installs and restarts (deploy.sh's deploy_steps()), so it
# gets the same real-state checks below as the two pre-existing services —
# same reasoning, same "don't just trust the command chain" discipline.
# Checked only if investment-research-webhook.service exists in APP_DIR, so
# this script still passes cleanly during the staged rollout window before
# the webhook service is actually installed on the VM.
#
# Usage: bash verify_deploy.sh [APP_DIR] [SERVICE]
#   Defaults match every existing deploy path's own values.
# Exit 0 if everything checks out, 1 otherwise — every individual check
# still runs and reports even after an earlier one fails, so a single
# invocation surfaces every problem found this run, not just the first.

APP_DIR="${1:-/root/investment-research}"
SERVICE="${2:-investment-research}"

ok=1

check() {
    # check LABEL CONDITION_CMD... -- runs the condition, reports PASS/FAIL,
    # and folds into the overall $ok without short-circuiting later checks.
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  [PASS] $label"
    else
        echo "  [FAIL] $label"
        ok=0
    fi
}

echo "=== Deploy verification ($(date -u +%FT%TZ)) ==="

check "investment-research service is active" \
    systemctl is-active --quiet "${SERVICE}"

check "investment-research-scheduler service is active" \
    systemctl is-active --quiet "${SERVICE}-scheduler"

check "installed investment-research.service matches the repo checkout" \
    cmp -s "${APP_DIR}/investment-research.service" "/etc/systemd/system/${SERVICE}.service"

check "installed investment-research-scheduler.service matches the repo checkout" \
    cmp -s "${APP_DIR}/investment-research-scheduler.service" "/etc/systemd/system/${SERVICE}-scheduler.service"

if [ -f "${APP_DIR}/investment-research-webhook.service" ]; then
    check "investment-research-webhook service is active" \
        systemctl is-active --quiet "${SERVICE}-webhook"

    check "installed investment-research-webhook.service matches the repo checkout" \
        cmp -s "${APP_DIR}/investment-research-webhook.service" "/etc/systemd/system/${SERVICE}-webhook.service"
fi

check "server's git HEAD matches origin/main (the pull genuinely landed the latest commit)" \
    bash -c "cd '${APP_DIR}' && [ \"\$(git rev-parse HEAD)\" = \"\$(git rev-parse origin/main)\" ]"

if [ "$ok" = "1" ]; then
    echo "DEPLOY VERIFICATION: PASS — service state and installed unit files match the repo."
    exit 0
else
    echo "DEPLOY VERIFICATION: FAIL — see [FAIL] lines above. The deploy's own command chain may have reported success despite this; do not assume the deploy landed cleanly."
    exit 1
fi
