"""Tests for the offline read layer (PRD 5 entry rule, 8.3 io.py, 8.11 read_product, 8.12).

Every input is a tiny synthetic file written into tmp_path. The HTTP tests run a server on
127.0.0.1; an autouse fixture refuses to resolve any other host, so no test reaches the network.
"""

from __future__ import annotations

import hashlib
import http.server
import re
import socket
import threading
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pytest
import rasterio  # type: ignore[import-untyped]
from affine import Affine
from pydantic import ValidationError
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.errors import NotGeoreferencedWarning  # type: ignore[import-untyped]
from rasterio.transform import from_origin  # type: ignore[import-untyped]
from rasterio.windows import Window  # type: ignore[import-untyped]

from terraluna_pipeline.io import (
    ProductMetadata,
    RangeNotSupportedError,
    RangeReadError,
    RasterWindow,
    fetch_range,
    read_geotiff_window,
    read_product,
    sha256_file,
)
from terraluna_pipeline.schema import ProductRecord

MOON_POLAR_STEREO = (
    "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs +type=crs"
)
"""A south-polar stereographic CRS on the IAU lunar sphere; a synthetic test CRS only."""


# --- No network ---------------------------------------------------------------------------------

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


# --- sha256_file --------------------------------------------------------------------------------

ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_of_a_known_byte_string(tmp_path: Path) -> None:
    path = tmp_path / "abc.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == ABC_SHA256


def test_sha256_of_an_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert sha256_file(path) == EMPTY_SHA256


@pytest.mark.parametrize("chunk_size", [1, 7, 4096, 1 << 20])
def test_sha256_streams_in_chunks_with_the_same_result(tmp_path: Path, chunk_size: int) -> None:
    data = bytes(range(256)) * 97 + b"tail"
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    assert sha256_file(path, chunk_size=chunk_size) == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("chunk_size", [0, -1])
def test_sha256_rejects_a_non_positive_chunk_size(tmp_path: Path, chunk_size: int) -> None:
    path = tmp_path / "abc.bin"
    path.write_bytes(b"abc")
    with pytest.raises(ValueError, match="chunk_size"):
        sha256_file(path, chunk_size=chunk_size)


# --- Synthetic GeoTIFFs -------------------------------------------------------------------------

WIDTH, HEIGHT = 30, 20
X0, Y0, PIXEL = -12000.0, 8000.0, 5.0


def grid() -> npt.NDArray[np.float32]:
    return np.arange(WIDTH * HEIGHT, dtype=np.float32).reshape(HEIGHT, WIDTH)


def write_geotiff(
    path: Path,
    array: npt.NDArray[Any],
    *,
    crs: str | None = MOON_POLAR_STEREO,
    transform: Affine | None = None,
    nodata: float | None = -9999.0,
    units: tuple[str, ...] | None = None,
    scales: tuple[float, ...] | None = None,
    offsets: tuple[float, ...] | None = None,
) -> Path:
    bands = array if array.ndim == 3 else array[np.newaxis]
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "width": bands.shape[2],
        "height": bands.shape[1],
        "count": bands.shape[0],
        "dtype": bands.dtype,
    }
    if crs is not None:
        profile["crs"] = crs
    if transform is not None:
        profile["transform"] = transform
    if nodata is not None:
        profile["nodata"] = nodata
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(bands)
            if units is not None:
                dst.units = units
            if scales is not None:
                dst.scales = scales
            if offsets is not None:
                dst.offsets = offsets
    assert path.stat().st_size < 50 * 1024
    return path


def dem(tmp_path: Path, **kwargs: Any) -> Path:
    kwargs.setdefault("transform", from_origin(X0, Y0, PIXEL, PIXEL))
    target = tmp_path / "data" / "raw" / "synthetic" / "dem.tif"
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_geotiff(target, grid(), **kwargs)


# --- read_geotiff_window ------------------------------------------------------------------------


