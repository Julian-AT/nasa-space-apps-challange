"""Tests for the bundle SVG allowlist (PRD 8.4). Every SVG here is a synthetic test string."""

from __future__ import annotations

import pytest

from terraluna_pipeline.svg import svg_allowlist_errors

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'


def _svg(body: str) -> bytes:
    return f"{HEAD}<title>t</title>{body}</svg>".encode()


def test_plain_svg_is_allowed() -> None:
    body = (
        '<defs><linearGradient id="g"/></defs><rect x="0" y="0" width="1" height="1" '
        'fill="url(#g)" style="fill: url(#g)"/><use href="#g"/><use xlink:href="#g"/>'
        '<image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/>'
        "<text>a &amp; b &#x3b1;</text>"
    )
    assert svg_allowlist_errors(b'<?xml version="1.0" encoding="UTF-8"?>\n' + _svg(body)) == []


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("<script>alert(1)</script>", "<script>"),
        ('<script xmlns="http://www.w3.org/1999/xhtml">x</script>', "<script>"),
        ("<foreignObject><div/></foreignObject>", "foreignObject"),
        ('<rect onclick="x()"/>', "onclick"),
        ('<rect ONLOAD="x()"/>', "ONLOAD"),
        ('<a href="https://example.org/">x</a>', "external"),
        ('<use xlink:href="other.svg#a"/>', "external"),
        ('<image href="javascript:alert(1)"/>', "external"),
        ('<image href="data:image/svg+xml;base64,PHN2Zy8+"/>', "external"),
        ('<image href="data:image/png;base64,a b"/>', "external"),
        ('<rect style="fill: url(https://example.org/a)"/>', "url("),
        ("<style>@import 'https://example.org/a.css';</style>", "@import"),
        ('<set attributeName="href" to="javascript:x"/>', "animating"),
        ('<animate attributeName="onclick"/>', "animating"),
    ],
)
def test_active_content_is_refused(body: str, needle: str) -> None:
    errors = svg_allowlist_errors(_svg(body))
    assert errors
    assert any(needle in error for error in errors), errors


def test_doctype_and_entities_are_refused() -> None:
    doctype = b'<!DOCTYPE svg [<!ENTITY a "aaaa">]>' + _svg("<text>&a;</text>")
    assert svg_allowlist_errors(doctype) == ["DOCTYPE declarations are not allowed"]
    assert svg_allowlist_errors(_svg("<text>&a;</text>"))


def test_non_svg_root_and_malformed_xml_are_refused() -> None:
    assert svg_allowlist_errors(b"<html/>")
    assert svg_allowlist_errors(b"<svg")
