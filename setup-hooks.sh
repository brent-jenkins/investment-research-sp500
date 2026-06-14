#!/usr/bin/env bash
# setup-hooks.sh — Install git hooks for this repository.
#
# Run once after cloning:
#   bash setup-hooks.sh
#
# Safe to re-run — overwrites any existing hook with the current version.

set -euo pipefail

HOOKS_DIR="$(git rev-parse --git-dir)/hooks"

echo "Installing git hooks..."

cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/usr/bin/env bash
# .git/hooks/pre-push — managed by setup-hooks.sh (re-run to update)
#
# After a push to main lands on GitHub, SSH into the server to pull and
# restart. The SSH runs in a background process (detached) so git can
# finish sending objects to GitHub first — otherwise the server's git pull
# would fetch the previous commit, not the one just pushed.

REPO_ROOT="$(git rev-parse --show-toplevel)"
SSH_KEY="$REPO_ROOT/ssh-keys/deploy_key"
VM_IP="${VM_IP:-203.0.113.10}"
LOG="/tmp/investment-research-deploy.log"

while IFS=' ' read -r local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" == "refs/heads/main" ]]; then
        echo ""
        echo "Deploying to research.example.com (runs after push completes)..."

        # Detach so this hook exits and lets git finish the push to GitHub.
        # The 15-second sleep ensures GitHub has received the objects before
        # the server tries to pull them.
        nohup bash -c "
            sleep 15
            ssh -i '$SSH_KEY' -o StrictHostKeyChecking=no root@$VM_IP \
                'cd /root/investment-research && git pull origin main && systemctl restart investment-research' \
                >> '$LOG' 2>&1
            echo 'Deploy exit code: '\$? >> '$LOG'
        " > /dev/null 2>&1 &
        disown

        echo "Deploy queued — server will pull in ~15 s. Tail log: tail -f $LOG"
        echo ""
        exit 0
    fi
done

exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-push"

echo "Done — pre-push hook installed at $HOOKS_DIR/pre-push"
echo ""
echo "Pushes to main will now automatically deploy to research.example.com."
echo "Deploy runs ~15 s after push. To skip: VM_IP= git push origin main (or just uninstall the hook)."
