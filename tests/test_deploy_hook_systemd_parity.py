"""Regression guard for the GitHub-webhook deploy migration (2026-08-05)
and the subsequent removal of the local pre-push test gate (same day,
once GitHub Actions became the real, unbypassable test gate).

Before this migration, "deploy this app" had THREE independent,
hand-maintained implementations of the same git-pull/pip-install/systemd-
restart command chain: deploy.sh, deploy.ps1, and the git pre-push hook's
own nohup'd background SSH session. All three kept drifting apart in
practice — see CLAUDE.md's "Deploy pipeline systemd parity audit" for two
real incidents this caused. deploy_steps() in deploy.sh is now the only
place any of that logic lives, called from exactly one place
(webhook_listener.py) or manually over SSH as a break-glass fallback.

The pre-push hook itself (and its generator, setup-hooks.sh) was removed
entirely, not just emptied out: once GitHub Actions ran the full suite on
GitHub's own infrastructure as the real deploy gate (independent of
whichever machine pushed, unbypassable via --no-verify), the local hook's
only remaining job — running the same suite again, locally, before every
push — no longer served that purpose; it only bought faster local feedback
at the cost of a second, redundant place testing policy could live. See
CLAUDE.md's "GitHub webhook deploy" for the reasoning.

This file guards three things:
  1. deploy.sh itself still contains every systemd-relevant step (sanity
     check on deploy_steps() — if a required pattern doesn't match here,
     the pattern is stale, not the script).
  2. deploy.sh always runs deploy_steps() unconditionally (no flag/mode
     switch that could silently break the webhook listener's plain
     `bash deploy.sh` invocation).
  3. No pre-push hook infrastructure exists in the repo or is installed
     locally — a regression guard, since reintroducing either recreates
     the exact "second place deploy/test logic can drift" problem this
     migration removed.
"""
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_SH = _REPO_ROOT / "deploy.sh"

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
    text = (_REPO_ROOT / "investment-research-webhook.service").read_text(encoding="utf-8")
    assert re.search(r"^KillMode=process\s*$", text, re.MULTILINE), (
        "investment-research-webhook.service must set KillMode=process -- without it, "
        "the service restarting itself (the last step of every deploy_steps() run) kills "
        "the still-running deploy.sh process before it can finish, silently skipping "
        "verify_deploy.sh. See this test's own docstring for the real incident this guards."
    )


def test_no_pre_push_hook_infrastructure_exists():
    """Regression guard for the pre-push hook's removal (2026-08-05): once
    GitHub Actions became the real, unbypassable test gate for deploys, the
    local pre-push hook (and its generator, setup-hooks.sh) had no
    remaining job and were removed entirely, not just emptied out --
    reintroducing either recreates a second place test/deploy policy can
    live and drift from the real gate, exactly the class of problem this
    whole migration exists to prevent. Checks both the tracked repo (no
    setup-hooks.sh file) and, where a real git checkout is available, that
    no pre-push hook is actually installed."""
    assert not (_REPO_ROOT / "setup-hooks.sh").exists(), (
        "setup-hooks.sh has come back -- the local pre-push hook was deliberately removed "
        "2026-08-05 once GitHub Actions became the real, unbypassable test gate. If a local "
        "test-gate hook is genuinely needed again, that's a deliberate decision to make "
        "explicitly, not something to silently reintroduce."
    )

    try:
        common_dir = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(_REPO_ROOT), check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # not a git checkout (e.g. an extracted tarball) -- nothing to check

    hook_path = Path(common_dir)
    if not hook_path.is_absolute():
        hook_path = _REPO_ROOT / hook_path
    hook_path = hook_path / "hooks" / "pre-push"
    assert not hook_path.exists(), (
        f"A pre-push hook is installed at {hook_path} -- the local test-gate hook was "
        "deliberately removed 2026-08-05 (see this test's own docstring). Remove it, or if "
        "it's serving some other genuine purpose now, document why it's intentional here."
    )
