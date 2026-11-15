"""The SVG allowlist for bundle figures (PRD 8.4, CONTRACTS a.3 ``figures/{name}.svg``).

A figure is copied into the release bundle and shown by the web app, so it must carry no active
content. :func:`svg_allowlist_errors` refuses:

- a DOCTYPE, an ENTITY declaration or any entity reference other than the five XML built-ins
  (no external or expanding entities reach the parser);
- a root element that is not ``svg`` in the SVG namespace, and any element outside that namespace;
- ``<script>`` and ``<foreignObject>`` elements;
- any attribute whose local name starts with ``on`` (event handlers), in any case;
- ``<animate>`` and ``<set>`` elements that target an ``href`` or ``on*`` attribute;
- an ``href`` or ``xlink:href`` that is not a ``#fragment`` or an embedded PNG;
- a CSS ``url(...)`` or ``@import`` in a ``style`` attribute or ``<style>`` element, unless the
  url is a ``#fragment``.

An embedded raster written as ``data:image/png;base64,...`` is not external: the bytes are inside
the SVG and the browser fetches nothing, so that one data URI form is allowed. Every other data
URI (``data:image/svg+xml``, ``data:text/html`` and so on) is refused, because it can carry
markup.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
PNG_DATA_URI_PREFIX = "data:image/png;base64,"

_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_ENTITY_REF = re.compile(rb"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);)")
_CSS_URL = re.compile(r"url\s*\(\s*['\"]?\s*([^'\")\s]*)", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import", re.IGNORECASE)
_PNG_BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}")


def _split(name: str) -> tuple[str, str]:
    """Split an ElementTree ``{namespace}local`` name."""
    if name.startswith("{"):
        namespace, _, local = name[1:].partition("}")
        return namespace, local
    return "", name


def _href_allowed(value: str) -> bool:
    stripped = value.strip()
    if stripped.startswith("#"):
        return True
    if stripped.startswith(PNG_DATA_URI_PREFIX):
        return _PNG_BASE64.fullmatch(stripped[len(PNG_DATA_URI_PREFIX) :]) is not None
    return False


def _css_errors(text: str, where: str) -> list[str]:
    errors: list[str] = []
    if _CSS_IMPORT.search(text):
        errors.append(f"{where}: CSS @import is not allowed")
    for match in _CSS_URL.finditer(text):
        if not match.group(1).startswith("#"):
            errors.append(f"{where}: CSS url({match.group(1)}) is not a #fragment")
    return errors


def svg_allowlist_errors(data: bytes) -> list[str]:
    """Return the reasons ``data`` is refused as a bundle SVG; an empty list means allowed."""
    match = _DECLARATION.search(data)
    if match:
        return [f"{match.group(1).decode('ascii').upper()} declarations are not allowed"]
    if _ENTITY_REF.search(data):
        return ["entity references other than the XML built-ins are not allowed"]
    try:
        # DOCTYPE and entity declarations are refused above, so expat expands nothing.
        root = ET.fromstring(data)
    except ET.ParseError as error:
        return [f"not well-formed XML: {error}"]

    errors: list[str] = []
    if _split(root.tag) != (SVG_NS, "svg"):
        errors.append(f"root element {root.tag!r} is not an SVG <svg>")
    for index, element in enumerate(root.iter()):
        namespace, local = _split(element.tag)
        where = f"element {index} <{local}>"
        if namespace != SVG_NS:
            errors.append(f"{where}: namespace {namespace or '(none)'!r} is not SVG")
        if local.lower() == "script":
            errors.append(f"{where}: <script> is not allowed")
        if local.lower() == "foreignobject":
            errors.append(f"{where}: <foreignObject> is not allowed")
        if local in ("animate", "set"):
            target = element.get("attributeName", "")
            target_local = target.rpartition(":")[2].lower()
            if target_local == "href" or target_local.startswith("on"):
                errors.append(f"{where}: animating {target!r} is not allowed")
        if local == "style" and element.text:
            errors.extend(_css_errors(element.text, where))
        for name, value in element.attrib.items():
            _, attr = _split(name)
            if attr.lower().startswith("on"):
                errors.append(f"{where}: event attribute {attr!r} is not allowed")
            elif attr == "href" and not _href_allowed(value):
                errors.append(f"{where}: href {value[:60]!r} is external")
            elif attr == "style":
                errors.extend(_css_errors(value, f"{where} style"))
    return errors
