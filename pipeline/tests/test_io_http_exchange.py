"""Tests for ``send_http_exchange``, the raw HTTP request of the CORS header check (T1-07).

Every request goes to a server on 127.0.0.1. An autouse fixture refuses to resolve any other
host, so no test reaches the network.
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from terraluna_pipeline.io import (
    DownloadError,
    HttpExchange,
    HttpExchangeError,
    send_http_exchange,
)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to resolve or connect to anything but the loopback interface."""
    real_getaddrinfo = socket.getaddrinfo

    def loopback_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in _LOOPBACK:
            raise socket.gaierror(socket.EAI_NONAME, f"test refused to resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", loopback_only)
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")


@dataclass
class ServerState:
    requests: list[dict[str, Any]] = field(default_factory=list)
    body: bytes = bytes(range(256)) * 8


class Handler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[ServerState]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _record(self) -> None:
        self.state.requests.append(
            {"method": self.command, "path": self.path, "headers": list(self.headers.items())}
        )

    def do_OPTIONS(self) -> None:
        self._record()
        body = b"<Error><Message>CORS is not enabled</Message></Error>"
        if self.path == "/forbidden":
            self.send_response(403, "Forbidden")
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD")
        self.send_header("Vary", "Origin")
        self.send_header("Vary", "Access-Control-Request-Method")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._record()
        body = self.state.body
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/file")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/whole":
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/chunked":
            self.send_response(206)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in (body[:10], body[10:30]):
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        part = body[:1024]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes 0-1023/{len(body)}")
        self.send_header("accept-ranges", "bytes")
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        self.wfile.write(part)


@pytest.fixture
def server() -> Iterator[tuple[str, ServerState]]:
    state = ServerState()
    handler = type("H", (Handler,), {"state": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


ORIGIN = {"Origin": "https://example.org"}


def test_get_records_status_line_headers_in_order_and_body(
    server: tuple[str, ServerState],
) -> None:
    base, state = server
    exchange = send_http_exchange(f"{base}/file", "GET", {**ORIGIN, "Range": "bytes=0-1023"})
    assert isinstance(exchange, HttpExchange)
    assert exchange.method == "GET"
    assert exchange.status == 206
    assert exchange.http_version == "HTTP/1.1"
    assert exchange.status_line.startswith("HTTP/1.1 206 ")
    names = [name for name, _ in exchange.headers]
    # Names keep the case the server sent, in the order sent.
    assert names.index("Content-Range") < names.index("accept-ranges")
    assert exchange.header("ACCEPT-RANGES") == ["bytes"]
    assert exchange.header("Access-Control-Allow-Origin") == []
    assert exchange.body == state.body[:1024]
    assert exchange.body_truncated is False
    sent = dict(state.requests[0]["headers"])
    assert sent["Origin"] == "https://example.org"
    assert sent["Range"] == "bytes=0-1023"
    assert sent["Accept-Encoding"] == "identity"
    # Nothing identifying is sent: no User-Agent at all.
    assert "User-Agent" not in sent
    assert dict(exchange.request_headers)["Origin"] == "https://example.org"
    assert dict(exchange.request_headers)["Host"] == base.removeprefix("http://")


def test_options_preflight_sends_method_and_keeps_repeated_headers(
    server: tuple[str, ServerState],
) -> None:
    base, state = server
    exchange = send_http_exchange(
        f"{base}/file",
        "OPTIONS",
        {
            **ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "range",
        },
    )
    assert state.requests[0]["method"] == "OPTIONS"
    assert exchange.status == 200
    assert exchange.header("vary") == ["Origin", "Access-Control-Request-Method"]
    assert exchange.body == b""


def test_http_error_status_is_an_observation_not_an_exception(
    server: tuple[str, ServerState],
) -> None:
    base, _ = server
    exchange = send_http_exchange(f"{base}/forbidden", "OPTIONS", ORIGIN)
    assert exchange.status == 403
    assert exchange.reason == "Forbidden"
    assert b"CORS is not enabled" in exchange.body


def test_redirect_is_recorded_and_not_followed(server: tuple[str, ServerState]) -> None:
    base, state = server
    exchange = send_http_exchange(f"{base}/redirect", "GET", ORIGIN)
    assert exchange.status == 302
    assert exchange.header("Location") == ["/file"]
    assert len(state.requests) == 1


def test_body_is_cut_at_the_limit_and_flagged(server: tuple[str, ServerState]) -> None:
    base, state = server
    exchange = send_http_exchange(f"{base}/whole", "GET", ORIGIN, max_body_bytes=100)
    assert exchange.status == 200
    assert exchange.body == state.body[:100]
    assert exchange.body_truncated is True


def test_chunked_body_is_decoded(server: tuple[str, ServerState]) -> None:
    base, state = server
    exchange = send_http_exchange(f"{base}/chunked", "GET", ORIGIN)
    assert exchange.body == state.body[:30]
    assert exchange.body_truncated is False


def test_unreachable_host_raises_naming_the_url() -> None:
    url = "http://127.0.0.1:9/file"
    with pytest.raises(HttpExchangeError, match=re.escape(url)) as raised:
        send_http_exchange(url, "GET", ORIGIN, timeout=2)
    assert isinstance(raised.value, DownloadError)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.org/file",
        "https://example.org/file?X-Amz-Signature=abc",
        "https://user:pw@example.org/file",
    ],
)
def test_refuses_non_http_and_signed_urls_before_sending(url: str) -> None:
    with pytest.raises(ValueError):
        send_http_exchange(url, "GET", ORIGIN)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_only_get_head_and_options_are_sent(server: tuple[str, ServerState], method: str) -> None:
    base, state = server
    with pytest.raises(ValueError, match=method):
        send_http_exchange(f"{base}/file", method, ORIGIN)
    assert state.requests == []


def test_refuses_identifying_request_headers(server: tuple[str, ServerState]) -> None:
    base, state = server
    for name in ("User-Agent", "Cookie", "Authorization", "Referer"):
        with pytest.raises(ValueError, match=name):
            send_http_exchange(f"{base}/file", "GET", {name: "x"})
    assert state.requests == []
