"""Scientific checks and Appendix A analyses (PRD 9, 22.3; T6-04, T6-07, T6-08).

checks.py records no gate itself: gate fragments go through ``gates.write_fragment`` when
gates.py is merged (CONTRACTS d). Every measured number in an analysis file comes from an
artifact named with its sha256 in ``input_hashes``; ``verify-records`` re-hashes them offline.

Engineering choices recorded in the outputs (the PRD fixes none of them):

- Block bootstrap: a 2-D moving-block bootstrap over the window index grid (window rows and
  columns at the stride), blocks of ``block`` x ``block`` windows drawn with replacement and
  tiled to the grid shape, statistic the median over non-missing cells, 95% percentile interval.
  Overlapping windows are correlated, so a block of 1 (independent windows) is never used for
  reported intervals (PRD 9).
- Breakover test: one line versus a continuous hinge (two slopes joined at a break) in
  ln nu - ln L, the break searched over the supported lags with at least three lags on each
  side including the break lag, criterion BIC (k = 2 for the line, 4 for the hinge: intercept,
  two slopes, break location), breakover when BIC(line) - BIC(hinge) > 10 (Kass and Raftery
  1995, "very strong"). Residual sums are floored at n * (1e-6)^2 so exact synthetic fits do not
  produce spurious differences. The Rosenburg et al. (2011) 1 km location is not used to choose
  the break; it is compared afterwards.
- Target percentile: the fraction of the body distribution at or below the target's value
  (``weak`` kind), times 100.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

EXIT_OK: Final = 0
EXIT_CHECK_FAILED: Final = 1
EXIT_BLOCKED: Final = 3

LOG_BASE: Final = "natural"
BIC_THRESHOLD: Final = 10.0
COMPARISON_BREAK_M: Final = 1000.0
"""The breakover location Rosenburg et al. (2011) report for LOLA. It is used only to decide
whether a breakover near it is testable (at least two supported lags beyond it) and for the
comparison afterwards, never to choose the break."""
MIN_LAGS_PER_SEGMENT: Final = 3
RSS_FLOOR_PER_POINT: Final = 1e-12
ROSENBURG_2011_MEDIAN_H: Final = {"highlands": 0.95, "maria": 0.76}
"""Median Hurst exponents for lunar highlands and maria reported by Rosenburg et al. (2011,
JGR 116, E02001, doi:10.1029/2010JE003716) from LOLA, as quoted in PRD 22.3 / T6-07."""

FloatArray = npt.NDArray[np.float64]


# --- Block bootstrap ---------------------------------------------------------------------------


def block_bootstrap_median(
    field: npt.ArrayLike, *, block: int, replicates: int, seed: int
) -> dict[str, Any]:
    """95% moving-block bootstrap interval of the median over a 2-D grid (NaN = missing)."""
    grid = np.asarray(field, dtype=np.float64)
    if grid.ndim != 2:
        raise ValueError("field must be 2-D")
    rows, cols = grid.shape
    b = max(1, min(int(block), rows, cols))
    rng = np.random.default_rng(seed)
    tiles_r, tiles_c = math.ceil(rows / b), math.ceil(cols / b)
    medians: list[float] = []
    for _ in range(int(replicates)):
        sample = np.empty((tiles_r * b, tiles_c * b))
        for tr in range(tiles_r):
            for tc in range(tiles_c):
                i = int(rng.integers(0, rows - b + 1))
                j = int(rng.integers(0, cols - b + 1))
                sample[tr * b : (tr + 1) * b, tc * b : (tc + 1) * b] = grid[i : i + b, j : j + b]
        values = sample[:rows, :cols]
        values = values[np.isfinite(values)]
        if values.size:
            medians.append(float(np.median(values)))
    result: dict[str, Any] = {
        "block": b,
        "replicates": int(replicates),
        "replicates_with_data": len(medians),
        "seed": int(seed),
        "level": 0.95,
        "method": "2-D moving-block bootstrap of the median, percentile interval",
    }
    if not medians:
        result.update(lo=None, hi=None)
    else:
        array = np.asarray(medians)
        result.update(lo=float(np.percentile(array, 2.5)), hi=float(np.percentile(array, 97.5)))
    return result


# --- Per-body distributions --------------------------------------------------------------------


def _summary(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "median": float(np.median(array)),
        "q25": float(np.percentile(array, 25.0)),
        "q75": float(np.percentile(array, 75.0)),
        "min": float(array.min()),
        "max": float(array.max()),
    }


def body_distribution(
    rows: Sequence[Mapping[str, float | None]],
    positions: Sequence[tuple[int, int]],
    *,
    block: int,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    """Distributions of each descriptor across windows: nu as natural log, H and valid fraction.

    ``rows[i]`` holds the descriptors of the window at grid position ``positions[i]``. A None
    value (unsupported lag, H unavailable) is counted in ``n_unavailable`` and left out; nothing
    is imputed.
    """
    if len(rows) != len(positions):
        raise ValueError("one position per row")
    names = sorted({name for row in rows for name in row})
    shape = (max(p[0] for p in positions) + 1, max(p[1] for p in positions) + 1)
    out: dict[str, Any] = {}
    for name in names:
        is_nu = ".nu_m@" in name
        key = f"log_{name}" if is_nu else name
        grid = np.full(shape, np.nan)
        values: list[float] = []
        missing = 0
        for row, (r, c) in zip(rows, positions, strict=True):
            value = row.get(name)
            if value is None:
                missing += 1
                continue
            v = math.log(value) if is_nu else float(value)
            values.append(v)
            grid[r, c] = v
        entry: dict[str, Any] = {"n": len(values), "n_unavailable": missing}
        if values:
            entry.update(_summary(values))
            entry["bootstrap_median"] = block_bootstrap_median(
                grid, block=block, replicates=replicates, seed=seed
            )
        else:
            entry.update(status="unavailable", reason="no_window_has_value")
        out[key] = entry
    return out


# --- Scale behaviour ---------------------------------------------------------------------------


def _fit_line(x: FloatArray, y: FloatArray) -> tuple[float, float, float]:
    design = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    rss = float(np.sum((y - design @ coef) ** 2))
    return float(coef[0]), float(coef[1]), rss


def _fit_hinge(x: FloatArray, y: FloatArray, xb: float) -> tuple[float, float, float, float]:
    design = np.column_stack([np.ones_like(x), x, np.maximum(0.0, x - xb)])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    rss = float(np.sum((y - design @ coef) ** 2))
    return float(coef[0]), float(coef[1]), float(coef[1] + coef[2]), rss


def _bic(rss: float, n: int, k: int) -> float:
    return n * math.log(max(rss, n * RSS_FLOOR_PER_POINT) / n) + k * math.log(n)


def breakover_test(lags_m: Sequence[float], nu_m: Sequence[float]) -> dict[str, Any]:
    """Line versus hinge in log-log space; see the module docstring for the declared procedure."""
    lags = np.asarray(lags_m, dtype=np.float64)
    nus = np.asarray(nu_m, dtype=np.float64)
    procedure = {
        "criterion": f"BIC(line) - BIC(hinge) > {BIC_THRESHOLD:g}",
        "min_lags_per_segment": MIN_LAGS_PER_SEGMENT,
        "log_base": LOG_BASE,
    }
    n = int(lags.size)
    candidates = range(MIN_LAGS_PER_SEGMENT - 1, n - MIN_LAGS_PER_SEGMENT + 1)
    beyond = int(np.count_nonzero(lags > COMPARISON_BREAK_M))
    if n < 2 * MIN_LAGS_PER_SEGMENT - 1 or not list(candidates) or beyond < 2:
        return {
            "status": "unavailable",
            "reason": "insufficient_lags_beyond_break",
            "breakover_detected": None,
            "n_lags": n,
            "n_lags_beyond_1km": beyond,
            "criterion": procedure["criterion"],
            **procedure,
        }
    x, y = np.log(lags), np.log(nus)
    _, slope, rss_line = _fit_line(x, y)
    best: tuple[float, int, float, float] | None = None
    for k in candidates:
        _, h1, h2, rss = _fit_hinge(x, y, float(x[k]))
        if best is None or rss < best[0]:
            best = (rss, k, h1, h2)
    assert best is not None
    rss_hinge, k, h1, h2 = best
    delta = _bic(rss_line, n, 2) - _bic(rss_hinge, n, 4)
    return {
        "status": "available",
        "reason": None,
        "n_lags": n,
        "lags_m": lags.tolist(),
        "single_slope_H": slope,
        "break_m": float(lags[k]),
        "H_below_break": h1,
        "H_above_break": h2,
        "delta_bic": delta,
        "breakover_detected": bool(delta > BIC_THRESHOLD),
        "n_lags_beyond_1km": beyond,
        "criterion": procedure["criterion"],
        **procedure,
    }


def rosenburg_side_by_side(h_value: float | None, *, label: str = "") -> dict[str, Any]:
    """Our H beside the Rosenburg et al. (2011) medians; no verdict, no wavelength correction."""
    return {
        "ours": h_value,
        "ours_label": label or "Derived",
        "rosenburg_2011_median_H": dict(ROSENBURG_2011_MEDIAN_H),
        "source": "Rosenburg et al. 2011, JGR Planets 116, E02001, doi:10.1029/2010JE003716",
        "note": (
            "Side by side only. Different baselines and surface types: Rosenburg et al. fit "
            "LOLA profile deviograms over their own baseline range per terrain type (global "
            "highlands and maria), we fit a 90 m grid deviogram over 270-1080 m lags in 5 km "
            "south-polar windows. No wavelength correction from H is applied (D11) and this "
            "comparison is no gate result."
        ),
    }


def target_percentile(
    value: float | None, distribution: Sequence[float], *, clone_values: Sequence[float]
) -> dict[str, Any]:
    """Empirical percentile (weak kind) of a target value, with the clone spread of it."""
    dist = np.asarray([v for v in distribution if v is not None], dtype=np.float64)
    if value is None or dist.size == 0:
        return {"status": "unavailable", "reason": "value_or_distribution_unavailable"}

    def pct(v: float) -> float:
        return float(100.0 * np.count_nonzero(dist <= v) / dist.size)

    result: dict[str, Any] = {
        "status": "available",
        "value": value,
        "percentile": pct(value),
        "n_distribution": int(dist.size),
        "kind": "weak (fraction at or below), x 100",
    }
    if clone_values:
        spread = np.asarray([pct(v) for v in clone_values])
        result["clone_spread"] = {
            "min": float(spread.min()),
            "median": float(np.median(spread)),
            "max": float(spread.max()),
            "n": int(spread.size),
        }
    else:
        result["clone_spread"] = None
    return result


def clone_percentile_spread(
    target_by_clone: Mapping[str, float | None],
    distribution_by_clone: Mapping[str, Sequence[float]],
) -> dict[str, Any] | None:
    """Spread of the target percentile when every window is re-measured on the same clone.

    For each clone, the target's value on that clone is ranked within the values of all windows
    on that same clone, so the clone moves the target and its background together. Clones where
    the target or the distribution is unavailable are skipped and counted.
    """
    values: list[float] = []
    skipped = 0
    for cid, value in target_by_clone.items():
        dist = np.asarray(distribution_by_clone.get(cid, ()), dtype=np.float64)
        if value is None or dist.size == 0:
            skipped += 1
            continue
        values.append(float(100.0 * np.count_nonzero(dist <= value) / dist.size))
    if not values:
        return None
    array = np.asarray(values)
    return {
        "min": float(array.min()),
        "median": float(np.median(array)),
        "max": float(array.max()),
        "n": int(array.size),
        "n_skipped": skipped,
        "method": "target re-ranked within all windows of the same clone",
    }


# --- G4 (T6-04): candidate table, masks, rank sensitivity, counterexample ---------------------

TERRAIN_FAMILIES: Final = ("terrain_relief", "terrain_texture")
G4_PARTS: Final = ("table", "masks", "distances_and_rank_sensitivity", "counterexample")
FORBIDDEN_CLAIMS: Final = ("analog confirmed", "valid analog", "probability")


def _clone_summary(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    lo, hi = float(np.percentile(array, 2.5)), float(np.percentile(array, 97.5))
    return {
        "min": float(array.min()),
        "p2_5": lo,
        "median": float(np.median(array)),
        "p97_5": hi,
        "max": float(array.max()),
        "n": int(array.size),
        "spread_half_width_95": (hi - lo) / 2.0,
    }


def g4_rank_sensitivity(
    base_target: Mapping[str, Any],
    clone_targets: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Terrain distances of every candidate under the base target and under each clone.

    Distances come from ``matching.terrain_distance`` and Pareto layers from
    ``matching.pareto_layers``; ties from ``matching.tie_groups`` with each candidate's spread
    = half the width of its clone 2.5-97.5 percentile range (measurement error only; an
    engineering choice). Families are never combined. A candidate whose distance is unavailable
    in a run is absent from that run's ranking, never ranked last.
    """
    from terraluna_pipeline import matching

    def distances(target: Mapping[str, Any]) -> dict[str, dict[str, float | None]]:
        out: dict[str, dict[str, float | None]] = {}
        for cid, variants in candidates.items():
            out[cid] = {
                family: matching.terrain_distance(target[family], variants[family]).value
                for family in TERRAIN_FAMILIES
            }
        return out

    def ranking(table: Mapping[str, Mapping[str, float | None]]) -> dict[str, Any]:
        complete = {
            cid: [float(d[f]) for f in TERRAIN_FAMILIES]  # type: ignore[arg-type]
            for cid, d in table.items()
            if all(d[f] is not None for f in TERRAIN_FAMILIES)
        }
        layers = matching.pareto_layers(complete)
        layer_of = {cid: i + 1 for i, layer in enumerate(layers) for cid in layer}
        rank1 = {}
        for family in TERRAIN_FAMILIES:
            values = {cid: v for cid, d in table.items() if (v := d[family]) is not None}
            best = min(values.values()) if values else None
            rank1[family] = {cid for cid, v in values.items() if v == best}
        return {"layer": layer_of, "rank1": rank1}

    base = distances(base_target)
    base_rank = ranking(base)
    runs = {cid: distances(target) for cid, target in clone_targets.items()}
    run_ranks = [ranking(table) for table in runs.values()]
    n = len(run_ranks)
    rows: dict[str, Any] = {}
    for cid in sorted(candidates):
        row: dict[str, Any] = {"distances": {}, "base_pareto_layer": base_rank["layer"].get(cid)}
        for family in TERRAIN_FAMILIES:
            clone_values = [v for t in runs.values() if (v := t[cid][family]) is not None]
            entry: dict[str, Any] = {"base": base[cid][family], "unit": "RMS of log nu"}
            if clone_values:
                entry.update(_clone_summary([float(v) for v in clone_values]))
            else:
                entry.update(spread_half_width_95=None, n=0)
            row["distances"][family] = entry
        if n:
            first = sum(1 for r in run_ranks if r["layer"].get(cid) == 1)
            row["rank_frequency"] = {
                "first_pareto_layer": {"frequency": first / n, "count": first, "n": n},
                **{
                    f"rank_1_{family}": {
                        "frequency": sum(1 for r in run_ranks if cid in r["rank1"][family]) / n,
                        "count": sum(1 for r in run_ranks if cid in r["rank1"][family]),
                        "n": n,
                    }
                    for family in TERRAIN_FAMILIES
                },
            }
        else:
            row["rank_frequency"] = None
        rows[cid] = row
    ties: dict[str, Any] = {}
    for family in TERRAIN_FAMILIES:
        result = matching.tie_groups(
            family,  # type: ignore[arg-type]
            {cid: rows[cid]["distances"][family]["base"] for cid in rows},
            {cid: rows[cid]["distances"][family]["spread_half_width_95"] for cid in rows},
        )
        ties[family] = {
            "groups": [
                {
                    "group_id": g.group_id,
                    "members": list(g.members),
                    "not_pairwise_tied": [list(p) for p in g.not_pairwise_tied],
                }
                for g in result.groups
            ],
            "by_window": {
                cid: {"tied": m.tied, "tie_group": m.tie_group, "tie_reason": m.tie_reason}
                for cid, m in result.by_window.items()
            },
        }
    return {"n_runs": n, "candidates": rows, "ties": ties}


