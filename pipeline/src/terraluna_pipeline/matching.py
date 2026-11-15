"""Pair qualification and per-family distances between a target and an Earth window (PRD 6.5).

Every family is computed on its own and never merged (D07): terrain relief, terrain texture,
radar CPR and the model embedding each get their own :class:`DistanceEntry`. Nothing here sums,
averages or normalizes across families, nothing compares a radar value with a terrain value,
and Hurst H is never an input to a distance (PRD 4 Mode B prohibition). A missing value is
null with a machine-readable reason, never 0; 0.0 is only the real zero of identical
fingerprints.

Public API

- :func:`qualify_pair` decides per observable (``terrain_relief``, ``terrain_texture``,
  ``radar_cpr``) whether a pair is comparable and returns a :class:`QualifyResult` with the
  ordered reasons and every checked fact. :class:`WindowSummary` is the small frozen view of a
  :class:`~terraluna_pipeline.schema.WindowEntry` it reads; :class:`QualifySettings` holds the
  config values (``terrain.min_valid_fraction``, ``radar.incidence_bins_deg``,
  ``radar.snr_rule``).
- :func:`terrain_distance` (RMS of log nu over the shared supported lags),
  :func:`radar_distance` and :func:`wasserstein_1` (CPR Wasserstein-1, quantile form),
  :func:`background_percentile` (``linear_within_bin_v1``, CONTRACTS a.4) and
  :func:`incidence_bin` (incidence enters a bin only through ``polarimetry.angle_to_degrees``).
- :func:`compare_windows` builds the t0b :class:`~terraluna_pipeline.schema.ComparisonRecord`;
  :func:`unavailable_uncertainty` builds the all-unavailable uncertainty components.
- :func:`pareto_shortlist`, :func:`pareto_layers` and :func:`trim_comparisons` (T4L-04): the
  encoder family ranks only with G5 PASS, and no family that sorts first is chosen here.
- :func:`tie_groups`, :func:`reference_spread`, :func:`background_sample`,
  :func:`percentile_fields` and the ``with_*`` fillers (T4L-05).
- ``python -m terraluna_pipeline.matching parity [--check]`` writes or checks
  fixtures/parity/matching_synthetic.json.

Reason codes (:class:`MatchReason`, CONTRACTS a.5)
    The declared order is the CONTRACTS a.5 list followed by the codes this module added, which
    the orchestrator should append to a.5: ``background_empty``, ``uncertainty_not_run``,
    ``mode_b_context_only`` (a Mode B radar signature is band-labelled context only),
    ``noise_rule_unset`` (``radar.snr_rule`` is null), ``coverage_threshold_unset`` (no radar
    coverage threshold exists in config), ``analysis_grid_mismatch``, ``resampler_mismatch``,
    ``terrain_fingerprint_missing``, ``radar_fingerprint_missing``, ``valid_fraction_unknown``,
    ``nu_missing`` (a null nu at a shared supported lag), ``no_valid_blocks`` (the same string
    fingerprints.py uses; matching does not import fingerprints.py),
    ``reference_spread_not_run`` and ``background_not_run`` (the T4L-05 fields before they are
    filled), ``no_ranking_family`` (no family is available for every candidate),
    ``distance_unavailable`` (no distance to tie or rank against a background) and
    ``background_missing`` (no histogram for the family). ``below_min_valid_fraction`` is the
    T3 code, read from ``terrain.WindowReason``.

Reasons inside one :class:`QualifyResult` are in check order: the mode and body gates first
(``shipping_mode_unset``, ``radar_unavailable_for_body``, ``radar_not_completed``), then the
PRD 6.5 facts in the order band, convention, geometry, calibration, support, coverage, noise
and QA (radar) or grid, lags, coverage, resampler and QA (terrain). A DistanceEntry carries the
first reason.

Lag keys
    T3 publishes no key-formatting function, so nu is looked up by the numeric value of each
    ``nu_m`` key (``float(key) == lag``) rather than by formatting a lag again. A variant with two
    keys for the same lag is refused.

Band parsing
    ``frequency_or_wavelength`` is free text. Every ``<number> <unit>`` pair in it (units GHz,
    MHz, cm, m; angle brackets allowed) is mapped to an IEEE 521 band letter; the band is known
    only when every pair maps to the same letter. The Mini-RF labels state
    ``2379.9994969 <GHz>`` beside "S-band (2.38 GHz)"; the two disagree, so the band is
    ``band_unknown`` until the record is corrected, never guessed.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
import math
import os
import re
import struct
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, get_args

from terraluna_pipeline import provenance, terrain
from terraluna_pipeline.config import DEFAULT_RUN_CONFIG, load_run_config
from terraluna_pipeline.polarimetry import ObsState, angle_to_degrees
from terraluna_pipeline.schema import (
    Body,
    ComparisonRecord,
    DistanceEntry,
    DistancesByFamily,
    EvidenceLabel,
    ExcludedObservable,
    Family,
    FingerprintRecord,
    GateStatus,
    ModelEmbeddingDistanceEntry,
    ProductRecord,
    RadarFingerprint,
    ReferenceSpread,
    ShippingMode,
    TerrainVariant,
    UncertaintyComponent,
    UncertaintyComponents,
    WindowEntry,
)


class MatchReason(StrEnum):
    """Machine-readable reasons of this module, in a stable declared order (CONTRACTS a.5)."""

    BAND_MISMATCH = "band_mismatch"
    BAND_UNKNOWN = "band_unknown"
    CONVENTION_UNKNOWN = "convention_unknown"
    GEOMETRY_INCOMPATIBLE = "geometry_incompatible"
    INCIDENCE_UNIT_UNKNOWN = "incidence_unit_unknown"
    INCIDENCE_BINS_UNSET = "incidence_bins_unset"
    CALIBRATION_UNRESOLVED = "calibration_unresolved"
    SUPPORT_MISMATCH = "support_mismatch"
    COVERAGE_BELOW_THRESHOLD = "coverage_below_threshold"
    QA_MISSING = "qa_missing"
    CROSS_BAND_CALIBRATION_UNRESOLVED = "cross_band_calibration_unresolved"
    RADAR_NOT_COMPLETED = "radar_not_completed"
    SHIPPING_MODE_UNSET = "shipping_mode_unset"
    RADAR_UNAVAILABLE_FOR_BODY = "radar_unavailable_for_body"
    NO_SHARED_LAGS = "no_shared_lags"
    NU_ZERO = "nu_zero"
    REFERENCE_SET_TOO_SMALL = "reference_set_too_small"
    BACKGROUND_SAMPLE_SIZE_UNSET = "background_sample_size_unset"
    SPREAD_UNAVAILABLE = "spread_unavailable"
    G5_NOT_PASS = "g5_not_pass"
    ENCODER_NOT_EVALUATED = "encoder_not_evaluated"
    # Added beyond CONTRACTS a.5 (listed in the module docstring for the orchestrator).
    BACKGROUND_EMPTY = "background_empty"
    UNCERTAINTY_NOT_RUN = "uncertainty_not_run"
    MODE_B_CONTEXT_ONLY = "mode_b_context_only"
    NOISE_RULE_UNSET = "noise_rule_unset"
    COVERAGE_THRESHOLD_UNSET = "coverage_threshold_unset"
    ANALYSIS_GRID_MISMATCH = "analysis_grid_mismatch"
    RESAMPLER_MISMATCH = "resampler_mismatch"
    TERRAIN_FINGERPRINT_MISSING = "terrain_fingerprint_missing"
    RADAR_FINGERPRINT_MISSING = "radar_fingerprint_missing"
    VALID_FRACTION_UNKNOWN = "valid_fraction_unknown"
    NU_MISSING = "nu_missing"
    NO_VALID_BLOCKS = "no_valid_blocks"
    REFERENCE_SPREAD_NOT_RUN = "reference_spread_not_run"
    BACKGROUND_NOT_RUN = "background_not_run"
    NO_RANKING_FAMILY = "no_ranking_family"
    DISTANCE_UNAVAILABLE = "distance_unavailable"
    BACKGROUND_MISSING = "background_missing"


BELOW_MIN_VALID_FRACTION: Final[str] = get_args(terrain.WindowReason)[0]
"""The T3 window-exclusion code, read from ``terrain.WindowReason`` and never retyped."""

Observable = Literal["terrain_relief", "terrain_texture", "radar_cpr"]
"""The observables :func:`qualify_pair` decides; model_embedding is not decided here."""

OBSERVABLES: Final[tuple[Observable, ...]] = ("terrain_relief", "terrain_texture", "radar_cpr")
FAMILIES: Final[tuple[Family, ...]] = (
    "terrain_relief",
    "terrain_texture",
    "radar_cpr",
    "model_embedding",
)

TERRAIN_UNIT: Final = "RMS of log nu"
RADAR_UNIT: Final = "CPR Wasserstein-1"
MODEL_UNIT: Final = "cosine distance"
UNITS: Final[Mapping[Family, str]] = MappingProxyType(
    {
        "terrain_relief": TERRAIN_UNIT,
        "terrain_texture": TERRAIN_UNIT,
        "radar_cpr": RADAR_UNIT,
        "model_embedding": MODEL_UNIT,
    }
)
DECLARED_RESAMPLER: Final = "aeqd_sphere"

_VARIANT_OF: Final[Mapping[str, Literal["relief", "texture"]]] = MappingProxyType(
    {"terrain_relief": "relief", "terrain_texture": "texture"}
)


# --- Small outcome record ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """A number or the reason it is missing: exactly one of ``value`` and ``reason`` is null."""

    value: float | None
    reason: str | None
    lags_m: tuple[float, ...] = ()
    """The shared lags a terrain distance used (empty for other outcomes)."""

    def __post_init__(self) -> None:
        if (self.value is None) == (self.reason is None):
            raise ValueError("exactly one of value and reason must be null")


# --- Bands, angles and product facts ----------------------------------------------------------

_SPEED_OF_LIGHT_M_S: Final = 299_792_458.0
_BANDS_GHZ: Final[tuple[tuple[str, float, float], ...]] = (
    # IEEE Std 521 letter bands, lower bound inclusive.
    ("P", 0.216, 0.45),
    ("L", 1.0, 2.0),
    ("S", 2.0, 4.0),
    ("C", 4.0, 8.0),
    ("X", 8.0, 12.0),
    ("Ku", 12.0, 18.0),
    ("K", 18.0, 27.0),
    ("Ka", 27.0, 40.0),
)
_QUANTITY: Final = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*<?\s*(GHz|MHz|cm|m)\b>?",
)
_UNIT_WORD: Final = re.compile(r"\b(radians|degrees)\b")


def _band_of_ghz(ghz: float) -> str | None:
    for letter, low, high in _BANDS_GHZ:
        if low <= ghz < high:
            return letter
    return None


def parse_band(frequency_or_wavelength: str | None) -> str | None:
    """The IEEE 521 band letter stated by a ProductRecord, or None when it is not established.

    Every ``<number> <unit>`` pair must give the same band; none, a pair outside every band, or
    two disagreeing pairs give None (``band_unknown``). A free-text band name is never read.
    """
    if frequency_or_wavelength is None:
        return None
    bands: set[str | None] = set()
    for number, unit in _QUANTITY.findall(frequency_or_wavelength):
        value = float(number)
        if unit == "GHz":
            ghz = value
        elif unit == "MHz":
            ghz = value / 1000.0
        else:
            metres = value / 100.0 if unit == "cm" else value
            if metres <= 0.0:
                return None
            ghz = _SPEED_OF_LIGHT_M_S / metres / 1e9
        bands.add(_band_of_ghz(ghz))
    if len(bands) != 1:
        return None
    (band,) = bands
    return band


def declared_angle_unit(product: ProductRecord | None) -> Literal["radians", "degrees"] | None:
    """The incidence unit the ProductRecord declares in ``incidence_source``, or None.

    Exactly one of the words ``radians`` and ``degrees`` must appear; units are never inferred
    from magnitude (PRD 7).
    """
    if product is None or product.incidence_source is None:
        return None
    words = set(_UNIT_WORD.findall(product.incidence_source))
    if words == {"radians"}:
        return "radians"
    if words == {"degrees"}:
        return "degrees"
    return None


def incidence_bin(
    angle: float, *, unit: str | None, edges_deg: Sequence[float] | None
) -> tuple[int | None, str | None]:
    """The index of the incidence bin holding ``angle``, or None with its reason.

    The angle enters a bin only through ``polarimetry.angle_to_degrees`` with the declared
    unit: an undeclared or unknown unit gives ``incidence_unit_unknown`` and null edges give
    ``incidence_bins_unset``. Bin ``k`` is ``[edges[k], edges[k + 1])``; an angle outside every
    bin, or a nonfinite one, gives ``geometry_incompatible``. The edges are never chosen here
    (ANA-01).
    """
    declared: Literal["radians", "degrees"]
    if unit == "radians":
        declared = "radians"
    elif unit == "degrees":
        declared = "degrees"
    else:
        return None, MatchReason.INCIDENCE_UNIT_UNKNOWN
    if edges_deg is None:
        return None, MatchReason.INCIDENCE_BINS_UNSET
    edges = _ascending_edges(edges_deg)
    converted = angle_to_degrees([angle], unit=declared)
    if int(converted.state[0]) != ObsState.VALID:
        return None, MatchReason.GEOMETRY_INCOMPATIBLE
    degrees = float(converted.value[0])
    if degrees < edges[0] or degrees >= edges[-1]:
        return None, MatchReason.GEOMETRY_INCOMPATIBLE
    return bisect.bisect_right(edges, degrees) - 1, None


def _ascending_edges(edges: Sequence[float]) -> tuple[float, ...]:
    values = tuple(float(edge) for edge in edges)
    if len(values) < 2:
        raise ValueError("bin edges need at least two values")
    if not all(math.isfinite(edge) for edge in values):
        raise ValueError("bin edges must be finite")
    if any(b <= a for a, b in itertools.pairwise(values)):
        raise ValueError("bin edges must be strictly ascending")
    return values


def _stated(text: str | None) -> bool:
    return text is not None and text.strip().lower() not in ("", "unknown")


# --- Window summaries and settings ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowSummary:
    """The window-level facts :func:`qualify_pair` reads, built from a WindowEntry.

    Terrain facts come from the terrain FingerprintRecord (null when there is none); radar
    fingerprints are kept by product id. ``valid_fraction_by_observable`` is the WindowRecord's.
    """

    window_id: str
    body: Body
    analysis_grid_m: float | None
    resampler: str | None
    terrain_valid_fraction: float | None
    supported_lags_m: Mapping[str, tuple[float, ...]]
    valid_fraction_by_observable: Mapping[str, float | None]
    radar: Mapping[str, RadarFingerprint] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_entry(cls, entry: WindowEntry) -> WindowSummary:
        record = entry.terrain.terrain if entry.terrain is not None else None
        lags: dict[str, tuple[float, ...]] = {}
        if record is not None:
            lags = {
                "terrain_relief": tuple(record.relief.supported_lags_m),
                "terrain_texture": tuple(record.texture.supported_lags_m),
            }
        radar = {
            item.radar.product_id: item.radar for item in entry.radar if item.radar is not None
        }
        return cls(
            window_id=entry.window.window_id,
            body=entry.window.body,
            analysis_grid_m=None if record is None else record.analysis_grid_m,
            resampler=None if record is None else record.resampler,
            terrain_valid_fraction=None if record is None else record.valid_fraction,
            supported_lags_m=MappingProxyType(lags),
            valid_fraction_by_observable=MappingProxyType(
                dict(entry.window.valid_fraction_by_observable)
            ),
            radar=MappingProxyType(radar),
        )


@dataclass(frozen=True, slots=True)
class QualifySettings:
    """Config values :func:`qualify_pair` uses. A null value stays null and is never defaulted.

    ``radar_min_valid_fraction`` has no config key yet, so it is null unless a caller passes
    one (tests pass explicit, labelled synthetic values).
    """

    min_valid_fraction: float
    incidence_bins_deg: tuple[float, ...] | None
    snr_rule: str | None
    radar_min_valid_fraction: float | None = None

    @classmethod
    def from_config(cls, path: Path = DEFAULT_RUN_CONFIG) -> QualifySettings:
        config = load_run_config(path)
        bins = config.value("radar.incidence_bins_deg")
        snr = config.value("radar.snr_rule")
        if bins is not None and not isinstance(bins, list):
            raise ValueError(f"radar.incidence_bins_deg must be a list or null, got {bins!r}")
        return cls(
            min_valid_fraction=config.terrain.min_valid_fraction,
            incidence_bins_deg=None if bins is None else _ascending_edges(bins),
            snr_rule=None if snr is None else str(snr),
        )


FactValue = str | float | bool | tuple[float, ...] | tuple[int | None, ...] | None


@dataclass(frozen=True, slots=True)
class QualifyResult:
    """Whether one observable of a pair is comparable, why not, and every fact checked."""

    observable: Observable
    shipping_mode: ShippingMode | None
    comparable: bool
    reasons: tuple[str, ...]
    facts: Mapping[str, FactValue]

    def __post_init__(self) -> None:
        if self.comparable == bool(self.reasons):
            raise ValueError("a comparable result has no reasons; any other has some")
        if self.comparable and any(value is None for value in self.facts.values()):
            raise ValueError("a comparable result never has an empty fact")

    def to_json(self) -> dict[str, object]:
        return {
            "observable": self.observable,
            "shipping_mode": self.shipping_mode,
            "comparable": self.comparable,
            "reasons": list(self.reasons),
            "facts": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.facts.items()
            },
        }


def qualify_pair(
    target_product: ProductRecord | None,
    earth_product: ProductRecord | None,
    observable: Observable,
    shipping_mode: ShippingMode | None,
    target_window: WindowSummary,
    earth_window: WindowSummary,
    *,
    settings: QualifySettings | None = None,
    config_path: Path = DEFAULT_RUN_CONFIG,
) -> QualifyResult:
    """Decide whether ``observable`` is comparable between the two windows (PRD 6.5 step 1).

    Terrain and radar are decided independently. Mode C excludes radar with
    ``radar_not_completed``; Mode B never makes radar comparable (cross-band pairs add
    ``cross_band_calibration_unresolved``, and every Mode B radar result ends with
    ``mode_b_context_only``); Mars excludes radar with ``radar_unavailable_for_body``. A null
    shipping mode excludes every observable with ``shipping_mode_unset`` and is never defaulted.
    Settings come from ``config_path`` unless ``settings`` is given.
    """
    if observable not in OBSERVABLES:
        raise ValueError(f"qualify_pair decides {OBSERVABLES}, not {observable!r}")
    active = settings if settings is not None else QualifySettings.from_config(config_path)
    reasons: list[str] = []
    facts: dict[str, FactValue] = {}
    if shipping_mode is None:
        reasons.append(MatchReason.SHIPPING_MODE_UNSET)
    if observable == "radar_cpr":
        _radar_facts(
            target_product,
            earth_product,
            shipping_mode,
            target_window,
            earth_window,
            active,
            reasons,
            facts,
        )
    else:
        _terrain_facts(
            target_product, observable, target_window, earth_window, active, reasons, facts
        )
    return QualifyResult(
        observable=observable,
        shipping_mode=shipping_mode,
        comparable=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        facts=MappingProxyType(facts),
    )


def _terrain_facts(
    target_product: ProductRecord | None,
    observable: Observable,
    target: WindowSummary,
    earth: WindowSummary,
    settings: QualifySettings,
    reasons: list[str],
    facts: dict[str, FactValue],
) -> None:
    facts["target_body"] = target.body
    facts["earth_body"] = earth.body
    facts["target_analysis_grid_m"] = target.analysis_grid_m
    facts["earth_analysis_grid_m"] = earth.analysis_grid_m
    if target.analysis_grid_m is None or earth.analysis_grid_m is None:
        reasons.append(MatchReason.TERRAIN_FINGERPRINT_MISSING)
    elif target.analysis_grid_m != earth.analysis_grid_m:
        reasons.append(MatchReason.ANALYSIS_GRID_MISMATCH)
    shared = shared_lags(
        target.supported_lags_m.get(observable, ()), earth.supported_lags_m.get(observable, ())
    )
    facts["shared_lags_m"] = shared
    if not shared:
        reasons.append(MatchReason.NO_SHARED_LAGS)
    facts["min_valid_fraction"] = settings.min_valid_fraction
    for side, summary in (("target", target), ("earth", earth)):
        fraction = summary.terrain_valid_fraction
        facts[f"{side}_valid_fraction"] = fraction
        if fraction is None:
            reasons.append(MatchReason.VALID_FRACTION_UNKNOWN)
        elif fraction < settings.min_valid_fraction:
            reasons.append(BELOW_MIN_VALID_FRACTION)
    for side, summary in (("target", target), ("earth", earth)):
        facts[f"{side}_resampler"] = summary.resampler
        if summary.resampler is None:
            reasons.append(MatchReason.TERRAIN_FINGERPRINT_MISSING)
        elif summary.resampler != DECLARED_RESAMPLER:
            reasons.append(MatchReason.RESAMPLER_MISMATCH)
    if target.body == "mars":
        qa = None if target_product is None else target_product.qa_definition
        facts["target_qa"] = qa if _stated(qa) else None
        if not _stated(qa):
            reasons.append(MatchReason.QA_MISSING)


def _radar_facts(
    target_product: ProductRecord | None,
    earth_product: ProductRecord | None,
    shipping_mode: ShippingMode | None,
    target: WindowSummary,
    earth: WindowSummary,
    settings: QualifySettings,
    reasons: list[str],
    facts: dict[str, FactValue],
) -> None:
    if "mars" in (target.body, earth.body):
        reasons.append(MatchReason.RADAR_UNAVAILABLE_FOR_BODY)
    if shipping_mode == "C":
        reasons.append(MatchReason.RADAR_NOT_COMPLETED)
    products = (("target", target_product, target), ("earth", earth_product, earth))
    fingerprints: dict[str, RadarFingerprint | None] = {}
    for side, product, summary in products:
        facts[f"{side}_product_id"] = None if product is None else product.product_id
        fingerprints[side] = None if product is None else summary.radar.get(product.product_id)
    # Band.
    bands = {
        side: parse_band(None if product is None else product.frequency_or_wavelength)
        for side, product, _ in products
    }
    facts["target_band"] = bands["target"]
    facts["earth_band"] = bands["earth"]
    if bands["target"] is None or bands["earth"] is None:
        reasons.append(MatchReason.BAND_UNKNOWN)
    elif bands["target"] != bands["earth"]:
        reasons.append(
            MatchReason.CROSS_BAND_CALIBRATION_UNRESOLVED
            if shipping_mode == "B"
            else MatchReason.BAND_MISMATCH
        )
    # Convention.
    for side, product, _ in products:
        convention = None if product is None else product.transmit_receive_convention
        facts[f"{side}_convention"] = convention if _stated(convention) else None
        if not _stated(convention):
            reasons.append(MatchReason.CONVENTION_UNKNOWN)
    # Geometry: incidence bins in declared units (CONTRACTS i).
    bins: list[int | None] = []
    geometry_reasons: list[str] = []
    for side, product, _ in products:
        unit = declared_angle_unit(product)
        facts[f"{side}_incidence_unit"] = unit
        fingerprint = fingerprints[side]
        incidence = None if fingerprint is None else fingerprint.incidence_deg
        if unit is None:
            geometry_reasons.append(MatchReason.INCIDENCE_UNIT_UNKNOWN)
            continue
        if settings.incidence_bins_deg is None:
            geometry_reasons.append(MatchReason.INCIDENCE_BINS_UNSET)
            continue
        if incidence is None:
            geometry_reasons.append(MatchReason.RADAR_FINGERPRINT_MISSING)
            continue
        # The fingerprint stores degrees (schema incidence_deg), so it enters as degrees.
        for angle in (incidence.min, incidence.max):
            index, reason = incidence_bin(
                angle, unit="degrees", edges_deg=settings.incidence_bins_deg
            )
            bins.append(index)
            if reason is not None:
                geometry_reasons.append(reason)
    if not geometry_reasons and len(set(bins)) != 1:
        geometry_reasons.append(MatchReason.GEOMETRY_INCOMPATIBLE)
    facts["incidence_bins"] = tuple(bins) if not geometry_reasons else None
    reasons.extend(geometry_reasons)
    # Calibration.
    for side, product, _ in products:
        calibration = None if product is None else product.calibration_type
        facts[f"{side}_calibration"] = calibration if _stated(calibration) else None
        if not _stated(calibration):
            reasons.append(MatchReason.CALIBRATION_UNRESOLVED)
    # Support.
    target_fp, earth_fp = fingerprints["target"], fingerprints["earth"]
    facts["target_support_m"] = None if target_fp is None else target_fp.support_m
    facts["earth_support_m"] = None if earth_fp is None else earth_fp.support_m
    if target_fp is None or earth_fp is None:
        reasons.append(MatchReason.RADAR_FINGERPRINT_MISSING)
    elif target_fp.support_m != earth_fp.support_m or target_fp.aggregation != earth_fp.aggregation:
        reasons.append(MatchReason.SUPPORT_MISMATCH)
    # Coverage.
    facts["radar_min_valid_fraction"] = settings.radar_min_valid_fraction
    for side in ("target", "earth"):
        fingerprint = fingerprints[side]
        coverage = None if fingerprint is None else fingerprint.valid_fraction
        facts[f"{side}_coverage"] = coverage
        if settings.radar_min_valid_fraction is None:
            reasons.append(MatchReason.COVERAGE_THRESHOLD_UNSET)
        elif coverage is not None and coverage < settings.radar_min_valid_fraction:
            reasons.append(MatchReason.COVERAGE_BELOW_THRESHOLD)
    # Noise.
    facts["snr_rule"] = settings.snr_rule
    if settings.snr_rule is None:
        reasons.append(MatchReason.NOISE_RULE_UNSET)
    # QA.
    for side, product, _ in products:
        qa = None if product is None else product.qa_definition
        facts[f"{side}_qa"] = qa if _stated(qa) else None
        if not _stated(qa):
            reasons.append(MatchReason.QA_MISSING)
    if shipping_mode == "B":
        reasons.append(MatchReason.MODE_B_CONTEXT_ONLY)


# --- Distances ----------------------------------------------------------------------------------


def shared_lags(a: Sequence[float], b: Sequence[float]) -> tuple[float, ...]:
    """The supported lags present in both lists, ascending."""
    return tuple(sorted(set(map(float, a)) & set(map(float, b))))


def _nu_at(variant: TerrainVariant, lag: float) -> float | None:
    matches = [value for key, value in variant.nu_m.items() if float(key) == lag]
    if len(matches) > 1:
        raise ValueError(f"nu_m has {len(matches)} keys for lag {lag!r}")
    return matches[0] if matches else None


def terrain_distance(a: TerrainVariant, b: TerrainVariant) -> Outcome:
    """Root-mean-square difference of ln nu over the shared supported lags (PRD 6.5 step 3).

    ``sqrt(sum((ln nu_a(L) - ln nu_b(L))^2 for L in shared) / len(shared))``, summed left to right
    over the shared lags in ascending order. Unit "RMS of log nu". No shared lag gives
    ``no_shared_lags``; a zero or nonfinite nu at a used lag gives ``nu_zero`` (ln 0 is never
    evaluated and no epsilon is added); a null nu at a used lag gives ``nu_missing``. Hurst H
    is never read.
    """
    lags = shared_lags(a.supported_lags_m, b.supported_lags_m)
    if not lags:
        return Outcome(None, MatchReason.NO_SHARED_LAGS)
    total = 0.0
    for lag in lags:
        nu_a, nu_b = _nu_at(a, lag), _nu_at(b, lag)
        if nu_a is None or nu_b is None:
            return Outcome(None, MatchReason.NU_MISSING, lags)
        if not (math.isfinite(nu_a) and math.isfinite(nu_b)) or nu_a <= 0.0 or nu_b <= 0.0:
            return Outcome(None, MatchReason.NU_ZERO, lags)
        difference = math.log(nu_a) - math.log(nu_b)
        total += difference * difference
    return Outcome(math.sqrt(total / len(lags)), None, lags)


def wasserstein_1(u: Sequence[float], v: Sequence[float]) -> float:
    """One-dimensional Wasserstein-1 distance of two equal-weight samples (quantile form).

    ``W1 = integral over t in (0, 1) of |F_u^-1(t) - F_v^-1(t)|``, evaluated exactly on the
    merged breakpoints ``i / n`` and ``j / m`` with integer positions in units of ``1 / (n m)``.
    Both samples must be non-empty and finite; they are sorted first, so the order of the
    values does not matter.
    """
    a, b = sorted(map(float, u)), sorted(map(float, v))
    if not a or not b:
        raise ValueError("both samples need at least one value")
    if not all(math.isfinite(x) for x in (*a, *b)):
        raise ValueError("samples must be finite")
    n, m = len(a), len(b)
    i = j = position = 0
    total = 0.0
    while i < n and j < m:
        next_a, next_b = (i + 1) * m, (j + 1) * n
        step = min(next_a, next_b)
        total += (step - position) * abs(a[i] - b[j])
        position = step
        if next_a == step:
            i += 1
        if next_b == step:
            j += 1
    return total / (n * m)


def radar_distance(a: RadarFingerprint, b: RadarFingerprint) -> Outcome:
    """CPR Wasserstein-1 between the ratio-of-mean-powers block distributions, in CPR units.

    Computed only for the same ``support_m`` and aggregation (else ``support_mismatch``); a
    missing distribution or one with n == 0 gives ``no_valid_blocks``. Missing and invalid
    blocks are already absent from the Distribution. SC, OC and total power stay context.
    """
    if a.support_m != b.support_m or a.aggregation != b.aggregation:
        return Outcome(None, MatchReason.SUPPORT_MISMATCH)
    da, db = a.cpr_ratio_of_mean_powers, b.cpr_ratio_of_mean_powers
    if da is None or db is None or da.n == 0 or db.n == 0:
        return Outcome(None, MatchReason.NO_VALID_BLOCKS)
    return Outcome(wasserstein_1(da.values, db.values), None)


def background_percentile(d: float, bin_edges: Sequence[float], counts: Sequence[int]) -> Outcome:
    """``linear_within_bin_v1`` with direction ``smaller_is_closer`` (CONTRACTS a.4).

    ``p = 100 * (sum(counts[:k]) + counts[k] * (d - lo_k) / (hi_k - lo_k)) / n`` for the bin
    ``k = [lo_k, hi_k)`` holding d; 0 below the first edge and 100 at or above the last. An
    empty histogram gives ``background_empty``. Edges that are not strictly ascending, counts
    of the wrong length or negative, and a nonfinite d raise ValueError.
    """
    edges = _ascending_edges(bin_edges)
    if len(counts) != len(edges) - 1:
        raise ValueError(f"{len(edges)} edges need {len(edges) - 1} counts, got {len(counts)}")
    if any(isinstance(c, bool) or not isinstance(c, int) or c < 0 for c in counts):
        raise ValueError("counts must be non-negative integers")
    if not math.isfinite(d):
        raise ValueError(f"the distance must be finite, got {d!r}")
    n = sum(counts)
    if n == 0:
        return Outcome(None, MatchReason.BACKGROUND_EMPTY)
    if d < edges[0]:
        return Outcome(0.0, None)
    if d >= edges[-1]:
        return Outcome(100.0, None)
    k = bisect.bisect_right(edges, d) - 1
    low, high = edges[k], edges[k + 1]
    below = sum(counts[:k])
    return Outcome(100.0 * (below + counts[k] * (d - low) / (high - low)) / n, None)


# --- Records ------------------------------------------------------------------------------------


def _entry(
    family: Family,
    *,
    value: float | None,
    reason: str | None,
    status: Literal["available", "unavailable", "not_comparable"],
) -> DistanceEntry:
    if family == "model_embedding":
        label: EvidenceLabel = "Predicted" if value is not None else "Unavailable"
    else:
        label = "Derived" if value is not None else "Unavailable"
    fields: dict[str, object] = {
        "value": value,
        "unit": UNITS[family],
        "evidence_label": label,
        "status": status,
        "reason": None if reason is None else str(reason),
        "reference_spread": None,
        "reference_spread_reason": str(MatchReason.REFERENCE_SPREAD_NOT_RUN),
        "background_percentile": None,
        "background_percentile_reason": str(MatchReason.BACKGROUND_NOT_RUN),
        "tied": None,
        "tie_group": None,
        "tie_reason": str(MatchReason.SPREAD_UNAVAILABLE),
    }
    if family == "model_embedding":
        return ModelEmbeddingDistanceEntry.model_validate(fields)
    return DistanceEntry.model_validate(fields)


def distance_entry(family: Family, outcome: Outcome) -> DistanceEntry:
    """A DistanceEntry for an eligible family: available with its value, or unavailable."""
    status: Literal["available", "unavailable"] = (
        "available" if outcome.value is not None else "unavailable"
    )
    return _entry(family, value=outcome.value, reason=outcome.reason, status=status)


def not_comparable_entry(family: Family, reason: str) -> DistanceEntry:
    """A DistanceEntry for a family excluded by :func:`qualify_pair`: null value, its reason."""
    return _entry(family, value=None, reason=reason, status="not_comparable")


def _unavailable_component(reason: str) -> UncertaintyComponent:
    return UncertaintyComponent(
        status="unavailable",
        evidence_label="Unavailable",
        reason=reason,
        note=None,
        by_family=None,
        n_requested=None,
        n_used=None,
        scenarios=None,
        rank_frequency=None,
    )


def unavailable_uncertainty(
    reason: str = MatchReason.UNCERTAINTY_NOT_RUN,
) -> UncertaintyComponents:
    """The four PRD 6.6 components, each unavailable with ``reason`` (T4L-05 and t6 fill them)."""
    return UncertaintyComponents(
        heterogeneity=_unavailable_component(str(reason)),
        measurement_error=_unavailable_component(str(reason)),
        statistical_prediction_error=_unavailable_component(str(reason)),
        scenario_sensitivity=_unavailable_component(str(reason)),
    )


def _radar_fingerprint(entry: WindowEntry, product_id: FactValue) -> RadarFingerprint | None:
    for item in entry.radar:
        if item.radar is not None and item.radar.product_id == product_id:
            return item.radar
    return None


def _terrain_variant(record: FingerprintRecord | None, family: Family) -> TerrainVariant | None:
    if record is None or record.terrain is None:
        return None
    return getattr(record.terrain, _VARIANT_OF[family])  # type: ignore[no-any-return]


def compare_windows(
    fp_target: WindowEntry,
    fp_earth: WindowEntry,
    eligibility: Mapping[str, QualifyResult],
    *,
    shipping_mode: ShippingMode | None,
    source_ids: Sequence[ProductRecord | tuple[str, str | None]],
    claim_limits: Sequence[str],
    config_path: Path = DEFAULT_RUN_CONFIG,
    model_distance: float | None = None,
) -> ComparisonRecord:
    """The t0b ComparisonRecord of one target window and one Earth window (PRD 6.5, 8.12).

    ``eligibility`` maps each of ``terrain_relief``, ``terrain_texture`` and ``radar_cpr`` to its
    :func:`qualify_pair` result (a missing key raises). Every family key is always present: an
    ineligible family is ``not_comparable`` with its first reason and a null value, an eligible
    one carries its distance or ``unavailable`` with the reason. model_embedding is computed
    nowhere here: a supplied ``model_distance`` is stored as Predicted, otherwise the family is
    unavailable with ``encoder_not_evaluated``. Spreads, percentiles and ties carry their
    not-yet-run reasons, and the uncertainty components are all unavailable
    (``uncertainty_not_run``). ``run_id`` is ``provenance.run_id`` over ``source_ids``.
    """
    for observable in OBSERVABLES:
        result = eligibility.get(observable)
        if result is None:
            raise ValueError(f"eligibility has no result for {observable}")
        if result.observable != observable or result.shipping_mode != shipping_mode:
            raise ValueError(f"the {observable} result was decided for another observable or mode")
    entries: dict[Family, DistanceEntry] = {}
    comparable: list[str] = []
    excluded: list[ExcludedObservable] = []
    for observable in OBSERVABLES:
        result = eligibility[observable]
        if not result.comparable:
            entries[observable] = not_comparable_entry(observable, result.reasons[0])
            excluded.append(
                ExcludedObservable(observable=observable, reasons=list(map(str, result.reasons)))
            )
            continue
        comparable.append(observable)
        if observable == "radar_cpr":
            a = _radar_fingerprint(fp_target, result.facts.get("target_product_id"))
            b = _radar_fingerprint(fp_earth, result.facts.get("earth_product_id"))
            outcome = (
                Outcome(None, MatchReason.RADAR_FINGERPRINT_MISSING)
                if a is None or b is None
                else radar_distance(a, b)
            )
        else:
            ta = _terrain_variant(fp_target.terrain, observable)
            tb = _terrain_variant(fp_earth.terrain, observable)
            outcome = (
                Outcome(None, MatchReason.TERRAIN_FINGERPRINT_MISSING)
                if ta is None or tb is None
                else terrain_distance(ta, tb)
            )
        entries[observable] = distance_entry(observable, outcome)
    entries["model_embedding"] = (
        distance_entry("model_embedding", Outcome(model_distance, None))
        if model_distance is not None
        else _entry(
            "model_embedding",
            value=None,
            reason=MatchReason.ENCODER_NOT_EVALUATED,
            status="unavailable",
        )
    )
    ids = [item.product_id if isinstance(item, ProductRecord) else item[0] for item in source_ids]
    return ComparisonRecord(
        target_window_id=fp_target.window.window_id,
        earth_window_id=fp_earth.window.window_id,
        shipping_mode=shipping_mode,
        comparable_observables=comparable,
        excluded_observables_and_reasons=excluded,
        distances_by_family=DistancesByFamily.model_validate(entries),
        uncertainty_components=unavailable_uncertainty(),
        sort_rule=None,
        claim_limits=list(claim_limits),
        source_ids=ids,
        run_id=provenance.run_id(config_path, source_ids),
    )


# --- Pareto shortlist, sort rule and trim (T4L-04; PRD 6.5 step 4, 8.4, 11.10) ----------------

COMPARISON_LIMIT: Final = 500
"""Most ComparisonRecords kept per target (PRD 8.4)."""

_TERRAIN_RADAR: Final[tuple[Family, ...]] = ("terrain_relief", "terrain_texture", "radar_cpr")


@dataclass(frozen=True, slots=True)
class ShortlistResult:
    """Pareto layers over the ranking families (PRD 6.5 step 4); distances are never summed.

    ``layers[0]`` is the non-dominated set. ``order`` lists every ranked earth window in the
    declared order and ``sort_rule`` states it. ``not_comparable`` names, per window, the families
    that were unavailable for it and so ranked nobody. With no ranking family, every tuple is
    empty and ``reason`` is ``no_ranking_family``.
    """

    families: tuple[Family, ...]
    layers: tuple[tuple[str, ...], ...]
    order: tuple[str, ...]
    sort_rule: str | None
    reason: str | None
    not_comparable: Mapping[str, tuple[Family, ...]]

    @property
    def shortlist(self) -> tuple[str, ...]:
        return self.layers[0] if self.layers else ()


def _by_window(records: Sequence[ComparisonRecord]) -> dict[str, ComparisonRecord]:
    by_id: dict[str, ComparisonRecord] = {}
    targets = {record.target_window_id for record in records}
    if len(targets) > 1:
        raise ValueError(f"records belong to several targets: {sorted(targets)}")
    for record in records:
        if record.earth_window_id in by_id:
            raise ValueError(f"{record.earth_window_id} is compared twice")
        by_id[record.earth_window_id] = record
    return by_id


def _allowed_families(g5_status: GateStatus | None) -> tuple[Family, ...]:
    return FAMILIES if g5_status == "PASS" else _TERRAIN_RADAR


def _available(record: ComparisonRecord, family: Family) -> float | None:
    entry = record.distances_by_family[family]
    return entry.value if entry.status == "available" else None


def _dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    return all(x <= y for x, y in zip(a, b, strict=True)) and any(
        x < y for x, y in zip(a, b, strict=True)
    )


def pareto_layers(points: Mapping[str, Sequence[float]]) -> tuple[tuple[str, ...], ...]:
    """Non-dominated sorting: layer 0 is the set no point dominates, then the next, and so on.

    ``a`` dominates ``b`` when ``a`` is no worse (not larger) in every coordinate and strictly
    better (smaller) in at least one. Each layer is sorted by window id.
    """
    ids = sorted(points)
    dominated_by = {i: 0 for i in ids}
    dominates: dict[str, list[str]] = {i: [] for i in ids}
    for a, b in itertools.combinations(ids, 2):
        if _dominates(points[a], points[b]):
            dominates[a].append(b)
            dominated_by[b] += 1
        elif _dominates(points[b], points[a]):
            dominates[b].append(a)
            dominated_by[a] += 1
    layers: list[tuple[str, ...]] = []
    current = [i for i in ids if dominated_by[i] == 0]
    while current:
        layers.append(tuple(sorted(current)))
        following: list[str] = []
        for a in current:
            for b in dominates[a]:
                dominated_by[b] -= 1
                if dominated_by[b] == 0:
                    following.append(b)
        current = following
    return tuple(layers)


def pareto_shortlist(
    records: Sequence[ComparisonRecord],
    g5_status: GateStatus | None,
    *,
    first_family: Family | None = None,
) -> ShortlistResult:
    """The Pareto layers of the earth windows over the ranking families (PRD 6.5 step 4).

    Ranking families are those available (status available, value set) for every record;
    model_embedding is one only when ``g5_status`` is PASS (PRD 11.10). Dominance: no worse in
    every ranking family and strictly better in at least one. A family unavailable for some
    record ranks nobody, and those records are listed in ``not_comparable`` rather than treated
    as a poor or perfect match. The default order is the Pareto layers, then window_id
    ascending (ANA-03: no single order). ``first_family``, which a caller names and this module
    never chooses, orders by that family ascending, then window_id; it is refused when it is
    model_embedding without G5 PASS or is unavailable for any record. Input order never
    changes the result.
    """
    by_id = _by_window(records)
    allowed = _allowed_families(g5_status)
    if first_family is not None:
        if first_family not in allowed:
            raise ValueError(f"{first_family} cannot rank while G5 is {g5_status}")
        missing = sorted(i for i, r in by_id.items() if _available(r, first_family) is None)
        if missing:
            raise ValueError(f"{first_family} is not available for {', '.join(missing)}")
    not_comparable = {
        window: tuple(f for f in allowed if _available(record, f) is None)
        for window, record in sorted(by_id.items())
    }
    families = tuple(
        f for f in allowed if by_id and all(_available(r, f) is not None for r in by_id.values())
    )
    blocked = MappingProxyType({w: fs for w, fs in not_comparable.items() if fs})
    if not families:
        return ShortlistResult((), (), (), None, MatchReason.NO_RANKING_FAMILY, blocked)
    points = {w: [_value(r, f) for f in families] for w, r in by_id.items()}
    layers = pareto_layers(points)
    if first_family is not None:
        order = tuple(sorted(by_id, key=lambda w: (_value(by_id[w], first_family), w)))
        rule = f"{first_family} ascending; then window_id"
    else:
        order = tuple(w for layer in layers for w in layer)
        rule = f"pareto layers over {', '.join(families)}; then window_id"
    return ShortlistResult(families, layers, order, rule, None, blocked)


def _value(record: ComparisonRecord, family: Family) -> float:
    value = _available(record, family)
    if value is None:
        raise ValueError(f"{family} is not available for {record.earth_window_id}")
    return value


@dataclass(frozen=True, slots=True)
class TrimReport:
    """What :func:`trim_comparisons` kept and dropped, in the declared order."""

    n_input: int
    n_kept: int
    n_dropped: int
    sort_rule: str
    kept_window_ids: tuple[str, ...]
    dropped_window_ids: tuple[str, ...]


def trim_comparisons(
    records: Sequence[ComparisonRecord],
    shortlist: Sequence[str],
    g5_status: GateStatus | None,
    *,
    limit: int = COMPARISON_LIMIT,
) -> tuple[list[ComparisonRecord], TrimReport]:
    """Keep at most ``limit`` records of one target in a declared deterministic order.

    The order is the shortlisted windows first (window_id ascending), then the successive
    Pareto layers of :func:`pareto_shortlist`, each by window_id, then any remaining windows by
    window_id. Every kept record gets that ``sort_rule``; when anything was dropped, each kept
    record also gets one deterministic claim_limits line stating the count. Shortlisted windows
    are never dropped: a shortlist longer than ``limit`` raises.
    """
    by_id = _by_window(records)
    chosen = sorted(set(shortlist))
    if len(chosen) != len(shortlist):
        raise ValueError("the shortlist names a window twice")
    unknown = [w for w in chosen if w not in by_id]
    if unknown:
        raise ValueError(f"shortlisted windows without a comparison: {', '.join(unknown)}")
    if len(chosen) > limit:
        raise ValueError(f"the shortlist ({len(chosen)}) exceeds the limit of {limit}")
    ranking = pareto_shortlist(list(by_id.values()), g5_status)
    seen = set(chosen)
    order = list(chosen)
    for window in (*ranking.order, *sorted(by_id)):
        if window not in seen:
            seen.add(window)
            order.append(window)
    layers = f"pareto layers over {', '.join(ranking.families)}; " if ranking.families else ""
    rule = f"shortlist; then {layers}then window_id"
    kept, dropped = order[:limit], order[limit:]
    line = (
        f"comparisons trimmed to {len(kept)} of {len(order)} for this target (PRD 8.4 limit "
        f"{limit}); {len(dropped)} dropped in the order: {rule}"
    )
    out = []
    for window in kept:
        record = by_id[window]
        limits = [*record.claim_limits, line] if dropped else list(record.claim_limits)
        out.append(record.model_copy(update={"sort_rule": rule, "claim_limits": limits}))
    report = TrimReport(len(order), len(kept), len(dropped), rule, tuple(kept), tuple(dropped))
    return out, report


# --- Ties, reference spread, background (T4L-05; PRD 6.5 steps 5 to 7, 6.6) -------------------


@dataclass(frozen=True, slots=True)
class TieMembership:
    """The tie state of one candidate in one family."""

    tied: bool | None
    tie_group: str | None
    tie_reason: str | None


@dataclass(frozen=True, slots=True)
class TieGroup:
    """Candidates joined by pairwise ties; ``not_pairwise_tied`` makes a chain visible."""

    group_id: str
    members: tuple[str, ...]
    not_pairwise_tied: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class TieResult:
    family: Family
    by_window: Mapping[str, TieMembership]
    groups: tuple[TieGroup, ...]


def tie_groups(
    family: Family,
    entries: Mapping[str, float | None],
    spreads: Mapping[str, float | None],
) -> TieResult:
    """Ties within one distance family (PRD 6.5 step 6); families are never combined.

    Two candidates are tied when ``|d_a - d_b| < s_a + s_b`` (the combined spread is the sum of
    the two supplied spreads, and a difference equal to it is not tied; an engineering choice).
    Groups are the connected components of the tie relation, so A~B and B~C put A, B and C in
    one group, and every pair in a group that is not itself tied is listed. A group id is
    ``"<family>:<smallest window id>"``. A candidate without a supplied spread claims no tie
    (tied null, tie_reason ``spread_unavailable``); one without a distance gets
    ``distance_unavailable``.
    """
    membership: dict[str, TieMembership] = {}
    usable: dict[str, tuple[float, float]] = {}
    for window in sorted(entries):
        value, spread = entries[window], spreads.get(window)
        if value is None:
            membership[window] = TieMembership(None, None, MatchReason.DISTANCE_UNAVAILABLE)
        elif spread is None:
            membership[window] = TieMembership(None, None, MatchReason.SPREAD_UNAVAILABLE)
        elif not (math.isfinite(value) and math.isfinite(spread)) or spread < 0.0:
            raise ValueError(f"{window}: distance and spread must be finite, spread >= 0")
        else:
            usable[window] = (value, spread)

    def tied(a: str, b: str) -> bool:
        (da, sa), (db, sb) = usable[a], usable[b]
        return abs(da - db) < sa + sb

    parent = {w: w for w in usable}

    def root(w: str) -> str:
        while parent[w] != w:
            w = parent[w]
        return w

    for a, b in itertools.combinations(sorted(usable), 2):
        if tied(a, b):
            ra, rb = root(a), root(b)
            parent[max(ra, rb)] = min(ra, rb)
    components: dict[str, list[str]] = {}
    for w in sorted(usable):
        components.setdefault(root(w), []).append(w)
    groups: list[TieGroup] = []
    for members in components.values():
        if len(members) < 2:
            membership[members[0]] = TieMembership(False, None, None)
            continue
        group_id = f"{family}:{members[0]}"
        chain = tuple((a, b) for a, b in itertools.combinations(members, 2) if not tied(a, b))
        groups.append(TieGroup(group_id, tuple(members), chain))
        for w in members:
            membership[w] = TieMembership(True, group_id, None)
    return TieResult(family, MappingProxyType(dict(sorted(membership.items()))), tuple(groups))


def with_tie(entry: DistanceEntry, tie: TieMembership) -> DistanceEntry:
    """``entry`` with the tie fields set; the schema refuses a tie without a reference spread."""
    fields = entry.model_dump() | {
        "tied": tie.tied,
        "tie_group": tie.tie_group,
        "tie_reason": None if tie.tie_reason is None else str(tie.tie_reason),
    }
    return type(entry).model_validate(fields)


def reference_spread(distances: Sequence[float]) -> tuple[ReferenceSpread | None, str | None]:
    """min, median, max and n of the distances within the target reference set (PRD 6.5 step 5).

    The median of an even count is the mean of the two middle values. Fewer than two reference
    windows give ``reference_set_too_small`` (no larger minimum is invented). This is a
    reference, not proof of suitability.
    """
    values = sorted(float(d) for d in distances)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("reference distances must be finite")
    if len(values) < 2:
        return None, MatchReason.REFERENCE_SET_TOO_SMALL
    n = len(values)
    middle = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2.0
    return ReferenceSpread(min=values[0], median=middle, max=values[-1], n=n), None


def with_reference_spread(entry: DistanceEntry, distances: Sequence[float]) -> DistanceEntry:
    """``entry`` with reference_spread filled from :func:`reference_spread`."""
    spread, reason = reference_spread(distances)
    fields = entry.model_dump() | {
        "reference_spread": None if spread is None else spread.model_dump(),
        "reference_spread_reason": None if reason is None else str(reason),
    }
    if spread is None:
        fields |= {"tied": None, "tie_group": None}
    return type(entry).model_validate(fields)


def uncertainty_components(
    *,
    heterogeneity: UncertaintyComponent | None = None,
    measurement_error: UncertaintyComponent | None = None,
    statistical_prediction_error: UncertaintyComponent | None = None,
    scenario_sensitivity: UncertaintyComponent | None = None,
) -> UncertaintyComponents:
    """The four PRD 6.6 components from supplied values, kept separate; a component that is
    not supplied is unavailable with ``uncertainty_not_run``. Nothing combines them."""
    missing = _unavailable_component(MatchReason.UNCERTAINTY_NOT_RUN)
    return UncertaintyComponents(
        heterogeneity=heterogeneity or missing,
        measurement_error=measurement_error or missing,
        statistical_prediction_error=statistical_prediction_error or missing,
        scenario_sensitivity=scenario_sensitivity or missing,
    )


BACKGROUND_STATEMENT: Final = "empirical background comparison, not a physical truth test"


class BackgroundSampleSizeUnsetError(ValueError):
    """Neither the argument nor ``uncertainty.background_sample_size`` sets the sample size."""

    code: Final = MatchReason.BACKGROUND_SAMPLE_SIZE_UNSET

    def __init__(self) -> None:
        super().__init__(f"blocked: {MatchReason.BACKGROUND_SAMPLE_SIZE_UNSET}")


@dataclass(frozen=True, slots=True)
class BackgroundSample:
    """A seeded background sample of eligible windows (PRD 6.5 step 7)."""

    seed: int
    requested_n: int
    actual_n: int
    sample_window_ids: tuple[str, ...]
    excluded_by_status: Mapping[str, int]
    statement: str = BACKGROUND_STATEMENT


def sample_rank(seed: int, window_id: str) -> str:
    """The rank key of a window: sha256 hex of ``"<seed>:<window_id>"`` in UTF-8."""
    return hashlib.sha256(f"{seed}:{window_id}".encode()).hexdigest()


def background_sample(
    universe: Sequence[WindowEntry | tuple[str, str]],
    sample_size: int | None,
    seed: int,
    *,
    config_path: Path = DEFAULT_RUN_CONFIG,
) -> BackgroundSample:
    """Draw ``sample_size`` eligible windows of one search universe, reproducibly.

    Windows are ranked by :func:`sample_rank` (ties by window id) and the first ``sample_size``
    eligible ones are taken, so the rule can be reimplemented anywhere. Rejected and unknown
    windows are excluded and counted by status. A null ``sample_size`` reads
    ``uncertainty.background_sample_size``; when that is null too,
    :class:`BackgroundSampleSizeUnsetError` is raised and no number is picked (ANA-02).
    """
    size = sample_size
    if size is None:
        configured = load_run_config(config_path).value("uncertainty.background_sample_size")
        if configured is None:
            raise BackgroundSampleSizeUnsetError
        if isinstance(configured, bool) or not isinstance(configured, int):
            raise ValueError(
                f"uncertainty.background_sample_size must be an integer: {configured!r}"
            )
        size = configured
    if isinstance(size, bool) or size < 1:
        raise ValueError(f"sample_size must be a positive integer, got {size!r}")
    statuses: dict[str, str] = {}
    for item in universe:
        window, status = (
            (item.window.window_id, item.status) if isinstance(item, WindowEntry) else item
        )
        if window in statuses:
            raise ValueError(f"{window} appears twice in the universe")
        statuses[window] = status
    excluded: dict[str, int] = {}
    eligible: list[str] = []
    for window, status in statuses.items():
        if status == "eligible":
            eligible.append(window)
        else:
            excluded[status] = excluded.get(status, 0) + 1
    ranked = sorted(eligible, key=lambda w: (sample_rank(seed, w), w))[:size]
    return BackgroundSample(
        seed=seed,
        requested_n=size,
        actual_n=len(ranked),
        sample_window_ids=tuple(sorted(ranked)),
        excluded_by_status=MappingProxyType(dict(sorted(excluded.items()))),
    )


@dataclass(frozen=True, slots=True)
class Histogram:
    """One family's background histogram (the BackgroundFamily fields matching needs)."""

    family: Family
    unit: str
    bin_edges: tuple[float, ...]
    counts: tuple[int, ...]
    model_id: str | None = None


