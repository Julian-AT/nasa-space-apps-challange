"""Provenance for every record writer: run_id, config version, file hashes and command line.

This is the single run_id implementation (CONTRACTS c.4; PRD 8.13: "run_id is a hash of the
config and all product checksums"). Each record computes it over the products it lists, such as
a ComparisonRecord's ``source_ids`` or a FingerprintRecord's ``product_ids``; the manifest
computes it over the staged sources.json. Any edit to config/run.yaml changes every run_id.

Nothing here reads a clock, the environment or a random source, so the same inputs always give
the same strings and re-recording a result is byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePath
from typing import TypedDict

from terraluna_pipeline.schema import ProductRecord

RUN_ID_PREFIX = "sha256:"

_SHA256 = re.compile(r"[0-9a-f]{64}")


class FileHash(TypedDict):
    """The SHA-256 of one file, named by the path it was read from."""

    path: str
    sha256: str


def canonical_json(value: object) -> bytes:
    """Deterministic JSON: sorted keys, 2-space indent, LF line endings and a trailing newline.

    NaN and infinities are refused. This is byte for byte ``export.canonical_json``; it lives
    here as well so that provenance imports no writer (CONTRACTS c.4).
    """
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _file_sha256(path: str | PurePath) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def config_version(path: str | PurePath) -> str:
    """The SHA-256 of the config file's bytes as read, with no normalisation."""
    return _file_sha256(path)


def file_hashes(paths: Iterable[str | PurePath]) -> list[FileHash]:
    """``[{"path", "sha256"}]`` for each file, sorted by its POSIX path as given.

    A path given twice is refused, so a list never names one input twice.
    """
    hashes: dict[str, FileHash] = {}
    for path in paths:
        name = PurePath(path).as_posix()
        if name in hashes:
            raise ValueError(f"{name} is listed twice")
        hashes[name] = {"path": name, "sha256": _file_sha256(path)}
    return [hashes[name] for name in sorted(hashes)]


def command_line(module: str, argv: Sequence[str]) -> str:
    """The shell-quoted command ``python -m <module> <argv...>``; ``shlex.split`` inverts it."""
    return shlex.join(["python", "-m", module, *argv])


def _product_pair(product: ProductRecord | tuple[str, str | None]) -> tuple[str, str | None]:
    if isinstance(product, ProductRecord):
        return product.product_id, product.checksum
    product_id, checksum = product
    if not isinstance(product_id, str) or product_id == "":
        raise ValueError(f"product id {product_id!r} must be a non-empty string")
    if checksum is not None and (
        not isinstance(checksum, str) or _SHA256.fullmatch(checksum) is None
    ):
        raise ValueError(
            f"checksum of {product_id} must be 64 lowercase hex characters or null, "
            f"not {checksum!r}"
        )
    return product_id, checksum


def run_id(
    config_path: str | PurePath, products: Iterable[ProductRecord | tuple[str, str | None]]
) -> str:
    """``"sha256:" + sha256(canonical_json({"config_sha256", "products"}))`` (CONTRACTS c.4).

    ``config_sha256`` is ``config_version(config_path)`` and ``products`` the
    ``[product_id, checksum or null]`` pairs sorted by product id, so the order in which the
    products are given does not matter. A product id given twice is refused.
    """
    pairs: dict[str, str | None] = {}
    for product in products:
        product_id, checksum = _product_pair(product)
        if product_id in pairs:
            raise ValueError(f"product {product_id} is listed twice")
        pairs[product_id] = checksum
    document = {
        "config_sha256": config_version(config_path),
        "products": [[product_id, pairs[product_id]] for product_id in sorted(pairs)],
    }
    return RUN_ID_PREFIX + hashlib.sha256(canonical_json(document)).hexdigest()
