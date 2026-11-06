"""Webhook signature checks for deliveries this process accepts.

GitHub: HMAC-SHA256 in ``X-Hub-Signature-256``
(https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

Slack: version ``v0``, a colon, the timestamp, a colon, and the raw body, HMAC-SHA256,
prefixed with ``v0=`` (https://docs.slack.dev/authentication/verifying-requests-from-slack).
Requests older than five minutes are rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from kovca_examples.client import KovcaError


def verify_github_signature(secret: str, body: bytes, signature_header: str) -> None:
    if not secret:
        raise KovcaError(
            "Set GITHUB_WEBHOOK_SECRET before verifying a GitHub delivery. "
            "https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries"
        )
    header = (signature_header or "").strip()
    if not header:
        raise KovcaError("GitHub delivery is missing the X-Hub-Signature-256 header.")
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, header):
        raise KovcaError("GitHub webhook signature did not match. Nothing was sent to Kovca.")


def verify_slack_signature(
    secret: str,
    body: bytes,
    timestamp: str,
    signature_header: str,
    *,
    now: float | None = None,
) -> None:
    if not secret:
        raise KovcaError(
            "Set SLACK_SIGNING_SECRET before verifying a Slack delivery. "
            "https://docs.slack.dev/authentication/verifying-requests-from-slack"
        )
    stamp = (timestamp or "").strip()
    if not stamp or not stamp.isdigit():
        raise KovcaError("Slack delivery is missing a numeric X-Slack-Request-Timestamp header.")
    current = time.time() if now is None else now
    if abs(current - int(stamp)) > 60 * 5:
        raise KovcaError(
            "Slack request timestamp is more than five minutes from local time. "
            "The delivery was rejected so it cannot be replayed."
        )
    base = b"v0:" + stamp.encode("ascii") + b":" + body
    expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    header = (signature_header or "").strip()
    if not header or not hmac.compare_digest(expected, header):
        raise KovcaError("Slack request signature did not match. Nothing was sent to Kovca.")
