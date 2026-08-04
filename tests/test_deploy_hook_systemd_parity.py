"""Structural parity guard between deploy.sh (the manual, complete deploy
path) and setup-hooks.sh's generated pre-push hook (the automated path
`git push origin main` triggers) — 2026-08-04.

Why this exists: two separate, independently-discovered gaps in the SAME
category were found reactively rather than by design — the automated
path not copying updated `.service` files into `/etc/systemd/system/`
(found via the FRED credential cleanup) and not running `systemctl
daemon-reload` before restarting (found via a real incident during the
per-user-accounts rollout that briefly seeded a random admin password
instead of the migrated one). See CLAUDE_HISTORY.md's "Deploy pipeline
systemd parity audit" for the full investigation both gaps were found and
fixed under, and every other divergence found in the same pass.

This is the structural safeguard against a THIRD instance of the same
class of drift, mirroring test_deploy_blackout_windows.py's own proven
"parse the real script text, cross-check it against the other real
source, never re-derive by hand and trust it stays in sync" design for
exactly this reason: a hand-maintained "the two paths are equivalent"
claim (like the one this task's own two originating incidents falsified)
is precisely the kind of assumption this project has already learned,
more than once, not to trust without a mechanical check. This test does
not replace deploy.sh being the single source of truth for what a
correct deploy does — it only asserts the automated path's own generated
command actually contains an equivalent operation for each one, so a
future systemd-relevant step added to deploy.sh without updating
setup-hooks.sh fails the suite immediately, at test time, rather than
being discovered reactively after something breaks in production the
way both of this file's own motivating incidents were.

Deliberately does NOT try to parse either script into a structured
command list and compare that (a "real" shell parser would be far more
robust to reordering/reformatting, but is a materially bigger and
riskier undertaking than this problem warrants) — this checks that a
required *pattern* appears in both files' raw text, which is exactly the
same level of rigor test_deploy_blackout_windows.py already uses for its
own window-bounds extraction, and is enough to catch the actual failure
shape both real incidents had: a step present in one script and entirely
absent from the other.
"""
import re
from pathlib import Path

_DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"
_SETUP_HOOKS_SH = Path(__file__).resolve().parent.parent / "setup-hooks.sh"

# Both service-name spellings that appear across the three deploy paths:
# the hook uses the literal name; deploy.sh/deploy.ps1 use a ${SERVICE}
# shell variable that expands to the identical literal at run time. A
# pattern needs to accept either spelling to genuinely check "was this
# step performed", not "was it performed using this exact literal string".
_SVC = r"(?:investment-research|\$\{?SERVICE\}?)"
_SCHED = rf"{_SVC}-scheduler"

# (label, regex) -- every entry must match somewhere in BOTH deploy.sh and
# the hook template setup-hooks.sh generates. Regexes are deliberately
# loose about whitespace/line-continuation (both scripts wrap these onto
# multiple lines for readability) but specific enough that a genuinely
# missing step can't accidentally match something unrelated.
_REQUIRED_SYSTEMD_STEPS = [
    (
        "discard local edits to the tracked .service files before pulling",
        r"git checkout\s+--\s+investment-research\.service\s+investment-research-scheduler\.service",
    ),
    (
        "pull the latest code",
        r"git pull origin main",
    ),
    (
        "install requirements.txt",
        r"pip install -r requirements\.txt",
    ),
    (
        "copy the updated investment-research.service into /etc/systemd/system/",
        rf"cp\s+\S*investment-research\.service\s+/etc/systemd/system/{_SVC}\.service",
    ),
    (
        "copy the updated investment-research-scheduler.service into /etc/systemd/system/",
        rf"cp\s+\S*investment-research-scheduler\.service\s+/etc/systemd/system/{_SCHED}\.service",
    ),
    (
        "ensure the scheduler's secrets.conf symlink exists",
        rf"ln\s+-sf[\s\\]+/etc/systemd/system/{_SVC}\.service\.d/secrets\.conf",
    ),
    (
        "reload the systemd daemon after installing the new unit files",
        r"systemctl daemon-reload",
    ),
    (
        "restart the web UI service",
        rf"systemctl restart {_SVC}(?!-scheduler)",
    ),
    (
        "ensure the scheduler service is enabled",
        rf"systemctl enable {_SCHED}",
    ),
    (
        "restart the scheduler service",
        rf"systemctl restart {_SCHED}",
    ),
    (
        "run the shared post-deploy verification script",
        r"verify_deploy\.sh",
    ),
]