def test_windowed_read_returns_shape_values_crs_transform_and_nodata(tmp_path: Path) -> None:
    path = dem(tmp_path)
    window = read_geotiff_window(path, Window(2, 3, 4, 5))
    assert isinstance(window, RasterWindow)
    assert window.array.shape == (5, 4)
    assert window.array.dtype == np.float32
    np.testing.assert_array_equal(window.array, grid()[3:8, 2:6])
    assert window.nodata == -9999.0
    assert window.crs == CRS.from_string(MOON_POLAR_STEREO).to_wkt()
    assert "1737400" in window.crs
    assert window.transform == Affine(PIXEL, 0.0, X0 + 2 * PIXEL, 0.0, -PIXEL, Y0 - 3 * PIXEL)
    assert (window.transform.c, window.transform.f) == (-11990.0, 7985.0)
    assert (window.col_off, window.row_off) == (2, 3)
    assert (window.raster_width, window.raster_height) == (WIDTH, HEIGHT)
    assert window.raster_transform == from_origin(X0, Y0, PIXEL, PIXEL)
    assert window.band == 1
    assert window.units is None
    assert (window.scale, window.offset) == (1.0, 0.0)


def test_read_without_a_window_returns_the_whole_band(tmp_path: Path) -> None:
    window = read_geotiff_window(dem(tmp_path))
    assert window.array.shape == (HEIGHT, WIDTH)
    np.testing.assert_array_equal(window.array, grid())
    assert window.transform == from_origin(X0, Y0, PIXEL, PIXEL)


def test_windowed_read_keeps_nodata_pixels_as_stored(tmp_path: Path) -> None:
    array = grid()
    array[3, 2] = -9999.0
    path = tmp_path / "holes.tif"
    write_geotiff(path, array, transform=from_origin(X0, Y0, PIXEL, PIXEL))
    window = read_geotiff_window(path, Window(2, 3, 2, 2))
    assert window.array[0, 0] == -9999.0
    assert not np.ma.isMaskedArray(window.array)


def test_missing_nodata_is_none_not_zero(tmp_path: Path) -> None:
    window = read_geotiff_window(dem(tmp_path, nodata=None))
    assert window.nodata is None


def test_missing_crs_and_geotransform_are_none_not_assumed(tmp_path: Path) -> None:
    path = write_geotiff(tmp_path / "bare.tif", grid(), crs=None, transform=None)
    window = read_geotiff_window(path, Window(1, 1, 2, 2))
    assert window.crs is None
    assert window.transform is None
    assert window.raster_transform is None


def test_band_units_scale_and_offset_are_read_from_the_file(tmp_path: Path) -> None:
    path = dem(tmp_path, units=("metre",), scales=(0.5,), offsets=(-100.0,))
    window = read_geotiff_window(path, Window(0, 0, 2, 2))
    assert window.units == "metre"
    assert (window.scale, window.offset) == (0.5, -100.0)
    np.testing.assert_array_equal(window.array, grid()[:2, :2])


def test_second_band_of_a_multiband_file(tmp_path: Path) -> None:
    bands = np.stack([grid(), grid() + 1000.0]).astype(np.float32)
    path = write_geotiff(tmp_path / "two.tif", bands, transform=from_origin(X0, Y0, PIXEL, PIXEL))
    window = read_geotiff_window(path, Window(0, 0, 3, 3), band=2)
    np.testing.assert_array_equal(window.array, grid()[:3, :3] + 1000.0)
    assert window.band == 2


@pytest.mark.parametrize(
    "window",
    [
        Window(28, 18, 5, 5),
        Window(-1, 0, 2, 2),
        Window(0, -1, 2, 2),
        Window(0, 0, WIDTH + 1, 1),
        Window(0, 0, 0, 2),
        Window(0, 0, 2, 0),
        Window(1.5, 0, 2, 2),
        Window(0, 0, 2.5, 2),
    ],
    ids=[
        "past-the-edge",
        "negative-column",
        "negative-row",
        "too-wide",
        "zero-width",
        "zero-height",
        "fractional-offset",
        "fractional-size",
    ],
)
def test_windows_outside_the_raster_or_fractional_are_refused(
    tmp_path: Path, window: Window
) -> None:
    with pytest.raises(ValueError, match="window"):
        read_geotiff_window(dem(tmp_path), window)


@pytest.mark.parametrize("band", [0, 2, -1])
def test_a_band_that_does_not_exist_is_refused(tmp_path: Path, band: int) -> None:
    with pytest.raises(ValueError, match="band"):
        read_geotiff_window(dem(tmp_path), band=band)


# --- read_product -------------------------------------------------------------------------------


