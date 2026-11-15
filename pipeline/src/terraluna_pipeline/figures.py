"""Card figures A to C as deterministic SVG (PRD 10, 8.4, 8.10; story T4R-02).

The pipeline renders the three figures of every evidence card so that the app and the report
show the same images:

- **A**, location maps: the target window (Moon on its PGDA78 DEM, Mars on CTX) and the Earth
  window (Copernicus GLO-30) as hillshades computed here from the cached DEM window, each with a
  scale bar whose length follows from the window's metres. No third-party basemap is used.
- **B**, nu(L) on log-log axes for target and Earth candidate, relief and texture, read from the
  windows files, with each family's distance, the reference spread (``distances_by_family``) and
  the clone envelope (the comparison's ``measurement_error`` component) when the comparison
  carries them, and ``Unavailable: <reason>`` otherwise.
- **C** by shipping mode (PRD 4): Mode A overlays the two radar block CPR distributions as
  cumulative distributions with the comparison's Wasserstein value; Mode B shows them side by
  side with their band labels and no comparison value; Mode C is an unavailable panel with the
  reason.

Nothing is computed from the science records here: every number on a figure is read from the
card, the comparison, the windows file or the DEM cache, except axis ticks, the scale bar length
and the hillshade itself.

Engineering choices: the SVG is written directly, not with matplotlib, so the output has no
timestamps, random ids or library-version noise; element order is fixed and coordinates use two
decimals. The hillshade (default azimuth 315 deg, altitude 45 deg, printed on the figure) is
embedded as a ``data:image/png;base64`` image written by Pillow without metadata chunks. That
data URI is not an external reference: :mod:`terraluna_pipeline.svg` allows exactly that form.
Colours are viridis samples, and series also differ by dash and marker (PRD 8.10).

CLI (CONTRACTS f)::

    python -m terraluna_pipeline.figures cards [--check] [--root PATH]
        [--azimuth 315] [--altitude 45]

``cards`` writes ``data/processed/figures/<card_id>_{A,B,C}.svg`` and the committed index
``outputs/figures/index.json``. ``--check`` recomputes every figure in memory, compares it and the
file on disk with the index, writes nothing and exits 1 naming each drifted figure. A missing
input (card, comparison, windows file or DEM cache) exits 3 naming it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from xml.sax.saxutils import escape

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from pydantic import TypeAdapter, ValidationError

from terraluna_pipeline.config import DEFAULT_RUN_CONFIG
from terraluna_pipeline.provenance import canonical_json
from terraluna_pipeline.schema import (
    CardRecord,
    ComparisonRecord,
    DistanceEntry,
    Family,
    RadarFingerprint,
    TerrainVariant,
    UncertaintyComponent,
    WindowEntry,
)
from terraluna_pipeline.svg import SVG_NS, svg_allowlist_errors

REPOSITORY_ROOT: Final[Path] = DEFAULT_RUN_CONFIG.parents[1]
COMMAND: Final = "python -m terraluna_pipeline.figures cards"
DEFAULT_AZIMUTH_DEG: Final = 315.0
DEFAULT_ALTITUDE_DEG: Final = 45.0
MAX_IMAGE_PX: Final = 480
"""Hillshades larger than this are block-averaged, so a 5 m DEM window stays a small file."""

FigureLetter = Literal["A", "B", "C"]
LETTERS: Final[tuple[FigureLetter, ...]] = ("A", "B", "C")

TARGET_COLOUR: Final = "#440154"  # viridis 0.0
EARTH_COLOUR: Final = "#21918c"  # viridis 0.5
SPREAD_COLOUR: Final = "#3b528b"  # viridis 0.25
ENVELOPE_COLOUR: Final = "#5ec962"  # viridis 0.75
INK: Final = "#1a1a1a"
MUTED: Final = "#5a5a5a"
GRID: Final = "#d0d0d0"

BODY_LABEL: Final = {"moon": "Lunar", "mars": "Mars", "earth": "Earth"}

_WINDOWS = TypeAdapter(list[WindowEntry])
_COMPARISONS = TypeAdapter(list[ComparisonRecord])


class FigureInputError(Exception):
    """An input exists but is invalid or inconsistent (exit 1)."""


@dataclass(frozen=True)
class Blocked(Exception):
    """Inputs are missing (exit 3). Each item is ``<code> <path>``."""

    items: tuple[str, ...]


@dataclass(frozen=True)
class DemWindow:
    """A cached DEM window: elevation in metres, row 0 north, NaN where invalid."""

    elevation_m: NDArray[np.float64]
    spacing_m: float
    product_id: str


@dataclass(frozen=True)
class CardInputs:
    card: CardRecord
    comparison: ComparisonRecord
    target_entry: WindowEntry
    earth_entry: WindowEntry
    target_dem: DemWindow
    earth_dem: DemWindow


# --- numbers and text ----------------------------------------------------------------------------


def _f(value: float) -> str:
    """Fixed two-decimal coordinates; never ``-0.00``."""
    text = f"{value:.2f}"
    return "0.00" if text == "-0.00" else text


def _num(value: float) -> str:
    """A value read from a record, as text (six significant digits)."""
    return format(value, ".6g")


def _t(text: str) -> str:
    return escape(text)


def _a(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def _unavailable(reason: str | None, fallback: str) -> str:
    return f"Unavailable: {reason or fallback}"


# --- hillshade and PNG ---------------------------------------------------------------------------


def hillshade(
    elevation_m: NDArray[np.float64],
    spacing_m: float,
    *,
    azimuth_deg: float = DEFAULT_AZIMUTH_DEG,
    altitude_deg: float = DEFAULT_ALTITUDE_DEG,
) -> NDArray[np.float64]:
    """Lambertian hillshade in [0, 1] of a DEM whose row 0 is north and columns run east.

    The sun's azimuth is clockwise from north and its altitude above the horizon. Slopes are
    central differences (one-sided at the edges); an invalid cell and its neighbours are NaN,
    so missing elevation is never shaded.
    """
    z = np.asarray(elevation_m, dtype=np.float64)
    if z.ndim != 2 or min(z.shape) < 2:
        raise FigureInputError(f"elevation must be a 2D array of at least 2x2, got {z.shape}")
    if not (math.isfinite(spacing_m) and spacing_m > 0):
        raise FigureInputError(f"spacing_m must be finite and positive, got {spacing_m!r}")
    dz_drow, dz_dcol = np.gradient(z, spacing_m)
    dz_dx = dz_dcol
    dz_dy = -dz_drow  # rows increase southward
    azimuth, altitude = math.radians(azimuth_deg), math.radians(altitude_deg)
    lx = math.sin(azimuth) * math.cos(altitude)
    ly = math.cos(azimuth) * math.cos(altitude)
    lz = math.sin(altitude)
    shade = (-dz_dx * lx - dz_dy * ly + lz) / np.sqrt(1.0 + dz_dx**2 + dz_dy**2)
    result: NDArray[np.float64] = np.where(np.isfinite(z), np.clip(shade, 0.0, 1.0), np.nan)
    return result


def _block_mean(values: NDArray[np.float64], factor: int) -> NDArray[np.float64]:
    """Mean over factor x factor blocks; a block with fewer than half its cells valid is NaN."""
    if factor == 1:
        return values
    valid = np.isfinite(values)
    rows = np.arange(0, values.shape[0], factor)
    cols = np.arange(0, values.shape[1], factor)

    def total(a: NDArray[np.float64]) -> NDArray[np.float64]:
        summed: NDArray[np.float64] = np.add.reduceat(np.add.reduceat(a, rows, axis=0), cols, 1)
        return summed

    sums = total(np.where(valid, values, 0.0))
    counts = total(valid.astype(np.float64))
    cells = total(np.ones_like(values))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean: NDArray[np.float64] = np.where(2.0 * counts >= cells, sums / counts, np.nan)
    return mean


def png_bytes(shade: NDArray[np.float64]) -> bytes:
    """Grey + alpha PNG of a [0, 1] shade; NaN cells are transparent. No metadata chunks."""
    finite = np.isfinite(shade)
    grey = np.rint(np.where(finite, np.clip(shade, 0.0, 1.0), 0.0) * 255.0).astype(np.uint8)
    alpha = np.where(finite, 255, 0).astype(np.uint8)
    image = Image.fromarray(np.ascontiguousarray(np.stack([grey, alpha], axis=-1)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def nice_scale_bar_m(width_m: float) -> float:
    """The largest 1, 2 or 5 x 10^k metres that is at most a quarter of the window width."""
    if not (math.isfinite(width_m) and width_m > 0):
        raise FigureInputError(f"window width must be positive, got {width_m!r}")
    target = width_m / 4.0
    exponent = math.floor(math.log10(target))
    best = 10.0**exponent
    for mantissa in (1.0, 2.0, 5.0, 10.0):
        candidate = mantissa * 10.0**exponent
        if candidate <= target * (1 + 1e-12):
            best = candidate
    return float(f"{best:.12g}")


def _length_label(metres: float) -> str:
    if metres >= 1000.0:
        return f"{metres / 1000.0:g} km"
    return f"{metres:g} m"


# --- SVG document --------------------------------------------------------------------------------


class _Svg:
    """An SVG document built in a fixed order."""

    def __init__(self, width: int, height: int, prefix: str, title: str, desc: str) -> None:
        self.parts: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="{prefix}-title {prefix}-desc">',
            f'<title id="{prefix}-title">{_t(title)}</title>',
            f'<desc id="{prefix}-desc">{_t(desc)}</desc>',
            f"<style>text{{font-family:sans-serif;font-size:12px;fill:{INK}}}"
            f".muted{{fill:{MUTED}}}.head{{font-size:15px;font-weight:bold}}"
            f".sub{{font-size:13px;font-weight:bold}}</style>",
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        ]

    def add(self, element: str) -> None:
        self.parts.append(element)

    def text(
        self,
        x: float,
        y: float,
        content: str,
        *,
        cls: str | None = None,
        anchor: str = "start",
        rotate: bool = False,
    ) -> None:
        attrs = f'x="{_f(x)}" y="{_f(y)}"'
        if anchor != "start":
            attrs += f' text-anchor="{anchor}"'
        if cls:
            attrs += f' class="{cls}"'
        if rotate:
            attrs += f' transform="rotate(-90 {_f(x)} {_f(y)})"'
        self.add(f"<text {attrs}>{_t(content)}</text>")

    def line(self, x1: float, y1: float, x2: float, y2: float, stroke: str, width: float) -> None:
        self.add(
            f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" '
            f'stroke="{stroke}" stroke-width="{_f(width)}"/>'
        )

    def rect(
        self, x: float, y: float, w: float, h: float, *, fill: str, stroke: str = "none"
    ) -> None:
        self.add(
            f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
            f'fill="{fill}" stroke="{stroke}"/>'
        )

    def render(self) -> bytes:
        data = ("\n".join([*self.parts, "</svg>"]) + "\n").encode("utf-8")
        errors = svg_allowlist_errors(data)
        if errors:  # pragma: no cover - a renderer bug, never an input problem
            raise AssertionError(f"rendered SVG fails the allowlist: {errors}")
        return data


def _heading(svg: _Svg, card: CardRecord, letter: FigureLetter, text: str) -> None:
    mock = "MOCK (synthetic test data). " if card.mock else ""
    svg.text(20, 28, f"{mock}Figure {letter}: {text}", cls="head")
    svg.text(20, 46, f"Card {card.card_id}; Mode {card.shipping_mode}", cls="muted")


def _prefix(card: CardRecord, letter: FigureLetter) -> str:
    return f"fig-{card.card_id.replace('__', '--')}-{letter}"


# --- figure A ------------------------------------------------------------------------------------


def _map_panel(
    svg: _Svg,
    x0: float,
    y0: float,
    box: float,
    label: str,
    window_id: str,
    dem: DemWindow,
    azimuth_deg: float,
    altitude_deg: float,
) -> None:
    rows, cols = dem.elevation_m.shape
    width_m, height_m = cols * dem.spacing_m, rows * dem.spacing_m
    scale = box / max(width_m, height_m)
    w, h = width_m * scale, height_m * scale
    factor = max(1, math.ceil(max(rows, cols) / MAX_IMAGE_PX))
    shade = _block_mean(
        hillshade(
            dem.elevation_m, dem.spacing_m, azimuth_deg=azimuth_deg, altitude_deg=altitude_deg
        ),
        factor,
    )
    encoded = base64.b64encode(png_bytes(shade)).decode("ascii")
    svg.text(x0, y0 - 26, f"{label} window {window_id}", cls="sub")
    svg.text(x0, y0 - 10, f"Hillshade of {dem.product_id}, {_num(dem.spacing_m)} m cells")
    svg.rect(x0, y0, w, h, fill="url(#nodata)")
    svg.add(
        f'<image x="{_f(x0)}" y="{_f(y0)}" width="{_f(w)}" height="{_f(h)}" '
        f'preserveAspectRatio="none" style="image-rendering:pixelated" '
        f'href="data:image/png;base64,{encoded}"/>'
    )
    svg.rect(x0, y0, w, h, fill="none", stroke=INK)
    svg.text(x0 + w - 6, y0 + 16, "N ↑", anchor="end")
    bar_m = nice_scale_bar_m(width_m)
    bar_px = bar_m * scale
    by = y0 + h + 22
    svg.rect(x0, by - 6, bar_px, 6, fill=INK)
    svg.line(x0, by - 10, x0, by, INK, 1)
    svg.line(x0 + bar_px, by - 10, x0 + bar_px, by, INK, 1)
    svg.text(x0 + bar_px + 8, by, _length_label(bar_m))
    svg.text(x0, by + 18, f"Window {_length_label(width_m)} x {_length_label(height_m)}")


def render_figure_a(
    inputs: CardInputs,
    *,
    azimuth_deg: float = DEFAULT_AZIMUTH_DEG,
    altitude_deg: float = DEFAULT_ALTITUDE_DEG,
) -> bytes:
    card = inputs.card
    body = BODY_LABEL[card.target.body]
    svg = _Svg(
        960,
        620,
        _prefix(card, "A"),
        f"Figure A: location of {card.target.target_window_id} and {card.earth.earth_window_id}",
        f"{body} target window on a hillshade of {inputs.target_dem.product_id} and the Earth "
        f"candidate window on a hillshade of {inputs.earth_dem.product_id}, each with a scale "
        "bar. No basemap is used. Hatched cells have no valid elevation.",
    )
    svg.add(
        '<defs><pattern id="nodata" width="6" height="6" patternUnits="userSpaceOnUse">'
        f'<rect width="6" height="6" fill="#f2f2f2"/><path d="M0 6L6 0" stroke="{MUTED}" '
        'stroke-width="0.8"/></pattern></defs>'
    )
    _heading(svg, card, "A", "location maps")
    _map_panel(
        svg,
        30,
        100,
        420,
        f"{body} target",
        card.target.target_window_id,
        inputs.target_dem,
        azimuth_deg,
        altitude_deg,
    )
    _map_panel(
        svg,
        510,
        100,
        420,
        "Earth candidate",
        card.earth.earth_window_id,
        inputs.earth_dem,
        azimuth_deg,
        altitude_deg,
    )
    svg.text(
        20,
        605,
        f"Hillshade: sun azimuth {_num(azimuth_deg)} deg, altitude {_num(altitude_deg)} deg "
        "(engineering choice). Hatched: no valid elevation. Row 0 is north.",
        cls="muted",
    )
    return svg.render()


# --- axes ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Axis:
    lo: float
    hi: float
    start: float
    end: float
    log: bool

    def __call__(self, value: float) -> float:
        a, b, v = self.lo, self.hi, value
        if self.log:
            a, b, v = math.log10(a), math.log10(b), math.log10(v)
        return self.start + (v - a) / (b - a) * (self.end - self.start)


def _log_axis(values: Iterable[float], start: float, end: float) -> _Axis:
    logs = [math.log10(v) for v in values]
    lo, hi = min(logs), max(logs)
    pad = max(0.1, 0.08 * (hi - lo))
    return _Axis(10 ** (lo - pad), 10 ** (hi + pad), start, end, log=True)


def _log_ticks(axis: _Axis) -> list[float]:
    ticks: list[float] = []
    span = math.log10(axis.hi) - math.log10(axis.lo)
    mantissas = (
        (1.0,) if span > 2.5 else (1.0, 2.0, 5.0) if span > 1.2 else (1.0, 2.0, 3.0, 5.0, 7.0)
    )
    for exponent in range(math.floor(math.log10(axis.lo)), math.ceil(math.log10(axis.hi)) + 1):
        for mantissa in mantissas:
            value = float(f"{mantissa * 10.0**exponent:.12g}")
            if axis.lo <= value <= axis.hi:
                ticks.append(value)
    return ticks


def _linear_axis(values: Iterable[float], start: float, end: float) -> _Axis:
    data = list(values)
    lo, hi = min(data), max(data)
    pad = 0.05 * (hi - lo) if hi > lo else max(0.05 * abs(hi), 0.05)
    return _Axis(lo - pad, hi + pad, start, end, log=False)


def _linear_ticks(axis: _Axis, count: int = 5) -> list[tuple[float, str]]:
    raw = (axis.hi - axis.lo) / count
    exponent = math.floor(math.log10(raw))
    step = min(
        (m * 10.0**exponent for m in (1.0, 2.0, 5.0, 10.0) if m * 10.0**exponent >= raw),
        default=10.0 ** (exponent + 1),
    )
    decimals = max(0, -math.floor(math.log10(step) + 1e-9))
    ticks: list[tuple[float, str]] = []
    k = math.ceil(axis.lo / step - 1e-9)
    while k * step <= axis.hi + 1e-12:
        value = round(k * step, decimals + 2)
        ticks.append((value, f"{value:.{decimals}f}"))
        k += 1
    return ticks


def _frame(
    svg: _Svg,
    x: _Axis,
    y: _Axis,
    x_ticks: Sequence[tuple[float, str]],
    y_ticks: Sequence[tuple[float, str]],
    x_label: str,
    y_label: str,
) -> None:
    for value, label in x_ticks:
        px = x(value)
        svg.line(px, y.end, px, y.start, GRID, 0.6)
        svg.text(px, y.start + 16, label, anchor="middle")
    for value, label in y_ticks:
        py = y(value)
        svg.line(x.start, py, x.end, py, GRID, 0.6)
        svg.text(x.start - 6, py + 4, label, anchor="end")
    svg.rect(x.start, y.end, x.end - x.start, y.start - y.end, fill="none", stroke=INK)
    svg.text((x.start + x.end) / 2, y.start + 34, x_label, anchor="middle")
    svg.text(x.start - 44, (y.start + y.end) / 2, y_label, anchor="middle", rotate=True)


def _tick_labels(values: Sequence[float]) -> list[tuple[float, str]]:
    return [(value, f"{value:g}") for value in values]


# --- figure B ------------------------------------------------------------------------------------

_Series = list[tuple[float, float | None]]


def _nu_series(entry: WindowEntry, which: Literal["relief", "texture"]) -> _Series | str:
    if entry.terrain is None or entry.terrain.terrain is None:
        return _unavailable(entry.reasons[0] if entry.reasons else None, "no_terrain_fingerprint")
    variant: TerrainVariant = getattr(entry.terrain.terrain, which)
    series = sorted((float(lag), nu) for lag, nu in variant.nu_m.items())
    if not any(nu is not None for _, nu in series):
        reasons = [lag.reason for lag in variant.unsupported_lags]
        return _unavailable(reasons[0] if reasons else None, "no_supported_lags")
    return series


def _draw_series(
    svg: _Svg, x: _Axis, y: _Axis, series: _Series, colour: str, dash: str, marker: str
) -> None:
    run: list[tuple[float, float]] = []
    runs: list[list[tuple[float, float]]] = []
    for lag, nu in series:
        if nu is None or nu <= 0:  # missing is never zero, and log axes need a positive value
            if run:
                runs.append(run)
            run = []
            continue
        run.append((x(lag), y(nu)))
    if run:
        runs.append(run)
    for points in runs:
        if len(points) > 1:
            svg.add(
                f'<path d="M{"L".join(f"{_f(px)} {_f(py)}" for px, py in points)}" '
                f'fill="none" stroke="{colour}" '
                f'stroke-width="2" stroke-dasharray="{dash}"/>'
            )
        for px, py in points:
            if marker == "circle":
                svg.add(f'<circle cx="{_f(px)}" cy="{_f(py)}" r="3.5" fill="{colour}"/>')
            else:
                svg.rect(px - 3.5, py - 3.5, 7, 7, fill=colour)


def _interval(
    svg: _Svg, axis: _Axis, y: float, lo: float, mid: float, hi: float, colour: str
) -> None:
    svg.rect(axis(lo), y - 4, max(axis(hi) - axis(lo), 1.0), 8, fill=colour)
    svg.line(axis(mid), y - 7, axis(mid), y + 7, INK, 1.5)


def _distance_strip(
    svg: _Svg,
    x0: float,
    y0: float,
    width: float,
    family: Family,
    distance: DistanceEntry,
    envelope: UncertaintyComponent,
) -> None:
    clone = envelope.by_family.get(family) if envelope.by_family else None
    clone_ok = (
        envelope.status == "available"
        and clone is not None
        and clone.spread_min is not None
        and clone.spread_median is not None
        and clone.spread_max is not None
    )
    values: list[float] = []
    if distance.value is not None:
        values.append(distance.value)
    if distance.reference_spread is not None:
        values += [distance.reference_spread.min, distance.reference_spread.max]
    if clone_ok and clone is not None:
        values += [clone.spread_min or 0.0, clone.spread_max or 0.0]
    if distance.status == "available" and distance.value is not None:
        svg.text(x0, y0, f"Distance ({distance.unit}): {_num(distance.value)}", cls="sub")
    else:
        svg.text(x0, y0, f"Distance: {_unavailable(distance.reason, distance.status)}", cls="sub")
    axis = _Axis(0.0, max(values) * 1.1 if values else 1.0, x0 + 150, x0 + width, log=False)
    rows = (("Reference spread", y0 + 24), ("Clone envelope", y0 + 58))
    for label, y in rows:
        svg.text(x0, y + 4, label)
    spread = distance.reference_spread
    if spread is not None:
        _interval(svg, axis, rows[0][1], spread.min, spread.median, spread.max, SPREAD_COLOUR)
        svg.text(
            axis.start,
            rows[0][1] + 20,
            f"{_num(spread.min)} to {_num(spread.max)}, median {_num(spread.median)}, n {spread.n}",
            cls="muted",
        )
    else:
        svg.text(axis.start, rows[0][1] + 4, _unavailable(distance.reference_spread_reason, "none"))
    if clone_ok and clone is not None:
        lo, mid, hi = clone.spread_min or 0.0, clone.spread_median or 0.0, clone.spread_max or 0.0
        _interval(svg, axis, rows[1][1], lo, mid, hi, ENVELOPE_COLOUR)
        n = f", n {clone.n}" if clone.n is not None else ""
        svg.text(
            axis.start,
            rows[1][1] + 20,
            f"{_num(lo)} to {_num(hi)}, median {_num(mid)}{n}",
            cls="muted",
        )
    else:
        reason = envelope.reason if envelope.status != "available" else f"no_{family}_clones"
        svg.text(axis.start, rows[1][1] + 4, _unavailable(reason, "none"))
    if distance.value is not None and distance.status == "available":
        px = axis(distance.value)
        svg.add(
            f'<path d="M{_f(px)} {_f(y0 + 10)}L{_f(px - 5)} {_f(y0 + 2)}L{_f(px + 5)} '
            f'{_f(y0 + 2)}Z" fill="{TARGET_COLOUR}"/>'
        )
        svg.line(px, y0 + 10, px, y0 + 64, TARGET_COLOUR, 1)
    base = y0 + 92
    svg.line(axis.start, base, axis.end, base, INK, 1)
    for value, label in _linear_ticks(axis, 4):
        svg.line(axis(value), base, axis(value), base + 4, INK, 1)
        svg.text(axis(value), base + 16, label, anchor="middle", cls="muted")


def render_figure_b(inputs: CardInputs) -> bytes:
    card, comparison = inputs.card, inputs.comparison
    svg = _Svg(
        960,
        660,
        _prefix(card, "B"),
        "Figure B: nu(L) for the target and the Earth candidate, relief and texture",
        "Log-log structure-function curves nu(L) in metres against lag L in metres for the "
        "target and Earth windows, relief (no detrend) and texture (plane removed), with each "
        "terrain distance, its reference spread and the clone envelope when they are available.",
    )
    _heading(svg, card, "B", "nu(L), relief and texture")
    distances = comparison.distances_by_family
    envelope = comparison.uncertainty_components.measurement_error
    panels: tuple[tuple[Literal["relief", "texture"], Family, DistanceEntry, float], ...] = (
        ("relief", "terrain_relief", distances.terrain_relief, 90.0),
        ("texture", "terrain_texture", distances.terrain_texture, 570.0),
    )
    for which, family, distance, x0 in panels:
        x_axis_start, x_axis_end, top, bottom = x0, x0 + 330, 110.0, 400.0
        heading = "Relief (no detrend)" if which == "relief" else "Texture (plane removed)"
        svg.text(x0, 88, heading, cls="sub")
        target = _nu_series(inputs.target_entry, which)
        earth = _nu_series(inputs.earth_entry, which)
        available = [s for s in (target, earth) if not isinstance(s, str)]
        points = [(lag, nu) for s in available for lag, nu in s if nu is not None and nu > 0]
        if not points:
            svg.rect(x0, top, 330, bottom - top, fill="#f2f2f2", stroke=GRID)
            svg.text(
                x0 + 165, (top + bottom) / 2, _unavailable(None, "no_nu_values"), anchor="middle"
            )
        else:
            x = _log_axis((p[0] for p in points), x_axis_start, x_axis_end)
            y = _log_axis((p[1] for p in points), bottom, top)
            _frame(
                svg,
                x,
                y,
                _tick_labels(_log_ticks(x)),
                _tick_labels(_log_ticks(y)),
                "Lag L (m)",
                "nu(L) (m)",
            )
            if not isinstance(target, str):
                _draw_series(svg, x, y, target, TARGET_COLOUR, "none", "circle")
            if not isinstance(earth, str):
                _draw_series(svg, x, y, earth, EARTH_COLOUR, "6 3", "square")
        legend_y = 450.0
        for label, colour, series, dash in (
            (f"Target {card.target.target_window_id}", TARGET_COLOUR, target, "none"),
            (f"Earth {card.earth.earth_window_id}", EARTH_COLOUR, earth, "6 3"),
        ):
            svg.add(
                f'<path d="M{_f(x0)} {_f(legend_y)}L{_f(x0 + 28)} {_f(legend_y)}" '
                f'stroke="{colour}" stroke-width="2" stroke-dasharray="{dash}"/>'
            )
            suffix = f" ({series})" if isinstance(series, str) else ""
            svg.text(x0 + 34, legend_y + 4, label + suffix)
            legend_y += 18
        _distance_strip(svg, x0 - 60, 510, 390, family, distance, envelope)
    svg.text(
        20,
        645,
        "Missing nu values are left out, never drawn as zero. Values from the windows file and "
        f"the comparison record (run {comparison.run_id[:19]}).",
        cls="muted",
    )
    return svg.render()


# --- figure C ------------------------------------------------------------------------------------


def _radar(entry: WindowEntry, preferred: Sequence[str]) -> RadarFingerprint | str:
    candidates = [
        fingerprint.radar
        for fingerprint in entry.radar
        if fingerprint.radar is not None
        and fingerprint.radar.cpr_ratio_of_mean_powers is not None
        and fingerprint.radar.cpr_ratio_of_mean_powers.n > 0
    ]
    if not candidates:
        reasons = [f.radar.reason for f in entry.radar if f.radar is not None and f.radar.reason]
        return _unavailable(reasons[0] if reasons else None, "no_radar_fingerprint")
    candidates.sort(key=lambda radar: (radar.product_id not in preferred, radar.product_id))
    return candidates[0]


def _cdf(svg: _Svg, x: _Axis, y: _Axis, values: Sequence[float], colour: str, dash: str) -> None:
    n = len(values)
    path = [f"M{_f(x.start)} {_f(y(0.0))}", f"L{_f(x(values[0]))} {_f(y(0.0))}"]
    for i, value in enumerate(values):
        path.append(f"L{_f(x(value))} {_f(y(i / n))}")
        path.append(f"L{_f(x(value))} {_f(y((i + 1) / n))}")
    path.append(f"L{_f(x.end)} {_f(y(1.0))}")
    svg.add(
        f'<path class="cdf" d="{"".join(path)}" fill="none" stroke="{colour}" '
        f'stroke-width="2" stroke-dasharray="{dash}"/>'
    )


def _radar_label(prefix: str, radar: RadarFingerprint) -> str:
    return (
        f"{prefix}: {radar.band}-band, {radar.product_id}, {_num(radar.support_m)} m blocks, "
        f"n {radar.cpr_ratio_of_mean_powers.n if radar.cpr_ratio_of_mean_powers else 0}"
    )


def _cdf_values(radar: RadarFingerprint) -> list[float]:
    distribution = radar.cpr_ratio_of_mean_powers
    return list(distribution.values) if distribution is not None else []


def _unavailable_panel(svg: _Svg, x0: float, y0: float, w: float, h: float, text: str) -> None:
    svg.rect(x0, y0, w, h, fill="#f2f2f2", stroke=GRID)
    svg.text(x0 + w / 2, y0 + h / 2, text, anchor="middle", cls="sub")


def render_figure_c(inputs: CardInputs) -> bytes:
    card, comparison = inputs.card, inputs.comparison
    mode = card.shipping_mode
    prefix = _prefix(card, "C")
    preferred = comparison.source_ids
    target = _radar(inputs.target_entry, preferred)
    earth = _radar(inputs.earth_entry, preferred)
    if mode == "C":
        reason = card.distances_by_family.radar_cpr.reason or "shipping_mode_c"
        svg = _Svg(
            960,
            300,
            prefix,
            "Figure C: radar comparison unavailable",
            f"Mode C card: the radar comparison is unavailable ({reason}).",
        )
        _heading(svg, card, "C", "radar block distributions")
        _unavailable_panel(svg, 20, 70, 920, 180, f"Unavailable: {reason}")
        svg.text(20, 280, "Mode C: terrain evidence only (PRD 4).", cls="muted")
        return svg.render()

    if mode == "B":
        svg = _Svg(
            960,
            520,
            prefix,
            "Figure C: radar block CPR distributions side by side",
            "Mode B card: the target and Earth radar block CPR distributions are shown side by "
            "side as cumulative fractions with their band labels. They are radar context and "
            "are not compared.",
        )
        _heading(svg, card, "C", "radar block CPR, side by side (context, not compared)")
        for side, radar, x0, colour, dash in (
            ("Target", target, 90.0, TARGET_COLOUR, "none"),
            ("Earth", earth, 570.0, EARTH_COLOUR, "6 3"),
        ):
            if isinstance(radar, str):
                _unavailable_panel(svg, x0, 110, 330, 290, radar)
                svg.text(x0, 88, side, cls="sub")
                continue
            values = _cdf_values(radar)
            x = _linear_axis(values, x0, x0 + 330)
            y = _Axis(0.0, 1.0, 400.0, 110.0, log=False)
            svg.text(x0, 88, _radar_label(side, radar), cls="sub")
            _frame(
                svg,
                x,
                y,
                _linear_ticks(x),
                [(v, f"{v:.2f}") for v in (0.0, 0.25, 0.5, 0.75, 1.0)],
                "CPR, block ratio of mean powers",
                "Cumulative fraction",
            )
            _cdf(svg, x, y, values, colour, dash)
        svg.text(
            20,
            500,
            "Mode B: different radar bands or unresolved calibration; shown as context (PRD 4).",
            cls="muted",
        )
        return svg.render()

    distance = comparison.distances_by_family.radar_cpr
    svg = _Svg(
        960,
        540,
        prefix,
        "Figure C: overlaid cumulative radar block CPR distributions",
        "Mode A card: cumulative distributions of the target and Earth radar block CPR, with "
        "the Wasserstein-1 value taken from the comparison record.",
    )
    _heading(svg, card, "C", "radar block CPR, overlaid cumulative distributions")
    if distance.status == "available" and distance.value is not None:
        svg.text(
            90,
            80,
            f"Wasserstein-1 from the comparison: {_num(distance.value)} ({distance.unit})",
            cls="sub",
        )
    else:
        svg.text(
            90, 80, f"Wasserstein-1: {_unavailable(distance.reason, distance.status)}", cls="sub"
        )
    if isinstance(target, str) or isinstance(earth, str):
        missing = target if isinstance(target, str) else earth
        _unavailable_panel(svg, 90, 110, 800, 300, str(missing))
        return svg.render()
    x = _linear_axis(_cdf_values(target) + _cdf_values(earth), 90, 890)
    y = _Axis(0.0, 1.0, 410.0, 110.0, log=False)
    _frame(
        svg,
        x,
        y,
        _linear_ticks(x),
        [(v, f"{v:.2f}") for v in (0.0, 0.25, 0.5, 0.75, 1.0)],
        "CPR, block ratio of mean powers",
        "Cumulative fraction",
    )
    _cdf(svg, x, y, _cdf_values(target), TARGET_COLOUR, "none")
    _cdf(svg, x, y, _cdf_values(earth), EARTH_COLOUR, "6 3")
    legend_y = 470.0
    for label, colour, dash in (
        (_radar_label("Target", target), TARGET_COLOUR, "none"),
        (_radar_label("Earth", earth), EARTH_COLOUR, "6 3"),
    ):
        svg.add(
            f'<path d="M90 {_f(legend_y)}L118 {_f(legend_y)}" stroke="{colour}" '
            f'stroke-width="2" stroke-dasharray="{dash}"/>'
        )
        svg.text(124, legend_y + 4, label)
        legend_y += 18
    return svg.render()


# --- inputs --------------------------------------------------------------------------------------


def _read_json_file(path: Path, root: Path, code: str, blocked: list[str]) -> bytes | None:
    if not path.is_file():
        blocked.append(f"{code} {path.relative_to(root).as_posix()}")
        return None
    return path.read_bytes()


def load_dem(path: Path) -> DemWindow:
    """Read a DEM cache: ``elevation_m`` (2D float), ``spacing_m`` and ``product_id``."""
    with np.load(path, allow_pickle=False) as data:
        try:
            elevation = np.asarray(data["elevation_m"], dtype=np.float64)
            spacing = float(np.asarray(data["spacing_m"]).item())
            product = str(np.asarray(data["product_id"]).item())
        except (KeyError, ValueError, TypeError) as error:
            raise FigureInputError(f"{path.name}: {error}") from None
    if elevation.ndim != 2 or not product:
        raise FigureInputError(f"{path.name}: elevation_m must be 2D and product_id non-empty")
    return DemWindow(elevation, spacing, product)


def _entry(entries: Sequence[WindowEntry], window_id: str, source: str) -> WindowEntry:
    for entry in entries:
        if entry.window.window_id == window_id:
            return entry
    raise FigureInputError(f"{source} has no window {window_id}")


def load_inputs(root: Path, card: CardRecord) -> CardInputs:
    """Read a card's comparison, window entries and DEM caches; :class:`Blocked` if absent."""
    blocked: list[str] = []
    target_id = card.target.target_id
    target_window, earth_window = card.target.target_window_id, card.earth.earth_window_id
    comparison_path = root / "outputs" / "comparisons" / f"{target_id}.json"
    comparison_raw = _read_json_file(comparison_path, root, "comparison_missing", blocked)
    windows_raw: dict[str, bytes | None] = {}
    for body in sorted({card.target.body, "earth"}):
        path = root / "data" / "processed" / "windows" / f"{body}.json"
        windows_raw[body] = _read_json_file(path, root, "windows_file_missing", blocked)
    dem_paths = [
        root / "data" / "processed" / "dem_windows" / f"{window_id}.npz"
        for window_id in (target_window, earth_window)
    ]
    for path in dem_paths:
        if not path.is_file():
            blocked.append(f"dem_cache_missing {path.relative_to(root).as_posix()}")
    if blocked:
        raise Blocked(tuple(blocked))
    assert comparison_raw is not None
    try:
        comparisons = _COMPARISONS.validate_json(comparison_raw)
        windows = {body: _WINDOWS.validate_json(raw or b"") for body, raw in windows_raw.items()}
    except ValidationError as error:
        raise FigureInputError(f"invalid input record for {card.card_id}: {error}") from None
    matches = [
        c
        for c in comparisons
        if (c.target_window_id, c.earth_window_id) == (target_window, earth_window)
    ]
    if len(matches) != 1:
        raise FigureInputError(
            f"{comparison_path.name}: expected one record for {card.card_id}, found {len(matches)}"
        )
    return CardInputs(
        card=card,
        comparison=matches[0],
        target_entry=_entry(windows[card.target.body], target_window, f"{card.target.body}.json"),
        earth_entry=_entry(windows["earth"], earth_window, "earth.json"),
        target_dem=load_dem(dem_paths[0]),
        earth_dem=load_dem(dem_paths[1]),
    )


