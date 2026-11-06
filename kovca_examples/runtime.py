"""Shared command-line helpers. No network and no secret storage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from kovca_examples.client import (
    DEFAULT_ORIGIN,
    KovcaClient,
    KovcaError,
    dump_public,
    validate_key,
    validate_origin,
)


def add_live_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--live",
        action="store_true",
        help="Contact Kovca. Refused unless --confirm-side-effects is also set.",
    )
    parser.add_argument(
        "--confirm-side-effects",
        action="store_true",
        help="Confirm that a live call may spend credits and run the published workflow's actions.",
    )


def workflow_settings(key_name: str) -> tuple[str, str | None, int | None, str | None]:
    origin = validate_origin(os.environ.get("KOVCA_ORIGIN", DEFAULT_ORIGIN))
    workflow_id = os.environ.get("KOVCA_WORKFLOW_ID", "").strip() or None
    if workflow_id is not None:
        from kovca_examples.client import validate_workflow_id

        validate_workflow_id(workflow_id)
    raw_version = os.environ.get("KOVCA_WORKFLOW_VERSION", "").strip()
    version: int | None = None
    if raw_version:
        if not raw_version.isdigit() or int(raw_version) < 1:
            raise KovcaError("KOVCA_WORKFLOW_VERSION must be a positive integer.")
        version = int(raw_version)
    api_key = os.environ.get(key_name, "").strip() or None
    return origin, workflow_id, version, api_key


def live_client(*, api_key: str | None, origin: str, purpose: str) -> KovcaClient:
    checked = validate_key(api_key, purpose=purpose)
    return KovcaClient(api_key=checked, origin=origin)


def read_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise KovcaError(f"File not found: {path}")
    return path.read_bytes()


def load_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KovcaError(f"{label} is not UTF-8.") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KovcaError(f"{label} is not JSON: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise KovcaError(f"{label} must be a JSON object.")
    return payload


def finish(payload: dict[str, Any], out: Path | None) -> int:
    rendered = dump_public(payload)
    sys.stdout.write(rendered)
    if out is not None:
        out.write_text(rendered, encoding="utf-8")
    return 0


def require_live(args: argparse.Namespace) -> None:
    if args.live and not args.confirm_side_effects:
        raise KovcaError(
            "Refusing --live without --confirm-side-effects. The default is a dry run "
            "that does not contact Kovca."
        )
