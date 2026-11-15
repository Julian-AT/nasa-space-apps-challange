"""Uncertainty (PRD 6.6): PGDA78 clone ensemble, scenario runs and separate components.

Measurement error (T6-01). PGDA product 78 publishes 100 elevation "clones" of the Site01 LDEM
(Barker et al. 2021, doi:10.1016/j.pss.2020.105119). Each clone is a whole, spatially correlated
realization of the DEM under the published error model, on the same 5 m grid as the surface
product (checked on the files: same shape, transform and value range). Every clone goes through
the unchanged D29 resampler (:func:`geometry.resample_window`) and the unchanged T3
:func:`terrain.terrain_fingerprint`; this module never generates noise of its own, and its public
API has no noise, seed or random-state parameter (PRD 6.6: correlated terrain error is never
imitated with independent pixel noise).

Spread per descriptor (relief and texture nu at each configured lag, and H): the median, the 2.5
and 97.5 percentiles across clones (:data:`PERCENTILE_ESTIMATOR`), min, max and n. A clone for
which T3 reports a descriptor unavailable is counted in ``n_unavailable`` with its T3 reason and
left out of that descriptor's spread; it is never entered as 0 or NaN.

Scenario and component summary (T6-02) is in :func:`summarize_uncertainty`: the four PRD 6.6
categories stay separate named components and are never summed or merged; rank stability is a
frequency over runs, never a probability.

Clone file layout (a T1 fact, read from the PGDA product 78 page and the files):
``Site01/Clones/Site01_final_adj_5mpp_<NNNN>_err.tif``, NNNN = 0001..0100, float32, NaN fill,
same grid as ``Site01_final_adj_5mpp_surf.tif``. Despite the ``_err`` suffix the values are
elevations (metres), not error fields: their range matches the surface product.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from terraluna_pipeline import geometry, io
from terraluna_pipeline.config import TerrainConfig, load_terrain_config
from terraluna_pipeline.export import canonical_json
from terraluna_pipeline.provenance import command_line, config_version
from terraluna_pipeline.terrain import ISOTROPIC_RULES, IsotropicRule, TerrainFingerprint

CLONE_LIMITS_NOTE: Final = (
    "The PGDA78 clones cover the published topographic error model only. They say nothing "
    "about radar calibration, sub-pixel rocks or Earth field truth (PRD 6.6)."
)
PERCENTILE_ESTIMATOR: Final = (
    "numpy.percentile method='linear' (Hyndman and Fan 1996 type 7) over the clones that have "
    "the descriptor"
)
CLONE_BODIES: Final = frozenset({"moon"})
"""Bodies with a published clone set: only the lunar PGDA78 DEM."""

PGDA78_PAGE_URL: Final = "https://pgda.gsfc.nasa.gov/products/78"
PGDA78_SPHERE_RADIUS_M: Final = 1737400.0
"""The sphere of the PGDA78 polar stereographic CRS (its WKT states SPHEROID 1737400, 0)."""

_CLONE_NAME = re.compile(r"^Site\d+_final_adj_5mpp_(\d{4})_err\.tif$")
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)

EXIT_OK: Final = 0
EXIT_CHECK_FAILED: Final = 1
EXIT_BLOCKED: Final = 3


class UncertaintyError(ValueError):
    """Raised for inputs the uncertainty summaries cannot use."""


# --- Descriptors of one fingerprint ------------------------------------------------------------

_VARIANT_KEYS: Final = {"none": "relief", "plane": "texture"}


def _lag_key(lag_m: float) -> str:
    return f"{lag_m:g}"


def fingerprint_descriptors(
    fingerprint: TerrainFingerprint,
) -> dict[str, tuple[float | None, str | None]]:
    """Each descriptor of a T3 fingerprint as ``(value, reason)``; exactly one is None.

    Names are ``relief.nu_m@<lag>``, ``texture.nu_m@<lag>``, ``relief.H`` and ``texture.H``.
    The reason is the T3 reason (variant, lag support or H fit), never invented here.
    """
    out: dict[str, tuple[float | None, str | None]] = {}
    for policy, variant in fingerprint.variants.items():
        prefix = _VARIANT_KEYS[policy]
        for lag, support, nu in zip(
            fingerprint.lags_m, fingerprint.lags, variant.nu_m, strict=True
        ):
            if nu is not None:
                out[f"{prefix}.nu_m@{_lag_key(lag)}"] = (float(nu), None)
            else:
                reason = variant.reason or fingerprint.excluded_reason or support.reason
                out[f"{prefix}.nu_m@{_lag_key(lag)}"] = (None, str(reason or "unsupported"))
        hurst = variant.hurst
        if hurst.H is not None:
            out[f"{prefix}.H"] = (float(hurst.H), None)
        else:
            out[f"{prefix}.H"] = (None, str(variant.reason or hurst.reason or "unavailable"))
    return out


# --- Clone spread (T6-01) ----------------------------------------------------------------------


def _percentile(values: npt.NDArray[np.float64], q: float) -> float:
    return float(np.percentile(values, q, method="linear"))


def _spread(base: float | None, values: list[float], reasons: list[str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "base": base,
        "n": len(values),
        "n_unavailable": len(reasons),
        "unavailable_reasons": dict(sorted(Counter(reasons).items())),
    }
    if not values:
        entry.update(
            status="unavailable",
            reason=(sorted(set(reasons))[0] if reasons else "no_usable_clones"),
            median=None,
            p2_5=None,
            p97_5=None,
            min=None,
            max=None,
        )
        return entry
    array = np.asarray(values, dtype=np.float64)
    entry.update(
        status="available",
        reason=None,
        median=_percentile(array, 50.0),
        p2_5=_percentile(array, 2.5),
        p97_5=_percentile(array, 97.5),
        min=float(array.min()),
        max=float(array.max()),
    )
    return entry


def clone_spread(
    base: TerrainFingerprint,
    clones: Sequence[tuple[str, TerrainFingerprint]],
    *,
    body: str,
    n_requested: int,
) -> dict[str, Any]:
    """Summarize descriptor spread across clone fingerprints (PRD 6.6 measurement error).

    ``base`` is the fingerprint of the published DEM, ``clones`` the ``(clone_id, fingerprint)``
    pairs actually processed, in processing order, and ``n_requested`` the number of clones the
    run asked for. Bodies other than the Moon return Unavailable (no published clone set); no
    usable clone returns Unavailable (``no_usable_clones``), never a spread of 0.
    """
    if isinstance(n_requested, bool) or not isinstance(n_requested, int) or n_requested < 0:
        raise UncertaintyError(f"n_requested must be a non-negative integer, got {n_requested!r}")
    if len(clones) > n_requested:
        raise UncertaintyError(f"{len(clones)} clones used but only {n_requested} requested")
    ids = [clone_id for clone_id, _ in clones]
    if len(set(ids)) != len(ids):
        raise UncertaintyError("clone ids must be unique")
    result: dict[str, Any] = {
        "body": body,
        "n_requested": n_requested,
        "n_used": len(clones),
        "clone_ids": ids,
        "count_statement": f"{len(clones)} of {n_requested} requested clones used",
        "percentile_estimator": PERCENTILE_ESTIMATOR,
        "labels": {
            "base": "Derived",
            "base_note": "computed by T3 from the published (Observed) PGDA78 surface DEM",
            "spread": "Derived",
        },
        "note": CLONE_LIMITS_NOTE,
        "descriptors": {},
        "per_clone": {},
    }
    if body not in CLONE_BODIES:
        result.update(status="unavailable", reason="no_published_clone_set", n_used=0, clone_ids=[])
        result["count_statement"] = f"0 of {n_requested} requested clones used"
        return result
    if not clones:
        result.update(status="unavailable", reason="no_usable_clones")
        return result
    base_values = fingerprint_descriptors(base)
    per_clone = {clone_id: fingerprint_descriptors(fp) for clone_id, fp in clones}
    descriptors: dict[str, Any] = {}
    for name, (base_value, _) in base_values.items():
        values: list[float] = []
        reasons: list[str] = []
        for clone_id in ids:
            value, reason = per_clone[clone_id].get(name, (None, "descriptor_missing"))
            if value is None:
                reasons.append(reason or "unavailable")
            else:
                values.append(value)
        descriptors[name] = _spread(base_value, values, reasons)
    result.update(status="available", reason=None, descriptors=descriptors)
    result["per_clone"] = {
        clone_id: {name: value for name, (value, _) in values.items()}
        for clone_id, values in per_clone.items()
    }
    return result


# --- Window geometry and clone reading ---------------------------------------------------------


@dataclass(frozen=True)
class MoonWindow:
    """A frozen Site01 window: id and centre (planetocentric degrees) from the G0 evidence."""

    window_id: str
    lon_deg: float
    lat_deg: float


def load_windows(path: Path) -> list[MoonWindow]:
    """Read the frozen Site01 windows (G0-02) from outputs/g0/windows-evidence.json."""
    import json

    document = json.loads(path.read_text(encoding="utf-8"))
    block = document["g0_02_moon"]
    windows = []
    for item in block["windows"]:
        lon, lat = item["centre_lonlat_deg"]
        windows.append(MoonWindow(str(item["window_id"]), float(lon), float(lat)))
    return windows


def search_windows(raster: io.RasterWindow, config: TerrainConfig) -> list[MoonWindow]:
    """The Site01 window grid exactly as ``search.evaluate_moon`` builds it.

    The grid and the window centres come from ``search.projected_grid`` over the raster extent
    with the raster's own polar stereographic lookup, so a clone run on the surface product
    reproduces the search fingerprints bit for bit (T5 is the producer of the windows).
    """
    from terraluna_pipeline.search import projected_grid

    lookup = _lookup(raster)
    transform = raster.transform
    assert transform is not None
    rows, cols = raster.array.shape
    x0, y1 = float(transform.c), float(transform.f)
    x1 = x0 + float(transform.a) * cols
    y0 = y1 + float(transform.e) * rows
    grid = projected_grid(
        prefix="moon-sps",
        body="moon",
        frame="polar_stereographic",
        crs=raster.crs or "",
        extent_m=(x0, y0, x1, y1),
        window_m=config.window_m,
        stride_m=config.stride_m,
        unproject=lambda x, y: lookup.unproject(x, y),
    )
    return [MoonWindow(g.window_id, g.centre_lon_deg, g.centre_lat_deg) for g in grid]


def _lookup(raster: io.RasterWindow) -> geometry.PolarStereographicLookup:
    transform = raster.transform
    if transform is None:
        raise UncertaintyError("the clone raster has no geotransform")
    return geometry.PolarStereographicLookup(
        pole="south",
        central_meridian_deg=0.0,
        latitude_of_true_scale_deg=-90.0,
        scale_factor=1.0,
        radius_m=PGDA78_SPHERE_RADIUS_M,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=float(transform.c),
        origin_y_m=float(transform.f),
        pixel_width_m=float(transform.a),
        pixel_height_m=float(transform.e),
    )


def window_fingerprints(
    raster: io.RasterWindow,
    windows: Sequence[MoonWindow],
    *,
    config: TerrainConfig,
    isotropic_rule: IsotropicRule,
) -> dict[str, TerrainFingerprint]:
    """Fingerprint each window of one PGDA78 raster (surface or clone) via D29 and T3."""
    lookup = _lookup(raster)
    grid = geometry.window_grid(config.window_m, config.analysis_grid_m)
    out: dict[str, TerrainFingerprint] = {}
    from terraluna_pipeline.terrain import terrain_fingerprint

    for window in windows:
        resampled = geometry.resample_window(
            raster.array,
            lookup,
            grid=grid,
            body="moon",
            lat0_rad=math.radians(window.lat_deg),
            lon0_rad=math.radians(window.lon_deg),
            nodata=raster.nodata,
        )
        out[window.window_id] = terrain_fingerprint(
            resampled.values,
            valid=resampled.valid,
            grid=grid,
            config=config,
            isotropic_rule=isotropic_rule,
            coarsening_factor=1.0,
        )
    return out


def clone_id(filename: str) -> str:
    """The four-digit clone id of a PGDA78 clone file name."""
    match = _CLONE_NAME.match(filename)
    if match is None:
        raise ValueError(f"{filename!r} is not a PGDA78 clone file name")
    return match.group(1)


# --- fetch-clones (the only network command) ---------------------------------------------------


def parse_clone_links(page: str, *, site: str) -> list[str]:
    """Clone GeoTIFF links of ``site`` on the PGDA product 78 page, sorted by clone id.

    The provider's Clones/ directory listing returns HTTP 404, so the product page's links are
    the file list (recorded in the raw PROVENANCE.json).
    """
    links = set()
    for href in _HREF.findall(page):
        name = href.rsplit("/", 1)[-1]
        if f"/{site}/Clones/" in href and _CLONE_NAME.match(name) and name.startswith(site + "_"):
            links.add(href)
    return sorted(links, key=lambda link: clone_id(link.rsplit("/", 1)[-1]))


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "terraluna-pipeline"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data: bytes = response.read()
    return data


def fetch_clones(
    *,
    page_url: str,
    cache_dir: Path,
    opener: Callable[[str], bytes] = _http_get,
    site: str = "Site01",
) -> list[dict[str, Any]]:
    """Download the clone files listed on the product page into ``cache_dir`` with sha256."""
    import hashlib

    page = opener(page_url).decode("utf-8", errors="replace")
    links = parse_clone_links(page, site=site)
    cache_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for link in links:
        name = link.rsplit("/", 1)[-1]
        target = cache_dir / name
        if not target.exists():
            data = opener(link)
            partial = target.with_suffix(".part")
            partial.write_bytes(data)
            partial.replace(target)
        records.append(
            {
                "clone_id": clone_id(name),
                "file": name,
                "url": link,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "bytes": target.stat().st_size,
            }
        )
    return records


# --- Scenario runs and the four components (T6-02) ---------------------------------------------

FAMILIES: Final = ("terrain_relief", "terrain_texture", "radar_cpr", "model_embedding")


def _unavailable(reason: str, *, note: str | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "evidence_label": "Unavailable",
        "reason": reason,
        "note": note,
        "by_family": None,
        "n_requested": None,
        "n_used": None,
        "scenarios": None,
        "rank_frequency": None,
    }


def _family_spread(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "spread_min": float(array.min()),
        "spread_median": float(np.median(array)),
        "spread_max": float(array.max()),
        "n": len(values),
    }


def rank_frequency(
    ranks_by_run: Sequence[Mapping[str, int | None]], *, candidate: str, rank: int = 1
) -> dict[str, Any]:
    """How often ``candidate`` held ``rank`` over the runs: a frequency, never a probability.

    A run in which the candidate is absent (ineligible) counts as a run without that rank, not
    as last place.
    """
    n = len(ranks_by_run)
    if n == 0:
        raise UncertaintyError("rank frequency needs at least one run")
    count = sum(1 for ranks in ranks_by_run if ranks.get(candidate) == rank)
    return {"frequency": count / n, "count": count, "n": n}


def summarize_uncertainty(
    *,
    body: str,
    heterogeneity: Mapping[str, Sequence[float]] | None,
    clone_distances: Mapping[str, Sequence[float]] | None,
    clone_n_requested: int | None,
    scenarios: Sequence[Mapping[str, Any]] | None,
    rank_frequency_by_family: Mapping[str, Mapping[str, Any]] | None,
    predictor_result: Mapping[str, Any] | None = None,
    shipping_mode: str | None = None,
    g5_status: str = "NOT_RUN",
) -> dict[str, Any]:
    """Build an UncertaintyComponents dict (CONTRACTS a.4) with the four categories separate.

    ``heterogeneity`` maps a family to within-window values (distribution summary, not a
    confidence interval). ``clone_distances`` maps a terrain family to its distance per clone;
    only terrain families take clone spreads, and only on the Moon. ``scenarios`` are the caller's
    declared runs, each ``{name, change, values_by_family, rank_by_family}``; this module has no
    default incidence bins or footprint sizes. In Mode B or C no radar entry is produced and a
    band-assumption scenario is skipped. The encoder family is excluded from rank frequency while
    G5 is not PASS. No combined scalar exists in the output.
    """
    radar_allowed = shipping_mode == "A"
    encoder_ranked = g5_status == "PASS"

    def keep(family: str) -> bool:
        if family == "radar_cpr" and not radar_allowed:
            return False
        return not (family == "model_embedding" and not encoder_ranked)

    if heterogeneity:
        het = {
            "status": "available",
            "evidence_label": "Derived",
            "reason": None,
            "note": "Spread of values across the window; a distribution summary, not a "
            "confidence interval (PRD 6.6).",
            "by_family": {
                family: _family_spread(values)
                for family, values in sorted(heterogeneity.items())
                if values and (family != "radar_cpr" or radar_allowed)
            },
            "n_requested": None,
            "n_used": None,
            "scenarios": None,
            "rank_frequency": None,
        }
    else:
        het = _unavailable("heterogeneity_not_supplied")

    if body not in CLONE_BODIES:
        measurement = _unavailable("no_published_clone_set")
    elif not clone_distances:
        measurement = _unavailable("clone_results_missing")
    else:
        families = {
            family: values
            for family, values in sorted(clone_distances.items())
            if family.startswith("terrain_") and values
        }
        n_used = max((len(values) for values in families.values()), default=0)
        if not families:
            measurement = _unavailable("clone_results_missing")
        else:
            measurement = {
                "status": "available",
                "evidence_label": "Derived",
                "reason": None,
                "note": CLONE_LIMITS_NOTE,
                "by_family": {f: _family_spread(v) for f, v in families.items()},
                "n_requested": clone_n_requested,
                "n_used": n_used,
                "scenarios": None,
                "rank_frequency": None,
            }

    if predictor_result is None:
        prediction = _unavailable("no_predictor_evaluated", note="no predictor evaluated")
    else:
        prediction = _unavailable("predictor_not_integrated", note="predictor result not wired")

    if scenarios:
        entries = []
        for scenario in scenarios:
            name = str(scenario["name"])
            if name.startswith("band") and not radar_allowed:
                continue
            entries.append(
                {
                    "name": name,
                    "change": str(scenario["change"]),
                    "values_by_family": {
                        k: v for k, v in scenario["values_by_family"].items() if keep(k)
                    },
                    "rank_by_family": {
                        k: v for k, v in scenario["rank_by_family"].items() if keep(k)
                    },
                }
            )
        rank_freq = None
        if rank_frequency_by_family:
            rank_freq = {k: dict(v) for k, v in rank_frequency_by_family.items() if keep(k)}
        scen = {
            "status": "available",
            "evidence_label": "Derived",
            "reason": None,
            "note": "Named scenario runs with no probability attached (PRD 6.6)."
            + (
                ""
                if radar_allowed
                else " Mode B/C: no cross-band radar match; band scenario skipped."
            ),
            "by_family": None,
            "n_requested": None,
            "n_used": None,
            "scenarios": entries,
            "rank_frequency": rank_freq or None,
        }
    elif rank_frequency_by_family:
        scen = {
            "status": "available",
            "evidence_label": "Derived",
            "reason": None,
            "note": "Rank stability across clone runs, as a frequency; never a probability.",
            "by_family": None,
            "n_requested": None,
            "n_used": None,
            "scenarios": None,
            "rank_frequency": {k: dict(v) for k, v in rank_frequency_by_family.items() if keep(k)}
            or None,
        }
    else:
        scen = _unavailable("scenarios_not_run")

    return {
        "heterogeneity": het,
        "measurement_error": measurement,
        "statistical_prediction_error": prediction,
        "scenario_sensitivity": scen,
    }


# --- annotate (T6-09): uncertainty into the comparisons ---------------------------------------

STALE_CLAIM: Final = (
    "PGDA78 error spatial correlation is not propagated: the uncertainty components (clones, "
    "scenarios) have not been run."
)
OLD_CLONE_CLAIM: Final = (
    "PGDA78 clone measurement error is propagated for the lunar target window only (clone "
    "counts in uncertainty_components); no Earth DEM error model is applied, and ties are not "
    "set in distances_by_family because the target has no reference set (schema: tied needs a "
    "reference spread); clone-based ties are listed in analysis/g4_candidates.json."
)
CLONE_CLAIM: Final = (
    "PGDA78 clone measurement error is propagated for the lunar target window only (clone "
    "counts in uncertainty_components); no Earth DEM error model is applied. Terrain ties use "
    "matching.tie_groups with each candidate's clone spread (half the 2.5-97.5 percentile range "
    "of its distance), measurement error only."
)
NO_CLONE_CLAIM: Final = (
    "No published elevation-error clone set exists for this target's DTM, so measurement error "
    "is Unavailable and no tie is claimed; scenario ranks come from mask and region filters only."
)
_REPLACED_CLAIMS: Final = (STALE_CLAIM, OLD_CLONE_CLAIM, CLONE_CLAIM, NO_CLONE_CLAIM)
TERRAIN: Final = ("terrain_relief", "terrain_texture")


def _rank_in(rows: Sequence[Mapping[str, Any]], window_id: str, family: str) -> int | None:
    values = {r["window_id"]: r[family]["base"] for r in rows if r[family]["base"] is not None}
    if window_id not in values:
        return None
    return 1 + sum(1 for v in values.values() if v < values[window_id])


def annotated_components(g4: Mapping[str, Any], window_id: str) -> dict[str, Any]:
    """UncertaintyComponents for one candidate from the G4 clone and scenario results."""
    rows = {r["window_id"]: r for r in g4["candidates"]}
    row = rows.get(window_id)
    het = _unavailable(
        "heterogeneity_not_computed",
        note="Within-window spread of the distance is not computed for this slice.",
    )
    prediction = _unavailable("no_predictor_evaluated", note="no predictor evaluated")
    if row is None:
        reason = "window_not_eligible"
        return {
            "heterogeneity": het,
            "measurement_error": _unavailable(reason),
            "statistical_prediction_error": prediction,
            "scenario_sensitivity": _unavailable(reason),
        }
    sensitivity = g4["rank_sensitivity"]
    by_family = {}
    for family in TERRAIN:
        entry = row[family]
        if entry.get("n"):
            by_family[family] = {
                "spread_min": entry["min"],
                "spread_median": entry["median"],
                "spread_max": entry["max"],
                "n": entry["n"],
            }
    measurement = (
        {
            "status": "available",
            "evidence_label": "Derived",
            "reason": None,
            "note": CLONE_LIMITS_NOTE
            + " Spread of this candidate's distance when the target is replaced by each clone.",
            "by_family": by_family,
            "n_requested": sensitivity["n_requested"],
            "n_used": sensitivity["n_runs"],
            "scenarios": None,
            "rank_frequency": None,
        }
        if by_family
        else _unavailable(
            "clone_results_missing"
            if g4.get("body", "moon") == "moon"
            else "no_published_clone_set",
            note=None if g4.get("body", "moon") == "moon" else CLONE_LIMITS_NOTE,
        )
    )
    table = g4["candidates"]
    scenarios = []
    for scenario in g4["scenario_sensitivity"]:
        name = scenario["name"]
        if name == "pipeline":
            kept = table
        elif name.startswith("worldcover_allowed_ge_"):
            threshold = float(name.removeprefix("worldcover_allowed_ge_"))
            kept = [
                r
                for r in table
                if (r["valid_fraction_by_observable"].get("worldcover_allowed") or 0.0) >= threshold
            ]
        elif name.startswith("region_") and name.endswith("_only"):
            region = name.removeprefix("region_").removesuffix("_only")
            kept = [r for r in table if r["search_region_id"] == region]
        else:
            continue
        present = any(r["window_id"] == window_id for r in kept)
        scenarios.append(
            {
                "name": name,
                "change": scenario["change"],
                "values_by_family": {f: (row[f]["base"] if present else None) for f in TERRAIN},
                "rank_by_family": {f: _rank_in(kept, window_id, f) for f in TERRAIN},
            }
        )
    freq = row.get("rank_frequency") or {}
    rank_frequency = {f: dict(freq[f"rank_1_{f}"]) for f in TERRAIN if freq.get(f"rank_1_{f}")}
    scen = {
        "status": "available",
        "evidence_label": "Derived",
        "reason": None,
        "note": (
            "Named mask and region scenarios with the rank by family in each; rank_frequency is "
            "how often this candidate ranked first by that family over the PGDA78 clone runs (none "
            "without a clone set), a frequency and never a probability (PRD 6.6). Mode C: no radar "
            "scenario."
        ),
        "by_family": None,
        "n_requested": None,
        "n_used": None,
        "scenarios": scenarios,
        "rank_frequency": rank_frequency or None,
    }
    return {
        "heterogeneity": het,
        "measurement_error": measurement,
        "statistical_prediction_error": prediction,
        "scenario_sensitivity": scen,
    }


def annotate_records(
    records: Sequence[Mapping[str, Any]], g4: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Comparison records with uncertainty_components filled; order and other fields kept."""
    from terraluna_pipeline.schema import UncertaintyComponents

    body = str(g4.get("body", "moon"))
    claim = CLONE_CLAIM if body == "moon" else NO_CLONE_CLAIM
    ties = g4.get("ties") or {}
    out = []
    for record in records:
        updated = dict(record)
        window = str(record["earth_window_id"])
        components = annotated_components(g4, window)
        UncertaintyComponents.model_validate(components)
        updated["uncertainty_components"] = components
        distances = {family: dict(entry) for family, entry in record["distances_by_family"].items()}
        for family in TERRAIN:
            membership = (ties.get(family) or {}).get("by_window", {}).get(window)
            entry = distances[family]
            if membership is None or entry.get("reference_spread") is None:
                continue
            entry.update(
                tied=membership["tied"],
                tie_group=membership["tie_group"],
                tie_reason=membership["tie_reason"],
            )
        updated["distances_by_family"] = distances
        limits: list[str] = []
        for line in record["claim_limits"]:
            if line in _REPLACED_CLAIMS:
                if claim not in limits:
                    limits.append(claim)
            elif line not in limits:
                limits.append(line)
        if claim not in limits:
            limits.append(claim)
        updated["claim_limits"] = limits
        out.append(updated)
    return out


