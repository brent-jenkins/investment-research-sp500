"""Regression guard for the GitHub-webhook deploy migration (2026-08-05).

Before this migration, "deploy this app" had THREE independent,
hand-maintained implementations of the same git-pull/pip-install/systemd-
restart command chain: deploy.sh, deploy.ps1, and the git pre-push hook's
own nohup'd background SSH session. All three kept drifting apart in
practice — see CLAUDE.md's "Deploy pipeline systemd parity audit" for two
real incidents this caused. This file used to assert the pre-push hook's
generated command chain stayed in systemd-parity with deploy.sh's; that
whole category of drift is now impossible by construction, not just
checked-for, because the hook no longer performs any deploy steps at all —
deploys are triggered by a GitHub webhook (webhook_listener.py) invoking
`bash deploy.sh`, the single place deploy_steps() lives (see deploy.sh's own
module docstring-equivalent header comment). deploy.sh's own former
"local/dev mode" (and its PowerShell twin, deploy.ps1) — which pushed from
a developer's machine and then delegated over SSH — was removed 2026-08-05
once the webhook made it redundant for routine deploys; deploy.sh now
always runs the deploy directly, whether invoked by the webhook listener or
manually over SSH as a break-glass fallback.

This file now guards the two things that could silently reintroduce the
old duplication:
  1. deploy.sh itself still contains every systemd-relevant step (sanity
     check on deploy_steps() — if a required pattern doesn't match here,
     the pattern is stale, not the script).
  2. The pre-push hook template setup-hooks.sh generates contains NO
     systemd/SSH/deploy commands of its own — a regression guard, since
     "helpfully" adding a deploy step back into the hook is exactly the
     mistake that created the original three-way drift.
  3. --git-common-dir vs --git-dir regression guard (unrelated to the
     above, carried over from the original file — still a real, distinct
     bug class worth guarding against).
"""
import re
from pathlib import Path

_DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"
_SETUP_HOOKS_SH = Path(__file__).resolve().parent.parent / "setup-hooks.sh"

_SVC = r"(?:investment-research|\$\{?SERVICE\}?)"
_SCHED = rf"{_SVC}-scheduler"
# deploy.sh writes unit files under an overridable SYSTEMD_DIR (defaults to
# the real /etc/systemd/system in every actual deploy; tests point it at a
# scratch directory) -- accept either spelling, same reasoning as _SVC above.
_SYSTEMD_DIR = r"(?:/etc/systemd/system|\$\{?SYSTEMD_DIR\}?)"

# (label, regex) -- every entry must match somewhere in deploy.sh's
# deploy_steps() function, the single canonical implementation of "deploy
# this app" server-side.
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
        rf'cp\s+"?\S*investment-research\.service"?\s+"?{_SYSTEMD_DIR}/{_SVC}\.service',
    ),
    (
        "copy the updated investment-research-scheduler.service into /etc/systemd/system/",
        rf'cp\s+"?\S*investment-research-scheduler\.service"?\s+"?{_SYSTEMD_DIR}/{_SCHED}\.service',
    ),
    (
        "ensure the scheduler's secrets.conf symlink exists",
        rf'ln\s+-sf[\s\\]+"?{_SYSTEMD_DIR}/{_SVC}\.service\.d/secrets\.conf',
    ),
    (
        "reload the systemd daemon after installing the new unit files",
        r"systemctl daemon-reload",
    ),
    (
        "restart the web UI service",
        rf'systemctl restart "?{_SVC}"?(?!-)',
    ),
    (
        "ensure the scheduler service is enabled",
        rf"systemctl enable \"?{_SCHED}\"?",
    ),
    (
        "restart the scheduler service",
        rf"systemctl restart \"?{_SCHED}\"?",
    ),
    (
        "run the shared post-deploy verification script",
        r"verify_deploy\.sh",
    ),
]