def percentile_fields(
    family: Family,
    distance: float | None,
    histogram: Histogram | None,
    *,
    sample_size_unset: bool = False,
) -> tuple[float | None, str | None]:
    """``(background_percentile, background_percentile_reason)``: exactly one is null.

    The percentile is :func:`background_percentile` against the histogram of the same family
    only (another family's histogram raises). The model family without a model histogram is
    ``encoder_not_evaluated``; an unset sample size is ``background_sample_size_unset``; a
    missing histogram is ``background_missing``, an empty one ``background_empty``, and a
    missing distance ``distance_unavailable``.
    """
    if histogram is not None and histogram.family != family:
        raise ValueError(
            f"a {family} distance is never ranked against a {histogram.family} background"
        )
    if sample_size_unset:
        return None, MatchReason.BACKGROUND_SAMPLE_SIZE_UNSET
    if histogram is None:
        if family == "model_embedding":
            return None, MatchReason.ENCODER_NOT_EVALUATED
        return None, MatchReason.BACKGROUND_MISSING
    if distance is None:
        return None, MatchReason.DISTANCE_UNAVAILABLE
    outcome = background_percentile(distance, histogram.bin_edges, histogram.counts)
    return outcome.value, outcome.reason


def with_background_percentile(
    entry: DistanceEntry,
    family: Family,
    histogram: Histogram | None,
    *,
    sample_size_unset: bool = False,
) -> DistanceEntry:
    """``entry`` with the background percentile fields from :func:`percentile_fields`."""
    value, reason = percentile_fields(
        family, entry.value, histogram, sample_size_unset=sample_size_unset
    )
    fields = entry.model_dump() | {
        "background_percentile": value,
        "background_percentile_reason": None if reason is None else str(reason),
    }
    return type(entry).model_validate(fields)


