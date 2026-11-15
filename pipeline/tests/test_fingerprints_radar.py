"""Tests for radar_fingerprint and canonical output (T4L-01; PRD 6.2, 7, 12).

All inputs are synthetic and the test names say so: Stokes S1 and S4 built from chosen SC and
OC under the declared Mini-RF convention (S4 = OC - SC), converted by the merged T2
``derive_circular`` and aggregated by T2 ``aggregate_blocks``. Tolerances are PRD 7's
arithmetic ones (rtol 1e-10, atol 1e-12).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from terraluna_pipeline import provenance
from terraluna_pipeline.fingerprints import (
    FingerprintReason,
    build_window_record,
    input_hash,
    radar_fingerprint,
)
from terraluna_pipeline.polarimetry import (
    MINIRF_SIS_CONVENTION,
    STOKES_INPUT,
    CircularPowers,
    StokesInput,
    derive_circular,
)
from terraluna_pipeline.schema import (
    Distribution,
    FingerprintRecord,
    IncidenceRange,
    ObservedInterpolatedQuality,
    WindowEntry,
    WindowPolygon,
)

pytestmark = pytest.mark.filterwarnings("error")

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_YAML = REPO_ROOT / "config" / "run.yaml"
PRODUCT = "synthetic-minirf-a"
PRODUCTS: list[tuple[str, str | None]] = [(PRODUCT, "b" * 64)]
RTOL = 1e-10
ATOL = 1e-12

Array = npt.NDArray[np.float64]


def synthetic_circular(
    sc: Array, oc: Array, valid: npt.NDArray[np.bool_] | None = None
) -> CircularPowers:
    """Synthetic circular powers: S1 = SC + OC and S4 = OC - SC, through T2 derive_circular."""
    sc = np.asarray(sc, dtype=np.float64)
    oc = np.asarray(oc, dtype=np.float64)
    mask = np.ones(sc.shape, dtype=np.bool_) if valid is None else valid
    return derive_circular(
        STOKES_INPUT,
        StokesInput(s1=sc + oc, s4=oc - sc, s2=None, s3=None),
        MINIRF_SIS_CONVENTION,
        mask,
    )


def synthetic_radar(circular: CircularPowers | None, **overrides: Any) -> FingerprintRecord:
    kwargs: dict[str, Any] = {
        "window_id": "moon-synthetic-w000",
        "body": "moon",
        "product_id": PRODUCT,
        "band": "S",
        "frequency_or_wavelength": "12.6 cm",
        "convention": MINIRF_SIS_CONVENTION,
        "common_support_m": 250.0,
        "pixel_size_m": 125.0,
        "incidence_deg": IncidenceRange(min=48.0, max=49.0),
        "products": PRODUCTS,
        "config_path": RUN_YAML,
        "input_hash": "c" * 64,
    }
    kwargs.update(overrides)
    return radar_fingerprint(circular, **kwargs)


def full(value: float, shape: tuple[int, int] = (4, 4)) -> Array:
    return np.full(shape, value, dtype=np.float64)


def distribution(record: FingerprintRecord, name: str) -> Distribution | None:
    assert record.radar is not None
    value: Distribution | None = getattr(record.radar, name)
    return value


# --- values ------------------------------------------------------------------------------------


def test_synthetic_valid_cpr_of_three_survives_unclipped() -> None:
    record = synthetic_radar(synthetic_circular(full(3.0), full(1.0)))
    assert record.kind == "radar"
    assert record.terrain is None
    assert record.evidence_label == "Derived"
    radar = record.radar
    assert radar is not None
    assert radar.reason is None
    assert radar.n_blocks == 4
    assert radar.n_valid == 4
    assert radar.valid_fraction == 1.0
    assert radar.support_m == 250.0
    assert radar.aggregation == "ratio_of_mean_powers"
    assert radar.blocks_independent is False
    for name in ("cpr_ratio_of_mean_powers", "cpr_mean_of_pixelwise_ratios"):
        dist = distribution(record, name)
        assert dist is not None
        assert dist.values == [3.0] * 4
        assert dist.max == 3.0
    sc = distribution(record, "sc")
    oc = distribution(record, "oc")
    assert sc is not None and sc.median == 3.0
    assert oc is not None and oc.median == 1.0
    assert radar.total_power is None
    assert radar.handedness_ratio is None
    assert record.run_id == provenance.run_id(RUN_YAML, PRODUCTS)


def test_synthetic_missing_invalid_and_valid_low_cpr_stay_distinct() -> None:
    # Four 2x2 blocks: top-left masked as noise (missing), top-right OC = 0 (invalid),
    # bottom-left SC = 0 (a valid CPR of 0), bottom-right CPR 2.
    sc = full(2.0)
    oc = full(1.0)
    valid = np.ones((4, 4), dtype=np.bool_)
    valid[:2, :2] = False
    sc[:2, 2:] = 1.0
    oc[:2, 2:] = 0.0
    sc[2:, :2] = 0.0
    record = synthetic_radar(synthetic_circular(sc, oc, valid))
    radar = record.radar
    assert radar is not None
    assert radar.n_blocks == 4
    assert radar.n_valid == 2
    assert radar.valid_fraction == 0.5
    cpr = distribution(record, "cpr_ratio_of_mean_powers")
    assert cpr is not None
    # The valid low CPR is a real 0; the missing and invalid blocks give no value at all.
    assert cpr.values == [0.0, 2.0]
    assert all(math.isfinite(v) for v in cpr.values)


def test_synthetic_all_invalid_blocks_are_unavailable_with_null_distributions() -> None:
    record = synthetic_radar(synthetic_circular(full(1.0), full(0.0)))
    assert record.evidence_label == "Unavailable"
    radar = record.radar
    assert radar is not None
    assert radar.reason == FingerprintReason.NO_VALID_BLOCKS
    assert radar.reason == "no_valid_blocks"
    assert radar.n_blocks == 4
    assert radar.n_valid == 0
    assert radar.valid_fraction == 0.0
    for name in ("cpr_ratio_of_mean_powers", "cpr_mean_of_pixelwise_ratios", "sc", "oc"):
        assert distribution(record, name) is None


def test_synthetic_noise_masked_window_is_unavailable() -> None:
    masked = np.zeros((4, 4), dtype=np.bool_)
    record = synthetic_radar(synthetic_circular(full(2.0), full(1.0), masked))
    assert record.radar is not None
    assert record.radar.reason == "no_valid_blocks"
    assert record.evidence_label == "Unavailable"


def test_synthetic_unknown_convention_is_unavailable() -> None:
    record = synthetic_radar(synthetic_circular(full(2.0), full(1.0)), convention=None)
    assert record.evidence_label == "Unavailable"
    assert record.radar is not None
    assert record.radar.reason == FingerprintReason.CONVENTION_UNKNOWN
    assert record.radar.convention is None
    assert record.radar.cpr_ratio_of_mean_powers is None


def test_synthetic_missing_channel_is_unavailable() -> None:
    record = synthetic_radar(None)
    assert record.evidence_label == "Unavailable"
    assert record.radar is not None
    assert record.radar.reason == FingerprintReason.MISSING_CHANNEL
    assert record.radar.n_blocks == 0
    assert record.radar.sc is None


def test_reason_codes_are_the_contract_vocabulary() -> None:
    assert {reason.value for reason in FingerprintReason} == {
        "convention_unknown",
        "missing_channel",
        "no_valid_blocks",
    }


# --- support ----------------------------------------------------------------------------------


def test_synthetic_block_size_comes_from_the_support_and_the_stated_pixel_size() -> None:
    circular = synthetic_circular(full(2.0, (8, 8)), full(1.0, (8, 8)))
    assert synthetic_radar(circular, pixel_size_m=62.5).radar.n_blocks == 4  # type: ignore[union-attr]
    assert synthetic_radar(circular, pixel_size_m=125.0, common_support_m=500.0).radar.n_blocks == 4  # type: ignore[union-attr]
    # 250 / 83.3 rounds to 3 pixels: the recorded support is the block side actually used.
    odd = synthetic_circular(full(2.0, (6, 6)), full(1.0, (6, 6)))
    record = synthetic_radar(odd, pixel_size_m=80.0)
    assert record.radar is not None
    assert record.radar.support_m == 240.0
    assert record.radar.n_blocks == 4


def test_synthetic_missing_pixel_size_raises() -> None:
    with pytest.raises(ValueError, match="pixel size"):
        synthetic_radar(synthetic_circular(full(2.0), full(1.0)), pixel_size_m=None)


def test_synthetic_mars_gets_no_radar_fingerprint() -> None:
    with pytest.raises(ValueError, match="Mars"):
        synthetic_radar(None, body="mars", window_id="mars-synthetic-w000")


def test_synthetic_mismatched_convention_or_product_raises() -> None:
    circular = synthetic_circular(full(2.0), full(1.0))
    with pytest.raises(ValueError, match="convention"):
        synthetic_radar(circular, convention="SC_OPP_TX_1_PLUS_I")
    with pytest.raises(ValueError, match="product"):
        synthetic_radar(circular, product_id="synthetic-other")


# --- properties (hypothesis) -----------------------------------------------------------------

powers = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False)


def summaries(record: FingerprintRecord) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for name in ("cpr_ratio_of_mean_powers", "cpr_mean_of_pixelwise_ratios", "sc", "oc"):
        dist = distribution(record, name)
        assert dist is not None
        out[name] = [*dist.values, dist.median, dist.p05, dist.p95]  # type: ignore[list-item]
    return out


def assert_close(a: list[float], b: list[float]) -> None:
    assert len(a) == len(b)
    for x, y in zip(a, b, strict=True):
        assert abs(x - y) <= ATOL + RTOL * abs(y), (x, y)


@settings(max_examples=40, deadline=None)
@given(
    sc=st.lists(powers, min_size=16, max_size=16),
    oc=st.lists(powers, min_size=16, max_size=16),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_synthetic_permuting_pixels_inside_a_block_keeps_the_statistics(
    sc: list[float], oc: list[float], seed: int
) -> None:
    sc_grid = np.array(sc, dtype=np.float64).reshape(4, 4)
    oc_grid = np.array(oc, dtype=np.float64).reshape(4, 4)
    base = summaries(synthetic_radar(synthetic_circular(sc_grid, oc_grid)))
    rng = np.random.default_rng(seed)
    sc_perm = sc_grid.copy()
    oc_perm = oc_grid.copy()
    for r in (0, 2):
        for c in (0, 2):
            order = rng.permutation(4)
            sc_perm[r : r + 2, c : c + 2] = (
                sc_grid[r : r + 2, c : c + 2].reshape(4)[order].reshape(2, 2)
            )
            oc_perm[r : r + 2, c : c + 2] = (
                oc_grid[r : r + 2, c : c + 2].reshape(4)[order].reshape(2, 2)
            )
    permuted = summaries(synthetic_radar(synthetic_circular(sc_perm, oc_perm)))
    for name, values in base.items():
        assert_close(permuted[name], values)


@settings(max_examples=40, deadline=None)
@given(
    sc=st.lists(powers, min_size=16, max_size=16),
    oc=st.lists(powers, min_size=16, max_size=16),
    factor=st.floats(min_value=1e-3, max_value=1e3),
)
def test_synthetic_scaling_sc_and_oc_together_keeps_the_cpr_summaries(
    sc: list[float], oc: list[float], factor: float
) -> None:
    sc_grid = np.array(sc, dtype=np.float64).reshape(4, 4)
    oc_grid = np.array(oc, dtype=np.float64).reshape(4, 4)
    base = summaries(synthetic_radar(synthetic_circular(sc_grid, oc_grid)))
    scaled = summaries(synthetic_radar(synthetic_circular(sc_grid * factor, oc_grid * factor)))
    for name in ("cpr_ratio_of_mean_powers", "cpr_mean_of_pixelwise_ratios"):
        assert_close(scaled[name], base[name])


# --- canonical output ------------------------------------------------------------------------


def synthetic_entry() -> WindowEntry:
    circular = synthetic_circular(full(3.0), full(1.0))
    radar = synthetic_radar(
        circular,
        window_id="earth-synthetic-w000",
        body="earth",
        input_hash=input_hash(
            arrays={"sc": circular.sc.value, "oc": circular.oc.value},
            rings={"ring_m": RING},
            settings={"common_support_m": 250.0},
        ),
    )
    return build_window_record(
        window_id="earth-synthetic-w000",
        body="earth",
        polygon=WindowPolygon(
            frame="utm",
            crs="EPSG:32627",
            epsg=32627,
            utm_zone="27N",
            ring_m=RING,
            ring_lonlat=[[-16.8, 64.98], [-16.7, 64.98], [-16.7, 64.94], [-16.8, 64.98]],
        ),
        area_m2=25_000_000.0,
        analysis_grid_m=90.0,
        product_ids=[PRODUCT],
        terrain=None,
        radar=[radar],
        status="unknown",
        reasons=["cache_missing"],
        search_region_id="holuhraun",
        scene_ids=["synthetic-scene"],
        acquisition_dates=["2015-05-30"],
        permanent_shadow_fraction=None,
        observed_interpolated_quality=ObservedInterpolatedQuality(
            observed_fraction=None,
            interpolated_fraction=None,
            source=None,
            reason="not_applicable_body",
        ),
        grid_index=[0, 1],
        card_id=None,
    )


RING = [[0.0, 0.0], [500.0, 0.0], [500.0, 500.0], [0.0, 0.0]]


def test_synthetic_window_with_radar_labels_the_product_derived() -> None:
    entry = synthetic_entry()
    key = f"radar_cpr:{PRODUCT}"
    assert entry.window.evidence_status_by_field[key] == "Derived"
    assert entry.window.valid_fraction_by_observable[key] == 1.0
    assert "radar_cpr" not in entry.window.valid_fraction_by_observable


def test_synthetic_same_input_gives_byte_identical_canonical_json() -> None:
    first = provenance.canonical_json(synthetic_entry().model_dump(mode="json"))
    second = provenance.canonical_json(synthetic_entry().model_dump(mode="json"))
    assert first == second
    reloaded = WindowEntry.model_validate_json(first)
    assert provenance.canonical_json(reloaded.model_dump(mode="json")) == first
