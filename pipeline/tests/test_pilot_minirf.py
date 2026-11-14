"""Tests for the minirf pilot source (T1-04; PRD 5 rows 1 and 2, 6.2, 9 G0, 15).

Everything is synthetic and offline. A mocked download layer serves provider pages, catalog
files and detached PDS3 labels shaped like the PDS Geosciences Node's LROMRF_0005 volume. A
mocked range fetcher serves a virtual 10346 x 10346 float32 mosaic: only the requested bytes
are generated, so the layout matches the real label while no test holds 428 MB. An autouse
fixture refuses every socket.
"""

from __future__ import annotations

import hashlib
import json
import math
import socket
import struct
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from pydantic import TypeAdapter

from terraluna_pipeline import pilot
from terraluna_pipeline.io import (
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
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


# --- A synthetic LROMRF_0005 volume -------------------------------------------------------------

HOST = "https://pds.example.test"
LANDING = HOST + "/missions/lro/mrf.htm"
VOLUME_PATH = "/lro/lro-l-mrflro-4-cdr-v1/lromrf_0005/"
VOLUME = HOST + VOLUME_PATH
DATA = VOLUME + "data/"
SAR = DATA + "sar/"
MOSAICS = SAR + "mosaics/"
CATALOG = VOLUME + "catalog/"
ERRATA = VOLUME + "errata.txt"
DSMAP = CATALOG + "dsmap.cat"
MOSAIC_CATALOG = CATALOG + "mrflro_5_cdr_mosaic_v1_0_ds.cat"
INSTRUMENT_CATALOG = CATALOG + "mrf_inst.cat"

CPR = "lsz_xxxxx_3cp_piu_90s000_v1"
S1 = "lsz_xxxxx_3s1_piu_90s000_v1"
S4 = "lsz_xxxxx_3s4_piu_90s000_v1"
S4_NORTH = "lsz_xxxxx_3s4_piu_90n000_v1"
CPR_NORTH = "lsz_xxxxx_3cp_piu_90n000_v1"
CPR_EAST = "lsz_04790_3cp_pju_90s000_e_v1"
S1_EAST = "lsz_04790_3s1_pju_90s000_e_v1"
S4_EAST = "lsz_04790_3s4_pju_90s000_e_v1"
CPR_IMG_URL = MOSAICS + CPR + ".img"
CPR_LBL_URL = MOSAICS + CPR + ".lbl"
S1_IMG_URL = MOSAICS + S1 + ".img"
S4_IMG_URL = MOSAICS + S4 + ".img"
S1_EAST_IMG_URL = MOSAICS + S1_EAST + ".img"
S4_EAST_IMG_URL = MOSAICS + S4_EAST + ".img"

LINES = 10346
SCALE_KM = 0.11845058759433
OFFSET = 5173.5000000002
RECORD_BYTES = LINES * 4
FILE_BYTES = LINES * RECORD_BYTES
EAST_LINES = 20691
EAST_SCALE_KM = 0.059225293797168
EAST_OFFSET = 10346.0
EAST_RECORD_BYTES = EAST_LINES * 4

ISIS_NULL = struct.unpack("<f", struct.pack("<I", 0xFF7FFFFB))[0]
ISIS_LRS = struct.unpack("<f", struct.pack("<I", 0xFF7FFFFC))[0]
ISIS_HRS = struct.unpack("<f", struct.pack("<I", 0xFF7FFFFF))[0]

SITE_EXTENT = {"left": -19000.0, "bottom": -20000.0, "right": -3000.0, "top": -4000.0}
PGDA_WKT = (
    'PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",SPHEROID["unnamed",1737400,0]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Polar_Stereographic"],PARAMETER["latitude_of_origin",-90],'
    'PARAMETER["central_meridian",0],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],UNIT["metre",1],AXIS["Easting",NORTH],AXIS["Northing",NORTH]]'
)
SITE_RECORD = "outputs/pilot/pgda78_site01.json"


def _window_by_formula(
    scale_km: float = SCALE_KM, offset: float = OFFSET
) -> tuple[int, int, int, int]:
    """The covering pixel window by DSMAP.CAT: x = (Sample - S0) * Scale, y = (L0 - Line) * Scale.

    Sample and Line are 1-based and integral at pixel centres, so the 0-based edge coordinate
    of Sample is Sample - 0.5.
    """
    scale = scale_km * 1000.0

    def u(x: float) -> float:
        return x / scale + offset - 0.5

    def v(y: float) -> float:
        return -y / scale + offset - 0.5

    col_off = math.floor(u(SITE_EXTENT["left"]))
    col_end = math.ceil(u(SITE_EXTENT["right"]))
    row_off = math.floor(v(SITE_EXTENT["top"]))
    row_end = math.ceil(v(SITE_EXTENT["bottom"]))
    return col_off, row_off, col_end - col_off, row_end - row_off


COL_OFF, ROW_OFF, WIN_W, WIN_H = 5012, 5206, 136, 136

SPECIAL_PIXELS: dict[tuple[int, int], float] = {
    (ROW_OFF, COL_OFF): ISIS_NULL,
    (5300, 5100): ISIS_NULL,
    (5250, 5050): ISIS_HRS,
    (5251, 5051): ISIS_LRS,
    (5260, 5060): 0.0,
    (5261, 5061): 0.0,
    (5262, 5062): -0.25,
    (5270, 5070): 2.5,
    # In a fetched row but outside the window's columns: it must not be counted.
    (5210, 100): ISIS_NULL,
}


# Synthetic S1 and S4 layers. S4 takes positive, negative and zero values; each layer has its
# own special pixels, and one null is shared, so a pixel counts only where both are valid.
S1_SPECIAL_PIXELS: dict[tuple[int, int], float] = {
    (5220, 5020): ISIS_NULL,
    (5221, 5021): ISIS_HRS,
    (5210, 100): ISIS_NULL,
}
S4_SPECIAL_PIXELS: dict[tuple[int, int], float] = {
    (5220, 5020): ISIS_NULL,
    (5230, 5030): ISIS_NULL,
    (5240, 5040): ISIS_LRS,
}


def mosaic_rows(
    first_row: int, row_count: int, quantity: str = "CPR", lines: int = LINES
) -> npt.NDArray[np.float32]:
    """Rows of a virtual CPR, S1 or S4 layer of ``lines`` x ``lines`` pixels, as float32."""
    rows, cols = np.mgrid[first_row : first_row + row_count, 0:lines]
    if quantity == "CPR":
        stored = 0.05 + ((rows * 7 + cols * 13) % 97) / 100.0
        special = SPECIAL_PIXELS
    elif quantity == "S1":
        stored = 0.5 + ((rows * 3 + cols * 5) % 89) / 50.0
        special = S1_SPECIAL_PIXELS
    else:
        stored = ((rows * 11 + cols * 3) % 41 - 20) / 40.0
        special = S4_SPECIAL_PIXELS
    values: npt.NDArray[np.float32] = stored.astype(np.float32)
    for (row, col), value in special.items():
        if first_row <= row < first_row + row_count and col < lines:
            values[row - first_row, col] = np.float32(value)
    return values


class VirtualMosaic:
    """The bytes of a virtual mosaic image file, generated per requested range."""

    def __init__(self, quantity: str = "CPR", lines: int = LINES) -> None:
        self.quantity = quantity
        self.lines = lines
        self.size = lines * lines * 4

    def read(self, first: int, last: int) -> bytes:
        record_bytes = self.lines * 4
        first_row, last_row = first // record_bytes, last // record_bytes
        block = (
            mosaic_rows(first_row, last_row - first_row + 1, self.quantity, self.lines)
            .astype("<f4")
            .tobytes()
        )
        start = first - first_row * record_bytes
        return block[start : start + last - first + 1]


IMAGES: dict[str, VirtualMosaic] = {
    CPR_IMG_URL: VirtualMosaic("CPR"),
    S1_IMG_URL: VirtualMosaic("S1"),
    S4_IMG_URL: VirtualMosaic("S4"),
    S1_EAST_IMG_URL: VirtualMosaic("S1", EAST_LINES),
    S4_EAST_IMG_URL: VirtualMosaic("S4", EAST_LINES),
}
"""The images the range fetcher serves, by URL."""


def pds_label(
    product: str,
    *,
    note: str,
    description: str,
    lines: int = LINES,
    resolution: str = "256.0",
    scale_km: str = "0.11845058759433",
    offset: str = "5173.5000000002",
    center_latitude: str = "-90.0",
    look: str | None = None,
    sample_type: str = "PC_REAL",
) -> bytes:
    """A detached PDS3 label shaped like the LROMRF_0005 mosaic labels, with CRLF endings."""
    look_line = f'LOOK_DIRECTION\t\t      =  "{look}"\n' if look else ""
    text = f"""PDS_VERSION_ID                =  PDS3
LABEL_REVISION_NOTE           =  "2012-JHU/APL Mini-RF LRO Ver. 1"
NOTE                          =  "{note}"
^IMAGE                        =  "{product.upper()}.IMG"
RECORD_TYPE                   =  FIXED_LENGTH
RECORD_BYTES                  =  {lines * 4}
FILE_RECORDS                  =  {lines}
DATA_SET_ID                   =  "LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0"
DATA_SET_NAME                 =  "LRO MOON MINI-RF 5 POLAR MOSAIC
CALIBRATED DATA REC V1.0"
PRODUCER_ID                   =  "JHUAPL"
PRODUCER_FULL_NAME            =  "MINI-RF POC TEAM"
PRODUCER_INSTITUTION_NAME     =  "JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS
LABORATORY"
PRODUCT_ID                    =  "{product.upper()}"
PRODUCT_VERSION_ID            =  "1.0"
PRODUCT_CREATION_TIME         =  2012-05-08
START_TIME                    =  2009-07-19T12:27:42.785399
STOP_TIME                     =  2011-01-11T01:32:11.7200
CENTER_FREQUENCY              =  2379.99968158 <GHz>
INSTRUMENT_MODE_ID            =  "ZOOM_S"
SOFTWARE_NAME                 =  "ISIS 3"
{look_line}
DESCRIPTION                   =  "{description}"

OBJECT = IMAGE
  LINES                      = {lines}
  LINE_SAMPLES               = {lines}
  BANDS                      = 1
  BAND_STORAGE_TYPE          = BAND_SEQUENTIAL
  OFFSET                     = 0.0
  SCALING_FACTOR             = 1.0
  SAMPLE_BITS                = 32
  SAMPLE_BIT_MASK            = 2#11111111111111111111111111111111#
  SAMPLE_TYPE                = {sample_type}
  CORE_NULL                  = 16#FF7FFFFB#
  CORE_LOW_REPR_SATURATION   = 16#FF7FFFFC#
  CORE_LOW_INSTR_SATURATION  = 16#FF7FFFFD#
  CORE_HIGH_REPR_SATURATION  = 16#FF7FFFFF#
  CORE_HIGH_INSTR_SATURATION = 16#FF7FFFFE#
END_OBJECT = IMAGE

OBJECT = IMAGE_MAP_PROJECTION
  ^DATA_SET_MAP_PROJECTION     = "DSMAP.CAT"
  COORDINATE_SYSTEM_TYPE       = "BODY-FIXED ROTATING"
  MAP_PROJECTION_TYPE          = "POLAR STEREOGRAPHIC"
  A_AXIS_RADIUS                = 1737.4 <km>
  B_AXIS_RADIUS                = 1737.4 <km>
  C_AXIS_RADIUS                = 1737.4 <km>
  COORDINATE_SYSTEM_NAME       = PLANETOCENTRIC
  POSITIVE_LONGITUDE_DIRECTION = EAST
  CENTER_LATITUDE              = {center_latitude} <deg>
  CENTER_LONGITUDE             = 0.0 <deg>
  MAP_PROJECTION_ROTATION      = 0.0 <deg>
  MAP_RESOLUTION               = {resolution} <pix/deg>
  MAP_SCALE                    = {scale_km} <km/pix>
  MAXIMUM_LATITUDE             = -70.0 <deg>
  MINIMUM_LATITUDE             = -90.0 <deg>
  LINE_PROJECTION_OFFSET       = {offset} <pixel>
  SAMPLE_PROJECTION_OFFSET     = {offset} <pixel>
END_OBJECT = IMAGE_MAP_PROJECTION
END
"""
    return text.replace("\n", "\r\n").encode("ascii")


CPR_DESCRIPTION = """The Circular Polarization Ratio product,
defined as the ratio of the same sense to the opposite sense circularly
polarized (CPR = SC / OS) radar returns. These Level 3 products are Derived
Data Records (DDRs) generated from multiple Level 1 RDRs. This is a combined East
and West-looking mosaic."""
S1_DESCRIPTION = """The First Stokes parameter product, defined
as S1 = |ERH| + |ERV|. This is a combined East and West-looking mosaic."""


S4_DESCRIPTION = """The Fourth Stokes parameter product. This is a combined East and
West-looking mosaic."""


def east_label(product: str, note: str, description: str) -> bytes:
    """A label on the East-looking 512 pixel per degree grid."""
    return pds_label(
        product,
        note=note,
        description=description,
        lines=EAST_LINES,
        resolution="512.0",
        scale_km="0.059225293797168",
        offset="10346.0",
        look="EAST",
    )


def labels(*, with_s4: bool = False) -> dict[str, bytes]:
    result = {
        CPR: pds_label(
            CPR,
            note="Circular Polarization Ratio South polar\nmosaic.",
            description=CPR_DESCRIPTION,
        ),
        S1: pds_label(
            S1, note="First stokes parameter South polar mosaic.", description=S1_DESCRIPTION
        ),
        CPR_NORTH: pds_label(
            CPR_NORTH,
            note="Circular Polarization Ratio North polar\nmosaic.",
            description=CPR_DESCRIPTION,
            center_latitude="90.0",
        ),
        CPR_EAST: pds_label(
            CPR_EAST,
            note="Circular Polarization Ratio South polar\nmosaic.",
            description="The Circular Polarization Ratio product (CPR = SC / OS).",
            lines=20691,
            resolution="512.0",
            scale_km="0.059225293797168",
            offset="10346.0",
            look="EAST",
        ),
    }
    if with_s4:
        result[S4] = pds_label(
            S4, note="Fourth stokes parameter South polar mosaic.", description=S4_DESCRIPTION
        )
    return result


def listing(url: str, children: Sequence[str]) -> bytes:
    """An IIS-style directory listing with absolute-path HREFs, as the PDS server sends."""
    path = url[len(HOST) :]
    parent = path.rstrip("/").rsplit("/", 1)[0] + "/"
    links = [f'<A HREF="{parent}">[To Parent Directory]</A><br>']
    links += [f'<A HREF="{path}{child}">{child}</A><br>' for child in children]
    return ("<html><body><pre>" + "\n".join(links) + "</pre></body></html>").encode()


LANDING_HTML = f"""<html><body>
<a HREF="https://pds.example.test/lro/lro-l-mrflro-4-cdr-v1/lromrf_0001/">LROMRF_0001</a>
<a href="https://pds.example.test/lro/lro-l-mrflro-4-cdr-v1/lromrf_0005">CDR-MOSAIC</a>
<a href="{MOSAIC_CATALOG}">CDR Level 3 Mosaics Data Set Description</a>
</body></html>""".encode()

DSMAP_TEXT = b"""PDS_VERSION_ID                = PDS3
OBJECT                        = DATA_SET_MAP_PROJECTION
  DATA_SET_ID                 = "LRO-L-MRFLRO-5-CDR-MAP-V1.0"
  OBJECT                      = DATA_SET_MAP_PROJECTION_INFO
    MAP_PROJECTION_TYPE       = "POLAR STEREOGRAPHIC"
    MAP_PROJECTION_DESC       = "
        Integral values of line and sample
        correspond to the center of a pixel.
       x = (Sample - S0) * Scale
       y = (L0 - Line) * Scale
    "
  END_OBJECT                  = DATA_SET_MAP_PROJECTION_INFO
END_OBJECT                    = DATA_SET_MAP_PROJECTION
END
"""

MOSAIC_CATALOG_TEXT = b"""PDS_VERSION_ID                    = PDS3
OBJECT                            = DATA_SET
 DATA_SET_ID                      = "LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0"
 OBJECT                           = DATA_SET_INFORMATION
  CITATION_DESC                   = "LRO Mini-RF Calibrated
                                    Level 3 Data Set V1.0,
                                    LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0,
                                    NASA Planetary Data System, 2008."
  DATA_SET_DESC                   = "
    Coordinate System
    =================
      The mosaics are produced from Level 1 products which are oriented in
      reference to the Moon Principle Axis (PA) coordinate system, as
      realized using NASA's SPICE products (Frames Kernel)."
 END_OBJECT                       = DATA_SET_INFORMATION
END_OBJECT                        = DATA_SET
END
"""

INSTRUMENT_TEXT = b"""PDS_VERSION_ID                   = PDS3
OBJECT                           = INSTRUMENT
  INSTRUMENT_ID                  = MRFLRO
  OBJECT                         = INSTRUMENT_INFORMATION
    INSTRUMENT_DESC              = "
   Mini-RF operated in S-band (2.38 GHz) and X/C-band (7.14 GHz) imaging
   modes and as a communications demonstration."
  END_OBJECT                     = INSTRUMENT_INFORMATION
END_OBJECT                       = INSTRUMENT
END
"""

ERRATA_TEXT = b"""PDS_VERSION_ID      = PDS3
Calibration Issue
-----------------
The Mini-RF instrument transmits nominally left-hand circularly polarized
(LCP) signals and receives linear horizontally and vertically-polarized
signals. Post-launch calibration revealed more ellipticity.
"""


def volume(
    *, with_s4: bool = False, mosaic_labels: dict[str, bytes] | None = None
) -> dict[str, bytes]:
    """The synthetic server: URL -> body (the mosaic .IMG is served only by range)."""
    found = labels(with_s4=with_s4) if mosaic_labels is None else mosaic_labels
    mosaic_children = sorted([f"{name}.lbl" for name in found] + [f"{name}.img" for name in found])
    served = {
        LANDING: LANDING_HTML,
        VOLUME: listing(VOLUME, ["catalog/", "data/", "errata.txt", "voldesc.cat"]),
        DATA: listing(DATA, ["sar/"]),
        SAR: listing(SAR, ["06700_06799/", "mosaics/"]),
        MOSAICS: listing(MOSAICS, mosaic_children),
        CATALOG: listing(CATALOG, ["dsmap.cat", "mrf_inst.cat", "mrflro_5_cdr_mosaic_v1_0_ds.cat"]),
        DSMAP: DSMAP_TEXT,
        MOSAIC_CATALOG: MOSAIC_CATALOG_TEXT,
        INSTRUMENT_CATALOG: INSTRUMENT_TEXT,
        ERRATA: ERRATA_TEXT,
    }
    served.update({MOSAICS + f"{name}.lbl": data for name, data in found.items()})
    return served


class MockDownloader:
    """Stands in for io.fetch_file: serves bodies from a dict and records every call."""

    def __init__(self, served: dict[str, bytes], *, fail: dict[str, Exception] | None = None):
        self.served = served
        self.fail = fail or {}
        self.calls: list[str] = []

    def __call__(
        self, url: str, dest: Path, *, max_bytes: int, magic: Sequence[bytes] = ()
    ) -> FetchedFile:
        self.calls.append(url)
        if url in self.fail:
            raise self.fail[url]
        if url == CPR_IMG_URL:
            raise FileTooLargeError(f"{url}: {FILE_BYTES} bytes is over the limit")
        if url not in self.served:
            raise DownloadError(f"{url}: HTTP 404 Not Found")
        data = self.served[url]
        if magic and not any(data.startswith(prefix) for prefix in magic):
            raise DownloadError(f"{url}: the body does not start with the expected bytes")
        dest.write_bytes(data)
        return FetchedFile(
            url=url,
            final_url=url,
            status=200,
            content_type="text/plain",
            content_length=len(data),
            bytes_received=len(data),
            last_modified="Thu, 17 May 2012 22:32:56 GMT",
            etag='"synthetic"',
            sha256=hashlib.sha256(data).hexdigest(),
            path=dest,
        )


class MockRangeFetcher:
    """Stands in for io.fetch_range: serves the virtual images with HTTP 206 semantics.

    ``fail`` fails every request; ``fail_for`` fails the requests for the URLs it names.
    ``size`` overrides the file size the server states.
    """

    def __init__(
        self,
        *,
        fail: Exception | None = None,
        fail_for: dict[str, Exception] | None = None,
        size: int | None = None,
    ) -> None:
        self.fail = fail
        self.fail_for = fail_for or {}
        self.size = size
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, url: str, first: int, last: int) -> RangeResponse:
        self.calls.append((url, first, last))
        if self.fail is not None:
            raise self.fail
        if url in self.fail_for:
            raise self.fail_for[url]
        if url not in IMAGES:
            raise RangeReadError(f"{url}: HTTP 404 Not Found for bytes={first}-{last}")
        image = IMAGES[url]
        size = image.size if self.size is None else self.size
        end = min(last, size - 1)
        body = image.read(first, end)
        return RangeResponse(
            url=url,
            final_url=url,
            requested_first=first,
            requested_last=last,
            status=206,
            content_range=f"bytes {first}-{end}/{size}",
            first_byte=first,
            last_byte=end,
            total_size=size,
            bytes_received=len(body),
            data=body,
            content_type="application/octet-stream",
            last_modified="Thu, 17 May 2012 22:32:56 GMT",
            etag='"0dc3d17d34cd1:0"',
        )


