"""Tests for the PDS3 label parser, image layout, map projection and row range reads (T1-04).

Every label and image is synthetic, modelled on the Mini-RF south polar mosaic label (PRD 5). An
autouse fixture refuses to resolve any host but the loopback interface, so no test reaches the
network.
"""

from __future__ import annotations

import http.server
import math
import re
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pytest
import rasterio  # type: ignore[import-untyped]
from affine import Affine
from pyproj import CRS, Transformer

from terraluna_pipeline import quality
from terraluna_pipeline.io import (
    Pds3Item,
    Pds3Object,
    Pds3Quantity,
    RangeNotSupportedError,
    RangeReadError,
    RangeResponse,
    decode_pds3_rows,
    fetch_pds3_rows,
    parse_pds3_label,
    pds3_image_layout,
    pds3_polar_stereographic,
    pds3_row_byte_range,
)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to resolve or connect to anything but the loopback interface."""
    real_getaddrinfo = socket.getaddrinfo

    def loopback_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in _LOOPBACK:
            raise socket.gaierror(socket.EAI_NONAME, f"test refused to resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", loopback_only)
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")


# --- A synthetic label modelled on the Mini-RF polar mosaic label -------------------------------

LABEL = """PDS_VERSION_ID                =  PDS3
/* a comment between statements */
NOTE                          =  "Circular Polarization Ratio South polar
mosaic."
^IMAGE                        =  {pointer}
RECORD_TYPE                   =  FIXED_LENGTH
RECORD_BYTES                  =  {record_bytes}
PRODUCT_CREATION_TIME         =  2012-05-08
START_TIME                    =  2009-07-19T12:27:42.785399
CENTER_FREQUENCY              =  2379.99968158 <GHz>
LOOK_DIRECTION\t\t      =  "EAST"

OBJECT = IMAGE
  LINES                      = {lines}
  LINE_SAMPLES               = {samples}
  BANDS                      = 1
  BAND_STORAGE_TYPE          = BAND_SEQUENTIAL
  OFFSET                     = 0.0
  SCALING_FACTOR             = 1.0
  SAMPLE_BITS                = {bits}
  SAMPLE_BIT_MASK            = 2#11111111111111111111111111111111#
  SAMPLE_TYPE                = {sample_type}
  CORE_NULL                  = 16#FF7FFFFB#
  CORE_LOW_REPR_SATURATION   = 16#FF7FFFFC#
  CORE_LOW_INSTR_SATURATION  = 16#FF7FFFFD#
  CORE_HIGH_REPR_SATURATION  = 16#FF7FFFFF#
  CORE_HIGH_INSTR_SATURATION = 16#FF7FFFFE#
END_OBJECT = IMAGE

OBJECT = IMAGE_MAP_PROJECTION
  ^DATA_SET_MAP_PROJECTION     = "DSMAP.CAT"
  MAP_PROJECTION_TYPE          = "POLAR STEREOGRAPHIC"
  A_AXIS_RADIUS                = 1737.4 <km>
  B_AXIS_RADIUS                = 1737.4 <km>
  C_AXIS_RADIUS                = 1737.4 <km>
  COORDINATE_SYSTEM_NAME       = PLANETOCENTRIC
  POSITIVE_LONGITUDE_DIRECTION = EAST
  CENTER_LATITUDE              = {center_latitude} <deg>
  CENTER_LONGITUDE             = {center_longitude} <deg>
  MAP_PROJECTION_ROTATION      = 0.0 <deg>
  MAP_RESOLUTION               = 256.0 <pix/deg>
  MAP_SCALE                    = {map_scale} <km/pix>
  LINE_PROJECTION_OFFSET       = {line_offset} <pixel>
  SAMPLE_PROJECTION_OFFSET     = {sample_offset} <pixel>