def metadata(**overrides: Any) -> ProductMetadata:
    value: dict[str, Any] = {
        "provider": "Synthetic test provider",
        "version": "test-1",
        "source_url": "https://data.example.org/synthetic/dem.tif",
        "body": "moon",
        "acquisition_time": None,
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "m",
        "calibration_type": None,
        "effective_resolution": "unknown",
        "incidence_source": None,
        "qa_definition": "unknown",
        "license_or_attribution": "unknown",
    }
    value.update(overrides)
    return value  # type: ignore[return-value]


def test_read_product_fills_the_record_from_the_file(tmp_path: Path) -> None:
    path = dem(tmp_path)
    arrays, record = read_product(
        "synthetic-dem", [path], metadata(), root=tmp_path, window=Window(2, 3, 4, 5)
    )
    assert isinstance(record, ProductRecord)
    assert len(arrays) == 1
    np.testing.assert_array_equal(arrays[0].array, grid()[3:8, 2:6])
    assert record.product_id == "synthetic-dem"
    assert record.checksum == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record.local_path == "data/raw/synthetic/dem.tif"
    assert record.crs_and_frame == CRS.from_string(MOON_POLAR_STEREO).to_wkt()
    assert record.native_grid is not None
    assert f"width={WIDTH} height={HEIGHT}" in record.native_grid
    assert "-12000.0" in record.native_grid
    assert record.units == "m"
    assert record.provider == "Synthetic test provider"
    assert record.source_url == "https://data.example.org/synthetic/dem.tif"
    assert record.body == "moon"
    assert record.frequency_or_wavelength is None


def test_read_product_never_returns_verified(tmp_path: Path) -> None:
    """read_product decodes a file but does not mask or plot it (PRD 5 entry rule)."""
    _, record = read_product("synthetic-dem", [dem(tmp_path)], metadata(), root=tmp_path)
    assert record.verification_status == "PARTIAL"


def test_missing_units_are_unknown_and_partial_never_guessed(tmp_path: Path) -> None:
    value = metadata()
    del value["units"]
    _, record = read_product("synthetic-dem", [dem(tmp_path)], value, root=tmp_path)
    assert record.units == "unknown"
    assert record.verification_status == "PARTIAL"
    with pytest.raises(ValidationError):
        ProductRecord.model_validate({**record.model_dump(), "verification_status": "VERIFIED"})


def test_units_declared_by_the_file_are_used_when_metadata_has_none(tmp_path: Path) -> None:
    value = metadata()
    del value["units"]
    path = dem(tmp_path, units=("metre",))
    _, record = read_product("synthetic-dem", [path], value, root=tmp_path)
    assert record.units == "metre"


def test_units_that_contradict_the_file_are_refused(tmp_path: Path) -> None:
    path = dem(tmp_path, units=("metre",))
    with pytest.raises(ValueError, match="units"):
        read_product("synthetic-dem", [path], metadata(units="km"), root=tmp_path)


def test_bands_with_different_units_are_unknown_without_metadata(tmp_path: Path) -> None:
    bands = np.stack([grid(), grid()]).astype(np.float32)
    path = tmp_path / "data" / "raw" / "two.tif"
    path.parent.mkdir(parents=True)
    write_geotiff(path, bands, transform=from_origin(X0, Y0, PIXEL, PIXEL), units=("m", "count"))
    value = metadata()
    del value["units"]
    arrays, record = read_product("two-band", [path], value, root=tmp_path)
    assert [window.band for window in arrays] == [1, 2]
    assert [window.units for window in arrays] == ["m", "count"]
    assert record.units == "unknown"


def test_missing_descriptive_metadata_is_unknown_and_explicit_null_stays_null(
    tmp_path: Path,
) -> None:
    value: ProductMetadata = {"body": "mars", "units": "m", "frequency_or_wavelength": None}
    _, record = read_product("synthetic-dem", [dem(tmp_path)], value, root=tmp_path)
    assert record.frequency_or_wavelength is None
    assert record.source_url is None
    for name in (
        "provider",
        "version",
        "acquisition_time",
        "transmit_receive_convention",
        "calibration_type",
        "effective_resolution",
        "incidence_source",
        "qa_definition",
        "license_or_attribution",
    ):
        assert getattr(record, name) == "unknown", name


