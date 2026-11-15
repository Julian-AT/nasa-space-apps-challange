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
    filled). ``below_min_valid_fraction`` is the T3 code, read from ``terrain.WindowReason``.

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

import bisect
import itertools
import math
import re
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
    ModelEmbeddingDistanceEntry,
    ProductRecord,
    RadarFingerprint,
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