# Patterns that must NEVER appear in the pre-push hook template -- any of
# these reappearing is a strong signal the old duplicated deploy path is
# being reintroduced.
_FORBIDDEN_IN_HOOK = [
    ("an ssh invocation", r"\bssh\s"),
    ("a systemctl invocation", r"\bsystemctl\b"),
    ("a git pull (deploy step, not this hook's own test-gate concern)", r"git pull origin main"),
    # Only flags actually INVOKING deploy.sh (the hook must not know how to
    # deploy) -- mentioning it in an explanatory comment (e.g. "deploys are
    # triggered by the webhook now, not this hook, see deploy.sh") is
    # expected and fine; only a genuine invocation is the regression.
    ("an invocation of deploy.sh (the hook must not know how to deploy)", r"\bbash\s+\S*deploy\.sh"),
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
    """Sanity check on the checklist itself -- if a required pattern doesn't
    even match deploy.sh (the acknowledged reference implementation), the
    pattern itself is wrong, not deploy.sh."""
    text = _DEPLOY_SH.read_text(encoding="utf-8")
    missing = [label for label, pattern in _REQUIRED_SYSTEMD_STEPS if not re.search(pattern, text)]
    assert not missing, (
        f"deploy.sh itself doesn't match the expected pattern for: {missing} -- "
        "the check's own regex is stale, not deploy.sh."
    )


def test_deploy_sh_runs_deploy_steps_unconditionally():
    """The webhook listener depends on `bash deploy.sh` running
    deploy_steps() directly, every time it's invoked -- no flag, no mode
    switch, no push/ssh involved. deploy.sh used to require a --server-side
    flag to reach this behavior (there was also a "local/dev mode" that
    pushed and delegated over SSH instead); that flag and mode were removed
    2026-08-05 once the webhook made the local/dev path redundant for
    routine deploys -- deploy.sh now always deploys directly. If
    deploy_steps() is ever made conditional again, the whole webhook deploy
    path silently breaks."""
    text = _DEPLOY_SH.read_text(encoding="utf-8")
    assert "deploy_steps" in text, "deploy.sh no longer defines deploy_steps() -- has it been restructured?"
    assert "--server-side" not in text, (
        "deploy.sh still references --server-side -- this flag/mode was deliberately removed "
        "2026-08-05 (see the file's own header comment); a bare `bash deploy.sh` must always "
        "deploy directly, unconditionally, for the webhook listener to keep working."
    )
    # deploy_steps() must be called unconditionally near the end of the file,
    # not gated behind an if-block -- a loose but effective check that it
    # isn't hidden behind some reintroduced mode switch.
    assert re.search(r"^deploy_steps$", text, re.MULTILINE), (
        "deploy.sh must call deploy_steps() as a bare, unconditional statement -- "
        "if it's only ever called inside a conditional block, the webhook listener's "
        "plain `bash deploy.sh` invocation may not actually run it."
    )


def test_pre_push_hook_contains_no_deploy_logic():
    """The decisive regression guard: the pre-push hook must be a pure
    test-gate now, with zero deploy steps of its own. Deploys are triggered
    by the GitHub webhook (webhook_listener.py -> deploy.sh)
    once a push lands on GitHub -- the hook runs strictly before that push
    even happens and has no business knowing how to deploy anything.
    Reintroducing any of these patterns here recreates the exact
    three-implementations-drifting-apart problem this migration removed."""
    hook_text = _extract_hook_template(_SETUP_HOOKS_SH.read_text(encoding="utf-8"))

    found = [label for label, pattern in _FORBIDDEN_IN_HOOK if re.search(pattern, hook_text)]
    assert not found, (
        f"The pre-push hook template contains deploy-shaped logic it should not: {found}. "
        "Deploys are triggered by the GitHub webhook, not this hook -- see "
        "webhook_listener.py and deploy.sh. If the hook is "
        "growing a deploy step again, that's the exact duplication this migration "
        "removed; don't reintroduce it here."
    )
    assert "python -m pytest" in hook_text, (
        "The pre-push hook must still run the test suite before allowing a push to "
        "main through -- that's its one remaining job."
    )


def test_webhook_service_uses_kill_mode_process():
    """Regression guard for a real incident hit during the webhook
    migration's own staged rollout (2026-08-05): deploy_steps() restarts
    investment-research-webhook as its own last restart step, and
    webhook_listener.py's trigger_deploy() spawns `bash deploy.sh` as a
    direct child process of that same systemd unit.
    With systemd's default KillMode=control-group, "systemctl restart
    investment-research-webhook" kills the ENTIRE cgroup on stop --
    including the still-running deploy.sh process that issued the restart
    command -- silently truncating the deploy before it reaches
    `systemctl status` or verify_deploy.sh. This was not theoretical: the
    first genuine GitHub-triggered deploy through this service was cut
    short at exactly this point (confirmed via journalctl -- the deploy
    process's log output stopped mid-"Restarting services..." with no
    "Server-side deploy complete" ever printed, even though the underlying
    git pull/service restarts had genuinely succeeded). KillMode=process
    targets only the unit's own tracked main PID on stop/restart, leaving
    deploy.sh (reparented, not killed) free to finish. See the unit file's
    own comment for the accepted trade-off this introduces."""
    text = (Path(__file__).resolve().parent.parent / "investment-research-webhook.service").read_text(
        encoding="utf-8"
    )
    assert re.search(r"^KillMode=process\s*$", text, re.MULTILINE), (
        "investment-research-webhook.service must set KillMode=process -- without it, "
        "the service restarting itself (the last step of every deploy_steps() run) kills "
        "the still-running deploy.sh process before it can finish, silently skipping "
        "verify_deploy.sh. See this test's own docstring for the real incident this guards."
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
    assert not re.search(r"\$\(git rev-parse --git-dir\)", text), (
        "setup-hooks.sh still contains a genuine `$(git rev-parse --git-dir)` call -- "
        "this resolves the WRONG (per-worktree) hooks path; use --git-common-dir instead."
    )