def test_a_file_without_crs_records_unknown_crs(tmp_path: Path) -> None:
    path = tmp_path / "data" / "raw" / "bare.tif"
    path.parent.mkdir(parents=True)
    write_geotiff(path, grid(), crs=None, transform=None)
    _, record = read_product("bare", [path], metadata(), root=tmp_path)
    assert record.crs_and_frame == "unknown"
    assert record.native_grid is not None
    assert "no geotransform" in record.native_grid


def test_body_is_required(tmp_path: Path) -> None:
    value = metadata()
    del value["body"]
    with pytest.raises(ValueError, match="body"):
        read_product("synthetic-dem", [dem(tmp_path)], value, root=tmp_path)


@pytest.mark.parametrize(
    "key", ["unit", "checksum", "local_path", "crs_and_frame", "native_grid", "product_id"]
)
def test_unknown_or_file_derived_metadata_keys_are_refused(tmp_path: Path, key: str) -> None:
    value: dict[str, Any] = dict(metadata())
    value[key] = "m"
    with pytest.raises(ValueError, match=key):
        read_product("synthetic-dem", [dem(tmp_path)], value, root=tmp_path)  # type: ignore[arg-type]


def test_a_signed_source_url_is_refused(tmp_path: Path) -> None:
    signed = metadata(source_url="https://bucket.example.org/dem.tif?X-Amz-Signature=abc")
    with pytest.raises(ValidationError):
        read_product("synthetic-dem", [dem(tmp_path)], signed, root=tmp_path)


def test_a_file_outside_root_is_refused(tmp_path: Path) -> None:
    path = dem(tmp_path)
    with pytest.raises(ValueError, match="root"):
        read_product("synthetic-dem", [path], metadata(), root=tmp_path / "elsewhere")


@pytest.mark.parametrize("count", [0, 2])
def test_one_record_reads_exactly_one_file(tmp_path: Path, count: int) -> None:
    path = dem(tmp_path)
    with pytest.raises(ValueError, match="one file"):
        read_product("synthetic-dem", [path] * count, metadata(), root=tmp_path)


# --- fetch_range against a local server --------------------------------------------------------

PAYLOAD = bytes(range(256)) * 40
"""10,240 bytes of synthetic content served by the test server."""


@dataclass
class ServerState:
    mode: str = "range"
    requests: list[dict[str, str]] = field(default_factory=list)


