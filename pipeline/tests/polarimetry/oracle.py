"""Real-scene oracle check of the polarimetry code against polsartools (T2-07).

PRD 12 ("Added to T1 and T2") asks T2 to check the team's covariance-to-circular and m-chi code
against polsartools on one real UAVSAR scene; PRD 6.2 and D19 make polsartools (GPL-3.0) the
independent, development-only oracle for G1 (PRD 9). This module holds the test-only helpers of
``pipeline/tests/test_polarimetry_oracle.py``. polsartools is imported only here, through
:func:`import_polsartools` at test time; no module under ``pipeline/src`` imports it.

What the check reads, and from where:

- the scene directory from the environment variable :data:`SCENE_ENV`. T1 has not fixed a data
  path, so a person or T1 sets it. Without a scene the check fails with ``data not present``
  and the path it looked at; nothing stands in for the scene;
- the six UAVSAR GRD products (HHHH, HVHV, VVVV real; HHHV, HHVV, HVVV complex) and the
  ``.ann`` annotation, with every layout fact (rows, columns, bytes per value, value format,
  byte order, header and tail bytes, file names) and every unit taken from the annotation
  (PRD 5). Angles go through story 1's :func:`angle_to_degrees` with the unit the annotation
  states;
- the comparison tolerance from :data:`TOLERANCE_PATH`. A person decides it before the
  comparison runs; until that file exists the check fails with ``tolerance not declared``;
- the polsartools conventions from :data:`CONVENTIONS_PATH`: the vector ordering, ellipticity
  and Stokes-sign mapping, derived from the polsartools source before any comparison ran.
  A difference that comes from a convention is reported as one, and resolving it means
  declaring the convention there; the code never searches for the sign that makes numbers
  agree.

The outcome is the actual one. Each compared field reports its pixel counts and largest
difference, and :func:`write_run_record` writes ``fixtures/polarimetry/oracle_run.json`` from
the run itself.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import json
import math
import platform
import re
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from terraluna_pipeline.polarimetry import (
    COVARIANCE_HH_HV_VV,
    COVARIANCE_INPUT,
    NEISH_SC_LL_CONVENTION,
    OPPOSITE_SC_CONVENTION,
    CovarianceCircularPowers,
    CovarianceInput,
    HandednessConvention,
    ObsState,
    StatedArray,
    Tolerance,
    angle_to_degrees,
    derive_circular,
    hybrid_stokes_from_covariance,
    m_chi_from_hybrid,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
FIXTURES: Final = REPO_ROOT / "fixtures" / "polarimetry"
TOLERANCE_PATH: Final = FIXTURES / "oracle_tolerance.json"
"""The comparison tolerance a person decided. The check fails until it exists."""
CONVENTIONS_PATH: Final = FIXTURES / "oracle_conventions.json"
RUN_RECORD_PATH: Final = FIXTURES / "oracle_run.json"

CODE_FILES: Final = (
    "pipeline/src/terraluna_pipeline/polarimetry.py",
    "pipeline/tests/polarimetry/oracle.py",
    "pipeline/tests/test_polarimetry_oracle.py",
)
"""The code the run record names by sha256: our polarimetry, this helper and the test."""

SCENE_ENV: Final = "TERRALUNA_UAVSAR_SCENE"
"""The environment variable naming the UAVSAR scene directory (open: T1 has not fixed a data
path)."""

REAL_PRODUCTS: Final = ("HHHH", "HVHV", "VVVV")
COMPLEX_PRODUCTS: Final = ("HHHV", "HHVV", "HVVV")
PRODUCTS: Final = REAL_PRODUCTS + COMPLEX_PRODUCTS

TERRALUNA_CHI_TX: Final = (-45.0, 45.0)
"""Our declared transmit ellipticities, in degrees (``TRANSMIT_ELLIPTICITIES``)."""


def _hybrid_field(name: str, chi_tx: float) -> str:
    return f"{name}@chi_tx={chi_tx:+.0f}"


POWER_FIELDS: Final = ("sc_ll", "oc_ll", "sc_opp", "oc_opp", "base_sc")
"""Circular powers: SC and OC under the SC_LL (NEISH) and SC_opp conventions, and base_SC."""
STOKES_FIELDS: Final = tuple(
    _hybrid_field(name, chi) for chi in TERRALUNA_CHI_TX for name in ("s1", "s2", "s3", "s4")
)
M_FIELDS: Final = tuple(_hybrid_field("m", chi) for chi in TERRALUNA_CHI_TX)
COMPARED_FIELDS: Final = POWER_FIELDS + STOKES_FIELDS + M_FIELDS
"""Every field the check compares; the tolerance must cover each exactly once."""


# --- Why the check cannot run ----------------------------------------------------------------


class OracleNotReady(Exception):
    """The check cannot compare anything yet. The test fails with this message."""


class DataNotPresent(OracleNotReady):
    """No usable scene at the path the check looked at."""


class ToleranceNotDeclared(OracleNotReady):
    """No decided comparison tolerance."""


class ConventionsNotDeclared(OracleNotReady):
    """No valid polsartools convention declaration, or one made for other polsartools code."""


class OracleNotInstalled(OracleNotReady):
    """polsartools (with the GDAL Python bindings it needs) is not importable."""


class SceneLayoutError(ValueError):
    """The annotation and the files disagree, or the annotation lacks a layout fact."""


# --- The annotation (PRD 5) ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnnotationEntry:
    """One ``key (unit) = value ; comment`` line of a UAVSAR annotation."""

    key: str
    unit: str | None
    value: str
    comment: str | None


_WITH_UNIT: Final = re.compile(
    r"^(?P<key>\S.*?)\s+\((?P<unit>[^()]*)\)\s*=\s*(?P<value>[^;]*?)\s*(?:;(?P<comment>.*))?$"
)
_WITHOUT_UNIT: Final = re.compile(
    r"^(?P<key>[^=;()]*[^=;()\s])\s*=\s*(?P<value>[^;]*?)\s*(?:;(?P<comment>.*))?$"
)


def parse_annotation(text: str) -> dict[str, list[AnnotationEntry]]:
    """Parse annotation lines into entries by key, keeping every occurrence in order.

    Lines starting with ``;`` and blank lines are comments. A key may itself contain a
    parenthesis (``Steering Angle (90 is Boresite) (deg) = 90.0``): the unit is the last
    parenthesized group before ``=``. Lines that are neither form are ignored.
    """
    entries: dict[str, list[AnnotationEntry]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        match = _WITH_UNIT.match(line) or _WITHOUT_UNIT.match(line)
        if match is None:
            continue
        groups = match.groupdict()
        comment = groups.get("comment")
        entry = AnnotationEntry(
            key=groups["key"].strip(),
            unit=groups["unit"].strip() if groups.get("unit") is not None else None,
            value=groups["value"].strip(),
            comment=comment.strip() if comment is not None else None,
        )
        entries.setdefault(entry.key, []).append(entry)
    return entries


class Annotation:
    """Typed access to annotation entries, failing loudly on a missing or ambiguous key."""

    def __init__(self, entries: Mapping[str, Sequence[AnnotationEntry]]) -> None:
        self._entries = {key: tuple(values) for key, values in entries.items()}

    def entry(self, key: str) -> AnnotationEntry:
        found = self._entries.get(key, ())
        if not found:
            raise SceneLayoutError(f"the annotation has no {key!r} entry")
        if len({(e.unit, e.value) for e in found}) > 1:
            raise SceneLayoutError(f"the annotation gives {key!r} more than one value")
        return found[0]

    def text(self, key: str, *, unit: str | None) -> str:
        entry = self.entry(key)
        if entry.unit != unit:
            raise SceneLayoutError(
                f"the annotation states {key!r} in {entry.unit!r}, expected {unit!r}"
            )
        return entry.value

    def integer(self, key: str, *, unit: str) -> int:
        value = self.text(key, unit=unit)
        if not re.fullmatch(r"[+-]?\d+", value):
            raise SceneLayoutError(f"the annotation's {key!r} is not an integer: {value!r}")
        return int(value)

    def angle_degrees(self, key: str) -> float:
        """An angle converted with story 1 from the unit the annotation states."""
        entry = self.entry(key)
        units: dict[str | None, Literal["radians", "degrees"]] = {
            "deg": "degrees",
            "rad": "radians",
        }
        if entry.unit not in units:
            raise SceneLayoutError(f"the annotation's {key!r} is not an angle: {entry.unit!r}")
        degrees = angle_to_degrees(np.array([float(entry.value)]), unit=units[entry.unit])
        if degrees.state[0] != ObsState.VALID:
            raise SceneLayoutError(f"the annotation's {key!r} is not a valid angle")
        return float(degrees.value[0])

    def angle_step_degrees(self, key: str) -> float:
        """A per-pixel angle step (``deg/pixel`` or ``rad/pixel``), converted with story 1."""
        entry = self.entry(key)
        units: dict[str | None, Literal["radians", "degrees"]] = {
            "deg/pixel": "degrees",
            "rad/pixel": "radians",
        }
        if entry.unit not in units:
            raise SceneLayoutError(f"the annotation's {key!r} is not an angle step: {entry.unit!r}")
        degrees = angle_to_degrees(np.array([float(entry.value)]), unit=units[entry.unit])
        if degrees.state[0] != ObsState.VALID:
            raise SceneLayoutError(f"the annotation's {key!r} is not a valid angle step")
        return float(degrees.value[0])


@dataclass(frozen=True, slots=True)
class GrdLayout:
    """The GRD layout stated by the annotation (PRD 5)."""

    rows: int
    cols: int
    real_dtype: str
    complex_dtype: str
    header_bytes: int
    tail_bytes: int
    files: Mapping[str, str]
    stated_sizes: Mapping[str, int | None]
    upper_left_lat_deg: float
    upper_left_lon_deg: float
    lat_step_deg: float
    lon_step_deg: float
    acquisition: str
    look_direction: str

    def dtype(self, product: str) -> str:
        return self.real_dtype if product in REAL_PRODUCTS else self.complex_dtype

    def file_bytes(self, product: str) -> int:
        size = np.dtype(self.dtype(product)).itemsize
        return self.header_bytes + self.rows * self.cols * size + self.tail_bytes


_FILE_SIZE: Final = re.compile(r"File Size\s+(\d+)\s+bytes")


def read_layout(annotation: Annotation) -> GrdLayout:
    """The GRD layout from the annotation; every fact is read, none is assumed.

    Power products are ``grd_pwr`` (``REAL*4``, 4 bytes) and cross products ``grd_mag``
    (8 bytes: a complex value of two float32). Both must state the same grid.
    """
    rows = annotation.integer("grd_pwr.set_rows", unit="pixels")
    cols = annotation.integer("grd_pwr.set_cols", unit="pixels")
    if (rows, cols) != (
        annotation.integer("grd_mag.set_rows", unit="pixels"),
        annotation.integer("grd_mag.set_cols", unit="pixels"),
    ):
        raise SceneLayoutError("the annotation states different grids for grd_pwr and grd_mag")
    if rows <= 0 or cols <= 0:
        raise SceneLayoutError(f"the annotation states an empty grid {rows} x {cols}")
    endian = annotation.text("val_endi", unit="&")
    orders = {"LITTLE ENDIAN": "<", "BIG ENDIAN": ">"}
    if endian not in orders:
        raise SceneLayoutError(f"unknown byte order {endian!r}")
    order = orders[endian]
    pwr_format = annotation.text("grd_pwr.val_frmt", unit="&")
    pwr_size = annotation.integer("grd_pwr.val_size", unit="bytes")
    if (pwr_format, pwr_size) != ("REAL*4", 4):
        raise SceneLayoutError(f"grd_pwr is {pwr_format} with {pwr_size} bytes, not REAL*4")
    mag_size = annotation.integer("grd_mag.val_size", unit="bytes")
    if mag_size != 8:
        raise SceneLayoutError(f"grd_mag has {mag_size} bytes per value, not 8 (complex float32)")
    files: dict[str, str] = {}
    sizes: dict[str, int | None] = {}
    for product in PRODUCTS:
        entry = annotation.entry(f"grd{product}")
        if entry.unit is not None or not entry.value or "/" in entry.value:
            raise SceneLayoutError(f"the annotation's grd{product} is not a file name")
        files[product] = entry.value
        stated = _FILE_SIZE.search(entry.comment or "")
        sizes[product] = int(stated.group(1)) if stated else None
    return GrdLayout(
        rows=rows,
        cols=cols,
        real_dtype=f"{order}f4",
        complex_dtype=f"{order}c8",
        header_bytes=annotation.integer("set_hddr", unit="bytes"),
        tail_bytes=annotation.integer("set_tail", unit="bytes"),
        files=files,
        stated_sizes=sizes,
        upper_left_lat_deg=annotation.angle_degrees("grd_pwr.row_addr"),
        upper_left_lon_deg=annotation.angle_degrees("grd_pwr.col_addr"),
        lat_step_deg=annotation.angle_step_degrees("grd_pwr.row_mult"),
        lon_step_deg=annotation.angle_step_degrees("grd_pwr.col_mult"),
        acquisition=annotation.text("Date of Acquisition", unit="&"),
        look_direction=annotation.text("Look Direction", unit="&"),
    )


# --- The scene -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scene:
    directory: Path
    annotation_path: Path
    layout: GrdLayout

    @property
    def scene_id(self) -> str:
        return self.annotation_path.stem

    def path(self, product: str) -> Path:
        return self.directory / self.layout.files[product]


def locate_scene(environ: Mapping[str, str]) -> Scene:
    """The scene named by :data:`SCENE_ENV`, or :class:`DataNotPresent` saying where it looked.

    The directory must hold exactly one ``.ann`` file and the six GRD files it names, each of
    the size its layout implies (and the size the annotation states, where it does).
    """
    raw = environ.get(SCENE_ENV, "")
    if not raw.strip():
        raise DataNotPresent(
            f"data not present: {SCENE_ENV} is not set, so no scene path was looked at. Set it "
            "to the directory of one UAVSAR PolSAR GRD scene (its .ann file and six .grd files)."
        )
    directory = Path(raw)
    if not directory.is_dir():
        raise DataNotPresent(
            f"data not present: looked at {directory} (from {SCENE_ENV}), which is not a directory"
        )
    annotations = sorted(directory.glob("*.ann"))
    if not annotations:
        raise DataNotPresent(
            f"data not present: looked at {directory} (from {SCENE_ENV}) and found no .ann file"
        )
    if len(annotations) > 1:
        raise SceneLayoutError(f"{directory} holds more than one .ann file: {annotations}")
    annotation_path = annotations[0]
    text = annotation_path.read_text(encoding="latin-1")
    layout = read_layout(Annotation(parse_annotation(text)))
    scene = Scene(directory=directory, annotation_path=annotation_path, layout=layout)
    missing = [p for p in PRODUCTS if not scene.path(p).is_file()]
    if missing:
        names = ", ".join(f"{scene.path(p)} (grd{p})" for p in missing)
        raise DataNotPresent(
            f"data not present: looked at {directory} (from {SCENE_ENV}); missing {names}"
        )
    for product in PRODUCTS:
        actual = scene.path(product).stat().st_size
        expected = layout.file_bytes(product)
        stated = layout.stated_sizes[product]
        if actual != expected or (stated is not None and stated != actual):
            raise SceneLayoutError(
                f"{scene.path(product)} has {actual} bytes; the annotation's layout gives "
                f"{expected} and its file list states {stated}"
            )
    return scene


@dataclass(frozen=True, slots=True)
class Window:
    """A pixel window of the GRD grid: rows ``row0 .. row0 + rows - 1`` and the same for
    columns, zero-based."""

    row0: int
    col0: int
    rows: int
    cols: int

    def check(self, layout: GrdLayout) -> None:
        if min(self.rows, self.cols) <= 0 or min(self.row0, self.col0) < 0:
            raise ValueError(f"empty or negative window {self}")
        if self.row0 + self.rows > layout.rows or self.col0 + self.cols > layout.cols:
            raise ValueError(f"window {self} leaves the {layout.rows} x {layout.cols} grid")

    def as_record(self, layout: GrdLayout) -> dict[str, Any]:
        last_row = self.row0 + self.rows - 1
        last_col = self.col0 + self.cols - 1
        return {
            "row0": self.row0,
            "col0": self.col0,
            "rows": self.rows,
            "cols": self.cols,
            "indexing": (
                "zero-based GRD row and column of the first pixel; rows and cols are counts"
            ),
            "first_pixel_centre_lat_deg": layout.upper_left_lat_deg
            + self.row0 * layout.lat_step_deg,
            "first_pixel_centre_lon_deg": layout.upper_left_lon_deg
            + self.col0 * layout.lon_step_deg,
            "last_pixel_centre_lat_deg": layout.upper_left_lat_deg + last_row * layout.lat_step_deg,
            "last_pixel_centre_lon_deg": layout.upper_left_lon_deg + last_col * layout.lon_step_deg,
        }


def read_window(scene: Scene, product: str, window: Window) -> npt.NDArray[Any]:
    """One product's window, in its stored dtype (float32 or complex64), as a new array."""
    window.check(scene.layout)
    data = np.memmap(
        scene.path(product),
        dtype=np.dtype(scene.layout.dtype(product)),
        mode="r",
        offset=scene.layout.header_bytes,
        shape=(scene.layout.rows, scene.layout.cols),
    )
    rows = slice(window.row0, window.row0 + window.rows)
    cols = slice(window.col0, window.col0 + window.cols)
    return np.array(data[rows, cols], copy=True)


