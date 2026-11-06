"""Client for the public Kovca HTTP API.

The contract lives in the docs, not in this file:

- https://kovca.io/docs/api-reference
- https://kovca.io/docs/workflows
- https://kovca.io/docs/runs
- https://kovca.io/docs/idempotency
- https://kovca.io/docs/errors
- https://kovca.io/docs/approvals

Network calls happen only through ``KovcaClient.send``. Command-line tools in
this repository require ``--live`` before they call ``send``.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

BODY_LIMIT = 16_384
RESPONSE_LIMIT = 1_048_576
DEFAULT_ORIGIN = "https://kovca.io"
USER_AGENT = "kovca-examples/0.1"

RETRYABLE_CODES = frozenset(
    {"RATE_LIMITED", "INTERNAL_ERROR", "SERVICE_UNAVAILABLE", "TIMEOUT"}
)


class KovcaError(Exception):
    """A request that this client refused, or an error Kovca returned."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retryable = retryable
        self.payload = dict(payload or {})


def redact_key(key: str | None) -> str:
    """Return the dashboard prefix of a workspace key, never the full value."""
    if not key:
        return "(not set)"
    stripped = key.strip()
    if stripped.startswith("kvt_"):
        return "kvt_…"
    if stripped.startswith("kvc_") and len(stripped) >= 12:
        return stripped[:12] + "…"
    return "(redacted)"


def validate_key(key: str | None, *, purpose: str) -> str:
    """Accept a workspace ``kvc_`` key and reject placeholders and ``kvt_`` keys."""
    if key is None or not str(key).strip():
        raise KovcaError(
            f"Set {purpose} to a workspace API key before a live call. "
            "Keys start with kvc_. Dry-run does not need one. "
            "https://kovca.io/docs/api-keys"
        )
    value = str(key).strip()
    if value.startswith("kvt_"):
        raise KovcaError(
            f"{purpose} is an instant workflow key (kvt_). Those keys cannot call "
            "the workflow API or the MCP server. Create a workspace key on the "
            "API keys page. https://kovca.io/docs/mcp"
        )
    if (
        "..." in value
        or "REPLACE" in value
        or "YOUR" in value
        or value in {"kvc_", "kvc_..."}
    ):
        raise KovcaError(
            f"{purpose} is still a placeholder. Put the key in the environment "
            "for one command. Do not commit it."
        )
    suffix = value[4:] if value.startswith("kvc_") else ""
    if (
        not value.startswith("kvc_")
        or not 20 <= len(suffix) <= 80
        or any(ch not in _URL_SAFE for ch in suffix)
    ):
        raise KovcaError(
            f"{purpose} must be a workspace key: kvc_ and then URL-safe characters. "
            "The dashboard shows the first 12 characters of a key after you create it."
        )
    return value


def validate_idempotency_key(key: str) -> str:
    if not isinstance(key, str) or not 1 <= len(key) <= 255:
        raise KovcaError("Idempotency-Key must be 1 to 255 characters.")
    if any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in key):
        raise KovcaError(
            "Idempotency-Key must be visible ASCII, with no spaces or control characters. "
            "https://kovca.io/docs/idempotency"
        )
    return key


def validate_origin(origin: str) -> str:
    value = (origin or "").strip().rstrip("/")
    if value == DEFAULT_ORIGIN:
        return value
    if value.startswith("http://127.0.0.1:") or value.startswith("http://localhost:"):
        host = value.split("://", 1)[1]
        port = host.split(":", 1)[1]
        if port.isdigit() and "@" not in value:
            return value
    if (
        value.startswith("https://")
        and " " not in value
        and "@" not in value
        and "\\" not in value
    ):
        return value
    raise KovcaError(
        "KOVCA_ORIGIN must be https://kovca.io, another explicit https origin, "
        "or http://127.0.0.1:<port> for a local test server."
    )


