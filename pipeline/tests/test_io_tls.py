"""TLS verification of the network layer (io.fetch_range and io.fetch_file).

PRD 8.3: the readers never weaken verification. They verify against certifi's Mozilla CA bundle,
which uv.lock pins, instead of the platform's OpenSSL file, which can lag behind: the uv-managed
Python on the build Mac reads /etc/ssl/cert.pem, which lacks the SSL.com TLS RSA Root CA 2022
that the PDS Geosciences Node chains to (checked on 2026-11-14). Offline: no request is sent.
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import certifi
import pytest

from terraluna_pipeline import io
from terraluna_pipeline.io import DownloadError, RangeReadError


def test_the_tls_context_verifies_hostnames_and_certificates() -> None:
    context = io.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert io.tls_context() is context


def test_the_tls_context_trusts_the_certifi_bundle() -> None:
    expected = Path(certifi.where()).read_text(encoding="ascii").count("BEGIN CERTIFICATE")
    loaded = io.tls_context().get_ca_certs()
    assert len(loaded) == expected > 100
    subjects = {
        tuple(pair for rdn in cert["subject"] for pair in rdn)
        for cert in loaded
        if isinstance(cert.get("subject"), tuple)
    }
    assert (
        ("countryName", "US"),
        ("organizationName", "SSL Corporation"),
        ("commonName", "SSL.com TLS RSA Root CA 2022"),
    ) in subjects


class _Stop(urllib.error.URLError):
    pass


def _capture_openers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Record the handlers each opener is built with; opening then fails without a request."""
    built: list[tuple[Any, ...]] = []

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> Any:
            raise _Stop("stopped before any request")

    def build_opener(*handlers: Any) -> Opener:
        built.append(handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    return built


def _https_contexts(handlers: tuple[Any, ...]) -> list[Any]:
    return [
        vars(handler)["_context"]
        for handler in handlers
        if isinstance(handler, urllib.request.HTTPSHandler)
    ]


def test_fetch_range_uses_the_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _capture_openers(monkeypatch)
    with pytest.raises(RangeReadError):
        io.fetch_range("https://data.example.test/file.img", 0, 9)
    (handlers,) = built
    assert _https_contexts(handlers) == [io.tls_context()]


def test_fetch_file_uses_the_tls_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    built = _capture_openers(monkeypatch)
    with pytest.raises(DownloadError):
        io.fetch_file("https://data.example.test/file.lbl", tmp_path / "file.lbl")
    (handlers,) = built
    assert _https_contexts(handlers) == [io.tls_context()]
    assert not (tmp_path / "file.lbl").exists()