FIXED = datetime(2026, 11, 14, 20, 40, 5, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED


SOURCE = pilot.MinirfSource(landing_url=LANDING)


def write_site_record(root: Path, extent: dict[str, float] | None = None) -> None:
    """The part of T1-03's committed outputs/pilot/pgda78_site01.json that T1-04 reads."""
    path = root / SITE_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "record": "pgda78_site01",
        "product": "PGDA78/Site01",
        "files": [
            {
                "role": "height",
                "file_name": "Site01_final_adj_5mpp_surf.tif",
                "crs_wkt": PGDA_WKT,
                "extent_m": extent or SITE_EXTENT,
            }
        ],
    }
    path.write_text(json.dumps(record))


def run(
    root: Path,
    *argv: str,
    served: dict[str, bytes] | None = None,
    download: MockDownloader | None = None,
    read_range: MockRangeFetcher | None = None,
) -> int:
    return pilot.main(
        list(argv),
        root=root,
        sources={"minirf": SOURCE},
        download=download or MockDownloader(served or volume()),
        read_range=read_range or MockRangeFetcher(),
        now=fixed_clock,
    )


@pytest.fixture
def fetched(tmp_path: Path) -> Path:
    """A repository root with the site record, after a successful fetch and record."""
    write_site_record(tmp_path)
    assert run(tmp_path, "fetch", "minirf") == 0
    assert run(tmp_path, "record", "minirf") == 0
    return tmp_path


