"""Tests for the cop30 pilot source (T1-06; PRD 5 row 11, 8.7 live-probe budget, 9 G0).

A stub server on 127.0.0.1 serves a synthetic tileList.txt (CRLF lines, as the bucket's), the
synthetic provider pages and, for any tile path, a synthetic EPSG:4326 cloud-optimized GeoTIFF on
that tile's 1-degree grid with Range support. fetch reads it through the real io.fetch_file and
io.fetch_range; nothing is recorded from a real server, and an autouse fixture refuses to resolve
any host but loopback. record and check run after the server has stopped, with sockets refused.
"""

from __future__ import annotations

import dataclasses
import hashlib
import http.server
import json
import math
import re
import socket
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pyproj
import pytest
import rasterio  # type: ignore[import-untyped]
from affine import Affine
from pydantic import TypeAdapter

from terraluna_pipeline import pilot
from terraluna_pipeline.io import GeoTiffHeader, read_geotiff_header
from terraluna_pipeline.quality import InvalidReason, valid_fraction, validity_mask
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

HOLUHRAUN_TILE = "Copernicus_DSM_COG_10_N64_00_W017_00_DEM"
FALLBACK_TILE = "Copernicus_DSM_COG_10_N65_00_W017_00_DEM"
OTHER_TILES = [
    "Copernicus_DSM_COG_10_N00_00_E006_00_DEM",
    "Copernicus_DSM_COG_10_N64_00_W016_00_DEM",
    "Copernicus_DSM_COG_10_S50_00_W075_00_DEM",
]

COLUMNS, ROWS = 180, 360
"""A synthetic 1-degree tile at 20 x 10 arc seconds, so a 5 km window spans a few 32-px tiles."""
TILE = 32

NOTICE = (
    "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under "
    "COPERNICUS by the European Union and ESA; all rights reserved"
)
LICENCE_SENTENCE = "The GLO-30 and GLO-90 datasets are available worldwide with a free license."
DSM_README = (
    "The Copernicus DEM is a Digital Surface Model (DSM) which represents the surface of the "
    "Earth including buildings, infrastructure and vegetation."
)
DSM_COLLECTION = (
    "The Copernicus DEM is a Digital Surface Model (DSM) that represents the surface of the "
    "Earth including buildings, infrastructure and vegetation."
)
VERSION_SENTENCE = (
    "Data is provided as Cloud Optimized GeoTIFFs and comes from Copernicus DEM 2021 release."
)


def readme(tile_size: int = TILE) -> str:
    return f"""<html><head><title>Copernicus Digital Elevation Model datasets</title></head>
<body><h1>Copernicus Digital Elevation Model datasets</h1>
<p>{DSM_README} We provide two instances of Copernicus DEM named GLO-30 Public and GLO-90.</p>
<p>GLO-30 Public provides limited worldwide coverage at 30 meters because a small subset of
tiles covering specific countries are not yet released to the public by the Copernicus
Programme.</p>
<p>For source data, we used the COP-DEM_GLO-30-DGED dataset for 30 meters, and the
COP-DEM_GLO-90-DGED dataset for 90 meters DEM.</p>
<h3>GLO-30 Public</h3>
<p>The properties of COG files specific to this dataset:</p>
<ul><li>original height: 3601 pixels</li><li>COG height: 3600 pixels</li>
<li>TIFF tile size: {tile_size} * {tile_size} pixels</li></ul>
<h3>GLO-90</h3>
<p>The properties of COG files specific to this dataset:</p>
<ul><li>TIFF tile size: 2048 * 2048 pixels</li></ul>
<p>The list of tiles is saved in each bucket as <a href="tileList.txt">tileList.txt</a>.</p>
<h2 id="license">License</h2>
<p>GLO-30 Public and GLO-90 are available on a free basis for the general public under the
terms and conditions of the Licence found <a
  href="https://licence.invalid/copernicus-dem/#Licencing">here</a>.</p>
</body></html>"""


def registry_json_ld(collection_url: str) -> str:
    """The JSON-LD the real registry page carries before its visible text, markup escaped."""
    licence = (
        "GLO-30 Public and GLO-90 are available on a free basis for the general public under the "
        f'terms and conditions of the Licence found on \\u003ca href=\\"{collection_url}\\"'
        "\\u003ehere\\u003c/a\\u003e."
    )
    return (
        '<script type="application/ld+json">\n{"@type" : "Dataset", '
        f'"description" : "{DSM_README} {VERSION_SENTENCE}", "license" : "{licence}"}}\n'
        "</script>"
    )


def registry(collection_url: str, head: str = "") -> str:
    return f"""<html><head>{head}{registry_json_ld(collection_url)}</head>
<body><h1>Copernicus Digital Elevation Model (DEM)</h1>
<h4>Description</h4><p>{DSM_README} {VERSION_SENTENCE}</p>
<h4>License</h4>
<p>GLO-30 Public and GLO-90 are available on a free basis for the general public under the
terms and conditions of the Licence found on <a href="{collection_url}">here</a>.</p>
<h4>Managed By</h4><p><a href="https://managed.invalid/">Synthetic Manager</a></p>
</body></html>"""


