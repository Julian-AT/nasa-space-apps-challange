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
    ``radar_fingerprint(circular, *, window_id, body, product_id, band, frequency_or_wavelength,
    convention, common_support_m, pixel_size_m, incidence_deg, products, config_path,
    input_hash) -> FingerprintRecord``
        The kind ``radar`` record of one product over a window; see its docstring.
    ``build_window_record(*, window_id, body, polygon, ..., status, reasons, ...) -> WindowEntry``
        One row of ``windows/{body}.json``; see its docstring.
    ``FingerprintReason``
        The reason codes of this module (CONTRACTS a.5): ``convention_unknown``,
        ``missing_channel`` and ``no_valid_blocks``. Other modules import them.

Radar mapping (CONTRACTS a.4; PRD 6.2, 7)
    ``circular`` is the T2 ``derive_circular`` record (linear SC, OC, CPR and their per-pixel
    states). The block size is ``common_support_m`` (config ``radar.common_support_m``)
    converted to pixels with the product's stated pixel size: ``round(support / pixel)`` per
    axis, at least 1. ``support_m`` records the block side that results, ``block_px * pixel``;
    it is the configured value when that is equal to PRD 7's arithmetic tolerance (rtol 1e-10,
    atol 1e-12). Rectangular blocks are refused. A missing pixel size raises; none is assumed.
    The per-block statistics come from T2 ``aggregate_blocks``, called unchanged; this module
    only collects each statistic's ``VALID`` block values into a Distribution: sorted values,
    ``n``, min, max, median and the 5th and 95th percentiles (numpy's default linear
    interpolation). ``cpr_ratio_of_mean_powers`` (primary) and ``cpr_mean_of_pixelwise_ratios``
    stay two named distributions; ``sc`` and ``oc`` are the block mean powers kept as context.
    T2 aggregates no block total power and no block handedness ratio, so ``total_power`` and
    ``handedness_ratio`` are null rather than computed here. ``n_blocks`` counts every block,
    ``n_valid`` the blocks with a ``VALID`` ratio of mean powers and ``valid_fraction`` their
    quotient. A block with no CPR (OC zero or negative, masked as noise, missing) contributes
    nothing: no zero, epsilon or large value is entered. With no valid block the record is
    ``Unavailable`` with ``no_valid_blocks`` and every distribution null. Without a channel
    (``circular`` None) it is ``Unavailable`` with ``missing_channel``, and with an undeclared
    convention with ``convention_unknown``; then no block is measured, so ``n_blocks`` and
    ``n_valid`` are 0 and ``valid_fraction`` is 0.0 beside the reason. Block statistics of an
    Observed product are ``Derived``. Mars has no radar (PRD 10) and is refused.

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
from enum import StrEnum
from pathlib import PurePath
from typing import Final

import numpy as np
import numpy.typing as npt

from terraluna_pipeline import provenance
from terraluna_pipeline.polarimetry import (
    CircularPowers,
    CovarianceCircularPowers,
    ObsState,
    StatedArray,
    aggregate_blocks,
)
from terraluna_pipeline.schema import (
    Body,
    Distribution,
    EnvironmentFlags,
    EvidenceLabel,
    FingerprintRecord,
    IncidenceRange,
    ObservedInterpolatedQuality,
    ProductRecord,
    RadarFingerprint,
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
    "FingerprintReason",
    "Product",
    "build_window_record",
    "input_hash",
    "lag_key",
    "radar_fingerprint",
    "terrain_record",
]


class FingerprintReason(StrEnum):
    """Why a radar fingerprint is Unavailable (CONTRACTS a.5). Consumers import these."""

    CONVENTION_UNKNOWN = "convention_unknown"
    """The product's polarimetric convention is not declared, so SC and OC are not defined."""

    MISSING_CHANNEL = "missing_channel"
    """A source channel the circular powers need is missing from the product."""

    NO_VALID_BLOCKS = "no_valid_blocks"
    """No block of the window has a valid ratio of mean powers."""


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


# --- radar --------------------------------------------------------------------------------------

_SUPPORT_RTOL: Final = 1e-10
_SUPPORT_ATOL: Final = 1e-12


