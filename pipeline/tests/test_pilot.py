"""Tests for the pilot runner (PRD 5 pilot step 2, 6.1, 8.12, 9 G0) and its pgda78 source.

Everything is synthetic and offline: a mocked download layer serves tiny GeoTIFFs, a tiny ROI
archive and provider pages shaped like PGDA's, written into tmp_path. An autouse fixture
refuses every socket, so no test can reach the network.
"""

from __future__ import annotations

import gzip
import hashlib
import io as stdio
import json
import re
import socket
import tarfile
import warnings
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
import rasterio  # type: ignore[import-untyped]
import shapefile  # type: ignore[import-untyped]
from PIL import Image
from pydantic import TypeAdapter
from rasterio.transform import from_origin  # type: ignore[import-untyped]

from terraluna_pipeline import pilot
from terraluna_pipeline.io import (
    WHOLE_FILE_LIMIT_BYTES,
    DownloadError,
    FetchedFile,
    FileTooLargeError,
    RangeNotSupportedError,
    RangeReadError,
    RangeResponse,
)
from terraluna_pipeline.schema import ProductRecord, SourcesFile

# --- No network ---------------------------------------------------------------------------------


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise OSError("test refused a network access")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every name lookup and connection, so fetch only works through the mock."""
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


# --- A synthetic PGDA site ----------------------------------------------------------------------

BASE = "https://pgda.example.test/"
SITE_URL = BASE + "data/LOLA_5mpp/Site01/"
README_URL = BASE + "data/LOLA_5mpp/README"
INDEX_URL = BASE + "data/LOLA_5mpp/"
PRODUCT_URL = BASE + "products/78"

STEREO = "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
"""A south-polar stereographic CRS on a 1,737,400 m sphere; synthetic, as PGDA's files state."""

ESRI_PRJ = (
    'PROJCS["unknown",GEOGCS["GCS_unknown",DATUM["D_unknown",SPHEROID["unknown",{radius},0.0]],'
    'PRIMEM["Reference meridian",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Stereographic_South_Pole"],PARAMETER["False_Easting",0.0],'
    'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",0.0],'
    'PARAMETER["Standard_Parallel_1",-90.0],UNIT["Meter",1.0]]'
)

WIDTH, HEIGHT, PIXEL = 40, 30, 5.0
LEFT, TOP = -19000.0, -4000.0
TRANSFORM = from_origin(LEFT, TOP, PIXEL, PIXEL)
NAN_PIXELS = [(0, 0), (3, 7), (29, 39), (10, 10), (11, 10), (12, 10)]
"""(row, col) of DEM pixels that hold NaN, the declared nodata."""
COUNT_NAN_PIXELS = [(5, 5), (6, 6)]
"""(row, col) of count-map pixels that hold NaN while the DEM is valid there."""

SURF = "Site01_final_adj_5mpp_surf.tif"
LDEC = "Site01_final_adj_5mpp_ldec.tif"
SLP = "Site01_final_adj_5mpp_slp.tif"
TOTERR = "Site01_final_adj_5mpp_toterr.tif"
SLPERR = "Site01_final_adj_5mpp_slperr.tif"
ROIS = "Site01_ROIs.tgz"
XYZI = "Site01_final_adj.xyzi"
GEOTIFFS = (SURF, LDEC, SLP, TOTERR, SLPERR)

README = """LOLA data for candidate South Pole landing sites

Each directory contains 5 m/pix GeoTiffs with south polar stereographic X/Y coords in meters, \
MOON_ME reference frame of ephemeris DE421.

*_surf.tif : surface height Z (meters) with interpolation filling empty pix
*_ldec.tif : LDEC map, number of LOLA returns in each pixel
*_toterr.tif : Total Z uncertainty in meters (formally, the RMS error)
*_slp.tif : Slope (degrees)
*_slperr.tif : Slope uncertainty in degrees (formally, the RMS error)
Clones/ : Directory containing 100 clones of LDEM

*.xyzi - final point clouds after track adjustments and cleaning
*_ROIs.tgz - archive containing shapefiles (for GIS)

Site01: Connecting ridge
Site04: Shackleton rim
"""

INDEX = """<H1>South Pole Landing Site LOLA DEMs</H1>
<H2>Site 01</H2>
Site01 LDEM: <a href=./Site01/Site01_final_adj_5mpp_surf.tif>x</a><br>
Site01 XYZI: <a href=./Site01/Site01_final_adj.xyzi>x</a><br>
Site01 LDEC: <a href=./Site01/Site01_final_adj_5mpp_ldec.tif>x</a><br>
Site01 slope: <a href=./Site01/Site01_final_adj_5mpp_slp.tif>x</a><br>
Site01 total Z uncertainty: <a href=./Site01/Site01_final_adj_5mpp_toterr.tif>x</a><br>
Site01 slope uncertainty: <a href=./Site01/Site01_final_adj_5mpp_slperr.tif>x</a><br>
<a href=./Site01/Clones/Site01_final_adj_5mpp_0001_err.tif>001</a>
<H2>Site04</H2>
Site04 LDEM: <a href=./Site04/Site04_final_adj_5mpp_surf.tif>x</a><br>
"""

PRODUCT_PAGE_WITH_ROIS = """<html><body><h1>High-Resolution LOLA Topography</h1>
Site01 LDEM: <a href=https://pgda.example.test/data/LOLA_5mpp/Site01/Site01_final_adj_5mpp_surf.tif>a</a>
Site01 ROI shapefile: <a href="https://pgda.example.test/data/LOLA_5mpp/Site01/Site01_ROIs.tgz">b</a>
<a href=https://pgda.example.test/data/LOLA_5mpp/Site01/Clones/Site01_final_adj_5mpp_0002_err.tif>c</a>
<a href=https://pgda.example.test/data/LOLA_5mpp/87S/LDEM_87S_5MPP.TIF>d</a>
Please cite the following reference when using any of the products described above:
Barker et al. (2021) <a href="https://doi.org/10.1016/j.pss.2020.105119">doi:10.1016/j.pss.2020.105119</a>
</body></html>
"""
PRODUCT_PAGE_WITHOUT_ROIS = PRODUCT_PAGE_WITH_ROIS.replace("Site01_ROIs.tgz", "elsewhere.txt")


def heights() -> npt.NDArray[np.float32]:
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    array: npt.NDArray[np.float32] = (100.0 + 2.0 * rows + cols).astype(np.float32)
    for row, col in NAN_PIXELS:
        array[row, col] = np.nan
    return array