def validate_workflow_id(workflow_id: str) -> str:
    if not _prefixed_id(workflow_id, "wf_"):
        raise KovcaError(
            "Workflow id must look like wf_.... Copy it from the Studio after you publish. "
            "https://kovca.io/docs/workflows"
        )
    return workflow_id


def validate_run_id(run_id: str) -> str:
    if not _prefixed_id(run_id, "run_"):
        raise KovcaError("Run id must look like run_.... https://kovca.io/docs/runs")
    return run_id


def validate_step_id(step_id: str) -> str:
    if (
        not isinstance(step_id, str)
        or not 1 <= len(step_id) <= 64
        or any(ch not in _ID_CHARS for ch in step_id)
    ):
        raise KovcaError(
            "Approval step id must be 1 to 64 letters, digits, underscores, or hyphens."
        )
    return step_id


@dataclass(frozen=True)
class PreparedCall:
    """A request that can be printed or sent. The API key is already redacted."""

    method: str
    url: str
    body: dict[str, Any] | None
    body_bytes: bytes
    idempotency_key: str | None
    key_redacted: str

    def public_dict(self) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.key_redacted}",
        }
        if self.method != "GET":
            headers["Content-Type"] = "application/json"
        if self.idempotency_key is not None:
            headers["Idempotency-Key"] = self.idempotency_key
        return {
            "method": self.method,
            "url": self.url,
            "headers": headers,
            "body": self.body,
        }


class KovcaClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        origin: str = DEFAULT_ORIGIN,
        sleeper: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self.api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self.origin = validate_origin(origin)
        self.sleeper = sleeper
        self.timeout = timeout
        if max_attempts < 1:
            raise KovcaError("max_attempts must be at least 1.")
        self.max_attempts = max_attempts

    def api_base(self) -> str:
        return self.origin + "/api"

    def prepare_execute(
        self,
        workflow_id: str,
        workflow_input: Mapping[str, Any],
        *,
        version: int | None = None,
        idempotency_key: str | None = None,
    ) -> PreparedCall:
        validate_workflow_id(workflow_id)
        if not isinstance(workflow_input, Mapping):
            raise KovcaError("Workflow input must be a JSON object.")
        body: dict[str, Any] = {"input": dict(workflow_input)}
        if version is not None:
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise KovcaError("version must be a positive integer.")
            body["version"] = version
        raw = encode_body(body)
        if len(raw) > BODY_LIMIT:
            raise KovcaError(
                f"Request body is {len(raw)} bytes. Kovca rejects bodies over "
                f"{BODY_LIMIT} bytes with 413 PAYLOAD_TOO_LARGE."
            )
        if idempotency_key is not None:
            idempotency_key = validate_idempotency_key(idempotency_key)
        return PreparedCall(
            method="POST",
            url=f"{self.api_base()}/v1/workflows/{workflow_id}/execute",
            body=body,
            body_bytes=raw,
            idempotency_key=idempotency_key,
            key_redacted=redact_key(self.api_key),
        )

    def prepare_run(self, run_id: str) -> PreparedCall:
        validate_run_id(run_id)
        return PreparedCall(
            method="GET",
            url=f"{self.api_base()}/v1/runs/{run_id}",
            body=None,
            body_bytes=b"",
            idempotency_key=None,
            key_redacted=redact_key(self.api_key),
        )

    def prepare_decision(
        self,
        run_id: str,
        step_id: str,
        decision: str,
        fields: Mapping[str, Any] | None = None,
    ) -> PreparedCall:
        validate_run_id(run_id)
        validate_step_id(step_id)
        if decision not in {"approved", "rejected"}:
            raise KovcaError("decision must be approved or rejected.")
        body: dict[str, Any] = {"decision": decision}
        if fields is not None:
            if not isinstance(fields, Mapping):
                raise KovcaError("decision fields must be a JSON object.")
            cleaned: dict[str, Any] = {}
            for key, value in fields.items():
                if not isinstance(key, str) or not key:
                    raise KovcaError("Each decision field name must be a non-empty string.")
                cleaned[key] = value
            body["fields"] = cleaned
        raw = encode_body(body)
        if len(raw) > BODY_LIMIT:
            raise KovcaError(
                f"Decision body is {len(raw)} bytes, over the {BODY_LIMIT} byte limit."
            )
        return PreparedCall(
            method="POST",
            url=f"{self.api_base()}/v1/runs/{run_id}/waits/{step_id}/decision",
            body=body,
            body_bytes=raw,
            idempotency_key=None,
            key_redacted=redact_key(self.api_key),
        )

    def send(self, prepared: PreparedCall) -> dict[str, Any]:
        """Perform a live HTTP call. Callers must opt in before they get here."""
        key = validate_key(self.api_key, purpose="API key")
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data: bytes | None
        if prepared.method == "GET":
            data = None
        else:
            headers["Content-Type"] = "application/json"
            data = prepared.body_bytes
        if prepared.idempotency_key is not None:
            headers["Idempotency-Key"] = prepared.idempotency_key

        last_error: KovcaError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, payload, response_headers = _request(
                    prepared.method,
                    prepared.url,
                    headers,
                    data,
                    self.timeout,
                )
            except KovcaError as exc:
                last_error = exc
                if attempt >= self.max_attempts or not exc.retryable:
                    raise
                self.sleeper(_backoff(attempt))
                continue

            if status in (200, 202):
                if not isinstance(payload, dict):
                    raise KovcaError(
                        "Kovca returned JSON that is not an object.",
                        status=status,
                    )
                return payload

            error = _error_from_response(status, payload)
            last_error = error
            if attempt >= self.max_attempts or not error.retryable:
                raise error
            self.sleeper(_retry_delay(response_headers, attempt))
        assert last_error is not None
        raise last_error


