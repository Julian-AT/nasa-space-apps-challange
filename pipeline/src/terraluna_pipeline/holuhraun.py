"""G3 Holuhraun reproduction: UAVSAR CPR distributions over lava facies (PRD 7, 9; T6-05).

The check compares the CPR distributions of three mapped Holuhraun lava facies with the
88-89% distribution overlap of Tolometti et al. (2022, JGR Planets, doi 10.1029/2021JB023419),
as a plausibility check of the processing chain, and reports the quantities of Neish et al.
(2017) only where acquisition, convention, region and aggregation match (they do not, so that
comparison is ``Unavailable`` with the mismatching items named).

Order of work (PRD 7: archive the protocol before looking at a result): the protocol
``analysis/holuhraun_protocol.json`` is committed first; :func:`run` refuses to run unless that
file is committed and unmodified, reads its input hashes, and records the commit in the
reproduction record. Nothing is tuned after a result is seen; a change goes into a new, dated
protocol file with the old one kept.

CPR comes from :func:`terraluna_pipeline.polarimetry.derive_circular` (covariance input in the
UAVSAR ``HH_HV_VV`` ordering, declared transmit handedness ``NEISH_SC_LL_TX_1_MINUS_I``) with its
validity mask and invalid reasons. No epsilon is added and nothing is clipped; CPR above 1
stays valid. Missing is never 0.

NOTE: the private ``_read_*`` helpers below read the UAVSAR ``.ann`` annotation and the GRD
rasters. They are a minimal stand-in and should be replaced by ``terraluna_pipeline.uavsar``
once that module (package t4-real) is merged. The scene directory comes from the environment
variable :data:`SCENE_ENV`, the facies directory from :data:`FACIES_ENV`; the code never sets
either.

CLI: ``python -m terraluna_pipeline.holuhraun run`` and ``... check`` (exit 0 done, 1 a check
failed, 2 usage, 3 blocked by a missing input, printed as ``blocked: ...``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from rasterio import features  # type: ignore[import-untyped]
from rasterio.transform import Affine, from_origin  # type: ignore[import-untyped]

from terraluna_pipeline import provenance
from terraluna_pipeline.polarimetry import (
    COVARIANCE_HH_HV_VV,
    COVARIANCE_INPUT,
    NEISH_SC_LL_CONVENTION,
    CovarianceInput,
    ObsState,
    Tolerance,
    derive_circular,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SCENE_ENV: Final = "TERRALUNA_UAVSAR_SCENE"
FACIES_ENV: Final = "TERRALUNA_HOLUHRAUN_FACIES"
TOLOMETTI_ENV: Final = "TERRALUNA_TOLOMETTI_DATA"
PROTOCOL_REL: Final = "analysis/holuhraun_protocol.json"
REPRODUCTION_REL: Final = "analysis/holuhraun_reproduction.json"
G3_REL: Final = "outputs/gates/G3.json"
CONFIG_REL: Final = "config/run.yaml"

SCENE_ID: Final = "Icelnd_00208_15083_004_150530_L090_CX_01"
FACIES: Final = ("rubbly", "spiny", "undifferentiated")
FACIES_FILES: Final = {
    "rubbly": "Rubbly_facies.geojson",
    "spiny": "Spiny_facies.geojson",
    "undifferentiated": "Undifferentiated_facies.geojson",
}
REAL_PRODUCTS: Final = ("HHHH", "HVHV", "VVVV")
COMPLEX_PRODUCTS: Final = ("HHHV", "HHVV", "HVVV")

# --- fixed in advance, recorded in the protocol; `check` fails if the two drift apart --------

CONVENTION: Final = NEISH_SC_LL_CONVENTION
ORDERING: Final = COVARIANCE_HH_HV_VV
CONVERSION_TOLERANCE: Final = Tolerance(rtol=2.0**-22, atol=0.0)
"""Hermitian and PSD tolerance for float32 GRD covariances (same derivation as
``fixtures/polarimetry/oracle_tolerance.json``: 4 u with u = 2**-24, atol 0)."""
OVERLAP_BINS: Final = (0.0, 2.0, 100)
"""CPR histogram range start, stop and number of equal bins; values outside go to an underflow
or overflow bin, never clipped."""
OVERLAP_EDGES: Final = np.linspace(OVERLAP_BINS[0], OVERLAP_BINS[1], OVERLAP_BINS[2] + 1)
INCIDENCE_MAX_DEG: Final = 65.0
INCIDENCE_NODATA_RAD: Final = -10000.0
BOXCAR: Final = 3
PRIMARY_VARIANT: Final = "boxcar3x3"
VARIANTS: Final = ("as_delivered", "boxcar3x3")
PERCENTILES: Final = (1, 5, 10, 25, 50, 75, 90, 95, 99)

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

# --- JSON ------------------------------------------------------------------------------------


def dumps(value: object) -> str:
    """Canonical JSON text: sorted keys, 2-space indent, LF, trailing newline, no NaN."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(value).encode("utf-8"))


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