def mosaic_json(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((root / "outputs/pilot/minirf_mosaic.json").read_bytes())
    return result


def sources(root: Path) -> list[ProductRecord]:
    return TypeAdapter(SourcesFile).validate_json((root / "outputs/sources.json").read_bytes())


RAW = "data/raw/minirf"

# --- fetch --------------------------------------------------------------------------------------


def test_the_window_by_formula_is_the_expected_one() -> None:
    assert _window_by_formula() == (COL_OFF, ROW_OFF, WIN_W, WIN_H)


def test_fetch_reads_only_the_labels_and_the_window_rows_by_one_range_request(
    tmp_path: Path,
) -> None:
    write_site_record(tmp_path)
    download, read_range = MockDownloader(volume()), MockRangeFetcher()
    assert run(tmp_path, "fetch", "minirf", download=download, read_range=read_range) == 0

    assert CPR_IMG_URL not in download.calls
    assert not any(url.endswith(".img") for url in download.calls)
    first, last = ROW_OFF * RECORD_BYTES, (ROW_OFF + WIN_H) * RECORD_BYTES - 1
    assert read_range.calls == [(CPR_IMG_URL, first, last)]
    for name in labels():
        assert MOSAICS + f"{name}.lbl" in download.calls

    raw = tmp_path / RAW
    rows = (raw / f"{CPR}.img.rows").read_bytes()
    assert rows == VirtualMosaic().read(first, last)
    metadata = json.loads((raw / "fetch.json").read_bytes())
    (entry,) = [e for e in metadata["files"] if e.get("read") == "rows"]
    assert entry["http_status"] == 206
    assert entry["content_range"] == f"bytes {first}-{last}/{FILE_BYTES}"
    assert entry["bytes_received"] == len(rows) == WIN_H * RECORD_BYTES
    assert entry["sha256"] == hashlib.sha256(rows).hexdigest()
    assert entry["retrieved_at"] == "2026-11-14T20:40:05Z"
    assert entry["remote_bytes"] == FILE_BYTES
    assert (raw / f"{CPR}.lbl").read_bytes() == labels()[CPR]


def test_fetch_stops_and_records_when_the_server_ignores_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_site_record(tmp_path)
    ignored = RangeNotSupportedError(
        f"{CPR_IMG_URL}: the server ignored the Range header and answered HTTP 200 with the "
        "whole file; the body was not read"
    )
    download = MockDownloader(volume())
    code = run(
        tmp_path, "fetch", "minirf", download=download, read_range=MockRangeFetcher(fail=ignored)
    )
    assert code != 0
    assert "Range" in capsys.readouterr().err
    raw = tmp_path / RAW
    assert not (raw / f"{CPR}.img.rows").exists()
    assert CPR_IMG_URL not in download.calls
    metadata = json.loads((raw / "fetch.json").read_bytes())
    assert metadata["window"]["status"] == "range_not_supported"
    assert "ignored the Range header" in metadata["window"]["reason"]
    assert metadata["stokes"]["status"] == "not_requested"

    assert run(tmp_path, "record", "minirf") == 0
    record = mosaic_json(tmp_path)
    assert record["range_read"]["status"] == "range_not_supported"
    assert record["cpr_statistics"]["status"] == "not_computed"
    assert [r.product_id for r in sources(tmp_path)] == label_ids()
    assert run(tmp_path, "check", "minirf") == 0


def test_an_unreachable_source_fails_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_site_record(tmp_path)
    failure = DownloadError(f"{LANDING}: could not connect: [Errno 8] nodename nor servname")
    download = MockDownloader(volume(), fail={LANDING: failure})
    assert run(tmp_path, "fetch", "minirf", download=download) != 0
    error = capsys.readouterr().err
    assert LANDING in error and "could not connect" in error
    assert not (tmp_path / RAW / "fetch.json").exists()


def test_a_range_failure_fails_the_fetch_naming_the_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_site_record(tmp_path)
    failure = RangeReadError(f"{CPR_IMG_URL}: HTTP 503 Service Unavailable")
    assert run(tmp_path, "fetch", "minirf", read_range=MockRangeFetcher(fail=failure)) != 0
    assert CPR_IMG_URL in capsys.readouterr().err
    assert not (tmp_path / RAW / "fetch.json").exists()


def test_a_file_size_that_differs_from_the_label_fails_the_fetch(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    assert run(tmp_path, "fetch", "minirf", read_range=MockRangeFetcher(size=FILE_BYTES + 4)) != 0
    assert not (tmp_path / RAW / "fetch.json").exists()


def test_without_the_site01_extent_the_record_is_partial_with_the_reason(tmp_path: Path) -> None:
    read_range = MockRangeFetcher()
    assert run(tmp_path, "fetch", "minirf", read_range=read_range) == 0
    assert read_range.calls == []
    assert run(tmp_path, "record", "minirf") == 0
    record = mosaic_json(tmp_path)
    assert record["window"]["status"] == "not_located"
    assert SITE_RECORD in record["window"]["reason"]
    assert "T1-03" in record["window"]["reason"]
    assert record["range_read"]["status"] == "not_requested"
    records = sources(tmp_path)
    assert [r.product_id for r in records] == label_ids()
    assert all(r.verification_status == "PARTIAL" for r in records)
    assert run(tmp_path, "check", "minirf") == 0


def test_the_volume_link_must_be_on_the_landing_page(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    served = volume()
    served[LANDING] = b"<html><body>no volumes here</body></html>"
    assert run(tmp_path, "fetch", "minirf", served=served) != 0


# --- record and check ---------------------------------------------------------------------------


def label_ids(found: dict[str, bytes] | None = None) -> list[str]:
    """The ProductRecord ids of every mosaic label a fetch downloads."""
    return sorted(f"MINIRF/LROMRF_0005/{name}.lbl" for name in (found or labels()))


def test_record_writes_the_mosaic_record_and_its_product_records(fetched: Path) -> None:
    record = mosaic_json(fetched)
    assert record["record"] == "minirf_mosaic"
    assert record["data_set_id"] == "LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0"
    assert record["product_id"] == "LSZ_XXXXX_3CP_PIU_90S000_V1"
    assert record["volume"] == "LROMRF_0005"
    assert record["label"]["url"] == CPR_LBL_URL
    assert record["label"]["sha256"] == hashlib.sha256(labels()[CPR]).hexdigest()
    assert record["data"]["url"] == CPR_IMG_URL
    assert record["data"]["remote_bytes"] == FILE_BYTES
    assert record["data"]["size_matches_label"] is True

    records = {r.product_id: r for r in sources(fetched)}
    assert list(records) == sorted([*label_ids(), f"MINIRF/LROMRF_0005/{CPR}.img"])
    image = records[f"MINIRF/LROMRF_0005/{CPR}.img"]
    label = records[f"MINIRF/LROMRF_0005/{CPR}.lbl"]
    assert label.local_path == f"{RAW}/{CPR}.lbl"
    assert label.checksum == hashlib.sha256((fetched / RAW / f"{CPR}.lbl").read_bytes()).hexdigest()
    assert image.local_path == f"{RAW}/{CPR}.img.rows"
    rows = (fetched / RAW / f"{CPR}.img.rows").read_bytes()
    assert image.checksum == hashlib.sha256(rows).hexdigest()
    assert image.source_url == CPR_IMG_URL and label.source_url == CPR_LBL_URL
    for item in records.values():
        assert item.body == "moon"
        assert item.verification_status == "PARTIAL"
    assert "2379.99968158 <GHz>" in str(image.frequency_or_wavelength)
    assert image.transmit_receive_convention == "unknown"
    assert "not recoverable" in str(image.incidence_source)
    assert str(image.crs_and_frame).endswith(
        "the data set catalog states 'The mosaics are produced from Level 1 products which are "
        "oriented in reference to the Moon Principle Axis (PA) coordinate system, as realized "
        "using NASA's SPICE products (Frames Kernel).'"
    )
    assert str(image.frequency_or_wavelength).endswith(
        "the instrument catalog states 'Mini-RF operated in S-band (2.38 GHz)'"
    )


def test_every_downloaded_mosaic_label_gets_a_product_record(fetched: Path) -> None:
    records = {r.product_id: r for r in sources(fetched)}
    verification = mosaic_json(fetched)["verification"]
    assert set(verification) == set(records)
    for name in labels():
        record = records[f"MINIRF/LROMRF_0005/{name}.lbl"]
        local = fetched / RAW / f"{name}.lbl"
        assert record.local_path == f"{RAW}/{name}.lbl"
        assert record.checksum == hashlib.sha256(local.read_bytes()).hexdigest()
        assert record.source_url == MOSAICS + f"{name}.lbl"
        assert record.verification_status == "PARTIAL"
        assert verification[record.product_id]["status"] == "PARTIAL"
    # Each record states its own label, not the CPR mosaic's.
    north = records[f"MINIRF/LROMRF_0005/{CPR_NORTH}.lbl"]
    assert "+lat_0=90" in str(north.crs_and_frame)
    east = records[f"MINIRF/LROMRF_0005/{CPR_EAST}.lbl"]
    assert "20691 x 20691" in str(east.native_grid) and "512.0 <pix/deg>" in str(east.native_grid)
    assert "East- and West-looking" not in str(east.incidence_source)
    s1 = records[f"MINIRF/LROMRF_0005/{S1}.lbl"]
    assert s1.units == "unknown"
    assert "SC / OS" in str(records[f"MINIRF/LROMRF_0005/{CPR}.lbl"].units)
    # Labels whose image was not read say so; nothing of them was masked.
    assert "the image was not read" in str(s1.qa_definition)
    assert "its image was not read" in verification[s1.product_id]["basis"]
    assert "masked by quality.validity_mask" in str(
        records[f"MINIRF/LROMRF_0005/{CPR}.lbl"].qa_definition
    )


def test_record_is_reproducible_and_check_matches(fetched: Path) -> None:
    before = {
        path: (fetched / path).read_bytes()
        for path in ("outputs/pilot/minirf_mosaic.json", "outputs/sources.json")
    }
    assert pilot.record("minirf", root=fetched, sources={"minirf": SOURCE}) == []
    assert pilot.check("minirf", root=fetched, sources={"minirf": SOURCE}) == []
    assert all((fetched / path).read_bytes() == data for path, data in before.items())


def test_check_reports_drift_in_the_committed_record(fetched: Path) -> None:
    path = fetched / "outputs/pilot/minirf_mosaic.json"
    path.write_bytes(path.read_bytes().replace(b'"minirf_mosaic"', b'"edited"'))
    assert run(fetched, "check", "minirf") == 1


def test_check_without_cached_data_names_the_fetch_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "check", "minirf") == 1
    assert "run: python -m terraluna_pipeline.pilot fetch minirf first" in capsys.readouterr().err


def test_record_refuses_changed_window_bytes(fetched: Path) -> None:
    path = fetched / RAW / f"{CPR}.img.rows"
    data = bytearray(path.read_bytes())
    data[100] ^= 0xFF
    path.write_bytes(bytes(data))
    assert run(fetched, "check", "minirf") == 1


def test_record_refuses_a_site_extent_that_changed_since_the_fetch(fetched: Path) -> None:
    write_site_record(fetched, {**SITE_EXTENT, "left": -15000.0})
    with pytest.raises(pilot.PilotError, match="fetch minirf"):
        pilot.check("minirf", root=fetched, sources={"minirf": SOURCE})


def test_record_keeps_other_sources_records(fetched: Path) -> None:
    other = sources(fetched)[0].model_dump()
    other["product_id"] = "PGDA78/Site01/Site01_final_adj_5mpp_surf.tif"
    path = fetched / "outputs/sources.json"
    current = json.loads(path.read_bytes())
    path.write_text(json.dumps([other, *current]))
    pilot.record("minirf", root=fetched, sources={"minirf": SOURCE})
    ids = [r.product_id for r in sources(fetched)]
    assert ids == sorted(ids)
    assert "PGDA78/Site01/Site01_final_adj_5mpp_surf.tif" in ids


# --- layout leads (AC1) -------------------------------------------------------------------------


def leads(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {lead["item"]: lead for lead in record["layout_leads"]}


def test_every_prd_layout_lead_is_checked_against_the_label(fetched: Path) -> None:
    found = leads(mosaic_json(fetched))
    assert set(found) == {
        "file",
        "data_set",
        "size",
        "sample_type",
        "values",
        "projection",
        "pixels_per_degree",
        "pixel_size",
        "projection_centre",
        "missing_data_isis_null",
        "missing_data_non_positive",
        "acquisitions",
    }
    for item, lead in found.items():
        assert lead["prd_lead"], item
        assert lead["label_value"] is not None, item
        if item != "missing_data_non_positive":
            assert lead["status"] == "confirmed", (item, lead)
    non_positive = found["missing_data_non_positive"]
    assert non_positive["status"] == "not_stated_in_label"
    assert "valid" in non_positive["basis"]


def test_a_contradicted_lead_is_recorded_as_contradicted(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    found = labels()
    found[CPR] = pds_label(
        CPR,
        note="Circular Polarization Ratio South polar\nmosaic.",
        description=CPR_DESCRIPTION,
        resolution="128.0",
    )
    served = volume(mosaic_labels=found)
    assert run(tmp_path, "fetch", "minirf", served=served) == 0
    assert run(tmp_path, "record", "minirf") == 0
    assert leads(mosaic_json(tmp_path))["pixels_per_degree"]["status"] == "contradicted"


def test_the_null_and_sample_type_come_from_the_label(fetched: Path) -> None:
    record = mosaic_json(fetched)
    assert record["layout"]["sample_type"] == "PC_REAL"
    assert record["layout"]["sample_bits"] == 32
    assert record["layout"]["dtype"] == "<f4"
    assert record["layout"]["byte_order"] == "little-endian"
    assert record["null_value"]["raw"] == "16#FF7FFFFB#"
    assert record["null_value"]["value"] == ISIS_NULL
    assert record["null_value"]["mask_reason"] == "nodata"
    reasons = {item["keyword"]: item["mask_reason"] for item in record["special_values"]}
    assert reasons == {
        "CORE_LOW_REPR_SATURATION": "isis_saturation",
        "CORE_LOW_INSTR_SATURATION": "isis_saturation",
        "CORE_HIGH_REPR_SATURATION": "isis_saturation",
        "CORE_HIGH_INSTR_SATURATION": "isis_saturation",
    }
    assert "SC / OS" in record["units"]["value"]


# --- window (AC3) -------------------------------------------------------------------------------


def test_the_window_is_located_from_the_site01_extent_in_the_label_projection(
    fetched: Path,
) -> None:
    window = mosaic_json(fetched)["window"]
    assert window["status"] == "read"
    assert window["site_extent"]["source"] == SITE_RECORD
    assert window["site_extent"]["extent_m"] == SITE_EXTENT
    converted = window["converted_bounds_m"]
    for key, value in SITE_EXTENT.items():
        assert converted[key] == pytest.approx(value, abs=1e-6)
    assert window["pixel_window"] == {
        "col_off": COL_OFF,
        "row_off": ROW_OFF,
        "width": WIN_W,
        "height": WIN_H,
    }
    assert window["rows"] == {"first": ROW_OFF, "last": ROW_OFF + WIN_H - 1, "count": WIN_H}
    scale = SCALE_KM * 1000.0
    extent = window["window_extent_m"]
    assert extent["left"] == pytest.approx((COL_OFF + 0.5 - OFFSET) * scale)
    assert extent["top"] == pytest.approx((OFFSET - (ROW_OFF + 0.5)) * scale)
    assert extent["left"] <= SITE_EXTENT["left"] and extent["right"] >= SITE_EXTENT["right"]
    assert extent["bottom"] <= SITE_EXTENT["bottom"] and extent["top"] >= SITE_EXTENT["top"]
    projection = mosaic_json(fetched)["projection"]
    assert projection["centred_on_pole"] is True
    assert "+proj=stere" in projection["proj4"] and "+R=1737400" in projection["proj4"]


# --- CPR statistics (AC4) -----------------------------------------------------------------------


def expected_window() -> npt.NDArray[np.float32]:
    return mosaic_rows(ROW_OFF, WIN_H)[:, COL_OFF : COL_OFF + WIN_W]


def test_cpr_statistics_are_raw_observations_of_the_window(fetched: Path) -> None:
    stats = mosaic_json(fetched)["cpr_statistics"]
    window = expected_window()
    bits = window.view("<u4")
    null = bits == 0xFF7FFFFB
    saturated = (bits >= 0xFF7FFFFC) & (bits <= 0xFF7FFFFF)
    valid = ~null & ~saturated
    values = window[valid].astype(np.float64)
    assert stats["status"] == "computed"
    assert stats["total_pixels"] == WIN_W * WIN_H
    assert stats["valid_pixels"] == int(valid.sum()) == WIN_W * WIN_H - 4
    assert stats["valid_fraction"] == pytest.approx(valid.sum() / (WIN_W * WIN_H))
    assert stats["invalid_by_reason"] == {
        "nodata": 2,
        "non_finite": 0,
        "outside_support": 0,
        "isis_saturation": 2,
    }
    assert stats["min"] == float(values.min()) == -0.25
    assert stats["max"] == float(values.max()) == 2.5
    assert stats["median"] == float(np.median(values))
    assert stats["zero_pixels"] == 2
    assert stats["negative_pixels"] == 1
    assert stats["positive_pixels"] == int(valid.sum()) - 3


def test_handedness_look_direction_and_incidence_are_stated(fetched: Path) -> None:
    record = mosaic_json(fetched)
    handedness = record["transmit_handedness"]
    assert handedness["stated_by_product"] == "not stated"
    (doc,) = handedness["volume_documentation"]
    assert doc["source"] == ERRATA
    assert "left-hand circularly polarized" in doc["text"]
    look = record["look_direction"]
    assert "combined East and West-looking" in look["label_text"]
    assert look["mixed"] is True
    assert look["single_look_mosaics_covering_site01"] == [f"{CPR_EAST}.lbl"]
    incidence = record["incidence"]
    assert "per-pixel incidence is not recoverable from the mosaic" in incidence["statement"]
    frequency = record["frequency"]
    assert frequency["label_raw"] == "2379.99968158 <GHz>"
    assert frequency["instrument_catalog_text"] == "Mini-RF operated in S-band (2.38 GHz)"


# --- Stokes observation (AC5) -------------------------------------------------------------------


def test_without_an_s4_layer_the_stokes_observation_is_not_performed(fetched: Path) -> None:
    record = mosaic_json(fetched)
    stokes = record["stokes_observation"]
    assert set(stokes) == {"status", "reason"}
    assert stokes["status"] == "not_performed"
    assert "S4" in stokes["reason"] and S1 in stokes["reason"]
    layers = {layer["file_name"]: layer for layer in record["mosaic_layers"]}
    assert set(layers) == {f"{name}.lbl" for name in labels()}
    assert layers[f"{S1}.lbl"]["quantity"] == "S1"
    assert layers[f"{S1}.lbl"]["covers_site01"] is True
    assert layers[f"{CPR_NORTH}.lbl"]["covers_site01"] is False
    assert layers[f"{CPR_EAST}.lbl"]["look_direction"] == "EAST"
    (item,) = record["open_g0_items"]
    assert "LRO-L-MRFLRO-5-CDR-MAP-V1.0" in item["item"]
    text = json.dumps(record)
    assert "verdict" not in {key.lower() for key in stokes}
    assert "s4_positive" not in text


def stokes_window(
    quantity: str, window: tuple[int, int, int, int], lines: int = LINES
) -> npt.NDArray[np.float32]:
    col_off, row_off, width, height = window
    return mosaic_rows(row_off, height, quantity, lines)[:, col_off : col_off + width]


def isis_special(values: npt.NDArray[np.float32]) -> npt.NDArray[np.bool_]:
    """The ISIS Null and the four saturation codes, by bit pattern (the oracle for the mask)."""
    bits = values.view("<u4")
    special: npt.NDArray[np.bool_] = (bits >= 0xFF7FFFFB) & (bits <= 0xFF7FFFFF)
    return special


def row_bytes(window: tuple[int, int, int, int], record_bytes: int) -> tuple[int, int]:
    _, row_off, _, height = window
    return row_off * record_bytes, (row_off + height) * record_bytes - 1


@pytest.fixture
def stokes_fetched(tmp_path: Path) -> Path:
    """A repository root after a fetch and record of a volume with S1 and S4 over Site01."""
    write_site_record(tmp_path)
    assert run(tmp_path, "fetch", "minirf", served=volume(with_s4=True)) == 0
    assert run(tmp_path, "record", "minirf") == 0
    return tmp_path


def test_fetch_range_reads_the_s1_and_s4_windows_when_both_cover_site01(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    download, read_range = MockDownloader(volume(with_s4=True)), MockRangeFetcher()
    assert run(tmp_path, "fetch", "minirf", download=download, read_range=read_range) == 0

    assert not any(url.endswith(".img") for url in download.calls)
    first, last = row_bytes((COL_OFF, ROW_OFF, WIN_W, WIN_H), RECORD_BYTES)
    assert read_range.calls == [
        (CPR_IMG_URL, first, last),
        (S1_IMG_URL, first, last),
        (S4_IMG_URL, first, last),
    ]
    raw = tmp_path / RAW
    metadata = json.loads((raw / "fetch.json").read_bytes())
    assert metadata["stokes"] == {"status": "read", "reason": None}
    entries = {e["quantity"]: e for e in metadata["files"] if e["role"] == "stokes_window_rows"}
    assert set(entries) == {"S1", "S4"}
    for quantity, name in (("S1", S1), ("S4", S4)):
        entry = entries[quantity]
        rows = (raw / f"{name}.img.rows").read_bytes()
        assert rows == IMAGES[MOSAICS + f"{name}.img"].read(first, last)
        assert entry["file_name"] == f"{name}.img.rows"
        assert entry["label_file_name"] == f"{name}.lbl"
        assert entry["provider_file_name"] == f"{name}.img"
        assert entry["http_status"] == 206
        assert entry["content_range"] == f"bytes {first}-{last}/{FILE_BYTES}"
        assert entry["bytes_received"] == len(rows) == WIN_H * RECORD_BYTES
        assert entry["sha256"] == hashlib.sha256(rows).hexdigest()
        assert entry["rows"] == {"first": ROW_OFF, "last": ROW_OFF + WIN_H - 1}


def test_the_stokes_observation_records_raw_s1_and_s4_statistics(stokes_fetched: Path) -> None:
    record = mosaic_json(stokes_fetched)
    stokes = record["stokes_observation"]
    assert stokes["status"] == "observed"
    assert stokes["reason"] is None
    assert "polarimetry.lunar_s4_sign_check (T2-05)" in stokes["note"]
    (pair,) = stokes["pairs"]
    window = (COL_OFF, ROW_OFF, WIN_W, WIN_H)
    assert pair["pixel_window"] == {
        "col_off": COL_OFF,
        "row_off": ROW_OFF,
        "width": WIN_W,
        "height": WIN_H,
    }
    s1, s4 = stokes_window("S1", window), stokes_window("S4", window)
    valid = ~isis_special(s1) & ~isis_special(s4)
    s1_valid, s4_valid = s1[valid].astype(np.float64), s4[valid].astype(np.float64)
    assert pair["total_pixels"] == WIN_W * WIN_H
    assert pair["valid_pixels"] == int(valid.sum()) == WIN_W * WIN_H - 4
    assert pair["s4_positive_pixels"] == int(np.count_nonzero(s4_valid > 0))
    assert pair["s4_negative_pixels"] == int(np.count_nonzero(s4_valid < 0))
    assert pair["s4_zero_pixels"] == int(np.count_nonzero(s4_valid == 0))
    assert min(pair["s4_positive_pixels"], pair["s4_negative_pixels"], pair["s4_zero_pixels"]) > 0
    # The values are recorded as stored: the negative S4 minimum shows no sign was flipped.
    assert pair["s1"]["min"] == float(s1_valid.min()) and pair["s1"]["max"] == float(s1_valid.max())
    assert pair["s4"]["min"] == float(s4_valid.min()) == -0.5
    assert pair["s4"]["max"] == float(s4_valid.max())
    first, last = row_bytes(window, RECORD_BYTES)
    for quantity, name in (("s1", S1), ("s4", S4)):
        layer = pair[quantity]
        assert layer["label_file_name"] == f"{name}.lbl"
        assert layer["image_file_name"] == f"{name}.img"
        assert layer["url"] == MOSAICS + f"{name}.img"
        assert layer["http_status"] == 206
        assert layer["byte_ranges"] == [[first, last]]
        assert layer["bytes_received"] == last - first + 1
        assert layer["local_path"] == f"{RAW}/{name}.img.rows"
        rows = (stokes_fetched / RAW / f"{name}.img.rows").read_bytes()
        assert layer["local_sha256"] == hashlib.sha256(rows).hexdigest()
        assert layer["units"]["value"] == "unknown"
        assert "UNIT" in layer["units"]["source"]
    assert pair["s1"]["invalid_by_reason"] == {
        "nodata": 1,
        "non_finite": 0,
        "outside_support": 0,
        "isis_saturation": 1,
    }
    assert pair["s4"]["invalid_by_reason"] == {
        "nodata": 2,
        "non_finite": 0,
        "outside_support": 0,
        "isis_saturation": 1,
    }
    # Raw observations only: no verdict and no S1 >= |S4| validity count anywhere.
    keys: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            keys.update(key.lower() for key in value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(stokes)
    assert not any("verdict" in key or "sign" in key or "abs" in key for key in keys)
    assert not any("open_g0" in key for key in keys)
    assert all("MAP-V1.0" not in item["item"] for item in record["open_g0_items"])
    assert pilot.check("minirf", root=stokes_fetched, sources={"minirf": SOURCE}) == []


def test_the_stokes_window_rows_get_product_records(stokes_fetched: Path) -> None:
    records = {r.product_id: r for r in sources(stokes_fetched)}
    for name in (S1, S4):
        image = records[f"MINIRF/LROMRF_0005/{name}.img"]
        rows = (stokes_fetched / RAW / f"{name}.img.rows").read_bytes()
        assert image.local_path == f"{RAW}/{name}.img.rows"
        assert image.checksum == hashlib.sha256(rows).hexdigest()
        assert image.source_url == MOSAICS + f"{name}.img"
        assert image.units == "unknown"
        assert image.verification_status == "PARTIAL"
        assert f"MINIRF/LROMRF_0005/{name}.lbl" in records
    ids = list(records)
    assert ids == sorted(ids)


def test_each_s1_and_s4_pair_is_read_on_its_own_grid(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    found = labels(with_s4=True)
    found[S1_EAST] = east_label(
        S1_EAST, "First stokes parameter South polar mosaic.", "The First Stokes parameter."
    )
    found[S4_EAST] = east_label(
        S4_EAST, "Fourth stokes parameter South polar mosaic.", "The Fourth Stokes parameter."
    )
    read_range = MockRangeFetcher()
    served = volume(mosaic_labels=found)
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) == 0
    assert run(tmp_path, "record", "minirf") == 0

    east = _window_by_formula(EAST_SCALE_KM, EAST_OFFSET)
    assert east != (COL_OFF, ROW_OFF, WIN_W, WIN_H)
    east_first, east_last = row_bytes(east, EAST_RECORD_BYTES)
    assert (S1_EAST_IMG_URL, east_first, east_last) in read_range.calls
    assert (S4_EAST_IMG_URL, east_first, east_last) in read_range.calls
    assert len(read_range.calls) == 5
    pairs = mosaic_json(tmp_path)["stokes_observation"]["pairs"]
    assert [(p["s1"]["label_file_name"], p["s4"]["label_file_name"]) for p in pairs] == [
        (f"{S1_EAST}.lbl", f"{S4_EAST}.lbl"),
        (f"{S1}.lbl", f"{S4}.lbl"),
    ]
    col_off, row_off, width, height = east
    assert pairs[0]["pixel_window"] == {
        "col_off": col_off,
        "row_off": row_off,
        "width": width,
        "height": height,
    }
    s4 = stokes_window("S4", east, EAST_LINES)
    assert pairs[0]["s4"]["min"] == float(s4.astype(np.float64).min())
    assert pairs[0]["valid_pixels"] == width * height


def test_an_s4_layer_that_does_not_cover_site01_is_not_read(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    found = labels()
    found[S4_NORTH] = pds_label(
        S4_NORTH,
        note="Fourth stokes parameter North polar mosaic.",
        description=S4_DESCRIPTION,
        center_latitude="90.0",
    )
    read_range = MockRangeFetcher()
    served = volume(mosaic_labels=found)
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) == 0
    assert [call[0] for call in read_range.calls] == [CPR_IMG_URL]
    assert run(tmp_path, "record", "minirf") == 0
    record = mosaic_json(tmp_path)
    stokes = record["stokes_observation"]
    assert set(stokes) == {"status", "reason"}
    assert stokes["status"] == "not_performed"
    assert f"{S4_NORTH}.lbl" in stokes["reason"] and "Site01" in stokes["reason"]
    (item,) = record["open_g0_items"]
    assert "LRO-L-MRFLRO-5-CDR-MAP-V1.0" in item["item"]


def test_an_s4_layer_without_an_s1_layer_on_its_grid_stops_the_fetch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_site_record(tmp_path)
    found = labels(with_s4=True)
    del found[S1]
    read_range = MockRangeFetcher()
    served = volume(mosaic_labels=found)
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) != 0
    error = capsys.readouterr().err
    assert f"{S4}.lbl" in error and "S1" in error
    assert read_range.calls == []
    assert not (tmp_path / RAW / "fetch.json").exists()


def ignored_range(url: str) -> RangeNotSupportedError:
    return RangeNotSupportedError(
        f"{url}: the server ignored the Range header and answered HTTP 200 with the "
        "whole file; the body was not read"
    )


def two_grid_labels() -> dict[str, bytes]:
    """The combined S1 and S4 layers plus an east-looking S1 and S4 pair on a finer grid."""
    found = labels(with_s4=True)
    found[S1_EAST] = east_label(
        S1_EAST, "First stokes parameter South polar mosaic.", "The First Stokes parameter."
    )
    found[S4_EAST] = east_label(
        S4_EAST, "Fourth stokes parameter South polar mosaic.", "The Fourth Stokes parameter."
    )
    return found


STOKES_STATISTICS = {"min", "max", "invalid_by_reason", "valid_pixels", "total_pixels"}


def test_a_refused_s4_range_read_keeps_the_s1_window_read_before_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_site_record(tmp_path)
    read_range = MockRangeFetcher(fail_for={S4_IMG_URL: ignored_range(S4_IMG_URL)})
    served = volume(with_s4=True)
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) != 0
    assert "Range" in capsys.readouterr().err
    first, last = row_bytes((COL_OFF, ROW_OFF, WIN_W, WIN_H), RECORD_BYTES)
    assert read_range.calls == [
        (CPR_IMG_URL, first, last),
        (S1_IMG_URL, first, last),
        (S4_IMG_URL, first, last),
    ]
    raw = tmp_path / RAW
    metadata = json.loads((raw / "fetch.json").read_bytes())
    assert metadata["window"]["status"] == "read"
    assert metadata["stokes"]["status"] == "range_not_supported"
    assert metadata["stokes"]["refused_label_file_name"] == f"{S4}.lbl"
    assert "ignored the Range header" in metadata["stokes"]["reason"]
    assert (raw / f"{CPR}.img.rows").exists()
    # The S1 window was read before the refusal: its bytes and HTTP metadata are kept.
    rows = (raw / f"{S1}.img.rows").read_bytes()
    assert rows == IMAGES[S1_IMG_URL].read(first, last)
    (entry,) = [e for e in metadata["files"] if e["role"] == "stokes_window_rows"]
    assert entry["quantity"] == "S1"
    assert entry["label_file_name"] == f"{S1}.lbl"
    assert entry["url"] == S1_IMG_URL
    assert entry["http_status"] == 206
    assert entry["content_range"] == f"bytes {first}-{last}/{FILE_BYTES}"
    assert entry["bytes_received"] == len(rows) == WIN_H * RECORD_BYTES
    assert entry["sha256"] == hashlib.sha256(rows).hexdigest()
    assert entry["retrieved_at"] == "2026-11-14T20:40:05Z"
    assert not (raw / f"{S4}.img.rows").exists()

    assert run(tmp_path, "record", "minirf") == 0
    record = mosaic_json(tmp_path)
    stokes = record["stokes_observation"]
    assert stokes["status"] == "not_performed"
    assert "ignored the Range header" in stokes["reason"]
    assert "polarimetry.lunar_s4_sign_check (T2-05)" in stokes["reason"]
    assert stokes["pairs"] == []
    (pair,) = stokes["pairs_not_observed"]
    assert pair["pixel_window"] == {
        "col_off": COL_OFF,
        "row_off": ROW_OFF,
        "width": WIN_W,
        "height": WIN_H,
    }
    (window,) = pair["windows_read"]
    assert window["label_file_name"] == f"{S1}.lbl"
    assert window["image_file_name"] == f"{S1}.img"
    assert window["url"] == S1_IMG_URL
    assert window["http_status"] == 206
    assert window["content_range"] == f"bytes {first}-{last}/{FILE_BYTES}"
    assert window["byte_ranges"] == [[first, last]]
    assert window["bytes_received"] == len(rows)
    assert window["retrieved_at"] == "2026-11-14T20:40:05Z"
    assert window["local_path"] == f"{RAW}/{S1}.img.rows"
    assert window["local_sha256"] == hashlib.sha256(rows).hexdigest()
    # No statistics for a pair with one window: neither layer is decoded.
    assert not STOKES_STATISTICS & (set(window) | set(pair))
    assert not any(key.startswith("s4_") for key in pair)
    (unread,) = pair["windows_not_read"]
    assert unread["label_file_name"] == f"{S4}.lbl"
    assert "ignored the Range header" in unread["reason"]

    records = {r.product_id: r for r in sources(tmp_path)}
    image = records[f"MINIRF/LROMRF_0005/{S1}.img"]
    assert image.local_path == f"{RAW}/{S1}.img.rows"
    assert image.checksum == hashlib.sha256(rows).hexdigest()
    assert image.source_url == S1_IMG_URL
    assert image.verification_status == "PARTIAL"
    assert "not decoded" in image.qa_definition and "masked by" not in image.qa_definition
    assert f"MINIRF/LROMRF_0005/{S4}.img" not in records
    assert "not decoded" in record["verification"][f"MINIRF/LROMRF_0005/{S1}.img"]["basis"]
    assert "not decoded" in record["verification"][f"MINIRF/LROMRF_0005/{S1}.lbl"]["basis"]
    assert record["cpr_statistics"]["status"] == "computed"
    (item,) = record["open_g0_items"]
    assert f"{S4}.lbl" in item["item"] and f"{S1}.lbl" not in item["item"]
    assert run(tmp_path, "check", "minirf") == 0


def test_a_refusal_keeps_the_statistics_of_the_pairs_read_before_it(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    read_range = MockRangeFetcher(fail_for={S4_IMG_URL: ignored_range(S4_IMG_URL)})
    served = volume(mosaic_labels=two_grid_labels())
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) != 0
    east = _window_by_formula(EAST_SCALE_KM, EAST_OFFSET)
    east_first, east_last = row_bytes(east, EAST_RECORD_BYTES)
    first, last = row_bytes((COL_OFF, ROW_OFF, WIN_W, WIN_H), RECORD_BYTES)
    assert read_range.calls == [
        (CPR_IMG_URL, first, last),
        (S1_EAST_IMG_URL, east_first, east_last),
        (S4_EAST_IMG_URL, east_first, east_last),
        (S1_IMG_URL, first, last),
        (S4_IMG_URL, first, last),
    ]
    assert run(tmp_path, "record", "minirf") == 0

    stokes = mosaic_json(tmp_path)["stokes_observation"]
    assert stokes["status"] == "incomplete"
    assert "ignored the Range header" in stokes["reason"]
    (observed,) = stokes["pairs"]
    assert (observed["s1"]["label_file_name"], observed["s4"]["label_file_name"]) == (
        f"{S1_EAST}.lbl",
        f"{S4_EAST}.lbl",
    )
    s4 = stokes_window("S4", east, EAST_LINES)
    assert observed["s4"]["min"] == float(s4.astype(np.float64).min())
    assert observed["valid_pixels"] == east[2] * east[3]
    (unobserved,) = stokes["pairs_not_observed"]
    assert [w["label_file_name"] for w in unobserved["windows_read"]] == [f"{S1}.lbl"]
    assert [w["label_file_name"] for w in unobserved["windows_not_read"]] == [f"{S4}.lbl"]
    records = {r.product_id for r in sources(tmp_path)}
    for name in (S1_EAST, S4_EAST, S1):
        assert f"MINIRF/LROMRF_0005/{name}.img" in records
    assert f"MINIRF/LROMRF_0005/{S4}.img" not in records
    assert run(tmp_path, "check", "minirf") == 0


def test_a_refused_first_stokes_read_requests_no_other_window(tmp_path: Path) -> None:
    write_site_record(tmp_path)
    read_range = MockRangeFetcher(fail_for={S1_IMG_URL: ignored_range(S1_IMG_URL)})
    served = volume(with_s4=True)
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) != 0
    assert [call[0] for call in read_range.calls] == [CPR_IMG_URL, S1_IMG_URL]
    assert run(tmp_path, "record", "minirf") == 0

    stokes = mosaic_json(tmp_path)["stokes_observation"]
    assert stokes["status"] == "not_performed"
    assert stokes["pairs"] == []
    (pair,) = stokes["pairs_not_observed"]
    assert pair["windows_read"] == []
    refused, skipped = pair["windows_not_read"]
    assert refused["label_file_name"] == f"{S1}.lbl"
    assert "ignored the Range header" in refused["reason"]
    assert skipped["label_file_name"] == f"{S4}.lbl"
    assert "not requested" in skipped["reason"]
    records = {r.product_id for r in sources(tmp_path)}
    assert not {f"MINIRF/LROMRF_0005/{name}.img" for name in (S1, S4)} & records
    assert run(tmp_path, "check", "minirf") == 0


def test_record_refuses_a_refused_fetch_that_lacks_the_windows_read_before_it(
    tmp_path: Path,
) -> None:
    write_site_record(tmp_path)
    read_range = MockRangeFetcher(fail_for={S4_IMG_URL: ignored_range(S4_IMG_URL)})
    served = volume(with_s4=True)
    assert run(tmp_path, "fetch", "minirf", served=served, read_range=read_range) != 0
    path = tmp_path / RAW / "fetch.json"
    metadata = json.loads(path.read_bytes())
    metadata["files"] = [e for e in metadata["files"] if e["role"] != "stokes_window_rows"]
    path.write_text(json.dumps(metadata))
    with pytest.raises(pilot.PilotError, match="fetch minirf"):
        pilot.check("minirf", root=tmp_path, sources={"minirf": SOURCE})


def test_record_refuses_a_fetch_that_lacks_the_stokes_rows(stokes_fetched: Path) -> None:
    path = stokes_fetched / RAW / "fetch.json"
    metadata = json.loads(path.read_bytes())
    metadata["files"] = [e for e in metadata["files"] if e.get("quantity") != "S4"]
    path.write_text(json.dumps(metadata))
    with pytest.raises(pilot.PilotError, match="fetch minirf"):
        pilot.check("minirf", root=stokes_fetched, sources={"minirf": SOURCE})


def test_the_registered_source_reads_the_pds_geosciences_node() -> None:
    source = pilot.SOURCES["minirf"]
    assert isinstance(source, pilot.MinirfSource)
    assert source.landing_url == "https://pds-geosciences.wustl.edu/missions/lro/mrf.htm"
    assert "pgda78" in pilot.SOURCES
