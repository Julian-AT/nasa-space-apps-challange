"""Fingerprint records and window assembly (PRD 6.2, 6.3, 7, 8.11, 8.12; CONTRACTS a.4, a.5).

This module converts already-measured values into the t0b records of
:mod:`terraluna_pipeline.schema`. It decodes no product and computes no nu and no block
statistic: terrain values are copied from the T3 :func:`terraluna_pipeline.terrain.
terrain_fingerprint` output, radar block statistics from the T2 polarimetry functions
(review-science #6).

Public API
    ``lag_key(lag_m) -> str``
        The string key of a lag in ``nu_m`` and ``nu_by_direction_m``: the shortest round-trip
        decimal of the float, written without a fraction when the lag is a whole number of
        metres (``270.0`` -> ``"270"``), which is also how ``JSON.stringify`` and
        ``String(270)`` write it in TypeScript. T3 holds nu as tuples aligned with
        ``TerrainFingerprint.lags_m``; each key is that lag. Exponent forms differ between
        Python and TypeScript, so a lag that would need one (below 1e-4 m or from 1e16 m) is
        refused, as are non-positive and non-finite lags.
    ``input_hash(*, arrays, rings, settings) -> str``
        The 64-hex SHA-256 over the named input arrays (dtype, shape and little-endian bytes;
        a numpy mask is hashed with its array), the named polygon rings and the settings, as
        canonical JSON. A changed array, polygon or setting changes it (PRD 12 invalidation).
    ``terrain_record(fp, *, window_id, body, products, config_path, input_hash)``
        The kind ``terrain`` FingerprintRecord of one T3 fingerprint. ``products`` are
        ``ProductRecord``\\ s or ``(product_id, sha256 | None)`` pairs; ``product_ids`` lists
        them in the given order and ``run_id`` is ``provenance.run_id(config_path, products)``
        (CONTRACTS c.4).
    ``build_window_record(*, window_id, body, polygon, ..., status, reasons, ...) -> WindowEntry``
        One row of ``windows/{body}.json``; see its docstring.

Terrain mapping (CONTRACTS a.4)
    ``relief`` is the T3 variant of detrend ``none`` and ``texture`` that of detrend
    ``plane``; both are required. For each, ``nu_m`` holds the isotropic nu at every
    configured lag and ``nu_by_direction_m`` the per-direction nu, keyed by T3's direction
    names (``x``, ``y``, ``diagonal_ne``, ``diagonal_nw``) and then by :func:`lag_key`; an
    unsupported value is null, never 0. ``supported_lags_m`` are T3's supported lags when the
    variant is available, else none. ``unsupported_lags`` gives every other configured lag
    with T3's lag reason, or, for a lag T3 supports in a variant T3 reports unavailable (a
    degenerate plane), the variant's reason. ``hurst`` and ``hurst_reason`` are T3's ``H`` and
    reason. ``tilt`` is ``[slope_east, slope_north, intercept_m]`` of the T3 plane (slopes in
    metres per metre, x east and y north in the D29 frame) on texture and null on relief or a
    texture without a plane. ``coarsening_factor`` and ``valid_fraction`` are copied; the
    resampler is the declared D29 ``aeqd_sphere``. The record is ``Derived`` when T3 supports at
    least one lag, else ``Unavailable`` (an excluded window).

Window flags (CONTRACTS a.4; PRD 5, 6.1, 10)
    ``valid_fraction_by_observable`` has ``terrain`` (the T3 valid fraction, null without a
    terrain record) and, per radar fingerprint, ``radar_cpr:<product_id>``; a window without
    radar has ``radar_cpr`` null. ``evidence_status_by_field`` has ``terrain_relief``,
    ``terrain_texture``, ``permanent_shadow_fraction`` and the radar keys of the same rule
    (``radar_cpr`` ``Unavailable`` when there is no radar product, never 0). Mars windows take
    no radar fingerprint (PRD 10: an "unavailable" note only).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import Final

import numpy as np
import numpy.typing as npt

from terraluna_pipeline import provenance
from terraluna_pipeline.schema import (
    Body,
    EnvironmentFlags,
    EvidenceLabel,
    FingerprintRecord,
    ObservedInterpolatedQuality,
    ProductRecord,
    TerrainFingerprint,
    TerrainVariant,
    UnsupportedLag,
    WindowEntry,
    WindowPolygon,
    WindowRecord,
    WindowStatus,
)
from terraluna_pipeline.terrain import DIRECTIONS
from terraluna_pipeline.terrain import TerrainFingerprint as T3Fingerprint
from terraluna_pipeline.terrain import TerrainVariant as T3Variant

__all__ = [
    "COPERNICUS_SURFACE_MODEL_NOTE",
    "RESAMPLER",
    "Product",
    "build_window_record",
    "input_hash",
    "lag_key",
    "terrain_record",
]

RESAMPLER: Final = "aeqd_sphere"
"""The declared D29 analysis-window resampler (PRD 7)."""

COPERNICUS_SURFACE_MODEL_NOTE: Final = (
    "Copernicus DEM GLO-30 is a surface model including vegetation and buildings; "
    "heights are relative to the EGM2008 geoid."
)
"""The PRD 5 note on every Earth window (PRD 5 source 11, D31)."""

EARTH_FLAG_UNCHECKED: Final = "unknown"
"""An Earth environment flag that applies but was not checked (CONTRACTS a.4)."""

Product = ProductRecord | tuple[str, str | None]
"""A product as ``provenance.run_id`` takes it: a ProductRecord or ``(id, sha256 | None)``."""

_KEY_MIN: Final = 1e-4
_KEY_MAX: Final = 1e16


def lag_key(lag_m: float) -> str:
    """The ``nu_m`` key of a lag in metres (see the module docstring)."""
    value = float(lag_m)
    if not (math.isfinite(value) and value > 0.0):
        raise ValueError(f"lag {lag_m!r} must be finite and positive")
    if not _KEY_MIN <= value < _KEY_MAX:
        raise ValueError(f"lag {lag_m!r} m would need an exponent in its key")
    if value.is_integer():
        return str(int(value))
    return repr(value)


# --- input_hash ---------------------------------------------------------------------------------


def _array_entry(name: str, array: npt.ArrayLike) -> dict[str, object]:
    mask: npt.NDArray[np.bool_] | None = None
    if isinstance(array, np.ma.MaskedArray):
        mask = np.ma.getmaskarray(array)
        data = np.asarray(np.ma.getdata(array))
    else:
        data = np.asarray(array)
    if data.dtype.kind not in "biuf":
        raise ValueError(f"array {name} has dtype {data.dtype}; only bool and numbers are hashed")
    little = np.ascontiguousarray(data, dtype=data.dtype.newbyteorder("<"))
    return {
        "dtype": little.dtype.str,
        "shape": list(little.shape),
        "sha256": hashlib.sha256(little.tobytes()).hexdigest(),
        "mask_sha256": None
        if mask is None
        else hashlib.sha256(np.ascontiguousarray(mask).tobytes()).hexdigest(),
    }


def input_hash(
    *,
    arrays: Mapping[str, npt.ArrayLike],
    rings: Mapping[str, Sequence[Sequence[float]]],
    settings: Mapping[str, object],
) -> str:
    """SHA-256 (64 lowercase hex) over the input arrays, polygon rings and settings.

    ``arrays`` are named numeric or boolean arrays, hashed by dtype, shape and little-endian
    bytes (so byte order does not matter, dtype and shape do); a masked array's mask is hashed
    too. ``rings`` are named lists of positions and ``settings`` any JSON values. NaN and
    infinities in rings or settings are refused.
    """
    document = {
        "arrays": {name: _array_entry(name, arrays[name]) for name in sorted(arrays)},
        "rings": {
            name: [[float(c) for c in position] for position in rings[name]] for name in rings
        },
        "settings": dict(settings),
    }
    try:
        text = provenance.canonical_json(document)
    except ValueError as error:
        raise ValueError(f"rings and settings must not hold NaN or infinity: {error}") from None
    return hashlib.sha256(text).hexdigest()


# --- terrain ------------------------------------------------------------------------------------


def _variant(fp: T3Fingerprint, source: T3Variant) -> TerrainVariant:
    keys = [lag_key(lag) for lag in fp.lags_m]
    available = source.reason is None
    unsupported: list[UnsupportedLag] = []
    for item in fp.lags:
        if item.reason is not None:
            unsupported.append(UnsupportedLag(lag_m=item.lag_m, reason=item.reason))
        elif source.reason is not None:
            unsupported.append(UnsupportedLag(lag_m=item.lag_m, reason=source.reason))
    tilt: list[float] | None = None
    if source.plane is not None:
        plane = source.plane
        tilt = [plane.slope_east, plane.slope_north, plane.intercept_m]
    return TerrainVariant(
        detrend=source.detrend,
        supported_lags_m=list(fp.supported_lags_m) if available else [],
        unsupported_lags=unsupported,
        nu_m=dict(zip(keys, source.nu_m, strict=True)),
        nu_by_direction_m={
            direction: dict(zip(keys, source.directional_nu_m[direction], strict=True))
            for direction in DIRECTIONS
        },
        hurst=source.hurst.H,
        hurst_reason=source.hurst.reason,
        tilt=tilt,
    )


def terrain_record(
    fp: T3Fingerprint,
    *,
    window_id: str,
    body: Body,
    products: Sequence[Product],
    config_path: str | PurePath,
    input_hash: str,
) -> FingerprintRecord:
    """The kind ``terrain`` FingerprintRecord of one T3 fingerprint (module docstring)."""
    if not isinstance(fp, T3Fingerprint):
        raise TypeError(f"fp must be a terrain.TerrainFingerprint, got {type(fp).__name__}")
    for policy in ("none", "plane"):
        if policy not in fp.variants:
            raise ValueError(f"the T3 fingerprint has no detrend {policy!r} variant")
    terrain = TerrainFingerprint(
        analysis_grid_m=fp.analysis_grid_m,
        coarsening_factor=fp.coarsening_factor,
        resampler=RESAMPLER,
        valid_fraction=fp.valid_fraction,
        relief=_variant(fp, fp.variants["none"]),
        texture=_variant(fp, fp.variants["plane"]),
    )
    label: EvidenceLabel = "Unavailable" if fp.excluded or not fp.supported_lags_m else "Derived"
    return FingerprintRecord(
        window_id=window_id,
        body=body,
        kind="terrain",
        product_ids=[_product_id(product) for product in products],
        evidence_label=label,
        input_hash=input_hash,
        run_id=provenance.run_id(config_path, products),
        terrain=terrain,
        radar=None,
    )


def _product_id(product: Product) -> str:
    return product.product_id if isinstance(product, ProductRecord) else product[0]


# --- window assembly --------------------------------------------------------------------------


def _variant_label(variant: TerrainVariant) -> EvidenceLabel:
    return "Derived" if any(nu is not None for nu in variant.nu_m.values()) else "Unavailable"


def build_window_record(
    *,
    window_id: str,
    body: Body,
    polygon: WindowPolygon,
    area_m2: float,
    analysis_grid_m: float,
    product_ids: Sequence[str],
    terrain: FingerprintRecord | None,
    radar: Sequence[FingerprintRecord],
    status: WindowStatus,
    reasons: Sequence[str],
    search_region_id: str | None,
    scene_ids: Sequence[str],
    acquisition_dates: Sequence[str],
    permanent_shadow_fraction: float | None,
    observed_interpolated_quality: ObservedInterpolatedQuality,
    grid_index: Sequence[int] | None,
    card_id: str | None,
) -> WindowEntry:
    """One WindowEntry: the WindowRecord, its fingerprints, and the caller's status.

    ``polygon`` is built by the caller from the product's own CRS. ``status`` and ``reasons``
    come from masks.py or T3 and are recorded as given. Earth environment flags are
    ``"unknown"`` and carry the Copernicus surface-model note; on the Moon and Mars they are
    null. ``permanent_shadow_fraction`` is only for the Moon; without a product it is null and
    ``Unavailable``. Every fingerprint must name this window and body and list only products in
    ``product_ids``; a terrain record must be on ``analysis_grid_m``. A Mars window takes no
    radar fingerprint. Raises ValueError for any of these mismatches.
    """
    if body == "mars" and radar:
        raise ValueError("Mars windows get no radar entries (PRD 10)")
    known = set(product_ids)
    for record in ([terrain] if terrain is not None else []) + list(radar):
        if record.window_id != window_id or record.body != body:
            raise ValueError(
                f"a {record.kind} fingerprint for {record.window_id} ({record.body}) does not "
                f"match window_id {window_id} ({body})"
            )
        missing = [pid for pid in record.product_ids if pid not in known]
        if missing:
            raise ValueError(f"fingerprint products {missing} are not in the window's product_ids")
    if (
        terrain is not None
        and terrain.terrain is not None
        and terrain.terrain.analysis_grid_m != analysis_grid_m
    ):
        raise ValueError(
            f"analysis_grid_m {analysis_grid_m} differs from the terrain fingerprint's "
            f"{terrain.terrain.analysis_grid_m}"
        )
    if permanent_shadow_fraction is not None and body != "moon":
        raise ValueError("permanent_shadow_fraction applies to Moon windows only (PRD 6.1)")

    valid: dict[str, float | None] = {}
    status_by_field: dict[str, EvidenceLabel] = {}
    if terrain is not None and terrain.terrain is not None:
        valid["terrain"] = terrain.terrain.valid_fraction
        status_by_field["terrain_relief"] = _variant_label(terrain.terrain.relief)
        status_by_field["terrain_texture"] = _variant_label(terrain.terrain.texture)
    else:
        valid["terrain"] = None
        status_by_field["terrain_relief"] = "Unavailable"
        status_by_field["terrain_texture"] = "Unavailable"
    if radar:
        for record in radar:
            if record.radar is None:
                raise ValueError("the radar slot holds radar fingerprints only")
            key = f"radar_cpr:{record.radar.product_id}"
            if key in valid:
                raise ValueError(f"radar product {record.radar.product_id} is listed twice")
            valid[key] = record.radar.valid_fraction
            status_by_field[key] = record.evidence_label
    else:
        valid["radar_cpr"] = None
        status_by_field["radar_cpr"] = "Unavailable"
    status_by_field["permanent_shadow_fraction"] = (
        "Unavailable" if permanent_shadow_fraction is None else "Derived"
    )

    earth = body == "earth"
    flags = EnvironmentFlags(
        search_region_id=search_region_id,
        scene_ids=list(scene_ids),
        acquisition_dates=list(acquisition_dates),
        snow_or_freeze=EARTH_FLAG_UNCHECKED if earth else None,
        moisture_state=EARTH_FLAG_UNCHECKED if earth else None,
        surface_change_since_acquisition=EARTH_FLAG_UNCHECKED if earth else None,
        permanent_shadow_fraction=permanent_shadow_fraction,
        surface_model_note=COPERNICUS_SURFACE_MODEL_NOTE if earth else None,
    )
    window = WindowRecord(
        window_id=window_id,
        body=body,
        polygon=polygon,
        area_m2=area_m2,
        analysis_grid_m=analysis_grid_m,
        product_ids=list(product_ids),
        valid_fraction_by_observable=valid,
        observed_interpolated_quality=observed_interpolated_quality,
        environment_flags=flags,
        evidence_status_by_field=status_by_field,
    )
    return WindowEntry(
        window=window,
        terrain=terrain,
        radar=list(radar),
        status=status,
        reasons=list(reasons),
        grid_index=None if grid_index is None else list(grid_index),
        card_id=card_id,
    )