# --- statistics ------------------------------------------------------------------------------


def _as_samples(name: str, values: npt.ArrayLike) -> FloatArray:
    array = np.asarray(values, dtype=np.float64).ravel()
    if array.size == 0:
        raise ValueError(f"{name} is empty")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} holds values that are not finite")
    return array


def _histogram(values: FloatArray, edges: FloatArray) -> FloatArray:
    """Normalized counts with an underflow bin first and an overflow bin last (no clipping)."""
    inner, _ = np.histogram(values, bins=edges)
    below = int(np.count_nonzero(values < edges[0]))
    above = int(np.count_nonzero(values > edges[-1]))
    # np.histogram includes the right edge in the last bin; keep exactly edges[-1] there.
    counts = np.concatenate(([below], inner, [above])).astype(np.float64)
    return counts / counts.sum()


def overlap_coefficient(a: npt.ArrayLike, b: npt.ArrayLike, *, edges: npt.ArrayLike) -> float:
    """Overlap coefficient of two histograms on common bins: ``sum_i min(p_i, q_i)``.

    ``edges`` are the shared bin edges; values below the first or above the last edge are
    counted in an underflow and an overflow bin (nothing is clipped or dropped). 1.0 for
    identical distributions, 0.0 for disjoint supports. Empty or non-finite input raises.
    """
    x = _as_samples("a", a)
    y = _as_samples("b", b)
    grid = np.asarray(edges, dtype=np.float64)
    return float(np.minimum(_histogram(x, grid), _histogram(y, grid)).sum())


def summarize_distribution(values: npt.ArrayLike) -> dict[str, Any]:
    """Count, mean, median, quartiles and percentiles of a CPR sample (linear, unitless)."""
    x = _as_samples("values", values)
    q25, q75 = np.percentile(x, [25.0, 75.0])
    return {
        "n": int(x.size),
        "mean": float(x.mean()),
        "median": float(np.median(x)),
        "q25": float(q25),
        "q75": float(q75),
        "iqr": float(q75 - q25),
        "min": float(x.min()),
        "max": float(x.max()),
        "percentiles": {
            str(p): float(v)
            for p, v in zip(PERCENTILES, np.percentile(x, PERCENTILES), strict=True)
        },
        "fraction_above_1": float(np.mean(x > 1.0)),
    }


def aggregate_powers(sc: npt.ArrayLike, oc: npt.ArrayLike) -> dict[str, Any]:
    """Both aggregates, never substituted for one another (PRD 7: 0.47 versus 0.51).

    ``ratio_of_mean_powers = mean(SC) / mean(OC)`` and ``mean_pixelwise_cpr = mean(SC / OC)``,
    over the same pixels. A nonpositive OC raises: the caller excludes such pixels first.
    """
    s = _as_samples("sc", sc)
    o = _as_samples("oc", oc)
    if s.shape != o.shape:
        raise ValueError("sc and oc must have the same shape")
    if np.any(o <= 0.0):
        raise ValueError("OC must be positive; exclude nonpositive OC pixels before aggregating")
    return {
        "n": int(s.size),
        "ratio_of_mean_powers": float(s.mean() / o.mean()),
        "mean_pixelwise_cpr": float(np.mean(s / o)),
        "label": "Derived",
    }


UNAVAILABLE_NO_POLYGONS: Final = "facies polygons not available"


def compare_facies(samples: Mapping[str, npt.ArrayLike] | None) -> dict[str, Any]:
    """Pairwise overlap coefficients of the facies CPR samples, or ``Unavailable``.

    No samples, a missing facies, or a facies with no sample returns ``Unavailable`` with the
    reason ``'facies polygons not available'``; no overlap is computed on an invented mask.
    """
    if not samples or any(
        name not in samples or np.asarray(samples[name]).size == 0 for name in FACIES
    ):
        return {"status": "Unavailable", "reason": UNAVAILABLE_NO_POLYGONS, "label": "Unavailable"}
    overlaps = {
        f"{a}|{b}": overlap_coefficient(samples[a], samples[b], edges=OVERLAP_EDGES)
        for a, b in combinations(FACIES, 2)
    }
    return {"status": "computed", "overlaps": overlaps, "label": "Derived"}


# --- facies membership -----------------------------------------------------------------------


def rasterize_facies(
    polygons: Mapping[str, Sequence[Mapping[str, Any]]],
    names: Sequence[str],
    transform: Affine,
    shape: tuple[int, int],
) -> tuple[npt.NDArray[np.int8], BoolArray]:
    """Pixel-centre membership of GeoJSON-like geometries on a grid.

    Returns ``labels`` (0 outside every facies, else the 1-based index into ``names`` of the
    first facies covering the pixel) and ``overlap`` (True where more than one facies covers the
    pixel; such pixels are excluded later and counted under the first facies).
    """
    covered = np.zeros((len(names), *shape), dtype=bool)
    for index, name in enumerate(names):
        geoms = list(polygons.get(name, ()))
        if geoms:
            covered[index] = features.rasterize(
                [(g, 1) for g in geoms],
                out_shape=shape,
                transform=transform,
                fill=0,
                all_touched=False,
                dtype="uint8",
            ).astype(bool)
    labels = np.zeros(shape, dtype=np.int8)
    for index in range(len(names) - 1, -1, -1):
        labels[covered[index]] = index + 1
    return labels, covered.sum(axis=0) > 1


