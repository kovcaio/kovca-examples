"""Local stand-in for the Kovca HTTP API. It never leaves the test process."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockKovca:
    def __init__(self, respond: Callable[[str, str, dict[str, str], bytes], tuple[int, dict[str, Any], dict[str, str]]]) -> None:
        self.respond = respond
        self.calls: list[dict[str, Any]] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "MockKovca":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=3)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length) if length else b""
                headers = {key.lower(): value for key, value in self.headers.items()}
                server.calls.append(
                    {"method": method, "path": self.path, "headers": headers, "body": body}
                )
                status, payload, extra = server.respond(method, self.path, headers, body)
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        return Handler
