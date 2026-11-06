"""Offline tests for the three example commands."""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from kovca_examples.approvals import main as approval_main
from kovca_examples.github_triage import main as github_main
from kovca_examples.slack_incidents import main as slack_main
from tests.keys import TEST_PUBLISH_KEY, TEST_RUN_KEY
from tests.mock_kovca import MockKovca

ROOT = Path(__file__).resolve().parents[1]
GITHUB = ROOT / "examples" / "github-issue-triage"
SLACK = ROOT / "examples" / "slack-incident-alerts"
APPROVAL = ROOT / "examples" / "ai-approval-workflows"


class ExampleCommandTests(unittest.TestCase):
    def test_github_dry_run_maps_the_documented_fields(self) -> None:
        with scoped_env({}):
            code, out, err = run(
                github_main,
                ["--fixture", str(GITHUB / "fixtures" / "issue-opened.webhook.json")],
            )
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        plan = json.loads(out)
        self.assertFalse(plan["live_call"])
        self.assertFalse(plan["skipped"])
        body = plan["request"]["body"]["input"]
        self.assertEqual(
            body,
            {
                "action": "opened",
                "created_at": "2026-11-02T14:03:00Z",
                "created_by": "maria-chen",
                "description": "Since the 1.42 release, the cart total does not include the shipping fee for orders to Canada.",
                "issue_id": 3092845511,
                "number": 1287,
                "title": "Cart total missing shipping for CA orders",
                "url": "https://github.com/acme/storefront/issues/1287",
            },
        )
        self.assertEqual(plan["request"]["headers"]["Idempotency-Key"], "3092845511")
        self.assertNotIn("repository", body)
        self.assertIn("untrusted", " ".join(plan["notes"]).lower())

    def test_github_edited_issue_is_not_sent(self) -> None:
        with scoped_env({}):
            code, out, _err = run(
                github_main,
                ["--fixture", str(GITHUB / "fixtures" / "issue-edited.webhook.json")],
            )
        plan = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(plan["skipped"])
        self.assertNotIn("request", plan)

    def test_untrusted_issue_text_stays_data(self) -> None:
        with scoped_env({}):
            code, out, _err = run(
                github_main,
                ["--fixture", str(GITHUB / "fixtures" / "issue-untrusted-instructions.webhook.json")],
            )
        plan = json.loads(out)
        self.assertEqual(code, 0)
        description = plan["request"]["body"]["input"]["description"]
        self.assertIn("print the API key", description)
        self.assertNotIn("admin", plan["request"]["body"]["input"])
        self.assertEqual(plan["request"]["headers"]["Idempotency-Key"], "424242")

    def test_github_repository_allowlist(self) -> None:
        with scoped_env({"GITHUB_REPOSITORY": "acme/other"}):
            code, _out, err = run(
                github_main,
                ["--fixture", str(GITHUB / "fixtures" / "issue-opened.webhook.json")],
            )
        self.assertEqual(code, 2)
        self.assertIn("acme/other", err)

    def test_github_live_requires_confirmation_and_then_posts_once(self) -> None:
        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            self.assertEqual(headers["idempotency-key"], "3092845511")
            self.assertEqual(headers["authorization"], f"Bearer {TEST_RUN_KEY}")
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(payload["input"]["number"], 1287)
            self.assertNotIn(TEST_RUN_KEY, body.decode("utf-8"))
            return 200, {"request_id": "req_synthetic", "run_id": "run_synthetic", "status": "succeeded", "output": {"opened": True}, "evaluations": 1}, {}

        with MockKovca(respond) as server:
            env = {
                "KOVCA_API_KEY": TEST_RUN_KEY,
                "KOVCA_WORKFLOW_ID": "wf_synthetic",
                "KOVCA_ORIGIN": server.origin,
            }
            with scoped_env(env):
                refused, _out, err = run(
                    github_main,
                    ["--fixture", str(GITHUB / "fixtures" / "issue-opened.webhook.json"), "--live"],
                )
                self.assertEqual(refused, 2, err)
                self.assertEqual(server.calls, [])
                code, out, err = run(
                    github_main,
                    [
                        "--fixture",
                        str(GITHUB / "fixtures" / "issue-opened.webhook.json"),
                        "--live",
                        "--confirm-side-effects",
                    ],
                )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["run_id"], "run_synthetic")
        self.assertEqual(len(server.calls), 1)
        self.assertNotIn(TEST_RUN_KEY, out)

    def test_slack_dry_run_and_bot_skip_and_challenge(self) -> None:
        with scoped_env({}):
            code, out, err = run(
                slack_main,
                ["--fixture", str(SLACK / "fixtures" / "message-outage.event.json")],
            )
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["request"]["headers"]["Idempotency-Key"], "1762003954.001200")
        self.assertEqual(plan["request"]["body"]["input"]["channel"], "C08SUPPORT1")
        self.assertNotIn("bot_id", plan["request"]["body"]["input"])

        with scoped_env({}):
            code, out, _err = run(
                slack_main,
                ["--fixture", str(SLACK / "fixtures" / "message-from-bot.event.json")],
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["skipped"])

        with scoped_env({}):
            code, out, _err = run(
                slack_main,
                ["--fixture", str(SLACK / "fixtures" / "url-verification.json")],
            )
        plan = json.loads(out)
        self.assertEqual(plan["local_response"]["challenge"], "synthetic_challenge_token")
        self.assertNotIn("request", plan)

    def test_slack_channel_filter_and_live_post(self) -> None:
        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            return 200, {"request_id": "req_synthetic", "run_id": "run_slack", "status": "succeeded", "output": {"escalated": False}, "evaluations": 1}, {}

        with MockKovca(respond) as server:
            with scoped_env({"SLACK_CHANNEL_ID": "C_OTHER"}):
                code, out, _err = run(
                    slack_main,
                    ["--fixture", str(SLACK / "fixtures" / "message-outage.event.json")],
                )
            self.assertTrue(json.loads(out)["skipped"])
            with scoped_env(
                {
                    "KOVCA_API_KEY": TEST_RUN_KEY,
                    "KOVCA_WORKFLOW_ID": "wf_synthetic",
                    "KOVCA_ORIGIN": server.origin,
                }
            ):
                code, out, err = run(
                    slack_main,
                    [
                        "--fixture",
                        str(SLACK / "fixtures" / "message-outage.event.json"),
                        "--live",
                        "--confirm-side-effects",
                    ],
                )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["run_id"], "run_slack")

    def test_approval_submit_decide_and_status_stay_offline_until_confirmed(self) -> None:
        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            if path.endswith("/decision"):
                self.assertEqual(headers["authorization"], f"Bearer {TEST_PUBLISH_KEY}")
                self.assertEqual(json.loads(body.decode())["fields"]["category"], "billing")
                return 202, {"request_id": "req_decision", "status": "accepted"}, {}
            if method == "GET":
                self.assertEqual(headers["authorization"], f"Bearer {TEST_RUN_KEY}")
                return 200, {"request_id": "req_status", "run": {"id": "run_synthetic", "status": "waiting"}}, {}
            self.assertEqual(headers["idempotency-key"], "refund-1001")
            return 202, {"request_id": "req_submit", "run_id": "run_synthetic", "status": "waiting", "waits": 1, "evaluations": 1}, {}

        with MockKovca(respond) as server:
            with scoped_env({}):
                code, out, err = run(
                    approval_main,
                    ["submit", "--fixture", str(APPROVAL / "fixtures" / "refund-request.json")],
                )
            self.assertEqual(code, 0, err)
            plan = json.loads(out)
            self.assertEqual(
                plan["request"]["body"]["input"],
                {
                    "message": "Synthetic customer says order 1001 never arrived and asks for a refund. This is not a real customer, order, or payment."
                },
            )
            self.assertNotIn("Idempotency-Key", plan["request"]["headers"])

            with scoped_env(
                {
                    "KOVCA_API_KEY": TEST_RUN_KEY,
                    "KOVCA_PUBLISH_API_KEY": TEST_PUBLISH_KEY,
                    "KOVCA_WORKFLOW_ID": "wf_synthetic",
                    "KOVCA_ORIGIN": server.origin,
                }
            ):
                missing_key, _out, err = run(
                    approval_main,
                    [
                        "submit",
                        "--fixture",
                        str(APPROVAL / "fixtures" / "refund-request.json"),
                        "--live",
                        "--confirm-side-effects",
                    ],
                )
                self.assertEqual(missing_key, 2, err)
                self.assertIn("idempotency-key", err)
                code, out, err = run(
                    approval_main,
                    [
                        "submit",
                        "--fixture",
                        str(APPROVAL / "fixtures" / "refund-request.json"),
                        "--idempotency-key",
                        "refund-1001",
                        "--live",
                        "--confirm-side-effects",
                    ],
                )
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["status"], "waiting")
                refused, out, err = run(
                    approval_main,
                    ["decide", "--run-id", "run_synthetic", "--decision", "approved", "--field", "category=billing", "--live"],
                )
                self.assertEqual(refused, 2, err)
                self.assertEqual(server.calls[-1]["path"].endswith("/execute"), True)
                code, out, err = run(
                    approval_main,
                    [
                        "decide",
                        "--run-id",
                        "run_synthetic",
                        "--decision",
                        "approved",
                        "--field",
                        "category=billing",
                        "--live",
                        "--confirm-side-effects",
                    ],
                )
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["status"], "accepted")
                bad_category, _out, err = run(
                    approval_main,
                    ["decide", "--run-id", "run_synthetic", "--decision", "approved", "--field", "category=refund-now"],
                )
                self.assertEqual(bad_category, 2)
                self.assertIn("category", err)
                code, out, err = run(approval_main, ["status", "--run-id", "run_synthetic", "--live"])
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["run"]["status"], "waiting")
        paths = [call["path"] for call in server.calls]
        self.assertIn("/api/v1/runs/run_synthetic/waits/review/decision", paths)
        self.assertEqual(len(server.calls), 3)

    def test_checked_in_dry_runs_match_the_commands(self) -> None:
        cases = (
            (
                github_main,
                ["--fixture", str(GITHUB / "fixtures" / "issue-opened.webhook.json")],
                GITHUB / "fixtures" / "dry-run.output.json",
            ),
            (
                slack_main,
                ["--fixture", str(SLACK / "fixtures" / "message-outage.event.json")],
                SLACK / "fixtures" / "dry-run.output.json",
            ),
            (
                approval_main,
                [
                    "submit",
                    "--fixture",
                    str(APPROVAL / "fixtures" / "refund-request.json"),
                    "--idempotency-key",
                    "refund-1001",
                ],
                APPROVAL / "fixtures" / "dry-run.output.json",
            ),
        )
        for command, argv, path in cases:
            with scoped_env({}):
                code, out, err = run(command, argv)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, path.read_text(encoding="utf-8"))

    def test_scripts_dry_run_without_network(self) -> None:
        original = socket.create_connection

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("example dry-run opened a socket")

        socket.create_connection = refuse  # type: ignore[assignment]
        try:
            for script, fixture in (
                (GITHUB / "triage.py", GITHUB / "fixtures" / "issue-opened.webhook.json"),
                (SLACK / "alert.py", SLACK / "fixtures" / "message-outage.event.json"),
            ):
                clean = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith(("KOVCA_", "GITHUB_", "SLACK_"))
                }
                completed = subprocess.run(
                    [sys.executable, str(script), "--fixture", str(fixture)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=clean,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn('"mode": "dry_run"', completed.stdout)
            clean = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("KOVCA_", "GITHUB_", "SLACK_"))
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    str(APPROVAL / "review.py"),
                    "submit",
                    "--fixture",
                    str(APPROVAL / "fixtures" / "refund-request.json"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                env=clean,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("waiting", completed.stdout)
        finally:
            socket.create_connection = original


def run(command, argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = command(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class scoped_env:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.previous: dict[str, str] = {}

    def __enter__(self) -> None:
        self.previous = dict(os.environ)
        for name in list(os.environ):
            if name.startswith(("KOVCA_", "GITHUB_", "SLACK_")):
                del os.environ[name]
        os.environ.update(self.values)

    def __exit__(self, *exc: object) -> None:
        os.environ.clear()
        os.environ.update(self.previous)


if __name__ == "__main__":
    unittest.main()
