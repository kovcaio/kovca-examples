"""Map a Slack message event onto the Slack incident-escalation workflow input.

Field names come from
https://kovca.io/guides/escalate-urgent-slack-channel-messages. This module
does not post to Slack and does not decide whether a message is high priority.
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

TEXT_LIMIT = 8000
_UNTRUSTED = (
    "The Slack message is untrusted. It is sent as JSON data. This program does "
    "not follow instructions written inside it and does not post to Slack."
)


def map_message(payload: dict[str, Any], *, include_bots: bool = False) -> dict[str, Any]:
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        if not isinstance(challenge, str) or not challenge:
            raise KovcaError("Slack url_verification payload has no challenge string.")
        return {
            "skipped": True,
            "local_response": {"challenge": challenge},
            "reason": "Slack URL verification is answered locally. Kovca is not called.",
            "notes": [
                "Return this challenge from your request handler. Do not forward it to Kovca."
            ],
        }

    event = _event(payload)
    if event.get("type") not in {None, "message"}:
        raise KovcaError(
            "Only a Slack message event can start the incident template. "
            f"This event type is {event.get('type')!r}."
        )
    subtype = event.get("subtype")
    if isinstance(subtype, str) and subtype:
        return _skip(f"Slack subtype is {subtype!r}. Only a plain new message is forwarded.")
    if event.get("bot_id") and not include_bots:
        return _skip(
            "The message has bot_id. Bot messages are skipped so a workflow that "
            "posts back into the channel cannot loop. Pass --include-bots to forward it. "
            "The stock Studio template does not apply this filter itself."
        )

    text = event.get("text")
    if not isinstance(text, str) or not text.strip():
        raise KovcaError(
            "The message has no text. Support Triage requires a message, and a "
            "file-only Slack message is in that group. Nothing was sent."
        )
    text = text.strip()
    if len(text) > TEXT_LIMIT:
        raise KovcaError(
            f"The message is {len(text)} characters. Support Triage accepts at most "
            f"{TEXT_LIMIT}, which is also the limit on the trigger's text field."
        )
    timestamp = event.get("ts")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise KovcaError("The message has no ts. That value is the idempotency key.")
    timestamp = timestamp.strip()
    channel = event.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise KovcaError("The message has no channel id.")
    channel = channel.strip()

    expected_channel = os.environ.get("SLACK_CHANNEL_ID", "").strip()
    if expected_channel and channel != expected_channel:
        return _skip(
            f"SLACK_CHANNEL_ID is {expected_channel}, but this message is in {channel}."
        )

    workflow_input: dict[str, Any] = {"channel": channel, "text": text, "ts": timestamp}
    for source_key, target_key in (
        ("channel_type", "channel_type"),
        ("user", "user"),
    ):
        value = event.get(source_key)
        if isinstance(value, str) and value.strip():
            workflow_input[target_key] = value.strip()
    team_id = event.get("team_id") or event.get("team") or payload.get("team_id")
    if isinstance(team_id, str) and team_id.strip():
        workflow_input["team_id"] = team_id.strip()
    for key in ("attachments", "blocks", "files"):
        value = event.get(key)
        if isinstance(value, list):
            workflow_input[key] = value

    notes = [
        "The template classifies text with Support Triage and, only when priority "
        "is high, opens an incident, posts to on-call, and adds a reaction. This "
        "program does not choose the priority and does not call Slack.",
        _UNTRUSTED,
    ]
    thread_ts = event.get("thread_ts")
    if isinstance(thread_ts, str) and thread_ts and thread_ts != timestamp:
        notes.append(
            "This event is a thread reply. The stock template leaves its thread "
            "filter unset, so a reply is classified on its own text."
        )
    idempotency_key = timestamp
    validate_idempotency_key(idempotency_key)
    return {
        "skipped": False,
        "idempotency_key": idempotency_key,
        "input": workflow_input,
        "notes": notes,
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
        if "local_response" in mapped:
            plan["local_response"] = mapped["local_response"]
        return plan
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
            "url": f"{client.api_base()}/v1/workflows/wf_<KOVCA_WORKFLOW_ID>/execute",
        }
        plan["notes"].append(
            "KOVCA_WORKFLOW_ID is not set. --live cannot run until it is the published workflow id."
        )
    plan["notes"].append(
        "Use the message ts as the Idempotency-Key. A retry returns the original "
        "run instead of opening a second incident, and the duplicate response has "
        "no output. Keep the first response. https://kovca.io/docs/idempotency"
    )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a Kovca Slack incident-escalation call. Prints the request "
            "and does not contact Kovca unless --live and --confirm-side-effects are both set."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, help="Local synthetic JSON file.")
    source.add_argument(
        "--delivery",
        type=Path,
        help="Raw Slack body. Requires SLACK_SIGNING_SECRET, --timestamp, and --signature.",
    )
    parser.add_argument("--timestamp", help="Value of X-Slack-Request-Timestamp.")
    parser.add_argument("--signature", help="Value of X-Slack-Signature.")
    parser.add_argument(
        "--include-bots",
        action="store_true",
        help="Forward messages that have bot_id. Off by default.",
    )
    add_live_flags(parser)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    try:
        origin, workflow_id, version, api_key = workflow_settings("KOVCA_API_KEY")
        if args.delivery:
            from kovca_examples.signatures import verify_slack_signature

            body = read_bytes(args.delivery)
            verify_slack_signature(
                os.environ.get("SLACK_SIGNING_SECRET", ""),
                body,
                args.timestamp or "",
                args.signature or "",
            )
            payload = load_json_object(body, label=str(args.delivery))
        else:
            payload = load_json_object(read_bytes(args.fixture), label=str(args.fixture))
        mapped = map_message(payload, include_bots=args.include_bots)
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
        print(
            "Live Slack incident call. Kovca may charge credits. If the published "
            "workflow's priority branch runs, it can open an incident and post to Slack.",
            file=sys.stderr,
        )
        return finish(client.send(prepared), args.out)
    except (KovcaError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 4 if isinstance(exc, KovcaError) and exc.status in {401, 403} else (
            2 if isinstance(exc, KovcaError) and exc.status is None else 1
        )


def _event(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("event"), dict):
        return payload["event"]
    if payload.get("text") is not None or payload.get("ts") is not None:
        return payload
    raise KovcaError(
        "The payload has no Slack event. Send an Events API envelope or the message object."
    )


def _skip(reason: str) -> dict[str, Any]:
    return {"skipped": True, "reason": reason, "notes": [_UNTRUSTED]}


if __name__ == "__main__":
    raise SystemExit(main())
