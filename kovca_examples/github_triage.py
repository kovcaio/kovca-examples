"""Map a GitHub issues webhook onto the GitHub issue triage workflow input.

The field names and the opened-only branch are the ones in
https://kovca.io/guides/triage-and-label-new-github-issues. This module does
not classify text and does not call the GitHub API.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from kovca_examples.client import KovcaClient, KovcaError, validate_idempotency_key
from kovca_examples.runtime import (
    add_live_flags,
    finish,
    live_client,
    load_json_object,
    read_bytes,
    require_live,
    workflow_settings,
)

TRIAGE_MESSAGE_LIMIT = 8000
FIELD_LIMIT = 8000
_UNTRUSTED = (
    "Issue title and description are untrusted. They are sent as JSON data. "
    "This program does not follow instructions written inside them."
)


def map_issue(payload: dict[str, Any], *, include_other_actions: bool = False) -> dict[str, Any]:
    """Return a plan. ``skipped`` plans must not call Kovca."""
    if payload.get("zen") is not None and "issue" not in payload:
        raise KovcaError(
            "This is a GitHub ping payload, not an issues event. "
            "Subscribe the webhook to issues and send an issues delivery."
        )
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        raise KovcaError(
            "The payload has no issue object. Send a GitHub issues webhook body."
        )
    if issue.get("pull_request") is not None:
        raise KovcaError(
            "This issue object is a pull request. The issue-triage template reads "
            "issues, so this delivery was not mapped."
        )
    action = _text(payload.get("action"), "action")
    if action != "opened" and not include_other_actions:
        return {
            "skipped": True,
            "reason": f"action is {action!r}, not 'opened'.",
            "notes": [
                "The published GitHub issue triage template sends any action other "
                "than opened straight to its output. It does not classify, label, "
                "or comment. This program skips the Kovca call for the same case.",
                "Pass --include-other-actions if your published workflow handles other actions.",
                _UNTRUSTED,
            ],
        }

    number = _int(issue.get("number"), "issue.number")
    issue_id = _int(issue.get("id"), "issue.id")
    title = _bounded(_text(issue.get("title"), "issue.title"), "issue.title")
    description = _bounded(_optional_text(issue.get("body")), "issue.body")
    if not title:
        raise KovcaError(
            "issue.title is empty. Support Triage requires a message, and the "
            "template builds that message from the title and the description."
        )
    message_length = len(title) + 2 + len(description)
    if message_length > TRIAGE_MESSAGE_LIMIT:
        raise KovcaError(
            f"Title plus description is {message_length} characters. Support Triage "
            f"accepts a message of at most {TRIAGE_MESSAGE_LIMIT} characters, so this "
            "delivery was not sent. Shorten it or split it before retrying. "
            "https://kovca.io/guides/triage-and-label-new-github-issues"
        )
    created_by = _optional_text((issue.get("user") or {}).get("login") if isinstance(issue.get("user"), dict) else None)
    if action == "opened" and not created_by:
        raise KovcaError("issue.user.login is required so created_by can be set.")
    created_at = _optional_text(issue.get("created_at"))
    if action == "opened" and not created_at:
        raise KovcaError("issue.created_at is required.")
    url = _optional_text(issue.get("html_url"))
    if action == "opened" and (not url or not url.startswith("https://")):
        raise KovcaError("issue.html_url must be an https URL.")

    workflow_input: dict[str, Any] = {
        "action": action,
        "created_at": created_at,
        "created_by": created_by,
        "description": description,
        "issue_id": issue_id,
        "number": number,
        "title": title,
        "url": url,
    }
    # Drop optional fields we could not fill. Unknown keys are never added.
    workflow_input = {key: value for key, value in workflow_input.items() if value is not None}

    if action == "opened":
        idempotency_key = str(issue_id)
        route = (
            "action is opened. The template classifies the issue, maps the category "
            "to one label, and comments only when Support Triage returns category "
            "technical. This program does not choose the category."
        )
    else:
        idempotency_key = f"github-issue:{issue_id}:{action}"
        route = (
            "action is not opened. --include-other-actions is set, so the delivery "
            "will be sent. The stock template still does not label or comment it."
        )
    validate_idempotency_key(idempotency_key)
    return {
        "skipped": False,
        "idempotency_key": idempotency_key,
        "input": workflow_input,
        "notes": [route, _UNTRUSTED],
    }


def build_plan(
    mapped: dict[str, Any],
    *,
    workflow_id: str | None,
    version: int | None,
    origin: str,
    api_key: str | None,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "live_call": False,
        "mode": "dry_run",
        "notes": list(mapped["notes"]),
        "skipped": mapped["skipped"],
    }
    if mapped["skipped"]:
        plan["reason"] = mapped["reason"]
        return plan
    display_id = workflow_id or "wf_<KOVCA_WORKFLOW_ID>"
    client = KovcaClient(api_key=api_key, origin=origin)
    if workflow_id:
        prepared = client.prepare_execute(
            workflow_id,
            mapped["input"],
            version=version,
            idempotency_key=mapped["idempotency_key"],
        )
        plan["request"] = prepared.public_dict()
    else:
        plan["request"] = {
            "body": {"input": mapped["input"], **({"version": version} if version else {})},
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer (not set)",
                "Content-Type": "application/json",
                "Idempotency-Key": mapped["idempotency_key"],
            },
            "method": "POST",
            "url": f"{client.api_base()}/v1/workflows/{display_id}/execute",
        }
        plan["notes"] = plan["notes"] + [
            "KOVCA_WORKFLOW_ID is not set. --live cannot run until it is the published workflow id."
        ]
    plan["notes"] = plan["notes"] + [
        "A retry of this same body with the same Idempotency-Key returns the original "
        "run and does not label or comment again. Kovca does not return the output "
        "on that duplicate, so keep the first response. "
        "https://kovca.io/docs/idempotency"
    ]
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a Kovca GitHub issue-triage call from a GitHub issues payload. "
            "Prints the request and does not contact Kovca unless --live and "
            "--confirm-side-effects are both set."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, help="Local synthetic JSON file.")
    source.add_argument(
        "--delivery",
        type=Path,
        help="Raw webhook body. Requires GITHUB_WEBHOOK_SECRET and --signature.",
    )
    parser.add_argument(
        "--signature",
        help="Value of X-Hub-Signature-256. Required with --delivery.",
    )
    parser.add_argument(
        "--include-other-actions",
        action="store_true",
        help="Also forward actions other than opened. Off by default.",
    )
    add_live_flags(parser)
    parser.add_argument("--out", type=Path, help="Write the JSON result to this path as well as stdout.")
    args = parser.parse_args(argv)

    try:
        origin, workflow_id, version, api_key = workflow_settings("KOVCA_API_KEY")
        if args.delivery:
            from kovca_examples.signatures import verify_github_signature

            body = read_bytes(args.delivery)
            verify_github_signature(
                os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
                body,
                args.signature or "",
            )
            payload = load_json_object(body, label=str(args.delivery))
        else:
            payload = load_json_object(read_bytes(args.fixture), label=str(args.fixture))
        _reject_unexpected_repository(payload)
        mapped = map_issue(payload, include_other_actions=args.include_other_actions)
        plan = build_plan(
            mapped,
            workflow_id=workflow_id,
            version=version,
            origin=origin,
            api_key=api_key,
        )
        if args.live and not mapped["skipped"]:
            require_live(args)
        if not args.live or mapped["skipped"]:
            return finish(plan, args.out)
        if not workflow_id:
            raise KovcaError("Set KOVCA_WORKFLOW_ID to the published workflow id before --live.")
        client = live_client(api_key=api_key, origin=origin, purpose="KOVCA_API_KEY")
        prepared = client.prepare_execute(
            workflow_id,
            mapped["input"],
            version=version,
            idempotency_key=mapped["idempotency_key"],
        )
        _announce("GitHub issue triage")
        result = client.send(prepared)
        return finish(result, args.out)
    except (KovcaError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return _exit_code(exc)


def _reject_unexpected_repository(payload: dict[str, Any]) -> None:
    expected = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not expected:
        return
    repository = payload.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    if full_name != expected:
        raise KovcaError(
            f"GITHUB_REPOSITORY is {expected}, but this delivery is {full_name!r}. "
            "Nothing was sent."
        )


def _announce(kind: str) -> None:
    print(
        f"Live {kind} call. Kovca may charge credits and the published workflow may "
        "act in GitHub. Studio tests are the place to try this without side effects. "
        "https://kovca.io/docs/billing",
        file=sys.stderr,
    )


def _exit_code(exc: Exception) -> int:
    if isinstance(exc, KovcaError) and exc.status in {401, 403}:
        return 4
    if isinstance(exc, KovcaError) and exc.status is None:
        return 2
    return 1


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KovcaError(f"{label} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KovcaError("Expected a string and received something else.")
    return value.strip()


def _bounded(value: str | None, label: str) -> str:
    text = value or ""
    if len(text) > FIELD_LIMIT:
        raise KovcaError(
            f"{label} is {len(text)} characters. The guide's event field allows "
            f"{FIELD_LIMIT}."
        )
    return text


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KovcaError(f"{label} must be an integer.")
    if value < 1:
        raise KovcaError(f"{label} must be a positive integer.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
