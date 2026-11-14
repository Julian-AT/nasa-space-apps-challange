"""Tests for the STAC item-search reader in io.py (T1-05; PRD 5 Mars inputs, 8.3).

The search pages are synthetic JSON served by a server on 127.0.0.1, read through the real
``fetch_file``; an autouse fixture refuses to resolve any other host, so no test reaches the
network.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest

from terraluna_pipeline.io import (
    STAC_MAX_PAGES,
    STAC_PAGE_LIMIT_BYTES,
    DownloadError,
    StacError,
    fetch_stac_search,
    parse_stac_item_collection,
    stac_search_url,
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


# --- Synthetic item collections -----------------------------------------------------------------


def feature(item_id: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": "synthetic_collection",
        "bbox": [1.0, 2.0, 3.0, 4.0],
        "properties": {},
        "assets": {},
        "links": [],
    }


def page(
    ids: list[str],
    *,
    next_href: str | None = None,
    matched: int | None = None,
    method: str | None = "GET",
    extra_link: dict[str, Any] | None = None,
) -> dict[str, Any]:
    links: list[dict[str, Any]] = [{"rel": "root", "href": "https://example.test/api"}]
    if next_href is not None:
        link: dict[str, Any] = {"rel": "next", "href": next_href, "type": "application/geo+json"}
        if method is not None:
            link["method"] = method
        links.insert(0, link)
    if extra_link is not None:
        links.append(extra_link)
    body: dict[str, Any] = {
        "type": "FeatureCollection",
        "numberReturned": len(ids),
        "features": [feature(item_id) for item_id in ids],
        "links": links,
    }
    if matched is not None:
        body["numberMatched"] = matched
    return body


def encode(body: Any) -> bytes:
    return json.dumps(body).encode("utf-8")


PAGE_URL = "https://stac.example.test/api/search?collections=c&bbox=1.0%2C2.0%2C3.0%2C4.0"


# --- stac_search_url ----------------------------------------------------------------------------


def test_the_search_url_names_the_collection_bbox_and_limit() -> None:
    url = stac_search_url(
        "https://stac.astrogeology.usgs.gov/api",
        collection="mro_ctx_controlled_usgs_dtms",
        bbox=(76.69, 17.41, 78.69, 19.41),
        limit=100,
    )
    assert url == (
        "https://stac.astrogeology.usgs.gov/api/search?collections=mro_ctx_controlled_usgs_dtms"
        "&bbox=76.69%2C17.41%2C78.69%2C19.41&limit=100"
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query == {
        "collections": ["mro_ctx_controlled_usgs_dtms"],
        "bbox": ["76.69,17.41,78.69,19.41"],
        "limit": ["100"],
    }


def test_a_trailing_slash_on_the_api_url_is_not_doubled() -> None:
    url = stac_search_url("https://x.test/api/", collection="c", bbox=(0, 0, 1, 1), limit=5)
    assert url.startswith("https://x.test/api/search?")


def test_an_antimeridian_bbox_keeps_west_greater_than_east() -> None:
    url = stac_search_url(
        "https://x.test/api", collection="c", bbox=(165.86, 33.87, -149.57, 64.17), limit=100
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["bbox"] == ["165.86,33.87,-149.57,64.17"]


@pytest.mark.parametrize(
    "bbox",
    [
        (0.0, 0.0, 1.0),
        (0.0, 2.0, 1.0, 1.0),
        (0.0, -91.0, 1.0, 1.0),
        (0.0, 0.0, 181.0, 1.0),
        (float("nan"), 0.0, 1.0, 1.0),
        (0.0, 0.0, float("inf"), 1.0),
    ],
)
def test_an_invalid_bbox_is_refused(bbox: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="bbox"):
        stac_search_url("https://x.test/api", collection="c", bbox=bbox, limit=10)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_is_refused(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        stac_search_url("https://x.test/api", collection="c", bbox=(0, 0, 1, 1), limit=limit)


def test_an_empty_collection_is_refused() -> None:
    with pytest.raises(ValueError, match="collection"):
        stac_search_url("https://x.test/api", collection="", bbox=(0, 0, 1, 1), limit=1)


# --- parse_stac_item_collection -----------------------------------------------------------------


def test_a_page_is_parsed_with_its_counts_and_next_link() -> None:
    parsed = parse_stac_item_collection(
        encode(page(["b", "a"], next_href="https://stac.example.test/api/search?next=x", matched=5)),
        PAGE_URL,
    )
    assert parsed.ids == ("b", "a")
    assert [item["id"] for item in parsed.features] == ["b", "a"]
    assert parsed.number_matched == 5
    assert parsed.number_returned == 2
    assert parsed.next_url == "https://stac.example.test/api/search?next=x"


def test_a_last_page_has_no_next_url_and_absent_counts_are_none() -> None:
    body = page(["a"])
    del body["numberReturned"]
    parsed = parse_stac_item_collection(encode(body), PAGE_URL)
    assert parsed.next_url is None
    assert parsed.number_matched is None
    assert parsed.number_returned is None


def test_a_relative_next_link_resolves_against_the_page_url() -> None:
    parsed = parse_stac_item_collection(encode(page([], next_href="search?next=2")), PAGE_URL)
    assert parsed.next_url == "https://stac.example.test/api/search?next=2"


def test_a_next_link_without_a_method_defaults_to_get() -> None:
    parsed = parse_stac_item_collection(
        encode(page([], next_href="https://s.test/n", method=None)), PAGE_URL
    )
    assert parsed.next_url == "https://s.test/n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([], "FeatureCollection"),
        ({"type": "Feature"}, "FeatureCollection"),
        ({"type": "FeatureCollection"}, "features"),
        ({"type": "FeatureCollection", "features": {}}, "features"),
        ({"type": "FeatureCollection", "features": [{"type": "Feature"}]}, "id"),
        ({"type": "FeatureCollection", "features": [{"type": "Feature", "id": ""}]}, "id"),
        ({"type": "FeatureCollection", "features": [{"id": "a"}]}, "Feature"),
        ({"type": "FeatureCollection", "features": [], "numberMatched": -1}, "numberMatched"),
        ({"type": "FeatureCollection", "features": [], "numberMatched": True}, "numberMatched"),
        ({"type": "FeatureCollection", "features": [], "numberMatched": 2.0}, "numberMatched"),
        ({"type": "FeatureCollection", "features": [], "numberReturned": 1}, "numberReturned"),
        ({"type": "FeatureCollection", "features": [], "links": {}}, "links"),
    ],
)
def test_a_malformed_page_is_refused_naming_the_url(body: Any, message: str) -> None:
    with pytest.raises(StacError, match=message) as raised:
        parse_stac_item_collection(encode(body), PAGE_URL)
    assert PAGE_URL in str(raised.value)


@pytest.mark.parametrize(
    "data", [b"<html>error</html>", b'{"type": "FeatureCollection", "features": [NaN]}', b"\xff"]
)
def test_a_page_that_is_not_strict_json_is_refused(data: bytes) -> None:
    with pytest.raises(StacError, match="JSON"):
        parse_stac_item_collection(data, PAGE_URL)


def test_deeply_nested_json_is_refused_as_a_stac_error() -> None:
    with pytest.raises(StacError, match="JSON"):
        parse_stac_item_collection(b"[" * 100_000 + b"]" * 100_000, PAGE_URL)


def test_a_post_next_link_is_refused() -> None:
    with pytest.raises(StacError, match="POST"):
        parse_stac_item_collection(
            encode(page([], next_href="https://s.test/search", method="POST")), PAGE_URL
        )


def test_a_next_link_with_a_body_is_refused() -> None:
    body = page([], next_href="https://s.test/search")
    body["links"][0]["body"] = {"token": "x"}
    with pytest.raises(StacError, match="body"):
        parse_stac_item_collection(encode(body), PAGE_URL)


def test_two_next_links_are_refused() -> None:
    body = page([], next_href="https://s.test/a", extra_link={"rel": "next", "href": "/b"})
    with pytest.raises(StacError, match="next"):
        parse_stac_item_collection(encode(body), PAGE_URL)


def test_a_next_link_to_another_scheme_is_refused() -> None:
    with pytest.raises(StacError, match="http"):
        parse_stac_item_collection(encode(page([], next_href="ftp://s.test/a")), PAGE_URL)


# --- fetch_stac_search against a server on 127.0.0.1 --------------------------------------------


@dataclass
class StacServer:
    pages: dict[str, bytes]
    requests: list[str] = field(default_factory=list)
    base: str = ""


class StacHandler(http.server.BaseHTTPRequestHandler):
    server_data: ClassVar[StacServer]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state = self.server_data
        state.requests.append(self.path)
        body = state.pages.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def stac_server() -> Iterator[StacServer]:
    state = StacServer(pages={})
    handler = type("Handler", (StacHandler,), {"server_data": state})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def name(number: int) -> str:
    return f"page{number:02d}.json"


def test_every_page_is_fetched_until_the_next_link_is_absent(
    stac_server: StacServer, tmp_path: Path
) -> None:
    base = stac_server.base
    stac_server.pages = {
        "/api/search?p=1": encode(page(["c", "a"], next_href=f"{base}/api/search?p=2", matched=4)),
        "/api/search?p=2": encode(page(["b", "d"], next_href="search?p=3", matched=4)),
        "/api/search?p=3": encode(page([], matched=4)),
    }
    pages = fetch_stac_search(f"{base}/api/search?p=1", tmp_path, name)
    assert [p.number for p in pages] == [1, 2, 3]
    assert [p.url for p in pages] == [f"{base}/api/search?p={n}" for n in (1, 2, 3)]
    assert stac_server.requests == ["/api/search?p=1", "/api/search?p=2", "/api/search?p=3"]
    assert [item for p in pages for item in p.content.ids] == ["c", "a", "b", "d"]
    assert pages[0].content.next_url == pages[1].url
    assert pages[1].content.next_url == pages[2].url
    assert pages[2].content.next_url is None
    for p in pages:
        assert p.fetched.path == tmp_path / name(p.number)
        assert p.fetched.path.read_bytes() == stac_server.pages[f"/api/search?p={p.number}"]
        assert p.fetched.status == 200
        assert p.fetched.content_type == "application/geo+json"


def test_a_next_link_that_repeats_a_page_is_refused(
    stac_server: StacServer, tmp_path: Path
) -> None:
    base = stac_server.base
    stac_server.pages = {
        "/s?p=1": encode(page(["a"], next_href=f"{base}/s?p=2")),
        "/s?p=2": encode(page(["b"], next_href=f"{base}/s?p=1")),
    }
    with pytest.raises(DownloadError, match="repeats") as raised:
        fetch_stac_search(f"{base}/s?p=1", tmp_path, name)
    assert f"{base}/s?p=1" in str(raised.value)
    assert stac_server.requests == ["/s?p=1", "/s?p=2"]


def test_more_pages_than_the_limit_are_refused(stac_server: StacServer, tmp_path: Path) -> None:
    base = stac_server.base
    stac_server.pages = {
        f"/s?p={n}": encode(page([str(n)], next_href=f"{base}/s?p={n + 1}")) for n in range(1, 5)
    }
    with pytest.raises(DownloadError, match="more than 3 pages"):
        fetch_stac_search(f"{base}/s?p=1", tmp_path, name, max_pages=3)
    assert len(stac_server.requests) == 3


def test_an_unreachable_page_names_the_url(stac_server: StacServer, tmp_path: Path) -> None:
    base = stac_server.base
    stac_server.pages = {"/s?p=1": encode(page(["a"], next_href=f"{base}/missing"))}
    with pytest.raises(DownloadError, match="404") as raised:
        fetch_stac_search(f"{base}/s?p=1", tmp_path, name)
    assert f"{base}/missing" in str(raised.value)


def test_a_malformed_page_names_its_url(stac_server: StacServer, tmp_path: Path) -> None:
    base = stac_server.base
    stac_server.pages = {"/s": b"<html>not json</html>"}
    with pytest.raises(StacError) as raised:
        fetch_stac_search(f"{base}/s", tmp_path, name)
    assert f"{base}/s" in str(raised.value)


def test_a_refused_connection_names_the_url(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}/api/search"
    with pytest.raises(DownloadError) as raised:
        fetch_stac_search(url, tmp_path, name)
    assert url in str(raised.value)


def test_the_page_limits_are_generous_but_bounded() -> None:
    assert STAC_MAX_PAGES == 100
    assert STAC_PAGE_LIMIT_BYTES == 20_000_000


def test_a_signed_next_link_is_refused_before_it_is_requested(
    stac_server: StacServer, tmp_path: Path
) -> None:
    base = stac_server.base
    stac_server.pages = {"/s": encode(page(["a"], next_href=f"{base}/s?X-Amz-Signature=abc"))}
    with pytest.raises(ValueError, match="signature"):
        fetch_stac_search(f"{base}/s", tmp_path, name)
    assert stac_server.requests == ["/s"]
