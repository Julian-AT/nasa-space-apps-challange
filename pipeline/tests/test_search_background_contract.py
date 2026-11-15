"""The committed search background files satisfy the Background contract (no extra fields)."""

from pathlib import Path

import pytest

from terraluna_pipeline.schema import Background

DIR = Path(__file__).resolve().parents[2] / "outputs" / "search" / "reference"
FILES = sorted(DIR.glob("background_*.json"))


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_background_validates(path: Path) -> None:
    Background.model_validate_json(path.read_bytes())