def _block_pixels(
    support_m: float, pixel_size_m: float | tuple[float, float] | None
) -> tuple[tuple[int, int], float]:
    if pixel_size_m is None:
        raise ValueError("the product states no pixel size; none is assumed")
    support = float(support_m)
    if not (math.isfinite(support) and support > 0.0):
        raise ValueError(f"common_support_m must be finite and positive, got {support_m!r}")
    sizes = (
        (float(pixel_size_m), float(pixel_size_m))
        if isinstance(pixel_size_m, int | float)
        else (float(pixel_size_m[0]), float(pixel_size_m[1]))
    )
    for size in sizes:
        if not (math.isfinite(size) and size > 0.0):
            raise ValueError(f"pixel size must be finite and positive, got {pixel_size_m!r}")
    block = (max(1, round(support / sizes[0])), max(1, round(support / sizes[1])))
    sides = (block[0] * sizes[0], block[1] * sizes[1])
    if not math.isclose(sides[0], sides[1], rel_tol=_SUPPORT_RTOL, abs_tol=_SUPPORT_ATOL):
        raise ValueError(
            f"blocks of {block} pixels of {sizes} m are {sides} m, not square; "
            "a rectangular support is not a common support"
        )
    if math.isclose(sides[0], support, rel_tol=_SUPPORT_RTOL, abs_tol=_SUPPORT_ATOL):
        return block, support
    return block, sides[0]


def _distribution(statistic: StatedArray) -> Distribution | None:
    """The VALID block values of one T2 statistic, or None when there is none."""
    used = np.equal(statistic.state, ObsState.VALID)
    values = np.sort(statistic.value[used])
    if values.size == 0:
        return None
    return Distribution(
        n=int(values.size),
        values=[float(v) for v in values.tolist()],
        min=float(values[0]),
        median=float(np.median(values)),
        p05=float(np.percentile(values, 5.0)),
        p95=float(np.percentile(values, 95.0)),
        max=float(values[-1]),
    )


def radar_fingerprint(
    circular: CircularPowers | CovarianceCircularPowers | None,
    *,
    window_id: str,
    body: Body,
    product_id: str,
    band: str,
    frequency_or_wavelength: str,
    convention: str | None,
    common_support_m: float,
    pixel_size_m: float | tuple[float, float] | None,
    incidence_deg: IncidenceRange | None,
    products: Sequence[Product],
    config_path: str | PurePath,
    input_hash: str,
) -> FingerprintRecord:
    """The kind ``radar`` FingerprintRecord of one product over a window (module docstring).

    ``circular`` is the T2 ``derive_circular`` output on the window's pixel grid, or None when
    the product lacks a channel it needs. ``convention`` is the product's declared convention,
    None when undeclared; when both are given they must agree. The pixel grid must divide into
    whole blocks (T2 ``aggregate_blocks`` raises otherwise). ``products`` must include
    ``product_id``. Raises ValueError for a Mars window or a missing pixel size.
    """
    if body == "mars":
        raise ValueError("Mars windows have no radar fingerprint (PRD 10)")
    block, support = _block_pixels(common_support_m, pixel_size_m)
    product_ids = [_product_id(product) for product in products]
    if product_id not in product_ids:
        raise ValueError(f"radar product {product_id} is not among the listed products")
    if circular is not None and convention is not None and circular.convention != convention:
        raise ValueError(
            f"declared convention {convention!r} differs from the record's {circular.convention!r}"
        )

    reason: FingerprintReason | None = None
    n_blocks = n_valid = 0
    cpr = pixelwise = sc = oc = None
    if circular is None:
        reason = FingerprintReason.MISSING_CHANNEL
    elif convention is None:
        reason = FingerprintReason.CONVENTION_UNKNOWN
    else:
        blocks = aggregate_blocks(circular, block_size=block)
        ratio = blocks.ratio_of_mean_powers
        n_blocks = int(ratio.state.size)
        n_valid = int(np.count_nonzero(np.equal(ratio.state, ObsState.VALID)))
        if n_valid == 0:
            reason = FingerprintReason.NO_VALID_BLOCKS
        else:
            cpr = _distribution(ratio)
            pixelwise = _distribution(blocks.mean_of_pixelwise_ratios)
            sc = _distribution(blocks.mean_sc)
            oc = _distribution(blocks.mean_oc)

    radar = RadarFingerprint(
        product_id=product_id,
        band=band,
        frequency_or_wavelength=frequency_or_wavelength,
        convention=convention,
        support_m=support,
        aggregation="ratio_of_mean_powers",
        blocks_independent=False,
        n_blocks=n_blocks,
        n_valid=n_valid,
        valid_fraction=n_valid / n_blocks if n_blocks else 0.0,
        cpr_ratio_of_mean_powers=cpr,
        cpr_mean_of_pixelwise_ratios=pixelwise,
        sc=sc,
        oc=oc,
        total_power=None,
        incidence_deg=incidence_deg,
        handedness_ratio=None,
        reason=None if reason is None else reason.value,
    )
    return FingerprintRecord(
        window_id=window_id,
        body=body,
        kind="radar",
        product_ids=product_ids,
        evidence_label="Unavailable" if reason is not None else "Derived",
        input_hash=input_hash,
        run_id=provenance.run_id(config_path, products),
        terrain=None,
        radar=radar,
    )


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