def _annotate(args: argparse.Namespace, *, check: bool) -> int:
    import json

    from terraluna_pipeline.export import write_comparisons

    root = Path(args.root)
    path = root / "outputs" / "comparisons" / f"{args.target_id}.json"
    g4_name = (
        "g4_candidates.json"
        if args.target_id == "moon-cr-site01-w000"
        else f"g4_candidates_{args.target_id}.json"
    )
    g4_path = root / "analysis" / g4_name
    missing = [str(p) for p in (path, g4_path) if not p.is_file()]
    if missing:
        print(f"blocked: missing {', '.join(missing)}")
        return EXIT_BLOCKED
    records = json.loads(path.read_text(encoding="utf-8"))
    g4 = json.loads(g4_path.read_text(encoding="utf-8"))
    annotated = annotate_records(records, g4)
    if check:
        from terraluna_pipeline.schema import ComparisonRecord

        expected = canonical_json(
            [ComparisonRecord.model_validate(r).model_dump(mode="json") for r in annotated]
        )
        if expected != path.read_bytes():
            print(f"check failed: {path} differs from the annotation of its inputs")
            return EXIT_CHECK_FAILED
        print(f"{path.name} matches its annotation")
        return EXIT_OK
    write_comparisons(path, annotated)
    return EXIT_OK


