"""Offline tests for the Kovca HTTP client."""

from __future__ import annotations

import json
import socket
import unittest

from kovca_examples.client import KovcaClient, KovcaError
from tests.keys import TEST_RUN_KEY
from tests.mock_kovca import MockKovca


class ClientTests(unittest.TestCase):
    def test_dry_run_does_not_open_a_socket(self) -> None:
        client = KovcaClient(api_key=TEST_RUN_KEY)
        prepared = client.prepare_execute(
            "wf_synthetic",
            {"message": "synthetic"},
            idempotency_key="case-1",
        )
        public = prepared.public_dict()
        self.assertEqual(public["method"], "POST")
        self.assertEqual(
            public["url"],
            "https://kovca.io/api/v1/workflows/wf_synthetic/execute",
        )
        self.assertEqual(public["headers"]["Idempotency-Key"], "case-1")
        self.assertNotIn(TEST_RUN_KEY, str(public))
        self.assertTrue(public["headers"]["Authorization"].startswith("Bearer kvc_AAAAAAAA"))
        self.assertEqual(public["headers"]["Authorization"].endswith("…"), True)

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("dry-run opened a socket")

        original = socket.create_connection
        socket.create_connection = refuse  # type: ignore[assignment]
        try:
            again = client.prepare_execute("wf_synthetic", {"message": "synthetic"})
            self.assertEqual(again.body, {"input": {"message": "synthetic"}})
        finally:
            socket.create_connection = original

    def test_rejects_instant_keys_placeholders_and_bad_origins(self) -> None:
        client = KovcaClient(api_key="kvt_not_a_workspace_key", origin="http://127.0.0.1:9")
        prepared = client.prepare_execute("wf_synthetic", {"message": "synthetic"})
        with self.assertRaises(KovcaError) as raised:
            client.send(prepared)
        self.assertIn("kvt_", str(raised.exception))

        with self.assertRaises(KovcaError):
            KovcaClient(api_key="kvc_...", origin="https://kovca.io").send(prepared)
        with self.assertRaises(KovcaError):
            KovcaClient(api_key=TEST_RUN_KEY, origin="http://example.test")

    def test_execute_success_and_idempotent_duplicate(self) -> None:
        seen: dict[str, bytes] = {}

        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            self.assertEqual(method, "POST")
            self.assertEqual(path, "/api/v1/workflows/wf_synthetic/execute")
            self.assertEqual(headers["authorization"], f"Bearer {TEST_RUN_KEY}")
            key = headers["idempotency-key"]
            if key in seen:
                self.assertEqual(seen[key], body)
                return 202, {"request_id": "req_original", "run_id": "run_synthetic", "status": "succeeded", "version": 1, "evaluations": 1}, {}
            seen[key] = body
            return 200, {"request_id": "req_original", "run_id": "run_synthetic", "status": "succeeded", "output": {"category": "technical"}, "evaluations": 1}, {}

        with MockKovca(respond) as server:
            client = KovcaClient(api_key=TEST_RUN_KEY, origin=server.origin, sleeper=lambda _seconds: None)
            prepared = client.prepare_execute(
                "wf_synthetic",
                {"title": "synthetic"},
                idempotency_key="3092845511",
            )
            first = client.send(prepared)
            second = client.send(prepared)
        self.assertEqual(first["output"]["category"], "technical")
        self.assertEqual(second["status"], "succeeded")
        self.assertNotIn("output", second)
        self.assertEqual(len(server.calls), 2)

    def test_retries_rate_limit_and_stops_for_usage_and_action_timeout(self) -> None:
        attempts = {"rate": 0}

        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            if "/wf_rate/" in path:
                attempts["rate"] += 1
                if attempts["rate"] == 1:
                    return 429, {"error": {"code": "RATE_LIMITED", "message": "Slow down."}, "request_id": "req_rate"}, {"Retry-After": "0"}
                return 200, {"request_id": "req_rate", "run_id": "run_synthetic", "status": "succeeded", "output": {}}, {}
            if "/wf_usage/" in path:
                return 429, {"error": {"code": "USAGE_LIMIT_EXCEEDED", "message": "Monthly credits are used up."}, "request_id": "req_usage"}, {}
            if "/wf_timeout/" in path:
                return 504, {"error": {"code": "ACTION_TIMEOUT", "message": "Destination timed out."}, "request_id": "req_timeout", "run_id": "run_synthetic"}, {}
            raise AssertionError(path)

        delays: list[float] = []
        with MockKovca(respond) as server:
            client = KovcaClient(
                api_key=TEST_RUN_KEY,
                origin=server.origin,
                sleeper=delays.append,
            )
            self.assertEqual(
                client.send(client.prepare_execute("wf_rate", {"message": "a"}, idempotency_key="rate-1")).get("run_id"),
                "run_synthetic",
            )
            with self.assertRaises(KovcaError) as usage_error:
                client.send(client.prepare_execute("wf_usage", {"message": "a"}, idempotency_key="usage-1"))
            with self.assertRaises(KovcaError) as timeout_error:
                client.send(client.prepare_execute("wf_timeout", {"message": "a"}, idempotency_key="timeout-1"))
        self.assertEqual(attempts["rate"], 2)
        self.assertEqual(delays, [0.0])
        self.assertEqual(usage_error.exception.code, "USAGE_LIMIT_EXCEEDED")
        self.assertIn("billing", str(usage_error.exception))
        self.assertFalse(usage_error.exception.retryable)
        self.assertEqual(timeout_error.exception.code, "ACTION_TIMEOUT")
        self.assertFalse(timeout_error.exception.retryable)
        paths = [call["path"] for call in server.calls]
        self.assertEqual(paths.count("/api/v1/workflows/wf_usage/execute"), 1)
        self.assertEqual(paths.count("/api/v1/workflows/wf_timeout/execute"), 1)

    def test_does_not_follow_redirects(self) -> None:
        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            return 302, {"error": {"code": "INVALID_REQUEST", "message": "no"}}, {"Location": "http://127.0.0.1:9/collect-the-key"}

        with MockKovca(respond) as server:
            client = KovcaClient(api_key=TEST_RUN_KEY, origin=server.origin, max_attempts=1)
            prepared = client.prepare_execute("wf_synthetic", {"message": "synthetic"})
            with self.assertRaises(KovcaError) as raised:
                client.send(prepared)
        self.assertIn("does not follow redirects", str(raised.exception))
        self.assertEqual(len(server.calls), 1)

    def test_auth_and_access_errors_name_the_fix(self) -> None:
        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            if "/wf_missing/" in path:
                return 401, {"error": {"code": "UNAUTHORIZED", "message": "Unknown key."}, "request_id": "req_auth"}, {}
            return 403, {"error": {"code": "INSUFFICIENT_ACCESS", "message": "Author access is required."}, "request_id": "req_access"}, {}

        with MockKovca(respond) as server:
            client = KovcaClient(api_key=TEST_RUN_KEY, origin=server.origin, max_attempts=1)
            with self.assertRaises(KovcaError) as auth_error:
                client.send(client.prepare_execute("wf_missing", {"message": "a"}))
            with self.assertRaises(KovcaError) as access_error:
                client.send(client.prepare_execute("wf_denied", {"message": "a"}))
        self.assertIn("https://kovca.io/docs/api-keys", str(auth_error.exception))
        self.assertIn("Publish access", str(access_error.exception))

    def test_body_limit_and_bad_idempotency_key(self) -> None:
        client = KovcaClient(api_key=TEST_RUN_KEY)
        with self.assertRaises(KovcaError):
            client.prepare_execute("wf_synthetic", {"message": "x" * 20000})
        with self.assertRaises(KovcaError):
            client.prepare_execute("wf_synthetic", {"message": "ok"}, idempotency_key="has space")

    def test_decision_endpoint(self) -> None:
        def respond(method: str, path: str, headers: dict[str, str], body: bytes):
            self.assertEqual(path, "/api/v1/runs/run_synthetic/waits/review/decision")
            self.assertEqual(json_body(body)["decision"], "approved")
            return 202, {"request_id": "req_decision", "status": "accepted"}, {}

        with MockKovca(respond) as server:
            client = KovcaClient(api_key=TEST_RUN_KEY, origin=server.origin)
            prepared = client.prepare_decision(
                "run_synthetic",
                "review",
                "approved",
                {"category": "billing"},
            )
            self.assertNotIn(TEST_RUN_KEY, str(prepared.public_dict()))
            result = client.send(prepared)
        self.assertEqual(result["request_id"], "req_decision")


def json_body(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
