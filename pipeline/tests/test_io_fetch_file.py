"""Tests for ``fetch_file``, the whole-file downloader of the read layer (PRD 5 pilot rule).

Every request goes to a server on 127.0.0.1. An autouse fixture refuses to resolve any other
host, so no test reaches the network. A missing ``dest.parent`` raises FileNotFoundError.
"""

from __future__ import annotations

import hashlib
import http.server
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest

from terraluna_pipeline.io import (
    WHOLE_FILE_LIMIT_BYTES,
    DownloadError,
    FetchedFile,
    FileTooLargeError,
    fetch_file,
)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
TIFF_MAGIC = (b"II*\x00", b"MM\x00*")
LIMIT = 1000


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
    body: bytes = b"II*\x00" + bytes(range(256)) * 2
    mode: str = "ok"
    requests: list[dict[str, str]] = field(default_factory=list)
    body_writes_failed: bool = False


class FileHandler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[ServerState]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state = self.state
        state.requests.append({k.lower(): v for k, v in self.headers.items()} | {"path": self.path})
        mode = state.mode
        if self.path.startswith("/signed"):
            self.send_error(500)
            return
        if mode == "redirect" and self.path == "/file.tif":
            self.send_response(302)
            self.send_header("Location", "/final.tif")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "redirect-signed":
            self.send_response(302)
            self.send_header("Location", "/signed?X-Amz-Signature=abc")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "not-found":
            self.send_error(404)
            return
        body = state.body
        self.send_response(200)
        if mode == "html":
            body = b"<html>not found</html>"
            self.send_header("Content-Type", "text/html")
        else:
            self.send_header("Content-Type", "image/tiff")
        self.send_header("Last-Modified", "Tue, 01 Jan 2019 00:00:00 GMT")
        self.send_header("ETag", '"abc123"')
        if mode == "gzip":
            self.send_header("Content-Encoding", "gzip")
        if mode == "short":
            self.send_header("Content-Length", str(len(body) + 50))
        elif mode != "no-length":
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            state.body_writes_failed = True
        self.close_connection = True