def collection(
    *, datum: bool = True, unit: bool = True, notice: bool = True, licence: bool = True
) -> str:
    parts = ["<html><body><h1>Copernicus DEM</h1>", f"<p>{DSM_COLLECTION}</p>"]
    parts.append("<p>Data were acquired by the TanDEM-X mission between 2011 and 2015.</p>")
    if datum:
        parts.append("<p>Coordinate Reference System: Vertical EGM2008 (EPSG 3855)</p>")
    if unit:
        parts.append("<p>Vertical Unit: meters</p>")
    if licence:
        parts.append(f"<h3>Licensing and citation guidelines</h3><p>{LICENCE_SENTENCE}</p>")
    if notice:
        parts.append(
            "<p>When communicating to the General Public or distributing the Copernicus "
            "WorldDEM-30 (GLO-30), the User shall inform the General Public of the source by "
            f"using the following notice: {NOTICE}</p>"
        )
    parts.append("</body></html>")
    return "\n".join(parts)


def tile_list(tiles: list[str]) -> bytes:
    return "".join(f"{tile}\r\n" for tile in tiles).encode("ascii")


def tile_origin(tile: str) -> tuple[int, int]:
    match = re.fullmatch(r"Copernicus_DSM_COG_10_([NS])([0-9]{2})_00_([EW])([0-9]{3})_00_DEM", tile)
    assert match is not None, tile
    lat = int(match.group(2)) * (1 if match.group(1) == "N" else -1)
    lon = int(match.group(4)) * (1 if match.group(3) == "E" else -1)
    return lat, lon


def tile_transform(tile: str) -> Affine:
    """The tile's geotransform as GDAL reports the real ones: pixel corners, half a pixel out."""
    lat, lon = tile_origin(tile)
    dx, dy = 1 / COLUMNS, 1 / ROWS
    return Affine(dx, 0.0, lon - dx / 2, 0.0, -dy, lat + 1 + dy / 2)


HOLE = (slice(50, 54), slice(25, 33))
"""A NaN block in the rows and columns the Holuhraun window reads."""


def write_tile(
    path: Path, tile: str, *, sparse_block: tuple[int, int] | None = None
) -> npt.NDArray[np.float32]:
    """The synthetic tile; with ``sparse_block`` (row, column) that internal tile is all zeros
    and written with SPARSE_OK, so the file does not store it and its tile index states no
    offset or size for it."""
    rng = np.random.default_rng(sum(tile.encode()))
    rows = np.arange(ROWS, dtype=np.float64)[:, None]
    cols = np.arange(COLUMNS, dtype=np.float64)[None, :]
    array = (700.0 + 3.0 * rows - 2.0 * cols + rng.normal(0.0, 4.0, (ROWS, COLUMNS))).astype(
        np.float32
    )
    array[HOLE] = np.nan
    options: dict[str, str] = {}
    if sparse_block is not None:
        row, col = sparse_block
        array[row * TILE : (row + 1) * TILE, col * TILE : (col + 1) * TILE] = 0.0
        options["SPARSE_OK"] = "TRUE"
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=COLUMNS,
        height=ROWS,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=tile_transform(tile),
        blocksize=TILE,
        compress="deflate",
        predictor=3,
        overview_resampling="average",
        **options,
    ) as dst:
        dst.write(array, 1)
    return array


@dataclass
class Content:
    """What the stub server serves; tests change it before the fetch."""

    tiles: list[str] = field(default_factory=lambda: [*OTHER_TILES, HOLUHRAUN_TILE, FALLBACK_TILE])
    readme: str = field(default_factory=readme)
    collection: str = field(default_factory=collection)
    registry_head: str = ""
    """Extra markup for the registry page's head, which is not visible text."""
    fail: dict[str, int] = field(default_factory=dict)
    """Path prefix -> HTTP status to answer instead."""


@dataclass
class ServerState:
    content: Content
    cogs: dict[str, bytes]
    base: str = ""
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
        content = state.content
        state.requests.append((self.path, self.headers.get("Range")))
        for prefix, status in content.fail.items():
            if self.path.startswith(prefix):
                self._send(status, b"failure", "text/plain")
                return
        path = urllib.parse.urlsplit(self.path).path
        tile_match = re.fullmatch(r"/(Copernicus_DSM_COG_10_[^/]+_DEM)/\1\.tif", path)
        if path == "/tileList.txt":
            self._send(200, tile_list(content.tiles), "text/plain")
        elif path == "/readme.html":
            self._send(200, content.readme.encode(), "text/html")
        elif path == "/registry/copernicus-dem/":
            page = registry(f"{state.base}/cdse/COP-DEM", content.registry_head)
            self._send(200, page.encode(), "text/html")
        elif path == "/cdse/COP-DEM":
            self._send(200, content.collection.encode(), "text/html; charset=UTF-8")
        elif tile_match is not None and tile_match.group(1) in state.cogs:
            self._range(state.cogs[tile_match.group(1)])
        else:
            self._send(404, b"not found", "text/plain")

    def _range(self, data: bytes) -> None:
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", self.headers.get("Range", ""))
        assert match is not None, "a tile must only be read by range"
        first, last = int(match.group(1)), min(int(match.group(2)), len(data) - 1)
        self._send(
            206,
            data[first : last + 1],
            "image/tiff",
            Content_Range=f"bytes {first}-{last}/{len(data)}",
            ETag='"tile-v1"',
            Last_Modified="Mon, 09 May 2022 14:41:24 GMT",
        )


