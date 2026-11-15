"""Tests for the wc pilot source (T1-07; PRD 5 row 12, 8.7 step 4, 9 G0).

A stub server on 127.0.0.1 serves a synthetic bucket readme, a synthetic WorldCover tiling grid
(GeoJSON, as the bucket's) and, for the pinned v200 2021 map key of a grid tile, a synthetic
EPSG:4326 uint8 COG with the provider's tag layout (legend, licence, copyright, time range) and
Range support. fetch reads it through the real io.fetch_file and io.fetch_range; nothing is
recorded from a real server. The area comes from a synthetic T1-06 record
(outputs/pilot/cop30_range_read.json) written into the test repository. record and check run
after the server has stopped, with sockets refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.server
import json
import re
import socket
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pytest
import rasterio  # type: ignore[import-untyped]
from affine import Affine
from pydantic import TypeAdapter

from terraluna_pipeline import pilot
from terraluna_pipeline.schema import ProductRecord, SourcesFile

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to resolve anything but the loopback interface."""
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


# --- Synthetic provider content -----------------------------------------------------------------

TILE = "N63W018"
PIXEL = 0.0025
"""Synthetic pixel size in degrees (the real maps use 1/12000 degree)."""
SIDE = 1200
"""Pixels per side of the synthetic 3-degree tile."""
BLOCK = 64
LEGEND = (
    "10  Tree cover\n20  Shrubland\n30  Grassland\n40  Cropland\n50  Built-up\n"
    "60  Bare/sparse vegetation\n70  Snow and ice\n80  Permanent water bodies\n"
    "90  Herbaceous wetland\n95  Mangroves\n100 Moss and lichen\n"
)
LICENCE = "CC-BY 4.0 - https://creativecommons.org/licenses/by/4.0/"
COPYRIGHT = (
    "ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed "
    "by ESA WorldCover consortium"
)
CENTRE = {"latitude": 64.85, "longitude": -16.83}
AREA_BOUNDS = {
    "west": -16.884260229365424,
    "south": 64.82692184916675,
    "east": -16.775827411011576,
    "north": 64.87306071610783,
}
NOT_IN_LEGEND = 42


def readme() -> str:
    return """<html><head><title>ESA WorldCover</title></head><body><h1>ESA WorldCover</h1>
<p>ESA WorldCover is a derived product.</p>
<ul><li>Time period: 2020</li><li>Resolution: 10m</li><li>CRS: EPSG:4326</li></ul>
<p>The ESA WorldCover 10 m 2020 V100 product is delivered in 3x3 degree tiles as Cloud
Optimized GeoTIFFs (COGs) in EPSG:4326 projection (geographic latitude/longitude CRS).</p>
</body></html>"""


def square(tile: str) -> list[list[float]]:
    match = re.fullmatch(r"([NS])([0-9]{2})([EW])([0-9]{3})", tile)
    assert match is not None
    south = int(match[2]) * (1 if match[1] == "N" else -1)
    west = int(match[4]) * (1 if match[3] == "E" else -1)
    return [
        [float(west), float(south)],
        [float(west), float(south + 3)],
        [float(west + 3), float(south + 3)],
        [float(west + 3), float(south)],
        [float(west), float(south)],
    ]


def grid(tiles: list[str]) -> bytes:
    features = [
        {
            "type": "Feature",
            "properties": {"ll_tile": tile},
            "geometry": {"type": "Polygon", "coordinates": [square(tile)]},
        }
        for tile in tiles
    ]
    return json.dumps(
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": features,
        }
    ).encode()


def map_key(tile: str) -> str:
    return f"v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"


CODES = np.array([10, 20, 30, 60, 70, 80, 100], dtype=np.uint8)