def covariance_window(scene: Scene, window: Window) -> npt.NDArray[np.complex128]:
    """The (HH, HV, VV) covariance per pixel, shape (rows, cols, 3, 3), as UAVSAR stores it.

    ``A = HHHH``, ``V = HVHV``, ``B = VVVV``, ``D = HHHV``, ``C = HHVV``, ``E = HVVV`` (PRD 6.2);
    the lower triangle is the conjugate of the upper one. float32 values widen to float64
    exactly. The window must lie inside the swath: a pixel with a zero, negative or non-finite
    power is refused, since the GRD files carry no validity flag to mask it with.
    """
    parts = {p: read_window(scene, p, window) for p in PRODUCTS}
    for product in REAL_PRODUCTS:
        values = parts[product]
        bad = ~(np.isfinite(values) & (values > 0))
        if np.any(bad):
            raise ValueError(
                f"the stated window {window} is not inside the swath: {int(bad.sum())} {product} "
                "pixels are zero, negative or not finite"
            )
    for product in COMPLEX_PRODUCTS:
        if not np.all(np.isfinite(parts[product])):
            raise ValueError(f"the stated window {window} has non-finite {product} values")
    cov = np.empty((window.rows, window.cols, 3, 3), dtype=np.complex128)
    cov[..., 0, 0] = parts["HHHH"]
    cov[..., 1, 1] = parts["HVHV"]
    cov[..., 2, 2] = parts["VVVV"]
    for (i, j), product in {(0, 1): "HHHV", (0, 2): "HHVV", (1, 2): "HVVV"}.items():
        value = parts[product].astype(np.complex128)
        cov[..., i, j] = value
        cov[..., j, i] = np.conj(value)
    return cov


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def stated_sums(directory: Path) -> dict[str, str]:
    """Hashes from a ``SHA256SUMS`` file beside the scene, if there is one."""
    path = directory / "SHA256SUMS"
    if not path.is_file():
        return {}
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line.strip())
        if match:
            sums[match.group(2)] = match.group(1)
    return sums


