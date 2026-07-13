"""A tiny stdlib HTTP server exposing the calculator.

Kept dependency-free (standard library only) so ``pyproject.toml`` stays with
``dependencies = []``. This is the long-running container entrypoint used for
progressive delivery (blue-green / canary) — a batch job has no traffic to shift.

Endpoints:
    GET /healthz            -> 200 {"status": "ok", "version": "<APP_VERSION>"}
    GET /add?a=..&b=..      -> {"result": a + b}
    GET /subtract?a=..&b=.. -> {"result": a - b}
    GET /divide?a=..&b=..   -> {"result": a / b}  (400 on divide-by-zero)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.calculator import add, divide, subtract

# Surfaced in /healthz so you can tell blue from green by eye during a rollout.
APP_VERSION = os.getenv("APP_VERSION", "dev")

_OPS = {"add": add, "subtract": subtract, "divide": divide}


class Handler(BaseHTTPRequestHandler):
    """Route GET requests to the calculator; everything else is 404."""

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"

        if route == "/healthz":
            self._send(200, {"status": "ok", "version": APP_VERSION})
            return

        op = _OPS.get(route.lstrip("/"))
        if op is None:
            self._send(404, {"error": f"unknown route: {route}"})
            return

        params = parse_qs(parsed.query)
        try:
            a = float(params["a"][0])
            b = float(params["b"][0])
        except (KeyError, IndexError, ValueError):
            self._send(400, {"error": "query params 'a' and 'b' (numbers) required"})
            return

        try:
            self._send(200, {"result": op(a, b), "version": APP_VERSION})
        except ZeroDivisionError as exc:
            self._send(400, {"error": str(exc)})

    def log_message(self, *args: object) -> None:
        """Silence per-request stderr logging; keep output clean for containers."""


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("", port), Handler)
    print(f"serving cicd-demo (version={APP_VERSION}) on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