MASK_SCENARIO_THRESHOLDS: Final = (0.95, 1.0)
"""Stricter WorldCover allowed-fraction thresholds tried as named scenarios (choice)."""


def mask_scenarios(
    table: Sequence[Mapping[str, Any]], *, base_threshold: float
) -> list[dict[str, Any]]:
    """Named mask and region scenarios over the eligible candidates: who stays, first layer.

    Only filters that the committed table can apply are run (no re-measurement); no
    probability or weight is attached.
    """
    from terraluna_pipeline import matching

    def first_layer(rows: Sequence[Mapping[str, Any]]) -> list[str]:
        points = {
            r["window_id"]: [float(r[f]["base"]) for f in TERRAIN_FAMILIES]
            for r in rows
            if all(r[f]["base"] is not None for f in TERRAIN_FAMILIES)
        }
        layers = matching.pareto_layers(points)
        return list(layers[0]) if layers else []

    out = [
        {
            "name": "pipeline",
            "change": f"WorldCover allowed fraction >= {base_threshold:g} (config), both regions",
            "n_eligible": len(table),
            "first_pareto_layer": first_layer(table),
        }
    ]
    for threshold in MASK_SCENARIO_THRESHOLDS:
        rows = [
            r
            for r in table
            if (r["valid_fraction_by_observable"].get("worldcover_allowed") or 0.0) >= threshold
        ]
        out.append(
            {
                "name": f"worldcover_allowed_ge_{threshold:g}",
                "change": (
                    f"WorldCover allowed fraction >= {threshold:g} instead of {base_threshold:g}"
                ),
                "n_eligible": len(rows),
                "first_pareto_layer": first_layer(rows),
            }
        )
    for region in sorted({str(r["search_region_id"]) for r in table}):
        rows = [r for r in table if str(r["search_region_id"]) == region]
        out.append(
            {
                "name": f"region_{region}_only",
                "change": f"search only the {region} region",
                "n_eligible": len(rows),
                "first_pareto_layer": first_layer(rows),
            }
        )
    return out


