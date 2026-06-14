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
# Intercepts pushes to main. Pushes to GitHub first, then SSHes into the
# server to pull and restart. The original `git push` that triggered this
# hook becomes a no-op ("Everything up to date") because the push was
# already handled here.
#
# Recursion guard: the inner `DEPLOYING=1 git push` sets DEPLOYING in the
# environment of the child git process. When git fires the pre-push hook
# again for that inner push, the check below exits 0 immediately.
# This is pure bash→git→bash, so env-var propagation is reliable.

if [[ "${DEPLOYING:-0}" == "1" ]]; then
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
SSH_KEY="$REPO_ROOT/ssh-keys/deploy_key"
VM_IP="${VM_IP:-203.0.113.10}"

while IFS=' ' read -r local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" == "refs/heads/main" ]]; then
        echo ""
        echo ">>> Pushing to GitHub..."
        if ! DEPLOYING=1 git push origin main; then
            echo "GitHub push failed — skipping server deploy."
            exit 0
        fi

        echo ""
        echo ">>> Deploying to research.example.com..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${VM_IP}" \
            "cd /root/investment-research && git pull origin main && systemctl restart investment-research"

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
echo "To skip deployment for a single push: DEPLOYING=1 git push origin main"