@pytest.fixture
def server() -> Iterator[tuple[str, ServerState]]:
    state = ServerState()
    handler = type("Handler", (FileHandler,), {"state": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/file.tif", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def only_files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_exact_bytes_and_reported_headers(tmp_path: Path, server: tuple[str, ServerState]) -> None:
    url, state = server
    dest = tmp_path / "out.tif"
    result = fetch_file(url, dest, magic=TIFF_MAGIC)
    assert isinstance(result, FetchedFile)
    assert dest.read_bytes() == state.body
    assert result.path == dest
    assert result.url == result.final_url == url
    assert result.status == 200
    assert result.content_type == "image/tiff"
    assert result.content_length == len(state.body)
    assert result.bytes_received == len(state.body)
    assert result.sha256 == hashlib.sha256(state.body).hexdigest()
    assert result.last_modified == "Tue, 01 Jan 2019 00:00:00 GMT"
    assert result.etag == '"abc123"'
    assert dest.stat().st_mode & 0o777 == 0o644
    assert only_files(tmp_path) == ["out.tif"]


def test_request_is_identity_encoded_without_range(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    fetch_file(url, tmp_path / "out.tif")
    assert len(state.requests) == 1
    assert state.requests[0]["accept-encoding"] == "identity"
    assert "range" not in state.requests[0]


def test_404_is_a_download_error_and_writes_nothing(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    state.mode = "not-found"
    dest = tmp_path / "out.tif"
    with pytest.raises(DownloadError, match="404") as info:
        fetch_file(url, dest)
    assert url in str(info.value)
    assert only_files(tmp_path) == []


def test_soft_404_html_fails_the_magic_check(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    state.mode = "html"
    with pytest.raises(DownloadError, match="text/html") as info:
        fetch_file(url, tmp_path / "out.tif", magic=TIFF_MAGIC)
    assert url in str(info.value)
    assert "error page" in str(info.value)
    assert only_files(tmp_path) == []


def test_second_magic_alternative_is_accepted(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    state.body = b"MM\x00*" + b"x" * 20
    result = fetch_file(url, tmp_path / "out.tif", magic=TIFF_MAGIC)
    assert result.bytes_received == len(state.body)


@pytest.mark.parametrize("size", [LIMIT, LIMIT + 500])
def test_content_length_at_or_over_the_limit_is_refused(
    tmp_path: Path, server: tuple[str, ServerState], size: int
) -> None:
    url, state = server
    state.body = b"x" * size
    with pytest.raises(FileTooLargeError, match="range") as info:
        fetch_file(url, tmp_path / "out.tif", max_bytes=LIMIT)
    message = str(info.value)
    assert url in message and str(size) in message and str(LIMIT) in message
    assert only_files(tmp_path) == []


def test_one_byte_under_the_limit_succeeds(tmp_path: Path, server: tuple[str, ServerState]) -> None:
    url, state = server
    state.body = b"x" * (LIMIT - 1)
    result = fetch_file(url, tmp_path / "out.tif", max_bytes=LIMIT)
    assert result.bytes_received == LIMIT - 1


def test_unknown_length_over_the_limit_stops_streaming(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    state.mode = "no-length"
    state.body = b"x" * (LIMIT + 10)
    with pytest.raises(FileTooLargeError):
        fetch_file(url, tmp_path / "out.tif", max_bytes=LIMIT)
    assert only_files(tmp_path) == []


def test_unknown_length_under_the_limit_succeeds(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    state.mode = "no-length"
    state.body = b"x" * (LIMIT - 1)
    result = fetch_file(url, tmp_path / "out.tif", max_bytes=LIMIT)
    assert result.content_length is None
    assert result.bytes_received == LIMIT - 1
    assert result.sha256 == hashlib.sha256(state.body).hexdigest()


def test_short_body_is_a_download_error(tmp_path: Path, server: tuple[str, ServerState]) -> None:
    url, state = server
    state.mode = "short"
    with pytest.raises(DownloadError) as info:
        fetch_file(url, tmp_path / "out.tif")
    assert str(len(state.body)) in str(info.value)
    assert str(len(state.body) + 50) in str(info.value)
    assert only_files(tmp_path) == []


def test_content_encoding_gzip_is_refused(tmp_path: Path, server: tuple[str, ServerState]) -> None:
    url, state = server
    state.mode = "gzip"
    with pytest.raises(DownloadError, match="Content-Encoding"):
        fetch_file(url, tmp_path / "out.tif")
    assert only_files(tmp_path) == []


def test_existing_dest_is_untouched_by_a_failed_download(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    dest = tmp_path / "out.tif"
    dest.write_bytes(b"precious")
    state.mode = "short"
    with pytest.raises(DownloadError):
        fetch_file(url, dest)
    state.mode = "html"
    with pytest.raises(DownloadError):
        fetch_file(url, dest, magic=TIFF_MAGIC)
    assert dest.read_bytes() == b"precious"
    assert only_files(tmp_path) == ["out.tif"]


def test_success_replaces_an_existing_dest(tmp_path: Path, server: tuple[str, ServerState]) -> None:
    url, state = server
    dest = tmp_path / "out.tif"
    dest.write_bytes(b"old")
    fetch_file(url, dest)
    assert dest.read_bytes() == state.body


def test_redirect_is_followed_and_recorded(tmp_path: Path, server: tuple[str, ServerState]) -> None:
    url, state = server
    state.mode = "redirect"
    result = fetch_file(url, tmp_path / "out.tif")
    assert result.url == url
    assert result.final_url == url.replace("/file.tif", "/final.tif")
    assert (tmp_path / "out.tif").read_bytes() == state.body


def test_redirect_to_a_signed_url_is_refused_before_following(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, state = server
    state.mode = "redirect-signed"
    with pytest.raises(DownloadError, match="not followed"):
        fetch_file(url, tmp_path / "out.tif")
    assert [r["path"] for r in state.requests] == ["/file.tif"]
    assert only_files(tmp_path) == []


@pytest.mark.parametrize(
    "bad",
    ["{base}?X-Amz-Signature=abc", "ftp://127.0.0.1/file.tif", "http://user:pw@127.0.0.1/file.tif"],
)
def test_signed_or_non_http_urls_are_refused_before_any_request(
    tmp_path: Path, server: tuple[str, ServerState], bad: str
) -> None:
    url, state = server
    bad_url = bad.format(base=url)
    with pytest.raises(ValueError) as info:
        fetch_file(bad_url, tmp_path / "out.tif")
    assert "abc" not in str(info.value) and "pw" not in str(info.value)
    assert state.requests == []


def test_connection_refused_is_a_download_error(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/file.tif"
    with pytest.raises(DownloadError, match="could not connect") as info:
        fetch_file(url, tmp_path / "out.tif")
    assert url in str(info.value)


@pytest.mark.parametrize("max_bytes", [0, -5])
def test_non_positive_max_bytes_is_refused(
    tmp_path: Path, server: tuple[str, ServerState], max_bytes: int
) -> None:
    url, state = server
    with pytest.raises(ValueError, match="max_bytes"):
        fetch_file(url, tmp_path / "out.tif", max_bytes=max_bytes)
    assert state.requests == []


def test_missing_parent_directory_raises_file_not_found(
    tmp_path: Path, server: tuple[str, ServerState]
) -> None:
    url, _ = server
    with pytest.raises(FileNotFoundError):
        fetch_file(url, tmp_path / "missing" / "out.tif")
    assert not (tmp_path / "missing").exists()


def test_default_limit_is_200_mb() -> None:
    assert WHOLE_FILE_LIMIT_BYTES == 200_000_000
