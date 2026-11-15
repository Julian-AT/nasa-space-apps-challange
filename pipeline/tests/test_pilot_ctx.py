"""Tests for the ctx pilot source (T1-05; PRD 5 Mars inputs, 9 G0, 15 CTX QA threshold).

A stub server on 127.0.0.1 serves synthetic STAC JSON (the collection and paged item searches),
synthetic provider pages, a synthetic qa_metrics.txt and a synthetic cloud-optimized GeoTIFF with
Range support. fetch reads it through the real io.fetch_file and io.fetch_range; nothing is
recorded from a real server, and an autouse fixture refuses to resolve any host but loopback.
record and check run after the server has stopped, with sockets refused.
"""

from __future__ import annotations

import hashlib
import http.server
import json
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
import pytest
import rasterio  # type: ignore[import-untyped]
from pydantic import TypeAdapter
from rasterio.transform import from_origin  # type: ignore[import-untyped]

from terraluna_pipeline import pilot
from terraluna_pipeline.quality import valid_fraction, validity_mask
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

COLLECTION = "mro_ctx_controlled_usgs_dtms"
JEZERO_BOX = "76.69,17.41,78.69,19.41"
ARCADIA_BOX = "165.86,33.87,-149.57,64.17"
LIMIT = 2

JEZERO_IDS = ["P02_SYNTH_JEZ", "N01_SYNTH_JEZ", "B17_SYNTH_JEZ_FIRST", "G03_SYNTH_JEZ"]
"""Four items over two full pages; the lexicographically first is on page 2."""
FIRST_JEZERO = "B17_SYNTH_JEZ_FIRST"
ARCADIA_IDS = ["F05_SYNTH_ARC", "D01_SYNTH_ARC", "K02_SYNTH_ARC"]

MARS_EQC_WKT = (
    'PROJCS["Synthetic Mars sphere equirectangular",GEOGCS["Synthetic Mars sphere",'
    'DATUM["Synthetic Mars sphere",SPHEROID["Synthetic Mars sphere",3396190,0]],'
    'PRIMEM["Reference Meridian",0],UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Equirectangular"],PARAMETER["standard_parallel_1",0],'
    'PARAMETER["central_meridian",0],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)
"""A synthetic test CRS on a Mars-radius sphere."""

WIDTH, HEIGHT = 600, 700
NODATA = -32767.0
HOLE = (slice(300, 320), slice(200, 260))
"""A nodata block inside the centred 256 x 256 window (rows 222-477, columns 172-427)."""

QA_TEXT = (
    "\t--> Setting number of processing threads to: 4\n"
    "\tFound input nodata value for DEM: -32767\n"
    "Max difference:       233.171 meters\n"
    "Min difference:       -158.493 meters\n"
    "Mean difference:      32.2583 meters\n"
    "StdDev of difference: 12.6913 meters\n"
    "Median difference:    32.3768 meters\n"
    "Writing difference file: aligned/synthetic-diff.csv\n"
)
QA_STD_LINE = "StdDev of difference: 12.6913 meters"

DOCUMENTATION = """<html><body><h1>Mars ASP Generated CTX DTMs and Orthoimages</h1>
<p>The DTMs are aligned and controlled to ground using individual MOLA shot tracks.</p>
<p>The qa_metrics.txt file for each DTM provides the maximum, minimum, mean, median, and
standard deviation of the final CTX-MOLA differences after ground control. In general, it is
desirable that all of these values be close to zero.</p>
<p>Each CTX DTM gives elevation values above or below the Mars geoid (referred to as the areoid)
and is presented in a simple cylindrical projection.</p>
</body></html>"""

TUTORIAL_COMMAND = (
    "stac-client search https://stac.astrogeology.usgs.gov/api/ --bbox 76.69 17.41 78.69 19.41 "
    "-c mro_ctx_controlled_usgs_dtms --matched"
)

TUTORIAL = f"""<html><body><h2>Where is Jezero Crater?</h2>
<p>Jezero is located at Longitude Latitude 77.69 E, 18.41 N in mc13</p>
<p>(e.g. <code>--bbox 76.69 17.41 78.69 19.41</code>).</p>
<pre><code><span class="line"><span class="cl">{TUTORIAL_COMMAND}
</span></span></code></pre>
<pre><code>80 items matched
</code></pre></body></html>"""

CS_NAME = "Planetocentric, +East, 0 - 360"

GAZETTEER = f"""<html><body><h3>Basic Info</h3><td>Arcadia Planitia</td>
<select name="selected_control_net" id="selected_control_net">
<option label="MDIM 2.0" value="348"
></option>
<option label="MDIM 2.1" value="25330"
selected ></option>
</select>
<input id="selected_coordinate_system" type="hidden" value="6">
<span id="coordinate_system_name" value="{CS_NAME}">{CS_NAME}</span>
<tr class="geometry geom-348 cs-6">
<th>Northmost Latitude</th>
<td>
54.68 &deg;
</td>
</tr>
<tr class="geometry geom-25330 cs-6">
<th>Northmost Latitude</th>
<td>
64.17 &deg;
</td>
</tr>
<tr class="geometry geom-25330 cs-6">
<th>Southmost Latitude</th>
<td>
33.87 &deg;
</td>
</tr>
<tr class="geometry geom-25330 cs-6">
<th>Eastmost Longitude</th>
<td>
210.43 &deg;
</td>
</tr>
<tr class="geometry geom-25330 cs-6">
<th>Westmost Longitude</th>
<td>
165.86 &deg;
</td>
</tr>
<tr class="geometry geom-25330 cs-7">
<th>Eastmost Longitude</th>
<td>
149.57 &deg;
</td>
</tr>
</body></html>"""


def write_cog(path: Path) -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(5)
    array = rng.normal(-2500.0, 40.0, (HEIGHT, WIDTH)).astype(np.float32)
    array[:30, :] = NODATA
    array[HOLE] = NODATA
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=WIDTH,
        height=HEIGHT,
        count=1,
        dtype="float32",
        crs=MARS_EQC_WKT,
        transform=from_origin(4_563_975.0, 1_082_444.0, 20.0, 20.0),
        nodata=NODATA,
        blocksize=256,
        compress="deflate",
        predictor=3,
        overview_resampling="nearest",
    ) as dst:
        dst.write(array, 1)
        dst.update_tags(GEOID="mola")
    return array