def _extract_hook_template(setup_hooks_text: str) -> str:
    """The pre-push hook body setup-hooks.sh writes out, between the
    `cat > ... << 'HOOK'` / `HOOK` markers -- this is the actual template
    that becomes .git/hooks/pre-push, not setup-hooks.sh's own wrapper
    code around it."""
    match = re.search(r"<< 'HOOK'\n(.*?)\nHOOK\n", setup_hooks_text, re.DOTALL)
    assert match, (
        "Could not find the << 'HOOK' ... HOOK heredoc in setup-hooks.sh -- "
        "has the hook-generation mechanism changed shape?"
    )
    return match.group(1)


def test_deploy_sh_itself_contains_every_required_step():
    """Sanity check on the checklist itself, not the real assertion below --
    if a required pattern doesn't even match deploy.sh (the acknowledged
    reference implementation), the pattern itself is wrong, not the hook."""
    text = _DEPLOY_SH.read_text(encoding="utf-8")
    missing = [label for label, pattern in _REQUIRED_SYSTEMD_STEPS if not re.search(pattern, text)]
    assert not missing, (
        f"deploy.sh itself doesn't match the expected pattern for: {missing} -- "
        "the check's own regex is stale, not deploy.sh."
    )


def test_pre_push_hook_has_systemd_parity_with_deploy_sh():
    """The decisive drift check: every systemd-relevant step deploy.sh
    performs must have a matching step in the automated pre-push hook's
    generated deploy command. A future step added to deploy.sh without
    updating setup-hooks.sh fails here, not in production."""
    hook_text = _extract_hook_template(_SETUP_HOOKS_SH.read_text(encoding="utf-8"))

    missing = [
        label for label, pattern in _REQUIRED_SYSTEMD_STEPS
        if not re.search(pattern, hook_text)
    ]
    assert not missing, (
        "The automated pre-push hook (setup-hooks.sh) is missing systemd-relevant "
        f"step(s) that deploy.sh performs: {missing}. Either add the missing step to "
        "the hook template in setup-hooks.sh (then re-run `bash setup-hooks.sh` to "
        "regenerate the real, installed .git/hooks/pre-push), or, if the difference "
        "is genuinely intentional, remove it from _REQUIRED_SYSTEMD_STEPS above with "
        "a comment explaining why it's correct for the two paths to diverge here."
    )


def test_hooks_dir_resolved_via_git_common_dir_not_git_dir():
    """Regression guard for a related bug found in the same pass (2026-08-04):
    setup-hooks.sh used to compute HOOKS_DIR from `git rev-parse --git-dir`,
    which returns a per-WORKTREE private admin path when run from inside a
    worktree (this project's own standing practice for every isolated
    change) rather than the single, shared hooks directory every worktree's
    `git push` actually reads from -- silently installing the hook
    somewhere git never consults. `--git-common-dir` resolves correctly
    regardless of which worktree the script runs from."""
    text = _SETUP_HOOKS_SH.read_text(encoding="utf-8")
    assert re.search(r"\$\(git rev-parse --git-common-dir\)", text), (
        "setup-hooks.sh must resolve HOOKS_DIR via `$(git rev-parse --git-common-dir)`, "
        "not `--git-dir` -- the latter returns a per-worktree private path when run "
        "from inside a worktree and would silently install the hook somewhere git "
        "never reads it from."
    )
    # Only checks actual command-substitution usage ($(...)), not prose --
    # this file's own explanatory comments legitimately mention the phrase
    # `git rev-parse --git-dir` (backtick-quoted, not $(...)-invoked) when
    # explaining what NOT to do; a bare substring check would false-positive
    # on that prose rather than catching a genuine reintroduction of the bug.
    assert not re.search(r"\$\(git rev-parse --git-dir\)", text), (
        "setup-hooks.sh still contains a genuine `$(git rev-parse --git-dir)` call -- "
        "this resolves the WRONG (per-worktree) hooks path; use --git-common-dir instead."
    )