def encode_body(body: Mapping[str, Any]) -> bytes:
    """Stable JSON bytes so a retry is the same request as the first attempt."""
    return json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def dump_public(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_URL_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)
_ID_CHARS = _URL_SAFE


def _prefixed_id(value: str, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    suffix = value[len(prefix) :]
    return 1 <= len(suffix) <= 80 and all(ch in _ID_CHARS for ch in suffix)


def _error_from_response(status: int, payload: Any) -> KovcaError:
    code: str | None = None
    message = f"Kovca returned HTTP {status}."
    request_id = None
    body: dict[str, Any] = payload if isinstance(payload, dict) else {}
    if isinstance(body.get("request_id"), str):
        request_id = body["request_id"]
    error = body.get("error")
    if isinstance(error, dict):
        if isinstance(error.get("code"), str):
            code = error["code"]
        if isinstance(error.get("message"), str) and error["message"].strip():
            message = error["message"].strip()
    hint = _hint(status, code)
    text = message if message.endswith(".") else message + "."
    if hint:
        text = f"{text} {hint}"
    if request_id:
        text = f"{text} request_id={request_id}."
    return KovcaError(
        text,
        status=status,
        code=code,
        request_id=request_id,
        retryable=_is_retryable(status, code),
        payload=body,
    )


def _hint(status: int | None, code: str | None) -> str:
    if code == "UNAUTHORIZED" or status == 401:
        return (
            "The key is missing, malformed, unknown, or revoked, or it is an instant "
            "kvt_ key. https://kovca.io/docs/api-keys"
        )
    if code == "INSUFFICIENT_ACCESS":
        return (
            "Run access can execute a published workflow. Author access is required "
            "to edit drafts over MCP. Publish access is required to publish, change "
            "automations, and answer approvals. https://kovca.io/docs/api-keys"
        )
    if code == "DECISIONS_NOT_ALLOWED":
        return (
            "This approval does not allow API decisions. A person uses the approval "
            "link or the Approvals inbox, or the step turns on Allow API decisions. "
            "https://kovca.io/docs/approvals"
        )
    if code == "WORKFLOW_DISABLED":
        return "The workflow is turned off in the Studio. Nothing ran and nothing was billed."
    if code == "IDEMPOTENCY_KEY_CONFLICT":
        return (
            "That Idempotency-Key was already used with a different request within "
            "24 hours. https://kovca.io/docs/idempotency"
        )
    if code == "NOT_FOUND":
        return "No workflow, version, or run matched this key. Runs are kept for 30 days."
    if code == "USAGE_LIMIT_EXCEEDED":
        return (
            "The workspace is out of free credits or at its spending cap. Do not retry "
            "until that changes. https://kovca.io/docs/billing"
        )
    if code == "BILLING_PAYMENT_REQUIRED":
        return (
            "Paid usage is blocked. Unused free credits still work. "
            "https://kovca.io/docs/billing"
        )
    if code == "ENDPOINT_REMOVED":
        return "Call POST /v1/workflows/{workflow_id}/execute. The old per-node routes are gone."
    if code == "WAIT_CLOSED":
        return "That approval was already answered, expired, or the run ended."
    if code == "INVALID_REQUEST":
        return (
            "The body does not match the published workflow. Unknown fields are "
            "rejected. https://kovca.io/docs/api-reference"
        )
    if code == "ACTION_TIMEOUT":
        return (
            "The action's destination may already have acted. Kovca does not re-run "
            "a finished workflow when the same Idempotency-Key is sent again. "
            "https://kovca.io/docs/actions"
        )
    if code == "PAYLOAD_TOO_LARGE":
        return f"The body limit is {BODY_LIMIT} bytes."
    return ""


def _is_retryable(status: int, code: str | None) -> bool:
    """Retry only where the errors doc says a retry is safe.

    ``ACTION_TIMEOUT`` is also HTTP 504, and the destination may have acted, so
    it is not retried automatically. ``USAGE_LIMIT_EXCEEDED`` is HTTP 429 and
    must not be retried.
    """
    if code == "USAGE_LIMIT_EXCEEDED" or code == "ACTION_TIMEOUT":
        return False
    if status == 429 and code == "RATE_LIMITED":
        return True
    if status == 500 and code in {None, "INTERNAL_ERROR"}:
        return True
    if status == 503 and code in {None, "SERVICE_UNAVAILABLE"}:
        return True
    if status == 504 and code in {None, "TIMEOUT"}:
        return True
    return False


def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw:
        try:
            seconds = float(str(raw).strip())
        except ValueError:
            seconds = -1
        if 0 <= seconds <= 5:
            return seconds
    return _backoff(attempt)


def _backoff(attempt: int) -> float:
    return min(4.0, 0.25 * (2 ** (attempt - 1)))


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise KovcaError(
            f"The origin returned HTTP {code} redirecting to {newurl}. "
            "This client does not follow redirects.",
            status=code,
            retryable=False,
        )


def _request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    data: bytes | None,
    timeout: float,
) -> tuple[int, Any, dict[str, str]]:
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_RefuseRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            raw = response.read(RESPONSE_LIMIT + 1)
            response_headers = {key: value for key, value in response.headers.items()}
    except KovcaError:
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = {key: value for key, value in exc.headers.items()} if exc.headers else {}
        if 300 <= status < 400:
            location = response_headers.get("Location") or response_headers.get("location")
            raise KovcaError(
                f"The origin returned HTTP {status} redirecting to {location}. "
                "This client does not follow redirects.",
                status=status,
            ) from None
        raw = exc.read(RESPONSE_LIMIT + 1)
    except urllib.error.URLError as exc:
        reason = exc.reason
        retryable = isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
        raise KovcaError(f"Could not reach Kovca: {reason}", retryable=retryable) from None
    if len(raw) > RESPONSE_LIMIT:
        raise KovcaError("The response was larger than 1 MB and was discarded.")
    if not raw:
        return status, {}, response_headers
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise KovcaError(
            f"The response was not JSON (HTTP {status}).",
            status=status,
        ) from None
    return status, payload, response_headers
