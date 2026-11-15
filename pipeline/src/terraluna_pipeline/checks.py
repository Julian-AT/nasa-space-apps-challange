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
    if args.part == "moon-distributions":
        return run_moon_distributions(root, arguments)
    pgda = Path(args.pgda_dir) if args.pgda_dir else root / "data" / "raw" / "pgda78"
    return run_scale_behaviour(root, pgda, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