# --- Declarations: conventions and tolerance -------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EllipticityMapping(_Strict):
    polsartools_chi_deg: float
    terraluna_chi_tx_deg: float
    transmit_jones_hv: tuple[str, str]
    terraluna_handedness: HandednessConvention


class StokesMapping(_Strict):
    terraluna: Literal["s1", "s2", "s3", "s4"]
    polsartools: Literal["s0", "s1", "s2", "s3"]
    sign: Literal[1, -1]


class PolsartoolsProcessing(_Strict):
    psi_deg: float
    win: int = Field(ge=1)
    block_size: tuple[int, int]
    fmt: Literal["tif"]
    derivation: str = Field(min_length=40)


class OracleConventions(_Strict):
    """The polsartools conventions, declared from its source before any comparison."""

    description: str = Field(min_length=20)
    polsartools_version: str
    polsartools_sources_sha256: dict[str, str]
    declared_utc: str
    declared_before_any_comparison: Literal[True]
    c3_ordering: Literal["HH_SQRT2HV_VV"]
    c3_derivation: str = Field(min_length=40)
    ellipticity: tuple[EllipticityMapping, EllipticityMapping]
    ellipticity_derivation: str = Field(min_length=40)
    stokes: tuple[StokesMapping, StokesMapping, StokesMapping, StokesMapping]
    stokes_derivation: str = Field(min_length=40)
    circular_from_stokes: str = Field(min_length=20)
    m_derivation: str = Field(min_length=20)
    processing: PolsartoolsProcessing

    @field_validator("polsartools_sources_sha256")
    @classmethod
    def _hex_digests(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("name the polsartools sources the conventions were read from")
        for path, digest in value.items():
            if not re.fullmatch(r"polsartools/[\w/]+\.py", path):
                raise ValueError(f"not a polsartools source path: {path!r}")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"not a lower-case sha256: {digest!r}")
        return value

    def terraluna_chi(self, polsartools_chi: float) -> float:
        for mapping in self.ellipticity:
            if mapping.polsartools_chi_deg == polsartools_chi:
                return mapping.terraluna_chi_tx_deg
        raise KeyError(polsartools_chi)

    def polsartools_chi(self, terraluna_chi: float) -> float:
        for mapping in self.ellipticity:
            if mapping.terraluna_chi_tx_deg == terraluna_chi:
                return mapping.polsartools_chi_deg
        raise KeyError(terraluna_chi)

    def stokes_map(self) -> dict[str, tuple[str, int]]:
        return {m.terraluna: (m.polsartools, m.sign) for m in self.stokes}


