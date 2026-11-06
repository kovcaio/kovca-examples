"""Signature vectors, MCP examples, and repository hygiene."""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import re
import unittest
from pathlib import Path

from kovca_examples.client import KovcaError
from kovca_examples.signatures import verify_github_signature, verify_slack_signature

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_URLS = {
    "https://kovca.io",
    "https://kovca.io/docs",
    "https://github.com/acme/storefront/issues/1287",
    "https://kovca.io/docs/mcp",
    "https://kovca.io/docs/api-keys",
    "https://kovca.io/docs/cli",
    "https://kovca.io/docs/billing",
    "https://kovca.io/security",
    "https://kovca.io/docs/workflows",
    "https://kovca.io/docs/runs",
    "https://kovca.io/docs/idempotency",
    "https://kovca.io/docs/errors",
    "https://kovca.io/docs/approvals",
    "https://kovca.io/docs/automations",
    "https://kovca.io/docs/actions",
    "https://kovca.io/docs/triggers",
    "https://kovca.io/docs/webhooks",
    "https://kovca.io/docs/api-reference",
    "https://kovca.io/docs/decision-ai",
    "https://kovca.io/guides/triage-and-label-new-github-issues",
    "https://kovca.io/guides/escalate-urgent-slack-channel-messages",
    "https://cursor.com/docs/mcp",
    "https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries",
    "https://docs.slack.dev/authentication/verifying-requests-from-slack",
    "https://api.slack.com/authentication/verifying-requests-from-slack",
}

SECRET_PATTERNS = (
    re.compile(r"@[A-Za-z0-9.-]+\.gmail\.com"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]+"),
    re.compile(r"\bwhsec_[A-Za-z0-9]{8,}"),
    re.compile(r"\bkvt_[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bx-access-token:[^@\s]+"),
)

ALLOWED_FAKE_KEYS = {
    "kvc_" + ("A" * 43),
    "kvc_" + ("B" * 43),
}


class SignatureTests(unittest.TestCase):
    def test_github_published_vector(self) -> None:
        # Vector published by GitHub's webhook delivery docs.
        secret = "It's a Secret to Everybody"
        body = b"Hello, World!"
        header = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        verify_github_signature(secret, body, header)
        with self.assertRaises(KovcaError):
            verify_github_signature(secret, body, header[:-1] + "0")

    def test_slack_signature_and_replay_window(self) -> None:
        secret = "synthetic-slack-signing-secret"
        body = b'{"type":"url_verification","challenge":"synthetic_challenge_token"}'
        timestamp = "1762003954"
        base = b"v0:" + timestamp.encode() + b":" + body
        signature = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        verify_slack_signature(secret, body, timestamp, signature, now=1762003954)
        with self.assertRaises(KovcaError):
            verify_slack_signature(secret, body, timestamp, signature, now=1762003954 + 301)
        with self.assertRaises(KovcaError):
            verify_slack_signature(secret, body, timestamp, "v0=00", now=1762003954)


class RepositoryTests(unittest.TestCase):
    def test_mcp_examples_use_placeholders(self) -> None:
        cursor = json.loads((ROOT / "guides" / "mcp" / "cursor.mcp.json").read_text(encoding="utf-8"))
        header = cursor["mcpServers"]["kovca"]["headers"]["Authorization"]
        self.assertEqual(header, "Bearer ${env:KOVCA_API_KEY}")
        self.assertEqual(cursor["mcpServers"]["kovca"]["url"], "https://kovca.io/api/v1/mcp")

        vscode = json.loads((ROOT / "guides" / "mcp" / "vscode.mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(
            vscode["servers"]["kovca"]["headers"]["Authorization"],
            "Bearer ${input:kovca-api-key}",
        )
        self.assertTrue(vscode["inputs"][0]["password"])

        script = (ROOT / "guides" / "mcp" / "claude-code.example.sh").read_text(encoding="utf-8")
        self.assertIn("https://kovca.io/api/v1/mcp", script)
        self.assertIn("KOVCA_API_KEY", script)
        self.assertIsNone(re.search(r"kvc_[A-Za-z0-9_-]{8,}", script))

    def test_approval_fragment_is_the_documented_graph_and_not_an_apply_file(self) -> None:
        fragment = json.loads(
            (ROOT / "examples" / "ai-approval-workflows" / "references" / "approval-graph.documented-fragment.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("kovca", fragment)
        self.assertNotIn("format", fragment)
        refs = [node["ref"] for node in fragment["nodes"]]
        self.assertEqual(
            refs,
            [
                "utility:input@1",
                "builtin:support/triage@1",
                "utility:condition@1",
                "wait:approval@1",
                "action:http@1",
            ],
        )
        self.assertEqual(fragment["nodes"][3]["config"]["notify"]["chat"]["connection"], "conn_...")

    def test_sample_outputs_are_labeled_and_valid(self) -> None:
        samples = list(ROOT.joinpath("examples").rglob("sample-*.json"))
        self.assertGreaterEqual(len(samples), 3)
        for path in samples:
            self.assertTrue(
                "shape-from-guide" in path.name or "from-docs" in path.name,
                path.name,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("synthetic", json.dumps(payload))

    def test_python_files_parse_and_links_are_known(self) -> None:
        for path in ROOT.rglob("*.py"):
            if any(part in {".git", "__pycache__", ".venv"} for part in path.parts):
                continue
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        urls = set()
        for path in list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.sh")):
            if any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8")
            urls.update(re.findall(r"https://[A-Za-z0-9./_#?=&%-]+", text))
        unknown = []
        for url in sorted(urls):
            cleaned = url.rstrip(").,")
            if cleaned in ALLOWED_URLS or cleaned.startswith("https://kovca.io/"):
                continue
            unknown.append(cleaned)
        self.assertEqual(unknown, [])

    def test_no_personal_identifiers_or_live_secrets(self) -> None:
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or any(part in {".git", "__pycache__", ".venv"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(ROOT).as_posix()
            in_tests = relative.startswith("tests/")
            for pattern in SECRET_PATTERNS:
                if in_tests:
                    continue
                if pattern.search(text):
                    offenders.append(f"{relative}: {pattern.pattern}")
            for match in re.findall(r"kvc_[A-Za-z0-9_-]{20,}", text):
                if match not in ALLOWED_FAKE_KEYS:
                    offenders.append(f"{relative}: unexpected key-shaped value")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
