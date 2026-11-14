"""Contract tests for ProductRecord and sources.json (PRD 5 entry rule, 8.4, 8.12).

The ProductRecord field list is read from PRD 8.12 itself rather than copied here. Every field
is required. A value that was not established is the string "unknown" or an explicit null, never
an empty string or 0, and source URLs never carry credentials or signatures (PRD 8.12 rules).
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, TypeAdapter, ValidationError

from terraluna_pipeline.schema import (
    CONTRACTS,
    SIGNED_URL_TOKENS,
    ProductRecord,
    has_url_credentials,
)

PRD = Path(__file__).resolve().parents[2] / "PRD.md"
HEX64 = "0123456789abcdef" * 4
STATUSES = ["NOT_CHECKED", "VERIFIED", "PARTIAL", "BLOCKED", "FAILED"]
NULLABLE = {
    "source_url",
    "local_path",
    "checksum",
    "acquisition_time",
    "frequency_or_wavelength",
    "transmit_receive_convention",
    "calibration_type",
    "native_grid",
    "effective_resolution",
    "incidence_source",
    "qa_definition",
}
"""Fields that may be null: they do not apply to every product (a DEM has no radar frequency),
or, for the formatted fields, null is how "unknown" is written."""
FORMATTED = {"source_url", "local_path", "checksum"}
"""Fields with a format, for which the string "unknown" is not a valid value."""
ENUMERATED = {"body", "verification_status"}


# --- PRD extraction and helpers -----------------------------------------------------------------


def prd_section(heading: str) -> str:
    text = PRD.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n")
    end = text.find("\n#", start + len(heading) + 2)
    return text[start : end if end != -1 else len(text)]


def prd_product_record_fields() -> list[str]:
    line = next(
        line
        for line in prd_section("### 8.12 Records").splitlines()
        if line.startswith("- **ProductRecord:**")
    )
    return re.findall(r"`([^`]+)`", line)


def literal_values(annotation: object) -> set[object]:
    if typing.get_origin(annotation) is typing.Literal:
        return set(typing.get_args(annotation))
    values: set[object] = set()
    for arg in typing.get_args(annotation):
        values |= literal_values(arg)
    return values


def product(**overrides: Any) -> dict[str, Any]:
    """A complete, verified record. Its values are schema test inputs, not observations."""
    record: dict[str, Any] = {
        "product_id": "example-product",
        "provider": "Example provider",
        "version": "v1",
        "source_url": "https://data.example.org/products/example/dem.tif",
        "local_path": "data/raw/example/dem.tif",
        "checksum": HEX64,
        "body": "moon",
        "acquisition_time": "unknown",
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "m",
        "calibration_type": None,
        "native_grid": "width=4 height=3",
        "effective_resolution": "unknown",
        "crs_and_frame": "unknown",
        "incidence_source": None,
        "qa_definition": "unknown",
        "license_or_attribution": "unknown",
        "verification_status": "VERIFIED",
    }
    record.update(overrides)
    return record


def assert_accepted(value: Any) -> ProductRecord:
    parsed = ProductRecord.model_validate(value)
    assert ProductRecord.model_validate_json(json.dumps(value)) == parsed
    assert json.loads(parsed.model_dump_json()) == value
    return parsed


def assert_rejected(value: Any, loc: tuple[str | int, ...], error_type: str | None = None) -> None:
    """Reject in both Python and JSON mode, with exactly one error at `loc`."""
    for mode in ("python", "json"):
        with pytest.raises(ValidationError) as excinfo:
            if mode == "python":
                ProductRecord.model_validate(value)
            else:
                ProductRecord.model_validate_json(json.dumps(value))
        errors = excinfo.value.errors()
        assert [error["loc"] for error in errors] == [loc], errors
        if error_type is not None:
            assert errors[0]["type"] == error_type, errors


def sources_adapter() -> TypeAdapter[Any]:
    return next(contract.adapter for contract in CONTRACTS if contract.name == "Sources")


# --- PRD conformance ----------------------------------------------------------------------------


def test_fields_are_exactly_the_prd_8_12_fields_in_order() -> None:
    fields = prd_product_record_fields()
    assert len(fields) == 19
    assert list(ProductRecord.model_fields) == fields
    assert list(product()) == fields


def test_every_field_is_required_without_default() -> None:
    for name, field in ProductRecord.model_fields.items():
        assert field.is_required(), f"ProductRecord.{name} has a default"


def test_verification_status_allows_exactly_the_five_states() -> None:
    annotation = ProductRecord.model_fields["verification_status"].annotation
    assert literal_values(annotation) == set(STATUSES)


def test_body_is_one_of_the_three_bodies() -> None:
    assert literal_values(ProductRecord.model_fields["body"].annotation) == {
        "moon",
        "mars",
        "earth",
    }


def test_product_record_is_a_bundle_record() -> None:
    assert issubclass(ProductRecord, BaseModel)
    assert ProductRecord.model_config.get("extra") == "forbid"
    assert ProductRecord.model_config.get("strict") is True
    assert ProductRecord.model_config.get("frozen") is True


# --- Whole records ------------------------------------------------------------------------------


def test_complete_record_parses_and_round_trips() -> None:
    parsed = assert_accepted(product())
    assert parsed.verification_status == "VERIFIED"
    assert parsed.frequency_or_wavelength is None


def test_blocked_record_with_nothing_read_parses() -> None:
    value = product(
        source_url=None,
        local_path=None,
        checksum=None,
        version="unknown",
        units="unknown",
        native_grid="unknown",
        verification_status="BLOCKED",
    )
    parsed = assert_accepted(value)
    assert parsed.checksum is None


@pytest.mark.parametrize("field", prd_product_record_fields())
def test_record_missing_any_field_fails(field: str) -> None:
    value = product()
    del value[field]
    assert_rejected(value, (field,), "missing")


def test_unknown_field_fails() -> None:
    assert_rejected(product(notes="free text"), ("notes",), "extra_forbidden")


@pytest.mark.parametrize("field", prd_product_record_fields())
def test_no_field_accepts_an_empty_string(field: str) -> None:
    assert_rejected(product(**{field: ""}), (field,))


@pytest.mark.parametrize("field", prd_product_record_fields())
@pytest.mark.parametrize("zero", [0, 0.0, False])
def test_no_field_accepts_zero(field: str, zero: object) -> None:
    assert_rejected(product(**{field: zero}), (field,))


@pytest.mark.parametrize("field", sorted(NULLABLE))
def test_nullable_fields_accept_an_explicit_null(field: str) -> None:
    parsed = assert_accepted(product(**{field: None}, verification_status="PARTIAL"))
    assert getattr(parsed, field) is None


@pytest.mark.parametrize("field", sorted(set(prd_product_record_fields()) - NULLABLE))
def test_other_fields_reject_null(field: str) -> None:
    assert_rejected(product(**{field: None}), (field,))


@pytest.mark.parametrize(
    "field", sorted(set(prd_product_record_fields()) - FORMATTED - ENUMERATED - {"product_id"})
)
def test_descriptive_fields_accept_unknown(field: str) -> None:
    assert_accepted(product(**{field: "unknown"}, verification_status="PARTIAL"))


@pytest.mark.parametrize("field", sorted(FORMATTED))
def test_formatted_fields_reject_the_string_unknown(field: str) -> None:
    assert_rejected(product(**{field: "unknown"}), (field,))


@pytest.mark.parametrize("status", STATUSES)
def test_verification_status_accepts_each_state(status: str) -> None:
    assert_accepted(product(verification_status=status))


@pytest.mark.parametrize("status", ["VERIFIED ", "verified", "PASS", "NOT_RUN", "UNKNOWN"])
def test_verification_status_rejects_anything_else(status: str) -> None:
    assert_rejected(product(verification_status=status), ("verification_status",))


@pytest.mark.parametrize("body", ["Moon", "lunar", "unknown", "venus"])
def test_body_rejects_anything_else(body: str) -> None:
    assert_rejected(product(body=body), ("body",))


def test_records_are_frozen() -> None:
    record = ProductRecord.model_validate(product())
    with pytest.raises(ValidationError):
        record.units = "km"  # type: ignore[misc]


# --- VERIFIED needs a decoded file with known units (PRD 5 entry rule, 8.11) -------------------


@pytest.mark.parametrize("units", ["unknown", "Unknown", "UNKNOWN"])
def test_verified_record_with_unknown_units_fails(units: str) -> None:
    assert_rejected(product(units=units), ())


@pytest.mark.parametrize("field", ["checksum", "local_path"])
def test_verified_record_without_a_local_file_fails(field: str) -> None:
    assert_rejected(product(**{field: None}), ())


@pytest.mark.parametrize("status", ["NOT_CHECKED", "PARTIAL", "BLOCKED", "FAILED"])
def test_unverified_records_may_have_unknown_units_and_no_file(status: str) -> None:
    assert_accepted(
        product(units="unknown", checksum=None, local_path=None, verification_status=status)
    )


# --- checksum -----------------------------------------------------------------------------------


@given(st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))
def test_checksum_accepts_any_64_lowercase_hex(checksum: str) -> None:
    assert_accepted(product(checksum=checksum))


@pytest.mark.parametrize(
    "checksum",
    [HEX64.upper(), HEX64[:63], HEX64 + "0", HEX64 + "\n", "sha256:" + HEX64, "g" + HEX64[1:]],
    ids=["uppercase", "63-chars", "65-chars", "trailing-newline", "prefixed", "non-hex"],
)
def test_checksum_rejects_non_canonical_digests(checksum: str) -> None:
    assert_rejected(product(checksum=checksum), ("checksum",))


# --- local_path ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "data/raw/pgda78/Site01_surf.tif",
        "data/raw/minirf/window-rows-100-200.bin",
        "data/interim/site01.tif",
        "data/processed/moon/site01_90m.tif",
    ],
)
def test_local_path_accepts_paths_under_the_data_directories(path: str) -> None:
    assert_accepted(product(local_path=path))


@pytest.mark.parametrize(
    "path",
    [
        "/Users/someone/data/raw/dem.tif",
        "~/data/raw/dem.tif",
        "../data/raw/dem.tif",
        "data/../raw/dem.tif",
        "data//raw/dem.tif",
        "data/raw/",
        "C:\\data\\dem.tif",
        "data/raw/my dem.tif",
        "data/raw/.hidden/dem.tif",
        "dem.tif",
        "data/dem.tif",
        "data/raw",
        "outputs/pilot/dem.tif",
        "data/cache/dem.tif",
    ],
)
def test_local_path_rejects_paths_outside_data_or_malformed(path: str) -> None:
    assert_rejected(product(local_path=path), ("local_path",))


# --- source_url ---------------------------------------------------------------------------------

PUBLIC_URLS = [
    "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site01/Site01_final_adj_5mpp_surf.tif",
    "https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-5-cdr-mosaic-v1/lromrf_0005/",
    "https://ode.rsl.wustl.edu/moon/indexproductpage.aspx?product_id=EXAMPLE&product_idGeo=1",
    "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/tileList.txt",
    "s3://copernicus-dem-30m/tileList.txt",
    "https://stac.astrogeology.usgs.gov/api/collections/mro_ctx_controlled_usgs_dtms",
    "https://example.org/data?page=2&limit=100#section-3",
    "https://example.org/path/with@sign/file.tif",
]

SIGNED_URLS = [
    "https://bucket.s3.amazonaws.com/a.tif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc",
    "https://bucket.s3.amazonaws.com/a.tif?x-amz-signature=abc",
    "https://bucket.s3.amazonaws.com/a.tif?X-Amz-Security-Token=abc",
    "https://bucket.s3.amazonaws.com/a.tif?X-Amz-Credential=AKIAEXAMPLE%2F20261114",
    "https://bucket.s3.amazonaws.com/a.tif?AWSAccessKeyId=AKIAEXAMPLE&Expires=1",
    "https://d111111abcdef8.cloudfront.net/a.tif?Expires=1&Signature=abc&Key-Pair-Id=K1",
    "https://storage.googleapis.com/b/a.tif?X-Goog-Signature=abc",
    "https://account.blob.core.windows.net/c/a.tif?sv=2024-01-01&sig=abc",
    "https://example.org/a.tif?token=abc",
    "https://example.org/a.tif?TOKEN=abc",
    "https://example.org/a.tif?access_token=abc",
    "https://example.org/a.tif?access_key=abc",
    "https://example.org/a.tif?api_key=abc",
    "https://example.org/a.tif?apikey=abc",
    "https://example.org/a.tif?password=abc",
    "https://example.org/a.tif?client_secret=abc",
    "https://example.org/a.tif#access_token=abc",
    "https://user:password@example.org/a.tif",
    "https://user@example.org/a.tif",
    "https://:password@example.org/a.tif",
]

MALFORMED_URLS = [
    "unknown",
    "pgda.gsfc.nasa.gov/data/",
    "/data/raw/dem.tif",
    "https://example.org/a b.tif",
    "https://example.org/a.tif\n",
    "https://example.org/\u00e9.tif",
    "https://",
    "://example.org/a.tif",
]


@pytest.mark.parametrize("url", PUBLIC_URLS)
def test_source_url_accepts_public_urls(url: str) -> None:
    assert not has_url_credentials(url)
    assert_accepted(product(source_url=url))


@pytest.mark.parametrize("url", SIGNED_URLS)
def test_source_url_rejects_signed_urls_and_credentials(url: str) -> None:
    assert has_url_credentials(url)
    assert_rejected(product(source_url=url), ("source_url",))


@pytest.mark.parametrize("url", MALFORMED_URLS)
def test_source_url_rejects_malformed_urls(url: str) -> None:
    assert_rejected(product(source_url=url), ("source_url",))


@pytest.mark.parametrize(
    "parameter", ["X-Amz-Signature=x", "Signature=x", "token=x", "access_key=x", "Expires=x"]
)
def test_each_parameter_the_story_names_is_rejected(parameter: str) -> None:
    url = f"https://example.org/a.tif?{parameter}"
    assert has_url_credentials(url)
    assert_rejected(product(source_url=url), ("source_url",))


_PARAM = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=0, max_size=8)


@given(
    token=st.sampled_from(SIGNED_URL_TOKENS),
    flips=st.lists(st.booleans(), min_size=32, max_size=32),
    prefix=_PARAM,
    separator=st.sampled_from(["?", "?a=1&", "#", "?a=1#b&"]),
)
def test_any_case_of_a_signed_parameter_is_rejected(
    token: str, flips: list[bool], prefix: str, separator: str
) -> None:
    cased = "".join(c.upper() if flip else c.lower() for c, flip in zip(token, flips, strict=False))
    equals = "" if cased.endswith("=") else "="
    url = f"https://example.org/a.tif{separator}{prefix}{cased}{equals}x"
    assert has_url_credentials(url)
    assert_rejected(product(source_url=url), ("source_url",))


# --- sources.json -------------------------------------------------------------------------------


def test_sources_is_a_list_of_product_records() -> None:
    adapter = sources_adapter()
    assert adapter.validate_python([]) == []
    first = product(product_id="a")
    second = product(product_id="b", verification_status="BLOCKED", checksum=None)
    parsed = adapter.validate_json(json.dumps([first, second]))
    assert [record.product_id for record in parsed] == ["a", "b"]
    assert all(isinstance(record, ProductRecord) for record in parsed)


@pytest.mark.parametrize(
    "value",
    [
        {"records": []},
        "[]",
        None,
        [product(source_url="https://example.org/a.tif?token=abc")],
        [product(units="unknown")],
        [{**product(), "extra": 1}],
    ],
    ids=["object", "string", "null", "signed-url", "verified-unknown-units", "extra-field"],
)
def test_sources_rejects_anything_else(value: object) -> None:
    adapter = sources_adapter()
    with pytest.raises(ValidationError):
        adapter.validate_python(value)
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(value))