class ConversionTolerance(_Strict):
    rtol: float = Field(ge=0.0, allow_inf_nan=False)
    atol: float = Field(ge=0.0, allow_inf_nan=False)
    justification: str = Field(min_length=20)


class FieldGroupTolerance(_Strict):
    """``|ours - polsartools| <= factor * scale`` per pixel, for every listed field.

    ``scale`` is ``span`` (``A + B + 2 V`` of the pixel's covariance, linear power) or
    ``span_over_s1`` (that span divided by our hybrid S1 at the field's ellipticity,
    dimensionless).
    """

    fields: tuple[str, ...] = Field(min_length=1)
    scale: Literal["span", "span_over_s1"]
    factor: float = Field(gt=0.0, allow_inf_nan=False)
    derivation: str = Field(min_length=40)


class OracleTolerance(_Strict):
    description: str = Field(min_length=20)
    decided_by: str | None
    decided_utc: str | None
    declared_before_results: Literal[True]
    justification: str = Field(min_length=40)
    conversion_tolerance: ConversionTolerance
    groups: tuple[FieldGroupTolerance, ...] = Field(min_length=1)

    def coverage_problem(self, fields: Sequence[str]) -> str | None:
        listed = [name for group in self.groups for name in group.fields]
        duplicated = sorted({name for name in listed if listed.count(name) > 1})
        missing = sorted(set(fields) - set(listed))
        unknown = sorted(set(listed) - set(fields))
        problems = [
            f"{label}: {', '.join(names)}"
            for label, names in (
                ("listed twice", duplicated),
                ("not covered", missing),
                ("not compared", unknown),
            )
            if names
        ]
        return "; ".join(problems) or None

    def group_of(self, field: str) -> FieldGroupTolerance:
        for group in self.groups:
            if field in group.fields:
                return group
        raise KeyError(field)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_tolerance(path: Path, *, compared_fields: Sequence[str]) -> OracleTolerance:
    """The decided tolerance, or :class:`ToleranceNotDeclared` with the reason."""
    where = _relative(path)
    if not path.is_file():
        raise ToleranceNotDeclared(
            f"tolerance not declared: {where} does not exist. A person decides the comparison "
            "tolerance, with a written justification, before the comparison runs (T2-07)."
        )
    try:
        tolerance = OracleTolerance.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise ToleranceNotDeclared(
            f"tolerance not declared: {where} is not valid: {error}"
        ) from error
    if not (tolerance.decided_by or "").strip() or not (tolerance.decided_utc or "").strip():
        raise ToleranceNotDeclared(
            f"tolerance not declared: {where} names no person in decided_by and decided_utc"
        )
    problem = tolerance.coverage_problem(compared_fields)
    if problem:
        raise ToleranceNotDeclared(f"tolerance not declared for every field in {where}: {problem}")
    return tolerance