def render_card(
    root: Path,
    card: CardRecord,
    *,
    azimuth_deg: float = DEFAULT_AZIMUTH_DEG,
    altitude_deg: float = DEFAULT_ALTITUDE_DEG,
) -> dict[FigureLetter, bytes]:
    """Render figures A, B and C of one card from the inputs under ``root``."""
    inputs = load_inputs(root, card)
    return {
        "A": render_figure_a(inputs, azimuth_deg=azimuth_deg, altitude_deg=altitude_deg),
        "B": render_figure_b(inputs),
        "C": render_figure_c(inputs),
    }


def load_cards(root: Path) -> list[CardRecord]:
    directory = root / "outputs" / "cards"
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not paths:
        raise Blocked((f"no_cards {(directory.relative_to(root)).as_posix()}",))
    cards: list[CardRecord] = []
    for path in paths:
        try:
            cards.append(CardRecord.model_validate_json(path.read_bytes()))
        except ValidationError as error:
            raise FigureInputError(f"{path.name}: {error}") from None
    return cards


def figure_path(card_id: str, letter: FigureLetter) -> str:
    """The bundle path of a figure, as the index and the card's FigureRef name it."""
    return f"figures/{card_id}_{letter}.svg"


def render_all(
    root: Path, command: str, azimuth_deg: float, altitude_deg: float
) -> tuple[dict[str, bytes], list[dict[str, object]]]:
    """Render every card's figures; returns bytes keyed by bundle path and the index entries."""
    blocked: list[str] = []
    rendered: dict[str, bytes] = {}
    index: list[dict[str, object]] = []
    for card in load_cards(root):
        try:
            figures = render_card(root, card, azimuth_deg=azimuth_deg, altitude_deg=altitude_deg)
        except Blocked as error:
            blocked += error.items
            continue
        for letter in LETTERS:
            data = figures[letter]
            path = figure_path(card.card_id, letter)
            rendered[path] = data
            index.append(
                {
                    "card_id": card.card_id,
                    "figure": letter,
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "command": command,
                }
            )
    if blocked:
        raise Blocked(tuple(blocked))
    return rendered, index