def write_map(path: Path, tile: str = TILE) -> npt.NDArray[np.uint8]:
    corner = square(tile)[1]
    rng = np.random.default_rng(7)
    array = CODES[rng.integers(0, len(CODES), (SIDE, SIDE))]
    # Nodata (0) and a code the legend does not state, inside the window around the centre.
    row = int((corner[1] - CENTRE["latitude"]) / PIXEL)
    col = int((CENTRE["longitude"] - corner[0]) / PIXEL)
    array[row - 2 : row, col - 3 : col] = 0
    array[row : row + 1, col : col + 2] = NOT_IN_LEGEND
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=SIDE,
        height=SIDE,
        count=1,
        dtype="uint8",
        nodata=0,
        crs="EPSG:4326",
        transform=Affine(PIXEL, 0.0, corner[0], 0.0, -PIXEL, corner[1]),
        blocksize=BLOCK,
        compress="deflate",
    ) as dst:
        dst.write(array, 1)
        dst.update_tags(
            legend=LEGEND,
            license=LICENCE,
            copyright=COPYRIGHT,
            product_tile=tile,
            product_version="V2.0.0",
            algorithm_version="V2.0.0",
            time_start="2021-01-01T00:00:00Z",
            time_end="2021-12-31T23:59:59Z",
            title="ESA WorldCover product at 10m resolution for year 2021",
        )
    return array


@dataclass
class Content:
    tiles: list[str] = field(default_factory=lambda: ["S54E168", TILE, "N63W015"])
    fail: dict[str, int] = field(default_factory=dict)


@dataclass
class ServerState:
    content: Content
    cog: bytes
    requests: list[tuple[str, str | None]] = field(default_factory=list)


class StubHandler(http.server.BaseHTTPRequestHandler):
    state: ClassVar[ServerState]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        state = self.state
        state.requests.append((self.path, self.headers.get("Range")))
        for prefix, status in state.content.fail.items():
            if self.path.startswith(prefix):
                self._send(status, b"failure", "text/plain")
                return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/readme.html":
            self._send(200, readme().encode(), "text/html")
        elif path == "/v200/2021/esa_worldcover_grid.geojson":
            self._send(200, grid(state.content.tiles), "application/geo+json")
        elif path == f"/{map_key(TILE)}":
            match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", self.headers.get("Range", ""))
            assert match is not None, "a map must only be read by range"
            data = state.cog
            first, last = int(match[1]), min(int(match[2]), len(data) - 1)
            self._send(
                206,
                data[first : last + 1],
                "binary/octet-stream",
                Content_Range=f"bytes {first}-{last}/{len(data)}",
                ETag='"map-v1"',
                Last_Modified="Wed, 26 Oct 2022 13:03:03 GMT",
            )
        else:
            self._send(404, b"not found", "text/plain")


class Stub:
    def __init__(self, tmp: Path, content: Content) -> None:
        path = tmp / "served_map.tif"
        self.array = write_map(path)
        self.state = ServerState(content=content, cog=path.read_bytes())
        handler = type("Handler", (StubHandler,), {"state": self.state})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def source(self, **fields: Any) -> pilot.WorldcoverSource:
        defaults: dict[str, Any] = {
            "endpoint": self.base,
            "range_block_bytes": 4096,
            "max_window_pixels": 32,
        }
        return pilot.WorldcoverSource(**(defaults | fields))


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 15, 2, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def write_area(root: Path, **changes: Any) -> None:
    """A synthetic T1-06 record with the fields the wc source reads."""
    window = {"centre": CENTRE, "geographic_bounds_deg": AREA_BOUNDS} | changes
    record = {
        "record": "cop30_range_read",
        "dem": {
            "tile": "Copernicus_DSM_COG_10_N64_00_W017_00_DEM",
            "product_id": "COP30/x/y.tif",
            "window": window,
        },
    }
    path = root / "outputs/pilot/cop30_range_read.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))


def run_fetch(root: Path, stub: Stub, **fields: Any) -> int:
    return pilot.main(
        ["fetch", "wc"], root=root, sources={"wc": stub.source(**fields)}, now=Clock()
    )


def run(command: str, root: Path, stub: Stub, **fields: Any) -> int:
    return pilot.main([command, "wc"], root=root, sources={"wc": stub.source(**fields)})


def fetch_and_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: Content | None = None,
    **fields: Any,
) -> tuple[Path, Stub]:
    root = tmp_path / "repo"
    root.mkdir()
    write_area(root)
    server = Stub(tmp_path, content or Content())
    try:
        assert run_fetch(root, server, **fields) == 0
    finally:
        server.stop()
    with monkeypatch.context() as patch:
        refuse_sockets(patch)
        assert run("record", root, server, **fields) == 0
    return root, server


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Stub]:
    return fetch_and_record(tmp_path, monkeypatch)