def load_conventions(path: Path) -> OracleConventions:
    where = _relative(path)
    if not path.is_file():
        raise ConventionsNotDeclared(f"polsartools conventions not declared: {where} is missing")
    try:
        return OracleConventions.model_validate_json(path.read_bytes())
    except ValidationError as error:
        raise ConventionsNotDeclared(
            f"polsartools conventions not declared: {where} is not valid: {error}"
        ) from error


# --- polsartools (D19: development and test only) --------------------------------------------


def import_polsartools() -> tuple[ModuleType, ModuleType]:
    """polsartools and the GDAL bindings it reads and writes with, or
    :class:`OracleNotInstalled`."""
    try:
        polsartools = importlib.import_module("polsartools")
        gdal = importlib.import_module("osgeo.gdal")
    except ImportError as error:
        raise OracleNotInstalled(
            f"polsartools cannot be imported ({error}). It is a development-only oracle (D19) "
            "in the opt-in 'oracle' dependency group of pipeline/pyproject.toml, and it needs "
            "the GDAL Python bindings, which build against a system GDAL of the locked version "
            "(gdal-config on PATH). Install them with: uv sync --locked --group oracle"
        ) from error
    return polsartools, gdal


def check_polsartools_sources(polsartools: ModuleType, conventions: OracleConventions) -> None:
    """The installed polsartools is the version, and the code, the conventions were read from."""
    installed = importlib.metadata.version("polsartools")
    if installed != conventions.polsartools_version:
        raise ConventionsNotDeclared(
            f"polsartools conventions not declared for polsartools {installed}: "
            f"{_relative(CONVENTIONS_PATH)} was derived from {conventions.polsartools_version}"
        )
    package = Path(str(polsartools.__file__)).parent
    for source, digest in conventions.polsartools_sources_sha256.items():
        path = package.parent / source
        actual = sha256_file(path) if path.is_file() else None
        if actual != digest:
            raise ConventionsNotDeclared(
                f"polsartools conventions not declared for this code: {source} has sha256 "
                f"{actual}, but the conventions were derived from {digest}"
            )


PolsartoolsOutputs = dict[str, npt.NDArray[np.float32]]
"""One ellipticity's polsartools outputs: C2 elements ``c11``, ``c12_real``, ``c12_imag``,
``c22`` from ``simulate_CP`` and ``m`` (``m_cp``) from ``m_chi``."""


def run_polsartools(
    polsartools: ModuleType,
    gdal: ModuleType,
    scene: Scene,
    window: Window,
    processing: PolsartoolsProcessing,
    chis: Iterable[float],
    work: Path,
) -> dict[float, PolsartoolsOutputs]:
    """Run polsartools' own chain on the scene and read its outputs for the window.

    ``import_uavsar_grd`` builds the C3 matrix from the annotation and the six GRD files (it
    reads whole files and writes the scene extent beside the annotation, so it runs on
    symbolic links in ``work``, not in the scene directory), ``clip`` cuts the window, and per
    ellipticity ``simulate_CP`` synthesizes the compact-pol C2 matrix and ``m_chi`` computes m.
    """
    gdal.UseExceptions()
    links = work / "scene"
    links.mkdir()
    for source in (scene.annotation_path, *(scene.path(p) for p in PRODUCTS)):
        (links / source.name).symlink_to(source.resolve())
    full = work / "full"
    full.mkdir()
    polsartools.import_uavsar_grd(
        str(links / scene.annotation_path.name), mat="C3", fmt=processing.fmt, out_dir=str(full)
    )
    outputs: dict[float, PolsartoolsOutputs] = {}
    for chi in chis:
        clipped = work / f"chi_{'plus' if chi > 0 else 'minus'}_{abs(chi):g}"
        polsartools.clip(
            str(full / "C3"),
            output_folder=str(clipped),
            start_x=window.col0,
            start_y=window.row0,
            dx=window.cols,
            dy=window.rows,
        )
        polsartools.simulate_CP(
            str(clipped),
            chi=chi,
            psi=processing.psi_deg,
            win=processing.win,
            fmt=processing.fmt,
            block_size=processing.block_size,
        )
        c2 = clipped / "C2CP"
        polsartools.m_chi(
            str(c2),
            chi=chi,
            psi=processing.psi_deg,
            win=processing.win,
            fmt=processing.fmt,
            block_size=processing.block_size,
        )
        names = {
            "c11": c2 / "C11.tif",
            "c12_real": c2 / "C12_real.tif",
            "c12_imag": c2 / "C12_imag.tif",
            "c22": c2 / "C22.tif",
            "m": c2 / "m_cp.tif",
        }
        outputs[chi] = {key: _read_raster(gdal, path, window) for key, path in names.items()}
    shutil.rmtree(full)
    return outputs