class Stub:
    def __init__(self, tmp: Path, content: Content) -> None:
        self.arrays: dict[str, npt.NDArray[np.float32]] = {}
        cogs: dict[str, bytes] = {}
        for tile in (HOLUHRAUN_TILE, FALLBACK_TILE):
            path = tmp / f"served_{tile}.tif"
            if not path.exists():
                write_tile(path, tile)
            with rasterio.open(path) as src:
                self.arrays[tile] = src.read(1)
            cogs[tile] = path.read_bytes()
        self.state = ServerState(content=content, cogs=cogs)
        handler = type("Handler", (StubHandler,), {"state": self.state})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.state.base = self.base

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def source(self, **fields: Any) -> pilot.Cop30Source:
        return pilot.Cop30Source(
            endpoint=self.base,
            registry_url=f"{self.base}/registry/copernicus-dem/",
            collection_url=f"{self.base}/cdse/COP-DEM",
            **{"range_block_bytes": 4096, **fields},
        )

    def tile_url(self, tile: str) -> str:
        return f"{self.base}/{tile}/{tile}.tif"


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 15, 1, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def run_fetch(root: Path, stub: Stub, **fields: Any) -> int:
    return pilot.main(
        ["fetch", "cop30"], root=root, sources={"cop30": stub.source(**fields)}, now=Clock()
    )


def run(command: str, root: Path, stub: Stub, **fields: Any) -> int:
    return pilot.main([command, "cop30"], root=root, sources={"cop30": stub.source(**fields)})


@pytest.fixture
def stub(tmp_path: Path) -> Iterator[Stub]:
    server = Stub(tmp_path, Content())
    try:
        yield server
    finally:
        server.stop()


def fetch_and_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: Content | None = None,
    **fields: Any,
) -> tuple[Path, Stub]:
    """Fetch from a fresh stub, stop it, then record with sockets refused."""
    root = tmp_path / "repo"
    root.mkdir()
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
    result: dict[str, Any] = json.loads((root / "data/raw/cop30/fetch.json").read_bytes())
    return result


def cop30_json(root: Path) -> dict[str, Any]:
    path = root / "outputs/pilot/cop30_range_read.json"
    result: dict[str, Any] = json.loads(path.read_bytes())
    return result


def sources(root: Path) -> list[ProductRecord]:
    return TypeAdapter(SourcesFile).validate_json((root / "outputs/sources.json").read_bytes())


def window_slice(window: dict[str, int]) -> tuple[slice, slice]:
    return (
        slice(window["row_off"], window["row_off"] + window["height"]),
        slice(window["col_off"], window["col_off"] + window["width"]),
    )


def header_for(transform: Affine, width: int = 1800, height: int = 3600) -> GeoTiffHeader:
    return GeoTiffHeader(
        width=width,
        height=height,
        count=1,
        dtypes=("float32",),
        block_shapes=((1024, 1024),),
        overviews=(2, 4, 8),
        image_structure={"LAYOUT": "COG"},
        tags={"AREA_OR_POINT": "Point"},
        crs=pyproj.CRS.from_epsg(4326).to_wkt(),
        transform=transform,
        nodata=(None,),
        units=(None,),
        scales=(1.0,),
        offsets=(0.0,),
        descriptions=(None,),
    )


REAL_TILE_TRANSFORM = Affine(1 / 1800, 0.0, -17.0 - 1 / 3600, 0.0, -1 / 3600, 65.0 + 1 / 7200)
"""The geotransform rasterio reports for the real N64 W017 tile (1800 x 3600, 2 x 1 arcsec)."""


# --- Tile naming, the window and the registered source ------------------------------------------


@pytest.mark.parametrize(
    ("latitude", "longitude", "tile"),
    [
        (64.85, -16.83, HOLUHRAUN_TILE),
        (65.5, -16.5, FALLBACK_TILE),
        (0.5, 6.5, "Copernicus_DSM_COG_10_N00_00_E006_00_DEM"),
        (-0.5, 0.5, "Copernicus_DSM_COG_10_S01_00_E000_00_DEM"),
        (0.5, -0.5, "Copernicus_DSM_COG_10_N00_00_W001_00_DEM"),
        (-49.5, -74.5, "Copernicus_DSM_COG_10_S50_00_W075_00_DEM"),
        (89.5, 179.5, "Copernicus_DSM_COG_10_N89_00_E179_00_DEM"),
    ],
)
def test_the_tile_is_the_one_degree_cell_holding_the_centre(
    latitude: float, longitude: float, tile: str
) -> None:
    assert pilot.cop30_tile_name(latitude, longitude) == tile


@pytest.mark.parametrize(("latitude", "longitude"), [(90.0, 0.0), (-91.0, 0.0), (0.0, 180.0)])
def test_a_centre_outside_the_tile_grid_is_refused(latitude: float, longitude: float) -> None:
    with pytest.raises(pilot.PilotError):
        pilot.cop30_tile_name(latitude, longitude)


