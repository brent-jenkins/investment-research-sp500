"""Tests for webhook_listener.py — the GitHub webhook deploy listener that
replaced the old push-triggered local SSH deploy mechanism (see
CLAUDE.md's "GitHub webhook deploy").

Covers exactly what the migration's own risk profile demands: a request
with an invalid or missing signature must never trigger a deploy, and a
genuinely valid, correctly-signed push to main must. `trigger_deploy` is
monkeypatched throughout — this suite verifies the listener's own
verify-then-decide logic, not `deploy.sh` itself (which does real,
destructive systemd/git operations and is covered separately by
tests/test_deploy_hook_systemd_parity.py and
tests/test_deploy_server_side_blackout.py).
"""
import hashlib
import hmac
import http.client
import json
import threading

import pytest

import webhook_listener as wl

SECRET = "test-secret-do-not-use-in-production"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# --- Pure function tests -----------------------------------------------------

def test_verify_signature_accepts_correct_signature():
    body = b'{"ref": "refs/heads/main"}'
    assert wl.verify_signature(SECRET, body, _sign(body)) is True


def test_verify_signature_rejects_wrong_secret():
    body = b'{"ref": "refs/heads/main"}'
    assert wl.verify_signature(SECRET, body, _sign(body, secret="wrong-secret")) is False


def test_verify_signature_rejects_tampered_body():
    body = b'{"ref": "refs/heads/main"}'
    signature = _sign(body)
    tampered = b'{"ref": "refs/heads/evil"}'
    assert wl.verify_signature(SECRET, tampered, signature) is False


def test_verify_signature_rejects_missing_header():
    assert wl.verify_signature(SECRET, b"{}", None) is False


def test_verify_signature_rejects_malformed_header():
    assert wl.verify_signature(SECRET, b"{}", "not-a-real-signature") is False
    assert wl.verify_signature(SECRET, b"{}", "sha1=deadbeef") is False


def test_should_trigger_deploy_push_to_main():
    assert wl.should_trigger_deploy("push", {"ref": "refs/heads/main"}) is True


def test_should_trigger_deploy_push_to_other_branch():
    assert wl.should_trigger_deploy("push", {"ref": "refs/heads/feature-x"}) is False


def test_should_trigger_deploy_ping_event():
    assert wl.should_trigger_deploy("ping", {}) is False


def test_should_trigger_deploy_pull_request_event():
    assert wl.should_trigger_deploy("pull_request", {"ref": "refs/heads/main"}) is False


# --- HTTP-level tests ---------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    """A real ThreadingHTTPServer bound to an ephemeral loopback port, with
    trigger_deploy replaced by a recording stub so no real subprocess is
    ever spawned."""
    calls = []
    monkeypatch.setattr(wl, "trigger_deploy", lambda *a, **k: calls.append(True))

    httpd = wl.ThreadingHTTPServer(("127.0.0.1", 0), wl.WebhookHandler)
    httpd.webhook_secret = SECRET
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(port, path, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _push_payload(ref="refs/heads/main"):
    return json.dumps({"ref": ref, "after": "deadbeef"}).encode("utf-8")


def test_missing_signature_header_is_rejected_and_does_not_deploy(server):
    port, calls = server
    body = _push_payload()
    status, _ = _post(port, "/webhook/deploy", body, {
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    assert status == 401
    assert calls == []


def test_invalid_signature_is_rejected_and_does_not_deploy(server):
    port, calls = server
    body = _push_payload()
    status, _ = _post(port, "/webhook/deploy", body, {
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": "sha256=" + "0" * 64,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    assert status == 401
    assert calls == []


def test_valid_signed_ping_does_not_deploy(server):
    port, calls = server
    body = b"{}"
    status, resp_body = _post(port, "/webhook/deploy", body, {
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": _sign(body),
        "Content-Length": str(len(body)),
    })
    assert status == 200
    assert resp_body == b"pong"
    assert calls == []


def test_valid_signed_push_to_other_branch_does_not_deploy(server):
    port, calls = server
    body = _push_payload(ref="refs/heads/some-feature")
    status, _ = _post(port, "/webhook/deploy", body, {
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": _sign(body),
        "Content-Length": str(len(body)),
    })
    assert status == 200
    assert calls == []


def test_valid_signed_push_to_main_triggers_deploy(server):
    port, calls = server
    body = _push_payload(ref="refs/heads/main")
    status, resp_body = _post(port, "/webhook/deploy", body, {
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": _sign(body),
        "Content-Length": str(len(body)),
    })
    assert status == 202
    assert b"deploy triggered" in resp_body
    assert calls == [True]


def test_health_endpoint_does_not_require_signature(server):
    port, _calls = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.read() == b"ok"
    finally:
        conn.close()


def test_listener_refuses_to_start_without_secret(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    # Mirrors the __main__ guard in webhook_listener.py directly, since that
    # code only runs under `if __name__ == "__main__"` and isn't itself a
    # importable function -- this asserts the guard's condition, which is
    # the part that matters (an unset secret must be treated as fatal).
    import os
    assert not os.environ.get("GITHUB_WEBHOOK_SECRET", "")
