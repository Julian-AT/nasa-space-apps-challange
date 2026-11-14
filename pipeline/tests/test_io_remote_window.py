"""Tests for windowed reads of a remote GeoTIFF and their offline re-decode (T1-03, PRD 5).

Every input is a synthetic GeoTIFF written into tmp_path and served by a server on 127.0.0.1; an
autouse fixture refuses to resolve any other host, so no test reaches the network.
"""

from __future__ import annotations

import hashlib
import http.server
import re
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pytest
import rasterio  # type: ignore[import-untyped]
from rasterio.transform import from_origin  # type: ignore[import-untyped]
from rasterio.windows import Window  # type: ignore[import-untyped]

from terraluna_pipeline.io import (
    RANGE_BLOCK_BYTES,
    ByteRanges,
    ProductMetadata,
    RangeCacheError,
    RangeNotSupportedError,
    RangeReadError,
    RangeResponse,
    RemoteWindow,
    fetch_range,
    read_geotiff_tags,
    read_geotiff_window,
    read_product,
    read_remote_geotiff_window,
    sha256_file,
)

MOON_POLAR_STEREO = (
    "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs +type=crs"
)
"""A south-polar stereographic CRS on the IAU lunar sphere; a synthetic test CRS only."""

SIZE = 512
BLOCK = 16384
WINDOW = Window(128, 128, 64, 64)

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


# --- Synthetic file and server ------------------------------------------------------------------


def write_synthetic(path: Path) -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(7)
    array = rng.normal(100.0, 20.0, (SIZE, SIZE)).astype(np.float32)
    array[130, 140] = np.nan
    array[131, 141] = np.nan
    array[300, 300] = np.nan
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=SIZE,
        height=SIZE,
        count=1,
        dtype="float32",
        crs=MOON_POLAR_STEREO,
        transform=from_origin(-1280.0, 1280.0, 5.0, 5.0),
        nodata=float("nan"),
        tiled=True,
        blockxsize=64,
        blockysize=64,
    ) as dst:
        dst.write(array, 1)
        dst.update_tags(SYNTHETIC="yes", AREA_OR_POINT="Area")
    return array


@dataclass
class ServerState:
    body: bytes = b""
    mode: str = "range"
    requests: list[dict[str, str]] = field(default_factory=list)
    served: int = 0


class RemoteHandler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[ServerState]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state = self.state
        state.requests.append({k.lower(): v for k, v in self.headers.items()})
        body = state.body
        etag = '"v1"' if state.mode != "etag-change" or len(state.requests) == 1 else '"v2"'
        common = {
            "Content-Type": "image/tiff",
            "Last-Modified": "Sat, 01 Jan 2000 00:00:00 GMT",
            "ETag": etag,
        }
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", self.headers.get("Range", ""))
        if state.mode == "ignore" or match is None:
            self._send(200, body, common)
            return
        first, last = int(match.group(1)), min(int(match.group(2)), len(body) - 1)
        total = "*" if state.mode == "unknown-total" else str(len(body))
        headers = {**common, "Content-Range": f"bytes {first}-{last}/{total}"}
        self._send(206, body[first : last + 1], headers)

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.server_state().served += len(body) if status == 206 else 0
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def server_state(self) -> ServerState:
        return self.state


@dataclass
class Remote:
    url: str
    path: Path
    array: npt.NDArray[np.float32]
    state: ServerState