def test_the_registered_source_reads_the_public_bucket_without_credentials() -> None:
    source = pilot.SOURCES["cop30"]
    assert isinstance(source, pilot.Cop30Source)
    assert source.bucket == "copernicus-dem-30m"
    assert source.region == "eu-central-1"
    assert source.base_url == "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"
    assert source.tile_url(HOLUHRAUN_TILE) == (
        "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/"
        f"{HOLUHRAUN_TILE}/{HOLUHRAUN_TILE}.tif"
    )
    assert source.tile_list_url == (
        "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/tileList.txt"
    )
    assert source.registry_url == "https://registry.opendata.aws/copernicus-dem/"
    assert source.collection_url == (
        "https://dataspace.copernicus.eu/explore-data/data-collections/"
        "copernicus-contributing-missions/collections-description/COP-DEM"
    )
    first, fallback = source.candidates
    assert (first.key, first.latitude, first.longitude) == ("holuhraun", 64.85, -16.83)
    assert pilot.cop30_tile_name(first.latitude, first.longitude) == HOLUHRAUN_TILE
    assert "Holuhraun" in first.basis
    assert pilot.cop30_tile_name(fallback.latitude, fallback.longitude) == FALLBACK_TILE
    assert source.window_side_m == 5000.0
    assert source.budget_bytes == pilot.COP30_BUDGET_BYTES == 4_000_000


def test_the_probe_window_is_the_smallest_pixel_window_holding_the_utm_square() -> None:
    header = header_for(REAL_TILE_TRANSFORM)
    probe = pilot.cop30_probe_window(header, 64.85, -16.83, 5000.0)
    assert probe.utm_epsg == 32628
    x, y = probe.centre_utm
    west, south, east, north = probe.utm_bounds
    assert east - west == pytest.approx(5000.0, abs=1e-6)
    assert north - south == pytest.approx(5000.0, abs=1e-6)
    assert (west + east) / 2 == pytest.approx(x) and (south + north) / 2 == pytest.approx(y)
    to_geo = pyproj.Transformer.from_crs(32628, 4326, always_xy=True)
    lon, lat = to_geo.transform(x, y)
    assert (lat, lon) == (pytest.approx(64.85, abs=1e-9), pytest.approx(-16.83, abs=1e-9))
    t = REAL_TILE_TRANSFORM
    left, top = t @ (probe.col_off, probe.row_off)
    right, bottom = t @ (probe.col_off + probe.width, probe.row_off + probe.height)
    # Every point of the square's edges lies inside the pixel window.
    steps = np.linspace(0.0, 1.0, 201)
    edge_x = np.concatenate([west + steps * 5000, np.full(201, east), east - steps * 5000])
    edge_y = np.concatenate([np.full(201, south), south + steps * 5000, np.full(201, north)])
    edge_x = np.concatenate([edge_x, np.full(201, west)])
    edge_y = np.concatenate([edge_y, north - steps * 5000])
    lons, lats = to_geo.transform(edge_x, edge_y)
    assert left <= float(np.min(lons)) and float(np.max(lons)) <= right
    assert bottom <= float(np.min(lats)) and float(np.max(lats)) <= top
    # Removing a column or row on any side would cut the square's bounding box.
    gw, gs, ge, gn = probe.geographic_bounds
    assert left + t.a > gw and right - t.a < ge
    assert top + t.e < gn and bottom - t.e > gs
    # About 5 km at 2 x 1 arc seconds near 64.85 N: roughly 190 columns and 162 rows.
    assert 180 <= probe.width <= 215 and 155 <= probe.height <= 190


def test_a_probe_window_that_leaves_the_tile_is_refused() -> None:
    header = header_for(REAL_TILE_TRANSFORM)
    with pytest.raises(pilot.PilotError, match="leaves"):
        pilot.cop30_probe_window(header, 64.999, -16.83, 5000.0)


def test_a_tile_whose_crs_is_not_epsg_4326_is_refused() -> None:
    header = header_for(REAL_TILE_TRANSFORM)
    utm = dataclasses.replace(header, crs=pyproj.CRS.from_epsg(32628).to_wkt())
    with pytest.raises(pilot.PilotError, match="EPSG:4326"):
        pilot.cop30_probe_window(utm, 64.85, -16.83, 5000.0)
    without = dataclasses.replace(header, crs=None)
    with pytest.raises(pilot.PilotError, match="CRS"):
        pilot.cop30_probe_window(without, 64.85, -16.83, 5000.0)


# --- fetch --------------------------------------------------------------------------------------


def test_fetch_saves_the_tile_list_documents_and_the_window_ranges(
    tmp_path: Path, stub: Stub
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 0
    raw = root / "data/raw/cop30"
    fetched = fetch_json(root)
    roles = [entry["role"] for entry in fetched["documents"]]
    assert roles == ["tile_list", "bucket_readme", "registry_entry", "collection_description"]
    for entry in fetched["documents"]:
        local = raw / entry["file_name"]
        assert hashlib.sha256(local.read_bytes()).hexdigest() == entry["sha256"]
        assert entry["http_status"] == 200
        assert entry["retrieved_at"].endswith("Z")
    assert (raw / "tileList.txt").read_bytes() == tile_list(Content().tiles)
    assert fetched["choice"]["chosen"] == "holuhraun"
    (window,) = fetched["files"]
    assert window["role"] == "dem_window"
    assert window["read"] == "window"
    assert window["tile"] == HOLUHRAUN_TILE
    assert window["url"] == stub.tile_url(HOLUHRAUN_TILE)
    assert window["provider_file_name"] == f"{HOLUHRAUN_TILE}.tif"
    assert window["file_name"] == f"{HOLUHRAUN_TILE}.tif.ranges"
    size = len(stub.state.cogs[HOLUHRAUN_TILE])
    assert window["remote_bytes"] == size
    assert window["bytes_received"] < size
    held = (raw / window["file_name"]).read_bytes()
    assert len(held) == window["bytes_received"]
    assert hashlib.sha256(held).hexdigest() == window["sha256"]
    exchanges = window["exchanges"]
    assert len(exchanges) == window["range_requests"] >= 2
    assert sum(e["bytes_received"] for e in exchanges) == window["bytes_received"]
    assert {e["phase"] for e in exchanges} == {"header", "window"}
    for exchange in exchanges:
        assert exchange["http_status"] == 206
        assert re.fullmatch(r"bytes=[0-9]+-[0-9]+", exchange["requested_range"])
        assert re.fullmatch(rf"bytes [0-9]+-[0-9]+/{size}", exchange["content_range"])
        assert exchange["retrieved_at"].endswith("Z")
    tile_requests = [(p, r) for p, r in stub.state.requests if p.endswith(".tif")]
    assert tile_requests and all(r is not None for _, r in tile_requests)
    assert all(HOLUHRAUN_TILE in p for p, _ in tile_requests)
    assert not any(p.name.startswith(".fetch-") for p in raw.iterdir())


@pytest.mark.parametrize("prefix", ["/tileList.txt", "/readme.html", "/cdse/COP-DEM"])
def test_an_unreachable_bucket_or_document_fails_naming_the_url(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str], prefix: str
) -> None:
    stub.state.content.fail = {prefix: 503}
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 1
    error = capsys.readouterr().err
    assert f"{stub.base}{prefix}" in error
    assert "HTTP 503" in error
    assert not (root / "data/raw/cop30/fetch.json").exists()


