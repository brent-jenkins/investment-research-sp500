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
# Runs the test suite before any push to main. Does NOT deploy anything --
# deploying used to happen from here too (a nohup'd background SSH session
# into the VM), but that made "deploy this app" have two independent,
# hand-maintained implementations -- this hook's own copy of the deploy
# command chain, and deploy.sh's copy -- which kept drifting apart (see
# CLAUDE.md's "Deploy pipeline systemd parity audit"). Deploys are now
# triggered by a GitHub webhook (see CLAUDE.md's "GitHub webhook deploy"
# and webhook_listener.py) once the push this hook is gating actually lands
# on GitHub -- there is no second deploy path left to keep in sync with
# deploy.sh, structurally, not just by convention.
#
# This hook's only remaining job: block a push to main if the test suite
# doesn't pass. It does NOT push to GitHub itself -- git runs the actual
# `git push` immediately after this hook exits 0.

REPO_ROOT="$(git rev-parse --show-toplevel)"

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
        echo "✅ Tests passed — pushing now; the GitHub webhook will deploy to"
        echo "    platform.example.com once this push lands on GitHub."
        echo ""
        exit 0
    fi
done

exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-push"

echo "Done — pre-push hook installed at $HOOKS_DIR/pre-push"
echo ""
echo "Pushes to main will now run the test suite before being allowed through."
echo "Deploys to platform.example.com are triggered by a GitHub webhook once the"
echo "push lands on GitHub (see CLAUDE.md's \"GitHub webhook deploy\"), not by"
echo "this hook. To skip the test gate for a single push: git push --no-verify"
