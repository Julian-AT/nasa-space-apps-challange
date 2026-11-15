"""Shard round trip, hash verification and training-only standardisation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from helpers_shards import make_mock_shards
from terraluna_ml import shards


def test_round_trip_and_hashes(tmp_path: Path) -> None:
    manifest = make_mock_shards(tmp_path / "s")
    opened = shards.open_shards(manifest)
    assert opened.windows.shape == (72, 3, 64, 64)
    assert opened.windows.dtype == np.float16
    assert opened.mock
    std, mask = opened.physics()
    assert std.shape == (72, 11)
    assert not mask[0, 3]
    assert np.isnan(std[0, 3])
    assert np.all(np.isfinite(std[mask]))
    assert opened.data_manifest_sha256 == shards.sha256_path(manifest)


def test_tampered_shard_fails(tmp_path: Path) -> None:
    manifest = make_mock_shards(tmp_path / "s")
    path = tmp_path / "s" / shards.WINDOWS_FILE
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(shards.ShardError, match="SHA-256"):
        shards.open_shards(manifest)


def test_standardisation_uses_train_only() -> None:
    raw = np.array([[1.0] * 11, [3.0] * 11, [100.0] * 11])
    mask = np.ones_like(raw, dtype=np.bool_)
    a = shards.fit_standardisation(raw, mask, ["train", "train", "test"])
    raw[2] = -50.0
    b = shards.fit_standardisation(raw, mask, ["train", "train", "test"])
    assert a == b
    assert a.mean[0] == 2.0