def write_small_cog(path: Path) -> bytes:
    """A 32 x 32 COG of a few kB: smaller than one 8192-byte range block."""
    array = np.full((32, 32), -2500.0, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=32,
        height=32,
        count=1,
        dtype="float32",
        crs=MARS_EQC_WKT,
        transform=from_origin(4_563_975.0, 1_082_444.0, 20.0, 20.0),
        nodata=NODATA,
        compress="deflate",
    ) as dst:
        dst.write(array, 1)
    return path.read_bytes()


SIGNED_QUERY = "?X-Amz-Credential=SECRETCRED&X-Amz-Signature=SECRETSIG"
"""Appended to an asset href to make it signed; the secrets must never reach output or errors."""


def item(base: str, item_id: str, *, qa: bool = True, signed: str | None = None) -> dict[str, Any]:
    folder = f"{base}/assets/{item_id}"
    assets: dict[str, Any] = {
        "dtm": {
            "href": f"{folder}/{item_id}_dtm.tif",
            "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            "title": "Digital Terrain Model",
            "roles": ["data"],
            "raster:bands": [{"data_type": "float32", "nodata": -32767}],
        },
        "thumbnail": {"href": f"{folder}/{item_id}.jpg", "type": "image/jpeg", "roles": ["x"]},
    }
    if qa:
        assets["qa_metrics"] = {
            "href": f"{folder}/qa_metrics.txt",
            "type": "text/plain",
            "title": "ASP Generated Quality Assurance Metrics",
            "roles": ["metadata"],
        }
    if signed is not None:
        assets[signed]["href"] += SIGNED_QUERY
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": COLLECTION,
        "bbox": [77.0, 17.1, 77.6, 18.2],
        "properties": {
            "title": f"DTM: {item_id}",
            "start_datetime": "2023-04-18T12:00:00.000000Z",
            "end_datetime": "2023-04-18T12:00:00.000000Z",
            "datetime": None,
            "gsd": 20.0,
            "license": "CC0-1.0",
            "proj:shape": [HEIGHT, WIDTH],
        },
        "assets": assets,
        "links": [],
    }


def collection(*, licence: bool = True, doi: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "Collection",
        "id": COLLECTION,
        "stac_version": "1.0.0",
        "title": "Synthetic MRO CTX DTMs",
        "description": "Synthetic collection for tests.",
        "providers": [
            {"name": "NASA", "roles": ["producer"]},
            {"name": "USGS", "roles": ["processor", "host"]},
        ],
        "links": [],
    }
    if licence:
        body["license"] = "CC0-1.0"
    if doi:
        body["sci:doi"] = "10.5066/P9JKVWR3"
        body["sci:citation"] = "Synthetic, 2023. https://doi.org/10.5066/P9JKVWR3"
        body["links"].append({"rel": "cite-as", "href": "https://doi.org/10.5066/P9JKVWR3"})
    return body


@dataclass
class Content:
    """What the stub server serves; tests change it before the fetch."""

    collection: dict[str, Any] = field(default_factory=collection)
    jezero_ids: list[str] = field(default_factory=lambda: list(JEZERO_IDS))
    arcadia_ids: list[str] = field(default_factory=lambda: list(ARCADIA_IDS))
    jezero_matched: int | None = 4
    no_qa_for: set[str] = field(default_factory=set)
    qa_text: str = QA_TEXT
    documentation: str = DOCUMENTATION
    tutorial: str = TUTORIAL
    gazetteer: str = GAZETTEER
    fail: dict[str, int] = field(default_factory=dict)
    """Path prefix -> HTTP status to answer instead."""
    signed: dict[str, str] = field(default_factory=dict)
    """Item ID -> the asset key whose href the search pages serve signed."""