def _read_raster(gdal: ModuleType, path: Path, window: Window) -> npt.NDArray[np.float32]:
    dataset = gdal.Open(str(path))
    values = np.asarray(dataset.GetRasterBand(1).ReadAsArray())
    dataset = None
    if values.shape != (window.rows, window.cols):
        raise ValueError(f"{path} has shape {values.shape}, not the window's")
    return values.astype(np.float32, copy=False)


def polsartools_stokes(outputs: PolsartoolsOutputs, polsartools_chi: float) -> dict[str, Any]:
    """polsartools' Stokes parameters of its C2 matrix, by its own m_chi definitions (float64).

    ``s0 = C11 + C22``, ``s1 = C11 - C22``, ``s2 = 2 Re C12``, and ``s3 = -2 Im C12`` for
    ``chi >= 0`` or ``+2 Im C12`` for ``chi < 0`` (``polsar/cp/m_chi.py``,
    ``process_chunk_mchi``).
    """
    c11 = outputs["c11"].astype(np.float64)
    c22 = outputs["c22"].astype(np.float64)
    re12 = outputs["c12_real"].astype(np.float64)
    im12 = outputs["c12_imag"].astype(np.float64)
    return {
        "s0": c11 + c22,
        "s1": c11 - c22,
        "s2": 2.0 * re12,
        "s3": (-2.0 if polsartools_chi >= 0 else 2.0) * im12,
    }


# --- Our side --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OurSide:
    fields: Mapping[str, StatedArray]
    span: npt.NDArray[np.float64]
    s1: Mapping[float, StatedArray]


def our_side(cov: npt.NDArray[np.complex128], tolerance: Tolerance) -> OurSide:
    """Our outputs through the public interfaces: derive_circular for SC/OC/base_SC under both
    conventions, hybrid_stokes_from_covariance for each ellipticity, m_chi_from_hybrid for m."""
    shape = cov.shape[:-2]
    valid = np.ones(shape, dtype=bool)
    data = CovarianceInput(covariance=cov, ordering=COVARIANCE_HH_HV_VV)

    def circular(convention: HandednessConvention) -> CovarianceCircularPowers:
        return derive_circular(
            COVARIANCE_INPUT, data, convention, validity_mask=valid, tolerance=tolerance
        )

    ll = circular(NEISH_SC_LL_CONVENTION)
    opp = circular(OPPOSITE_SC_CONVENTION)
    fields: dict[str, StatedArray] = {
        "sc_ll": ll.sc,
        "oc_ll": ll.oc,
        "sc_opp": opp.sc,
        "oc_opp": opp.oc,
        "base_sc": ll.base_sc,
    }
    s1: dict[float, StatedArray] = {}
    for chi in TERRALUNA_CHI_TX:
        hybrid = hybrid_stokes_from_covariance(
            cov,
            ordering=COVARIANCE_HH_HV_VV,
            chi_tx=chi,
            product_masked=~valid,
            tolerance=tolerance,
        )
        for name in ("s1", "s2", "s3", "s4"):
            fields[_hybrid_field(name, chi)] = getattr(hybrid, name)
        fields[_hybrid_field("m", chi)] = m_chi_from_hybrid(hybrid, tolerance=tolerance).m
        s1[chi] = hybrid.s1
    diagonal = np.real(np.diagonal(cov, axis1=-2, axis2=-1))
    span = diagonal[..., 0] + diagonal[..., 2] + 2.0 * diagonal[..., 1]
    return OurSide(fields=fields, span=span, s1=s1)


# --- The oracle side, under the declared conventions ------------------------------------------


def oracle_fields(
    outputs: Mapping[float, PolsartoolsOutputs],
    conventions: OracleConventions,
    *,
    pairing: Callable[[float], float],
    s4_sign: int,
) -> dict[str, npt.NDArray[np.float64]]:
    """polsartools' values for every compared field.

    ``pairing`` maps our chi_tx to the polsartools chi whose outputs stand for it, and
    ``s4_sign`` multiplies the declared S4. The declared mapping is
    ``pairing=conventions.polsartools_chi`` with ``s4_sign=1``; anything else is only used to
    say whether a mismatch looks like a convention difference.
    """
    stokes_map = conventions.stokes_map()
    fields: dict[str, npt.NDArray[np.float64]] = {}
    sc_oc: dict[float, tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]] = {}
    for chi in TERRALUNA_CHI_TX:
        theirs = pairing(chi)
        stokes = polsartools_stokes(outputs[theirs], theirs)
        ours: dict[str, npt.NDArray[np.float64]] = {}
        for name in ("s1", "s2", "s3", "s4"):
            source, sign = stokes_map[name]
            ours[name] = sign * stokes[source] * (s4_sign if name == "s4" else 1)
            fields[_hybrid_field(name, chi)] = ours[name]
        fields[_hybrid_field("m", chi)] = outputs[theirs]["m"].astype(np.float64)
        # PRD 6.2, SIS convention S4 = OC - SC: SC = (S1 - S4) / 2, OC = (S1 + S4) / 2.
        sc_oc[chi] = ((ours["s1"] - ours["s4"]) / 2.0, (ours["s1"] + ours["s4"]) / 2.0)
    by_convention = {
        NEISH_SC_LL_CONVENTION: sc_oc[-45.0],
        OPPOSITE_SC_CONVENTION: sc_oc[45.0],
    }
    fields["sc_ll"], fields["oc_ll"] = by_convention[NEISH_SC_LL_CONVENTION]
    fields["sc_opp"], fields["oc_opp"] = by_convention[OPPOSITE_SC_CONVENTION]
    fields["base_sc"] = (fields["sc_ll"] + fields["sc_opp"]) / 2.0
    return fields