def fetch_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((root / "data/raw/worldcover/fetch.json").read_bytes())
    return result


def wc_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        (root / "outputs/pilot/worldcover_window.json").read_bytes()
    )
    return result


def sources(root: Path) -> list[ProductRecord]:
    return TypeAdapter(SourcesFile).validate_json((root / "outputs/sources.json").read_bytes())


def window_values(stub: Stub, window: dict[str, int]) -> npt.NDArray[np.uint8]:
    return stub.array[
        window["row_off"] : window["row_off"] + window["height"],
        window["col_off"] : window["col_off"] + window["width"],
    ]


# --- Tests --------------------------------------------------------------------------------------


def test_registered_with_raw_directory_worldcover() -> None:
    source = pilot.SOURCES["wc"]
    assert source.name == "wc"
    assert isinstance(source, pilot.WorldcoverSource)
    assert source.raw_directory == "worldcover"


def test_default_source_reads_the_real_bucket_and_pins_v200_2021() -> None:
    source = pilot.WorldcoverSource()
    assert source.base_url == "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
    assert (source.version, source.year) == ("v200", 2021)
    assert source.max_window_pixels == 512
    assert source.map_url(TILE) == (
        "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
        "ESA_WorldCover_10m_2021_v200_N63W018_Map.tif"
    )


