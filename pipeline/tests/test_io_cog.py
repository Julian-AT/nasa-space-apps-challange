"""Tests for the COG header reader and the per-request range log in io.py (T1-05; PRD 5, 8.3).

A synthetic cloud-optimized GeoTIFF is written into tmp_path and served with Range support by a
server on 127.0.0.1; an autouse fixture refuses to resolve any other host.
"""

from __future__ import annotations

import http.server
import math
import re
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pytest
import rasterio  # type: ignore[import-untyped]
from rasterio.transform import from_origin  # type: ignore[import-untyped]
from rasterio.windows import Window  # type: ignore[import-untyped]

from terraluna_pipeline.io import (
    ByteRanges,
    GeoTiffHeader,
    RangeCacheError,
    RangeExchange,
    read_geotiff_header,
    read_remote_geotiff_window,
)

MARS_EQC = "+proj=eqc +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +R=3396190 +units=m +no_defs +type=crs"
"""An equirectangular CRS on a Mars sphere; a synthetic test CRS only."""

WIDTH, HEIGHT = 600, 700
BLOCK = 8192
NODATA = -32767.0

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


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


def write_cog(path: Path, *, nodata: float | None = NODATA) -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(11)
    array = rng.normal(-2000.0, 50.0, (HEIGHT, WIDTH)).astype(np.float32)
    array[:40, :] = NODATA
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=WIDTH,
        height=HEIGHT,
        count=1,
        dtype="float32",
        crs=MARS_EQC,
        transform=from_origin(4_000_000.0, 1_000_000.0, 20.0, 20.0),
        nodata=nodata,
        blocksize=256,
        compress="deflate",
        predictor=3,
        overview_resampling="nearest",
    ) as dst:
        dst.write(array, 1)
        dst.update_tags(GEOID="mola")
    return array


@dataclass
class RangeServer:
    body: bytes = b""
    requests: list[str] = field(default_factory=list)


class RangeHandler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[RangeServer]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state = self.state
        requested = self.headers.get("Range", "")
        state.requests.append(requested)
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", requested)
        assert match is not None, "the COG must only be read by range"
        first, last = int(match.group(1)), min(int(match.group(2)), len(state.body) - 1)
        data = state.body[first : last + 1]
        self.send_response(206)
        self.send_header("Content-Type", "image/tiff")
        self.send_header("Content-Range", f"bytes {first}-{last}/{len(state.body)}")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", '"cog-v1"')
        self.end_headers()
        self.wfile.write(data)