# --- Parity fixture (PRD 8.8; T10-08 and T13-03 mirror these) --------------------------------

PARITY_SCHEMA_VERSION: Final = 1
PARITY_FILE_NAME: Final = "matching_synthetic.json"
PARITY_ATOL: Final = 1e-9
"""PRD 8.8 parity tolerance, absolute, for every float output."""
REPOSITORY_ROOT: Final[Path] = DEFAULT_RUN_CONFIG.parents[1]
DEFAULT_PARITY_PATH: Final[Path] = REPOSITORY_ROOT / "fixtures" / "parity" / PARITY_FILE_NAME
PARITY_SOURCE: Final = "pipeline/src/terraluna_pipeline/matching.py"
_UNCOMPARED: Final = frozenset({"git_commit"})
"""Fields ``--check`` does not compare: a commit hash changes with the next commit."""

_E: Final = math.e
_TerrainCase = tuple[str, str, list[float], list[float | None], list[float], list[float | None]]
_TERRAIN_CASES: Final[tuple[_TerrainCase, ...]] = (
    (
        "hand_case",
        "relief",
        [270.0, 360.0, 540.0],
        [1.0, _E, _E * _E],
        [270.0, 360.0, 540.0],
        [1.0, 1.0, 1.0],
    ),
    (
        "hand_case_texture",
        "texture",
        [270.0, 360.0, 540.0],
        [1.0, _E, _E * _E],
        [270.0, 360.0, 540.0],
        [1.0, 1.0, 1.0],
    ),
    (
        "identical",
        "relief",
        [270.0, 360.0, 540.0, 720.0],
        [1.5, 2.0, 2.5, 3.0],
        [270.0, 360.0, 540.0, 720.0],
        [1.5, 2.0, 2.5, 3.0],
    ),
    (
        "shared_lag_intersection",
        "relief",
        [270.0, 360.0, 540.0],
        [1.0, _E, 5.0],
        [360.0, 540.0, 720.0],
        [1.0, 1.0, 7.0],
    ),
    (
        "shared_lag_intersection_texture",
        "texture",
        [270.0, 360.0, 540.0, 1080.0],
        [0.75, 1.25, 2.5, 4.0],
        [360.0, 1080.0],
        [1.5, 3.5],
    ),
    ("no_shared_lags", "relief", [270.0], [1.0], [360.0], [1.0]),
    ("nu_zero", "texture", [270.0, 360.0], [0.0, 1.0], [270.0, 360.0], [1.0, 1.0]),
)
_PERCENTILE_CASES: Final[tuple[tuple[str, float, list[float], list[int]], ...]] = (
    ("inside", 1.5, [0.0, 1.0, 2.0, 3.0], [2, 3, 5]),
    ("interior_edge", 1.0, [0.0, 1.0, 2.0, 3.0], [2, 3, 5]),
    ("first_edge", 0.0, [0.0, 1.0, 2.0, 3.0], [2, 3, 5]),
    ("last_edge", 3.0, [0.0, 1.0, 2.0, 3.0], [2, 3, 5]),
    ("below", -1.0, [0.0, 1.0, 2.0, 3.0], [2, 3, 5]),
    ("above", 4.0, [0.0, 1.0, 2.0, 3.0], [2, 3, 5]),
    ("uneven_bins", 0.3, [0.0, 0.1, 0.25, 0.7, 1.9], [7, 0, 11, 3]),
    ("empty_histogram", 1.5, [0.0, 1.0, 2.0, 3.0], [0, 0, 0]),
)