@pytest.fixture
def remote(tmp_path: Path) -> Iterator[Remote]:
    path = tmp_path / "synthetic.tif"
    array = write_synthetic(path)
    state = ServerState(body=path.read_bytes())
    handler = type("Handler", (RemoteHandler,), {"state": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield Remote(
            f"http://127.0.0.1:{httpd.server_address[1]}/dir/synthetic.tif", path, array, state
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def read_window(remote: Remote, **kwargs: Any) -> RemoteWindow:
    return read_remote_geotiff_window(
        remote.url, lambda width, height: WINDOW, block_bytes=BLOCK, **kwargs
    )


# --- The windowed remote read -------------------------------------------------------------------


def test_remote_window_equals_a_local_read_and_transfers_little(remote: Remote) -> None:
    result = read_window(remote)
    local = read_geotiff_window(remote.path, WINDOW)
    got = result.band
    assert got.array.dtype == local.array.dtype == np.float32
    assert np.array_equal(got.array, local.array, equal_nan=True)
    assert np.array_equal(got.array, remote.array[128:192, 128:192], equal_nan=True)
    assert int(np.isnan(got.array).sum()) == 2
    assert got.crs == local.crs
    assert got.crs is not None and "1737400" in got.crs
    assert got.transform == local.transform
    assert got.nodata is not None and np.isnan(got.nodata)
    assert (got.col_off, got.row_off) == (128, 128)
    assert result.window == (128, 128, 64, 64)
    assert (result.raster_width, result.raster_height) == (SIZE, SIZE)
    file_size = remote.path.stat().st_size
    assert result.bytes_received < 0.1 * file_size
    assert result.bytes_received == len(result.ranges.data) == remote.state.served
    assert result.ranges.size == file_size
    assert result.ranges.name == "synthetic.tif"
    assert result.status == 206
    assert result.url == result.final_url == remote.url
    assert result.content_type == "image/tiff"
    assert result.last_modified == "Sat, 01 Jan 2000 00:00:00 GMT"
    assert result.etag == '"v1"'
    assert result.requests == len(remote.state.requests) >= 2
    assert all(request["range"].startswith("bytes=") for request in remote.state.requests)
    assert remote.state.requests[0]["range"] == f"bytes=0-{BLOCK - 1}"
    file_bytes = remote.path.read_bytes()
    cursor = 0
    for first, last in result.ranges.ranges:
        length = last - first + 1
        assert result.ranges.data[cursor : cursor + length] == file_bytes[first : last + 1]
        cursor += length


def test_default_block_size_is_a_quarter_mebibyte_and_works(remote: Remote) -> None:
    assert RANGE_BLOCK_BYTES == 1 << 18
    result = read_remote_geotiff_window(remote.url, lambda width, height: WINDOW)
    assert np.array_equal(result.band.array, remote.array[128:192, 128:192], equal_nan=True)
    assert remote.state.requests[0]["range"] == f"bytes=0-{RANGE_BLOCK_BYTES - 1}"
    assert result.bytes_received < remote.path.stat().st_size


def test_choose_window_receives_the_raster_size(remote: Remote) -> None:
    seen: list[tuple[int, int]] = []

    def choose(width: int, height: int) -> Window:
        seen.append((width, height))
        return Window(0, 0, 8, 8)

    result = read_remote_geotiff_window(remote.url, choose, block_bytes=BLOCK)
    assert seen == [(SIZE, SIZE)]
    assert np.array_equal(result.band.array, remote.array[:8, :8], equal_nan=True)


def test_a_window_outside_the_raster_is_refused(remote: Remote) -> None:
    with pytest.raises(ValueError, match="does not lie inside"):
        read_remote_geotiff_window(
            remote.url, lambda width, height: Window(500, 500, 64, 64), block_bytes=BLOCK
        )


# --- Offline re-decode --------------------------------------------------------------------------


def refuse_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a socket connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_the_window_decodes_offline_from_the_byte_ranges(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = read_window(remote)
    refuse_sockets(monkeypatch)
    again = read_geotiff_window(tmp_path / "does-not-exist.tif", WINDOW, ranges=result.ranges)
    assert np.array_equal(again.array, result.band.array, equal_nan=True)
    assert again.array.dtype == np.float32
    assert again.crs == result.band.crs
    assert again.transform == result.band.transform
    tags = read_geotiff_tags(tmp_path / "does-not-exist.tif", ranges=result.ranges)
    assert tags["SYNTHETIC"] == "yes"
    assert tags == read_geotiff_tags(remote.path)
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in tags.items())


def test_read_product_from_byte_ranges(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = read_window(remote)
    cache = tmp_path / "data" / "raw" / "x" / "synthetic.ranges.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(result.ranges.data)
    refuse_sockets(monkeypatch)
    metadata: ProductMetadata = {"body": "moon"}
    bands, record = read_product(
        "synthetic-dem", [cache], metadata, root=tmp_path, window=WINDOW, ranges=result.ranges
    )
    assert len(bands) == 1
    assert np.array_equal(bands[0].array, result.band.array, equal_nan=True)
    assert record.checksum == sha256_file(cache) == hashlib.sha256(result.ranges.data).hexdigest()
    assert record.local_path == "data/raw/x/synthetic.ranges.bin"
    assert record.crs_and_frame == result.band.crs
    assert record.verification_status == "PARTIAL"


def test_read_product_refuses_ranges_that_differ_from_the_local_file(
    remote: Remote, tmp_path: Path
) -> None:
    result = read_window(remote)
    cache = tmp_path / "data" / "raw" / "x" / "other.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(result.ranges.data[:-1] + bytes([result.ranges.data[-1] ^ 1]))
    with pytest.raises(ValueError, match=r"other\.bin"):
        read_product(
            "synthetic-dem",
            [cache],
            {"body": "moon"},
            root=tmp_path,
            window=WINDOW,
            ranges=result.ranges,
        )


def test_a_missing_block_raises_and_never_yields_zeros(remote: Remote, tmp_path: Path) -> None:
    result = read_window(remote)
    head = ByteRanges(
        name=result.ranges.name,
        size=result.ranges.size,
        ranges=((0, BLOCK - 1),),
        data=result.ranges.read(0, BLOCK),
    )
    with pytest.raises(RangeCacheError, match=r"synthetic\.tif"):
        read_geotiff_window(tmp_path / "x.tif", WINDOW, ranges=head)


# --- Failures of the server ---------------------------------------------------------------------


def test_a_server_that_ignores_range_is_reported(remote: Remote) -> None:
    remote.state.mode = "ignore"
    with pytest.raises(RangeNotSupportedError):
        read_window(remote)
    assert len(remote.state.requests) == 1


def test_an_unknown_total_size_is_refused(remote: Remote) -> None:
    remote.state.mode = "unknown-total"
    with pytest.raises(RangeReadError, match="file size"):
        read_window(remote)


def test_a_changing_etag_is_refused(remote: Remote) -> None:
    remote.state.mode = "etag-change"
    with pytest.raises(RangeReadError, match="changed while it was read"):
        read_window(remote)
    assert len(remote.state.requests) >= 2


def test_max_bytes_stops_the_read_before_the_request_that_would_reach_it(
    remote: Remote,
) -> None:
    cap = BLOCK + 1000
    with pytest.raises(RangeReadError, match="whole"):
        read_window(remote, max_bytes=cap)
    assert remote.state.served < cap
    assert len(remote.state.requests) == 1


@pytest.mark.parametrize("extra", [0, 1, 50_000])
def test_a_first_request_that_returns_the_whole_file_is_refused(
    remote: Remote, extra: int
) -> None:
    # A file no larger than one block arrives whole with the first request: not a window read.
    size = remote.path.stat().st_size
    with pytest.raises(RangeReadError, match="whole") as info:
        read_remote_geotiff_window(remote.url, lambda w, h: WINDOW, block_bytes=size + extra)
    assert remote.url in str(info.value)
    assert f"{size}-byte" in str(info.value)
    assert len(remote.state.requests) == 1


def test_a_request_that_would_complete_the_file_is_not_sent(remote: Remote) -> None:
    # Two blocks: the header is in the first, the bottom-right tile in the second, so reading
    # that tile would hold every byte of the file.
    size = remote.path.stat().st_size
    half = -(-size // 2)
    corner = Window(SIZE - 64, SIZE - 64, 64, 64)
    with pytest.raises(RangeReadError, match="whole") as info:
        read_remote_geotiff_window(remote.url, lambda w, h: corner, block_bytes=half)
    assert remote.url in str(info.value)
    assert len(remote.state.requests) == 1
    assert remote.state.served == half < size


def test_a_window_read_never_holds_the_whole_file(remote: Remote) -> None:
    result = read_window(remote)
    assert not result.ranges.covers_whole_file
    assert len(result.ranges.data) < result.ranges.size


def test_a_signed_url_is_refused_before_any_request(remote: Remote) -> None:
    with pytest.raises(ValueError, match="signature"):
        read_remote_geotiff_window(
            remote.url + "?X-Amz-Signature=abc", lambda width, height: WINDOW
        )
    assert remote.state.requests == []


@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_limits_are_refused(remote: Remote, bad: int) -> None:
    with pytest.raises(ValueError, match="block_bytes"):
        read_remote_geotiff_window(remote.url, lambda w, h: WINDOW, block_bytes=bad)
    with pytest.raises(ValueError, match="max_bytes"):
        read_remote_geotiff_window(remote.url, lambda w, h: WINDOW, max_bytes=bad)
    assert remote.state.requests == []


def test_a_url_without_a_file_name_is_refused(remote: Remote) -> None:
    with pytest.raises(ValueError, match="file name"):
        read_remote_geotiff_window(remote.url.rsplit("/", 1)[0] + "/", lambda w, h: WINDOW)
    assert remote.state.requests == []


# --- fetch_range headers ------------------------------------------------------------------------


def test_fetch_range_reports_content_type_last_modified_and_etag(remote: Remote) -> None:
    response = fetch_range(remote.url, 0, 99)
    assert isinstance(response, RangeResponse)
    assert response.content_type == "image/tiff"
    assert response.last_modified == "Sat, 01 Jan 2000 00:00:00 GMT"
    assert response.etag == '"v1"'
    assert response.data == remote.path.read_bytes()[:100]


# --- ByteRanges ---------------------------------------------------------------------------------


def make(ranges: tuple[tuple[int, int], ...], data: bytes, *, size: int = 100) -> ByteRanges:
    return ByteRanges(name="f.tif", size=size, ranges=ranges, data=data)


def test_byte_ranges_read_within_a_range_and_at_the_end() -> None:
    data = bytes(range(10)) + bytes(range(20, 25))
    held = make(((10, 19), (90, 94)), data)
    assert held.read(10, 10) == bytes(range(10))
    assert held.read(12, 3) == bytes([2, 3, 4])
    assert held.read(90, 5) == bytes(range(20, 25))
    assert held.read(100, 5) == b""
    assert held.read(150, 1) == b""
    assert held.read(10, 0) == b""


@pytest.mark.parametrize(
    ("ranges", "whole"),
    [
        (((0, 99),), True),
        (((0, 98),), False),
        (((1, 99),), False),
        (((0, 49), (51, 99)), False),
        (((40, 59),), False),
    ],
)
def test_byte_ranges_state_whether_they_hold_the_whole_file(
    ranges: tuple[tuple[int, int], ...], whole: bool
) -> None:
    held = make(ranges, bytes(sum(last - first + 1 for first, last in ranges)))
    assert held.covers_whole_file is whole


def test_byte_ranges_clip_to_the_file_size() -> None:
    held = make(((95, 99),), bytes(range(5)))
    assert held.read(95, 50) == bytes(range(5))


def test_byte_ranges_refuse_a_read_across_a_gap_and_name_the_span() -> None:
    held = make(((10, 19), (30, 39)), bytes(20))
    with pytest.raises(RangeCacheError, match=r"f\.tif") as info:
        held.read(15, 20)
    assert "20-29" in str(info.value)
    with pytest.raises(RangeCacheError):
        held.read(0, 5)


@pytest.mark.parametrize(
    ("ranges", "length"),
    [
        (((20, 29), (10, 19)), 20),  # unsorted
        (((10, 25), (20, 29)), 26),  # overlapping
        (((10, 19), (20, 29)), 20),  # adjacent
        (((10, 100),), 91),  # last >= size
        (((10, 19),), 9),  # data too short
        (((10, 19),), 11),  # data too long
        (((19, 10),), 10),  # last < first
        (((-1, 5),), 7),  # negative
        ((), 0),  # no range
    ],
)
def test_byte_ranges_validate_their_ranges(
    ranges: tuple[tuple[int, int], ...], length: int
) -> None:
    with pytest.raises(ValueError):
        make(ranges, bytes(length))


@pytest.mark.parametrize("name", ["", "a/b.tif", "..", "."])
def test_byte_ranges_validate_the_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        ByteRanges(name=name, size=10, ranges=((0, 4),), data=bytes(5))


def test_byte_ranges_validate_the_size() -> None:
    with pytest.raises(ValueError, match="size"):
        ByteRanges(name="f.tif", size=0, ranges=((0, 0),), data=bytes(1))