def _figure_file(root: Path, path: str) -> Path:
    return root / "data" / "processed" / path


def _index_file(root: Path) -> Path:
    return root / "outputs" / "figures" / "index.json"


def check(root: Path, rendered: dict[str, bytes], index: list[dict[str, object]]) -> list[str]:
    """Compare a fresh render with the committed index and the figure files on disk."""
    index_path = _index_file(root)
    if not index_path.is_file():
        return [f"index missing: {index_path.relative_to(root).as_posix()}"]
    try:
        listed = json.loads(index_path.read_bytes())
    except ValueError as error:
        return [f"index is not JSON: {error}"]
    if not isinstance(listed, list) or not all(isinstance(e, dict) for e in listed):
        return ["index must be a list of objects"]
    problems: list[str] = []
    by_path = {str(entry.get("path")): entry for entry in listed}
    fresh = {str(entry["path"]): entry for entry in index}
    for path in sorted(set(by_path) - set(fresh)):
        problems.append(f"drifted: {path} is in the index but no card renders it")
    for path, entry in sorted(fresh.items()):
        old = by_path.get(path)
        if old is None:
            problems.append(f"drifted: {path} is rendered but not in the index")
            continue
        if old != entry:
            problems.append(f"drifted: {path} differs from its index entry")
        on_disk = _figure_file(root, path)
        if not on_disk.is_file():
            problems.append(f"drifted: {path} is not rendered on disk")
        elif hashlib.sha256(on_disk.read_bytes()).hexdigest() != old.get("sha256"):
            problems.append(f"drifted: {path} on disk does not match its index sha256")
    if not problems and listed != index:
        problems.append("drifted: index order or content differs from a fresh render")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m terraluna_pipeline.figures",
        description="Render the evidence card figures A to C as SVG (PRD 10).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    cards = commands.add_parser("cards", help="render figures for every card in outputs/cards")
    cards.add_argument("--check", action="store_true", help="recompute and compare; write nothing")
    cards.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help="repository root")
    cards.add_argument("--azimuth", type=float, default=DEFAULT_AZIMUTH_DEG, help="sun azimuth")
    cards.add_argument("--altitude", type=float, default=DEFAULT_ALTITUDE_DEG, help="sun altitude")
    args = parser.parse_args(argv)
    root: Path = args.root
    azimuth, altitude = float(args.azimuth), float(args.altitude)
    if not (0.0 <= azimuth <= 360.0 and 0.0 <= altitude <= 90.0):
        parser.error("azimuth must be in [0, 360] and altitude in [0, 90]")
    command = COMMAND
    if azimuth != DEFAULT_AZIMUTH_DEG or altitude != DEFAULT_ALTITUDE_DEG:
        command += f" --azimuth {_num(azimuth)} --altitude {_num(altitude)}"
    try:
        rendered, index = render_all(root, command, azimuth, altitude)
    except Blocked as error:
        print(f"blocked: {', '.join(error.items)}")
        return 3
    except FigureInputError as error:
        print(f"invalid input: {error}")
        return 1
    if args.check:
        problems = check(root, rendered, index)
        for problem in problems:
            print(problem)
        if problems:
            return 1
        print(f"figures match the index: {len(index)} figure(s)")
        return 0
    for path, data in rendered.items():
        target = _figure_file(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    index_path = _index_file(root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(canonical_json(index))
    print(f"wrote {len(index)} figure(s) and {index_path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