END_OBJECT = IMAGE_MAP_PROJECTION
END
"""

DEFAULTS: dict[str, Any] = {
    "pointer": '"SYN.IMG"',
    "record_bytes": 32,
    "lines": 6,
    "samples": 8,
    "bits": 32,
    "sample_type": "PC_REAL",
    "center_latitude": "-90.0",
    "center_longitude": "0.0",
    "map_scale": "0.1",
    "line_offset": "3.75",
    "sample_offset": "4.25",
}


def make_label(**overrides: Any) -> str:
    return LABEL.format(**{**DEFAULTS, **overrides})


def modified(text: str, old: str, new: str) -> str:
    assert old in text, f"{old!r} is not in the label"
    return text.replace(old, new, 1)


def layout_of(**overrides: Any) -> Any:
    return pds3_image_layout(parse_pds3_label(make_label(**overrides)))


def projection_of(**overrides: Any) -> Any:
    return pds3_polar_stereographic(parse_pds3_label(make_label(**overrides)))


# --- Parsing: value forms -----------------------------------------------------------------------


def only_item(text: str) -> Pds3Item:
    root = parse_pds3_label(text + "\nEND\n")
    assert len(root.items) == 1
    return root.items[0]


@pytest.mark.parametrize(
    ("written", "value"),
    [
        ('"FILE.IMG"', "FILE.IMG"),
        ('""', ""),
        ("'a symbol'", "a symbol"),
        ("PC_REAL", "PC_REAL"),
        ("2009-07-19T12:27:42.785399", "2009-07-19T12:27:42.785399"),
        ("2012-05-08", "2012-05-08"),
        ("01:32:11.7200", "01:32:11.7200"),
        ("10346", 10346),
        ("-7", -7),
        ("+7", 7),
        ("0.0", 0.0),
        ("-3.5", -3.5),
        ("1.5E3", 1500.0),
        ("2e-2", 0.02),
        (".5", 0.5),
        ("5.", 5.0),
        ("16#FF7FFFFB#", 0xFF7FFFFB),
        ("2#1011#", 11),
        ("8#17#", 15),
        ("1737.4 <km>", Pds3Quantity(1737.4, "km")),
        ("512 <BYTES>", Pds3Quantity(512, "BYTES")),
        ("256.0 <pix/deg>", Pds3Quantity(256.0, "pix/deg")),
        ("-90.0<deg>", Pds3Quantity(-90.0, "deg")),
        ("(1, 2.5, ABC)", (1, 2.5, "ABC")),
        ("()", ()),
        ('{"A", "B", C}', ("A", "B", "C")),
        ('("FILE.IMG", 3)', ("FILE.IMG", 3)),
        ('("FILE.IMG", 5 <BYTES>)', ("FILE.IMG", Pds3Quantity(5, "BYTES"))),
        ("(1.0 <m>, (2, 3))", (Pds3Quantity(1.0, "m"), (2, 3))),
    ],
)
def test_value_forms(written: str, value: Any) -> None:
    item = only_item(f"KEY = {written}")
    assert item.key == "KEY"
    assert item.value == value
    assert type(item.value) is type(value)
    assert item.raw == written


def test_sequences_and_sets_may_span_lines_and_carry_units() -> None:
    item = only_item('LIST = (1 <m>,\n   2 <m>,  /* a comment, with a comma */\n  "x y"\n)')
    assert item.value == (Pds3Quantity(1, "m"), Pds3Quantity(2, "m"), "x y")
    assert item.raw.startswith("(1 <m>,") and item.raw.endswith(")")
    assert "\n" in item.raw
    assert only_item("SET = {\n A,\n B\n}").value == ("A", "B")


def test_a_multi_line_string_keeps_its_content_verbatim_and_text_collapses_whitespace() -> None:
    item = only_item('NOTE = "first line\n   second\tline\nthird /* not a comment */"')
    assert item.value == "first line\n   second\tline\nthird /* not a comment */"
    assert item.raw == '"first line\n   second\tline\nthird /* not a comment */"'
    assert item.text() == "first line second line third /* not a comment */"


def test_text_refuses_a_value_that_is_not_a_string() -> None:
    with pytest.raises(TypeError):
        only_item("LINES = 5").text()
    with pytest.raises(TypeError):
        only_item("LIST = (1, 2)").text()


def test_keys_may_start_with_a_caret_and_tabs_may_surround_the_equals_sign() -> None:
    root = parse_pds3_label('^IMAGE\t=\t"A.IMG"\nLOOK_DIRECTION\t\t      =  "EAST"\nEND\n')
    assert [item.key for item in root.items] == ["^IMAGE", "LOOK_DIRECTION"]
    assert root.require("LOOK_DIRECTION").value == "EAST"


def test_comments_are_ignored_wherever_whitespace_is_allowed() -> None:
    text = (
        "/* leading\n comment */\n"
        "A = 1 /* trailing */\n"
        "B /* before equals */ = /* before value */ 2\n"
        "/* a */ /* b */\n"
        "END\n"
    )
    root = parse_pds3_label(text)
    assert [(i.key, i.value) for i in root.items] == [("A", 1), ("B", 2)]
    assert root.require("A").raw == "1"


def test_the_raw_text_is_exactly_what_was_written_stripped() -> None:
    root = parse_pds3_label(
        "A =   16#FF7FFFFB#   \nB =\t1.50 <km>\nC = 2012-05-08 \nD = 1.0E3\nEND\n"
    )
    assert [item.raw for item in root.items] == ["16#FF7FFFFB#", "1.50 <km>", "2012-05-08", "1.0E3"]


# --- Parsing: structure -------------------------------------------------------------------------


def test_nested_objects_and_groups() -> None:
    text = (
        "A = 1\n"
        "OBJECT = OUTER\n"
        "  B = 2\n"
        "  GROUP = INNER\n"
        "    C = 3\n"
        "  END_GROUP = INNER\n"
        "  OBJECT = LEAF\n"
        "    D = 4\n"
        "  END_OBJECT\n"
        "END_OBJECT = OUTER\n"
        "GROUP = TOP\n"
        "  E = 5\n"
        "END_GROUP\n"
        "END\n"
    )
    root = parse_pds3_label(text)
    assert root.kind == "ROOT" and root.name == ""
    assert [i.key for i in root.items] == ["A"]
    assert [(c.kind, c.name) for c in root.children] == [("OBJECT", "OUTER"), ("GROUP", "TOP")]
    outer = root.child("OUTER")
    assert [i.key for i in outer.items] == ["B"]
    assert [(c.kind, c.name) for c in outer.children] == [("GROUP", "INNER"), ("OBJECT", "LEAF")]
    assert outer.child("INNER").require("C").value == 3
    assert outer.child("LEAF").require("D").value == 4
    assert root.child("TOP").require("E").value == 5
    assert root.get("B") is None


def test_parsing_stops_at_the_end_line() -> None:
    root = parse_pds3_label("A = 1\nEND\nthis is not parsed = = \n")
    assert [i.key for i in root.items] == ["A"]
    assert [i.key for i in parse_pds3_label("A = 1\nEND").items] == ["A"]
    assert [i.key for i in parse_pds3_label("A = 1\n  END  /* done */\n").items] == ["A"]


def test_the_object_names_may_be_quoted_or_bare_and_keys_keep_their_case_as_written() -> None:
    root = parse_pds3_label(
        'Mixed_Case = 1\nOBJECT = "QUOTED"\nX = 1\nEND_OBJECT = "QUOTED"\nEND\n'
    )
    assert root.items[0].key == "Mixed_Case"
    assert root.child("QUOTED").name == "QUOTED"


def test_get_require_and_child_errors() -> None:
    root = parse_pds3_label(
        "A = 1\nA = 2\nB = 3\nOBJECT = O\nEND_OBJECT\nOBJECT = O\nEND_OBJECT\n"
        "OBJECT = P\nQ = 1\nEND_OBJECT\nEND\n"
    )
    assert root.get("B") == Pds3Item(key="B", value=3, raw="3")
    assert root.get("MISSING") is None
    with pytest.raises(ValueError, match="A"):
        root.get("A")
    with pytest.raises(ValueError, match="MISSING"):
        root.require("MISSING")
    with pytest.raises(ValueError, match="P") as missing_in_p:
        root.child("P").require("MISSING")
    assert "MISSING" in str(missing_in_p.value)
    with pytest.raises(ValueError, match="O"):
        root.child("O")
    with pytest.raises(ValueError, match="NOPE"):
        root.child("NOPE")


def test_the_full_synthetic_label_parses() -> None:
    root = parse_pds3_label(make_label())
    assert root.require("NOTE").text() == "Circular Polarization Ratio South polar mosaic."
    assert root.require("CENTER_FREQUENCY").value == Pds3Quantity(2379.99968158, "GHz")
    assert root.require("LOOK_DIRECTION").value == "EAST"
    assert root.require("^IMAGE").value == "SYN.IMG"
    image = root.child("IMAGE")
    assert image.require("SAMPLE_BIT_MASK").value == 2**32 - 1
    assert image.require("SAMPLE_BIT_MASK").raw == "2#" + "1" * 32 + "#"
    assert root.child("IMAGE_MAP_PROJECTION").require("MAP_SCALE").value == Pds3Quantity(
        0.1, "km/pix"
    )


# --- Parsing: errors carry the line number ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "line", "what"),
    [
        ('A = 1\nB = "never closed\nC = 2\nEND\n', 2, "string"),
        ("A = 1\nB = 'never closed\nEND\n", 2, "string"),
        ("A = 1\n\nB = (1, 2,\n3\nEND\n", 3, "sequence"),
        ("A = 1\nB = {1, 2\nEND\n", 2, "set"),
        ("A = 1\nOBJECT = X\nB = 2\nEND_OBJECT = Y\nEND\n", 4, "END_OBJECT"),
        ("OBJECT = X\nB = 2\nEND_GROUP = X\nEND\n", 3, "END_GROUP"),
        ("GROUP = X\nEND_OBJECT = X\nEND\n", 2, "END_OBJECT"),
        ("A = 1\nEND_OBJECT = X\nEND\n", 2, "END_OBJECT"),
        ("A = 1\nOBJECT = X\nB = 2\nEND\n", 4, "unclosed"),
        ("A = 1\nOBJECT = X\nGROUP = Y\nB = 2\nEND\n", 5, "unclosed"),
        ("A = 1\nB = 2\n", 2, "END"),
        ("A = 1\nOBJECT = X\nB = 2\n", 3, "unclosed"),
        ("", 1, "END"),
        ("A = 1\nJUNK\nB = 2\nEND\n", 2, "="),
        ("A = 1\nJUNK /* c */ 5\nEND\n", 2, "="),
        ("A = 1\nB = 1 2\nEND\n", 2, "unexpected"),
        ("A = 1\nB =\n", 3, "value"),
        ("A = 1\n/* never closed\nEND\n", 2, "comment"),
        ("A = 1\nB = 3 <km\nEND\n", 2, "unit"),
        ("A = 1\nB = ABC <km>\nEND\n", 2, "unit"),
        ("A = 1\nB = (1 2)\nEND\n", 2, "sequence"),
        ("A = 1\nOBJECT\nEND_OBJECT\nEND\n", 2, "="),
        ("A = 1\nB = 16#GG#\nEND\n", 2, "based"),
        ("A = 1\nB = 2#102#\nEND\n", 2, "based"),
    ],
)
def test_parse_errors_name_the_line(text: str, line: int, what: str) -> None:
    with pytest.raises(ValueError, match=rf"line {line}\b") as error:
        parse_pds3_label(text)
    assert what in str(error.value)


# --- The image layout ---------------------------------------------------------------------------


def test_the_synthetic_label_gives_the_expected_layout() -> None:
    layout = layout_of()
    assert layout.file_name == "SYN.IMG"
    assert layout.offset_bytes == 0
    assert (layout.lines, layout.line_samples, layout.bands) == (6, 8, 1)
    assert layout.sample_type == "PC_REAL"
    assert layout.sample_bits == 32
    assert layout.dtype == np.dtype("<f4")
    assert layout.line_bytes == 32
    assert layout.image_bytes == 192
    assert layout.record_bytes == 32
    assert layout.scaling_factor == 1.0 and layout.value_offset == 0.0
    assert (
        set(layout.special_values)
        == set(layout.special_raw)
        == {
            "CORE_NULL",
            "CORE_LOW_REPR_SATURATION",
            "CORE_LOW_INSTR_SATURATION",
            "CORE_HIGH_REPR_SATURATION",
            "CORE_HIGH_INSTR_SATURATION",
        }
    )
    assert layout.special_values["CORE_NULL"] == quality.ISIS_NULL_REAL4
    assert math.isclose(layout.special_values["CORE_NULL"], -3.4028227e38, rel_tol=1e-7)
    assert layout.special_raw["CORE_NULL"] == "16#FF7FFFFB#"
    saturation = [
        layout.special_values[key]
        for key in (
            "CORE_LOW_REPR_SATURATION",
            "CORE_LOW_INSTR_SATURATION",
            "CORE_HIGH_INSTR_SATURATION",
            "CORE_HIGH_REPR_SATURATION",
        )
    ]
    assert tuple(saturation) == quality.ISIS_SATURATION_REAL4
    assert layout.special_raw["CORE_HIGH_REPR_SATURATION"] == "16#FF7FFFFF#"
    assert isinstance(layout.special_values["CORE_NULL"], float)


def test_the_real_mosaic_sized_layout_is_computed_without_overflow() -> None:
    layout = layout_of(lines=10346, samples=10346, record_bytes=41384)
    assert layout.line_bytes == 41384
    assert layout.image_bytes == 10346 * 41384


def test_scaling_and_offset_are_reported_and_defaults_apply_only_when_absent() -> None:
    text = make_label()
    changed = modified(
        modified(text, "OFFSET                     = 0.0", "OFFSET = 5.5"),
        "SCALING_FACTOR             = 1.0",
        "SCALING_FACTOR = 0.25",
    )
    layout = pds3_image_layout(parse_pds3_label(changed))
    assert (layout.scaling_factor, layout.value_offset) == (0.25, 5.5)
    absent = modified(
        modified(text, "  OFFSET                     = 0.0\n", ""),
        "  SCALING_FACTOR             = 1.0\n",
        "",
    )
    layout = pds3_image_layout(parse_pds3_label(absent))
    assert (layout.scaling_factor, layout.value_offset) == (1.0, 0.0)


def test_special_values_written_in_decimal_are_taken_as_written() -> None:
    text = modified(make_label(), "CORE_NULL                  = 16#FF7FFFFB#", "CORE_NULL = -32768")
    layout = pds3_image_layout(parse_pds3_label(text))
    assert layout.special_values["CORE_NULL"] == -32768.0
    assert layout.special_raw["CORE_NULL"] == "-32768"


def test_special_values_are_bit_patterns_in_the_images_own_width() -> None:
    big = layout_of(sample_type="IEEE_REAL")
    assert big.dtype == np.dtype(">f4")
    assert big.special_values["CORE_NULL"] == quality.ISIS_NULL_REAL4
    wide = re.sub(r"16#FF7FFFF[0-9A-F]#", "16#FFEFFFFFFFFFFFFF#", make_label(bits=64))
    layout = pds3_image_layout(parse_pds3_label(wide))
    assert layout.dtype == np.dtype("<f8")
    assert layout.special_values["CORE_NULL"] == -np.finfo(np.float64).max
    signed = re.sub(
        r"16#FF7FFFF[0-9A-F]#", "16#8000#", make_label(sample_type="MSB_INTEGER", bits=16)
    )
    assert pds3_image_layout(parse_pds3_label(signed)).special_values["CORE_NULL"] == -32768.0
    too_wide = modified(make_label(), "16#FF7FFFFB#", "16#1FF7FFFFB#")
    with pytest.raises(ValueError, match="CORE_NULL"):
        pds3_image_layout(parse_pds3_label(too_wide))


def test_a_special_value_that_is_not_a_number_is_refused() -> None:
    text = modified(make_label(), "CORE_NULL                  = 16#FF7FFFFB#", 'CORE_NULL = "N/A"')
    with pytest.raises(ValueError, match="CORE_NULL"):
        pds3_image_layout(parse_pds3_label(text))


def test_a_label_without_special_values_gives_empty_dictionaries() -> None:
    text = make_label()
    start = text.index("  CORE_NULL")
    end = text.index("END_OBJECT = IMAGE\n")
    layout = pds3_image_layout(parse_pds3_label(text[:start] + text[end:]))
    assert layout.special_values == {} and layout.special_raw == {}


def test_a_label_without_record_bytes_reports_none() -> None:
    text = modified(make_label(), "RECORD_BYTES                  =  32\n", "")
    assert pds3_image_layout(parse_pds3_label(text)).record_bytes is None


@pytest.mark.parametrize(
    ("pointer", "record_bytes", "offset"),
    [
        ('"SYN.IMG"', 32, 0),
        ('("SYN.IMG", 1)', 32, 0),
        ('("SYN.IMG", 3)', 32, 64),
        ('("SYN.IMG", 5 <BYTES>)', 32, 4),
        ('("SYN.IMG", 1 <BYTES>)', 32, 0),
    ],
)
def test_pointer_forms(pointer: str, record_bytes: int, offset: int) -> None:
    layout = layout_of(pointer=pointer, record_bytes=record_bytes)
    assert layout.file_name == "SYN.IMG"
    assert layout.offset_bytes == offset


@pytest.mark.parametrize(
    ("pointer", "match"),
    [
        ("3", "attached labels are not supported"),
        ("3 <BYTES>", "attached labels are not supported"),
        ('("SYN.IMG", 0)', "pointer"),
        ('("SYN.IMG", 0 <BYTES>)', "pointer"),
        ('("SYN.IMG", 2 <KM>)', "pointer"),
        ('("SYN.IMG", 2.5)', "pointer"),
        ('("SYN.IMG", 2, 3)', "pointer"),
        ("(1, 2)", "pointer"),
    ],
)
def test_pointer_refusals(pointer: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        layout_of(pointer=pointer)


def test_a_record_pointer_needs_record_bytes_and_the_pointer_must_exist() -> None:
    text = modified(
        make_label(pointer='("SYN.IMG", 3)'), "RECORD_BYTES                  =  32\n", ""
    )
    with pytest.raises(ValueError, match="RECORD_BYTES"):
        pds3_image_layout(parse_pds3_label(text))
    with pytest.raises(ValueError, match=r"\^IMAGE"):
        pds3_image_layout(parse_pds3_label(modified(make_label(), "^IMAGE", "^OTHER")))


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("BANDS                      = 1", "BANDS = 3", "BANDS"),
        ("SAMPLE_BITS                = 32", "SAMPLE_BITS = 16", "SAMPLE_BITS"),
        ("SAMPLE_TYPE                = PC_REAL", "SAMPLE_TYPE = VAX_REAL", "SAMPLE_TYPE"),
        ("SAMPLE_TYPE                = PC_REAL", "SAMPLE_TYPE = LSB_IEEE_REAL", "SAMPLE_TYPE"),
        ("SAMPLE_TYPE                = PC_REAL", "SAMPLE_TYPE = BOGUS", "SAMPLE_TYPE"),
        ("SAMPLE_TYPE                = PC_REAL", "SAMPLE_TYPE = pc_real", "SAMPLE_TYPE"),
        ("BAND_STORAGE_TYPE          = BAND_SEQUENTIAL", "LINE_PREFIX_BYTES = 4", "LINE_PREFIX"),
        ("BAND_STORAGE_TYPE          = BAND_SEQUENTIAL", "LINE_SUFFIX_BYTES = 4", "LINE_SUFFIX"),
        ("LINES                      = 6", "LINES = 0", "LINES"),
        ("LINES                      = 6", "LINES = 6.5", "LINES"),
        ("LINE_SAMPLES               = 8", "LINE_SAMPLES = -8", "LINE_SAMPLES"),
        ("SAMPLE_BITS                = 32\n", "", "SAMPLE_BITS"),
        ("OFFSET                     = 0.0", 'OFFSET = "x"', "OFFSET"),
    ],
)
def test_layout_refusals(old: str, new: str, match: str) -> None:
    text = modified(make_label(), old, new)
    with pytest.raises(ValueError, match=match):
        pds3_image_layout(parse_pds3_label(text))


def test_zero_prefix_and_suffix_bytes_are_accepted() -> None:
    text = modified(
        make_label(),
        "BAND_STORAGE_TYPE          = BAND_SEQUENTIAL",
        "LINE_PREFIX_BYTES = 0\nLINE_SUFFIX_BYTES = 0 <BYTES>",
    )
    assert pds3_image_layout(parse_pds3_label(text)).line_bytes == 32


def test_a_label_without_an_image_object_is_refused() -> None:
    with pytest.raises(ValueError, match="IMAGE"):
        pds3_image_layout(parse_pds3_label("A = 1\nEND\n"))


# --- SAMPLE_TYPE names (PDS3 Standards Reference, Appendix C) -----------------------------------

BIG, LITTLE = ">", "<"
TYPE_CASES: list[tuple[str, str, int]] = [
    *((name, f"{LITTLE}f", bits) for name in ("PC_REAL",) for bits in (32, 64)),
    *(
        (name, f"{BIG}f", bits)
        for name in ("IEEE_REAL", "FLOAT", "REAL", "MAC_REAL", "SUN_REAL")
        for bits in (32, 64)
    ),
    *(
        (name, f"{LITTLE}i", bits)
        for name in ("LSB_INTEGER", "PC_INTEGER", "VAX_INTEGER")
        for bits in (8, 16, 32)
    ),
    *(
        (name, f"{BIG}i", bits)
        for name in ("MSB_INTEGER", "INTEGER", "MAC_INTEGER", "SUN_INTEGER")
        for bits in (8, 16, 32)
    ),
    *(
        (name, f"{LITTLE}u", bits)
        for name in ("LSB_UNSIGNED_INTEGER", "PC_UNSIGNED_INTEGER", "VAX_UNSIGNED_INTEGER")
        for bits in (8, 16, 32)
    ),
    *(
        (name, f"{BIG}u", bits)
        for name in (
            "MSB_UNSIGNED_INTEGER",
            "UNSIGNED_INTEGER",
            "MAC_UNSIGNED_INTEGER",
            "SUN_UNSIGNED_INTEGER",
        )
        for bits in (8, 16, 32)
    ),
]


def sample_array(code: str, bits: int, shape: tuple[int, int]) -> npt.NDArray[Any]:
    dtype = np.dtype(f"{code}{bits // 8}")
    rng = np.random.default_rng(bits)
    if dtype.kind == "f":
        values = rng.normal(0.0, 1000.0, shape)
        values[0, 0] = np.finfo(dtype).max
        values[0, 1] = -np.finfo(dtype).tiny
        return values.astype(dtype)
    info = np.iinfo(dtype)
    integers = rng.integers(info.min, info.max, shape, endpoint=True)
    integers[0, 0], integers[0, 1] = info.min, info.max
    return integers.astype(dtype)


@pytest.mark.parametrize(("name", "code", "bits"), TYPE_CASES)
def test_sample_types_round_trip_bit_for_bit(name: str, code: str, bits: int) -> None:
    label = make_label(sample_type=name, bits=bits, samples=5, lines=3)
    layout = pds3_image_layout(parse_pds3_label(re.sub(r"  CORE_.*\n", "", label)))
    expected = sample_array(code, bits, (3, 5))
    assert layout.dtype == expected.dtype
    assert layout.sample_type == name and layout.sample_bits == bits
    assert layout.line_bytes == 5 * bits // 8 and layout.image_bytes == 3 * 5 * bits // 8
    decoded = decode_pds3_rows(expected.tobytes(), layout, 3)
    assert decoded.dtype == layout.dtype
    assert decoded.shape == (3, 5)
    assert decoded.tobytes() == expected.tobytes()
    assert decoded.flags.writeable
    # The same bytes are read as a different value when the byte order is wrong.
    swapped = np.frombuffer(expected.tobytes(), dtype=expected.dtype.newbyteorder("S"))
    if bits > 8:
        assert swapped.tobytes() == expected.tobytes()
        assert not np.array_equal(decoded.ravel(), swapped, equal_nan=True)


@pytest.mark.parametrize(
    ("name", "bits"),
    [
        ("PC_REAL", 16),
        ("PC_REAL", 80),
        ("IEEE_REAL", 16),
        ("IEEE_REAL", 80),
        ("MSB_INTEGER", 64),
        ("LSB_UNSIGNED_INTEGER", 12),
        ("VAX_REAL", 32),
        ("VAXG_REAL", 64),
        ("LSB_IEEE_REAL", 32),
        ("MSB_IEEE_REAL", 32),
        ("UNKNOWN_TYPE", 32),
    ],
)
def test_sample_type_refusals(name: str, bits: int) -> None:
    with pytest.raises(ValueError, match="SAMPLE_"):
        layout_of(sample_type=name, bits=bits)


def test_row_byte_range_is_inclusive_and_offset_aware() -> None:
    layout = layout_of(pointer='("SYN.IMG", 3)')  # offset 64, 32 bytes per line, 6 lines
    assert pds3_row_byte_range(layout, 0, 0) == (64, 95)
    assert pds3_row_byte_range(layout, 2, 4) == (128, 223)
    assert pds3_row_byte_range(layout, 0, 5) == (64, 64 + 192 - 1)
    for first, last in ((-1, 2), (0, 6), (3, 2), (6, 6)):
        with pytest.raises(ValueError, match="row"):
            pds3_row_byte_range(layout, first, last)


def test_decode_refuses_a_wrong_length() -> None:
    layout = layout_of()
    with pytest.raises(ValueError, match="bytes"):
        decode_pds3_rows(bytes(63), layout, 2)
    with pytest.raises(ValueError, match="bytes"):
        decode_pds3_rows(bytes(65), layout, 2)
    with pytest.raises(ValueError, match="row_count"):
        decode_pds3_rows(b"", layout, 0)


def test_decode_returns_stored_values_without_scaling_or_masking() -> None:
    text = modified(make_label(), "SCALING_FACTOR             = 1.0", "SCALING_FACTOR = 2.0")
    layout = pds3_image_layout(parse_pds3_label(text))
    stored = np.full((1, 8), quality.ISIS_NULL_REAL4, dtype="<f4")
    stored[0, 3] = 7.0
    decoded = decode_pds3_rows(stored.tobytes(), layout, 1)
    assert decoded[0, 3] == 7.0
    assert decoded[0, 0] == quality.ISIS_NULL_REAL4


# --- The map projection -------------------------------------------------------------------------


def test_the_transform_places_the_projection_offsets_at_pixel_centres() -> None:
    projection = projection_of(
        lines=6, samples=6, line_offset="3.5", sample_offset="3.5", map_scale="0.1"
    )
    scale = 100.0
    assert projection.map_scale_m == pytest.approx(scale, rel=1e-12)
    assert projection.transform.c == pytest.approx(-3 * scale, abs=1e-9)
    assert projection.transform.f == pytest.approx(3 * scale, abs=1e-9)
    assert projection.transform.a == pytest.approx(scale)
    assert projection.transform.e == pytest.approx(-scale)
    assert projection.transform.b == projection.transform.d == 0.0
    assert (projection.lines, projection.line_samples) == (6, 6)
    # The pole is the shared corner of the four central pixels.
    assert projection.transform @ (3.0, 3.0) == pytest.approx((0.0, 0.0), abs=1e-9)


def test_the_transform_agrees_with_the_archive_pixel_formula() -> None:
    projection = projection_of(
        lines=6, samples=8, line_offset="3.75", sample_offset="4.25", map_scale="0.2"
    )
    scale, sample0, line0 = 200.0, 4.25, 3.75
    assert isinstance(projection.transform, Affine)
    for sample, line in ((1, 1), (8, 6), (4, 3), (2, 5)):
        x = (sample - sample0) * scale
        y = (line0 - line) * scale
        got = projection.transform @ (sample - 0.5, line - 0.5)
        assert got == pytest.approx((x, y), abs=1e-9)


def test_the_real_mosaic_numbers_give_the_documented_origin() -> None:
    projection = projection_of(
        lines=10346,
        samples=10346,
        line_offset="5173.5000000002",
        sample_offset="5173.5000000002",
        map_scale="0.11845058759433",
        center_latitude="-90.0",
    )
    scale = 118.45058759433
    assert projection.map_scale_m == pytest.approx(scale, rel=1e-12)
    assert projection.transform.c == pytest.approx(-5173.0000000002 * scale, rel=1e-12)
    assert projection.transform.f == pytest.approx(5173.0000000002 * scale, rel=1e-12)
    assert projection.map_resolution_ppd == 256.0
    assert projection.radius_m == pytest.approx(1737400.0)
    assert projection.center_latitude_deg == -90.0 and projection.center_longitude_deg == 0.0
    assert projection.line_projection_offset == 5173.5000000002
    assert projection.sample_projection_offset == 5173.5000000002


@pytest.mark.filterwarnings("ignore:You will likely lose:UserWarning")
def test_the_crs_is_the_south_or_north_polar_stereographic_sphere() -> None:
    south = projection_of()
    assert isinstance(south.crs, CRS)
    parameters = south.crs.to_dict()
    assert parameters["proj"] == "stere"
    assert (parameters["lat_0"], parameters["lat_ts"], parameters["lon_0"]) == (-90, -90, 0)
    assert parameters["R"] == 1737400
    assert south.crs.is_projected and south.crs.axis_info[0].unit_name == "metre"
    north = projection_of(center_latitude="90.0", center_longitude="45.0")
    assert (north.crs.to_dict()["lat_0"], north.crs.to_dict()["lon_0"]) == (90, 45)


def dsmap_south(
    lat_deg: float, lon_deg: float, lon0_deg: float, radius: float
) -> tuple[float, float]:
    """DSMAP.CAT south polar stereographic (true scale at the pole), written independently."""
    k = 2 * radius * math.tan(math.pi / 4 + math.radians(lat_deg) / 2)
    dlon = math.radians(lon_deg - lon0_deg)
    return k * math.sin(dlon), k * math.cos(dlon)


def dsmap_north(
    lat_deg: float, lon_deg: float, lon0_deg: float, radius: float
) -> tuple[float, float]:
    k = 2 * radius * math.tan(math.pi / 4 - math.radians(lat_deg) / 2)
    dlon = math.radians(lon_deg - lon0_deg)
    return k * math.sin(dlon), -k * math.cos(dlon)


@pytest.mark.parametrize(
    ("center_latitude", "lon0", "points", "formula"),
    [
        (
            "-90.0",
            0.0,
            [(-89.0, 0.0), (-80.0, 90.0), (-75.0, 200.0), (-70.0, 359.0), (-85.5, 45.0)],
            dsmap_south,
        ),
        (
            "-90.0",
            30.0,
            [(-89.9, 31.0), (-72.0, 10.0), (-70.0, 300.0)],
            dsmap_south,
        ),
        (
            "90.0",
            0.0,
            [(89.0, 0.0), (80.0, 90.0), (75.0, 200.0), (70.0, 359.0)],
            dsmap_north,
        ),
        ("90.0", 20.0, [(88.0, 21.0), (71.0, 250.0)], dsmap_north),
    ],
)
def test_pyproj_agrees_with_the_dsmap_polar_formulas(
    center_latitude: str, lon0: float, points: list[tuple[float, float]], formula: Any
) -> None:
    projection = projection_of(center_latitude=center_latitude, center_longitude=str(lon0))
    radius = 1737400.0
    sphere = CRS.from_dict({"proj": "longlat", "R": radius, "no_defs": True})
    transformer = Transformer.from_crs(sphere, projection.crs, always_xy=True)
    for lat, lon in points:
        x, y = transformer.transform(lon, lat)
        want_x, want_y = formula(lat, lon, lon0, radius)
        assert x == pytest.approx(want_x, abs=1e-6)
        assert y == pytest.approx(want_y, abs=1e-6)


def test_pixel_window_covers_the_bounds_with_whole_pixels() -> None:
    projection = projection_of(
        lines=6, samples=8, line_offset="3.5", sample_offset="4.5", map_scale="1.0"
    )
    # Scale 1000 m, c = -4000 m and f = +3000 m: columns span -4000..4000 m, rows 3000..-3000 m.
    assert projection.transform.c == pytest.approx(-4000.0)
    assert projection.transform.f == pytest.approx(3000.0)
    assert projection.pixel_window(-1500.0, -1500.0, 1500.0, 1500.0) == (2, 1, 4, 4)
    assert projection.pixel_window(-4000.0, -3000.0, 4000.0, 3000.0) == (0, 0, 8, 6)
    assert projection.pixel_window(-4000.0, 0.0, -1000.0, 3000.0) == (0, 0, 3, 3)
    assert projection.pixel_window(-3999.5, -2999.5, 3999.5, 2999.5) == (0, 0, 8, 6)
    assert projection.pixel_window(0.5, 0.5, 1.5, 1.5) == (4, 2, 1, 1)
    assert projection.pixel_window(3000.0, -3000.0, 4000.0, -2000.0) == (7, 5, 1, 1)


def test_pixel_window_is_the_exact_inverse_of_the_transform() -> None:
    projection = projection_of(
        lines=6, samples=8, line_offset="3.75", sample_offset="4.25", map_scale="0.1"
    )
    transform = projection.transform
    for col_off, row_off, width, height in ((0, 0, 8, 6), (1, 2, 3, 2), (7, 5, 1, 1), (2, 0, 5, 6)):
        left, top = transform @ (col_off, row_off)
        right, bottom = transform @ (col_off + width, row_off + height)
        assert projection.pixel_window(left, bottom, right, top) == (
            col_off,
            row_off,
            width,
            height,
        )
        # Growing the bounds by a fraction of a pixel adds one whole pixel on each grown side.
        inner = projection.pixel_window(left + 1.0, bottom + 1.0, right - 1.0, top - 1.0)
        assert inner == (col_off, row_off, width, height)


def test_pixel_window_refuses_empty_and_outside_bounds_and_never_clips() -> None:
    projection = projection_of(
        lines=6, samples=8, line_offset="3.5", sample_offset="4.5", map_scale="1.0"
    )
    for bounds in (
        (-4001.0, -3000.0, 4000.0, 3000.0),
        (-4000.0, -3000.0, 4001.0, 3000.0),
        (-4000.0, -3001.0, 4000.0, 3000.0),
        (-4000.0, -3000.0, 4000.0, 3001.0),
        (5000.0, 0.0, 6000.0, 1000.0),
    ):
        with pytest.raises(ValueError, match="image"):
            projection.pixel_window(*bounds)
    for bounds in (
        (0.0, 0.0, 0.0, 1000.0),
        (0.0, 0.0, 1000.0, 0.0),
        (1000.0, 0.0, 0.0, 1000.0),
        (0.0, 1000.0, 1000.0, 0.0),
        (float("nan"), 0.0, 1.0, 1.0),
        (0.0, 0.0, float("inf"), 1.0),
    ):
        with pytest.raises(ValueError):
            projection.pixel_window(*bounds)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("POLAR STEREOGRAPHIC", "SINUSOIDAL", "MAP_PROJECTION_TYPE"),
        ("POSITIVE_LONGITUDE_DIRECTION = EAST", "POSITIVE_LONGITUDE_DIRECTION = WEST", "POSITIVE"),
        (
            "COORDINATE_SYSTEM_NAME       = PLANETOCENTRIC",
            "COORDINATE_SYSTEM_NAME = PLANETOGRAPHIC",
            "COORDINATE",
        ),
        ("COORDINATE_SYSTEM_NAME       = PLANETOCENTRIC\n", "", "COORDINATE"),
        (
            "MAP_PROJECTION_ROTATION      = 0.0 <deg>",
            "MAP_PROJECTION_ROTATION = 15.0 <deg>",
            "ROTATION",
        ),
        ("B_AXIS_RADIUS                = 1737.4 <km>", "B_AXIS_RADIUS = 1737.5 <km>", "RADIUS"),
        ("C_AXIS_RADIUS                = 1737.4 <km>", "C_AXIS_RADIUS = 1736.0 <km>", "RADIUS"),
        (
            "A_AXIS_RADIUS                = 1737.4 <km>",
            "A_AXIS_RADIUS = 1737.4 <furlong>",
            "A_AXIS_RADIUS",
        ),
        ("MAP_SCALE                    = 0.1 <km/pix>", "MAP_SCALE = 0.1 <deg/pix>", "MAP_SCALE"),
        ("MAP_SCALE                    = 0.1 <km/pix>", "MAP_SCALE = 0.1", "MAP_SCALE"),
        ("MAP_SCALE                    = 0.1 <km/pix>", "MAP_SCALE = -0.1 <km/pix>", "MAP_SCALE"),
        (
            "CENTER_LATITUDE              = -90.0 <deg>",
            "CENTER_LATITUDE = -89.0 <deg>",
            "CENTER_LATITUDE",
        ),
        (
            "MAP_RESOLUTION               = 256.0 <pix/deg>",
            "MAP_RESOLUTION = 256.0 <km>",
            "MAP_RESOLUTION",
        ),
        (
            "LINE_PROJECTION_OFFSET       = 3.75 <pixel>",
            'LINE_PROJECTION_OFFSET = "x"',
            "LINE_PROJECTION_OFFSET",
        ),
        ("SAMPLE_PROJECTION_OFFSET     = 4.25 <pixel>\n", "", "SAMPLE_PROJECTION_OFFSET"),
    ],
)
def test_projection_refusals(old: str, new: str, match: str) -> None:
    text = modified(make_label(), old, new)
    with pytest.raises(ValueError, match=match):
        pds3_polar_stereographic(parse_pds3_label(text))


def test_a_label_without_the_projection_object_is_refused() -> None:
    root = parse_pds3_label("OBJECT = IMAGE\nLINES = 1\nLINE_SAMPLES = 1\nEND_OBJECT\nEND\n")
    with pytest.raises(ValueError, match="IMAGE_MAP_PROJECTION"):
        pds3_polar_stereographic(root)


@pytest.mark.parametrize(
    ("written", "metres"),
    [
        ("0.5 <km/pix>", 500.0),
        ("0.5 <km/pixel>", 500.0),
        ("500.0 <m/pix>", 500.0),
        ("500.0 <m/pixel>", 500.0),
        ("500 <meters/pixel>", 500.0),
    ],
)
def test_map_scale_units_are_converted_to_metres(written: str, metres: float) -> None:
    text = modified(
        make_label(), "MAP_SCALE                    = 0.1 <km/pix>", f"MAP_SCALE = {written}"
    )
    projection = pds3_polar_stereographic(parse_pds3_label(text))
    assert projection.map_scale_m == pytest.approx(metres)


def test_radius_in_metres_and_a_missing_rotation_and_resolution() -> None:
    text = make_label()
    text = modified(text, "1737.4 <km>", "1737400.0 <m>")
    text = modified(text, "1737.4 <km>", "1737400.0 <m>")
    text = modified(text, "1737.4 <km>", "1737400.0 <m>")
    text = modified(text, "  MAP_PROJECTION_ROTATION      = 0.0 <deg>\n", "")
    text = modified(text, "  MAP_RESOLUTION               = 256.0 <pix/deg>\n", "")
    projection = pds3_polar_stereographic(parse_pds3_label(text))
    assert projection.radius_m == 1737400.0
    assert projection.map_resolution_ppd is None


# --- Independent oracle: GDAL's PDS driver ------------------------------------------------------


def write_detached_pair(tmp_path: Path, image: npt.NDArray[np.float32]) -> Path:
    lines, samples = image.shape
    (tmp_path / "SYN.IMG").write_bytes(image.astype("<f4").tobytes())
    label = tmp_path / "syn.lbl"
    label.write_text(make_label(lines=lines, samples=samples, record_bytes=samples * 4))
    return label


def synthetic_image() -> npt.NDArray[np.float32]:
    rng = np.random.default_rng(11)
    image = rng.normal(1.0, 0.25, (6, 8)).astype(np.float32)
    image[1, 2] = quality.ISIS_NULL_REAL4
    image[4, 7] = quality.ISIS_SATURATION_REAL4[0]
    return image


def test_gdal_with_the_documented_shift_agrees_with_the_reader(tmp_path: Path) -> None:
    image = synthetic_image()
    label_path = write_detached_pair(tmp_path, image)
    root = parse_pds3_label(label_path.read_text())
    layout = pds3_image_layout(root)
    projection = pds3_polar_stereographic(root)
    decoded = decode_pds3_rows(
        (tmp_path / layout.file_name).read_bytes()[: layout.image_bytes], layout, layout.lines
    )
    with (
        rasterio.Env(PDS_SampleProjOffset_Shift="-0.5", PDS_LineProjOffset_Shift="-0.5"),
        rasterio.open(label_path) as dataset,
    ):
        assert dataset.driver == "PDS"
        assert (dataset.height, dataset.width, dataset.count) == (6, 8, 1)
        gdal_array = dataset.read(1)
        gdal_transform = dataset.transform
        gdal_nodata = dataset.nodata
        gdal_crs = dataset.crs
    assert gdal_array.tobytes() == decoded.tobytes() == image.tobytes()
    assert tuple(gdal_transform)[:6] == pytest.approx(tuple(projection.transform)[:6], abs=1e-6)
    assert gdal_nodata == layout.special_values["CORE_NULL"]
    assert gdal_crs is not None
    assert gdal_crs.to_dict()["proj"] == "stere"
    assert float(gdal_crs.to_dict()["R"]) == projection.radius_m


def test_gdals_default_places_the_origin_one_pixel_further(tmp_path: Path) -> None:
    image = synthetic_image()
    label_path = write_detached_pair(tmp_path, image)
    projection = pds3_polar_stereographic(parse_pds3_label(label_path.read_text()))
    scale = projection.map_scale_m
    sample0, line0 = projection.sample_projection_offset, projection.line_projection_offset
    with rasterio.open(label_path) as dataset:
        default = dataset.transform
    assert default.c == pytest.approx(-(sample0 + 0.5) * scale, abs=1e-6)
    assert default.f == pytest.approx((line0 + 0.5) * scale, abs=1e-6)
    assert default.c == pytest.approx(projection.transform.c - scale, abs=1e-6)
    assert default.f == pytest.approx(projection.transform.f + scale, abs=1e-6)


# --- Row range reads ----------------------------------------------------------------------------

URL = "http://127.0.0.1:9/mosaic/SYN.IMG"
HEADER_BYTES = 64


def file_bytes() -> tuple[bytes, npt.NDArray[np.float32]]:
    image = synthetic_image()
    return bytes(range(HEADER_BYTES)) + image.astype("<f4").tobytes(), image


def offset_layout() -> Any:
    return layout_of(pointer='("SYN.IMG", 3)', record_bytes=32)  # offset 64


def response_for(
    blob: bytes, url: str, first: int, last: int, *, total: int | None = -1
) -> RangeResponse:
    end = min(last, len(blob) - 1)
    data = blob[first : end + 1]
    return RangeResponse(
        url=url,
        final_url=url,
        requested_first=first,
        requested_last=last,
        status=206,
        content_range=f"bytes {first}-{end}/{len(blob) if total == -1 else total}",
        first_byte=first,
        last_byte=end,
        total_size=len(blob) if total == -1 else total,
        bytes_received=len(data),
        data=data,
    )


@dataclass
class FakeFetch:
    blob: bytes
    calls: list[tuple[str, int, int]] = field(default_factory=list)

    def __call__(self, url: str, first: int, last: int) -> RangeResponse:
        self.calls.append((url, first, last))
        return response_for(self.blob, url, first, last)


def test_fetch_pds3_rows_makes_one_exact_range_request() -> None:
    blob, image = file_bytes()
    layout = offset_layout()
    fetch = FakeFetch(blob)
    response = fetch_pds3_rows(URL, layout, 2, 4, fetch=fetch)
    assert fetch.calls == [(URL, 64 + 2 * 32, 64 + 5 * 32 - 1)]
    assert response.requested_first == 128 and response.requested_last == 223
    assert response.total_size == len(blob) == layout.offset_bytes + layout.image_bytes
    decoded = decode_pds3_rows(response.data, layout, 3)
    assert decoded.tobytes() == image[2:5].tobytes()
    # The last row ends exactly at the end of the file.
    last = fetch_pds3_rows(URL, layout, 5, 5, fetch=fetch)
    assert fetch.calls[-1] == (URL, len(blob) - 32, len(blob) - 1)
    assert decode_pds3_rows(last.data, layout, 1).tobytes() == image[5:6].tobytes()


def test_fetch_pds3_rows_refuses_a_size_that_differs_from_the_label() -> None:
    blob, _ = file_bytes()
    layout = offset_layout()

    def longer(url: str, first: int, last: int) -> RangeResponse:
        return response_for(blob, url, first, last, total=len(blob) + 1)

    def unknown(url: str, first: int, last: int) -> RangeResponse:
        return response_for(blob, url, first, last, total=None)

    for fetch in (longer, unknown):
        with pytest.raises(RangeReadError, match="differs from the label") as error:
            fetch_pds3_rows(URL, layout, 0, 1, fetch=fetch)
        assert URL in str(error.value) or "127.0.0.1" in str(error.value)


def test_fetch_pds3_rows_refuses_a_response_that_is_not_the_requested_bytes() -> None:
    blob, _ = file_bytes()
    layout = offset_layout()

    def shifted(url: str, first: int, last: int) -> RangeResponse:
        return response_for(blob, url, first - 32, last - 32)

    def short(url: str, first: int, last: int) -> RangeResponse:
        return response_for(blob, url, first, last - 1)

    def truncated_data(url: str, first: int, last: int) -> RangeResponse:
        good = response_for(blob, url, first, last)
        return RangeResponse(**{**good.__dict__, "data": good.data[:-1]})

    for fetch in (shifted, short, truncated_data):
        with pytest.raises(RangeReadError, match=r"127\.0\.0\.1"):
            fetch_pds3_rows(URL, layout, 2, 3, fetch=fetch)


def test_fetch_pds3_rows_propagates_fetch_errors_unchanged() -> None:
    layout = offset_layout()
    not_supported = RangeNotSupportedError("the server ignored Range")
    failure = RangeReadError("HTTP 503")

    def ignores_range(url: str, first: int, last: int) -> RangeResponse:
        raise not_supported

    def fails(url: str, first: int, last: int) -> RangeResponse:
        raise failure

    with pytest.raises(RangeNotSupportedError) as first:
        fetch_pds3_rows(URL, layout, 0, 0, fetch=ignores_range)
    assert first.value is not_supported
    with pytest.raises(RangeReadError) as second:
        fetch_pds3_rows(URL, layout, 0, 0, fetch=fails)
    assert second.value is failure


def test_fetch_pds3_rows_checks_rows_and_url_before_fetching() -> None:
    blob, _ = file_bytes()
    layout = offset_layout()
    fetch = FakeFetch(blob)
    with pytest.raises(ValueError, match="row"):
        fetch_pds3_rows(URL, layout, 0, 6, fetch=fetch)
    with pytest.raises(ValueError, match="http"):
        fetch_pds3_rows("file:///etc/passwd", layout, 0, 0, fetch=fetch)
    with pytest.raises(ValueError, match="credentials"):
        fetch_pds3_rows("https://user:secret@127.0.0.1/x.img", layout, 0, 0, fetch=fetch)
    assert fetch.calls == []


class MosaicHandler(http.server.BaseHTTPRequestHandler):
    blob: ClassVar[bytes] = b""
    honour_range: ClassVar[bool] = True
    requests: ClassVar[list[str | None]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.requests.append(self.headers.get("Range"))
        match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", self.headers.get("Range", ""))
        if not self.honour_range or match is None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.blob)))
            self.end_headers()
            self.wfile.write(self.blob)
            return
        first, last = int(match.group(1)), min(int(match.group(2)), len(self.blob) - 1)
        body = self.blob[first : last + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {first}-{last}/{len(self.blob)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def serve(blob: bytes, *, honour_range: bool) -> Iterator[tuple[str, list[str | None]]]:
    requests: list[str | None] = []
    handler = type(
        "Handler",
        (MosaicHandler,),
        {"blob": blob, "honour_range": honour_range, "requests": requests},
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/SYN.IMG", requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_fetch_pds3_rows_through_a_loopback_server_with_range_support() -> None:
    blob, image = file_bytes()
    layout = offset_layout()
    with serve(blob, honour_range=True) as (url, requests):
        response = fetch_pds3_rows(url, layout, 1, 3)
    assert requests == [f"bytes={64 + 32}-{64 + 4 * 32 - 1}"]
    assert response.status == 206
    assert response.content_range == f"bytes 96-{64 + 4 * 32 - 1}/{len(blob)}"
    assert response.total_size == len(blob)
    assert decode_pds3_rows(response.data, layout, 3).tobytes() == image[1:4].tobytes()


def test_fetch_pds3_rows_through_a_loopback_server_that_ignores_range() -> None:
    blob, _ = file_bytes()
    layout = offset_layout()
    with (
        serve(blob, honour_range=False) as (url, requests),
        pytest.raises(RangeNotSupportedError, match="body was not read"),
    ):
        fetch_pds3_rows(url, layout, 0, 1)
    assert requests == [f"bytes=64-{64 + 2 * 32 - 1}"]


def test_fetch_pds3_rows_through_a_loopback_server_with_a_different_file_size() -> None:
    blob, _ = file_bytes()
    layout = offset_layout()
    with (
        serve(blob + bytes(32), honour_range=True) as (url, _requests),
        pytest.raises(RangeReadError, match="differs from the label"),
    ):
        fetch_pds3_rows(url, layout, 0, 1)


def test_crlf_and_lf_labels_parse_identically() -> None:
    lf = make_label()
    crlf = lf.replace("\n", "\r\n")
    assert "\r\n" in crlf and "\r\n" not in lf
    root_lf, root_crlf = parse_pds3_label(lf), parse_pds3_label(crlf)
    assert root_lf == root_crlf
    note = root_crlf.require("NOTE")
    assert note.value == "Circular Polarization Ratio South polar\nmosaic."
    assert note.raw == '"Circular Polarization Ratio South polar\nmosaic."'
    assert "\r" not in note.raw
    assert pds3_image_layout(root_crlf) == pds3_image_layout(root_lf)
    assert pds3_polar_stereographic(root_crlf) == pds3_polar_stereographic(root_lf)


def test_crlf_in_sequences_comments_and_errors() -> None:
    text = "A = (1,\r\n 2)\r\n/* c\r\n d */\r\nB = 3\r\nEND\r\n"
    root = parse_pds3_label(text)
    assert root.require("A").value == (1, 2)
    assert root.require("B").value == 3
    assert root.require("A").raw == "(1,\n 2)"
    with pytest.raises(ValueError, match=r"line 2\b"):
        parse_pds3_label("A = 1\r\nB = 'open\r\nEND\r\n")


def test_the_parser_returns_plain_frozen_objects() -> None:
    root = parse_pds3_label(make_label())
    assert isinstance(root, Pds3Object)
    with pytest.raises(AttributeError):
        root.name = "x"  # type: ignore[misc]
