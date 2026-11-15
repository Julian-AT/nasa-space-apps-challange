"""Tests for the cors pilot source (T1-07; PRD 8.7 step 2, 9 G0, 15 architecture review).

A stub server on 127.0.0.1 stands in for the two buckets. Each path answers an OPTIONS
preflight and a Range GET in one of several CORS behaviours (S3 without CORS, full CORS, an
origin allow list, no exposed headers, a wildcard expose list, a server that ignores Range).
The URLs come from synthetic T1-06 and wc records written into the test repository. fetch goes
through the real io.send_http_exchange; record and check run with sockets refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.server
import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from terraluna_pipeline import pilot

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
NEUTRAL = "https://example.org"
LOCALHOST = "http://localhost:3000"
BODY = bytes(range(256)) * 8
MATTERING = (
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "access-control-max-age",
    "vary",
    "content-range",
    "accept-ranges",
    "content-length",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    real_getaddrinfo = socket.getaddrinfo

    def loopback_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in _LOOPBACK:
            raise socket.gaierror(socket.EAI_NONAME, f"test refused to resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", loopback_only)
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")


def refuse_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a socket connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@dataclass
class ServerState:
    modes: dict[str, str]
    requests: list[dict[str, Any]] = field(default_factory=list)


class Handler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[ServerState]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _mode(self) -> str:
        self.state.requests.append(
            {"method": self.command, "path": self.path, "headers": list(self.headers.items())}
        )
        return self.state.modes.get(self.path, "missing")

    def _finish(self, status: int, body: bytes, headers: list[tuple[str, str]]) -> None:
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allow(self, mode: str) -> list[tuple[str, str]]:
        origin = self.headers.get("Origin", "")
        if mode == "echo_neutral":
            return [("Access-Control-Allow-Origin", origin)] if origin == NEUTRAL else []
        return [("Access-Control-Allow-Origin", "*")]

    def do_OPTIONS(self) -> None:
        mode = self._mode()
        if mode in ("disabled", "missing"):
            body = b"<Error><Message>CORSResponse: CORS is not enabled for this bucket.</Message>"
            self._finish(403, body, [("Content-Type", "application/xml")])
            return
        headers = [
            *self._allow(mode),
            ("Access-Control-Allow-Methods", "GET, HEAD"),
            ("Access-Control-Allow-Headers", "range"),
            ("Access-Control-Max-Age", "3000"),
            ("Vary", "Origin, Access-Control-Request-Headers, Access-Control-Request-Method"),
        ]
        self._finish(200, b"", headers)

    def do_GET(self) -> None:
        mode = self._mode()
        if mode == "missing":
            self._finish(404, b"not found", [])
            return
        if mode == "range_ignored":
            self._finish(200, BODY, [*self._allow(mode), ("Accept-Ranges", "bytes")])
            return
        headers = [
            ("Accept-Ranges", "bytes"),
            ("Content-Range", f"bytes 0-1023/{len(BODY)}"),
            ("Content-Type", "image/tiff"),
        ]
        if mode != "disabled":
            headers += self._allow(mode)
            headers.append(("Vary", "Origin"))
        if mode in ("enabled", "echo_neutral"):
            headers.append(("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges"))
        if mode == "wildcard_expose":
            headers.append(("Access-Control-Expose-Headers", "*"))
        self._finish(206, BODY[:1024], headers)


class Stub:
    def __init__(self, glo30: str = "disabled", worldcover: str = "disabled") -> None:
        self.state = ServerState(modes={"/glo30.tif": glo30, "/wc.tif": worldcover})
        handler = type("H", (Handler,), {"state": self.state})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 15, 3, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def write_records(root: Path, base: str, *, wc: bool = True) -> None:
    pilot_dir = root / "outputs/pilot"
    pilot_dir.mkdir(parents=True, exist_ok=True)
    (pilot_dir / "cop30_range_read.json").write_text(
        json.dumps({"dem": {"range_read": {"url": f"{base}/glo30.tif"}}})
    )
    if wc:
        (pilot_dir / "worldcover_window.json").write_text(
            json.dumps({"range_read": {"url": f"{base}/wc.tif"}})
        )


def run(command: str, root: Path, **kwargs: Any) -> int:
    return pilot.main([command, "cors"], root=root, sources={"cors": pilot.CorsSource()}, **kwargs)


def fetch_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, glo30: str, worldcover: str
) -> tuple[Path, Stub]:
    root = tmp_path / "repo"
    root.mkdir()
    stub = Stub(glo30, worldcover)
    try:
        write_records(root, stub.base)
        assert run("fetch", root, now=Clock()) == 0
    finally:
        stub.stop()
    with monkeypatch.context() as patch:
        refuse_sockets(patch)
        assert run("record", root) == 0
    return root, stub


@pytest.fixture
def disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Stub]:
    return fetch_and_record(tmp_path, monkeypatch, "disabled", "disabled")


def cors_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((root / "outputs/pilot/cors_check.json").read_bytes())
    return result


def fetch_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((root / "data/raw/cors/fetch.json").read_bytes())
    return result


def url_entry(record: dict[str, Any], key: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = [item for item in record["urls"] if item["key"] == key]
    (entry,) = entries
    return entry


def all_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_keys(item)


# --- Tests --------------------------------------------------------------------------------------


def test_registered_with_the_stated_neutral_and_localhost_origins() -> None:
    source = pilot.SOURCES["cors"]
    assert isinstance(source, pilot.CorsSource)
    assert source.name == "cors"
    assert pilot.raw_directory(source) == "cors"
    assert dict(source.origins) == {"neutral": NEUTRAL, "localhost": LOCALHOST}
    for _, origin in source.origins:
        assert "terraluna" not in origin.lower() and "gradient" not in origin.lower()


def test_fetch_sends_a_preflight_and_a_range_get_per_url_and_origin(
    disabled: tuple[Path, Stub],
) -> None:
    _, stub = disabled
    requests = stub.state.requests
    assert len(requests) == 8
    seen = set()
    for request in requests:
        headers = dict(request["headers"])
        assert "User-Agent" not in headers
        assert set(headers) <= {
            "Host",
            "Accept-Encoding",
            "Origin",
            "Access-Control-Request-Method",
            "Access-Control-Request-Headers",
            "Range",
        }
        if request["method"] == "OPTIONS":
            assert headers["Access-Control-Request-Method"] == "GET"
            assert headers["Access-Control-Request-Headers"] == "range"
            assert "Range" not in headers
        else:
            assert request["method"] == "GET"
            assert headers["Range"] == "bytes=0-1023"
        seen.add((request["path"], headers["Origin"], request["method"]))
    assert seen == {
        (path, origin, method)
        for path in ("/glo30.tif", "/wc.tif")
        for origin in (NEUTRAL, LOCALHOST)
        for method in ("OPTIONS", "GET")
    }


def test_fetch_saves_status_lines_headers_bodies_and_times(disabled: tuple[Path, Stub]) -> None:
    root, _ = disabled
    raw = root / "data/raw/cors"
    files = fetch_json(root)["files"]
    assert len(files) == 8
    for entry in files:
        assert entry["status_line"].startswith("HTTP/1.1 ")
        assert entry["response_headers"]
        assert entry["requested_at"] < entry["retrieved_at"]
        body = (raw / entry["file_name"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
        assert len(body) == entry["bytes_received"]


def test_s3_without_cors_supports_proxy_needed(disabled: tuple[Path, Stub]) -> None:
    root, _ = disabled
    record = cors_json(root)
    assert record["cors_evidence_supports"] == "proxy_needed"
    assert record["cors_evidence_reasons"]
    for key in ("glo30", "worldcover"):
        entry = url_entry(record, key)
        assert entry["cors_evidence_supports"] == "proxy_needed"
        assert entry["reasons"]
        for origin in entry["origins"]:
            preflight, get = origin["preflight"], origin["range_get"]
            assert preflight["status"] == 403
            assert preflight["headers_that_matter"]["access-control-allow-origin"] == {
                "state": "absent",
                "values": [],
            }
            assert preflight["observations"]["allows_origin"] is False
            assert get["status"] == 206
            assert get["observations"]["returned_206"] is True
            assert get["observations"]["allows_origin"] is False
            assert get["observations"]["expose_headers_lists_content_range"] is False
            assert get["observations"]["expose_headers_lists_accept_ranges"] is False
            assert get["headers_that_matter"]["content-range"] == {
                "state": "present",
                "values": ["bytes 0-1023/2048"],
            }


def test_every_exchange_records_each_header_that_matters(disabled: tuple[Path, Stub]) -> None:
    root, _ = disabled
    for entry in cors_json(root)["urls"]:
        for origin in entry["origins"]:
            for kind in ("preflight", "range_get"):
                assert sorted(origin[kind]["headers_that_matter"]) == sorted(MATTERING)


def test_full_cors_supports_direct_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, "enabled", "enabled")
    record = cors_json(root)
    assert record["cors_evidence_supports"] == "direct_read"
    for origin in url_entry(record, "glo30")["origins"]:
        observations = origin["range_get"]["observations"]
        assert observations["allows_origin"] is True
        assert observations["expose_headers_lists_content_range"] is True
        assert observations["expose_headers_lists_accept_ranges"] is True
        assert observations["expose_headers_wildcard"] is False
        assert origin["preflight"]["observations"] == {
            "ok_status": True,
            "allows_origin": True,
            "allows_get": True,
            "allows_range_header": True,
        }


def test_wildcard_expose_is_recorded_apart_from_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, "wildcard_expose", "disabled")
    record = cors_json(root)
    assert record["cors_evidence_supports"] == "direct_read"
    get = url_entry(record, "glo30")["origins"][0]["range_get"]["observations"]
    assert get["expose_headers_wildcard"] is True
    assert get["expose_headers_lists_content_range"] is False


def test_an_origin_allow_list_that_misses_localhost_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, "echo_neutral", "disabled")
    entry = url_entry(cors_json(root), "glo30")
    assert entry["cors_evidence_supports"] == "inconclusive"
    assert any(LOCALHOST in reason for reason in entry["reasons"])


def test_missing_expose_headers_is_absent_and_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, "no_expose", "disabled")
    entry = url_entry(cors_json(root), "glo30")
    assert entry["cors_evidence_supports"] == "inconclusive"
    get = entry["origins"][0]["range_get"]
    assert get["headers_that_matter"]["access-control-expose-headers"]["state"] == "absent"
    assert get["observations"]["expose_headers_lists_content_range"] is False
    assert any("Content-Range" in reason for reason in entry["reasons"])


def test_a_server_ignoring_range_is_not_206_and_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, "range_ignored", "disabled")
    entry = url_entry(cors_json(root), "glo30")
    assert entry["cors_evidence_supports"] == "inconclusive"
    assert entry["origins"][0]["range_get"]["observations"]["returned_206"] is False


def test_glo30_decides_the_top_level_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, "disabled", "enabled")
    record = cors_json(root)
    assert record["cors_evidence_supports"] == "proxy_needed"
    assert url_entry(record, "worldcover")["cors_evidence_supports"] == "direct_read"


def test_record_states_server_side_limits_and_never_sets_cop30_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    run_yaml = b"cop30_mode: null\nshipping_mode: null\n"
    (root / "config/run.yaml").write_bytes(run_yaml)
    stub = Stub("enabled", "enabled")
    try:
        write_records(root, stub.base)
        assert run("fetch", root, now=Clock()) == 0
    finally:
        stub.stop()
    assert run("record", root) == 0
    record = cors_json(root)
    statement = record["method"]["statement"]
    assert "server-side request" in statement
    assert "sends an Origin header" in statement
    assert "not a real browser fetch" in statement
    assert "implies but does not replace a browser run" in statement
    assert "chosen by the team at G0" in record["cop30_mode_note"]
    assert "cop30_mode" not in set(all_keys(record))
    assert (root / "config/run.yaml").read_bytes() == run_yaml
    assert record["method"]["origins"] == [
        {"key": "neutral", "origin": NEUTRAL, "why": pilot.CorsSource().origin_reasons["neutral"]},
        {
            "key": "localhost",
            "origin": LOCALHOST,
            "why": pilot.CorsSource().origin_reasons["localhost"],
        },
    ]


def test_record_leaves_sources_json_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "outputs").mkdir(parents=True)
    (root / "outputs/sources.json").write_bytes(b"[]\n")
    stub = Stub()
    try:
        write_records(root, stub.base)
        assert run("fetch", root, now=Clock()) == 0
    finally:
        stub.stop()
    assert run("record", root) == 0
    assert (root / "outputs/sources.json").read_bytes() == b"[]\n"


def test_check_passes_offline_and_fails_on_changes(
    disabled: tuple[Path, Stub],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = disabled
    refuse_sockets(monkeypatch)
    assert run("check", root) == 0
    path = root / "outputs/pilot/cors_check.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"proxy_needed"', b'"direct_read"', 1))
    assert run("check", root) == 1
    assert "cors_check.json: differs" in capsys.readouterr().err
    path.write_bytes(original)
    body = root / "data/raw/cors" / fetch_json(root)["files"][1]["file_name"]
    body.write_bytes(body.read_bytes() + b"x")
    assert run("check", root) == 1
    assert "does not match the size and sha256" in capsys.readouterr().err


def test_record_refuses_a_url_that_changed_since_fetch(
    disabled: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = disabled
    write_records(root, "http://127.0.0.1:1")
    assert run("record", root) == 1
    assert "differs from the URL the fetch used" in capsys.readouterr().err


def test_fetch_without_the_wc_record_fails_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_records(root, "http://127.0.0.1:1", wc=False)
    assert run("fetch", root, now=Clock()) == 1
    assert "outputs/pilot/worldcover_window.json" in capsys.readouterr().err
    assert not (root / "data/raw/cors/fetch.json").exists()


def test_unreachable_bucket_exits_non_zero_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    stub = Stub()
    stub.stop()
    write_records(root, stub.base)
    assert run("fetch", root, now=Clock()) == 1
    assert f"{stub.base}/glo30.tif" in capsys.readouterr().err
    assert not (root / "data/raw/cors/fetch.json").exists()


def test_source_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        pilot.CorsSource().range_header = "bytes=0-1"  # type: ignore[misc]


def test_an_s3_error_message_in_the_body_is_quoted(disabled: tuple[Path, Stub]) -> None:
    root, _ = disabled
    origin = url_entry(cors_json(root), "glo30")["origins"][0]
    assert origin["preflight"]["body"]["error_message"] == (
        "CORSResponse: CORS is not enabled for this bucket."
    )
    assert origin["range_get"]["body"]["error_message"] is None