# --- pixels to CPR ---------------------------------------------------------------------------


def _boxcar3(cov: npt.NDArray[np.complex128], valid: BoolArray) -> tuple[Any, BoolArray]:
    """3x3 mean of the covariance elements where the whole window lies inside the array and is
    valid. Returns (filtered covariance, complete); elsewhere the filtered value is zero."""
    rows, cols = valid.shape
    out = np.zeros_like(cov)
    complete = np.zeros((rows, cols), dtype=bool)
    if rows < BOXCAR or cols < BOXCAR:
        return out, complete
    inner = (slice(1, rows - 1), slice(1, cols - 1))
    count = np.zeros((rows - 2, cols - 2), dtype=np.int64)
    acc = np.zeros((rows - 2, cols - 2, 3, 3), dtype=np.complex128)
    for dr in range(3):
        for dc in range(3):
            window = (slice(dr, rows - 2 + dr), slice(dc, cols - 2 + dc))
            count += valid[window]
            acc += np.where(valid[window][..., None, None], cov[window], 0.0)
    full = count == BOXCAR * BOXCAR
    complete[inner] = full
    out[inner] = np.where(full[..., None, None], acc / (BOXCAR * BOXCAR), 0.0)
    return out, complete


def _reason_counts(reason: npt.NDArray[np.str_]) -> dict[str, int]:
    names, counts = np.unique(reason[reason != ""], return_counts=True)
    return {str(n): int(c) for n, c in zip(names, counts, strict=True)}


def analyse_variant(
    cov: npt.NDArray[np.complex128],
    nodata: BoolArray,
    incidence_deg: FloatArray,
    labels: npt.NDArray[np.int8],
    names: Sequence[str],
    boxcar: bool,
    overlap_mask: BoolArray,
) -> dict[str, dict[str, Any]]:
    """CPR, SC and OC of the valid pixels of each facies, with counted exclusion reasons.

    ``cov`` has shape ``(rows, cols, 3, 3)`` in the UAVSAR ``HH_HV_VV`` ordering; ``nodata`` is
    True where the product has no observation; ``incidence_deg`` is NaN where unknown. With
    ``boxcar`` the covariance is first averaged over a 3x3 window (the look increase of
    Tolometti et al. 2022 section 2.3, an assumption on where the filter acts).

    Exclusion order, first match wins: ``FACIES_OVERLAP``, ``PRODUCT_NODATA``,
    ``FILTER_WINDOW_INCOMPLETE`` (boxcar only), ``INCIDENCE_NODATA``, ``INCIDENCE_ABOVE_MAX``,
    then the reasons of :func:`terraluna_pipeline.polarimetry.derive_circular` (for example
    ``OC_NONPOSITIVE``). No epsilon and no clipping; CPR above 1 stays valid.
    """
    if boxcar:
        used, complete = _boxcar3(cov, ~nodata)
    else:
        used, complete = cov, np.ones(nodata.shape, dtype=bool)
    member = labels > 0
    rr, cc = np.nonzero(member)
    label = labels[rr, cc]
    n = rr.size
    reason = np.full(n, "", dtype="<U32")

    def assign(mask: BoolArray, text: str) -> None:
        free = (reason == "") & mask
        reason[free] = text

    assign(overlap_mask[rr, cc], "FACIES_OVERLAP")
    assign(nodata[rr, cc], "PRODUCT_NODATA")
    assign(~complete[rr, cc], "FILTER_WINDOW_INCOMPLETE")
    inc = incidence_deg[rr, cc]
    assign(~np.isfinite(inc), "INCIDENCE_NODATA")
    with np.errstate(invalid="ignore"):
        assign(inc > INCIDENCE_MAX_DEG, "INCIDENCE_ABOVE_MAX")

    sc_all = np.full(n, np.nan)
    oc_all = np.full(n, np.nan)
    cpr_all = np.full(n, np.nan)
    todo = np.nonzero(reason == "")[0]
    if todo.size:
        result = derive_circular(
            COVARIANCE_INPUT,
            CovarianceInput(used[rr[todo], cc[todo]], ORDERING),
            CONVENTION,
            np.ones(todo.size, dtype=bool),
            tolerance=CONVERSION_TOLERANCE,
        )
        state = result.cpr.state
        bad = state != int(ObsState.VALID)
        reason[todo[bad]] = np.asarray(result.cpr.reason)[bad]
        good = ~bad
        sc_all[todo[good]] = result.sc.value[good]
        oc_all[todo[good]] = result.oc.value[good]
        cpr_all[todo[good]] = result.cpr.value[good]

    out: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(names, start=1):
        sel = label == index
        valid = sel & (reason == "")
        out[name] = {
            "n_in_polygon": int(sel.sum()),
            "n_valid": int(valid.sum()),
            "excluded": _reason_counts(np.where(sel, reason, "")),
            "cpr": cpr_all[valid],
            "sc": sc_all[valid],
            "oc": oc_all[valid],
            "incidence_deg": inc[valid],
        }
    return out


