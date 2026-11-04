"""No credential may be committed to this repository.

This is the public copy of the project. Before it was published, its history
was rewritten to remove a FRED API key and a Brevo SMTP key that had sat in
config.yaml, plus the SMTP login and a personal address. Secrets come from the
environment (T212_API_KEY, T212_API_SECRET, SMTP_PASSWORD, ADMIN_PASSWORD_HASH,
GITHUB_WEBHOOK_SECRET). These tests fail the build if one is written into a
file again.
"""
import os
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that are git-ignored runtime state, or are this file's own home.
_SKIP_DIRS = {".git", "__pycache__", "database", ".venv", "venv", "env", ".pytest_cache"}
_TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".sh", ".service", ".timer",
    ".json", ".csv", ".example", ".cfg", ".ini", ".html",
}
_TEXT_NAMES = {"Caddyfile", ".gitignore"}

# Shapes that are a credential whatever they are attached to.
CREDENTIAL_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bcrypt hash": re.compile(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}"),
    "Brevo key": re.compile(r"\bx(?:smtp|key)sib-[0-9a-f]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}

_SECRET_KEY_NAME = re.compile(r"password|secret|api_key|apikey|token", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def find_credentials(text: str) -> list[str]:
    """Names of the credential shapes found in text."""
    return [name for name, rx in CREDENTIAL_PATTERNS.items() if rx.search(text)]


def non_blank_secret_fields(node, path: str = "") -> list[str]:
    """Dotted paths of secret-named config keys that hold a non-blank value."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if _SECRET_KEY_NAME.search(str(key)) and not isinstance(value, (dict, list)):
                if value not in (None, "") and not isinstance(value, bool):
                    found.append(here)
            found.extend(non_blank_secret_fields(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(non_blank_secret_fields(value, f"{path}[{i}]"))
    return found


def _repo_text_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix in _TEXT_SUFFIXES or name in _TEXT_NAMES:
                yield p


def test_no_credential_shaped_value_in_any_text_file():
    scanned, hits = 0, []
    for p in _repo_text_files():
        scanned += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        for name in find_credentials(text):
            hits.append(f"{p.relative_to(REPO_ROOT)}: {name}")
    # Guard against a scan that passes because it found nothing to read.
    assert scanned > 500, f"only {scanned} files scanned -- the walk is broken"
    assert hits == [], "credential-shaped values committed:\n" + "\n".join(hits)


def test_config_secret_fields_are_blank():
    config = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    leaked = non_blank_secret_fields(config)
    assert leaked == [], f"set these from the environment, not config.yaml: {leaked}"


def test_config_addresses_are_placeholders():
    text = (REPO_ROOT / "config.yaml").read_text(encoding="utf-8")
    addresses = _EMAIL.findall(text)
    assert addresses, "config.yaml has no addresses -- this test is no longer checking anything"
    real = [a for a in addresses if not a.lower().endswith("@example.com")]
    assert real == [], f"use @example.com placeholders in config.yaml: {real}"


def test_the_detectors_recognise_what_they_are_named_for():
    """Without this, an over-narrow pattern would pass every scan above."""
    assert find_credentials("-----BEGIN OPENSSH " + "PRIVATE KEY-----") == ["private key"]
    assert find_credentials("$2b$12$" + "a" * 53) == ["bcrypt hash"]
    assert find_credentials("xsmtpsib-" + "a1" * 16) == ["Brevo key"]
    assert find_credentials("ghp_" + "A" * 36) == ["GitHub token"]
    assert find_credentials("AKIA" + "ABCDEFGHIJKLMNOP") == ["AWS access key"]
    assert find_credentials("an ordinary sentence about passwords") == []

    cfg = {"email": {"smtp_password": "hunter2", "smtp_user": "x"},
           "macro": {"fred_api_key": ""}, "flags": {"token_refresh": True}}
    assert non_blank_secret_fields(cfg) == ["email.smtp_password"]
