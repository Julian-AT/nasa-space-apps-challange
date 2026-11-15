"""Spatial split maps (T11-04; PRD 9 "What is held out", 11.2 "Splits by space").

Windows are assigned to train, validation or test by spatial block, never per window:

- **Moon**: four 90 degree longitude sectors, [-180, -90), [-90, 0), [0, 90), [90, 180). One
  test, one validation, two train (PRD 11.2).
- **Mars**: the 10 x 10 degree tile of each CTX DTM's centre; DTMs whose footprints overlap (one
  connected acquisition) are merged with every tile they touch into one block, so all windows of
  one DTM and of one connected acquisition share a split.
- **Earth**: 2 x 2 degree blocks of the window centre.

Mars and Earth: ``round(0.15 n)`` test and ``round(0.10 n)`` validation blocks (at least one
each), chosen by a seeded permutation. Moon: the sector roles are a seeded choice among the
assignments that leave training non-empty under the configured touch rule (recorded).

**Touch rule** (PRD 11.2: "training blocks that touch a test block are dropped"): touch means
sharing an edge or a corner (longitude wraps). ``pole_point_counts`` states whether Moon sectors,
which all meet at the pole, touch through that point; with it True every Moon training sector
touches the test sector and building the split raises :class:`EmptySplitError` (it never falls
back to another rule). The configured value is recorded in the split map. Dropped blocks are
listed with the reason and the dropped fraction is recorded.

Only training blocks may be used to fit preprocessing statistics (see ``shards``).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import numpy as np

RULE_VERSION: Final = "terraluna-splits/1"
MOON_SECTORS: Final = ("lon[-180,-90)", "lon[-90,0)", "lon[0,90)", "lon[90,180)")


class EmptySplitError(ValueError):
    """A split left empty after the touch rule; the message names the reason."""


@dataclass(frozen=True)
class SplitConfig:
    test_fraction: float = 0.15
    val_fraction: float = 0.10
    mars_tile_deg: float = 10.0
    earth_block_deg: float = 2.0
    touch: str = "edge_or_corner"
    pole_point_counts: bool = False
    seed: int = 0


@dataclass
class SplitMap:
    """Block assignment per body; ``assignment[block] in {train, val, test, dropped}``."""

    config: SplitConfig
    assignment: dict[str, str] = field(default_factory=dict)
    body_of: dict[str, str] = field(default_factory=dict)
    dropped_reason: dict[str, str] = field(default_factory=dict)
    notes: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        doc: dict[str, object] = {
            "rule_version": RULE_VERSION,
            "config": self.config.__dict__,
            "assignment": dict(sorted(self.assignment.items())),
            "body_of": dict(sorted(self.body_of.items())),
            "dropped": dict(sorted(self.dropped_reason.items())),
            "notes": self.notes,
        }
        per_body: dict[str, dict[str, int]] = {}
        for block, role in self.assignment.items():
            per_body.setdefault(self.body_of[block], {}).setdefault(role, 0)
            per_body[self.body_of[block]][role] += 1
        doc["blocks_by_body_role"] = per_body
        train_like = {
            b: (r.get("dropped", 0), r.get("dropped", 0) + r.get("train", 0))
            for b, r in per_body.items()
        }
        doc["dropped_training_fraction"] = {
            b: (d / t if t else None) for b, (d, t) in train_like.items()
        }
        text = json.dumps(doc, sort_keys=True)
        doc["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        return doc


def moon_sector(lon_deg: float) -> int:
    lon = (lon_deg + 180.0) % 360.0 - 180.0
    return int(math.floor((lon + 180.0) / 90.0)) % 4


def moon_block(lon_deg: float) -> str:
    return f"moon:{MOON_SECTORS[moon_sector(lon_deg)]}"


def earth_block(lat_deg: float, lon_deg: float, size: float = 2.0) -> str:
    i = int(math.floor(lat_deg / size))
    j = int(math.floor(((lon_deg + 180.0) % 360.0 - 180.0) / size))
    return f"earth:{i * size:+.0f}:{j * size:+.0f}"


def tile_key(lat_deg: float, lon_deg: float, size: float) -> tuple[int, int]:
    return int(math.floor(lat_deg / size)), int(math.floor(((lon_deg + 180.0) % 360.0) / size))


def _tiles_touch(a: tuple[int, int], b: tuple[int, int], n_lon: int) -> bool:
    di = abs(a[0] - b[0])
    dj = abs(a[1] - b[1]) % n_lon
    dj = min(dj, n_lon - dj)
    return di <= 1 and dj <= 1


def _counts(n: int, config: SplitConfig) -> tuple[int, int]:
    n_test = max(1, int(round(config.test_fraction * n)))
    n_val = max(1, int(round(config.val_fraction * n)))
    if n_test + n_val >= n:
        raise EmptySplitError(f"{n} blocks cannot hold {n_test} test and {n_val} validation")
    return n_test, n_val


def _assign_tiled(
    body: str,
    blocks: Mapping[str, Sequence[tuple[int, int]]],
    n_lon: int,
    config: SplitConfig,
    split: SplitMap,
    rng: np.random.Generator,
) -> None:
    names = sorted(blocks)
    n_test, n_val = _counts(len(names), config)
    order = [names[i] for i in rng.permutation(len(names))]
    roles = {b: "train" for b in names}
    for b in order[:n_test]:
        roles[b] = "test"
    for b in order[n_test : n_test + n_val]:
        roles[b] = "val"
    test_tiles = [t for b in names if roles[b] == "test" for t in blocks[b]]
    for b in names:
        split.body_of[b] = body
        if roles[b] == "train" and any(
            _tiles_touch(t, u, n_lon) for t in blocks[b] for u in test_tiles
        ):
            roles[b] = "dropped"
            split.dropped_reason[b] = "training block touches a test block (edge or corner)"
        split.assignment[b] = roles[b]
    for role in ("train", "val", "test"):
        if not any(r == role for b, r in split.assignment.items() if split.body_of[b] == body):
            raise EmptySplitError(f"{body}: no {role} block survives the touch rule")


def build_split_map(
    *,
    mars_dtm_tiles: Mapping[str, Sequence[tuple[int, int]]],
    earth_blocks: Sequence[str],
    config: SplitConfig,
) -> tuple[SplitMap, dict[str, str]]:
    """Assign Moon sectors, Mars DTM groups and Earth blocks.

    ``mars_dtm_tiles`` maps each DTM id to the 10 degree tiles its footprint touches (the tile
    of its centre first). Returns the split map and the Mars DTM -> block id map.
    """
    rng = np.random.default_rng(config.seed)
    split = SplitMap(config=config)

    # Moon: four sectors in a ring.
    valid: list[tuple[int, int]] = []
    for test in range(4):
        for val in range(4):
            if val == test:
                continue
            train = [s for s in range(4) if s not in (test, val)]
            kept = [s for s in train if not (config.pole_point_counts or (s - test) % 4 in (1, 3))]
            if kept:
                valid.append((test, val))
    if not valid:
        raise EmptySplitError(
            "moon: every training sector touches the test sector under the configured rule "
            f"(pole_point_counts={config.pole_point_counts})"
        )
    test, val = valid[int(rng.integers(len(valid)))]
    for s, name in enumerate(MOON_SECTORS):
        block = f"moon:{name}"
        split.body_of[block] = "moon"
        if s == test:
            split.assignment[block] = "test"
        elif s == val:
            split.assignment[block] = "val"
        elif config.pole_point_counts or (s - test) % 4 in (1, 3):
            split.assignment[block] = "dropped"
            split.dropped_reason[block] = "training sector touches the test sector along an edge"
        else:
            split.assignment[block] = "train"
    split.notes["moon_role_choice"] = (
        "seeded choice among the sector assignments that leave a training sector under the "
        f"configured touch rule ({len(valid)} candidates)"
    )

    # Mars: union-find over DTMs sharing a tile.
    parent: dict[str, str] = {d: d for d in mars_dtm_tiles}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[tuple[int, int], str] = {}
    for dtm in sorted(mars_dtm_tiles):
        for tile in mars_dtm_tiles[dtm]:
            if tile in owner:
                parent[find(dtm)] = find(owner[tile])
            else:
                owner[tile] = dtm
    groups: dict[str, list[str]] = {}
    for dtm in sorted(mars_dtm_tiles):
        groups.setdefault(find(dtm), []).append(dtm)
    mars_blocks: dict[str, list[tuple[int, int]]] = {}
    dtm_block: dict[str, str] = {}
    for members in groups.values():
        tiles = sorted({t for d in members for t in mars_dtm_tiles[d]})
        name = f"mars:tile{tiles[0][0]:+d}:{tiles[0][1]:d}"
        mars_blocks[name] = tiles
        for d in members:
            dtm_block[d] = name
    n_lon_mars = int(round(360.0 / config.mars_tile_deg))
    _assign_tiled("mars", mars_blocks, n_lon_mars, config, split, rng)

    # Earth: 2 x 2 degree blocks.
    earth: dict[str, list[tuple[int, int]]] = {}
    for b in sorted(set(earth_blocks)):
        _, lat_s, lon_s = b.split(":")
        i = int(round(float(lat_s) / config.earth_block_deg))
        j = int(round((float(lon_s) + 180.0) / config.earth_block_deg))
        earth[b] = [(i, j)]
    n_lon_earth = int(round(360.0 / config.earth_block_deg))
    _assign_tiled("earth", earth, n_lon_earth, config, split, rng)
    return split, dtm_block
