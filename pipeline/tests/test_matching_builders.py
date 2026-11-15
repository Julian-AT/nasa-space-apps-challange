"""Synthetic record builders shared by the matching tests.

Every value here is a labelled synthetic test input, never an observation. Each builder returns
a validated t0b record; keyword overrides replace top-level fields.
"""

from __future__ import annotations

import math
from typing import Any

from terraluna_pipeline.schema import (
    FingerprintRecord,
    ProductRecord,
    TerrainVariant,
    WindowEntry,
)

HEX64 = "0" * 64
RUN_ID = "sha256:" + "1" * 64
LAGS = [270.0, 360.0, 540.0, 720.0, 1080.0]

RING_M = [[-2500.0, -2500.0], [2500.0, -2500.0], [2500.0, 2500.0], [-2500.0, 2500.0]]
RING_LONLAT = [[10.25, -89.5], [12.5, -89.5], [12.5, -89.25], [10.25, -89.25]]


def lag_key(lag: float) -> str:
    """Synthetic keys written as whole metres, as the schema examples write them."""
    return str(int(lag)) if float(lag).is_integer() else repr(lag)


def variant(
    nus: list[float | None] | None = None,
    *,
    lags: list[float] | None = None,
    detrend: str = "none",
    hurst: float | None = 0.625,
) -> dict[str, Any]:
    used = LAGS if lags is None else lags
    values = [1.0] * len(used) if nus is None else nus
    return {
        "detrend": detrend,
        "supported_lags_m": [lag for lag, nu in zip(used, values, strict=True) if nu is not None],
        "unsupported_lags": [],
        "nu_m": {lag_key(lag): nu for lag, nu in zip(used, values, strict=True)},
        "nu_by_direction_m": {},
        "hurst": hurst,
        "hurst_reason": None if hurst is not None else "insufficient_lags",
        "tilt": None,
    }


def terrain_variant(nus: list[float | None], **kwargs: Any) -> TerrainVariant:
    return TerrainVariant.model_validate(variant(nus, **kwargs))