@dataclass
class ServerState:
    content: Content
    cog: bytes
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
        parts = urllib.parse.urlsplit(self.path)
        if parts.path == "/api/search":
            self._search(urllib.parse.parse_qs(parts.query))
        elif parts.path == f"/api/collections/{COLLECTION}":
            self._json(content.collection)
        elif parts.path == "/docs/ctxdtms/":
            self._send(200, content.documentation.encode(), "text/html")
        elif parts.path == "/docs/tutorials/mosaic_json/":
            self._send(200, content.tutorial.encode(), "text/html")
        elif parts.path == "/Feature/348":
            self._send(200, content.gazetteer.encode(), "text/html; charset=utf-8")
        elif parts.path.endswith("/qa_metrics.txt"):
            self._send(200, content.qa_text.encode(), "text/plain")
        elif parts.path.endswith("_dtm.tif"):
            self._range(state.cog)
        else:
            self._send(404, b"not found", "text/plain")

    def _json(self, body: Any) -> None:
        self._send(200, json.dumps(body).encode(), "application/geo+json")

    def _search(self, query: dict[str, list[str]]) -> None:
        state = self.state
        bbox = query["bbox"][0]
        limit = int(query["limit"][0])
        assert query["collections"] == [COLLECTION]
        if bbox == JEZERO_BOX:
            ids, matched = state.content.jezero_ids, state.content.jezero_matched
        elif bbox == ARCADIA_BOX:
            ids, matched = state.content.arcadia_ids, None
        else:
            raise AssertionError(f"unexpected bbox {bbox}")
        start = int(query.get("next", ["0"])[0])
        chunk = ids[start : start + limit]
        links: list[dict[str, Any]] = [{"rel": "root", "href": f"{state.base}/api"}]
        if len(chunk) == limit:  # as the real server: a full page always links a next page
            next_query = urllib.parse.urlencode(
                {"collections": COLLECTION, "bbox": bbox, "limit": limit, "next": start + limit}
            )
            links.insert(
                0,
                {"rel": "next", "method": "GET", "href": f"{state.base}/api/search?{next_query}"},
            )
        body: dict[str, Any] = {
            "type": "FeatureCollection",
            "numberReturned": len(chunk),
            "features": [
                item(
                    state.base,
                    i,
                    qa=i not in state.content.no_qa_for,
                    signed=state.content.signed.get(i),
                )
                for i in chunk
            ],
            "links": links,
        }
        if matched is not None:
            body["numberMatched"] = matched
        self._json(body)

    def _range(self, data: bytes) -> None:
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", self.headers.get("Range", ""))
        assert match is not None, "the DTM must only be read by range"
        first, last = int(match.group(1)), min(int(match.group(2)), len(data) - 1)
        self._send(
            206,
            data[first : last + 1],
            "image/tiff",
            Content_Range=f"bytes {first}-{last}/{len(data)}",
            ETag='"dtm-v1"',
            Last_Modified="Sun, 14 May 2023 04:03:23 GMT",
        )


class Stub:
    def __init__(self, tmp: Path, content: Content) -> None:
        self.cog_path = tmp / "served_dtm.tif"
        self.array = write_cog(self.cog_path)
        self.state = ServerState(content=content, cog=self.cog_path.read_bytes())
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

    def source(self, **fields: Any) -> pilot.CtxSource:
        return pilot.CtxSource(
            api_url=f"{self.base}/api",
            documentation_url=f"{self.base}/docs/ctxdtms/",
            jezero_basis_url=f"{self.base}/docs/tutorials/mosaic_json/",
            arcadia_basis_url=f"{self.base}/Feature/348",
            page_limit=LIMIT,
            **{"range_block_bytes": 8192, **fields},
        )


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 14, 22, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def run_fetch(root: Path, stub: Stub) -> int:
    return pilot.main(["fetch", "ctx"], root=root, sources={"ctx": stub.source()}, now=Clock())


def run(command: str, root: Path, stub: Stub) -> int:
    return pilot.main([command, "ctx"], root=root, sources={"ctx": stub.source()})


@pytest.fixture
def stub(tmp_path: Path) -> Iterator[Stub]:
    server = Stub(tmp_path, Content())
    try:
        yield server
    finally:
        server.stop()


def fetch_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: Content | None = None
) -> tuple[Path, Stub]:
    """Fetch from a fresh stub, stop it, then record with sockets refused."""
    root = tmp_path / "repo"
    root.mkdir()
    server = Stub(tmp_path, content or Content())
    try:
        assert run_fetch(root, server) == 0
    finally:
        server.stop()
    with monkeypatch.context() as patch:
        refuse_sockets(patch)
        assert run("record", root, server) == 0
    return root, server


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Stub]:
    return fetch_and_record(tmp_path, monkeypatch)


def fetch_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((root / "data/raw/ctx/fetch.json").read_bytes())
    return result


def ctx_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((root / "outputs/pilot/ctx_dtm.json").read_bytes())
    return result


def sources(root: Path) -> list[ProductRecord]:
    return TypeAdapter(SourcesFile).validate_json((root / "outputs/sources.json").read_bytes())


def probe(record: dict[str, Any], key: str) -> dict[str, Any]:
    (found,) = [p for p in record["probes"] if p["key"] == key]
    result: dict[str, Any] = found
    return result


def centre_window() -> tuple[int, int]:
    return (HEIGHT - 256) // 2, (WIDTH - 256) // 2


# --- fetch --------------------------------------------------------------------------------------