def _parity_variant(lags: list[float], nus: list[float | None], detrend: str) -> TerrainVariant:
    return TerrainVariant.model_validate(
        {
            "detrend": "none" if detrend == "relief" else "plane",
            "supported_lags_m": lags,
            "unsupported_lags": [],
            "nu_m": {
                (str(int(lag)) if lag.is_integer() else repr(lag)): nu
                for lag, nu in zip(lags, nus, strict=True)
            },
            "nu_by_direction_m": {},
            "hurst": None,
            "hurst_reason": "insufficient_lags",
            "tilt": None,
        }
    )


def _outcome_json(outcome: Outcome) -> dict[str, object]:
    return {
        "value": outcome.value,
        "reason": None if outcome.reason is None else str(outcome.reason),
    }


def parity_document(
    *, config_path: Path = DEFAULT_RUN_CONFIG, git_commit: str | None = None
) -> dict[str, object]:
    """The matching parity document: synthetic inputs and the Python outputs (PRD 8.8)."""
    terrain_cases: list[dict[str, object]] = []
    for name, variant_name, lags_a, nus_a, lags_b, nus_b in _TERRAIN_CASES:
        a = _parity_variant(lags_a, nus_a, variant_name)
        b = _parity_variant(lags_b, nus_b, variant_name)
        outcome = terrain_distance(a, b)
        terrain_cases.append(
            {
                "name": name,
                "family": f"terrain_{variant_name}",
                "a": {"supported_lags_m": lags_a, "nu_m": dict(a.nu_m)},
                "b": {"supported_lags_m": lags_b, "nu_m": dict(b.nu_m)},
                "expected": {
                    **_outcome_json(outcome),
                    "shared_lags_m": list(outcome.lags_m),
                    "unit": TERRAIN_UNIT,
                },
            }
        )
    percentile_cases = [
        {
            "name": name,
            "d": d,
            "bin_edges": edges,
            "counts": counts,
            "expected": _outcome_json(background_percentile(d, edges, counts)),
        }
        for name, d, edges, counts in _PERCENTILE_CASES
    ]
    return {
        "schema_version": PARITY_SCHEMA_VERSION,
        "data_kind": "synthetic",
        "description": (
            "Python reference outputs of matching.terrain_distance (RMS of log nu over the "
            "shared supported lags, relief and texture separately) and "
            "matching.background_percentile (linear_within_bin_v1, smaller_is_closer). Null "
            "marks an unavailable value with its reason, never 0."
        ),
        "generator": "python -m terraluna_pipeline.matching parity",
        "producing_source": PARITY_SOURCE,
        "git_commit": git_commit,
        "config_sha256": provenance.config_version(config_path),
        "tolerance": {"atol": PARITY_ATOL, "rtol": 0.0},
        "terrain_distance": terrain_cases,
        "background_percentile": percentile_cases,
    }