def g4_status(parts: Mapping[str, bool], *, t5_outputs: bool) -> tuple[str, list[str]]:
    """PASS needs all four parts; a missing part gives PARTIAL naming it; no T5 outputs NOT_RUN."""
    if not t5_outputs:
        return "NOT_RUN", ["t5_outputs"]
    missing = [part for part in G4_PARTS if not parts.get(part)]
    return ("PASS" if not missing else "PARTIAL"), missing


def split_windows(entries: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Eligible window ids, and the rejected or unknown windows with reasons (never ranked)."""
    eligible: list[str] = []
    excluded: list[dict[str, Any]] = []
    for entry in entries:
        window_id = str(entry["window"]["window_id"])
        if entry["status"] == "eligible":
            eligible.append(window_id)
        else:
            excluded.append(
                {
                    "window_id": window_id,
                    "status": entry["status"],
                    "reasons": list(entry["reasons"]),
                }
            )
    return sorted(eligible), sorted(excluded, key=lambda item: item["window_id"])


COUNTEREXAMPLE_PATH: Final = "analysis/g4_counterexample.json"


def counterexample_ok(root: Path) -> tuple[bool, str | None]:
    """Whether the analyst's counterexample file exists and every inspected path exists."""
    path = root / COUNTEREXAMPLE_PATH
    if not path.is_file():
        return False, f"{COUNTEREXAMPLE_PATH} is missing"
    record = json.loads(path.read_text(encoding="utf-8"))
    for key in ("candidate_id", "inspected", "artifact_paths"):
        if key not in record:
            return False, f"{COUNTEREXAMPLE_PATH} lacks {key}"
    missing = [p for p in record["artifact_paths"] if not (root / p).exists()]
    if missing:
        return False, f"inspected artifacts missing: {', '.join(missing)}"
    return True, None


def _target_variants(entry: Mapping[str, Any]) -> dict[str, Any]:
    from terraluna_pipeline.schema import TerrainVariant

    terrain = entry["terrain"]["terrain"]
    return {
        "terrain_relief": TerrainVariant.model_validate(terrain["relief"]),
        "terrain_texture": TerrainVariant.model_validate(terrain["texture"]),
    }


def _clone_variants(
    base: Mapping[str, Any], per_clone: Mapping[str, Mapping[str, float | None]]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cid, values in per_clone.items():
        variants = {}
        for family, prefix in (("terrain_relief", "relief"), ("terrain_texture", "texture")):
            variant = base[family]
            nu = {lag: values.get(f"{prefix}.nu_m@{lag}") for lag in variant.nu_m}
            variants[family] = variant.model_copy(update={"nu_m": nu})
        out[cid] = variants
    return out


def run_g4(root: Path, target_id: str, argv: Sequence[str]) -> int:
    """analysis/g4_candidates.json and the G4 fragment from the real search outputs."""
    from terraluna_pipeline import gates
    from terraluna_pipeline.config import load_run_config
    from terraluna_pipeline.provenance import command_line, config_version, run_id
    from terraluna_pipeline.schema import GateRecord

    config_path = root / "config" / "run.yaml"
    targets_path = root / "outputs" / "search" / "targets.json"
    comparisons_path = root / "outputs" / "comparisons" / f"{target_id}.json"
    earth_path = root / "data" / "processed" / "windows" / "earth.json"
    moon_path = root / "data" / "processed" / "windows" / "moon.json"
    needed = [targets_path, comparisons_path, earth_path, moon_path]
    missing = [str(p.relative_to(root)) for p in needed if not p.is_file()]
    if missing:
        print(f"blocked: missing T5 outputs {', '.join(missing)}; G4 stays NOT_RUN")
        return EXIT_BLOCKED
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    target = next((t for t in targets if t["target_id"] == target_id), None)
    if target is None:
        print(f"blocked: {target_id} not in outputs/search/targets.json")
        return EXIT_BLOCKED
    primary = target["primary_window_id"]
    clones_path = root / "outputs" / "uncertainty" / f"clones_{primary}.json"
    if not clones_path.is_file():
        print(f"blocked: {clones_path.relative_to(root)} (run uncertainty run-clones)")
        return EXIT_BLOCKED
    comparisons = json.loads(comparisons_path.read_text(encoding="utf-8"))
    earth = json.loads(earth_path.read_text(encoding="utf-8"))
    moon = json.loads(moon_path.read_text(encoding="utf-8"))
    clones = json.loads(clones_path.read_text(encoding="utf-8"))
    moon_entry = next(e for e in moon if e["window"]["window_id"] == primary)
    base_target = _target_variants(moon_entry)
    spread = clones["by_isotropic_rule"][PRIMARY_RULE]
    clone_targets = _clone_variants(base_target, spread["per_clone"])
    eligible, excluded = split_windows(earth)
    by_id = {e["window"]["window_id"]: e for e in earth}
    candidates = {wid: _target_variants(by_id[wid]) for wid in eligible}
    sensitivity = g4_rank_sensitivity(base_target, clone_targets, candidates)

    # Consistency: the base distances recomputed here must equal the committed comparisons.
    mismatches = []
    compared = {r["earth_window_id"]: r for r in comparisons}
    for wid, row in sensitivity["candidates"].items():
        record = compared.get(wid)
        for family in TERRAIN_FAMILIES:
            committed = None if record is None else record["distances_by_family"][family]["value"]
            if committed != row["distances"][family]["base"]:
                mismatches.append(f"{wid}:{family}")
    if mismatches:
        print(f"check failed: base distances differ from {comparisons_path.name}: {mismatches[:5]}")
        return EXIT_CHECK_FAILED

    run = load_run_config(config_path)
    masks_cfg = run.value("earth_search.masks")
    regions: dict[str, dict[str, Any]] = {}
    for entry in earth:
        region = entry["window"]["environment_flags"].get("search_region_id") or "unknown"
        counts = regions.setdefault(
            region, {"generated": 0, "eligible": 0, "rejected_by_reason": {}}
        )
        counts["generated"] += 1
        if entry["status"] == "eligible":
            counts["eligible"] += 1
        for reason in entry["reasons"]:
            counts["rejected_by_reason"][reason] = counts["rejected_by_reason"].get(reason, 0) + 1
    table = []
    for wid in eligible:
        entry = by_id[wid]
        row = sensitivity["candidates"][wid]
        window = entry["window"]
        table.append(
            {
                "window_id": wid,
                "body": window["body"],
                "area_m2": window["area_m2"],
                "search_region_id": window["environment_flags"].get("search_region_id"),
                "valid_fraction_by_observable": window["valid_fraction_by_observable"],
                "eligibility": entry["status"],
                "exclusion_reasons": entry["reasons"],
                "radar_cpr": {
                    "status": "unavailable",
                    "reason": "shipping_mode_c_radar_not_completed",
                    "value": None,
                },
                "terrain_relief": row["distances"]["terrain_relief"],
                "terrain_texture": row["distances"]["terrain_texture"],
                "base_pareto_layer": row["base_pareto_layer"],
                "rank_frequency": row["rank_frequency"],
                "ties": {f: sensitivity["ties"][f]["by_window"].get(wid) for f in TERRAIN_FAMILIES},
            }
        )
    shortlist = target.get("shortlist") or {}
    scenarios = mask_scenarios(table, base_threshold=float(masks_cfg["min_allowed_fraction"]))  # type: ignore[index]
    counter_ok, counter_reason = counterexample_ok(root)
    parts = {
        "table": bool(table) or True,
        "masks": True,
        "distances_and_rank_sensitivity": sensitivity["n_runs"] > 0,
        "counterexample": counter_ok,
    }
    status, missing_parts = g4_status(parts, t5_outputs=True)
    committed_inputs = [config_path, targets_path, comparisons_path, clones_path]
    if counter_ok:
        committed_inputs.append(root / COUNTEREXAMPLE_PATH)
    evidence = {
        "record_type": "terraluna G4 candidate table and rank sensitivity",
        "evidence_label": "Derived",
        "command": command_line("terraluna_pipeline.checks", argv),
        "config_version": config_version(config_path),
        "input_hashes": _hash_entries(root, committed_inputs),
        "uncommitted_inputs_sha256": {
            str(p.relative_to(root)): _sha256(p) for p in (earth_path, moon_path)
        },
        "target_id": target_id,
        "target_window_id": primary,
        "shipping_mode": run.value("run.shipping_mode"),
        "scope": (
            "Slice search over the configured Earth regions (outputs/search/targets.json "
            f"n_compared = {shortlist.get('n_compared')}), terrain families only (Mode C). "
            "Not the full T5 search universe."
        ),
        "sort_rule": shortlist.get("rule"),
        "sort_note": (
            "No single order: Pareto layers over terrain_relief and terrain_texture, then "
            "window_id; no family sorts first (ANA-03 undecided). No composite exists."
        ),
        "shortlist_earth_window_ids": shortlist.get("earth_window_ids"),
        "masks": {
            "worldcover": {
                "product": "ESA WorldCover v200, 2021 (WorldCover/esa-worldcover/v200/2021/map)",
                "allowed_classes": masks_cfg["worldcover_allowed"],  # type: ignore[index]
                "min_allowed_fraction": masks_cfg["min_allowed_fraction"],  # type: ignore[index]
                "water_rule": masks_cfg["water"],  # type: ignore[index]
                "sampling": "nearest class at each 90 m cell centre",
            },
            "terrain_valid_fraction_min": run.value("terrain.min_valid_fraction"),
            "permanent_shadow": {
                "status": "unavailable",
                "reason": "G0-10 permanent-shadow product undecided",
            },
            "counts_by_region": dict(sorted(regions.items())),
        },
        "candidates": table,
        "excluded_windows_never_ranked": excluded,
        "ties": sensitivity["ties"],
        "tie_rule": (
            "matching.tie_groups: |d_a - d_b| < s_a + s_b, s = half width of the candidate's "
            "PGDA78 clone 2.5-97.5 percentile distance range (measurement error only)"
        ),
        "rank_sensitivity": {
            "runs": "PGDA78 Site01 clones of the target window, each re-measured through D29 and "
            "T3; Earth windows held fixed (no Earth error model)",
            "n_runs": sensitivity["n_runs"],
            "n_requested": spread["n_requested"],
            "wording": "rank stability is a frequency over clone runs, never a probability",
        },
        "scenario_sensitivity": scenarios,
        "clone_limits": spread["note"],
        "counterexample": {"present": counter_ok, "reason": counter_reason},
        "g4_parts": parts,
        "status": status,
        "missing_parts": missing_parts,
    }
    evidence_path = root / "analysis" / "g4_candidates.json"
    _write(evidence_path, evidence)
    n_first = sum(1 for r in table if r["base_pareto_layer"] == 1)
    stable = [
        r["window_id"]
        for r in table
        if r["rank_frequency"] and r["rank_frequency"]["first_pareto_layer"]["frequency"] == 1.0
    ]
    product_pairs = (
        [(pid, None) for pid in sorted(comparisons[0]["source_ids"])] if comparisons else []
    )
    record = GateRecord.model_validate(
        {
            "gate_id": "G4",
            "status": status,
            "evidence_path": "analysis/g4_candidates.json",
            "run_id": run_id(config_path, product_pairs),
            "scope": (
                f"{target_id} ({primary}) against {len(table)} eligible Earth windows of the slice "
                "search; terrain families only (Mode C)"
            ),
            "result": (
                f"{len(table)} candidates, {len(excluded)} excluded windows listed; first Pareto "
                f"layer {n_first} window(s); {len(stable)} window(s) in the first layer in all "
                f"{sensitivity['n_runs']} clone runs"
            ),
            "consequence": None
            if status == "PASS"
            else f"G4 {status}: missing {', '.join(missing_parts)}; no candidate claim beyond "
            "the terrain-only slice",
            "command": evidence["command"],
            "config_version": evidence["config_version"],
            "input_hashes": _hash_entries(root, [*committed_inputs, evidence_path]),
        }
    )
    gates.write_fragment(record, root=root)
    gates.merge(root)
    return EXIT_OK


# --- verify-records ----------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify_records(root: Path) -> list[str]:
    """Problems found in analysis/*.json under ``root``; empty when every record verifies."""
    problems: list[str] = []
    directory = root / "analysis"
    if not directory.is_dir():
        return [f"{directory} is missing"]
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{path.name}: unreadable ({error})")
            continue
        if not isinstance(record, dict):
            problems.append(f"{path.name}: not a JSON object")
            continue
        if record.get("mock") is True:
            problems.append(f"{path.name}: mock record")
        hashes = record.get("input_hashes")
        if not isinstance(hashes, list):
            problems.append(f"{path.name}: no input_hashes list")
            continue
        for item in hashes:
            target = root / str(item.get("path"))
            if not target.is_file():
                problems.append(f"{path.name}: input {item.get('path')} is missing")
            elif _sha256(target) != item.get("sha256"):
                problems.append(f"{path.name}: input {item.get('path')} changed")
    return problems


# --- Appendix A runners -----------------------------------------------------------------------

PRIMARY_RULE: Final = "direction_mean"
BOOTSTRAP_BLOCK: Final = 2
"""Two windows at the 2.5 km stride = one 5 km window width, the overlap length (PRD 9)."""
BOOTSTRAP_REPLICATES: Final = 2000
BOOTSTRAP_SEED: Final = 20261115
SCALE_LAGS_M: Final = (
    10.0,
    20.0,
    45.0,
    90.0,
    135.0,
    180.0,
    270.0,
    360.0,
    540.0,
    720.0,
    1080.0,
    1440.0,
    2160.0,
    2880.0,
    4320.0,
)
"""Deviogram lags on the native 5 m PGDA78 grid (an engineering choice: multiples of 5 m
spanning the 90 m analysis grid to beyond 1 km on both sides)."""


def _write(path: Path, record: Mapping[str, Any]) -> None:
    from terraluna_pipeline.export import canonical_json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(dict(record)))


def _hash_entries(root: Path, paths: Sequence[Path]) -> list[dict[str, str]]:
    entries = []
    for path in paths:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        entries.append({"path": rel, "sha256": _sha256(path)})
    return sorted(entries, key=lambda item: item["path"])


def _clone_records(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    directory = root / "outputs" / "uncertainty"
    records = []
    for path in sorted(directory.glob("clones_*.json")):
        records.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return records


def run_moon_distributions(root: Path, argv: Sequence[str]) -> int:
    """analysis/a1_body_distributions.json and a1_target_percentiles.json from real artifacts."""
    from terraluna_pipeline.config import load_run_config
    from terraluna_pipeline.provenance import command_line, config_version

    windows_path = root / "outputs" / "g0" / "windows-evidence.json"
    config_path = root / "config" / "run.yaml"
    records = _clone_records(root)
    if not records or not windows_path.is_file():
        print("blocked: outputs/uncertainty/clones_*.json or outputs/g0/windows-evidence.json")
        return EXIT_BLOCKED
    evidence = json.loads(windows_path.read_text(encoding="utf-8"))["g0_02_moon"]
    position = {w["window_id"]: (int(w["iy"]), int(w["ix"])) for w in evidence["windows"]}
    valid = {w["window_id"]: float(w["dem_valid_fraction"]) for w in evidence["windows"]}
    rows: list[dict[str, float | None]] = []
    positions: list[tuple[int, int]] = []
    per_window: dict[str, dict[str, Any]] = {}
    for _, record in records:
        window_id = record["window_id"]
        spread = record["by_isotropic_rule"][PRIMARY_RULE]
        row: dict[str, float | None] = {
            name: entry["base"] for name, entry in spread["descriptors"].items()
        }
        row["valid_fraction"] = valid[window_id]
        rows.append(row)
        positions.append(position[window_id])
        per_window[window_id] = spread
    moon = body_distribution(
        rows, positions, block=BOOTSTRAP_BLOCK, replicates=BOOTSTRAP_REPLICATES, seed=BOOTSTRAP_SEED
    )
    run_config = load_run_config(config_path)
    target_id = str(run_config.value("targets.moon.primary_window_id"))
    if target_id not in per_window:
        print(f"blocked: no clone record for the primary window {target_id}")
        return EXIT_BLOCKED
    inputs = _hash_entries(root, [windows_path, config_path, *(p for p, _ in records)])
    common = {
        "command": command_line("terraluna_pipeline.checks", argv),
        "config_version": config_version(config_path),
        "input_hashes": inputs,
        "isotropic_rule": PRIMARY_RULE,
        "log_base": LOG_BASE,
        "clone_limits": per_window[target_id]["note"],
    }
    _write(
        root / "analysis" / "a1_body_distributions.json",
        {
            **common,
            "record_type": "terraluna appendix A per-body fingerprint distributions",
            "evidence_label": "Derived",
            "bodies": {
                "moon": {
                    "status": "available",
                    "scope": (
                        f"PGDA78 Site01 (Connecting Ridge) 5 km windows only, n = {len(rows)}, "
                        "2.5 km stride, so neighbouring windows overlap by half; not a body-wide "
                        "lunar distribution"
                    ),
                    "descriptors": moon,
                },
                "earth": {"status": "not_run", "reason": "t5_search_outputs_not_available"},
                "mars": {"status": "not_run", "reason": "t5_search_outputs_not_available"},
            },
            "bootstrap": {
                "block_windows": BOOTSTRAP_BLOCK,
                "replicates": BOOTSTRAP_REPLICATES,
                "seed": BOOTSTRAP_SEED,
                "note": "block = 2 windows at the 2.5 km stride, one window width (choice)",
            },
        },
    )
    target = per_window[target_id]
    percentiles: dict[str, Any] = {}
    clone_ids = list(target["per_clone"])
    for name, entry in target["descriptors"].items():
        transform = math.log if ".nu_m@" in name else float
        distribution = [transform(v) for row in rows if (v := row.get(name)) is not None]
        result = target_percentile(
            None if entry["base"] is None else transform(entry["base"]),
            distribution,
            clone_values=[],
        )
        if result["status"] == "available":
            result["clone_spread"] = clone_percentile_spread(
                {
                    cid: (
                        None if (v := target["per_clone"][cid].get(name)) is None else transform(v)
                    )
                    for cid in clone_ids
                },
                {
                    cid: [
                        transform(v)
                        for spread in per_window.values()
                        if (v := spread["per_clone"].get(cid, {}).get(name)) is not None
                    ]
                    for cid in clone_ids
                },
            )
        percentiles[name] = result
    _write(
        root / "analysis" / "a1_target_percentiles.json",
        {
            **common,
            "record_type": "terraluna appendix A target percentiles",
            "evidence_label": "Derived",
            "targets": {
                target_id: {
                    "body": "moon",
                    "distribution": "PGDA78 Site01 windows (the target is one of them)",
                    "n_clones_used": target["n_used"],
                    "n_clones_requested": target["n_requested"],
                    "percentiles": percentiles,
                }
            },
            "not_run": {
                "earth_and_mars_distributions": "t5_search_outputs_not_available",
                "mars_targets": "no Mars primary window frozen (config targets.mars)",
            },
        },
    )
    return EXIT_OK


def run_scale_behaviour(root: Path, pgda_dir: Path, argv: Sequence[str]) -> int:
    """analysis/a1_scale_behaviour.json: native 5 m Site01 deviograms and the breakover test."""
    from terraluna_pipeline import io
    from terraluna_pipeline.provenance import command_line, config_version
    from terraluna_pipeline.terrain import structure_function

    surf = pgda_dir / "Site01" / "Site01_final_adj_5mpp_surf.tif"
    ldec = pgda_dir / "Site01" / "Site01_final_adj_5mpp_ldec.tif"
    config_path = root / "config" / "run.yaml"
    records = _clone_records(root)
    missing = [str(p) for p in (surf, ldec) if not p.is_file()]
    if missing or not records:
        print(f"blocked: missing {', '.join(missing) or 'outputs/uncertainty/clones_*.json'}")
        return EXIT_BLOCKED
    z = io.read_geotiff_window(surf).array.astype(np.float64)
    counts = io.read_geotiff_window(ldec).array
    finite = np.isfinite(z)
    variants: dict[str, Any] = {}
    for name, mask in (
        ("all_cells", finite),
        ("observed_cells_only", finite & (np.asarray(counts) > 0)),
    ):
        sf = structure_function(
            np.where(mask, z, 0.0),
            valid=mask,
            spacing_m=5.0,
            lags_m=SCALE_LAGS_M,
            isotropic_rule=PRIMARY_RULE,
        )
        kept = [item for item in sf.isotropic if item.nu_m is not None]
        lags = [item.lag_m for item in kept]
        nus = [float(item.nu_m) for item in kept if item.nu_m is not None]
        pairs = [item.pair_count for item in kept]
        fit_from = [(lag, nu) for lag, nu in zip(lags, nus, strict=True) if lag >= 90.0]
        variants[name] = {
            "cells": int(np.count_nonzero(mask)),
            "deviogram": [
                {"lag_m": lag, "nu_m": nu, "pair_count": count}
                for lag, nu, count in zip(lags, nus, pairs, strict=True)
            ],
            "breakover_test_lags_ge_90m": breakover_test(
                [lag for lag, _ in fit_from], [nu for _, nu in fit_from]
            ),
            "breakover_test_all_lags": breakover_test(lags, nus),
        }
    target_rows = {}
    for _, record in records:
        spread = record["by_isotropic_rule"][PRIMARY_RULE]["descriptors"]
        lag_names = sorted(
            (float(name.split("@")[1]), name) for name in spread if name.startswith("relief.nu_m@")
        )
        lags = [lag for lag, name in lag_names if spread[name]["base"] is not None]
        nus = [spread[name]["base"] for _, name in lag_names if spread[name]["base"] is not None]
        target_rows[record["window_id"]] = {
            "breakover_test": breakover_test(lags, nus),
            "relief_H": spread["relief.H"]["base"],
            "relief_H_clone_p2_5_p97_5": [spread["relief.H"]["p2_5"], spread["relief.H"]["p97_5"]],
            "rosenburg_side_by_side": rosenburg_side_by_side(spread["relief.H"]["base"]),
        }
    whole_h = variants["all_cells"]["breakover_test_lags_ge_90m"].get("single_slope_H")
    _write(
        root / "analysis" / "a1_scale_behaviour.json",
        {
            "record_type": "terraluna appendix A scale behaviour",
            "evidence_label": "Derived",
            "command": command_line("terraluna_pipeline.checks", argv),
            "config_version": config_version(config_path),
            "input_hashes": _hash_entries(root, [config_path, *(p for p, _ in records)]),
            "raw_inputs_sha256": {surf.name: _sha256(surf), ldec.name: _sha256(ldec)},
            "isotropic_rule": PRIMARY_RULE,
            "site": "PGDA78 Site01 (Connecting Ridge), whole 16 x 16 km product at native 5 m",
            "caveat": (
                "The 5 m surface fills pixels without LOLA returns by interpolation (README); "
                "only about 10% of 5 m pixels hold returns, so short lags of 'all_cells' are "
                "shaped by the interpolation. 'observed_cells_only' uses pairs of cells that "
                "both hold returns (ldec > 0)."
            ),
            "site_deviograms": variants,
            "site_rosenburg_side_by_side": rosenburg_side_by_side(
                whole_h, label="Derived (single-slope H over 90 m to 4.32 km lags, all cells)"
            ),
            "windows_90m": target_rows,
        },
    )
    return EXIT_OK


# --- CLI ---------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checks CLI."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="python -m terraluna_pipeline.checks")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-records", help="re-hash analysis/*.json inputs (offline)")
    verify.add_argument("--root", default=".")
    g4 = sub.add_parser("g4", help="G4 candidate table and rank sensitivity")
    g4.add_argument("--target-id", default="moon-cr-site01-w000")
    g4.add_argument("--root", default=".")
    analysis = sub.add_parser("analysis", help="Appendix A analyses")
    analysis.add_argument("part", choices=["moon-distributions", "scale-behaviour"])
    analysis.add_argument("--pgda-dir", default=None)
    analysis.add_argument("--root", default=".")
    args = parser.parse_args(arguments)
    if args.command == "verify-records":
        problems = verify_records(Path(args.root))
        for problem in problems:
            print(problem)
        return EXIT_CHECK_FAILED if problems else EXIT_OK
    root = Path(args.root)
    if args.command == "g4":
        return run_g4(root, args.target_id, arguments)
    if args.part == "moon-distributions":
        return run_moon_distributions(root, arguments)
    pgda = Path(args.pgda_dir) if args.pgda_dir else root / "data" / "raw" / "pgda78"
    return run_scale_behaviour(root, pgda, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
