#!/usr/bin/env bash
# setup-hooks.sh — Install git hooks for this repository.
#
# Run once after cloning:
#   bash setup-hooks.sh
#
# Safe to re-run — overwrites any existing hook with the current version.

set -euo pipefail

# --git-common-dir, not --git-dir (2026-08-04): hooks are shared across
# every worktree of this repository, stored once in the MAIN repo's
# .git/hooks -- but `git rev-parse --git-dir`, when run from inside a
# worktree (this project's own established practice for every isolated
# change), returns that worktree's own private per-worktree admin
# directory (.git/worktrees/<name>), not the shared location git actually
# reads hooks from. Using --git-dir here would silently install the hook
# into a path git never consults, leaving the real, shared hook every
# worktree's `git push` actually triggers untouched. --git-common-dir
# resolves to the main .git directory regardless of which worktree this
# script is run from, so re-running it from any worktree correctly
# updates the one real hook.
HOOKS_DIR="$(git rev-parse --git-common-dir)/hooks"

echo "Installing git hooks..."

cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/usr/bin/env bash
# .git/hooks/pre-push — managed by setup-hooks.sh (re-run to update)
#
# Runs the test suite before any push to main, then schedules a deploy to
# platform.example.com in the background — restarting BOTH investment-research
# (Streamlit UI) and investment-research-scheduler (the daemon that actually
# fires scheduled jobs and places orders). Restarting only the UI service
# leaves the scheduler daemon running stale in-memory code indefinitely,
# silently bypassing any regime-gate or order-logic change just deployed.
# Does NOT push to GitHub itself —
# git runs the actual `git push` immediately after this hook exits 0, so
# that push (the one the user typed) is what puts the commits on GitHub.
#
# (Earlier version of this hook pushed to GitHub itself from inside the
# hook, which raced the outer `git push`: the hook's push would advance
# origin/main, and the outer push — already holding the old remote SHA —
# would then get rejected with "cannot lock ref". Not pushing from the
# hook at all avoids the double-push entirely.)
#
# The background delay gives the outer `git push` time to land on GitHub
# before the server's `git pull` runs; deploy output goes to the log file
# below rather than the terminal, since the hook has already exited.

REPO_ROOT="$(git rev-parse --show-toplevel)"
SSH_KEY="$REPO_ROOT/ssh-keys/deploy_key"
VM_IP="${VM_IP:-203.0.113.10}"
LOG_FILE="/tmp/investment-research-deploy.log"

while IFS=' ' read -r local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" == "refs/heads/main" ]]; then
        echo ""
        echo ">>> Running test suite..."
        if ! (cd "$REPO_ROOT" && python -m pytest); then
            echo ""
            echo "❌ Tests failed — push aborted. Fix failing tests before deploying."
            exit 1
        fi
        echo ""
        echo "✅ Tests passed — pushing now; deploy to platform.example.com will follow in the background."
        echo "    (deploy log: $LOG_FILE)"

        nohup bash -c '
            sleep 15
            ssh_key="$1"; vm_ip="$2"; log_file="$3"
            {
                echo "[$(date -u +%FT%TZ)] Deploying to platform.example.com"
                # Full systemd-relevant step sequence, matching deploy.sh/
                # deploy.ps1 exactly (see CLAUDE_HISTORY.mds "Deploy pipeline
                # systemd parity audit", 2026-08-04) -- every `2>/dev/null ||
                # true` guard is wrapped in its own parentheses so it only
                # tolerates ITS OWN preceding command failing, never an
                # earlier ones (an unparenthesized guard later in a &&-chain
                # silently swallows any earlier failure too, confirmed as a
                # real bug found in deploy.ps1 during that same audit -- see
                # that section for the direct reproduction). Ends by calling
                # verify_deploy.sh, the same post-deploy check deploy.sh/
                # deploy.ps1 also call, so a "successful" exit code here is
                # backed by directly-checked real state, not just assumed
                # from the command chain completing.
                if ssh -i "$ssh_key" -o StrictHostKeyChecking=no "root@${vm_ip}" \
                    "cd /root/investment-research \
                    && (git checkout -- investment-research.service investment-research-scheduler.service 2>/dev/null || true) \
                    && git pull origin main \
                    && python3.11 -m pip install -r requirements.txt --quiet \
                    && (python3.11 -m pip install TA-Lib --quiet 2>/dev/null || true) \
                    && cp investment-research.service /etc/systemd/system/investment-research.service \
                    && cp investment-research-scheduler.service /etc/systemd/system/investment-research-scheduler.service \
                    && mkdir -p /etc/systemd/system/investment-research-scheduler.service.d \
                    && (ln -sf /etc/systemd/system/investment-research.service.d/secrets.conf /etc/systemd/system/investment-research-scheduler.service.d/secrets.conf 2>/dev/null || true) \
                    && systemctl daemon-reload \
                    && systemctl restart investment-research \
                    && (systemctl enable investment-research-scheduler 2>/dev/null || true) \
                    && systemctl restart investment-research-scheduler \
                    && sleep 3 \
                    && bash verify_deploy.sh"; then
                    echo "[$(date -u +%FT%TZ)] Deploy completed and verified."
                else
                    echo "[$(date -u +%FT%TZ)] DEPLOY FAILED OR VERIFICATION FAILED -- see output above. Do not assume the deploy landed cleanly."
                fi
            } >> "$log_file" 2>&1
        ' _ "$SSH_KEY" "$VM_IP" "$LOG_FILE" >/dev/null 2>&1 &
        disown

        echo ""
        exit 0
    fi
done

exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-push"

echo "Done — pre-push hook installed at $HOOKS_DIR/pre-push"
echo ""
echo "Pushes to main will now run the test suite, then automatically deploy to"
echo "platform.example.com in the background ~15s after the push completes."
echo "To skip both for a single push: git push --no-verify"