def counts() -> npt.NDArray[np.float32]:
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    array = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    array[(rows + cols) % 10 == 0] = 1.0
    array[(rows * cols) % 17 == 1] = 2.0
    array[0, 1] = 5.0
    for row, col in COUNT_NAN_PIXELS:
        array[row, col] = np.nan
    return array


def geotiff_bytes(
    array: npt.NDArray[Any],
    *,
    crs: str = STEREO,
    width: int = WIDTH,
    tmp: Path,
    **creation: Any,
) -> bytes:
    """A GeoTIFF of ``array``; ``creation`` passes GTiff creation options such as blockysize."""
    path = tmp / "scratch.tif"
    with (
        warnings.catch_warnings(),
        rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=width,
            height=array.shape[0],
            count=1,
            dtype=array.dtype,
            crs=crs,
            transform=TRANSFORM,
            nodata=float("nan"),
            **creation,
        ) as dst,
    ):
        dst.write(array, 1)
    data = path.read_bytes()
    path.unlink()
    return data


ROI_SQUARES = [
    # (left, bottom, right, top) in metres, inside the synthetic extent.
    (-18900.0, -4100.0, -18850.0, -4050.0),
    (-18860.0, -4120.0, -18820.0, -4080.0),
]


def roi_archive(
    *,
    prj_radius: str = "1737400.0",
    with_prj: bool = True,
    replace: dict[str, bytes] | None = None,
) -> bytes:
    """A tgz holding the ROI shapefile; ``replace`` swaps members (by name) for other bytes."""
    shp, shx, dbf = stdio.BytesIO(), stdio.BytesIO(), stdio.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as writer:
        writer.field("X", "N", 24, 15)
        writer.field("Y", "N", 24, 15)
        for left, bottom, right, top in ROI_SQUARES:
            ring = [[left, bottom], [left, top], [right, top], [right, bottom], [left, bottom]]
            writer.poly([ring])
            writer.record((left + right) / 2, (bottom + top) / 2)
    members: dict[str, bytes] = {
        "Site01_ROIs.shp": shp.getvalue(),
        "Site01_ROIs.shx": shx.getvalue(),
        "Site01_ROIs.dbf": dbf.getvalue(),
    }
    if with_prj:
        members["Site01_ROIs.prj"] = ESRI_PRJ.format(radius=prj_radius).encode()
    members.update(replace or {})
    buffer = stdio.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 1606827929
            archive.addfile(info, stdio.BytesIO(data))
        link = tarfile.TarInfo("Site01_ROIs.gmt")
        link.type, link.linkname, link.mtime = tarfile.SYMTYPE, "Site01_regions.gmt", 1606827929
        archive.addfile(link)
    return buffer.getvalue()


def site(tmp: Path, **overrides: bytes) -> dict[str, bytes]:
    """The synthetic server: URL -> body. Keyword overrides replace a body by file name."""
    files = {
        SURF: geotiff_bytes(heights(), tmp=tmp),
        LDEC: geotiff_bytes(counts(), tmp=tmp),
        SLP: geotiff_bytes(np.full((HEIGHT, WIDTH), 12.5, np.float32), tmp=tmp),
        TOTERR: geotiff_bytes(np.full((HEIGHT, WIDTH), 0.4, np.float32), tmp=tmp),
        SLPERR: geotiff_bytes(np.full((HEIGHT, WIDTH), 1.5, np.float32), tmp=tmp),
        ROIS: roi_archive(),
    }
    files.update({name: data for name, data in overrides.items() if name in files})
    served = {SITE_URL + name: data for name, data in files.items()}
    served[README_URL] = overrides.get("README", README.encode())
    served[INDEX_URL] = overrides.get("INDEX", INDEX.encode())
    served[PRODUCT_URL] = overrides.get("PRODUCT", PRODUCT_PAGE_WITH_ROIS.encode())
    return served


class MockDownloader:
    """Stands in for io.fetch_file: serves bodies from a dict and records every call."""

    def __init__(self, served: dict[str, bytes], *, fail: dict[str, Exception] | None = None):
        self.served = served
        self.fail = fail or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, dest: Path, *, max_bytes: int, magic: Sequence[bytes] = ()
    ) -> FetchedFile:
        self.calls.append({"url": url, "dest": dest, "max_bytes": max_bytes, "magic": magic})
        if url in self.fail:
            raise self.fail[url]
        if url not in self.served:
            raise DownloadError(f"{url}: HTTP 404 Not Found")
        data = self.served[url]
        if len(data) >= max_bytes:
            raise FileTooLargeError(f"{url}: {len(data)} bytes is over the limit")
        if magic and not any(data.startswith(prefix) for prefix in magic):
            raise DownloadError(f"{url}: the body does not start with the expected bytes")
        dest.write_bytes(data)
        return FetchedFile(
            url=url,
            final_url=url,
            status=200,
            content_type="application/octet-stream",
            content_length=len(data),
            bytes_received=len(data),
            last_modified="Fri, 05 Jun 2020 18:39:35 GMT",
            etag='W/"synthetic"',
            sha256=hashlib.sha256(data).hexdigest(),
            path=dest,
        )


