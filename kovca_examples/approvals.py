"""Call a published workflow that pauses on the documented approval step.

The graph fragment checked in beside the example is the one published at
https://kovca.io/docs/approvals. It is not a ``kovca workflows apply`` file.
This module submits ``message`` and, only after two explicit flags, can send
an approval decision with a separate Publish key.
"""

from __future__ import annotations

import argparse
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

MESSAGE_LIMIT = 8000
DOCUMENTED_CATEGORIES = ("billing", "technical", "account", "product", "other")
_UNTRUSTED = (
    "The message is untrusted case text. It is sent as JSON data. This program "
    "does not treat it as instructions."
)


def map_case(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise KovcaError(
            "The case needs a non-empty message string. The approval graph on "
            "https://kovca.io/docs/approvals declares that single input field."
        )
    message = message.strip()
    if len(message) > MESSAGE_LIMIT:
        raise KovcaError(
            f"message is {len(message)} characters. Support Triage accepts at most "
            f"{MESSAGE_LIMIT}."
        )
    extra = sorted(set(payload) - {"message"})
    if extra:
        raise KovcaError(
            "Unknown case fields: "
            + ", ".join(extra)
            + ". The documented approval input declares message only. Unknown "
            "fields are rejected by the workflow API."
        )
    return {
        "input": {"message": message},
        "notes": [
            "A published workflow with wait:approval@1 returns 202 and status "
            "waiting. The approval link is not in that response. A person answers "
            "from the link or the Approvals inbox unless the step allows API decisions.",
            _UNTRUSTED,
        ],
    }


def parse_fields(pairs: list[str], *, check_category: bool) -> dict[str, str]:
    fields: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise KovcaError("Each --field value must look like key=value.")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in key):
            raise KovcaError(
                "Field names must be lowercase letters, digits, or underscores."
            )
        if len(value) > 500:
            raise KovcaError(f"Field {key} is longer than 500 characters.")
        if key in fields:
            raise KovcaError(f"Field {key} was given twice.")
        fields[key] = value
    if check_category and "category" in fields and fields["category"] not in DOCUMENTED_CATEGORIES:
        allowed = ", ".join(DOCUMENTED_CATEGORIES)
        raise KovcaError(
            f"category must be one of: {allowed}. That is the form on the documented "
            "approval step. Pass --no-category-check for a workflow with a different form."
        )
    return fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Submit a case to a published approval workflow, or prepare an "
            "approval decision. Network calls require explicit flags."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="Dry-run or execute a case.")
    submit.add_argument("--fixture", type=Path, required=True)
    submit.add_argument(
        "--idempotency-key",
        help="Required with --live. Retries of the same case must reuse it.",
    )
    add_live_flags(submit)
    submit.add_argument("--out", type=Path)

    decide = sub.add_parser(
        "decide",
        help="Dry-run or send POST /v1/runs/{run_id}/waits/{step_id}/decision.",
    )
    decide.add_argument("--run-id", required=True)
    decide.add_argument("--step", default="review")
    decide.add_argument("--decision", required=True, choices=("approved", "rejected"))
    decide.add_argument("--field", action="append", default=[])
    decide.add_argument(
        "--no-category-check",
        action="store_true",
        help="Do not check category against the documented approval form.",
    )
    add_live_flags(decide)
    decide.add_argument("--out", type=Path)

    status = sub.add_parser("status", help="Dry-run or GET a run. This does not decide it.")
    status.add_argument("--run-id", required=True)
    status.add_argument(
        "--live",
        action="store_true",
        help="Send GET /v1/runs/{run_id}. This reads metadata and does not continue the run.",
    )
    status.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "submit":
            return _submit(args)
        if args.command == "decide":
            return _decide(args)
        return _status(args)
    except (KovcaError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, KovcaError) and exc.status in {401, 403}:
            return 4
        if isinstance(exc, KovcaError) and exc.status is None:
            return 2
        return 1


