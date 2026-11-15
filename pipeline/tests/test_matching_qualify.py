"""qualify_pair (T4L-02): eligibility, reasons, incidence binning and the Mode B ban.

All inputs are labelled synthetic test inputs; incidence edges here are test-only values and
never config values (ANA-01 stays unset).
"""

from __future__ import annotations

import itertools
import json
import re
from typing import TYPE_CHECKING, Any, get_args

import pytest

from terraluna_pipeline import terrain
from terraluna_pipeline.config import DEFAULT_RUN_CONFIG
from terraluna_pipeline.matching import (
    BELOW_MIN_VALID_FRACTION,
    OBSERVABLES,
    MatchReason,
    QualifySettings,
    WindowSummary,
    incidence_bin,
    parse_band,
    qualify_pair,
)
from terraluna_pipeline.schema import ExcludedObservable

if TYPE_CHECKING:  # mypy's name for the builders; pytest puts pipeline/tests on sys.path
    from pipeline.tests.test_matching_builders import (
        entry,
        product,
        radar_fp,
        radar_product,
        variant,
    )
else:
    from test_matching_builders import entry, product, radar_fp, radar_product, variant

SYNTHETIC_EDGES = (20.0, 30.0, 40.0, 50.0)
"""Test-only incidence edges in degrees (synthetic, not a config value)."""

DOCUMENTED = QualifySettings(
    min_valid_fraction=0.8,
    incidence_bins_deg=SYNTHETIC_EDGES,
    snr_rule="synthetic rule",
    radar_min_valid_fraction=0.5,
)
CONFIG = QualifySettings.from_config(DEFAULT_RUN_CONFIG)

TARGET_ID = "moon-sps-x2-y2"
EARTH_ID = "earth-utm27n-e280-n2880"
BANDS = {
    "P": "0.43 GHz",
    "L": "1.2575 GHz",
    "S": "2380 MHz",
    "C": "5.405 GHz",
    "X": "3.1 cm",
}


def summaries(**earth_kwargs: Any) -> tuple[WindowSummary, WindowSummary]:
    target = entry(TARGET_ID, radar=[radar_fp(TARGET_ID, "t-radar")])
    earth = entry(EARTH_ID, radar=[radar_fp(EARTH_ID, "e-radar")], **earth_kwargs)
    return WindowSummary.from_entry(target), WindowSummary.from_entry(earth)


def radar_pair(target_band: str, earth_band: str) -> tuple[Any, Any]:
    return (
        radar_product("t-radar", BANDS[target_band]),
        radar_product("e-radar", BANDS[earth_band], body="earth"),
    )


def test_reason_codes_are_snake_case_and_fit_the_excluded_record() -> None:
    codes = [str(code) for code in MatchReason]
    assert len(set(codes)) == len(codes)
    assert all(re.fullmatch(r"^[a-z][a-z0-9_]*$", code) for code in codes)
    ExcludedObservable(observable="radar_cpr", reasons=codes)


def test_below_min_valid_fraction_is_the_t3_code() -> None:
    assert get_args(terrain.WindowReason)[0] == BELOW_MIN_VALID_FRACTION
    assert "below_min_valid_fraction" not in {code.value for code in MatchReason}


def test_band_is_parsed_from_numbers_and_units_only() -> None:
    assert parse_band("1.2575 GHz") == "L"
    assert parse_band("23.8 cm") == "L"
    assert parse_band("CENTER_FREQUENCY = 2380 <MHz>") == "S"
    assert parse_band("S-band") is None
    assert parse_band(None) is None
    # The Mini-RF label pairs a mislabelled unit with the catalog value: they disagree.
    assert parse_band("CENTER_FREQUENCY = 2379.9994969 <GHz>; 'S-band (2.38 GHz)'") is None


def test_mode_a_same_band_with_all_facts_is_comparable() -> None:
    target, earth = summaries()
    tp, ep = radar_pair("L", "L")
    result = qualify_pair(tp, ep, "radar_cpr", "A", target, earth, settings=DOCUMENTED)
    assert result.comparable, result.reasons
    assert result.reasons == ()
    assert result.facts["target_band"] == result.facts["earth_band"] == "L"
    assert all(value is not None for value in result.facts.values())


def test_mode_a_different_bands_are_a_band_mismatch() -> None:
    target, earth = summaries()
    tp, ep = radar_pair("S", "L")
    result = qualify_pair(tp, ep, "radar_cpr", "A", target, earth, settings=DOCUMENTED)
    assert not result.comparable
    assert result.reasons == (MatchReason.BAND_MISMATCH,)