FIXED = datetime(2026, 11, 14, 19, 30, 5, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED


class MockRangeFetcher:
    """Stands in for io.fetch_range: serves byte ranges of the bodies and records every call."""

    def __init__(self, served: dict[str, bytes], *, fail: dict[str, Exception] | None = None):
        self.served = served
        self.fail = fail or {}
        self.calls: list[tuple[str, int, int]] = []
        self.bytes_served: dict[str, int] = {}

    def __call__(self, url: str, first: int, last: int) -> RangeResponse:
        self.calls.append((url, first, last))
        if url in self.fail:
            raise self.fail[url]
        if url not in self.served:
            raise RangeReadError(f"{url}: HTTP 404 Not Found for bytes={first}-{last}")
        data = self.served[url]
        end = min(last, len(data) - 1)
        body = data[first : end + 1]
        self.bytes_served[url] = self.bytes_served.get(url, 0) + len(body)
        return RangeResponse(
            url=url,
            final_url=url,
            requested_first=first,
            requested_last=last,
            status=206,
            content_range=f"bytes {first}-{end}/{len(data)}",
            first_byte=first,
            last_byte=end,
            total_size=len(data),
            bytes_received=len(body),
            data=body,
            content_type="image/tiff",
            last_modified="Fri, 05 Jun 2020 18:39:35 GMT",
            etag='W/"synthetic"',
        )


WINDOW_PIXELS = 16
BLOCK_BYTES = 512
WINDOW = {"col_off": (WIDTH - 16) // 2, "row_off": (HEIGHT - 16) // 2, "width": 16, "height": 16}
"""The centred window of WINDOW_PIXELS on the synthetic 40 x 30 grid: columns 12-27, rows 7-22."""


def sources_for(**fields: Any) -> dict[str, pilot.Source]:
    return {"pgda78": pilot.Pgda78Source(base_url=BASE, **fields)}


def windowed_sources() -> dict[str, pilot.Source]:
    return sources_for(window_pixels=WINDOW_PIXELS, range_block_bytes=BLOCK_BYTES)


def do_fetch(
    root: Path,
    downloader: MockDownloader,
    read_range: MockRangeFetcher | None = None,
    sources: dict[str, pilot.Source] | None = None,
) -> None:
    pilot.fetch(
        "pgda78",
        root=root,
        sources=sources or sources_for(),
        download=downloader,
        read_range=read_range or MockRangeFetcher({}),
        now=fixed_clock,
    )


@pytest.fixture
def fetched(tmp_path: Path) -> Path:
    """A repository root whose data/raw/pgda78 holds a mocked fetch of the synthetic site."""
    root = tmp_path / "repo"
    root.mkdir()
    do_fetch(root, MockDownloader(site(tmp_path)))
    return root


@pytest.fixture
def recorded(fetched: Path) -> Path:
    pilot.record("pgda78", root=fetched, sources=sources_for())
    return fetched


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def pilot_json(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / "outputs/pilot/pgda78_site01.json").read_bytes())
    return data


def file_entry(record: dict[str, Any], name: str) -> dict[str, Any]:
    (entry,) = [item for item in record["files"] if item["file_name"] == name]
    return dict(entry)


def sources(root: Path) -> list[ProductRecord]:
    return TypeAdapter(SourcesFile).validate_json((root / "outputs/sources.json").read_bytes())


# --- fetch --------------------------------------------------------------------------------------


def test_fetch_writes_only_under_data_raw_pgda78(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "keep.txt").write_text("unchanged")
    before = snapshot(root)
    do_fetch(root, MockDownloader(site(tmp_path)))
    after = snapshot(root)
    changed = {path for path in after if before.get(path) != after[path]}
    assert changed
    assert all(path.startswith("data/raw/pgda78/") for path in changed)
    assert after["keep.txt"] == before["keep.txt"]
    leftovers = [p for p in (root / "data/raw/pgda78").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_fetch_records_retrieval_time_urls_status_and_byte_counts(fetched: Path) -> None:
    metadata = json.loads((fetched / "data/raw/pgda78/fetch.json").read_bytes())
    assert metadata["source"] == "pgda78"
    by_name = {item["file_name"]: item for item in metadata["files"]}
    assert set(by_name) == {*GEOTIFFS, ROIS}
    for name, item in by_name.items():
        data = (fetched / "data/raw/pgda78" / name).read_bytes()
        assert item["url"] == SITE_URL + name
        assert item["http_status"] == 200
        assert item["bytes_received"] == item["content_length"] == len(data)
        assert item["sha256"] == hashlib.sha256(data).hexdigest()
        assert item["retrieved_at"] == "2026-11-14T19:30:05Z"
    documents = {item["role"]: item for item in metadata["documents"]}
    assert documents["readme"]["url"] == README_URL
    assert documents["data_index"]["url"] == INDEX_URL
    assert documents["product_page"]["url"] == PRODUCT_URL


def test_fetch_uses_whole_file_limit_and_magic_and_skips_clones_points_and_mosaic(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    downloader = MockDownloader(site(tmp_path))
    do_fetch(root, downloader)
    urls = [call["url"] for call in downloader.calls]
    assert not any("Clones" in url or url.endswith(".xyzi") or "87S" in url for url in urls)
    assert all(call["max_bytes"] == WHOLE_FILE_LIMIT_BYTES for call in downloader.calls)
    by_url = {call["url"]: call for call in downloader.calls}
    for name in GEOTIFFS:
        assert set(by_url[SITE_URL + name]["magic"]) == {b"II*\x00", b"MM\x00*"}
    assert set(by_url[SITE_URL + ROIS]["magic"]) == {b"\x1f\x8b"}
    metadata = json.loads((root / "data/raw/pgda78/fetch.json").read_bytes())
    assert XYZI in {item["file_name"] for item in metadata["listed_not_fetched"]}
    # Two distinct clone files are linked (one per page); each counts once.
    assert metadata["subfolder_files_not_fetched"] == {"count": 2, "folders": ["Clones/"]}


@pytest.mark.parametrize("name", [SURF, SLPERR, ROIS])
def test_fetch_failure_exits_non_zero_naming_the_url_and_writes_no_record(
    tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    served = site(tmp_path)
    del served[SITE_URL + name]
    code = pilot.main(
        ["fetch", "pgda78"],
        root=root,
        sources=sources_for(),
        download=MockDownloader(served),
        now=fixed_clock,
    )
    assert code != 0
    assert SITE_URL + name in capsys.readouterr().err
    assert not (root / "data/raw/pgda78/fetch.json").exists()
    assert not (root / "outputs").exists()


def test_fetch_failure_keeps_an_earlier_complete_fetch_usable(
    fetched: Path, tmp_path: Path
) -> None:
    pilot.record("pgda78", root=fetched, sources=sources_for())
    before = snapshot(fetched / "data/raw/pgda78")
    served = site(tmp_path)
    del served[SITE_URL + SLP]
    with pytest.raises(DownloadError, match=re.escape(SLP)):
        do_fetch(fetched, MockDownloader(served))
    assert snapshot(fetched / "data/raw/pgda78") == before
    assert pilot.check("pgda78", root=fetched, sources=sources_for()) == []


# --- Files at or over the whole-file limit ------------------------------------------------------


def too_large(*names: str) -> dict[str, Exception]:
    return {
        SITE_URL + name: FileTooLargeError(f"{SITE_URL + name}: the file is over the limit")
        for name in names
    }


def striped_site(tmp_path: Path) -> dict[str, bytes]:
    """The synthetic site with one-row strips, so a window needs only some of each file."""
    return site(
        tmp_path,
        **{
            SURF: geotiff_bytes(heights(), tmp=tmp_path, blockysize=1),
            LDEC: geotiff_bytes(counts(), tmp=tmp_path, blockysize=1),
            SLP: geotiff_bytes(
                np.full((HEIGHT, WIDTH), 12.5, np.float32), tmp=tmp_path, blockysize=1
            ),
            TOTERR: geotiff_bytes(
                np.full((HEIGHT, WIDTH), 0.4, np.float32), tmp=tmp_path, blockysize=1
            ),
            SLPERR: geotiff_bytes(
                np.full((HEIGHT, WIDTH), 1.5, np.float32), tmp=tmp_path, blockysize=1
            ),
        },
    )


def window_of(array: npt.NDArray[Any]) -> npt.NDArray[Any]:
    rows = slice(WINDOW["row_off"], WINDOW["row_off"] + WINDOW["height"])
    cols = slice(WINDOW["col_off"], WINDOW["col_off"] + WINDOW["width"])
    return array[rows, cols]


@pytest.mark.parametrize("windowed", [(SURF,), GEOTIFFS], ids=["height-only", "every-geotiff"])
def test_a_geotiff_over_the_limit_is_read_by_window_and_never_downloaded(
    tmp_path: Path, windowed: tuple[str, ...]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    served = striped_site(tmp_path)
    downloader = MockDownloader(served, fail=too_large(*windowed))
    ranges = MockRangeFetcher(served)
    do_fetch(root, downloader, ranges, windowed_sources())
    raw = root / "data/raw/pgda78"
    assert {url for url, _, _ in ranges.calls} == {SITE_URL + name for name in windowed}
    for name in windowed:
        assert ranges.bytes_served[SITE_URL + name] < len(served[SITE_URL + name])
    metadata = json.loads((raw / "fetch.json").read_bytes())
    by_role = {item["role"]: item for item in metadata["files"]}
    entry = by_role["height"]
    assert entry["read"] == "window"
    assert entry["provider_file_name"] == SURF
    assert entry["file_name"] == SURF + ".ranges"
    assert not (raw / SURF).exists()
    cache = (raw / entry["file_name"]).read_bytes()
    assert entry["bytes_received"] == len(cache) == ranges.bytes_served[SITE_URL + SURF]
    assert entry["sha256"] == hashlib.sha256(cache).hexdigest()
    assert entry["remote_bytes"] == len(served[SITE_URL + SURF])
    assert entry["http_status"] == 206
    assert entry["window"] == WINDOW
    assert entry["range_requests"] == sum(1 for url, _, _ in ranges.calls if url == SITE_URL + SURF)
    assert "over the limit" in entry["whole_file_refused"]
    for first, last in entry["ranges"]:
        assert 0 <= first <= last < len(served[SITE_URL + SURF])

    pilot.record("pgda78", root=root, sources=windowed_sources())
    record = pilot_json(root)
    assert record["decoded_window"] == WINDOW
    surf = file_entry(record, SURF)
    assert surf["read"] == "window"
    assert surf["bytes"] == len(served[SITE_URL + SURF])
    assert surf["sha256"] == "unknown"
    assert surf["local_path"] == f"data/raw/pgda78/{SURF}.ranges"
    assert surf["range_read"]["local_sha256"] == hashlib.sha256(cache).hexdigest()
    assert surf["range_read"]["bytes_received"] == len(cache)
    assert surf["decoded_window"]["width"] == WINDOW["width"]
    assert (surf["width"], surf["height"]) == (WIDTH, HEIGHT)
    assert surf["extent_m"]["left"] == LEFT
    window_dem = window_of(heights())
    assert surf["valid"]["total_pixels"] == window_dem.size
    assert surf["valid"]["valid_pixels"] == int(np.sum(~np.isnan(window_dem)))
    for name in set(GEOTIFFS) - set(windowed):
        whole = file_entry(record, name)
        assert whole["read"] == "whole"
        assert whole["range_read"] is None
        assert whole["decoded_window"] == surf["decoded_window"]
        assert whole["valid"]["total_pixels"] == window_dem.size
    support = record["effective_support"]
    count = window_of(counts())
    valid = ~np.isnan(window_dem) & ~np.isnan(count)
    assert support["observed_pixels"] == int(np.sum(valid & (count >= 1)))
    assert support["interpolated_pixels"] == int(np.sum(valid & (count == 0)))
    preview = record["preview"]
    assert (preview["width"], preview["height"], preview["step"]) == (16, 16, 1)
    pixels = np.asarray(Image.open(root / "outputs/pilot/pgda78_site01_preview.png"))
    low, high = float(np.nanmin(window_dem)), float(np.nanmax(window_dem))
    assert preview["grey_scale"]["black_m"] == low
    roi = pixels == pilot.ROI_INDEX
    finite = ~np.isnan(window_dem) & ~roi
    assert np.array_equal(pixels[finite], pilot.grey_index(window_dem, low, high)[finite])
    assert roi.any()
    by_id = {r.product_id: r for r in sources(root)}
    height = by_id[f"PGDA78/Site01/{SURF}"]
    assert height.local_path == f"data/raw/pgda78/{SURF}.ranges"
    assert height.checksum == hashlib.sha256(cache).hexdigest()
    assert height.verification_status == "VERIFIED"
    assert "checksum" in record["field_notes"]
    assert pilot.check("pgda78", root=root, sources=windowed_sources()) == []
    first = snapshot(root / "outputs")
    pilot.record("pgda78", root=root, sources=windowed_sources())
    assert snapshot(root / "outputs") == first


def test_a_whole_read_records_no_window(recorded: Path) -> None:
    record = pilot_json(recorded)
    assert record["decoded_window"] is None
    for name in GEOTIFFS:
        entry = file_entry(record, name)
        assert entry["read"] == "whole"
        assert entry["range_read"] is None
        assert entry["decoded_window"] is None
    assert "checksum" not in record["field_notes"]


def test_a_tampered_range_cache_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    served = striped_site(tmp_path)
    do_fetch(
        root,
        MockDownloader(served, fail=too_large(SURF)),
        MockRangeFetcher(served),
        windowed_sources(),
    )
    cache = root / "data/raw/pgda78" / (SURF + ".ranges")
    data = cache.read_bytes()
    cache.write_bytes(data[:-1] + bytes([data[-1] ^ 0xFF]))
    with pytest.raises(pilot.PilotError, match=re.escape("fetch.json")):
        pilot.record("pgda78", root=root, sources=windowed_sources())


def test_a_failed_windowed_read_fails_fetch_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    served = striped_site(tmp_path)
    refused = RangeNotSupportedError(f"{SITE_URL + SURF}: the server ignored the Range header")
    code = pilot.main(
        ["fetch", "pgda78"],
        root=root,
        sources=windowed_sources(),
        download=MockDownloader(served, fail=too_large(SURF)),
        read_range=MockRangeFetcher(served, fail={SITE_URL + SURF: refused}),
        now=fixed_clock,
    )
    assert code != 0
    assert SITE_URL + SURF in capsys.readouterr().err
    assert not (root / "data/raw/pgda78/fetch.json").exists()


def test_an_roi_archive_over_the_limit_is_recorded_as_not_fetched(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    served = site(tmp_path)
    ranges = MockRangeFetcher(served)
    do_fetch(root, MockDownloader(served, fail=too_large(ROIS)), ranges)
    assert ranges.calls == []
    assert not (root / "data/raw/pgda78" / ROIS).exists()
    pilot.record("pgda78", root=root, sources=sources_for())
    roi = pilot_json(root)["roi"]
    assert roi["status"] == "not_fetched"
    assert "over the limit" in roi["reason"]
    assert "cannot be read by window" in roi["reason"]
    assert roi["archive"]["url"] == SITE_URL + ROIS
    assert roi["outlines_drawn"] is False
    assert roi["outlines_not_drawn_reason"] == roi["reason"]
    assert f"PGDA78/Site01/{ROIS}" not in {r.product_id for r in sources(root)}


def test_fetch_fails_when_the_listing_lacks_a_required_product(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    index = INDEX.replace("Site01_final_adj_5mpp_ldec.tif", "Site01_other.tif")
    downloader = MockDownloader(site(tmp_path, INDEX=index.encode()))
    with pytest.raises(pilot.PilotError, match=re.escape("_ldec.tif")):
        do_fetch(root, downloader)
    assert not (root / "data/raw/pgda78/fetch.json").exists()


def test_fetch_fails_when_the_readme_lacks_the_file_table(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    downloader = MockDownloader(site(tmp_path, README=b"<!DOCTYPE html><html>Not Found</html>"))
    with pytest.raises(pilot.PilotError, match="README"):
        do_fetch(root, downloader)


def test_a_signed_link_in_a_listing_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    index = INDEX.replace(
        "Site01_final_adj_5mpp_slp.tif>", "Site01_final_adj_5mpp_slp.tif?X-Amz-Signature=abc>"
    )
    with pytest.raises((pilot.PilotError, ValueError), match="credentials or a signature"):
        do_fetch(root, MockDownloader(site(tmp_path, INDEX=index.encode())))


# --- record -------------------------------------------------------------------------------------


def test_record_writes_the_pilot_json_preview_and_sources(recorded: Path) -> None:
    assert sorted(p.name for p in (recorded / "outputs/pilot").iterdir()) == [
        "pgda78_site01.json",
        "pgda78_site01_preview.png",
    ]
    assert (recorded / "outputs/sources.json").is_file()


def test_record_reports_file_names_urls_sizes_and_checksums(recorded: Path) -> None:
    record = pilot_json(recorded)
    for name in GEOTIFFS:
        entry = file_entry(record, name)
        data = (recorded / "data/raw/pgda78" / name).read_bytes()
        assert entry["url"] == SITE_URL + name
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["local_path"] == f"data/raw/pgda78/{name}"
        assert entry["retrieved_at"] == "2026-11-14T19:30:05Z"


def test_record_reads_crs_grid_extent_and_nodata_from_the_file(recorded: Path) -> None:
    entry = file_entry(pilot_json(recorded), SURF)
    with rasterio.open(recorded / "data/raw/pgda78" / SURF) as src:
        wkt = src.crs.to_wkt()
    assert entry["crs_wkt"] == wkt
    assert entry["crs_linear_units"] == "metre"
    assert entry["frame_named_in_file"] is None
    assert entry["crs_spheroid"]["semi_major_axis_m"] == 1737400.0
    assert entry["pixel_size_m"] == {"x": PIXEL, "y": PIXEL}
    assert entry["extent_m"] == {
        "left": LEFT,
        "right": LEFT + WIDTH * PIXEL,
        "top": TOP,
        "bottom": TOP - HEIGHT * PIXEL,
    }
    assert (entry["width"], entry["height"], entry["dtype"]) == (WIDTH, HEIGHT, "float32")
    assert entry["nodata"] == "NaN"
    assert entry["area_or_point"] == "Area"


def test_frame_is_quoted_from_the_provider_and_not_assumed(recorded: Path) -> None:
    record = pilot_json(recorded)
    assert record["frame_stated_by_provider"]["text"] == (
        "MOON_ME reference frame of ephemeris DE421"
    )
    assert record["frame_stated_by_provider"]["source"] == README_URL


def test_units_come_from_the_readme_never_guessed(recorded: Path) -> None:
    record = pilot_json(recorded)
    assert file_entry(record, SURF)["units"] == "meters"
    assert file_entry(record, SLP)["units"] == "degrees"
    assert file_entry(record, TOTERR)["units"] == "meters"
    assert file_entry(record, SLPERR)["units"] == "degrees"
    assert file_entry(record, LDEC)["units"] == "number of LOLA returns in each pixel"
    assert file_entry(record, SURF)["units_in_file"] is None


def test_units_missing_from_the_readme_are_unknown(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    readme = README.replace("Slope (degrees)", "Slope")
    do_fetch(root, MockDownloader(site(tmp_path, README=readme.encode())))
    pilot.record("pgda78", root=root, sources=sources_for())
    assert file_entry(pilot_json(root), SLP)["units"] == "unknown"
    by_id = {record.product_id: record for record in sources(root)}
    slope = by_id[f"PGDA78/Site01/{SLP}"]
    assert slope.units == "unknown"
    assert slope.verification_status == "PARTIAL"


def test_dem_valid_fraction_is_decoded_from_the_array(recorded: Path) -> None:
    valid = file_entry(pilot_json(recorded), SURF)["valid"]
    total = WIDTH * HEIGHT
    assert valid["total_pixels"] == total
    assert valid["valid_pixels"] == total - len(NAN_PIXELS)
    assert valid["valid_fraction"] == (total - len(NAN_PIXELS)) / total
    assert valid["invalid_by_reason"]["nodata"] == len(NAN_PIXELS)
    assert pilot_json(recorded)["dem_valid_fraction"] == valid["valid_fraction"]


def test_effective_support_comes_from_the_count_map(recorded: Path) -> None:
    support = pilot_json(recorded)["effective_support"]
    dem, count = heights(), counts()
    dem_valid = ~np.isnan(dem)
    count_valid = ~np.isnan(count)
    observed = int(np.sum(dem_valid & count_valid & (count >= 1)))
    interpolated = int(np.sum(dem_valid & count_valid & (count == 0)))
    unknown = int(np.sum(dem_valid & ~count_valid))
    assert support["count_file"] == LDEC
    assert support["dem_valid_pixels"] == int(dem_valid.sum())
    assert support["observed_pixels"] == observed
    assert support["interpolated_pixels"] == interpolated
    assert support["count_invalid_pixels"] == unknown == len(COUNT_NAN_PIXELS)
    assert support["observed_fraction"] == observed / int(dem_valid.sum())
    assert support["interpolated_fraction"] == interpolated / int(dem_valid.sum())
    histogram = {int(k): v for k, v in support["count_histogram"].items()}
    assert sum(histogram.values()) == observed + interpolated
    assert histogram[5] == 1
    assert support["grid_matches_height"] is True


def test_a_count_map_on_another_grid_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    other = geotiff_bytes(np.zeros((HEIGHT, WIDTH - 1), np.float32), width=WIDTH - 1, tmp=tmp_path)
    do_fetch(root, MockDownloader(site(tmp_path, **{LDEC: other})))
    with pytest.raises(pilot.PilotError, match="grid"):
        pilot.record("pgda78", root=root, sources=sources_for())


def test_a_non_integral_count_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    bad = counts()
    bad[2, 2] = 0.5
    do_fetch(root, MockDownloader(site(tmp_path, **{LDEC: geotiff_bytes(bad, tmp=tmp_path)})))
    with pytest.raises(pilot.PilotError, match="count"):
        pilot.record("pgda78", root=root, sources=sources_for())


def test_roi_shapefiles_are_listed_and_drawn(recorded: Path) -> None:
    roi = pilot_json(recorded)["roi"]
    assert roi["status"] == "read"
    names = {member["name"]: member for member in roi["members"]}
    assert set(names) == {
        "Site01_ROIs.dbf",
        "Site01_ROIs.gmt",
        "Site01_ROIs.prj",
        "Site01_ROIs.shp",
        "Site01_ROIs.shx",
    }
    assert names["Site01_ROIs.gmt"]["type"] == "symlink"
    assert names["Site01_ROIs.gmt"]["link_target"] == "Site01_regions.gmt"
    (layer,) = roi["shapefiles"]
    assert layer["name"] == "Site01_ROIs"
    assert layer["readable"] is True
    assert layer["read_error"] is None
    assert layer["read_warnings"] == []
    assert layer["shape_type"] == "POLYGON"
    assert layer["shape_count"] == len(ROI_SQUARES)
    assert layer["crs_matches_height"] is True
    assert "resolve to the same PROJ parameters" in layer["crs_comparison"]
    assert layer["inside_height_extent"] is True
    assert [shape["bbox_m"] for shape in layer["shapes"]] == [list(s) for s in ROI_SQUARES]
    assert roi["outlines_drawn"] is True
    assert roi["outlines_not_drawn_reason"] is None


def test_roi_with_a_different_crs_is_not_drawn_and_says_why(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    archive = roi_archive(prj_radius="1738100.0")
    do_fetch(root, MockDownloader(site(tmp_path, **{ROIS: archive})))
    pilot.record("pgda78", root=root, sources=sources_for())
    roi = pilot_json(root)["roi"]
    assert roi["shapefiles"][0]["crs_matches_height"] is False
    assert roi["outlines_drawn"] is False
    assert "CRS" in roi["outlines_not_drawn_reason"]
    preview = Image.open(root / "outputs/pilot/pgda78_site01_preview.png")
    assert not np.any(np.asarray(preview) == pilot.ROI_INDEX)


def test_roi_without_a_prj_is_not_drawn(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    do_fetch(root, MockDownloader(site(tmp_path, **{ROIS: roi_archive(with_prj=False)})))
    pilot.record("pgda78", root=root, sources=sources_for())
    roi = pilot_json(root)["roi"]
    assert roi["outlines_drawn"] is False
    assert ".prj" in roi["outlines_not_drawn_reason"]


def record_with_rois(tmp_path: Path, archive: bytes) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    do_fetch(root, MockDownloader(site(tmp_path, **{ROIS: archive})))
    pilot.record("pgda78", root=root, sources=sources_for())
    return root


def one_record_dbf() -> bytes:
    shp, shx, dbf = stdio.BytesIO(), stdio.BytesIO(), stdio.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as writer:
        writer.field("X", "N", 24, 15)
        writer.poly([[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0]]])
        writer.record(1.0)
    return dbf.getvalue()


def assert_no_outlines_but_a_complete_record(root: Path) -> None:
    preview = Image.open(root / "outputs/pilot/pgda78_site01_preview.png")
    assert not np.any(np.asarray(preview) == pilot.ROI_INDEX)
    by_id = {record.product_id: record for record in sources(root)}
    assert by_id[f"PGDA78/Site01/{SURF}"].verification_status == "VERIFIED"
    assert pilot_json(root)["effective_support"]["observed_pixels"] > 0
    assert pilot.check("pgda78", root=root, sources=sources_for()) == []


@pytest.mark.parametrize(
    ("members", "warning"),
    [
        ({"Site01_ROIs.shp": b"garbage" * 30}, None),
        ("truncated", "Declared file size"),
        ({"Site01_ROIs.dbf": one_record_dbf()}, None),
    ],
    ids=["corrupt-shp", "truncated-shp", "dbf-with-fewer-records"],
)
def test_an_unreadable_shapefile_is_recorded_as_missing_with_the_reason(
    tmp_path: Path, members: dict[str, bytes] | str, warning: str | None
) -> None:
    if members == "truncated":
        with tarfile.open(fileobj=stdio.BytesIO(roi_archive()), mode="r:gz") as archive:
            stream = archive.extractfile("Site01_ROIs.shp")
            assert stream is not None
            members = {"Site01_ROIs.shp": stream.read()[:120]}
    assert isinstance(members, dict)
    root = record_with_rois(tmp_path, roi_archive(replace=members))
    roi = pilot_json(root)["roi"]
    assert roi["status"] == "read"
    (layer,) = roi["shapefiles"]
    assert layer["name"] == "Site01_ROIs"
    assert layer["readable"] is False
    assert "Site01_ROIs.shp cannot be read" in layer["read_error"]
    assert layer["shapes"] == []
    assert layer["shape_count"] is None
    if warning is not None:
        assert any(warning in text for text in layer["read_warnings"])
    assert roi["outlines_drawn"] is False
    assert "Site01_ROIs" in roi["outlines_not_drawn_reason"]
    assert "cannot be read" in roi["outlines_not_drawn_reason"]
    by_id = {record.product_id: record for record in sources(root)}
    assert by_id[f"PGDA78/Site01/{ROIS}"].verification_status == "PARTIAL"
    basis = pilot_json(root)["verification"][f"PGDA78/Site01/{ROIS}"]["basis"]
    assert "not every shapefile could be read" in basis
    assert_no_outlines_but_a_complete_record(root)


def test_a_prj_that_is_not_utf8_is_not_drawn_and_says_why(tmp_path: Path) -> None:
    archive = roi_archive(replace={"Site01_ROIs.prj": b"\xff\xfePROJCS"})
    root = record_with_rois(tmp_path, archive)
    roi = pilot_json(root)["roi"]
    (layer,) = roi["shapefiles"]
    assert layer["readable"] is True
    assert layer["prj_wkt"] is None
    assert layer["crs_matches_height"] is None
    assert "UTF-8" in layer["crs_comparison"]
    assert roi["outlines_drawn"] is False
    assert ".prj" in roi["outlines_not_drawn_reason"]
    assert "UTF-8" in roi["outlines_not_drawn_reason"]
    assert_no_outlines_but_a_complete_record(root)


@pytest.mark.parametrize(
    ("archive", "reason"),
    [
        (gzip.compress(b"not a tar archive"), "is not a readable gzip tar archive"),
        (b"\x1f\x8b\x08\x00truncated", "is not a readable gzip tar archive"),
        ("oversized", "unpacks to more than 100 bytes"),
    ],
    ids=["gzip-but-not-tar", "truncated-gzip", "over-the-unpack-limit"],
)
def test_an_unreadable_roi_archive_is_recorded_as_missing_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive: bytes | str, reason: str
) -> None:
    if archive == "oversized":
        monkeypatch.setattr(pilot, "_ROI_ARCHIVE_MAX_BYTES", 100)
        archive = roi_archive()
    assert isinstance(archive, bytes)
    root = record_with_rois(tmp_path, archive)
    roi = pilot_json(root)["roi"]
    assert roi["status"] == "unreadable"
    assert reason in roi["reason"]
    assert ROIS in roi["reason"]
    assert roi["members"] == []
    assert roi["shapefiles"] == []
    assert roi["outlines_drawn"] is False
    assert roi["outlines_not_drawn_reason"] == roi["reason"]
    assert roi["archive"]["sha256"] == hashlib.sha256(archive).hexdigest()
    by_id = {record.product_id: record for record in sources(root)}
    roi_record = by_id[f"PGDA78/Site01/{ROIS}"]
    assert roi_record.verification_status == "FAILED"
    assert roi_record.units == "unknown"
    assert roi_record.crs_and_frame == "unknown"
    assert roi_record.checksum == hashlib.sha256(archive).hexdigest()
    basis = pilot_json(root)["verification"][f"PGDA78/Site01/{ROIS}"]["basis"]
    assert reason in basis
    assert_no_outlines_but_a_complete_record(root)


def test_roi_not_listed_by_the_provider_is_recorded_as_missing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    downloader = MockDownloader(site(tmp_path, PRODUCT=PRODUCT_PAGE_WITHOUT_ROIS.encode()))
    do_fetch(root, downloader)
    assert SITE_URL + ROIS not in [call["url"] for call in downloader.calls]
    pilot.record("pgda78", root=root, sources=sources_for())
    roi = pilot_json(root)["roi"]
    assert roi["status"] == "not_listed"
    assert roi["outlines_drawn"] is False
    assert roi["outlines_not_drawn_reason"]
    assert f"PGDA78/Site01/{ROIS}" not in {r.product_id for r in sources(root)}


def test_preview_is_rendered_from_the_decoded_dem(recorded: Path) -> None:
    path = recorded / "outputs/pilot/pgda78_site01_preview.png"
    data = path.read_bytes()
    assert len(data) <= pilot.PREVIEW_MAX_BYTES == 200_000
    preview = pilot_json(recorded)["preview"]
    assert preview["path"] == "outputs/pilot/pgda78_site01_preview.png"
    assert preview["bytes"] == len(data)
    assert preview["sha256"] == hashlib.sha256(data).hexdigest()
    image = Image.open(path)
    assert image.format == "PNG"
    assert image.mode == "P"
    step = preview["step"]
    assert image.size == (preview["width"], preview["height"])
    assert image.size == (len(range(0, WIDTH, step)), len(range(0, HEIGHT, step)))
    pixels = np.asarray(image)
    sampled = heights()[::step, ::step]
    invalid = np.isnan(sampled)
    roi = pixels == pilot.ROI_INDEX
    assert np.all(pixels[invalid & ~roi] == pilot.INVALID_INDEX)
    valid = ~invalid & ~roi
    lowest, highest = np.nanmin(heights()), np.nanmax(heights())
    assert preview["grey_scale"] == {
        "black_m": float(lowest),
        "white_m": float(highest),
        "levels": pilot.GREY_LEVELS,
    }
    expected = pilot.grey_index(sampled, float(lowest), float(highest))
    assert np.array_equal(pixels[valid], expected[valid])
    assert roi.any()


def test_grey_index_maps_the_range_to_the_grey_levels() -> None:
    values = np.array([0.0, 50.0, 100.0], dtype=np.float32)
    assert pilot.grey_index(values, 0.0, 100.0).tolist() == [0, 126, pilot.GREY_LEVELS - 1]
    assert pilot.grey_index(np.array([7.0]), 7.0, 7.0).tolist() == [0]


def test_record_lists_tool_versions_and_copies_the_retrieval_date(recorded: Path) -> None:
    record = pilot_json(recorded)
    versions = record["tool_versions"]
    assert {"python", "gdal", "numpy", "pillow", "pydantic", "pyshp", "rasterio"} <= set(versions)
    assert versions["rasterio"] == rasterio.__version__
    assert record["retrieval"]["first_retrieved_at"] == "2026-11-14T19:30:05Z"
    assert record["retrieval"]["last_retrieved_at"] == "2026-11-14T19:30:05Z"


def test_record_is_deterministic_byte_for_byte(recorded: Path) -> None:
    first = snapshot(recorded / "outputs")
    pilot.record("pgda78", root=recorded, sources=sources_for())
    second = snapshot(recorded / "outputs")
    assert {k: v[0] for k, v in first.items()} == {k: v[0] for k, v in second.items()}


def test_record_refuses_raw_files_that_differ_from_the_fetch(fetched: Path) -> None:
    path = fetched / "data/raw/pgda78" / SLP
    path.write_bytes(path.read_bytes()[:-1] + b"\x00")
    with pytest.raises(pilot.PilotError, match=re.escape("fetch.json")):
        pilot.record("pgda78", root=fetched, sources=sources_for())


def test_a_malformed_fetch_metadata_is_a_clear_error(fetched: Path) -> None:
    path = fetched / "data/raw/pgda78/fetch.json"
    metadata = json.loads(path.read_bytes())
    metadata["files"] = [item for item in metadata["files"] if item["role"] != "slope"]
    path.write_text(json.dumps(metadata))
    with pytest.raises(
        pilot.PilotError, match=re.escape("run: python -m terraluna_pipeline.pilot fetch")
    ):
        pilot.record("pgda78", root=fetched, sources=sources_for())


@pytest.mark.parametrize("content", [b"[]", b'{"files": [{}]}', b"not json"])
def test_a_fetch_metadata_of_the_wrong_shape_is_a_clear_error(
    fetched: Path, content: bytes
) -> None:
    (fetched / "data/raw/pgda78/fetch.json").write_bytes(content)
    with pytest.raises(pilot.PilotError, match=re.escape("fetch pgda78")):
        pilot.check("pgda78", root=fetched, sources=sources_for())


# --- sources.json -------------------------------------------------------------------------------


def test_sources_holds_a_validated_record_per_product(recorded: Path) -> None:
    records = sources(recorded)
    ids = [record.product_id for record in records]
    assert ids == sorted(ids)
    assert ids == sorted(f"PGDA78/Site01/{name}" for name in (*GEOTIFFS, ROIS))
    for record in records:
        name = record.product_id.rsplit("/", 1)[1]
        data = (recorded / "data/raw/pgda78" / name).read_bytes()
        assert record.local_path == f"data/raw/pgda78/{name}"
        assert record.checksum == hashlib.sha256(data).hexdigest()
        assert record.source_url == SITE_URL + name
        assert record.body == "moon"
    by_id = {record.product_id: record for record in records}
    assert by_id[f"PGDA78/Site01/{SURF}"].verification_status == "VERIFIED"
    assert by_id[f"PGDA78/Site01/{SURF}"].units == "meters"
    for name in (LDEC, SLP, TOTERR, SLPERR, ROIS):
        assert by_id[f"PGDA78/Site01/{name}"].verification_status == "PARTIAL"
    with rasterio.open(recorded / "data/raw/pgda78" / SURF) as src:
        assert by_id[f"PGDA78/Site01/{SURF}"].crs_and_frame == src.crs.to_wkt()


def test_sources_upsert_keeps_other_products_and_replaces_this_sources(recorded: Path) -> None:
    path = recorded / "outputs/sources.json"
    records = json.loads(path.read_bytes())
    other = dict(records[0], product_id="AAA/other", source_url=None, local_path=None)
    other["checksum"] = None
    stale = dict(records[0], product_id="PGDA78/Site01/Site01_old.tif")
    changed = dict(records[1], version="edited by hand")
    path.write_text(json.dumps([changed, stale, other, *records[2:]]))
    pilot.record("pgda78", root=recorded, sources=sources_for())
    ids = [record.product_id for record in sources(recorded)]
    assert ids == sorted(ids)
    assert "AAA/other" in ids
    assert "PGDA78/Site01/Site01_old.tif" not in ids
    assert all(r.version != "edited by hand" for r in sources(recorded))
    first = path.read_bytes()
    pilot.record("pgda78", root=recorded, sources=sources_for())
    assert path.read_bytes() == first


# --- check --------------------------------------------------------------------------------------


def test_check_passes_after_record_and_writes_nothing(recorded: Path) -> None:
    before = snapshot(recorded)
    assert pilot.check("pgda78", root=recorded, sources=sources_for()) == []
    assert pilot.main(["check", "pgda78"], root=recorded, sources=sources_for()) == 0
    assert snapshot(recorded) == before


@pytest.mark.parametrize(
    "relative",
    ["outputs/pilot/pgda78_site01.json", "outputs/pilot/pgda78_site01_preview.png"],
)
def test_check_fails_when_a_committed_output_differs(recorded: Path, relative: str) -> None:
    path = recorded / relative
    path.write_bytes(path.read_bytes() + b"\n")
    before = snapshot(recorded)
    differences = pilot.check("pgda78", root=recorded, sources=sources_for())
    assert any(relative in difference for difference in differences)
    assert pilot.main(["check", "pgda78"], root=recorded, sources=sources_for()) != 0
    assert snapshot(recorded) == before


def test_check_fails_when_sources_lacks_a_record(recorded: Path) -> None:
    path = recorded / "outputs/sources.json"
    path.write_text(json.dumps(json.loads(path.read_bytes())[1:], indent=2) + "\n")
    differences = pilot.check("pgda78", root=recorded, sources=sources_for())
    assert any("outputs/sources.json" in difference for difference in differences)


def test_check_fails_when_outputs_are_missing(fetched: Path) -> None:
    differences = pilot.check("pgda78", root=fetched, sources=sources_for())
    assert any("pgda78_site01.json" in difference for difference in differences)
    assert not (fetched / "outputs").exists()


@pytest.mark.parametrize("command", ["record", "check"])
def test_missing_raw_data_names_the_fetch_command(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = pilot.main([command, "pgda78"], root=tmp_path, sources=sources_for())
    assert code != 0
    assert "run: python -m terraluna_pipeline.pilot fetch pgda78 first" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_an_incomplete_fetch_counts_as_missing(fetched: Path) -> None:
    (fetched / "data/raw/pgda78/fetch.json").unlink()
    with pytest.raises(pilot.MissingRawDataError, match="fetch pgda78 first"):
        pilot.check("pgda78", root=fetched, sources=sources_for())


def test_record_and_check_run_offline(fetched: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The autouse fixture already refuses sockets; here any urllib call fails loudly too."""
    import urllib.request

    calls: list[str] = []

    def no_urlopen(*args: Any, **kwargs: Any) -> Any:
        calls.append("urlopen")
        raise AssertionError("record and check must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_urlopen)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", no_urlopen)
    pilot.record("pgda78", root=fetched, sources=sources_for())
    assert pilot.check("pgda78", root=fetched, sources=sources_for()) == []
    assert calls == []


def test_the_default_root_is_the_working_directory(
    fetched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(fetched)
    assert pilot.main(["record", "pgda78"], sources=sources_for()) == 0
    assert pilot.main(["check", "pgda78"], sources=sources_for()) == 0


def test_an_unknown_source_is_refused() -> None:
    with pytest.raises(SystemExit):
        pilot.main(["check", "nosuchsource"])


def test_the_registered_pgda78_source_reads_pgda() -> None:
    source = pilot.SOURCES["pgda78"]
    assert isinstance(source, pilot.Pgda78Source)
    assert source.base_url == "https://pgda.gsfc.nasa.gov/"