def _submit(args: argparse.Namespace) -> int:
    origin, workflow_id, version, api_key = workflow_settings("KOVCA_API_KEY")
    payload = load_json_object(read_bytes(args.fixture), label=str(args.fixture))
    mapped = map_case(payload)
    idempotency_key = args.idempotency_key
    if idempotency_key is not None:
        idempotency_key = validate_idempotency_key(idempotency_key)
    client = KovcaClient(api_key=api_key, origin=origin)
    plan: dict[str, Any] = {
        "live_call": False,
        "mode": "dry_run",
        "notes": list(mapped["notes"]),
    }
    if workflow_id:
        plan["request"] = client.prepare_execute(
            workflow_id,
            mapped["input"],
            version=version,
            idempotency_key=idempotency_key,
        ).public_dict()
    else:
        body: dict[str, Any] = {"input": mapped["input"]}
        if version is not None:
            body["version"] = version
        plan["request"] = {
            "body": body,
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer (not set)",
                "Content-Type": "application/json",
            },
            "method": "POST",
            "url": f"{client.api_base()}/v1/workflows/wf_<KOVCA_WORKFLOW_ID>/execute",
        }
        if idempotency_key is not None:
            plan["request"]["headers"]["Idempotency-Key"] = idempotency_key
        plan["notes"].append("KOVCA_WORKFLOW_ID is not set.")
    if idempotency_key is None:
        plan["notes"].append(
            "No Idempotency-Key was set. Pass --idempotency-key before a live "
            "submit so a retry cannot open a second waiting run. "
            "https://kovca.io/docs/idempotency"
        )
    else:
        plan["notes"].append(
            "A retry with this same key and body returns the original run and "
            "does not start another one. The duplicate response omits the output. "
            "https://kovca.io/docs/idempotency"
        )
    if not args.live:
        return finish(plan, args.out)
    require_live(args)
    if not workflow_id:
        raise KovcaError("Set KOVCA_WORKFLOW_ID before --live.")
    if idempotency_key is None:
        raise KovcaError(
            "Pass --idempotency-key with --live. A submit without one can open "
            "a second waiting run if you retry."
        )
    sender = live_client(api_key=api_key, origin=origin, purpose="KOVCA_API_KEY")
    prepared = sender.prepare_execute(
        workflow_id,
        mapped["input"],
        version=version,
        idempotency_key=idempotency_key,
    )
    print(
        "Live approval submit. A Run key is enough. The call may spend credits and "
        "pause on an approval. It does not approve the case.",
        file=sys.stderr,
    )
    return finish(sender.send(prepared), args.out)


def _decide(args: argparse.Namespace) -> int:
    origin, _workflow_id, _version, api_key = workflow_settings("KOVCA_PUBLISH_API_KEY")
    fields = parse_fields(args.field, check_category=not args.no_category_check)
    client = KovcaClient(api_key=api_key, origin=origin)
    prepared = client.prepare_decision(args.run_id, args.step, args.decision, fields or None)
    plan = {
        "live_call": False,
        "mode": "dry_run",
        "notes": [
            "This uses KOVCA_PUBLISH_API_KEY and does not fall back to KOVCA_API_KEY. "
            "Decide needs Publish access, and the approval step must allow API decisions. "
            "https://kovca.io/docs/approvals",
            "The first answer wins. Sending this continues the published workflow, "
            "including any action on the approved or rejected branch.",
        ],
        "request": prepared.public_dict(),
    }
    if not args.live:
        return finish(plan, args.out)
    require_live(args)
    sender = live_client(api_key=api_key, origin=origin, purpose="KOVCA_PUBLISH_API_KEY")
    prepared = sender.prepare_decision(args.run_id, args.step, args.decision, fields or None)
    print(
        "Live approval decision. This continues the waiting run if the step allows "
        "API decisions. It is not reversible from this program.",
        file=sys.stderr,
    )
    return finish(sender.send(prepared), args.out)


def _status(args: argparse.Namespace) -> int:
    origin, _workflow_id, _version, api_key = workflow_settings("KOVCA_API_KEY")
    client = KovcaClient(api_key=api_key, origin=origin)
    prepared = client.prepare_run(args.run_id)
    if not args.live:
        return finish(
            {
                "live_call": False,
                "mode": "dry_run",
                "notes": [
                    "GET /v1/runs/{id} returns metadata only. Inputs, outputs, and "
                    "approval answers are not stored. https://kovca.io/docs/runs"
                ],
                "request": prepared.public_dict(),
            },
            args.out,
        )
    sender = live_client(api_key=api_key, origin=origin, purpose="KOVCA_API_KEY")
    return finish(sender.send(sender.prepare_run(args.run_id)), args.out)


if __name__ == "__main__":
    raise SystemExit(main())
