"""search.Sources takes the VERIFIED checksum over a PARTIAL one, like export.merge_sources."""

import json
from pathlib import Path

from terraluna_pipeline.search import Sources


def _write(path: Path, status: str, checksum: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([{"product_id": "p", "verification_status": status, "checksum": checksum}])
    )


def test_verified_checksum_wins_over_partial(tmp_path: Path) -> None:
    _write(tmp_path / "outputs" / "sources.json", "PARTIAL", "aaa")
    _write(tmp_path / "outputs" / "realcard" / "sources.json", "VERIFIED", "bbb")
    assert Sources(tmp_path).known["p"] == "bbb"


def test_partial_alone_is_kept(tmp_path: Path) -> None:
    _write(tmp_path / "outputs" / "sources.json", "PARTIAL", "aaa")
    assert Sources(tmp_path).known["p"] == "aaa"
