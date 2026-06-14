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
# Automatically deploys to research.example.com whenever commits are pushed
# to the main branch.
#
# Recursion guard: deploy.ps1 calls 'git push origin main' internally, which
# would re-enter this hook. Setting DEPLOYING=1 before launching deploy.ps1
# propagates to all child processes (PowerShell → git → this hook), so the
# inner push exits immediately without triggering a second deployment.

if [[ "${DEPLOYING:-}" == "1" ]]; then
    exit 0
fi

while IFS=' ' read -r local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" == "refs/heads/main" ]]; then
        echo ""
        echo "Auto-deploying to research.example.com..."
        echo ""

        # cygpath converts the Git Bash path (/c/GitHub/...) to a Windows path
        # (C:\GitHub\...) that PowerShell's -File parameter can resolve.
        WIN_ROOT="$(cygpath -w "$(git rev-parse --show-toplevel)")"

        DEPLOYING=1 powershell.exe -NonInteractive -ExecutionPolicy Bypass \
            -File "${WIN_ROOT}\\deploy.ps1"

        # Exit 0 whether deploy succeeded or failed — the push itself has already
        # been handled by deploy.ps1 (step 1/3), so aborting here serves no purpose.
        # Errors are visible in the deploy.ps1 output above.
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