# --- CLI ---------------------------------------------------------------------------------------

DEFAULT_CONFIG: Final = Path("config/run.yaml")
DEFAULT_WINDOWS: Final = Path("outputs/g0/windows-evidence.json")
DEFAULT_OUT: Final = Path("outputs/uncertainty")


def _pgda_dir(value: str | None) -> Path:
    if value:
        return Path(value)
    env = os.environ.get("TERRALUNA_PGDA78_DIR")
    return Path(env) if env else Path("data/raw/pgda78")


def _run_clones(args: argparse.Namespace) -> int:
    pgda = _pgda_dir(args.pgda_dir)
    surf = pgda / "Site01" / "Site01_final_adj_5mpp_surf.tif"
    clones_dir = pgda / "Site01" / "Clones"
    windows_path = Path(args.windows)
    needed = [surf, clones_dir] + ([windows_path] if args.window_source == "g0" else [])
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        print(f"blocked: missing input {', '.join(missing)}")
        return EXIT_BLOCKED
    files = sorted(p for p in clones_dir.iterdir() if _CLONE_NAME.match(p.name))
    if not files:
        print(f"blocked: no clone files in {clones_dir}")
        return EXIT_BLOCKED
    n_requested = int(args.n_clones)
    files = files[:n_requested]
    config = load_terrain_config(Path(args.config))
    surface = io.read_geotiff_window(surf)
    windows = (
        search_windows(surface, config)
        if args.window_source == "search"
        else load_windows(windows_path)
    )
    if args.window_id:
        wanted = set(args.window_id)
        windows = [w for w in windows if w.window_id in wanted]
        if not windows:
            print(f"blocked: window ids {sorted(wanted)} not in {windows_path}")
            return EXIT_BLOCKED
    rules: tuple[IsotropicRule, ...] = ISOTROPIC_RULES
    base = {
        rule: window_fingerprints(surface, windows, config=config, isotropic_rule=rule)
        for rule in rules
    }
    per_clone: dict[str, dict[IsotropicRule, dict[str, TerrainFingerprint]]] = {}
    hashes = {surf.name: io.sha256_file(surf)}
    for path in files:
        cid = clone_id(path.name)
        raster = io.read_geotiff_window(path)
        hashes[path.name] = io.sha256_file(path)
        per_clone[cid] = {
            rule: window_fingerprints(raster, windows, config=config, isotropic_rule=rule)
            for rule in rules
        }
        print(f"clone {cid} done", file=sys.stderr)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for window in windows:
        record: dict[str, Any] = {
            "record_type": "terraluna pgda78 clone ensemble",
            "window_id": window.window_id,
            "product_id": "PGDA78/Site01",
            "source_files_sha256": dict(sorted(hashes.items())),
            "command": command_line("terraluna_pipeline.uncertainty", args.argv),
            "config_version": config_version(Path(args.config)),
            "window_source": (
                "search.projected_grid over the Site01 surface extent (as search.evaluate_moon)"
                if args.window_source == "search"
                else f"G0 evidence {windows_path}"
            ),
            "windows_evidence_sha256": (
                io.sha256_file(windows_path) if args.window_source == "g0" else None
            ),
            "resampler": "D29: geometry.resample_window (spherical AEQD about the window centre, "
            "bilinear, PGDA78 sphere 1737400 m)",
            "by_isotropic_rule": {
                rule: clone_spread(
                    base[rule][window.window_id],
                    [(cid, per_clone[cid][rule][window.window_id]) for cid in per_clone],
                    body="moon",
                    n_requested=n_requested,
                )
                for rule in rules
            },
        }
        (out_dir / f"clones_{window.window_id}.json").write_bytes(canonical_json(record))
    return EXIT_OK