def test_fetch_saves_every_page_the_documents_qa_and_the_window_ranges(
    tmp_path: Path, stub: Stub
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 0
    raw = root / "data/raw/ctx"
    fetched = fetch_json(root)
    roles = [entry["role"] for entry in fetched["documents"]]
    assert roles.count("stac_search") == 5  # Jezero: 2 + 2 + 0 items; Arcadia: 2 + 1
    for role in ("collection", "documentation", "jezero_box_basis", "arcadia_box_basis"):
        assert roles.count(role) == 1
    pages = [entry for entry in fetched["documents"] if entry["role"] == "stac_search"]
    assert [(p["probe"], p["page"]) for p in pages] == [
        ("jezero", 1),
        ("jezero", 2),
        ("jezero", 3),
        ("arcadia_planitia", 1),
        ("arcadia_planitia", 2),
    ]
    for entry in fetched["documents"]:
        local = raw / entry["file_name"]
        assert local.read_bytes()
        assert hashlib.sha256(local.read_bytes()).hexdigest() == entry["sha256"]
        assert entry["http_status"] == 200
        assert entry["retrieved_at"].endswith("Z")
    assert fetched["choice"]["item_id"] == FIRST_JEZERO
    files = {entry["role"]: entry for entry in fetched["files"]}
    assert set(files) == {"qa_metrics", "dtm_window"}
    assert (raw / files["qa_metrics"]["file_name"]).read_text() == QA_TEXT
    window = files["dtm_window"]
    assert window["read"] == "window"
    assert window["provider_file_name"] == f"{FIRST_JEZERO}_dtm.tif"
    assert window["file_name"] == f"{FIRST_JEZERO}_dtm.tif.ranges"
    assert window["remote_bytes"] == len(stub.state.cog)
    assert window["bytes_received"] < len(stub.state.cog)
    assert window["window"] == {"col_off": 172, "row_off": 222, "width": 256, "height": 256}
    exchanges = window["exchanges"]
    assert len(exchanges) == window["range_requests"] >= 2
    assert sum(e["bytes_received"] for e in exchanges) == window["bytes_received"]
    for exchange in exchanges:
        assert exchange["http_status"] == 206
        assert re.fullmatch(r"bytes=[0-9]+-[0-9]+", exchange["requested_range"])
        assert re.fullmatch(
            rf"bytes [0-9]+-[0-9]+/{len(stub.state.cog)}", exchange["content_range"]
        )
        assert exchange["retrieved_at"].endswith("Z")
    dtm_requests = [(path, rng) for path, rng in stub.state.requests if path.endswith("_dtm.tif")]
    assert dtm_requests and all(rng is not None for _, rng in dtm_requests)
    assert not any(name.startswith(".fetch-") for name in (p.name for p in raw.iterdir()))


def test_the_registered_source_reads_the_usgs_stac_api() -> None:
    source = pilot.SOURCES["ctx"]
    assert isinstance(source, pilot.CtxSource)
    assert source.api_url == "https://stac.astrogeology.usgs.gov/api"
    assert source.collection == COLLECTION
    assert source.window_pixels == 256
    assert source.jezero_bbox == (76.69, 17.41, 78.69, 19.41)
    assert source.arcadia_bbox == (165.86, 33.87, -149.57, 64.17)
    assert source.documentation_url == "https://stac.astrogeology.usgs.gov/docs/data/mars/ctxdtms/"
    assert source.jezero_basis_url == (
        "https://stac.astrogeology.usgs.gov/docs/tutorials/mosaic_json/"
    )
    assert source.arcadia_basis_url == "https://planetarynames.wr.usgs.gov/Feature/348"


@pytest.mark.parametrize("prefix", ["/api/search", "/api/collections/", "/docs/ctxdtms/"])
def test_an_unreachable_stac_page_or_document_fails_naming_the_url(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str], prefix: str
) -> None:
    stub.state.content.fail = {prefix: 500}
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 1
    error = capsys.readouterr().err
    assert f"{stub.base}{prefix}" in error
    assert "HTTP 500" in error
    assert not (root / "data/raw/ctx/fetch.json").exists()


@pytest.mark.parametrize("suffix", ["_dtm.tif", "/qa_metrics.txt"])
def test_an_unreachable_asset_fails_naming_the_url(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str], suffix: str
) -> None:
    asset = f"/assets/{FIRST_JEZERO}/{FIRST_JEZERO}_dtm.tif"
    if suffix != "_dtm.tif":
        asset = f"/assets/{FIRST_JEZERO}/qa_metrics.txt"
    stub.state.content.fail = {asset: 404}
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 1
    error = capsys.readouterr().err
    assert f"{stub.base}{asset}" in error
    assert "404" in error
    assert not (root / "data/raw/ctx/fetch.json").exists()


def test_a_refused_connection_fails_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        port = probe_socket.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    source = pilot.CtxSource(
        api_url=f"{base}/api",
        documentation_url=f"{base}/docs/",
        jezero_basis_url=f"{base}/t/",
        arcadia_basis_url=f"{base}/g",
    )
    root = tmp_path / "repo"
    root.mkdir()
    assert pilot.main(["fetch", "ctx"], root=root, sources={"ctx": source}, now=Clock()) == 1
    error = capsys.readouterr().err
    assert f"{base}/api/collections/{COLLECTION}" in error
    assert "could not connect" in error


@pytest.mark.parametrize("case", ["block_larger_than_the_cog", "cog_smaller_than_a_block"])
def test_a_range_read_that_would_take_the_whole_cog_fails_naming_the_url(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    fields: dict[str, Any] = {}
    if case == "block_larger_than_the_cog":
        fields["range_block_bytes"] = len(stub.state.cog)
    else:
        stub.state.cog = write_small_cog(tmp_path / "small_dtm.tif")
        assert len(stub.state.cog) < 8192
    root = tmp_path / "repo"
    root.mkdir()
    source = stub.source(**fields)
    assert pilot.main(["fetch", "ctx"], root=root, sources={"ctx": source}, now=Clock()) == 1
    error = capsys.readouterr().err
    assert f"{stub.base}/assets/{FIRST_JEZERO}/{FIRST_JEZERO}_dtm.tif" in error
    assert "whole" in error
    assert not (root / "data/raw/ctx/fetch.json").exists()
    assert not list((root / "data/raw/ctx").glob("*.ranges"))


@pytest.mark.parametrize("asset", ["thumbnail", "dtm", "qa_metrics"])
def test_a_signed_asset_href_fails_the_fetch_before_any_asset_request(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str], asset: str
) -> None:
    # The thumbnail is never fetched, so only this check keeps its signed href out of the record.
    stub.state.content.signed = {FIRST_JEZERO: asset}
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 1
    error = capsys.readouterr().err
    assert FIRST_JEZERO in error
    assert f"the {asset} asset" in error
    assert "credentials or a signature" in error
    assert "SECRET" not in error
    assert not [path for path, _ in stub.state.requests if path.startswith("/assets/")]
    assert not (root / "data/raw/ctx/fetch.json").exists()