# --- target applicability, plausibility, status ---------------------------------------------


def check_applicability(facts: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    """Whether a published target applies: region, convention, aggregation and incidence band
    must all match; otherwise ``Unavailable`` with the mismatching items named. An unknown
    (``None`` or absent) fact is a mismatch, never a match."""
    mismatches: list[str] = []
    for item in ("region", "convention"):
        if facts.get(item) is None or facts.get(item) != target.get(item):
            mismatches.append(item)
    reported = facts.get("aggregation")
    wanted = target.get("aggregation")
    if reported is None or wanted is None or not set(wanted) <= set(reported):
        mismatches.append("aggregation")
    low_high = target.get("incidence_band_deg")
    seen = facts.get("incidence_range_deg")
    if (
        seen is None
        or low_high is None
        or not (float(low_high[0]) <= float(seen[0]) and float(seen[1]) <= float(low_high[1]))
    ):
        mismatches.append("incidence_band")
    if mismatches:
        return {"status": "Unavailable", "mismatches": mismatches}
    return {"status": "applicable", "mismatches": []}


def plausibility(
    overlaps: Mapping[str, float], levels: Mapping[str, float], rule: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify the run against the archived rule.

    ``suspected_processing_error``: any pairwise overlap below ``overlap_suspect_below`` (much
    lower than the published 88-89% means far better separation than published).
    ``consistent``: every overlap at or above ``overlap_consistent_min`` and every facies mean
    CPR within ``level_tolerance_cpr`` of the published mean. Anything else is ``discrepant``.
    """
    lowest = min(overlaps.values())
    published = rule["published_mean_cpr"]
    tolerance = float(rule["level_tolerance_cpr"])
    level_gap = {k: abs(float(levels[k]) - float(published[k])) for k in published}
    levels_ok = all(gap <= tolerance for gap in level_gap.values())
    if lowest < float(rule["overlap_suspect_below"]):
        outcome = "suspected_processing_error"
    elif lowest >= float(rule["overlap_consistent_min"]) and levels_ok:
        outcome = "consistent"
    else:
        outcome = "discrepant"
    return {
        "outcome": outcome,
        "lowest_overlap": float(lowest),
        "level_gap_cpr": level_gap,
        "levels_within_tolerance": levels_ok,
    }


_OUTCOMES: Final = ("consistent", "discrepant", "suspected_processing_error")


def g3_status(*, run: bool, outcome: str | None, other_parts_recorded: bool) -> str:
    """G3 status: NOT_RUN if the reproduction has not run; PASS only if it is inside the archived
    tolerance and the other G3 parts (lunar shadow behaviour, impact-melt context; T6-06) are
    recorded; FAIL for a suspected processing error; otherwise PARTIAL."""
    if not run:
        return "NOT_RUN"
    if outcome not in _OUTCOMES:
        raise ValueError(f"outcome must be one of {_OUTCOMES}, got {outcome!r}")
    if outcome == "suspected_processing_error":
        return "FAIL"
    if outcome == "consistent" and other_parts_recorded:
        return "PASS"
    return "PARTIAL"


# --- reading the scene (replace by terraluna_pipeline.uavsar when merged) ---------------------

_ANN_LINE: Final = re.compile(
    r"^(?P<key>[^=;(]*?)\s*(?:\((?P<unit>[^)]*)\))?\s*=\s*(?P<value>[^;]*)"
)


def _read_ann(path: Path) -> dict[str, tuple[str | None, str]]:
    entries: dict[str, tuple[str | None, str]] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        match = _ANN_LINE.match(line)
        if match and match["key"]:
            entries.setdefault(match["key"].strip(), (match["unit"], match["value"].strip()))
    return entries


class _Scene:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        ann = directory / f"{SCENE_ID}.ann"
        entries = _read_ann(ann)
        self.ann_path = ann
        self.rows = int(entries["grd_pwr.set_rows"][1])
        self.cols = int(entries["grd_pwr.set_cols"][1])
        self.lat0 = float(entries["grd_pwr.row_addr"][1])
        self.lon0 = float(entries["grd_pwr.col_addr"][1])
        self.dlat = float(entries["grd_pwr.row_mult"][1])
        self.dlon = float(entries["grd_pwr.col_mult"][1])
        self.units = {
            "row_mult": entries["grd_pwr.row_mult"][0],
            "col_mult": entries["grd_pwr.col_mult"][0],
        }
        self.files = {p: directory / entries[f"grd{p}"][1].split()[0] for p in REAL_PRODUCTS}
        self.files.update(
            {p: directory / entries[f"grd{p}"][1].split()[0] for p in COMPLEX_PRODUCTS}
        )
        self.inc = directory / f"{SCENE_ID}.inc"
        self.look = entries.get("Look Direction", (None, "unknown"))[1]

    @property
    def transform(self) -> Affine:
        return from_origin(
            self.lon0 - self.dlon / 2.0, self.lat0 - self.dlat / 2.0, self.dlon, -self.dlat
        )

    def memmap(self, path: Path, dtype: str) -> Any:
        return np.memmap(path, dtype=dtype, mode="r", shape=(self.rows, self.cols))

    def input_files(self) -> list[Path]:
        return sorted([self.ann_path, self.inc, *self.files.values()])


def _read_cov(scene: _Scene, window: tuple[slice, slice]) -> tuple[Any, BoolArray]:
    parts: dict[str, Any] = {}
    for p in REAL_PRODUCTS:
        parts[p] = np.array(scene.memmap(scene.files[p], "<f4")[window], dtype=np.float64)
    for p in COMPLEX_PRODUCTS:
        parts[p] = np.array(scene.memmap(scene.files[p], "<c8")[window], dtype=np.complex128)
    shape = parts["HHHH"].shape
    cov = np.zeros((*shape, 3, 3), dtype=np.complex128)
    cov[..., 0, 0] = parts["HHHH"]
    cov[..., 1, 1] = parts["HVHV"]
    cov[..., 2, 2] = parts["VVVV"]
    for (i, j), p in {(0, 1): "HHHV", (0, 2): "HHVV", (1, 2): "HVVV"}.items():
        cov[..., i, j] = parts[p]
        cov[..., j, i] = np.conj(parts[p])
    finite = np.ones(shape, dtype=bool)
    zero = np.ones(shape, dtype=bool)
    for p in REAL_PRODUCTS:
        finite &= np.isfinite(parts[p])
        zero &= parts[p] == 0.0
    for p in COMPLEX_PRODUCTS:
        finite &= np.isfinite(parts[p])
    nodata = zero | ~finite
    cov[nodata] = 0.0
    return cov, nodata


def _read_polygons(directory: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for name, filename in FACIES_FILES.items():
        data = json.loads((directory / filename).read_text(encoding="utf-8"))
        out[name] = [f["geometry"] for f in data["features"] if f.get("geometry")]
    return out


def _bounds(geoms: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node[0], (int, float)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
        else:
            for child in node:
                walk(child)

    for g in geoms:
        walk(g["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


# --- git and protocol ------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _protocol_commit() -> str | None:
    """The commit that last changed the protocol, or None if it is uncommitted or modified."""
    try:
        sha = _git("log", "-1", "--format=%H", "--", PROTOCOL_REL)
        dirty = _git("status", "--porcelain", "--", PROTOCOL_REL)
    except (subprocess.CalledProcessError, OSError):
        return None
    if not sha or dirty:
        return None
    return sha


def _load_protocol() -> dict[str, Any]:
    loaded = json.loads((REPO_ROOT / PROTOCOL_REL).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("the protocol must be a JSON object")
    return loaded


_REQUIRED_PROTOCOL_KEYS: Final = (
    "protocol_id",
    "scene_id",
    "raw_inputs_sha256",
    "facies_source",
    "overlap_definition",
    "aggregation",
    "incidence_band_rule",
    "oc_validity_rule",
    "plausibility_rule",
    "g3_status_logic",
    "parameters",
)


def _protocol_drift(protocol: Mapping[str, Any]) -> list[str]:
    problems = [f"protocol lacks {k!r}" for k in _REQUIRED_PROTOCOL_KEYS if k not in protocol]
    params = protocol.get("parameters", {})
    expected = {
        "overlap_bins": list(OVERLAP_BINS),
        "incidence_max_deg": INCIDENCE_MAX_DEG,
        "conversion_rtol": CONVERSION_TOLERANCE.rtol,
        "conversion_atol": CONVERSION_TOLERANCE.atol,
        "convention": CONVENTION,
        "ordering": ORDERING,
        "boxcar": BOXCAR,
        "primary_variant": PRIMARY_VARIANT,
    }
    for key, value in expected.items():
        if params.get(key) != value:
            problems.append(
                f"protocol parameter {key!r} is {params.get(key)!r}, code has {value!r}"
            )
    return problems


# --- the run ---------------------------------------------------------------------------------


def _blocked(message: str) -> int:
    print(f"blocked: {message}")
    return 3


def _verify_hashes(
    protocol: Mapping[str, Any], scene: _Scene, facies_dir: Path
) -> tuple[dict[str, str], list[str]]:
    declared: Mapping[str, str] = protocol["raw_inputs_sha256"]
    found: dict[str, str] = {}
    problems: list[str] = []
    paths = [*scene.input_files(), *(facies_dir / f for f in FACIES_FILES.values())]
    for path in paths:
        digest = _sha256(path)
        found[path.name] = digest
        if declared.get(path.name) != digest:
            problems.append(f"{path.name}: sha256 {digest} differs from the protocol")
    return found, problems


def _facies_window(
    scene: _Scene, polygons: Mapping[str, Sequence[Mapping[str, Any]]]
) -> tuple[tuple[slice, slice], Affine]:
    boxes = [_bounds(g) for g in polygons.values() if g]
    west = min(b[0] for b in boxes)
    south = min(b[1] for b in boxes)
    east = max(b[2] for b in boxes)
    north = max(b[3] for b in boxes)
    halo = 2
    col0 = max(int(np.floor((west - (scene.lon0 - scene.dlon / 2)) / scene.dlon)) - halo, 0)
    col1 = min(int(np.ceil((east - (scene.lon0 - scene.dlon / 2)) / scene.dlon)) + halo, scene.cols)
    row0 = max(int(np.floor((north - (scene.lat0 - scene.dlat / 2)) / scene.dlat)) - halo, 0)
    row1 = min(
        int(np.ceil((south - (scene.lat0 - scene.dlat / 2)) / scene.dlat)) + halo, scene.rows
    )
    window = (slice(row0, row1), slice(col0, col1))
    transform = scene.transform * Affine.translation(col0, row0)
    return window, transform


def _variant_record(
    result: Mapping[str, Mapping[str, Any]], protocol: Mapping[str, Any]
) -> dict[str, Any]:
    samples = {name: result[name]["cpr"] for name in FACIES}
    per_facies: dict[str, Any] = {}
    for name in FACIES:
        item = result[name]
        record: dict[str, Any] = {
            "n_in_polygon": item["n_in_polygon"],
            "n_valid": item["n_valid"],
            "excluded_by_reason": item["excluded"],
            "label": "Derived",
        }
        if item["n_valid"]:
            record["cpr"] = summarize_distribution(item["cpr"])
            record["aggregation"] = aggregate_powers(item["sc"], item["oc"])
        per_facies[name] = record
    comparison = compare_facies(samples)
    union_sc = np.concatenate([result[n]["sc"] for n in FACIES])
    union_oc = np.concatenate([result[n]["oc"] for n in FACIES])
    record_out: dict[str, Any] = {
        "per_facies": per_facies,
        "comparison": comparison,
        "union_of_three_facies": aggregate_powers(union_sc, union_oc) if union_sc.size else None,
    }
    if comparison["status"] == "computed":
        levels = {n: per_facies[n]["aggregation"]["mean_pixelwise_cpr"] for n in FACIES}
        record_out["plausibility"] = plausibility(
            comparison["overlaps"], levels, protocol["plausibility_rule"]
        )
    return record_out


def _definition_check(directory: Path) -> dict[str, Any]:
    """The same overlap definition on the published per-pixel CPR values of Tolometti et al.
    (Mendeley Data 10.17632/tjjmf382nz), reported as a definition check; it changes nothing."""
    files = {
        "rubbly": "LavaFacieRubbly_RadarValues.csv",
        "spiny": "LavaFacieSpiny_RadarValues.csv",
        "undifferentiated": "LavaFacieUndiff_RadarValues.csv",
    }
    samples: dict[str, FloatArray] = {}
    hashes: dict[str, str] = {}
    for name, filename in files.items():
        matches = sorted(directory.glob(f"*{filename}"))
        if not matches:
            return {"status": "Unavailable", "reason": f"{filename} not found"}
        table = np.loadtxt(matches[0], delimiter=",", skiprows=1, usecols=[1], dtype=np.float64)
        samples[name] = table[np.isfinite(table)]
        hashes[matches[0].name] = _sha256(matches[0])
    comparison = compare_facies(samples)
    comparison["n"] = {k: int(v.size) for k, v in samples.items()}
    comparison["raw_inputs_sha256"] = hashes
    comparison["label"] = "Observed (published values) with Derived overlap"
    return comparison


def run(argv_for_record: Sequence[str] = ("run",)) -> int:
    scene_env = os.environ.get(SCENE_ENV)
    if not scene_env or not Path(scene_env).is_dir():
        return _blocked(f"UAVSAR scene directory ({SCENE_ENV} not set or directory missing)")
    facies_env = os.environ.get(FACIES_ENV)
    if not facies_env or not all((Path(facies_env) / f).is_file() for f in FACIES_FILES.values()):
        return _blocked(f"{UNAVAILABLE_NO_POLYGONS} ({FACIES_ENV} not set or files missing)")
    scene_dir, facies_dir = Path(scene_env), Path(facies_env)
    if not (scene_dir / f"{SCENE_ID}.ann").is_file():
        return _blocked(f"scene {SCENE_ID} not found in {SCENE_ENV}")

    commit = _protocol_commit()
    if commit is None:
        print("protocol is not committed or has uncommitted changes; commit it before running")
        return 1
    protocol = _load_protocol()
    drift = _protocol_drift(protocol)
    if drift:
        print("\n".join(drift))
        return 1

    scene = _Scene(scene_dir)
    hashes, problems = _verify_hashes(protocol, scene, facies_dir)
    if problems:
        print("\n".join(problems))
        return 1

    polygons = _read_polygons(facies_dir)
    window, transform = _facies_window(scene, polygons)
    shape = (window[0].stop - window[0].start, window[1].stop - window[1].start)
    labels, overlap = rasterize_facies(polygons, FACIES, transform, shape)
    cov, nodata = _read_cov(scene, window)
    inc_raw = np.array(scene.memmap(scene.inc, "<f4")[window], dtype=np.float64)
    incidence = np.where(inc_raw <= INCIDENCE_NODATA_RAD + 1.0, np.nan, np.degrees(inc_raw))

    variants: dict[str, Any] = {}
    for variant in VARIANTS:
        result = analyse_variant(
            cov, nodata, incidence, labels, FACIES, variant == PRIMARY_VARIANT, overlap
        )
        variants[variant] = _variant_record(result, protocol)
        if variant == PRIMARY_VARIANT:
            primary_pixels = result

    used = np.concatenate([primary_pixels[n]["cpr"] for n in FACIES])
    member = labels > 0
    inc_member = incidence[member]
    finite = inc_member[np.isfinite(inc_member)]
    inc_range = [float(finite.min()), float(finite.max())] if finite.size else None
    valid_inc = np.concatenate([primary_pixels[n]["incidence_deg"] for n in FACIES])
    inc_valid_range = [float(valid_inc.min()), float(valid_inc.max())] if valid_inc.size else None
    facts = {
        "region": "Voigt and Hamilton 2021 facies polygons (Rubbly, Spiny, Undifferentiated)",
        "convention": CONVENTION,
        "aggregation": ["ratio_of_mean_powers", "mean_pixelwise_cpr"],
        "incidence_range_deg": inc_range,
    }
    applicability = check_applicability(facts, protocol["neish_2017_target"])

    primary = variants[PRIMARY_VARIANT]
    outcome = primary["plausibility"]["outcome"] if "plausibility" in primary else None
    ran = outcome is not None
    status = g3_status(run=ran, outcome=outcome, other_parts_recorded=False)

    discrepancies = _discrepancies(primary, protocol, applicability)
    record: dict[str, Any] = {
        "record_type": "terraluna holuhraun reproduction (G3, T6-05)",
        "scene_id": SCENE_ID,
        "protocol_path": PROTOCOL_REL,
        "protocol_commit": commit,
        "protocol_sha256": _sha256(REPO_ROOT / PROTOCOL_REL),
        "input_hashes": provenance.file_hashes([REPO_ROOT / PROTOCOL_REL]),
        "raw_inputs_sha256": dict(sorted(hashes.items())),
        "evidence_labels": {
            "facies_polygons": "Observed (published mapping, Voigt et al. 2021)",
            "polygon_to_scene_registration": "Assumed (no shift applied; no co-registration check)",
            "cpr_values": "Derived (UAVSAR covariance, T2 conversion)",
            "incidence": "Observed (UAVSAR local incidence angle, radians per the JPL format page, "
            "converted to degrees; the annotation does not state this unit)",
            "published_targets": "Observed (literature), compared as plausibility only",
        },
        "scene": {
            "grd_rows": scene.rows,
            "grd_cols": scene.cols,
            "pixel_spacing_deg": {"lat": scene.dlat, "lon": scene.dlon},
            "spacing_unit": scene.units,
            "look_direction": scene.look,
            "window_rows": [window[0].start, window[0].stop],
            "window_cols": [window[1].start, window[1].stop],
        },
        "incidence_deg": {
            "range_over_all_polygon_pixels": inc_range,
            "unit": "degrees",
            "max_applied": INCIDENCE_MAX_DEG,
            "valid_range_over_primary_variant": inc_valid_range,
        },
        "primary_variant": PRIMARY_VARIANT,
        "variants": variants,
        "neish_2017_applicability": applicability,
        "discrepancies": discrepancies,
        "g3_status": status,
        "valid_pixels_primary_variant": int(used.size),
    }
    tolometti = os.environ.get(TOLOMETTI_ENV)
    if tolometti and Path(tolometti).is_dir():
        record["published_values_definition_check"] = _definition_check(Path(tolometti))
    _write(REPO_ROOT / REPRODUCTION_REL, record)

    command = provenance.command_line("terraluna_pipeline.holuhraun", list(argv_for_record))
    products = [(f"uavsar/{k}", v) for k, v in sorted(hashes.items())]
    gate: dict[str, Any] = {
        "gate_id": "G3",
        "status": status,
        "evidence_path": REPRODUCTION_REL,
        "run_id": provenance.run_id(REPO_ROOT / CONFIG_REL, products),
        "scope": "Holuhraun UAVSAR CPR facies reproduction only; lunar shadow behaviour and "
        "impact-melt context (T6-06) are not part of this record",
        "result": _gate_result(primary, outcome),
        "consequence": _gate_consequence(status, outcome),
        "command": command,
        "config_version": provenance.config_version(REPO_ROOT / CONFIG_REL),
        "input_hashes": provenance.file_hashes(
            [REPO_ROOT / PROTOCOL_REL, REPO_ROOT / REPRODUCTION_REL]
        ),
    }
    # input_hashes paths must be repo-relative.
    for item in gate["input_hashes"]:
        item["path"] = Path(item["path"]).resolve().relative_to(REPO_ROOT).as_posix()
    _write(REPO_ROOT / G3_REL, gate)
    print(f"G3 {status}: {gate['result']}")
    return 0


def _gate_result(primary: Mapping[str, Any], outcome: str | None) -> str:
    if outcome is None or "plausibility" not in primary:
        return "Reproduction could not be evaluated."
    overlaps = primary["comparison"]["overlaps"]
    lo, hi = min(overlaps.values()), max(overlaps.values())
    return (
        f"Pairwise facies CPR overlap {100 * lo:.1f}% to {100 * hi:.1f}% against 88-89% "
        f"(Tolometti et al. 2022); outcome {outcome} under the archived rule. "
        "Neish et al. 2017 values not compared (region mismatch)."
    )


def _gate_consequence(status: str, outcome: str | None) -> str:
    base = "G3 cannot be PASS: lunar shadow behaviour and impact-melt context are not recorded. "
    if status == "FAIL":
        return base + "Suspected processing error: do not claim the Holuhraun reproduction."
    if outcome == "discrepant":
        return base + (
            "Claim narrowed: the Holuhraun check is a partial plausibility check with an "
            "explained discrepancy; no replication of the published overlap is claimed."
        )
    return base + "The Holuhraun plausibility check is inside the archived tolerance; partial G3."


def _discrepancies(
    primary: Mapping[str, Any], protocol: Mapping[str, Any], applicability: Mapping[str, Any]
) -> dict[str, Any]:
    rule = protocol["plausibility_rule"]
    out: dict[str, Any] = {
        "published_overlap_range": rule["published_overlap_range"],
        "published_mean_cpr": rule["published_mean_cpr"],
        "neish_2017": {
            "targets": protocol["neish_2017_target"].get("values"),
            "applicability": dict(applicability),
            "note": "Reported values are not compared with the Neish targets: see mismatches.",
        },
        "possible_reasons": protocol.get("possible_reasons_for_difference", []),
    }
    comparison = primary.get("comparison", {})
    if comparison.get("status") == "computed":
        out["measured_overlaps"] = comparison["overlaps"]
        out["plausibility"] = primary.get("plausibility")
    return out


# --- check -----------------------------------------------------------------------------------


def check() -> int:
    """Offline consistency of the committed records; reads no scene data."""
    problems: list[str] = []
    try:
        protocol = _load_protocol()
    except (OSError, ValueError) as error:
        print(f"protocol unreadable: {error}")
        return 1
    problems += _protocol_drift(protocol)
    repro_path = REPO_ROOT / REPRODUCTION_REL
    g3_path = REPO_ROOT / G3_REL
    if repro_path.exists():
        repro = json.loads(repro_path.read_text(encoding="utf-8"))
        if repro.get("protocol_sha256") != _sha256(REPO_ROOT / PROTOCOL_REL):
            problems.append("the reproduction record was made with a different protocol file")
        if repro.get("raw_inputs_sha256") != {
            k: protocol["raw_inputs_sha256"].get(k) for k in repro.get("raw_inputs_sha256", {})
        }:
            problems.append("the reproduction inputs differ from the protocol input hashes")
        primary = repro.get("variants", {}).get(repro.get("primary_variant", ""), {})
        outcome = primary.get("plausibility", {}).get("outcome")
        expected = g3_status(run=outcome is not None, outcome=outcome, other_parts_recorded=False)
        if repro.get("g3_status") != expected:
            problems.append(
                f"g3_status is {repro.get('g3_status')!r}, the logic gives {expected!r}"
            )
        if g3_path.exists():
            gate = json.loads(g3_path.read_text(encoding="utf-8"))
            if gate.get("status") != expected:
                problems.append(f"G3.json status {gate.get('status')!r} differs from {expected!r}")
            for item in gate.get("input_hashes", []):
                target = REPO_ROOT / item["path"]
                if not target.is_file() or _sha256(target) != item["sha256"]:
                    problems.append(f"G3.json input hash is stale: {item['path']}")
    elif g3_path.exists():
        problems.append("G3.json exists without a reproduction record")
    if problems:
        print("\n".join(problems))
        return 1
    print("ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terraluna_pipeline.holuhraun", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the reproduction on the scene named by " + SCENE_ENV)
    sub.add_parser("check", help="offline consistency check of the committed records")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run":
        return run(["run"])
    return check()


if __name__ == "__main__":
    sys.exit(main())