def test_missing_frequency_is_band_unknown_not_a_pass() -> None:
    target, earth = summaries()
    tp, ep = radar_pair("L", "L")
    tp = tp.model_copy(update={"frequency_or_wavelength": None})
    result = qualify_pair(tp, ep, "radar_cpr", "A", target, earth, settings=DOCUMENTED)
    assert MatchReason.BAND_UNKNOWN in result.reasons
    assert result.facts["target_band"] is None


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("transmit_receive_convention", "unknown", MatchReason.CONVENTION_UNKNOWN),
        ("calibration_type", None, MatchReason.CALIBRATION_UNRESOLVED),
        ("qa_definition", "unknown", MatchReason.QA_MISSING),
        ("incidence_source", "an incidence layer", MatchReason.INCIDENCE_UNIT_UNKNOWN),
    ],
)
def test_each_unknown_fact_adds_its_own_code(field: str, value: str | None, code: str) -> None:
    target, earth = summaries()
    tp, ep = radar_pair("L", "L")
    tp = tp.model_copy(update={field: value})
    result = qualify_pair(tp, ep, "radar_cpr", "A", target, earth, settings=DOCUMENTED)
    assert result.reasons == (code,)


def test_support_coverage_and_geometry_failures() -> None:
    tp, ep = radar_pair("L", "L")
    target = WindowSummary.from_entry(entry(TARGET_ID, radar=[radar_fp(TARGET_ID, "t-radar")]))
    earth = WindowSummary.from_entry(
        entry(
            EARTH_ID,
            radar=[
                radar_fp(
                    EARTH_ID,
                    "e-radar",
                    support_m=500.0,
                    valid_fraction=0.25,
                    incidence=(31.0, 33.0),
                )
            ],
        )
    )
    result = qualify_pair(tp, ep, "radar_cpr", "A", target, earth, settings=DOCUMENTED)
    assert result.reasons == (
        MatchReason.GEOMETRY_INCOMPATIBLE,
        MatchReason.SUPPORT_MISMATCH,
        MatchReason.COVERAGE_BELOW_THRESHOLD,
    )


def test_null_config_bins_and_snr_rule_make_radar_not_comparable() -> None:
    assert CONFIG.incidence_bins_deg is None
    target, earth = summaries()
    tp, ep = radar_pair("L", "L")
    result = qualify_pair(tp, ep, "radar_cpr", "A", target, earth, settings=CONFIG)
    assert not result.comparable
    assert MatchReason.INCIDENCE_BINS_UNSET in result.reasons
    assert MatchReason.NOISE_RULE_UNSET in result.reasons
    assert result.facts["incidence_bins"] is None


def test_mode_b_sweep_never_makes_radar_eligible_and_never_shows_a_verdict() -> None:
    target, earth = summaries()
    for (tb, eb), observable in itertools.product(itertools.product(BANDS, repeat=2), OBSERVABLES):
        tp, ep = radar_pair(tb, eb)
        for settings in (DOCUMENTED, CONFIG):
            result = qualify_pair(tp, ep, observable, "B", target, earth, settings=settings)
            text = json.dumps(result.to_json()).lower()
            assert "radar match: passed" not in text
            assert "passed" not in text
            if observable == "radar_cpr":
                assert not result.comparable
                assert result.reasons[-1] == MatchReason.MODE_B_CONTEXT_ONLY
                if tb != eb:
                    assert MatchReason.CROSS_BAND_CALIBRATION_UNRESOLVED in result.reasons
                    assert MatchReason.BAND_MISMATCH not in result.reasons
            else:
                assert result.comparable, result.reasons


def test_mode_c_excludes_radar_and_keeps_terrain() -> None:
    target, earth = summaries()
    tp, ep = radar_pair("L", "L")
    radar = qualify_pair(tp, ep, "radar_cpr", "C", target, earth, settings=DOCUMENTED)
    assert not radar.comparable
    assert radar.reasons == (MatchReason.RADAR_NOT_COMPLETED,)
    for observable in ("terrain_relief", "terrain_texture"):
        in_c = qualify_pair(product(), product(), observable, "C", target, earth, settings=CONFIG)
        in_a = qualify_pair(product(), product(), observable, "A", target, earth, settings=CONFIG)
        assert in_c.comparable and in_a.comparable
        assert in_c.facts == in_a.facts


def test_null_shipping_mode_is_never_defaulted() -> None:
    target, earth = summaries()
    for observable in OBSERVABLES:
        result = qualify_pair(
            product(), product(), observable, None, target, earth, settings=CONFIG
        )
        assert not result.comparable
        assert result.reasons[0] == MatchReason.SHIPPING_MODE_UNSET
        assert result.shipping_mode is None