def parity_text(document: Mapping[str, object]) -> str:
    """Canonical JSON; every float is its shortest round-trip repr, checked bit-equal on reload."""
    text = provenance.canonical_json(document).decode("utf-8")
    _same_floats(document, json.loads(text), "$")
    return text


def _same_floats(original: object, reloaded: object, path: str) -> None:
    if isinstance(original, float):
        if not isinstance(reloaded, float) or struct.pack("<d", original) != struct.pack(
            "<d", reloaded
        ):
            raise ValueError(f"{path}: {original!r} did not survive JSON as {reloaded!r}")
    elif isinstance(original, Mapping):
        if not isinstance(reloaded, dict) or set(reloaded) != set(original):
            raise ValueError(f"{path}: keys changed in JSON")
        for key, value in original.items():
            _same_floats(value, reloaded[key], f"{path}.{key}")
    elif isinstance(original, list | tuple):
        if not isinstance(reloaded, list) or len(reloaded) != len(original):
            raise ValueError(f"{path}: list changed in JSON")
        for index, (a, b) in enumerate(zip(original, reloaded, strict=True)):
            _same_floats(a, b, f"{path}[{index}]")


def producing_commit(repo_root: Path = REPOSITORY_ROOT) -> str:
    """The last commit that changed matching.py, which must be unmodified."""

    def git(*args: str) -> str:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()

    if git("status", "--porcelain", "--", PARITY_SOURCE):
        raise ValueError(f"commit {PARITY_SOURCE} before writing the parity fixture")
    commit = git("log", "-1", "--format=%H", "--", PARITY_SOURCE)
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError(f"no commit holds {PARITY_SOURCE} (got {commit!r})")
    return commit