# --- Comparison ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldComparison:
    field: str
    n_pixels: int
    n_compared: int
    not_compared: Mapping[str, int]
    n_mismatch: int
    n_oracle_nonfinite: int
    max_abs_diff: float | None
    max_diff_over_allowance: float | None
    convention_note: str | None

    @property
    def matches(self) -> bool:
        return self.n_compared > 0 and self.n_mismatch == 0

    def failure(self) -> str | None:
        if self.n_compared == 0:
            return f"{self.field}: no pixel was compared (ours: {dict(self.not_compared)})"
        if self.n_mismatch == 0:
            return None
        text = (
            f"{self.field}: {self.n_mismatch} of {self.n_compared} compared pixels differ "
            f"beyond the declared tolerance (max |difference| {self.max_abs_diff:.6g}, "
            f"{self.max_diff_over_allowance:.6g} times the allowance; "
            f"{self.n_oracle_nonfinite} polsartools values not finite)"
        )
        if self.convention_note:
            text += f". {self.convention_note}"
        return text

    def as_record(self) -> dict[str, Any]:
        return {
            "n_pixels": self.n_pixels,
            "n_compared": self.n_compared,
            "not_compared_ours_not_valid": dict(sorted(self.not_compared.items())),
            "n_mismatch": self.n_mismatch,
            "n_oracle_nonfinite": self.n_oracle_nonfinite,
            "max_abs_diff": self.max_abs_diff,
            "max_diff_over_allowance": self.max_diff_over_allowance,
            "convention_note": self.convention_note,
        }


def _mismatches(
    ours: StatedArray, theirs: npt.NDArray[np.float64], allowance: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.float64]]:
    compared = ours.state == ObsState.VALID
    with np.errstate(invalid="ignore"):
        diff = np.abs(ours.value - theirs)
        within = diff <= allowance
    return compared & ~within, diff


def compare_field(
    field: str,
    ours: StatedArray,
    theirs: npt.NDArray[np.float64],
    allowance: npt.NDArray[np.float64],
    alternatives: Mapping[str, npt.NDArray[np.float64]] | None = None,
) -> FieldComparison:
    """Compare one field where ours is VALID; ours not VALID is counted by reason, not compared.

    A polsartools value that is not finite where ours is VALID is a mismatch. When the field
    mismatches, each alternative (a convention other than the declared one) is tried; one that
    matches every compared pixel is reported as a convention difference. The field still fails:
    a convention is resolved by declaring it, never by picking the alternative that agrees.
    """
    compared = ours.state == ObsState.VALID
    reasons, counts = np.unique(ours.reason[~compared], return_counts=True)
    not_compared = {str(r): int(c) for r, c in zip(reasons.tolist(), counts.tolist(), strict=True)}
    mismatch, diff = _mismatches(ours, theirs, allowance)
    n_compared = int(compared.sum())
    n_mismatch = int(mismatch.sum())
    max_abs: float | None = None
    max_ratio: float | None = None
    if n_compared:
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = diff[compared] / allowance[compared]
        finite = np.isfinite(diff[compared])
        max_abs = float(np.max(diff[compared])) if finite.all() else math.inf
        max_ratio = float(np.max(ratio)) if np.isfinite(ratio).all() else math.inf
    note = None
    if n_mismatch:
        agreeing = [
            name
            for name, values in (alternatives or {}).items()
            if not np.any(_mismatches(ours, values, allowance)[0])
        ]
        if agreeing:
            note = (
                "Convention difference: the field agrees within the tolerance under "
                f"{' and under '.join(agreeing)}, not under the declared conventions in "
                f"{_relative(CONVENTIONS_PATH)}. Resolve it by declaring the convention there "
                "with its derivation, not by choosing the alternative that agrees."
            )
    return FieldComparison(
        field=field,
        n_pixels=int(compared.size),
        n_compared=n_compared,
        not_compared=not_compared,
        n_mismatch=n_mismatch,
        n_oracle_nonfinite=int((compared & ~np.isfinite(theirs)).sum()),
        max_abs_diff=max_abs,
        max_diff_over_allowance=max_ratio,
        convention_note=note,
    )


def allowance_for(field: str, tolerance: OracleTolerance, side: OurSide) -> npt.NDArray[np.float64]:
    group = tolerance.group_of(field)
    if group.scale == "span":
        return group.factor * side.span
    chi = float(field.rsplit("=", 1)[1])
    with np.errstate(invalid="ignore", divide="ignore"):
        return group.factor * side.span / side.s1[chi].value