def test_an_unreachable_tile_fails_naming_the_url(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    stub.state.content.fail = {f"/{HOLUHRAUN_TILE}/": 403}
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 1
    error = capsys.readouterr().err
    assert stub.tile_url(HOLUHRAUN_TILE) in error
    assert "HTTP 403" in error
    assert not (root / "data/raw/cop30/fetch.json").exists()


def test_a_bucket_that_refuses_connections_fails_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Stub(tmp_path, Content())
    source = server.source()
    server.stop()
    root = tmp_path / "repo"
    root.mkdir()
    assert pilot.main(["fetch", "cop30"], root=root, sources={"cop30": source}, now=Clock()) == 1
    error = capsys.readouterr().err
    assert f"{server.base}/tileList.txt" in error
    assert "could not connect" in error


def test_a_failed_fetch_keeps_an_earlier_one(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 0
    before = (root / "data/raw/cop30/fetch.json").read_bytes()
    stub.state.content.fail = {"/cdse/": 500}
    assert run_fetch(root, stub) == 1
    assert (root / "data/raw/cop30/fetch.json").read_bytes() == before
    capsys.readouterr()


# --- record -------------------------------------------------------------------------------------


def test_record_states_the_range_read_as_observed(recorded: tuple[Path, Stub]) -> None:
    root, stub = recorded
    record = cop30_json(root)
    fetched = fetch_json(root)
    (entry,) = fetched["files"]
    assert record["record"] == "cop30_range_read"
    dem = record["dem"]
    read = dem["range_read"]
    size = len(stub.state.cogs[HOLUHRAUN_TILE])
    assert read["url"] == stub.tile_url(HOLUHRAUN_TILE)
    assert read["s3_uri"] == f"s3://copernicus-dem-30m/{HOLUHRAUN_TILE}/{HOLUHRAUN_TILE}.tif"
    assert read["http_status"] == 206
    assert read["content_range"] == entry["exchanges"][0]["content_range"]
    assert read["content_range"].endswith(f"/{size}")
    assert read["object_size_bytes"] == size
    assert read["bytes_received"] == entry["bytes_received"] < size
    requested = 0
    for exchange in entry["exchanges"]:
        first, last = map(int, exchange["requested_range"][6:].split("-"))
        requested += last - first + 1
    assert read["bytes_requested"] == requested
    assert read["range_requests"] == len(entry["exchanges"])
    assert read["ranges"] == entry["ranges"]
    assert read["exchanges"] == entry["exchanges"]
    assert read["range_block_bytes"] == 4096
    assert read["whole_file_downloaded"] is False
    phases = read["bytes_by_phase"]
    assert (
        phases["header"]["bytes_received"] + phases["window"]["bytes_received"]
        == (read["bytes_received"])
    )
    assert phases["header"]["requests"] + phases["window"]["requests"] == read["range_requests"]
    assert read["local_path"] == f"data/raw/cop30/{HOLUHRAUN_TILE}.tif.ranges"
    assert read["local_sha256"] == entry["sha256"]


def test_record_states_the_window_footprint_and_values(recorded: tuple[Path, Stub]) -> None:
    root, stub = recorded
    dem = cop30_json(root)["dem"]
    window = dem["window"]
    assert window["side_m"] == 5000.0
    assert window["centre"] == {"latitude": 64.85, "longitude": -16.83}
    assert "Holuhraun" in window["centre_basis"]
    assert window["utm_crs"] == "EPSG:32628"
    bounds = window["utm_bounds_m"]
    assert bounds["east"] - bounds["west"] == pytest.approx(5000.0, abs=1e-6)
    assert bounds["north"] - bounds["south"] == pytest.approx(5000.0, abs=1e-6)
    pixels = window["pixel_window"]
    assert pixels == fetch_json(root)["files"][0]["window"]
    values = stub.arrays[HOLUHRAUN_TILE][window_slice(pixels)]
    mask = validity_mask(values)
    fraction = valid_fraction(mask)
    assert fraction.invalid_pixels > 0, "the synthetic hole must lie in the window"
    stats = dem["values"]
    assert stats["valid_pixels"] == fraction.valid_pixels
    assert stats["total_pixels"] == fraction.total_pixels == values.size
    assert stats["valid_fraction"] == fraction.fraction
    assert stats["invalid_by_reason"] == {
        reason.label: count
        for reason, count in mask.reason_counts().items()
        if reason != InvalidReason.VALID
    }
    assert stats["invalid_by_reason"]["non_finite"] == fraction.invalid_pixels
    assert stats["elevation_min"] == float(np.min(values[mask.valid]))
    assert stats["elevation_max"] == float(np.max(values[mask.valid]))
    rule = stats["live_probe_quality_rule"]
    assert rule["threshold"] == 0.8
    assert rule["met"] is (fraction.fraction >= 0.8)


def test_record_states_the_cog_structure_crs_datum_nodata_and_units(
    recorded: tuple[Path, Stub],
) -> None:
    root, _ = recorded
    dem = cop30_json(root)["dem"]
    cog = dem["cog"]
    assert cog["internal_tile_shape"] == {"rows": TILE, "columns": TILE}
    assert cog["tile_grid"] == {"across": math.ceil(COLUMNS / TILE), "down": math.ceil(ROWS / TILE)}
    with rasterio.open(Path(root).parent / f"served_{HOLUHRAUN_TILE}.tif") as src:
        factors = src.overviews(1)
    assert [o["factor"] for o in cog["overviews"]] == factors
    assert [o["level"] for o in cog["overviews"]] == list(range(len(factors)))
    pixels = dem["window"]["pixel_window"]
    rows = range(pixels["row_off"] // TILE, (pixels["row_off"] + pixels["height"] - 1) // TILE + 1)
    cols = range(pixels["col_off"] // TILE, (pixels["col_off"] + pixels["width"] - 1) // TILE + 1)
    touched = cog["tiles_touched"]
    assert touched["count"] == len(rows) * len(cols) >= 2
    assert [(t["row"], t["col"]) for t in touched["tiles"]] == [(r, c) for r in rows for c in cols]
    assert all(t["held_in_ranges"] is True for t in touched["tiles"])
    assert touched["stored_bytes"] == sum(t["size"] for t in touched["tiles"])
    assert cog["readme_tile_size"]["matches_file"] is True
    crs = dem["crs"]
    assert crs["epsg_identified"] == 4326
    assert crs["wkt"] == read_geotiff_header(Path(root).parent / f"served_{HOLUHRAUN_TILE}.tif").crs
    assert dem["pixel_size"] == {"x": 1 / COLUMNS, "y": 1 / ROWS, "unit": "degree"}
    vertical = dem["vertical_datum"]
    assert vertical["stated_by_file"] == "unknown"
    assert vertical["product_documentation"]["quote"] == "Vertical EGM2008 (EPSG 3855)"
    assert vertical["product_documentation"]["datum"] == "EGM2008"
    assert vertical["product_documentation"]["epsg"] == 3855
    assert dem["nodata"]["stated_by_file"] is None
    units = dem["units"]
    assert units["stated_by_file"] is None
    assert units["product_documentation"]["quote"] == "Vertical Unit: meters"


def test_a_touched_tile_the_file_does_not_store_has_unknown_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Block (1, 0) holds rows 32-63 and columns 0-31, which the Holuhraun window reads (HOLE).
    write_tile(tmp_path / f"served_{HOLUHRAUN_TILE}.tif", HOLUHRAUN_TILE, sparse_block=(1, 0))
    root, _ = fetch_and_record(tmp_path, monkeypatch)
    dem = cop30_json(root)["dem"]
    touched = dem["cog"]["tiles_touched"]
    by_position = {(t["row"], t["col"]): t for t in touched["tiles"]}
    unstored = by_position.pop((1, 0))
    assert (unstored["offset"], unstored["size"]) == (None, None)
    assert unstored["held_in_ranges"] is None
    assert by_position
    assert all(t["size"] > 0 and t["held_in_ranges"] is True for t in by_position.values())
    assert touched["stored_bytes"] is None
    budget = dem["live_probe_budget"]
    assert budget["touched_tile_bytes"] is None
    assert budget["tiles_touched"] == touched["count"] == len(by_position) + 1
    assert budget["bytes_fetched"] == dem["range_read"]["bytes_received"] > 0


def test_provider_statements_are_read_from_visible_text_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = (
        "<style>p::after { content: 'comes from Copernicus DEM 2019 release.' }</style>"
        "<script>var s = 'Data is provided as Cloud Optimized GeoTIFFs and comes from "
        "Copernicus DEM 2019 release.';</script>"
        "<!-- Data is provided as Cloud Optimized GeoTIFFs and comes from Copernicus DEM 2019 "
        "release. -->"
    )
    page = collection().replace(
        "<body>",
        "<body><SCRIPT type='application/json'>Vertical EGM96 (EPSG 5773) Vertical Unit: feet"
        "</SCRIPT>",
        1,
    )
    content = Content(registry_head=decoy, collection=page)
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    record = cop30_json(root)
    registry_licence = record["licence"]["registry_licence"]["quote"]
    assert registry_licence == (
        "GLO-30 Public and GLO-90 are available on a free basis for the general public under the "
        "terms and conditions of the Licence found on here ."
    )
    assert record["version"]["value"].startswith("Copernicus DEM 2021 release")
    vertical = record["dem"]["vertical_datum"]["product_documentation"]
    assert (vertical["datum"], vertical["epsg"]) == ("EGM2008", 3855)
    assert record["dem"]["units"]["product_documentation"]["quote"] == "Vertical Unit: meters"
    (product,) = [r for r in sources(root) if r.product_id.startswith("COP30/")]
    assert "2021 release" in product.version and "2019" not in product.version


def test_the_registry_licence_quote_is_the_visible_sentence(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    quote = cop30_json(root)["licence"]["registry_licence"]["quote"]
    assert quote.endswith("Licence found on here .")
    assert "\\u003c" not in quote and "href" not in quote and "<" not in quote


def test_record_states_the_budget_and_the_surface_model_caveat(
    recorded: tuple[Path, Stub],
) -> None:
    root, _ = recorded
    record = cop30_json(root)
    budget = record["dem"]["live_probe_budget"]
    read = record["dem"]["range_read"]
    assert budget["limit_bytes"] == 4_000_000
    assert budget["bytes_fetched"] == read["bytes_received"]
    assert budget["within_budget"] is True
    assert budget["tiles_touched"] == record["dem"]["cog"]["tiles_touched"]["count"]
    caveat = record["surface_model_caveat"]
    assert "vegetation" in caveat["caveat"] and "buildings" in caveat["caveat"]
    assert DSM_README in [quote["quote"] for quote in caveat["quotes"]]
    assert DSM_COLLECTION in [quote["quote"] for quote in caveat["quotes"]]


def test_an_over_budget_read_is_recorded_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stub = fetch_and_record(tmp_path, monkeypatch, budget_bytes=1000)
    budget = cop30_json(root)["dem"]["live_probe_budget"]
    assert budget["limit_bytes"] == 1000
    assert budget["within_budget"] is False
    assert budget["bytes_fetched"] > 1000
    assert run("check", root, stub, budget_bytes=1000) == 0


def test_the_product_record_is_upserted_with_the_provider_statements(
    recorded: tuple[Path, Stub],
) -> None:
    root, stub = recorded
    (record,) = [r for r in sources(root) if r.product_id.startswith("COP30/")]
    (entry,) = fetch_json(root)["files"]
    assert record.product_id == f"COP30/copernicus-dem-30m/{HOLUHRAUN_TILE}/{HOLUHRAUN_TILE}.tif"
    assert record.body == "earth"
    assert record.source_url == stub.tile_url(HOLUHRAUN_TILE)
    assert record.local_path == f"data/raw/cop30/{HOLUHRAUN_TILE}.tif.ranges"
    held = (root / record.local_path).read_bytes()
    assert record.checksum == hashlib.sha256(held).hexdigest() == entry["sha256"]
    assert NOTICE in record.license_or_attribution
    assert LICENCE_SENTENCE in record.license_or_attribution
    assert "2021 release" in record.version
    assert "meters" in record.units
    assert record.frequency_or_wavelength is None
    assert record.verification_status == "PARTIAL"
    assert (
        record.crs_and_frame
        == read_geotiff_header(Path(root).parent / f"served_{HOLUHRAUN_TILE}.tif").crs
    )


def test_the_upsert_keeps_other_records_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "outputs").mkdir(parents=True)
    other = {
        "product_id": "ZZZ/other",
        "provider": "unknown",
        "version": "unknown",
        "source_url": None,
        "local_path": None,
        "checksum": None,
        "body": "moon",
        "acquisition_time": "unknown",
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "unknown",
        "calibration_type": None,
        "native_grid": None,
        "effective_resolution": None,
        "crs_and_frame": "unknown",
        "incidence_source": None,
        "qa_definition": None,
        "license_or_attribution": "unknown",
        "verification_status": "NOT_CHECKED",
    }
    (root / "outputs/sources.json").write_text(json.dumps([other]))
    server = Stub(tmp_path, Content())
    try:
        assert run_fetch(root, server) == 0
    finally:
        server.stop()
    with monkeypatch.context() as patch:
        refuse_sockets(patch)
        assert run("record", root, server) == 0
    ids = [r.product_id for r in sources(root)]
    assert ids == sorted(ids)
    assert ids[-1] == "ZZZ/other"
    assert sum(1 for i in ids if i.startswith("COP30/")) == 1


def test_missing_provider_statements_are_recorded_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(collection=collection(datum=False, unit=False, notice=False, licence=False))
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    dem = cop30_json(root)["dem"]
    assert dem["vertical_datum"]["stated_by_file"] == "unknown"
    assert dem["vertical_datum"]["product_documentation"] is None
    assert dem["units"]["product_documentation"] is None
    (record,) = [r for r in sources(root) if r.product_id.startswith("COP30/")]
    assert record.units == "unknown"
    assert record.license_or_attribution == "unknown"


def test_a_readme_tile_size_that_differs_from_the_file_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(readme=readme(tile_size=1024))
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    stated = cop30_json(root)["dem"]["cog"]["readme_tile_size"]
    assert (stated["rows"], stated["columns"]) == (1024, 1024)
    assert stated["matches_file"] is False


def test_record_is_reproducible_and_writes_no_mode(recorded: tuple[Path, Stub]) -> None:
    root, stub = recorded
    before = {
        path: (root / path).read_bytes()
        for path in ("outputs/pilot/cop30_range_read.json", "outputs/sources.json")
    }
    assert run("record", root, stub) == 0
    for path, data in before.items():
        assert (root / path).read_bytes() == data

    def keys(value: Any) -> Iterator[str]:
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    found = set(keys(cop30_json(root)))
    assert not found & {"cop30_mode", "shipping_mode", "g0_status"}
    assert not (root / "config").exists()
    assert not (root / "outputs/validation.json").exists()


# --- the fallback tile --------------------------------------------------------------------------


def test_an_absent_holuhraun_tile_is_recorded_and_the_fallback_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(tiles=[*OTHER_TILES, FALLBACK_TILE])
    root, stub = fetch_and_record(tmp_path, monkeypatch, content)
    choice = cop30_json(root)["tile_choice"]
    first, fallback = choice["candidates"]
    assert (first["key"], first["listed_in_tile_list"], first["status"]) == (
        "holuhraun",
        False,
        "absent",
    )
    assert first["tile"] == HOLUHRAUN_TILE
    assert (fallback["tile"], fallback["status"]) == (FALLBACK_TILE, "chosen")
    assert choice["chosen"] == fallback["key"]
    assert choice["fallback_used"] is True
    assert HOLUHRAUN_TILE in choice["reason"]
    dem = cop30_json(root)["dem"]
    assert dem["range_read"]["url"] == stub.tile_url(FALLBACK_TILE)
    assert dem["window"]["centre"] == {"latitude": 65.5, "longitude": -16.5}
    assert not any(HOLUHRAUN_TILE in path for path, _ in stub.state.requests)


def test_no_listed_candidate_is_recorded_without_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(tiles=list(OTHER_TILES))
    root, stub = fetch_and_record(tmp_path, monkeypatch, content)
    record = cop30_json(root)
    assert record["tile_choice"]["chosen"] is None
    assert all(c["status"] == "absent" for c in record["tile_choice"]["candidates"])
    assert record["dem"] is None
    assert record["tile_choice"]["reason"]
    assert not [r for r in sources(root) if r.product_id.startswith("COP30/")]
    assert not any(path.endswith(".tif") for path, _ in stub.state.requests)


def test_the_tile_list_count_is_read_from_the_saved_file(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    listed = cop30_json(root)["tile_list"]
    assert listed["tiles_listed"] == len(Content().tiles)
    assert listed["lines_not_tile_names"] == 0
    assert listed["local_path"] == "data/raw/cop30/tileList.txt"


# --- check --------------------------------------------------------------------------------------


def test_check_is_offline_and_passes_on_unchanged_inputs(
    recorded: tuple[Path, Stub], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stub = recorded
    refuse_sockets(monkeypatch)
    assert run("check", root, stub) == 0


def test_check_fails_when_a_committed_output_differs(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    path = root / "outputs/pilot/cop30_range_read.json"
    path.write_bytes(path.read_bytes().replace(b'"within_budget": true', b'"within_budget": false'))
    assert run("check", root, stub) == 1
    assert "cop30_range_read.json" in capsys.readouterr().err


def test_check_without_raw_data_names_the_fetch_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = pilot.Cop30Source(endpoint="http://127.0.0.1:9")
    assert pilot.main(["check", "cop30"], root=root, sources={"cop30": source}) == 1
    assert "run: python -m terraluna_pipeline.pilot fetch cop30 first" in capsys.readouterr().err


def test_record_refuses_altered_ranges(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    (entry,) = fetch_json(root)["files"]
    path = root / "data/raw/cop30" / entry["file_name"]
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    assert run("record", root, stub) == 1
    assert "fetch cop30" in capsys.readouterr().err


def test_record_refuses_a_window_that_is_not_the_probe_window(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    path = root / "data/raw/cop30/fetch.json"
    fetched = json.loads(path.read_bytes())
    fetched["files"][0]["window"]["col_off"] += 1
    path.write_bytes(json.dumps(fetched).encode())
    assert run("record", root, stub) == 1
    assert "window" in capsys.readouterr().err


def test_record_refuses_a_choice_the_tile_list_does_not_support(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    listed = root / "data/raw/cop30/tileList.txt"
    listed.write_bytes(tile_list([*OTHER_TILES, FALLBACK_TILE]))
    path = root / "data/raw/cop30/fetch.json"
    fetched = json.loads(path.read_bytes())
    for entry in fetched["documents"]:
        if entry["role"] == "tile_list":
            entry["sha256"] = hashlib.sha256(listed.read_bytes()).hexdigest()
            entry["bytes_received"] = listed.stat().st_size
    path.write_bytes(json.dumps(fetched).encode())
    assert run("record", root, stub) == 1
    assert "tileList.txt" in capsys.readouterr().err


def test_record_refuses_ranges_that_hold_the_whole_tile(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    whole = stub.state.cogs[HOLUHRAUN_TILE]
    path = root / "data/raw/cop30/fetch.json"
    fetched = json.loads(path.read_bytes())
    entry = fetched["files"][0]
    (root / "data/raw/cop30" / entry["file_name"]).write_bytes(whole)
    entry["ranges"] = [[0, len(whole) - 1]]
    entry["bytes_received"] = len(whole)
    entry["sha256"] = hashlib.sha256(whole).hexdigest()
    path.write_bytes(json.dumps(fetched).encode())
    assert run("record", root, stub) == 1
    assert "whole" in capsys.readouterr().err