class RangeHandler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[ServerState]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state = self.state
        state.requests.append(
            {"path": self.path, **{k.lower(): v for k, v in self.headers.items()}}
        )
        mode = state.mode
        if self.path.startswith("/signed"):
            mode = "range"
        if mode == "missing":
            self.send_error(404, "Not Found")
            return
        if mode == "redirect-signed":
            self.send_response(302)
            self.send_header("Location", "/signed?X-Amz-Signature=abc")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "redirect" and self.path == "/payload.bin":
            self.send_response(302)
            self.send_header("Location", "/moved")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "ignore":
            self._send(200, PAYLOAD, {})
            return
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", self.headers.get("Range", ""))
        if match is None:
            self._send(200, PAYLOAD, {})
            return
        first, last = int(match.group(1)), min(int(match.group(2)), len(PAYLOAD) - 1)
        body = PAYLOAD[first : last + 1]
        headers = {"Content-Range": f"bytes {first}-{last}/{len(PAYLOAD)}"}
        if mode == "no-content-range":
            headers = {}
        elif mode == "wrong-start":
            headers["Content-Range"] = f"bytes {first + 1}-{last}/{len(PAYLOAD)}"
        elif mode == "short-range":
            headers["Content-Range"] = f"bytes {first}-{last - 10}/{len(PAYLOAD)}"
            body = body[:-10]
        elif mode == "truncated":
            body = body[: len(body) // 2]
        elif mode == "gzip":
            headers["Content-Encoding"] = "gzip"
        elif mode == "unknown-total":
            headers["Content-Range"] = f"bytes {first}-{last}/*"
        self._send(206, body, headers)

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server() -> Iterator[tuple[str, ServerState]]:
    state = ServerState()
    handler = type("Handler", (RangeHandler,), {"state": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/payload.bin", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_range_read_returns_exactly_the_requested_bytes(
    server: tuple[str, ServerState],
) -> None:
    url, state = server
    response = fetch_range(url, 100, 1123)
    assert response.data == PAYLOAD[100:1124]
    assert response.bytes_received == 1024
    assert response.status == 206
    assert response.content_range == "bytes 100-1123/10240"
    assert (response.first_byte, response.last_byte, response.total_size) == (100, 1123, 10240)
    assert (response.requested_first, response.requested_last) == (100, 1123)
    assert response.url == response.final_url == url
    assert state.requests[0]["range"] == "bytes=100-1123"
    assert state.requests[0]["accept-encoding"] == "identity"


def test_range_read_of_one_byte(server: tuple[str, ServerState]) -> None:
    url, _ = server
    response = fetch_range(url, 0, 0)
    assert response.data == PAYLOAD[:1]
    assert response.bytes_received == 1


def test_range_past_the_end_is_clamped_by_the_server_and_recorded(
    server: tuple[str, ServerState],
) -> None:
    url, _ = server
    response = fetch_range(url, 10000, 20000)
    assert response.data == PAYLOAD[10000:]
    assert response.bytes_received == 240
    assert (response.first_byte, response.last_byte) == (10000, 10239)


def test_range_read_with_unknown_total_size(server: tuple[str, ServerState]) -> None:
    url, state = server
    state.mode = "unknown-total"
    response = fetch_range(url, 5, 9)
    assert response.data == PAYLOAD[5:10]
    assert response.total_size is None


def test_range_read_follows_a_plain_redirect_and_records_it(
    server: tuple[str, ServerState],
) -> None:
    url, state = server
    state.mode = "redirect"
    response = fetch_range(url, 0, 9)
    assert response.data == PAYLOAD[:10]
    assert response.url == url
    assert response.final_url == url.replace("/payload.bin", "/moved")
    assert [request["path"] for request in state.requests] == ["/payload.bin", "/moved"]
    assert state.requests[1]["range"] == "bytes=0-9"


def test_server_ignoring_range_raises(server: tuple[str, ServerState]) -> None:
    url, state = server
    state.mode = "ignore"
    with pytest.raises(RangeNotSupportedError, match="200"):
        fetch_range(url, 0, 1023)
    assert issubclass(RangeNotSupportedError, RangeReadError)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("no-content-range", "Content-Range"),
        ("wrong-start", "Content-Range"),
        ("short-range", "shorter"),
        ("truncated", "bytes"),
        ("gzip", "Content-Encoding"),
        ("missing", "404"),
    ],
)
def test_bad_partial_responses_raise(
    server: tuple[str, ServerState], mode: str, message: str
) -> None:
    url, state = server
    state.mode = mode
    with pytest.raises(RangeReadError, match=message) as excinfo:
        fetch_range(url, 100, 1123)
    assert url in str(excinfo.value)


def test_redirect_to_a_signed_url_is_refused_before_it_is_followed(
    server: tuple[str, ServerState],
) -> None:
    url, state = server
    state.mode = "redirect-signed"
    with pytest.raises(RangeReadError, match="credentials or a signature"):
        fetch_range(url, 0, 9)
    assert [request["path"] for request in state.requests] == ["/payload.bin"]


def test_signed_url_is_refused_before_any_request(server: tuple[str, ServerState]) -> None:
    url, state = server
    with pytest.raises(ValueError, match="credentials or a signature"):
        fetch_range(url + "?X-Amz-Signature=abc", 0, 9)
    assert state.requests == []


def test_connection_failure_raises_a_clear_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}/payload.bin"
    with pytest.raises(RangeReadError, match="could not connect") as excinfo:
        fetch_range(url, 0, 9, timeout=5.0)
    assert url in str(excinfo.value)


def test_dns_failure_raises_a_clear_error() -> None:
    url = "http://products.example.invalid/payload.bin"
    with pytest.raises(RangeReadError, match="could not connect") as excinfo:
        fetch_range(url, 0, 9, timeout=5.0)
    assert url in str(excinfo.value)


@pytest.mark.parametrize(("first", "last"), [(-1, 9), (10, 9), (0, -1)])
def test_invalid_byte_ranges_are_refused(first: int, last: int) -> None:
    with pytest.raises(ValueError, match="range"):
        fetch_range("http://127.0.0.1:9/payload.bin", first, last)


@pytest.mark.parametrize(
    "url", ["file:///etc/hosts", "ftp://127.0.0.1/payload.bin", "127.0.0.1/payload.bin"]
)
def test_only_http_urls_are_read(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        fetch_range(url, 0, 9)
