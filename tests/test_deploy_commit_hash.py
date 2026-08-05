"""Tests for deploy.sh's deployed-commit-hash capture -- the Status page
(main.py's "Last Deployment" card) shows exactly which commit is currently
running, sourced from a file deploy.sh writes at deploy time
(`${APP_DIR}/.deployed_commit`) rather than re-derived from `git log`/`git
rev-parse` against the live checkout at app runtime. Capturing it at deploy
time means the value stays correct as "what was actually deployed" even if
someone later runs a manual `git pull` on the server without redeploying.

These tests drive the REAL deploy.sh script end-to-end via subprocess
against a disposable scratch APP_DIR (same fixture pattern as
tests/test_deploy_server_side_blackout.py, duplicated here rather than
shared -- every deploy test file in this suite is self-contained), with
`systemctl`/`python3.11` shimmed out via PATH so no real systemd or Python
package installs happen.
"""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_SH = _REPO_ROOT / "deploy.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="requires bash")


def _run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def deploy_scratch(tmp_path):
    """Same scratch-repo layout as test_deploy_server_side_blackout.py's own
    fixture -- see its docstring for the full rationale. Duplicated rather
    than imported, matching this suite's existing per-file self-containment
    for deploy tests."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    origin_dir = tmp_path / "origin.git"
    systemd_dir = tmp_path / "etc_systemd"
    systemd_dir.mkdir()
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir()
    marker_file = tmp_path / "verify_deploy_ran.marker"

    _run_git(tmp_path, "init", "-q", "--bare", str(origin_dir))

    _run_git(app_dir, "init", "-q", "-b", "main")
    _run_git(app_dir, "config", "user.email", "test@example.com")
    _run_git(app_dir, "config", "user.name", "Test")

    (app_dir / "investment-research.service").write_text("[Unit]\nDescription=test\n", encoding="utf-8")
    (app_dir / "investment-research-scheduler.service").write_text("[Unit]\nDescription=test\n", encoding="utf-8")
    (app_dir / "requirements.txt").write_text("", encoding="utf-8")
    (app_dir / "verify_deploy.sh").write_text(
        "#!/usr/bin/env bash\ntouch \"$DEPLOY_TEST_MARKER_FILE\"\nexit 0\n", encoding="utf-8"
    )

    _run_git(app_dir, "add", ".")
    _run_git(app_dir, "commit", "-q", "-m", "initial")
    _run_git(app_dir, "remote", "add", "origin", str(origin_dir))
    _run_git(app_dir, "push", "-q", "-u", "origin", "main")

    for name, body in (
        ("systemctl", "#!/usr/bin/env bash\nexit 0\n"),
        ("python3.11", "#!/usr/bin/env bash\nexit 0\n"),
    ):
        stub_path = stub_bin / name
        stub_path.write_text(body, encoding="utf-8")
        stub_path.chmod(0o755)

    return {
        "app_dir": app_dir,
        "systemd_dir": systemd_dir,
        "stub_bin": stub_bin,
        "marker_file": marker_file,
    }


def _run_server_side(scratch, extra_env=None, timeout=30):
    import os

    env = dict(os.environ)
    env["PATH"] = str(scratch["stub_bin"]) + os.pathsep + env.get("PATH", "")
    env["APP_DIR"] = str(scratch["app_dir"])
    env["SERVICE"] = "test-investment-research"
    env["SYSTEMD_DIR"] = str(scratch["systemd_dir"])
    env["DEPLOY_TEST_MARKER_FILE"] = str(scratch["marker_file"])
    # Skip the blackout wait entirely -- irrelevant to hash-capture, and
    # keeps these tests fast regardless of real wall-clock time.
    env["DEPLOY_SKIP_BLACKOUT_CHECK"] = "1"
    env.update(extra_env or {})

    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(_DEPLOY_SH)],
        cwd=str(scratch["app_dir"]),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - start
    return result, elapsed


def test_deploy_writes_the_real_head_hash_to_deployed_commit_file(deploy_scratch):
    result, _ = _run_server_side(deploy_scratch)
    assert result.returncode == 0, result.stdout + result.stderr

    real_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(deploy_scratch["app_dir"]), check=True, capture_output=True, text=True,
    ).stdout.strip()

    hash_file = deploy_scratch["app_dir"] / ".deployed_commit"
    assert hash_file.exists(), ".deployed_commit was never written by deploy_steps()"
    written = hash_file.read_text(encoding="utf-8").strip()
    assert written == real_head
    assert len(written) == 40, f"expected a full 40-char SHA-1, got {written!r}"


def test_deploy_writes_a_fresh_hash_after_a_new_commit_lands(deploy_scratch):
    """The file must reflect whatever `git pull` actually landed, not a
    value baked in at repo-init time -- add a second commit to the bare
    origin, then confirm the written hash tracks it after deploy_steps()
    pulls it in."""
    app_dir = deploy_scratch["app_dir"]

    # Simulate a second push landing on origin/main via a second clone --
    # cloned from the bare origin (not app_dir itself), since pushing back
    # into app_dir's own checked-out branch would be rejected by git's
    # default denyCurrentBranch safeguard.
    origin_dir = app_dir.parent / "origin.git"
    second_clone = app_dir.parent / "second_clone"
    _run_git(app_dir.parent, "clone", "-q", str(origin_dir), str(second_clone))
    # The bare origin's own HEAD symref still points at git's default
    # branch name (e.g. "master"), not "main" -- clone's own auto-checkout
    # silently fails to check out a real branch as a result, even though
    # refs/heads/main exists and was fetched. Check it out explicitly.
    _run_git(second_clone, "checkout", "main")
    _run_git(second_clone, "config", "user.email", "test@example.com")
    _run_git(second_clone, "config", "user.name", "Test")
    (second_clone / "new_file.txt").write_text("hello\n", encoding="utf-8")
    _run_git(second_clone, "add", "new_file.txt")
    _run_git(second_clone, "commit", "-q", "-m", "second commit")
    _run_git(second_clone, "push", "-q", "origin", "main")

    result, _ = _run_server_side(deploy_scratch)
    assert result.returncode == 0, result.stdout + result.stderr

    real_head = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=str(app_dir), check=True, capture_output=True, text=True,
    ).stdout.strip()

    written = (app_dir / ".deployed_commit").read_text(encoding="utf-8").strip()
    assert written == real_head
