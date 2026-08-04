"""Minimal GitHub webhook listener — replaces the old push-triggered local
SSH deploy mechanism (a git pre-push hook that SSHed into the VM from the
developer's own machine) with a server-side listener that GitHub notifies
directly on push.

Why this exists: the pre-push-hook design meant "deploy this app" had two
independent, hand-maintained implementations — the hook's own copy of the
git-pull/pip-install/systemd-restart command chain, and deploy.sh's copy —
which kept drifting apart (see CLAUDE.md's "Deploy pipeline systemd parity
audit"). This listener removes the duplication structurally: it does exactly
one thing — verify the request is genuinely from GitHub, then invoke
`bash deploy.sh` — and contains no deploy logic of its own.
deploy.sh's deploy_steps() function is the only place the actual deploy
steps live.

Deliberately stdlib-only (http.server + hmac + hashlib), no Flask/FastAPI
dependency — this is a tiny, single-endpoint service and adding a web
framework to requirements.txt for it isn't warranted.

Run as a systemd service (investment-research-webhook.service), behind
Caddy's reverse proxy (see Caddyfile's /webhook/deploy route) so it's
reachable over HTTPS the same way the main dashboard is, without listening
on a public port directly itself.

Usage (systemd handles this automatically):
    python3.11 webhook_listener.py

Required env var: GITHUB_WEBHOOK_SECRET (set in the systemd secrets
drop-in, same as T212_API_KEY/etc — see CLAUDE.md's "Managing server
credentials"). The listener refuses to start without it — an unauthenticated
deploy trigger reachable from the internet is not an acceptable fallback.
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
DEPLOY_SCRIPT = APP_DIR / "deploy.sh"
TARGET_REF = "refs/heads/main"


def verify_signature(secret: str, payload: bytes, signature_header: str | None) -> bool:
    """True iff signature_header is a valid `sha256=<hex>` HMAC-SHA256 of
    payload keyed with secret, per GitHub's webhook signing scheme
    (https://docs.github.com/webhooks/using-webhooks/validating-webhook-deliveries).
    Constant-time comparison (hmac.compare_digest) — a timing side-channel
    on this check would let an attacker recover the secret byte-by-byte."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    their_digest = signature_header[len("sha256="):]
    our_digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(their_digest, our_digest)


def should_trigger_deploy(event_type: str, payload: dict) -> bool:
    """True only for a push to the main branch. GitHub also sends a `ping`
    event when a webhook is first created (and pushes for other branches,
    if the webhook is ever broadened past its current push-only, no-branch-
    filter config) — none of those should trigger a deploy."""
    if event_type != "push":
        return False
    return payload.get("ref") == TARGET_REF


def trigger_deploy(deploy_script: Path = DEPLOY_SCRIPT) -> subprocess.Popen:
    """Spawns `bash deploy.sh` in the background and returns immediately —
    GitHub expects a webhook response within ~10s, and a full deploy (pip
    install, service restarts, verification) can take longer than that.
    Output is inherited from this process's own stdout/stderr, which
    systemd sends to the journal (StandardOutput=journal in
    investment-research-webhook.service) — `journalctl -u
    investment-research-webhook -f` shows deploy progress live, the same
    way every other job's logs already work in this project.

    No reimplementation of any deploy step here — this is the ONLY thing
    this listener does in response to a verified push.
    """
    logger.info("Triggering deploy: bash %s", deploy_script)
    return subprocess.Popen(["bash", str(deploy_script)], cwd=str(APP_DIR))


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "Sp500InvestWebhookListener/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 — override BaseHTTPRequestHandler's default stderr logging
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, "ok")
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path != "/webhook/deploy":
            self._send(404, "not found")
            return

        secret = self.server.webhook_secret  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0"))
        payload_bytes = self.rfile.read(length) if length else b""
        signature_header = self.headers.get("X-Hub-Signature-256")

        if not verify_signature(secret, payload_bytes, signature_header):
            logger.warning(
                "Rejected webhook request from %s — invalid or missing signature.",
                self.address_string(),
            )
            self._send(401, "invalid signature")
            return

        event_type = self.headers.get("X-GitHub-Event", "")

        if event_type == "ping":
            logger.info("Received GitHub webhook ping — signature verified OK, no deploy triggered.")
            self._send(200, "pong")
            return

        try:
            payload = json.loads(payload_bytes.decode("utf-8")) if payload_bytes else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, "malformed JSON payload")
            return

        if not should_trigger_deploy(event_type, payload):
            ref = payload.get("ref", "<none>")
            logger.info("Ignoring %s event (ref=%s) — not a push to %s.", event_type, ref, TARGET_REF)
            self._send(200, f"ignored: event={event_type} ref={ref}")
            return

        logger.info(
            "Verified push to %s (commit %s) — triggering deploy.",
            TARGET_REF,
            payload.get("after", "<unknown>"),
        )
        trigger_deploy()
        self._send(202, "deploy triggered")


def run_server(port: int, secret: str) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), WebhookHandler)
    server.webhook_secret = secret  # type: ignore[attr-defined]
    logger.info("Webhook listener running on 127.0.0.1:%s (POST /webhook/deploy, GET /health)", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.error(
            "GITHUB_WEBHOOK_SECRET is not set — refusing to start. An unauthenticated "
            "deploy trigger is not an acceptable fallback. Set it in the systemd secrets "
            "drop-in (see CLAUDE.md's 'Managing server credentials')."
        )
        sys.exit(1)

    listen_port = int(os.environ.get("WEBHOOK_LISTEN_PORT", "8600"))
    run_server(listen_port, webhook_secret)