def test_mars_radar_is_unavailable_and_terrain_needs_ctx_qa() -> None:
    mars_id = "mars-jezero-w000"
    target = WindowSummary.from_entry(entry(mars_id))
    earth = WindowSummary.from_entry(entry(EARTH_ID))
    radar = qualify_pair(product(), product(), "radar_cpr", "C", target, earth, settings=CONFIG)
    assert radar.reasons[0] == MatchReason.RADAR_UNAVAILABLE_FOR_BODY
    without = qualify_pair(
        product(qa_definition=None),
        product(),
        "terrain_relief",
        "C",
        target,
        earth,
        settings=CONFIG,
    )
    assert without.reasons == (MatchReason.QA_MISSING,)
    ctx = product(qa_definition="CTX-MOLA difference statistics (synthetic)")
    with_qa = qualify_pair(ctx, product(), "terrain_relief", "C", target, earth, settings=CONFIG)
    assert with_qa.comparable


def test_terrain_valid_fraction_threshold_is_inclusive() -> None:
    assert CONFIG.min_valid_fraction == 0.8
    target = WindowSummary.from_entry(entry(TARGET_ID))
    at = WindowSummary.from_entry(entry(EARTH_ID, valid_fraction=0.8))
    below = WindowSummary.from_entry(entry(EARTH_ID, valid_fraction=0.79))
    ok = qualify_pair(product(), product(), "terrain_relief", "C", target, at, settings=CONFIG)
    assert ok.comparable
    bad = qualify_pair(product(), product(), "terrain_relief", "C", target, below, settings=CONFIG)
    assert bad.reasons == (BELOW_MIN_VALID_FRACTION,)


def test_terrain_needs_shared_lags_and_the_same_grid() -> None:
    target = WindowSummary.from_entry(entry(TARGET_ID, relief=variant([1.0], lags=[270.0])))
    earth = WindowSummary.from_entry(
        entry(EARTH_ID, relief=variant([1.0], lags=[360.0]), analysis_grid_m=180.0)
    )
    result = qualify_pair(
        product(), product(), "terrain_relief", "C", target, earth, settings=CONFIG
    )
    assert result.reasons == (MatchReason.ANALYSIS_GRID_MISMATCH, MatchReason.NO_SHARED_LAGS)
    assert result.facts["shared_lags_m"] == ()
    texture = qualify_pair(
        product(), product(), "terrain_texture", "C", target, earth, settings=CONFIG
    )
    assert texture.reasons == (MatchReason.ANALYSIS_GRID_MISMATCH,)


def test_missing_terrain_fingerprint_is_not_a_pass() -> None:
    target = WindowSummary.from_entry(entry(TARGET_ID))
    earth = WindowSummary.from_entry(entry(EARTH_ID, terrain=False))
    result = qualify_pair(
        product(), product(), "terrain_relief", "C", target, earth, settings=CONFIG
    )
    assert not result.comparable
    assert MatchReason.TERRAIN_FINGERPRINT_MISSING in result.reasons
    assert MatchReason.VALID_FRACTION_UNKNOWN in result.reasons


def test_model_embedding_is_not_decided_here() -> None:
    target, earth = summaries()
    with pytest.raises(ValueError, match="qualify_pair decides"):
        qualify_pair(
            product(),
            product(),
            "model_embedding",  # type: ignore[arg-type]
            "C",
            target,
            earth,
            settings=CONFIG,
        )


def test_the_same_number_in_radians_and_degrees_never_shares_a_bin_silently() -> None:
    edges = (0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0)  # synthetic, test-only
    for angle in (0.5, 0.7, 0.9):
        in_degrees, _ = incidence_bin(angle, unit="degrees", edges_deg=edges)
        in_radians, _ = incidence_bin(angle, unit="radians", edges_deg=edges)
        assert in_degrees == 0
        assert in_radians is not None and in_radians != in_degrees
    assert incidence_bin(0.7, unit=None, edges_deg=edges) == (
        None,
        MatchReason.INCIDENCE_UNIT_UNKNOWN,
    )
    assert incidence_bin(0.7, unit="degrees", edges_deg=None) == (
        None,
        MatchReason.INCIDENCE_BINS_UNSET,
    )


def test_qualify_pair_is_pure_and_records_facts_when_excluded() -> None:
    target, earth = summaries()
    tp, ep = radar_pair("S", "L")
    first = qualify_pair(tp, ep, "radar_cpr", "C", target, earth, settings=DOCUMENTED)
    second = qualify_pair(tp, ep, "radar_cpr", "C", target, earth, settings=DOCUMENTED)
    assert first == second
    assert first.facts["target_band"] == "S" and first.facts["earth_band"] == "L"
    assert first.facts["target_support_m"] == 250.0
