"""Tests for the HTTP server (the long-running container entrypoint)."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

from app.server import Handler


@pytest.fixture
def address() -> Iterator[tuple[str, int]]:
    """Start the server on an ephemeral port for the duration of a test."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(address: tuple[str, int], path: str) -> tuple[int, dict[str, object]]:
    # http.client talks straight to the socket — no system proxy in the way.
    conn = http.client.HTTPConnection(*address, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_healthz(address: tuple[str, int]) -> None:
    status, body = _get(address, "/healthz")
    assert status == 200
    assert body["status"] == "ok"
    assert "version" in body


def test_add(address: tuple[str, int]) -> None:
    status, body = _get(address, "/add?a=2&b=3")
    assert status == 200
    assert body["result"] == 5


def test_subtract(address: tuple[str, int]) -> None:
    status, body = _get(address, "/subtract?a=5&b=3")
    assert status == 200
    assert body["result"] == 2


def test_divide_by_zero_returns_400(address: tuple[str, int]) -> None:
    status, body = _get(address, "/divide?a=1&b=0")
    assert status == 400
    assert "error" in body


def test_missing_params_returns_400(address: tuple[str, int]) -> None:
    status, _ = _get(address, "/add?a=2")
    assert status == 400


def test_unknown_route_returns_404(address: tuple[str, int]) -> None:
    status, _ = _get(address, "/nope")
    assert status == 404