def parity_differences(committed: Mapping[str, object], fresh: Mapping[str, object]) -> list[str]:
    """Top-level fields that differ, ignoring :data:`_UNCOMPARED`."""
    keys = (set(committed) | set(fresh)) - _UNCOMPARED
    return sorted(key for key in keys if committed.get(key) != fresh.get(key))


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m terraluna_pipeline.matching parity [--check] [--output PATH]``."""
    parser = argparse.ArgumentParser(prog="python -m terraluna_pipeline.matching")
    commands = parser.add_subparsers(dest="command", required=True)
    parity = commands.add_parser("parity", help="write or check the matching parity fixture")
    parity.add_argument("--check", action="store_true", help="compare only; write nothing")
    parity.add_argument("--output", type=Path, default=DEFAULT_PARITY_PATH)
    parity.add_argument("--config", type=Path, default=DEFAULT_RUN_CONFIG)
    arguments = parser.parse_args(argv)
    output: Path = arguments.output
    if arguments.check:
        fresh = json.loads(parity_text(parity_document(config_path=arguments.config)))
        try:
            committed = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"error: cannot read {output}: {error}", file=sys.stderr)
            return 1
        differences = parity_differences(committed, fresh)
        if differences:
            print(f"error: {output} differs in {', '.join(differences)}", file=sys.stderr)
            return 1
        print(f"{output} is current")
        return 0
    try:
        commit = producing_commit()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    text = parity_text(parity_document(config_path=arguments.config, git_commit=commit))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(text.encode("utf-8"))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