def distribution(values: list[float]) -> dict[str, Any] | None:
    ordered = sorted(values)
    if not ordered:
        return {
            "n": 0,
            "values": [],
            "min": None,
            "median": None,
            "p05": None,
            "p95": None,
            "max": None,
        }
    mid = ordered[len(ordered) // 2]
    return {
        "n": len(ordered),
        "values": ordered,
        "min": ordered[0],
        "median": mid,
        "p05": ordered[0],
        "p95": ordered[-1],
        "max": ordered[-1],
    }


def radar_fp(
    window_id: str,
    product_id: str,
    *,
    cpr: list[float] | None = None,
    support_m: float = 250.0,
    incidence: tuple[float, float] | None = (40.0, 42.0),
    valid_fraction: float = 0.9,
) -> dict[str, Any]:
    values = [0.25, 0.5, 0.75] if cpr is None else cpr
    return {
        "window_id": window_id,
        "body": window_id.split("-")[0],
        "kind": "radar",
        "product_ids": [product_id],
        "evidence_label": "Derived",
        "input_hash": HEX64,
        "run_id": RUN_ID,
        "terrain": None,
        "radar": {
            "product_id": product_id,
            "band": "L",
            "frequency_or_wavelength": "1.2575 GHz",
            "convention": "synthetic",
            "support_m": support_m,
            "aggregation": "ratio_of_mean_powers",
            "blocks_independent": False,
            "n_blocks": max(len(values), 1),
            "n_valid": len(values),
            "valid_fraction": valid_fraction,
            "cpr_ratio_of_mean_powers": distribution(values),
            "cpr_mean_of_pixelwise_ratios": None,
            "sc": None,
            "oc": None,
            "total_power": None,
            "incidence_deg": None
            if incidence is None
            else {"min": incidence[0], "max": incidence[1]},
            "handedness_ratio": None,
            "reason": None,
        },
    }


def entry(
    window_id: str = "moon-sps-x2-y2",
    *,
    relief: dict[str, Any] | None = None,
    texture: dict[str, Any] | None = None,
    valid_fraction: float = 0.875,
    analysis_grid_m: float = 90.0,
    terrain: bool = True,
    radar: list[dict[str, Any]] | None = None,
) -> WindowEntry:
    body = window_id.split("-")[0]
    terrain_fp: dict[str, Any] | None = None
    if terrain:
        terrain_fp = {
            "window_id": window_id,
            "body": body,
            "kind": "terrain",
            "product_ids": ["synthetic-dem"],
            "evidence_label": "Derived",
            "input_hash": HEX64,
            "run_id": RUN_ID,
            "terrain": {
                "analysis_grid_m": analysis_grid_m,
                "coarsening_factor": None,
                "resampler": "aeqd_sphere",
                "valid_fraction": valid_fraction,
                "relief": variant() if relief is None else relief,
                "texture": variant(detrend="plane") if texture is None else texture,
            },
            "radar": None,
        }
    return WindowEntry.model_validate(
        {
            "window": {
                "window_id": window_id,
                "body": body,
                "polygon": {
                    "frame": "aeqd_sphere",
                    "crs": "+proj=aeqd +R=1737400 +units=m (synthetic)",
                    "epsg": None,
                    "utm_zone": None,
                    "ring_m": [*RING_M, RING_M[0]],
                    "ring_lonlat": [*RING_LONLAT, RING_LONLAT[0]],
                },
                "area_m2": 25_000_000.0,
                "analysis_grid_m": analysis_grid_m,
                "product_ids": ["synthetic-dem"],
                "valid_fraction_by_observable": {"terrain": valid_fraction},
                "observed_interpolated_quality": {
                    "observed_fraction": None,
                    "interpolated_fraction": None,
                    "source": None,
                    "reason": "synthetic",
                },
                "environment_flags": {
                    "search_region_id": None,
                    "scene_ids": [],
                    "acquisition_dates": [],
                    "snow_or_freeze": None,
                    "moisture_state": None,
                    "surface_change_since_acquisition": None,
                    "permanent_shadow_fraction": None,
                    "surface_model_note": None,
                },
                "evidence_status_by_field": {},
            },
            "terrain": terrain_fp,
            "radar": [] if radar is None else radar,
            "status": "eligible",
            "reasons": [],
            "grid_index": None,
            "card_id": None,
        }
    )


def product(product_id: str = "synthetic-dem", **overrides: Any) -> ProductRecord:
    """A synthetic ProductRecord; radar facts are set only through overrides."""
    record: dict[str, Any] = {
        "product_id": product_id,
        "provider": "synthetic",
        "version": "v1",
        "source_url": None,
        "local_path": None,
        "checksum": None,
        "body": "moon",
        "acquisition_time": None,
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "m",
        "calibration_type": None,
        "native_grid": None,
        "effective_resolution": None,
        "crs_and_frame": "synthetic",
        "incidence_source": None,
        "qa_definition": None,
        "license_or_attribution": "synthetic test input",
        "verification_status": "NOT_CHECKED",
    }
    record.update(overrides)
    return ProductRecord.model_validate(record)


def radar_product(product_id: str, frequency: str, body: str = "moon") -> ProductRecord:
    """A synthetic radar ProductRecord with every PRD 6.5 fact documented."""
    return product(
        product_id,
        body=body,
        frequency_or_wavelength=frequency,
        transmit_receive_convention="synthetic: transmit circular, receive linear",
        units="linear power",
        calibration_type="synthetic sigma0",
        incidence_source="synthetic incidence layer, in degrees",
        qa_definition="synthetic QA layer",
    )


def fingerprint_of(window: WindowEntry) -> FingerprintRecord:
    assert window.terrain is not None
    return window.terrain


E = math.e