# --- The run ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OracleRun:
    record: dict[str, Any]
    comparisons: tuple[FieldComparison, ...]

    @property
    def failures(self) -> list[str]:
        return [text for c in self.comparisons if (text := c.failure()) is not None]


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_oracle_check(
    scene: Scene,
    window: Window,
    tolerance: OracleTolerance,
    conventions: OracleConventions,
    polsartools: ModuleType,
    gdal: ModuleType,
    work: Path,
) -> OracleRun:
    """Compare our fields with polsartools' on the window and build the run record.

    The record holds only what the run determines: the same code, declarations, software and
    scene give the same bytes, so rerunning the check leaves the committed record unchanged.
    It names the code by the sha256 of each file it ran (:data:`CODE_FILES`), not by a
    commit, and holds no timestamp.
    """
    window.check(scene.layout)
    check_polsartools_sources(polsartools, conventions)
    sums = stated_sums(scene.directory)
    files = []
    for name, path in [(f"grd{p}", scene.path(p)) for p in PRODUCTS] + [
        ("annotation", scene.annotation_path)
    ]:
        digest = sha256_file(path)
        stated = sums.get(path.name)
        files.append(
            {
                "role": name,
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": digest,
                "matches_scene_sha256sums": None if stated is None else stated == digest,
            }
        )
    mismatched = [f["file"] for f in files if f["matches_scene_sha256sums"] is False]
    if mismatched:
        raise ValueError(f"scene files differ from the scene's SHA256SUMS: {mismatched}")

    conversion = Tolerance(
        rtol=tolerance.conversion_tolerance.rtol, atol=tolerance.conversion_tolerance.atol
    )
    ours = our_side(covariance_window(scene, window), conversion)
    outputs = run_polsartools(
        polsartools,
        gdal,
        scene,
        window,
        conventions.processing,
        [conventions.polsartools_chi(chi) for chi in TERRALUNA_CHI_TX],
        work,
    )
    declared = oracle_fields(outputs, conventions, pairing=conventions.polsartools_chi, s4_sign=1)
    swapped = oracle_fields(
        outputs, conventions, pairing=lambda chi: conventions.polsartools_chi(-chi), s4_sign=1
    )
    negated = oracle_fields(outputs, conventions, pairing=conventions.polsartools_chi, s4_sign=-1)
    alternative_sets = {
        "the opposite ellipticity pairing": swapped,
        "the opposite S4 sign": negated,
    }
    comparisons = tuple(
        compare_field(
            field,
            ours.fields[field],
            declared[field],
            allowance_for(field, tolerance, ours),
            {name: values[field] for name, values in alternative_sets.items()},
        )
        for field in COMPARED_FIELDS
    )
    outcome = "MATCH" if all(c.matches for c in comparisons) else "MISMATCH"
    record: dict[str, Any] = {
        "record_type": "terraluna polsartools oracle run (T2-07)",
        "written_by": (
            "pipeline/tests/test_polarimetry_oracle.py::test_real_scene_matches_polsartools"
        ),
        "outcome": outcome,
        "scene": {
            "scene_id": scene.scene_id,
            "directory_name": scene.directory.name,
            "source": f"the directory named by {SCENE_ENV}; its absolute path is not recorded",
            "acquisition": scene.layout.acquisition,
            "look_direction": scene.layout.look_direction,
            "files": files,
        },
        "layout": {
            "rows": scene.layout.rows,
            "cols": scene.layout.cols,
            "real_dtype": scene.layout.real_dtype,
            "complex_dtype": scene.layout.complex_dtype,
            "header_bytes": scene.layout.header_bytes,
            "tail_bytes": scene.layout.tail_bytes,
            "upper_left_pixel_centre_lat_deg": scene.layout.upper_left_lat_deg,
            "upper_left_pixel_centre_lon_deg": scene.layout.upper_left_lon_deg,
            "lat_step_deg": scene.layout.lat_step_deg,
            "lon_step_deg": scene.layout.lon_step_deg,
            "source": "the scene's .ann annotation (PRD 5)",
        },
        "window": window.as_record(scene.layout),
        "software": {
            "polsartools": _version("polsartools"),
            "gdal_python": str(getattr(gdal, "__version__", None)),
            "numpy": np.__version__,
            "python": platform.python_version(),
            "terraluna_pipeline": _version("terraluna-pipeline"),
        },
        "declarations": {
            "tolerance": _relative(TOLERANCE_PATH),
            "tolerance_sha256": sha256_file(TOLERANCE_PATH),
            "tolerance_decided_by": tolerance.decided_by,
            "conventions": _relative(CONVENTIONS_PATH),
            "conventions_sha256": sha256_file(CONVENTIONS_PATH),
        },
        "fields": {c.field: c.as_record() for c in comparisons},
        "failures": [text for c in comparisons if (text := c.failure()) is not None],
        "code_sha256": {name: sha256_file(REPO_ROOT / name) for name in CODE_FILES},
        "g1_note": (
            "G1 may not be PASS before this check has run on a real scene and passed, unless a "
            "person decides otherwise (T2-07 for the story 6 scope statement)."
        ),
    }
    return OracleRun(record=record, comparisons=comparisons)


def write_run_record(record: Mapping[str, Any], path: Path) -> None:
    """Write the run record as the run produced it (JSON, sorted keys, LF)."""
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- Import scan (D19) -----------------------------------------------------------------------


def polsartools_imports(source: str, filename: str = "<source>") -> list[str]:
    """Each place a module's source imports polsartools: an import statement, or an
    ``importlib.import_module`` / ``__import__`` call with a literal module name."""

    def is_polsartools(name: str | None) -> bool:
        return name is not None and (name == "polsartools" or name.startswith("polsartools."))

    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            found += [f"{filename}:{node.lineno}" for a in node.names if is_polsartools(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and is_polsartools(node.module):
                found.append(f"{filename}:{node.lineno}")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if (
                name in {"import_module", "__import__"}
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and is_polsartools(first.value)
            ):
                found.append(f"{filename}:{node.lineno}")
    return found