def test_fetch_saves_raw_files_under_data_raw_worldcover(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    raw = root / "data/raw/worldcover"
    metadata = fetch_json(root)
    for entry in [*metadata["documents"], *metadata["files"]]:
        local = raw / entry["file_name"]
        assert local.is_file()
        assert hashlib.sha256(local.read_bytes()).hexdigest() == entry["sha256"]
        assert entry["retrieved_at"].startswith("2026-11-15T02:00:")
    (window,) = metadata["files"]
    assert window["read"] == "window"
    assert window["provider_file_name"] == f"ESA_WorldCover_10m_2021_v200_{TILE}_Map.tif"
    assert all(exchange["http_status"] == 206 for exchange in window["exchanges"])
    assert not (root / "data/raw/wc").exists()


def test_tile_is_the_grid_tile_that_holds_the_t1_06_area(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    record = wc_json(root)
    choice = record["tile_choice"]
    assert choice["tile"] == TILE
    assert choice["tiles_in_grid"] == 3
    assert choice["tile_bounds_deg"] == {"west": -18.0, "south": 63.0, "east": -15.0, "north": 66.0}
    assert choice["product_tile_tag"] == TILE
    assert choice["product_tile_matches"] is True
    area = record["area"]
    assert area["source"] == "outputs/pilot/cop30_range_read.json"
    assert area["centre"] == CENTRE
    assert area["geographic_bounds_deg"] == AREA_BOUNDS


def test_year_is_pinned_and_the_reason_recorded(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    pin = wc_json(root)["year_pin"]
    assert pin["version"] == "v200"
    assert pin["year"] == 2021
    assert "v100" in pin["reason"] and "2020" in pin["reason"]
    assert pin["file_time_start"] == "2021-01-01T00:00:00Z"
    assert pin["file_time_end"] == "2021-12-31T23:59:59Z"
    assert "v200/2021/map/" in wc_json(root)["range_read"]["url"]


def test_window_is_at_most_the_cap_centred_inside_the_area(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    window = wc_json(root)["window"]
    pixels = window["pixel_window"]
    assert pixels["width"] <= 32 and pixels["height"] <= 32
    area = window["area_pixel_window"]
    # The area is about 43 x 18 synthetic pixels: the cap shortens the columns only.
    assert pixels["width"] == 32 and area["width"] > 32
    assert pixels["height"] == area["height"] < 32
    assert area["col_off"] <= pixels["col_off"]
    assert pixels["col_off"] + pixels["width"] <= area["col_off"] + area["width"]
    assert area["row_off"] <= pixels["row_off"]
    assert pixels["row_off"] + pixels["height"] <= area["row_off"] + area["height"]
    centre_col = int((CENTRE["longitude"] + 18.0) // PIXEL)
    assert abs(pixels["col_off"] + pixels["width"] // 2 - centre_col) <= 1
    expected = pixels["width"] * pixels["height"] / (area["width"] * area["height"])
    assert window["fraction_of_area_window"] == pytest.approx(expected)
    assert window["max_window_pixels"] == 32


def test_default_cap_is_512(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = fetch_and_record(tmp_path, monkeypatch, max_window_pixels=512)
    window = wc_json(root)["window"]
    assert window["pixel_window"]["width"] == window["area_pixel_window"]["width"]
    assert window["fraction_of_area_window"] == 1.0


def test_class_counts_match_the_window_and_names_come_from_the_legend(
    recorded: tuple[Path, Stub],
) -> None:
    root, stub = recorded
    record = wc_json(root)
    values = window_values(stub, record["window"]["pixel_window"])
    classes = record["classes"]
    codes, counts = np.unique(values[values != 0], return_counts=True)
    present = {entry["code"]: entry for entry in classes["present"]}
    assert sorted(present) == [int(code) for code in codes]
    for code, count in zip(codes, counts, strict=True):
        assert present[int(code)]["pixels"] == int(count)
    assert present[60]["name"] == "Bare/sparse vegetation"
    assert present[100]["name"] == "Moss and lichen"
    assert present[NOT_IN_LEGEND]["name"] is None
    assert present[NOT_IN_LEGEND]["in_legend"] is False
    assert classes["nodata_value"] == 0
    assert classes["nodata_pixels"] == int(np.sum(values == 0)) == 6
    assert classes["total_pixels"] == values.size
    assert classes["legend_as_stated"] == LEGEND
    assert classes["legend"][0] == {"code": 10, "name": "Tree cover"}


def test_bare_class_is_not_called_dry_bare_mineral_soil(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    record = wc_json(root)
    caveat = record["classes"]["bare_ground_caveat"]
    assert "does not establish dry bare mineral soil" in caveat
    text = json.dumps(record)
    assert text.count("dry bare mineral soil") == text.count("not establish dry bare mineral soil")


def test_range_read_records_bytes_and_sha256_of_the_fetched_ranges(
    recorded: tuple[Path, Stub],
) -> None:
    root, stub = recorded
    read = wc_json(root)["range_read"]
    local = root / read["local_path"]
    assert read["local_path"] == (
        f"data/raw/worldcover/ESA_WorldCover_10m_2021_v200_{TILE}_Map.tif.ranges"
    )
    assert read["local_sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
    assert read["bytes_received"] == local.stat().st_size
    assert read["bytes_received"] == sum(e["bytes_received"] for e in read["exchanges"])
    assert read["object_size_bytes"] == len(stub.state.cog)
    assert read["whole_file_downloaded"] is False
    assert read["bytes_received"] < len(stub.state.cog)
    assert read["http_status"] == 206


def test_header_crs_and_licence_as_the_file_states(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    record = wc_json(root)
    assert record["crs"]["epsg_identified"] == 4326
    assert record["header"]["dtypes"] == ["uint8"]
    assert record["licence"]["file_license"] == LICENCE
    assert record["licence"]["file_copyright"] == COPYRIGHT


def test_product_record_upserted_sorted_with_the_ranges_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "outputs").mkdir(parents=True)
    other = {
        "product_id": "AAA/other",
        "provider": "p",
        "version": "unknown",
        "source_url": None,
        "local_path": None,
        "checksum": None,
        "body": "earth",
        "acquisition_time": "unknown",
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "unknown",
        "calibration_type": None,
        "native_grid": "unknown",
        "effective_resolution": "unknown",
        "crs_and_frame": "unknown",
        "incidence_source": None,
        "qa_definition": None,
        "license_or_attribution": "unknown",
        "verification_status": "NOT_CHECKED",
    }
    stale = other | {"product_id": "WorldCover/stale"}
    zzz = other | {"product_id": "ZZZ/other"}
    (root / "outputs/sources.json").write_text(json.dumps([other, stale, zzz]))
    write_area(root)
    server = Stub(tmp_path, Content())
    try:
        assert run_fetch(root, server) == 0
    finally:
        server.stop()
    with monkeypatch.context() as patch:
        refuse_sockets(patch)
        assert run("record", root, server) == 0
    records = sources(root)
    ids = [record.product_id for record in records]
    assert ids == sorted(ids)
    assert "WorldCover/stale" not in ids
    (product,) = [record for record in records if record.product_id.startswith("WorldCover/")]
    assert product.product_id == f"WorldCover/esa-worldcover/{map_key(TILE)}"
    assert product.local_path == wc_json(root)["range_read"]["local_path"]
    assert product.checksum == wc_json(root)["range_read"]["local_sha256"]
    assert product.verification_status == "PARTIAL"
    assert product.body == "earth"
    assert "2021-01-01T00:00:00Z" in (product.acquisition_time or "")
    assert LICENCE in product.license_or_attribution


def test_check_passes_offline_and_fails_on_a_changed_output(
    recorded: tuple[Path, Stub], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    refuse_sockets(monkeypatch)
    assert run("check", root, stub) == 0
    path = root / "outputs/pilot/worldcover_window.json"
    path.write_bytes(path.read_bytes().replace(b'"wc"', b'"wx"', 1))
    assert run("check", root, stub) == 1
    assert "worldcover_window.json: differs" in capsys.readouterr().err


def test_record_refuses_altered_raw_bytes(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    ranges = root / wc_json(root)["range_read"]["local_path"]
    data = bytearray(ranges.read_bytes())
    data[100] ^= 0xFF
    ranges.write_bytes(bytes(data))
    assert run("record", root, stub) == 1
    assert "does not match the size and sha256 in fetch.json" in capsys.readouterr().err


def test_record_refuses_an_area_that_changed_since_fetch(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    write_area(root, centre={"latitude": 64.9, "longitude": -16.83})
    assert run("record", root, stub) == 1
    assert "differs from the one the fetch used" in capsys.readouterr().err


def test_fetch_without_the_t1_06_record_fails_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    server = Stub(tmp_path, Content())
    try:
        assert run_fetch(root, server) == 1
    finally:
        server.stop()
    assert "outputs/pilot/cop30_range_read.json" in capsys.readouterr().err
    assert not (root / "data/raw/worldcover/fetch.json").exists()


def test_area_in_no_grid_tile_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_area(root)
    server = Stub(tmp_path, Content(tiles=["S54E168"]))
    try:
        assert run_fetch(root, server) == 1
    finally:
        server.stop()
    assert "no tile of the WorldCover grid" in capsys.readouterr().err
    assert not (root / "data/raw/worldcover/fetch.json").exists()


def test_unreachable_bucket_exits_non_zero_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_area(root)
    server = Stub(tmp_path, Content())
    server.stop()
    source = server.source()
    assert pilot.main(["fetch", "wc"], root=root, sources={"wc": source}, now=Clock()) == 1
    err = capsys.readouterr().err
    assert f"{server.base}/readme.html" in err
    assert not (root / "data/raw/worldcover/fetch.json").exists()


def test_http_error_on_the_map_exits_non_zero_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_area(root)
    server = Stub(tmp_path, Content(fail={"/v200/2021/map/": 403}))
    try:
        assert run_fetch(root, server) == 1
    finally:
        server.stop()
    assert map_key(TILE) in capsys.readouterr().err


def test_record_without_fetch_names_the_fetch_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write_area(root)
    source = pilot.WorldcoverSource(endpoint="http://127.0.0.1:9")
    assert pilot.main(["check", "wc"], root=root, sources={"wc": source}) == 1
    err = capsys.readouterr().err
    assert "data/raw/worldcover/" in err and "fetch wc" in err


def test_grid_tile_must_be_an_axis_aligned_square() -> None:
    feature = {
        "type": "Feature",
        "properties": {"ll_tile": TILE},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 3], [3, 4], [0, 0]]]},
    }
    with pytest.raises(pilot.PilotError, match="rectangle"):
        pilot.worldcover_grid_tiles({"type": "FeatureCollection", "features": [feature]})


def test_source_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        pilot.WorldcoverSource().year = 2020  # type: ignore[misc]