@dataclass
class Served:
    url: str
    path: Path
    array: npt.NDArray[np.float32]
    state: RangeServer


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    path = tmp_path / "synthetic_dtm.tif"
    array = write_cog(path)
    state = RangeServer(body=path.read_bytes())
    handler = type("Handler", (RangeHandler,), {"state": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield Served(
            f"http://127.0.0.1:{httpd.server_address[1]}/cog/synthetic_dtm.tif", path, array, state
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --- read_geotiff_header ------------------------------------------------------------------------


def test_the_header_states_what_the_file_states(tmp_path: Path) -> None:
    path = tmp_path / "a.tif"
    write_cog(path)
    header = read_geotiff_header(path)
    assert isinstance(header, GeoTiffHeader)
    assert (header.width, header.height, header.count) == (WIDTH, HEIGHT, 1)
    assert header.dtypes == ("float32",)
    assert header.block_shapes == ((256, 256),)
    assert header.overviews == (2,)
    assert header.image_structure["LAYOUT"] == "COG"
    assert header.image_structure["COMPRESSION"] == "DEFLATE"
    assert header.image_structure["PREDICTOR"] == "3"
    assert header.tags == {"GEOID": "mola", "AREA_OR_POINT": "Area"}
    assert header.nodata == (NODATA,)
    assert header.units == (None,)
    assert header.scales == (1.0,)
    assert header.offsets == (0.0,)
    assert header.descriptions == (None,)
    assert header.crs is not None and "3396190" in header.crs
    assert header.transform == from_origin(4_000_000.0, 1_000_000.0, 20.0, 20.0)


def test_a_file_without_nodata_states_none(tmp_path: Path) -> None:
    path = tmp_path / "b.tif"
    write_cog(path, nodata=None)
    assert read_geotiff_header(path).nodata == (None,)


def test_a_file_without_a_geotransform_has_no_transform(tmp_path: Path) -> None:
    path = tmp_path / "plain.tif"
    with rasterio.open(path, "w", driver="GTiff", width=4, height=3, count=2, dtype="uint8") as d:
        d.write(np.zeros((2, 3, 4), dtype=np.uint8))
    header = read_geotiff_header(path)
    assert header.transform is None
    assert header.crs is None
    assert header.count == 2
    assert header.dtypes == ("uint8", "uint8")
    assert header.overviews == ()


# --- the remote read with a header and an exchange log ------------------------------------------


def centre(width: int, height: int) -> Window:
    return Window((width - 256) // 2, (height - 256) // 2, 256, 256)


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 14, 22, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def test_the_header_is_read_through_the_same_ranges_and_decodes_offline(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = read_remote_geotiff_window(
        served.url, centre, block_bytes=BLOCK, with_header=True, now=Clock()
    )
    assert result.header is not None
    local = read_geotiff_header(served.path)
    assert result.header == local

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a socket connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    again = read_geotiff_header(Path("offline.tif"), ranges=result.ranges)
    assert again == local
    window = result.band.array
    assert window.shape == (256, 256)
    row, col = (HEIGHT - 256) // 2, (WIDTH - 256) // 2
    assert np.array_equal(window, served.array[row : row + 256, col : col + 256])
    assert result.bytes_received < served.path.stat().st_size


def test_without_with_header_no_header_is_returned(served: Served) -> None:
    result = read_remote_geotiff_window(served.url, centre, block_bytes=BLOCK)
    assert result.header is None


def test_every_range_request_is_logged_with_its_http_metadata(served: Served) -> None:
    clock = Clock()
    result = read_remote_geotiff_window(
        served.url, centre, block_bytes=BLOCK, with_header=True, now=clock
    )
    exchanges = result.exchanges
    assert len(exchanges) == result.requests == len(served.state.requests) >= 2
    assert all(isinstance(exchange, RangeExchange) for exchange in exchanges)
    size = served.path.stat().st_size
    for exchange, requested in zip(exchanges, served.state.requests, strict=True):
        assert exchange.url == served.url
        assert requested == f"bytes={exchange.requested_first}-{exchange.requested_last}"
        assert exchange.status == 206
        last = min(exchange.requested_last, size - 1)
        assert exchange.content_range == f"bytes {exchange.requested_first}-{last}/{size}"
        assert exchange.bytes_received == last - exchange.requested_first + 1
    assert exchanges[0].requested_first == 0
    assert exchanges[0].requested_last == BLOCK - 1
    assert sum(exchange.bytes_received for exchange in exchanges) == result.bytes_received
    times = [exchange.retrieved_at for exchange in exchanges]
    assert all(t is not None and t.tzinfo is not None for t in times)
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_without_a_clock_the_exchanges_have_no_time(served: Served) -> None:
    result = read_remote_geotiff_window(served.url, centre, block_bytes=BLOCK)
    assert result.exchanges
    assert all(exchange.retrieved_at is None for exchange in result.exchanges)


def test_a_header_byte_not_held_is_never_guessed(served: Served, tmp_path: Path) -> None:
    result = read_remote_geotiff_window(served.url, centre, block_bytes=BLOCK, with_header=True)
    first, last = result.ranges.ranges[0]
    truncated = ByteRanges(
        name=result.ranges.name,
        size=result.ranges.size,
        ranges=((first, min(last, 15)),),
        data=result.ranges.data[: min(last, 15) - first + 1],
    )
    with pytest.raises(RangeCacheError):
        read_geotiff_header(tmp_path / "x.tif", ranges=truncated)


def test_nan_nodata_headers_compare_equal(tmp_path: Path) -> None:
    path = tmp_path / "nan.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=8,
        height=8,
        count=1,
        dtype="float32",
        crs=MARS_EQC,
        transform=from_origin(0.0, 0.0, 1.0, 1.0),
        nodata=float("nan"),
    ) as dst:
        dst.write(np.zeros((1, 8, 8), dtype=np.float32))
    header = read_geotiff_header(path)
    assert header.nodata[0] is not None and math.isnan(header.nodata[0])
    assert header == read_geotiff_header(path)
