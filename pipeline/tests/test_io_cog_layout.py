"""Tests for the COG layout reader and the header-aware remote window read (T1-06; PRD 8.3, 8.7).

A synthetic cloud-optimized GeoTIFF is written into tmp_path. Its tiles are deflate-compressed
without a predictor, so a test can decompress the bytes at the offset and size the layout states
and compare them with the array it wrote: an oracle independent of GDAL's tile index. The file
is served with Range support by a server on 127.0.0.1; an autouse fixture refuses to resolve any
other host.
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
import zlib
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
    ByteRanges,
    CogBlock,
    CogLayout,
    CogOverview,
    GeoTiffHeader,
    RangeCacheError,
    read_cog_layout,
    read_geotiff_header,
    read_remote_cog_window,
    read_remote_geotiff_window,
)

WIDTH, HEIGHT = 600, 700
TILE = 256
BLOCK = 8192
WINDOW = Window(200, 250, 100, 30)
"""Columns 200-299 and rows 250-279: block columns 0 and 1, block rows 0 and 1."""
SPARSE_BLOCK = (0, 1)
"""A block WINDOW touches, which the sparse COG does not store."""

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


def write_cog(
    path: Path, *, sparse_block: tuple[int, int] | None = None
) -> npt.NDArray[np.float32]:
    """A 600 x 700 EPSG:4326 COG in 256 x 256 tiles, deflate without a predictor.

    With ``sparse_block`` (row, column), that block is all zeros and the file is written with
    SPARSE_OK, so GDAL leaves the block out of the file and its tile index states no offset or
    size for it.
    """
    rng = np.random.default_rng(23)
    array = rng.normal(800.0, 30.0, (HEIGHT, WIDTH)).astype(np.float32)
    options: dict[str, str] = {}
    if sparse_block is not None:
        row, col = sparse_block
        array[row * TILE : (row + 1) * TILE, col * TILE : (col + 1) * TILE] = 0.0
        options["SPARSE_OK"] = "TRUE"
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=WIDTH,
        height=HEIGHT,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-17.0, 65.0, 1 / 600, 1 / 700),
        blocksize=TILE,
        compress="deflate",
        predictor=1,
        overview_resampling="nearest",
        **options,
    ) as dst:
        dst.write(array, 1)
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
    path = tmp_path / "dem.tif"
    yield from serve(path, write_cog(path))


@pytest.fixture
def served_sparse(tmp_path: Path) -> Iterator[Served]:
    """The COG with block (0, 1), which WINDOW touches, left out of the file (SPARSE_OK)."""
    path = tmp_path / "sparse.tif"
    yield from serve(path, write_cog(path, sparse_block=SPARSE_BLOCK))


def serve(path: Path, array: npt.NDArray[np.float32]) -> Iterator[Served]:
    state = RangeServer(body=path.read_bytes())
    handler = type("Handler", (RangeHandler,), {"state": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/tiles/dem.tif"
        yield Served(url=url, path=path, array=array, state=state)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def choose(header: GeoTiffHeader) -> Window:
    assert header.width == WIDTH and header.height == HEIGHT
    return WINDOW


# --- read_cog_layout ----------------------------------------------------------------------------


def test_the_layout_states_the_block_grid_and_the_overviews(tmp_path: Path) -> None:
    path = tmp_path / "dem.tif"
    write_cog(path)
    layout = read_cog_layout(path, WINDOW)
    assert isinstance(layout, CogLayout)
    assert (layout.width, layout.height, layout.band) == (WIDTH, HEIGHT, 1)
    assert layout.block_shape == (TILE, TILE)
    assert (layout.blocks_across, layout.blocks_down) == (3, 3)
    assert layout.window == (200, 250, 100, 30)
    with rasterio.open(path) as src:
        factors = src.overviews(1)
    assert factors, "the synthetic COG must have overviews"
    assert layout.overviews == tuple(
        CogOverview(
            level=level,
            factor=factor,
            width=-(-WIDTH // factor),
            height=-(-HEIGHT // factor),
            block_shape=(TILE, TILE),
        )
        for level, factor in enumerate(factors)
    )


def test_the_touched_blocks_hold_exactly_the_tile_bytes(tmp_path: Path) -> None:
    path = tmp_path / "dem.tif"
    array = write_cog(path)
    layout = read_cog_layout(path, WINDOW)
    assert [(block.row, block.col) for block in layout.blocks] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    data = path.read_bytes()
    for block in layout.blocks:
        assert isinstance(block, CogBlock)
        assert block.offset is not None and block.size is not None and block.size > 0
        tile = zlib.decompress(data[block.offset : block.offset + block.size])
        rows = slice(block.row * TILE, (block.row + 1) * TILE)
        cols = slice(block.col * TILE, (block.col + 1) * TILE)
        assert tile == array[rows, cols].astype("<f4").tobytes()
    assert layout.stored_bytes == sum(block.size for block in layout.blocks if block.size)


def test_a_block_the_file_does_not_store_has_no_offset_or_size(tmp_path: Path) -> None:
    path = tmp_path / "sparse.tif"
    write_cog(path, sparse_block=SPARSE_BLOCK)
    layout = read_cog_layout(path, WINDOW)
    by_position = {(block.row, block.col): block for block in layout.blocks}
    assert set(by_position) == {(0, 0), (0, 1), (1, 0), (1, 1)}
    unstored = by_position.pop(SPARSE_BLOCK)
    assert (unstored.offset, unstored.size) == (None, None)
    for block in by_position.values():
        assert block.offset is not None and block.size is not None and block.size > 0


def test_the_stored_bytes_of_a_window_with_an_unstated_block_size_are_unknown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sparse.tif"
    write_cog(path, sparse_block=SPARSE_BLOCK)
    layout = read_cog_layout(path, WINDOW)
    assert layout.stored_bytes is None


@pytest.mark.parametrize(
    ("sizes", "stored"),
    [
        ([(100, 7), (107, 5)], 12),
        ([(100, 7), (None, None)], None),
        ([(None, None), (None, None)], None),
        ([(100, 7), (107, None)], None),
    ],
)
def test_stored_bytes_are_a_sum_only_when_every_block_size_is_stated(
    sizes: list[tuple[int | None, int | None]], stored: int | None
) -> None:
    layout = CogLayout(
        width=512,
        height=256,
        band=1,
        block_shape=(256, 256),
        blocks_across=2,
        blocks_down=1,
        overviews=(),
        window=(0, 0, 512, 256),
        blocks=tuple(
            CogBlock(row=0, col=col, offset=offset, size=size)
            for col, (offset, size) in enumerate(sizes)
        ),
    )
    assert layout.stored_bytes == stored


@pytest.mark.parametrize(
    ("window", "blocks"),
    [
        (Window(0, 0, 1, 1), [(0, 0)]),
        (Window(255, 255, 2, 2), [(0, 0), (0, 1), (1, 0), (1, 1)]),
        (Window(512, 512, 88, 188), [(2, 2)]),
        (Window(0, 0, WIDTH, HEIGHT), [(r, c) for r in range(3) for c in range(3)]),
    ],
)
def test_the_touched_blocks_follow_the_window(
    tmp_path: Path, window: Window, blocks: list[tuple[int, int]]
) -> None:
    path = tmp_path / "dem.tif"
    write_cog(path)
    layout = read_cog_layout(path, window)
    assert [(block.row, block.col) for block in layout.blocks] == blocks


@pytest.mark.parametrize(
    "window",
    [
        Window(-1, 0, 10, 10),
        Window(595, 0, 10, 10),
        Window(0, 695, 10, 10),
        Window(0, 0, 0, 10),
        Window(0.5, 0, 10, 10),
    ],
)
def test_a_window_outside_the_raster_or_fractional_is_refused(
    tmp_path: Path, window: Window
) -> None:
    path = tmp_path / "dem.tif"
    write_cog(path)
    with pytest.raises(ValueError, match="window"):
        read_cog_layout(path, window)


def test_a_band_the_file_lacks_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "dem.tif"
    write_cog(path)
    with pytest.raises(ValueError, match="band 2"):
        read_cog_layout(path, WINDOW, band=2)


# --- ByteRanges.holds ---------------------------------------------------------------------------


def test_byte_ranges_say_whether_they_hold_a_span() -> None:
    ranges = ByteRanges(name="f.tif", size=100, ranges=((0, 9), (20, 29)), data=bytes(20))
    assert ranges.holds(0, 9)
    assert ranges.holds(3, 3)
    assert ranges.holds(20, 29)
    assert not ranges.holds(9, 10)
    assert not ranges.holds(5, 25)
    assert not ranges.holds(30, 30)
    assert not ranges.holds(95, 120)
    with pytest.raises(ValueError):
        ranges.holds(5, 4)
    with pytest.raises(ValueError):
        ranges.holds(-1, 4)


# --- read_remote_cog_window ---------------------------------------------------------------------


def test_the_remote_read_chooses_the_window_from_the_header(served: Served) -> None:
    seen: list[GeoTiffHeader] = []

    def chooser(header: GeoTiffHeader) -> Window:
        seen.append(header)
        return WINDOW

    result = read_remote_cog_window(served.url, chooser, block_bytes=BLOCK)
    (header,) = seen
    assert header == read_geotiff_header(served.path)
    assert header.transform is not None
    assert result.header == header
    assert result.window == (200, 250, 100, 30)
    assert np.array_equal(result.band.array, served.array[250:280, 200:300])
    assert result.bytes_received < served.path.stat().st_size
    assert not result.ranges.covers_whole_file


def test_the_remote_layout_matches_the_local_file_and_the_stored_ranges(
    served: Served, tmp_path: Path
) -> None:
    result = read_remote_cog_window(served.url, choose, block_bytes=BLOCK)
    expected = read_cog_layout(served.path, WINDOW)
    assert result.layout == expected
    assert read_cog_layout(tmp_path / "unused.tif", WINDOW, ranges=result.ranges) == expected


def test_every_touched_block_is_held_in_the_stored_ranges(served: Served) -> None:
    result = read_remote_cog_window(served.url, choose, block_bytes=BLOCK)
    assert result.layout is not None and result.layout.blocks
    for block in result.layout.blocks:
        assert block.offset is not None and block.size is not None
        assert result.ranges.holds(block.offset, block.offset + block.size - 1)


def test_the_remote_read_of_a_sparse_cog_reports_the_unstored_block(
    served_sparse: Served,
) -> None:
    result = read_remote_cog_window(served_sparse.url, choose, block_bytes=BLOCK)
    assert result.layout is not None
    assert result.layout == read_cog_layout(served_sparse.path, WINDOW)
    by_position = {(block.row, block.col): block for block in result.layout.blocks}
    assert (by_position[SPARSE_BLOCK].offset, by_position[SPARSE_BLOCK].size) == (None, None)
    assert result.layout.stored_bytes is None
    assert np.array_equal(result.band.array, served_sparse.array[250:280, 200:300])
    assert not result.ranges.covers_whole_file


def test_the_exchanges_are_tagged_header_then_window(served: Served) -> None:
    result = read_remote_cog_window(served.url, choose, block_bytes=BLOCK)
    phases = [exchange.phase for exchange in result.exchanges]
    assert phases[0] == "header"
    assert "window" in phases
    first_window = phases.index("window")
    assert set(phases[:first_window]) == {"header"}
    assert set(phases[first_window:]) == {"window"}
    assert result.exchanges[0].requested_first == 0
    assert result.exchanges[0].requested_last == BLOCK - 1
    assert sum(e.bytes_received for e in result.exchanges) == result.bytes_received


def test_the_window_phase_fetches_the_touched_tiles(served: Served) -> None:
    result = read_remote_cog_window(served.url, choose, block_bytes=BLOCK)
    assert result.layout is not None
    window_spans = [
        (e.requested_first, e.requested_last) for e in result.exchanges if e.phase == "window"
    ]
    for block in result.layout.blocks:
        assert block.offset is not None and block.size is not None
        first, last = block.offset, block.offset + block.size - 1
        header_held = any(
            e.requested_first <= first and last <= e.requested_last
            for e in result.exchanges
            if e.phase == "header"
        )
        assert header_held or any(a <= last and first <= b for a, b in window_spans)


def test_a_layout_byte_not_held_is_never_guessed(served: Served, tmp_path: Path) -> None:
    result = read_remote_cog_window(served.url, choose, block_bytes=BLOCK)
    first, last = result.ranges.ranges[0]
    truncated = ByteRanges(
        name=result.ranges.name,
        size=result.ranges.size,
        ranges=((first, min(last, 15)),),
        data=result.ranges.data[: min(last, 15) - first + 1],
    )
    with pytest.raises(RangeCacheError):
        read_cog_layout(tmp_path / "x.tif", WINDOW, ranges=truncated)


def test_the_plain_remote_read_tags_its_exchanges_too(served: Served) -> None:
    def centre(width: int, height: int) -> Window:
        return WINDOW

    result = read_remote_geotiff_window(served.url, centre, block_bytes=BLOCK)
    assert result.layout is None
    phases = [exchange.phase for exchange in result.exchanges]
    assert phases[0] == "header"
    assert set(phases) <= {"header", "window"}