def test_a_signed_href_on_an_item_not_chosen_does_not_stop_the_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the chosen item's assets are followed or recorded; other items are only counted.
    content = Content(signed={"P02_SYNTH_JEZ": "thumbnail"})
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    assert "SECRET" not in (root / "outputs/pilot/ctx_dtm.json").read_text()
    assert "SECRET" not in (root / "outputs/sources.json").read_text()


def test_a_failed_fetch_keeps_an_earlier_complete_fetch(
    tmp_path: Path, stub: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert run_fetch(root, stub) == 0
    before = (root / "data/raw/ctx/fetch.json").read_bytes()
    stub.state.content.fail = {"/api/search": 503}
    assert run_fetch(root, stub) == 1
    assert (root / "data/raw/ctx/fetch.json").read_bytes() == before
    assert run("record", root, stub) == 0


# --- record: the search -------------------------------------------------------------------------


def test_record_counts_the_items_found_over_every_page(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    record = ctx_json(root)
    jezero = probe(record, "jezero")
    assert jezero["search"]["items_found"] == 4
    assert jezero["search"]["item_ids"] == sorted(JEZERO_IDS)
    assert len(jezero["search"]["pages"]) == 3
    assert [p["number_returned"] for p in jezero["search"]["pages"]] == [2, 2, 0]
    assert jezero["search"]["number_matched_stated"] == [4]
    assert jezero["search"]["duplicate_item_ids"] == []
    assert jezero["search"]["pages"][-1]["next_url"] is None
    arcadia = probe(record, "arcadia_planitia")
    assert arcadia["search"]["items_found"] == 3
    assert arcadia["search"]["number_matched_stated"] == []
    assert len(arcadia["search"]["pages"]) == 2
    first = jezero["search"]["pages"][0]
    assert first["url"] == jezero["search"]["first_url"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(first["url"]).query)
    assert query == {"collections": [COLLECTION], "bbox": [JEZERO_BOX], "limit": [str(LIMIT)]}
    assert first["local_path"].startswith("data/raw/ctx/")


def test_the_items_found_are_not_copied_from_the_prd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(jezero_ids=["Z1", "A9", "M5"], jezero_matched=7)
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    jezero = probe(ctx_json(root), "jezero")
    assert jezero["search"]["items_found"] == 3
    assert jezero["search"]["number_matched_stated"] == [7]
    assert ctx_json(root)["chosen_item"]["item_id"] == "A9"


def test_the_boxes_are_pilot_probes_with_a_confirmed_basis(recorded: tuple[Path, Stub]) -> None:
    root, stub = recorded
    record = ctx_json(root)
    jezero = probe(record, "jezero")
    arcadia = probe(record, "arcadia_planitia")
    for box in (jezero, arcadia):
        assert "pilot probe" in box["role"]
        assert "not a target window" in box["role"]
    assert jezero["bbox"] == [76.69, 17.41, 78.69, 19.41]
    assert jezero["bbox_crosses_antimeridian"] is False
    assert arcadia["bbox"] == [165.86, 33.87, -149.57, 64.17]
    assert arcadia["bbox_crosses_antimeridian"] is True
    assert jezero["basis"]["confirmed"] is True
    assert jezero["basis"]["document"] == f"{stub.base}/docs/tutorials/mosaic_json/"
    assert (
        "--bbox 76.69 17.41 78.69 19.41 -c mro_ctx_controlled_usgs_dtms"
        in (jezero["basis"]["quotes"][0])
    )
    assert (
        "Jezero is located at Longitude Latitude 77.69 E, 18.41 N" in jezero["basis"]["quotes"][1]
    )
    assert arcadia["basis"]["confirmed"] is True
    stated = arcadia["basis"]["stated"]
    assert stated["control_network"] == "MDIM 2.1"
    assert stated["coordinate_system"] == "Planetocentric, +East, 0 - 360"
    assert stated["northmost_latitude"] == "64.17"
    assert stated["southmost_latitude"] == "33.87"
    assert stated["eastmost_longitude"] == "210.43"
    assert stated["westmost_longitude"] == "165.86"
    assert record["target_windows"]["chosen"] is False
    assert "PRD 15" in record["target_windows"]["reason"]


def test_a_basis_missing_from_its_document_is_recorded_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(
        tutorial="<html><p>No box here.</p></html>",
        gazetteer=GAZETTEER.replace("210.43", "211.00"),
    )
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    record = ctx_json(root)
    jezero = probe(record, "jezero")
    assert jezero["basis"]["confirmed"] is False
    assert jezero["basis"]["quotes"] == []
    arcadia = probe(record, "arcadia_planitia")
    assert arcadia["basis"]["confirmed"] is False
    assert arcadia["basis"]["stated"]["eastmost_longitude"] == "211.00"
    assert arcadia["bbox"] == [165.86, 33.87, -149.57, 64.17]


def test_the_chosen_item_is_the_lexicographically_first_jezero_id(
    recorded: tuple[Path, Stub],
) -> None:
    root, stub = recorded
    chosen = ctx_json(root)["chosen_item"]
    assert chosen["item_id"] == FIRST_JEZERO == min(JEZERO_IDS)
    assert "lexicographically first" in chosen["rule"]
    assert chosen["probe"] == "jezero"
    assert chosen["found_on_page"] == 2
    assets = chosen["assets"]
    assert assets["dtm"]["href"] == f"{stub.base}/assets/{FIRST_JEZERO}/{FIRST_JEZERO}_dtm.tif"
    assert assets["qa_metrics"]["href"] == f"{stub.base}/assets/{FIRST_JEZERO}/qa_metrics.txt"
    assert assets["thumbnail"]["type"] == "image/jpeg"
    assert chosen["properties"]["start_datetime"] == "2023-04-18T12:00:00.000000Z"


def test_no_jezero_item_is_recorded_and_nothing_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stub = fetch_and_record(tmp_path, monkeypatch, Content(jezero_ids=[]))
    record = ctx_json(root)
    assert record["chosen_item"]["item_id"] is None
    assert "no item" in record["chosen_item"]["reason"]
    assert record["dtm"] is None
    assert record["qa_metrics"]["status"] == "not_read"
    assert not any(path.endswith("_dtm.tif") for path, _ in stub.state.requests)
    assert [r.product_id for r in sources(root)] == []


# --- record: the DTM window ---------------------------------------------------------------------


def test_the_dtm_window_is_read_by_range_with_its_header(recorded: tuple[Path, Stub]) -> None:
    root, stub = recorded
    dtm = ctx_json(root)["dtm"]
    header = dtm["header"]
    assert (header["width"], header["height"], header["count"]) == (WIDTH, HEIGHT, 1)
    assert header["dtypes"] == ["float32"]
    assert header["block_shapes"] == [[256, 256]]
    assert header["image_structure"]["LAYOUT"] == "COG"
    assert header["tags"]["GEOID"] == "mola"
    assert header["nodata"] == [NODATA]
    assert header["units"] == [None]
    assert "3396190" in header["crs_wkt"]
    assert header["transform"] == [20.0, 0.0, 4_563_975.0, 0.0, -20.0, 1_082_444.0]
    assert header["pixel_size"] == {"x": 20.0, "y": 20.0, "unit": "metre"}
    assert dtm["band"] == 1
    assert dtm["units_stated_by_file"] is None
    read = dtm["range_read"]
    assert read["whole_file_downloaded"] is False
    assert read["remote_bytes"] == len(stub.state.cog)
    assert 0 < read["bytes_received"] < len(stub.state.cog)
    assert read["local_path"] == f"data/raw/ctx/{FIRST_JEZERO}_dtm.tif.ranges"
    assert len(read["exchanges"]) == read["range_requests"]
    assert dtm["window"] == {"col_off": 172, "row_off": 222, "width": 256, "height": 256}
    assert dtm["stac_raster_band"] == {
        "data_type": "float32",
        "nodata": -32767,
        "matches_file": True,
    }


def test_the_valid_fraction_comes_from_quality_on_the_window(recorded: tuple[Path, Stub]) -> None:
    root, stub = recorded
    row, col = centre_window()
    window = stub.array[row : row + 256, col : col + 256]
    expected = valid_fraction(validity_mask(window, nodata=NODATA))
    valid = ctx_json(root)["dtm"]["valid"]
    assert valid["valid_pixels"] == expected.valid_pixels == 256 * 256 - 20 * 60
    assert valid["total_pixels"] == 256 * 256
    assert valid["valid_fraction"] == expected.fraction
    assert valid["invalid_by_reason"]["nodata"] == 20 * 60
    assert valid["min_stored_valid"] == float(window[window != NODATA].min())
    assert valid["max_stored_valid"] == float(window[window != NODATA].max())
    assert "quality.validity_mask" in valid["method"]


# --- record: qa_metrics.txt ---------------------------------------------------------------------


def test_the_ctx_mola_std_dev_is_quoted_exactly_with_its_line(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    qa = ctx_json(root)["qa_metrics"]
    assert qa["status"] == "read"
    assert qa["text"] == QA_TEXT
    std = qa["ctx_mola_std_dev"]
    assert std["line"] == QA_STD_LINE
    assert std["line_number"] == 6
    assert std["value"] == "12.6913"
    assert std["unit"] == "meters"
    assert qa["local_path"] == "data/raw/ctx/qa_metrics.txt"
    definition = qa["definition"]
    assert definition["quote"].startswith("The qa_metrics.txt file for each DTM provides")
    assert "CTX-MOLA differences after ground control" in definition["quote"]
    threshold = qa["ctx_qa_threshold"]
    assert threshold["applied"] is False
    assert threshold["value"] is None
    assert "PRD 15" in threshold["reason"]


def test_an_item_without_qa_metrics_records_it_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stub = fetch_and_record(tmp_path, monkeypatch, Content(no_qa_for={FIRST_JEZERO}))
    qa = ctx_json(root)["qa_metrics"]
    assert qa["status"] == "absent"
    assert FIRST_JEZERO in qa["reason"]
    assert qa["ctx_mola_std_dev"] is None
    assert not any(path.endswith("qa_metrics.txt") for path, _ in stub.state.requests)
    ids = [r.product_id for r in sources(root)]
    assert ids == [f"CTX/{COLLECTION}/{FIRST_JEZERO}/{FIRST_JEZERO}_dtm.tif"]


def test_qa_metrics_without_a_std_dev_line_records_the_value_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = QA_TEXT.replace(QA_STD_LINE + "\n", "")
    root, _ = fetch_and_record(tmp_path, monkeypatch, Content(qa_text=text))
    qa = ctx_json(root)["qa_metrics"]
    assert qa["status"] == "read"
    assert qa["ctx_mola_std_dev"] is None
    assert "no 'StdDev of difference' line" in qa["ctx_mola_std_dev_absent_reason"]


# --- record: licence, DOI and product records ---------------------------------------------------


def test_licence_and_doi_are_recorded_as_the_collection_states_them(
    recorded: tuple[Path, Stub],
) -> None:
    root, stub = recorded
    record = ctx_json(root)
    assert record["collection"]["license"] == {
        "value": "CC0-1.0",
        "stated_by": f"{stub.base}/api/collections/{COLLECTION} (license)",
    }
    assert record["collection"]["doi"] == {
        "value": "10.5066/P9JKVWR3",
        "stated_by": f"{stub.base}/api/collections/{COLLECTION} (sci:doi)",
    }
    for r in sources(root):
        assert "CC0-1.0" in r.license_or_attribution
        assert "10.5066/P9JKVWR3" in r.license_or_attribution


def test_licence_and_doi_not_stated_by_the_collection_are_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = Content(collection=collection(licence=False, doi=False))
    root, _ = fetch_and_record(tmp_path, monkeypatch, content)
    record = ctx_json(root)
    assert record["collection"]["license"] == {"value": "unknown", "stated_by": None}
    assert record["collection"]["doi"] == {"value": "unknown", "stated_by": None}
    for r in sources(root):
        assert r.license_or_attribution == "unknown"


def test_product_records_are_upserted_with_local_paths_and_checksums(
    recorded: tuple[Path, Stub],
) -> None:
    root, stub = recorded
    records = {r.product_id: r for r in sources(root)}
    dtm_id = f"CTX/{COLLECTION}/{FIRST_JEZERO}/{FIRST_JEZERO}_dtm.tif"
    qa_id = f"CTX/{COLLECTION}/{FIRST_JEZERO}/qa_metrics.txt"
    assert sorted(records) == [dtm_id, qa_id]
    dtm, qa = records[dtm_id], records[qa_id]
    for r in (dtm, qa):
        assert r.body == "mars"
        assert r.local_path is not None and r.local_path.startswith("data/raw/ctx/")
        assert r.checksum == hashlib.sha256((root / r.local_path).read_bytes()).hexdigest()
        assert r.verification_status == "PARTIAL"
        assert r.frequency_or_wavelength is None
        assert r.transmit_receive_convention is None
        assert r.incidence_source is None
        assert "NASA" in r.provider and "USGS" in r.provider
    assert dtm.local_path == f"data/raw/ctx/{FIRST_JEZERO}_dtm.tif.ranges"
    assert dtm.source_url == f"{stub.base}/assets/{FIRST_JEZERO}/{FIRST_JEZERO}_dtm.tif"
    assert dtm.units == "unknown"
    assert dtm.crs_and_frame.startswith("PROJCS[")
    assert dtm.native_grid is not None and "width=600 height=700" in dtm.native_grid
    assert dtm.qa_definition is not None and "-32767" in dtm.qa_definition
    assert dtm.acquisition_time == "unknown"
    assert qa.local_path == "data/raw/ctx/qa_metrics.txt"
    assert qa.source_url == f"{stub.base}/assets/{FIRST_JEZERO}/qa_metrics.txt"
    assert qa.units.startswith("meters")
    verification = ctx_json(root)["verification"]
    assert set(verification) == {dtm_id, qa_id}
    assert "not plotted" in verification[dtm_id]["basis"]


def test_record_keeps_other_sources_records_and_sorts_by_product_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "outputs").mkdir(parents=True)
    other = {
        "product_id": "AAA/other.tif",
        "provider": "p",
        "version": "unknown",
        "source_url": None,
        "local_path": None,
        "checksum": None,
        "body": "moon",
        "acquisition_time": None,
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
    assert run("record", root, server) == 0
    ids = [r.product_id for r in sources(root)]
    assert ids[0] == "AAA/other.tif"
    assert ids == sorted(ids)
    assert len(ids) == 3


# --- record and check: offline and reproducible -------------------------------------------------


def test_record_is_reproducible_and_check_matches(
    recorded: tuple[Path, Stub], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, stub = recorded
    before = {
        path: (root / path).read_bytes()
        for path in ("outputs/sources.json", "outputs/pilot/ctx_dtm.json")
    }
    refuse_sockets(monkeypatch)
    assert pilot.record("ctx", root=root, sources={"ctx": stub.source()}) == []
    assert pilot.check("ctx", root=root, sources={"ctx": stub.source()}) == []
    assert run("check", root, stub) == 0
    for path, data in before.items():
        assert (root / path).read_bytes() == data


def test_check_reports_drift_in_the_committed_record(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    path = root / "outputs/pilot/ctx_dtm.json"
    path.write_bytes(path.read_bytes().replace(b'"jezero"', b'"jezer0"', 1))
    assert run("check", root, stub) == 1
    assert "ctx_dtm.json: differs" in capsys.readouterr().err


def test_check_without_cached_data_names_the_fetch_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = pilot.CtxSource()
    assert pilot.main(["check", "ctx"], root=tmp_path, sources={"ctx": source}) == 1
    assert "run: python -m terraluna_pipeline.pilot fetch ctx first" in capsys.readouterr().err


def test_record_refuses_changed_raw_bytes(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    qa = root / "data/raw/ctx/qa_metrics.txt"
    qa.write_text(QA_TEXT.replace("12.6913", "1.6913"))
    assert run("record", root, stub) == 1
    assert "does not match the size and sha256" in capsys.readouterr().err


def test_record_refuses_window_ranges_that_hold_the_whole_cog(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    # A fetch.json whose window entry holds every byte of the COG is not a window read, even
    # when its sizes and sha256 agree; record derives that from the stored ranges.
    root, stub = recorded
    before = (root / "outputs/pilot/ctx_dtm.json").read_bytes()
    path = root / "data/raw/ctx/fetch.json"
    fetched = fetch_json(root)
    (entry,) = [e for e in fetched["files"] if e["role"] == "dtm_window"]
    cog = stub.state.cog
    (root / "data/raw/ctx" / entry["file_name"]).write_bytes(cog)
    entry["ranges"] = [[0, len(cog) - 1]]
    entry["bytes_received"] = len(cog)
    entry["sha256"] = hashlib.sha256(cog).hexdigest()
    path.write_text(json.dumps(fetched))
    assert run("record", root, stub) == 1
    error = capsys.readouterr().err
    assert "whole" in error
    assert "run: python -m terraluna_pipeline.pilot fetch ctx" in error
    assert (root / "outputs/pilot/ctx_dtm.json").read_bytes() == before


def sign_saved_asset(root: Path, asset: str) -> None:
    """Sign ``asset`` of the chosen item in its saved search page, keeping fetch.json consistent."""
    path = root / "data/raw/ctx/fetch.json"
    fetched = fetch_json(root)
    for entry in fetched["documents"]:
        if entry["role"] != "stac_search" or entry["probe"] != "jezero":
            continue
        page_path = root / "data/raw/ctx" / entry["file_name"]
        page = json.loads(page_path.read_bytes())
        for feature in page["features"]:
            if feature["id"] == FIRST_JEZERO:
                feature["assets"][asset]["href"] += SIGNED_QUERY
                data = json.dumps(page).encode()
                page_path.write_bytes(data)
                entry["bytes_received"] = len(data)
                entry["sha256"] = hashlib.sha256(data).hexdigest()
    path.write_text(json.dumps(fetched))


@pytest.mark.parametrize("asset", ["thumbnail", "dtm"])
def test_record_refuses_a_saved_item_with_a_signed_asset_href(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str], asset: str
) -> None:
    root, stub = recorded
    before = {
        name: (root / name).read_bytes()
        for name in ("outputs/pilot/ctx_dtm.json", "outputs/sources.json")
    }
    sign_saved_asset(root, asset)
    for command in ("record", "check"):
        assert run(command, root, stub) == 1
        error = capsys.readouterr().err
        assert FIRST_JEZERO in error
        assert f"the {asset} asset" in error
        assert "SECRET" not in error
    assert {name: (root / name).read_bytes() for name in before} == before


SIGNED_URL = "https://example.test/astrogeology" + SIGNED_QUERY


@pytest.mark.parametrize(
    ("where", "json_path"),
    [
        ("provider_url", "$.collection.providers[0].url"),
        ("cite_as_link", "$.collection.cite_as"),
        ("citation_text", "$.collection.citation.value"),
    ],
)
def test_record_writes_no_output_that_holds_a_url_with_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    where: str,
    json_path: str,
) -> None:
    # Provider-stated URLs that fetch never requests are checked when the outputs are built.
    body = collection()
    if where == "provider_url":
        body["providers"][0]["url"] = SIGNED_URL
    elif where == "cite_as_link":
        body["links"][0]["href"] = SIGNED_URL
    else:
        body["sci:citation"] = f"Synthetic, 2023. {SIGNED_URL}."
    root = tmp_path / "repo"
    root.mkdir()
    server = Stub(tmp_path, Content(collection=body))
    try:
        assert run_fetch(root, server) == 0
    finally:
        server.stop()
    capsys.readouterr()
    for command in ("record", "check"):
        assert run(command, root, server) == 1
        error = capsys.readouterr().err
        assert "outputs/pilot/ctx_dtm.json" in error
        assert json_path in error
        assert "credentials or a signature" in error
        assert "SECRET" not in error
    assert not (root / "outputs/pilot/ctx_dtm.json").exists()
    assert not (root / "outputs/sources.json").exists()


def test_record_refuses_a_page_chain_that_does_not_link_up(
    recorded: tuple[Path, Stub], capsys: pytest.CaptureFixture[str]
) -> None:
    root, stub = recorded
    path = root / "data/raw/ctx/fetch.json"
    fetched = fetch_json(root)
    pages = [e for e in fetched["documents"] if e["role"] == "stac_search"]
    pages[1]["url"] = pages[1]["url"] + "&tampered=1"
    path.write_text(json.dumps(fetched))
    assert run("record", root, stub) == 1
    assert "next link" in capsys.readouterr().err


def test_the_record_states_open_g0_items_and_no_gate_result(recorded: tuple[Path, Stub]) -> None:
    root, _ = recorded
    record = ctx_json(root)
    text = json.dumps(record)
    assert "G0" in " ".join(record["open_g0_items"])
    assert "PASS" not in text
    assert "shipping mode" in record["description"]
    assert record["tool_versions"]["gdal"]