def _fetch_clones(args: argparse.Namespace) -> int:
    pgda = _pgda_dir(args.pgda_dir)
    records = fetch_clones(page_url=args.page_url, cache_dir=pgda / "Site01" / "Clones")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sources.json").write_bytes(
        canonical_json(
            {"product_id": "PGDA78/Site01 clones", "page": args.page_url, "files": records}
        )
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the uncertainty CLI; see the module docstring."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="python -m terraluna_pipeline.uncertainty")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch-clones", help="download the PGDA78 Site01 clones (network)")
    fetch.add_argument("--pgda-dir", default=None)
    fetch.add_argument("--page-url", default=PGDA78_PAGE_URL)
    fetch.add_argument("--out-dir", default=str(DEFAULT_OUT))
    run = sub.add_parser("run-clones", help="recompute descriptors per clone (cache only)")
    run.add_argument("--pgda-dir", default=None)
    run.add_argument("--windows", default=str(DEFAULT_WINDOWS))
    run.add_argument("--config", default=str(DEFAULT_CONFIG))
    run.add_argument("--out-dir", default=str(DEFAULT_OUT))
    run.add_argument("--window-id", action="append", default=None)
    run.add_argument("--n-clones", type=int, default=100)
    run.add_argument("--window-source", choices=["search", "g0"], default="search")
    for name, text in (
        ("annotate", "write uncertainty into the comparisons"),
        ("verify", "offline twin"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--target-id", default="moon-cr-site01-w000")
        command.add_argument("--root", default=".")
    args = parser.parse_args(arguments)
    args.argv = arguments
    if args.command in ("annotate", "verify"):
        return _annotate(args, check=args.command == "verify")
    if args.command == "fetch-clones":
        return _fetch_clones(args)
    return _run_clones(args)


if __name__ == "__main__":
    raise SystemExit(main())
