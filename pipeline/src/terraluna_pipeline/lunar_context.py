"""G3 further checks: lunar shadow behaviour and impact-melt context ranges (PRD 9; T6-06).

Two qualitative, context-only checks on the 25 Site01 windows, from the Mini-RF south-polar
mosaics (PDS LROMRF_0005, ``lsz_xxxxx_3cp_piu_90s000_v1`` CPR and ``lsz_xxxxx_3s1_piu_90s000_v1``
S1, combined East and West looking):

- Shadow (PRD 9, Fassett et al. 2024, doi 10.3847/PSJ/ad0a61): shadowed and sunlit windows are
  compared qualitatively, per group median and IQR of window CPR, S1 and valid fraction. No pass
  or fail and no equivalence claim. Fassett et al. correct topographically; these mosaics are
  orthorectified onto the LOLA base map by their producers but no topographic (incidence)
  correction is applied here, and no per-pixel incidence is recoverable, so the comparison is not
  corrected like theirs. The split needs the permanent-shadow fraction per window, which needs
  decision G0-10; while no product is named the split is ``Unavailable`` naming G0-10.
- Impact melt (Carter et al. 2012): the share of valid CPR pixels inside the closed range
  0.7 to 1.0 per window, labelled Context, Derived. CPR above 1 stays valid and is counted. There
  is no pass mark and G3 does not depend on the count.

Fassett delta-CPR and delta-S1 layers are lunar context only and never enter a distance (D13);
:func:`fassett_layer_use` refuses that use. Their definitions have not been read for this
record, so no Fassett layer is used here at all.

CPR in these mosaics is the product's own ratio (SC / OC); nothing is converted. Pixel validity is
:func:`terraluna_pipeline.quality.validity_mask` with the label's CORE_NULL; the label's
saturation codes are counted as ``isis_saturation``. A window pixel belongs to a window when its
centre lies in the window's half-open square ``[x0, x0 + size) x [y0, y0 + size)``.

CLI: ``python -m terraluna_pipeline.lunar_context run | check`` (exit 0 done, 1 check failed,
2 usage, 3 blocked by an unreadable mosaic, printed as ``blocked: ...``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from terraluna_pipeline import provenance
from terraluna_pipeline.holuhraun import (
    CONFIG_REL,
    G3_REL,
    PROTOCOL_REL,
    REPO_ROOT,
    REPRODUCTION_REL,
    _sha256,
    _write,
    g3_status,
)

SHADOW_REL: Final = "analysis/g3_shadow.json"
IMPACT_MELT_REL: Final = "analysis/g3_impact_melt_context.json"
WINDOWS_EVIDENCE_REL: Final = "outputs/g0/windows-evidence.json"

CARTER_RANGE: Final = (0.7, 1.0)
CARTER_SOURCE: Final = (
    "Carter et al. 2012, Mini-RF observations of lunar impact melts and ejecta flows, "
    "JGR Planets (PRD 9, R/B6): fresh impact melts typically have CPR 0.7 to 1.0"
)
FASSETT_SOURCE: Final = (
    "Fassett et al. 2024, Mini-RF orthorectification and empirical reduction, "
    "doi 10.3847/PSJ/ad0a61 (PRD A3, D13)"
)

MOSAIC_BASE: Final = (
    "https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-4-cdr-v1/lromrf_0005/data/sar/mosaics/"
)
CPR_IMAGE: Final = "lsz_xxxxx_3cp_piu_90s000_v1.img"
S1_IMAGE: Final = "lsz_xxxxx_3s1_piu_90s000_v1.img"
MOSAIC_LINES: Final = 10346
MOSAIC_SAMPLES: Final = 10346
LINE_BYTES: Final = 41384

FloatArray = npt.NDArray[np.float64]


class MosaicUnreadable(RuntimeError):
    """The Mini-RF mosaic could not be read (network or layout)."""


# --- per-window statistics ---------------------------------------------------------------------


def window_stats(
    window_id: str, cpr_valid: npt.ArrayLike, s1_valid: npt.ArrayLike, n_pixels: int
) -> dict[str, Any]:
    """Summary of one window from its valid CPR and S1 pixel values.

    A window without a valid value has ``None`` medians, never 0. CPR above 1 is kept.
    """
    cpr = np.asarray(cpr_valid, dtype=np.float64).ravel()
    s1 = np.asarray(s1_valid, dtype=np.float64).ravel()
    low, high = CARTER_RANGE
    return {
        "window_id": window_id,
        "n_pixels": int(n_pixels),
        "n_valid_cpr": int(cpr.size),
        "n_valid_s1": int(s1.size),
        "valid_fraction": float(cpr.size / n_pixels) if n_pixels else 0.0,
        "cpr_median": float(np.median(cpr)) if cpr.size else None,
        "s1_median": float(np.median(s1)) if s1.size else None,
        "n_cpr_in_context_range": int(np.count_nonzero((cpr >= low) & (cpr <= high))),
        "n_cpr_above_1": int(np.count_nonzero(cpr > 1.0)),
    }


# --- shadow ------------------------------------------------------------------------------------


def split_by_shadow(
    windows: Sequence[Mapping[str, Any]], *, split_fraction: float
) -> dict[str, list[Mapping[str, Any]]]:
    """Shadowed (fraction >= split_fraction), sunlit (below) and unknown (no fraction).

    The split fraction is the caller's recorded engineering choice; a window with an unknown
    shadow fraction goes to ``unknown``, never to ``sunlit``.
    """
    if not 0.0 < split_fraction <= 1.0:
        raise ValueError(f"split_fraction must be in (0, 1], got {split_fraction!r}")
    groups: dict[str, list[Mapping[str, Any]]] = {"shadowed": [], "sunlit": [], "unknown": []}
    for window in windows:
        fraction = window.get("shadow_fraction")
        if fraction is None:
            groups["unknown"].append(window)
        elif float(fraction) >= split_fraction:
            groups["shadowed"].append(window)
        else:
            groups["sunlit"].append(window)
    return groups


def _spread(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "q25": None, "q75": None, "iqr": None}
    x = np.asarray(values, dtype=np.float64)
    q25, q75 = np.percentile(x, [25.0, 75.0])
    return {
        "n": int(x.size),
        "median": float(np.median(x)),
        "q25": float(q25),
        "q75": float(q75),
        "iqr": float(q75 - q25),
    }


def _group_record(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [float(w[key]) for w in group if w.get(key) is not None]

    return {
        "n_windows": len(group),
        "window_ids": [str(w["window_id"]) for w in group],
        "cpr_median": _spread(values("cpr_median")),
        "s1_median": _spread(values("s1_median")),
        "valid_fraction": _spread(values("valid_fraction")),
    }


CORRECTION_DIFFERENCE: Final = (
    "Fassett et al. 2024 orthorectify and correct for topography (incidence angle) before "
    "comparing; these mosaics carry no per-pixel incidence and no topographic correction is "
    "applied here, so this is a qualitative comparison of uncorrected values and not a "
    "reproduction of their result."
)


def _direction(a: float | None, b: float | None, what: str) -> str:
    if a is None or b is None:
        return f"{what}: not comparable (a group has no valid window)"
    if a > b:
        return f"median {what} higher in shadowed windows"
    if a < b:
        return f"median {what} lower in shadowed windows"
    return f"median {what} equal in both groups"


def shadow_comparison(
    windows: Sequence[Mapping[str, Any]],
    *,
    split_fraction: float,
    justification: str,
    shadow_source: str | None,
) -> dict[str, Any]:
    """Qualitative shadowed versus sunlit comparison, or ``Unavailable`` naming what is missing."""
    if not windows:
        return {
            "status": "Unavailable",
            "reason": "Mini-RF windows not available (missing artifact: per-window CPR and S1)",
            "label": "Unavailable",
        }
    groups = split_by_shadow(windows, split_fraction=split_fraction)
    if not groups["shadowed"] and not groups["sunlit"]:
        return {
            "status": "Unavailable",
            "reason": "permanent-shadow fraction per window not available: no shadow product is "
            "named (decision G0-10); every window is in the unknown group",
            "label": "Unavailable",
            "n_windows_unknown": len(groups["unknown"]),
        }
    for name in ("shadowed", "sunlit"):
        if not groups[name]:
            return {
                "status": "Unavailable",
                "reason": f"no {name} window at split fraction {split_fraction}: "
                "the two groups cannot be compared",
                "label": "Unavailable",
            }
    record = {name: _group_record(group) for name, group in groups.items()}
    shadowed, sunlit = record["shadowed"], record["sunlit"]
    cpr_a, cpr_b = shadowed["cpr_median"]["median"], sunlit["cpr_median"]["median"]
    statement = "; ".join(
        [
            _direction(shadowed["s1_median"]["median"], sunlit["s1_median"]["median"], "S1"),
            _direction(cpr_a, cpr_b, "CPR"),
            "group medians and interquartile ranges are listed; no test and no verdict",
        ]
    )
    return {
        "status": "computed",
        "label": "Derived",
        "groups": record,
        "statement": statement,
        "split_fraction": float(split_fraction),
        "split_justification": justification,
        "shadow_fraction_source": shadow_source,
        "correction_difference": CORRECTION_DIFFERENCE,
        "reference": FASSETT_SOURCE,
    }


# --- impact melt context -----------------------------------------------------------------------


def impact_melt_context(windows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Where window CPR falls against the 0.7 to 1.0 context range (Carter et al. 2012)."""
    if not windows:
        return {
            "status": "Unavailable",
            "reason": "Mini-RF windows not available (missing artifact: per-window Mini-RF CPR)",
            "label": "Unavailable",
        }
    rows: list[dict[str, Any]] = []
    for w in windows:
        n_valid = int(w["n_valid_cpr"])
        n_in = int(w["n_cpr_in_context_range"])
        rows.append(
            {
                "window_id": w["window_id"],
                "n_valid": n_valid,
                "n_in_range": n_in,
                "n_above_1": int(w["n_cpr_above_1"]),
                "fraction_of_valid_in_range": (n_in / n_valid) if n_valid else None,
                "cpr_median": w.get("cpr_median"),
            }
        )
    total = sum(r["n_valid"] for r in rows)
    in_range = sum(r["n_in_range"] for r in rows)
    return {
        "status": "computed",
        "label": "Context, Derived",
        "range": [CARTER_RANGE[0], CARTER_RANGE[1]],
        "range_closed": True,
        "source": CARTER_SOURCE,
        "note": "A context range, not a pass mark. CPR above 1 stays valid and is counted in "
        "n_above_1; it is never truncated. G3 status does not depend on these counts.",
        "windows": rows,
        "pooled": {
            "n_valid": total,
            "n_in_range": in_range,
            "n_above_1": sum(r["n_above_1"] for r in rows),
            "fraction_of_valid_in_range": (in_range / total) if total else None,
        },
    }


# --- Fassett layers (D13) ----------------------------------------------------------------------

_FASSETT_LAYERS: Final = ("delta_cpr", "delta_s1")


def fassett_layer_use(layer: str, *, purpose: str) -> dict[str, Any]:
    """Fassett delta layers may appear only as lunar context, never in a distance (D13)."""
    if layer not in _FASSETT_LAYERS:
        raise ValueError(f"layer must be one of {_FASSETT_LAYERS}, got {layer!r}")
    if purpose == "distance":
        return {
            "status": "Unavailable",
            "reason": "D13: Fassett delta-CPR and delta-S1 are empirically normalized lunar "
            "context quantities, not CPR or S1 values; they never enter a cross-body distance",
            "label": "Unavailable",
        }
    if purpose == "context":
        return {"status": "context_only", "label": "lunar context", "layer": layer}
    raise ValueError(f"purpose must be 'distance' or 'context', got {purpose!r}")


# --- combined G3 status ------------------------------------------------------------------------


def combined_g3_status(
    holuhraun_outcome: str | None, shadow_recorded: bool, impact_melt_recorded: bool
) -> str:
    """The one G3 status: the Holuhraun logic, with 'other parts recorded' meaning both the
    shadow and the impact-melt parts. The impact-melt counts themselves never change it."""
    return g3_status(
        run=holuhraun_outcome is not None,
        outcome=holuhraun_outcome,
        other_parts_recorded=shadow_recorded and impact_melt_recorded,
    )


# --- mosaic geometry ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MosaicGrid:
    """Polar-stereographic mosaic grid: ``x = (sample - S0) * scale``, ``y = (L0 - line) * scale``
    with 1-based sample and line; integral values are pixel centres (PDS DSMAP.CAT)."""

    scale_m: float
    offset_sample: float
    offset_line: float


MINIRF_GRID: Final = MosaicGrid(
    scale_m=118.45058759433, offset_sample=5173.5000000002, offset_line=5173.5000000002
)
"""From the label keywords MAP_SCALE 0.11845058759433 km/pix and the sample and line projection
offsets, as recorded in outputs/pilot/minirf_mosaic.json."""


def window_pixels(grid: MosaicGrid, *, x0: float, y0: float, size_m: float) -> tuple[slice, slice]:
    """Row and column slices (0-based) of pixels whose centres lie in the half-open square."""
    s0, l0, scale = grid.offset_sample, grid.offset_line, grid.scale_m
    # centre of 0-based column j: x = (j + 1 - S0) * scale; of row i: y = (L0 - (i + 1)) * scale
    col_first = int(np.ceil(x0 / scale + s0 - 1.0 - 1e-9))
    col_last = int(np.ceil((x0 + size_m) / scale + s0 - 1.0 - 1e-9)) - 1
    row_first = int(np.floor(l0 - 1.0 - (y0 + size_m) / scale + 1e-9)) + 1
    row_last = int(np.floor(l0 - 1.0 - y0 / scale + 1e-9))
    return slice(row_first, row_last + 1), slice(col_first, col_last + 1)


# --- reading the real mosaic and the CLI -------------------------------------------------------


def _read_mosaic_windows() -> dict[str, Any]:
    """Range-read the rows covering Site01 of the CPR and S1 mosaics and summarize each window.

    Imports are local so the pure functions above need no network or GDAL.
    """
    from terraluna_pipeline import io as tio
    from terraluna_pipeline.quality import InvalidReason, validity_mask

    evidence = json.loads((REPO_ROOT / WINDOWS_EVIDENCE_REL).read_text(encoding="utf-8"))
    grid_info = evidence["g0_02_moon"]["grid"]
    nx, ny = int(grid_info["nx"]), int(grid_info["ny"])
    ox, oy = grid_info["origin_m"]
    stride, size = float(grid_info["stride_m"]), float(grid_info["window_m"])
    specs = [
        (f"moon-sps-x{i}-y{j}", ox + i * stride, oy + j * stride)
        for i in range(nx)
        for j in range(ny)
    ]
    slices = {wid: window_pixels(MINIRF_GRID, x0=x, y0=y, size_m=size) for wid, x, y in specs}
    row_lo = min(s[0].start for s in slices.values())
    row_hi = max(s[0].stop for s in slices.values())
    first, last = row_lo * LINE_BYTES, row_hi * LINE_BYTES - 1
    null = float(np.frombuffer(bytes.fromhex("FF7FFFFB"), dtype=">f4")[0])

    layers: dict[str, Any] = {}
    for key, name in (("cpr", CPR_IMAGE), ("s1", S1_IMAGE)):
        try:
            response = tio.fetch_range(MOSAIC_BASE + name, first, last)
        except (tio.RangeReadError, ValueError, OSError) as error:
            raise MosaicUnreadable(f"{name}: {error}") from error
        body = bytes(response.data)
        rows = row_hi - row_lo
        if len(body) != rows * LINE_BYTES:
            raise MosaicUnreadable(f"{name}: got {len(body)} bytes, expected {rows * LINE_BYTES}")
        import hashlib

        array = np.frombuffer(body, dtype="<f4").reshape(rows, MOSAIC_SAMPLES)
        layers[key] = {
            "name": name,
            "array": array,
            "sha256_of_rows_read": hashlib.sha256(body).hexdigest(),
            "byte_range": [first, last],
            "total_size": response.total_size,
        }

    windows: list[dict[str, Any]] = []
    reasons: dict[str, dict[str, int]] = {}
    for wid, _, _ in specs:
        rs, cs = slices[wid]
        sel = (slice(rs.start - row_lo, rs.stop - row_lo), cs)
        masks = {k: validity_mask(layers[k]["array"][sel], nodata=null) for k in layers}
        values = {k: np.asarray(layers[k]["array"][sel], dtype=np.float64) for k in layers}
        cpr_ok = masks["cpr"].valid
        s1_ok = masks["s1"].valid
        record = window_stats(
            wid,
            values["cpr"][cpr_ok],
            values["s1"][s1_ok],
            n_pixels=int(cpr_ok.size),
        )
        record["shadow_fraction"] = None
        windows.append(record)
        reasons[wid] = {
            code.label: int(np.count_nonzero(masks["cpr"].reason == int(code)))
            for code in InvalidReason
            if code != InvalidReason.VALID
        }
    return {
        "windows": windows,
        "invalid_cpr_by_reason": reasons,
        "layers": {
            k: {f: v for f, v in layer.items() if f != "array"} for k, layer in layers.items()
        },
        "window_pixel_slices": {
            wid: {"rows": [s[0].start, s[0].stop], "cols": [s[1].start, s[1].stop]}
            for wid, s in slices.items()
        },
        "window_size_m": size,
    }


SPLIT_FRACTION: Final = 0.5
SPLIT_JUSTIFICATION: Final = (
    "Engineering choice recorded before any shadow value exists: a window counts as shadowed when "
    "at least half of its area is permanently shadowed. The PRD sets no split."
)


def _hashes_for_mosaic(layers: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        f"{MOSAIC_BASE}{layer['name']}": {
            "sha256_of_rows_read": layer["sha256_of_rows_read"],
            "byte_range": layer["byte_range"],
            "total_size": layer["total_size"],
        }
        for layer in layers.values()
    }


def run() -> int:
    try:
        data = _read_mosaic_windows()
    except (MosaicUnreadable, OSError, KeyError) as error:
        print(f"blocked: Mini-RF mosaic windows not readable ({error})")
        return 3
    windows = data["windows"]
    evidence_path = REPO_ROOT / WINDOWS_EVIDENCE_REL
    common = {
        "input_hashes": provenance.file_hashes([evidence_path]),
        "raw_inputs_sha256": _hashes_for_mosaic(data["layers"]),
        "mosaic_products": {
            "cpr": "LSZ_XXXXX_3CP_PIU_90S000_V1 (LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0, LROMRF_0005)",
            "s1": "LSZ_XXXXX_3S1_PIU_90S000_V1 (same data set; S1 = |ERH| + |ERV| per its label)",
            "note": "combined East and West looking mosaic; CPR is the product's own SC/OC; no "
            "per-pixel incidence is recoverable; transmit handedness not stated by the product",
        },
        "window_pixel_slices": data["window_pixel_slices"],
        "window_size_m": data["window_size_m"],
        "pixel_membership": "pixel centre in [x0, x0+size) x [y0, y0+size); mosaic grid from "
        "the label (MAP_SCALE, projection offsets), as in outputs/pilot/minirf_mosaic.json",
        "evidence_labels": {
            "window_cpr_s1": "Observed (Mini-RF mosaic values) with Derived window statistics",
            "shadow_fraction": "Unavailable (decision G0-10: no permanent-shadow product named)",
        },
    }
    shadow = shadow_comparison(
        windows,
        split_fraction=SPLIT_FRACTION,
        justification=SPLIT_JUSTIFICATION,
        shadow_source=None,
    )
    shadow_record = {
        "record_type": "terraluna G3 lunar shadow behaviour (T6-06)",
        **common,
        "windows": windows,
        "invalid_cpr_by_reason": data["invalid_cpr_by_reason"],
        "comparison": shadow,
        "reference": FASSETT_SOURCE,
        "fassett_layers": "not used: their delta definitions have not been read for this record "
        "(PRD: definitions are recorded before first use); never used in a distance (D13)",
        "not_feasible": [
            "shadowed versus sunlit split: no permanent-shadow product (G0-10)",
            "topographic correction as in Fassett et al.: no per-pixel incidence in the mosaics",
        ],
    }
    melt = impact_melt_context(windows)
    melt_record = {
        "record_type": "terraluna G3 impact-melt context range (T6-06)",
        **common,
        "context": melt,
        "fassett_layers": shadow_record["fassett_layers"],
    }
    _write(REPO_ROOT / SHADOW_REL, shadow_record)
    _write(REPO_ROOT / IMPACT_MELT_REL, melt_record)

    repro_path = REPO_ROOT / REPRODUCTION_REL
    repro = json.loads(repro_path.read_text(encoding="utf-8"))
    primary = repro["variants"][repro["primary_variant"]]
    outcome = primary.get("plausibility", {}).get("outcome")
    shadow_recorded = shadow["status"] == "computed"
    melt_recorded = melt["status"] == "computed"
    status = combined_g3_status(outcome, shadow_recorded, melt_recorded)

    overlaps = primary["comparison"]["overlaps"]
    pooled = melt["pooled"]
    fraction = pooled["fraction_of_valid_in_range"]
    result = (
        f"Holuhraun: facies CPR overlap {100 * min(overlaps.values()):.1f}% to "
        f"{100 * max(overlaps.values()):.1f}% against 88-89% (Tolometti et al. 2022), outcome "
        f"{outcome}. Shadow: {shadow['status']} ({shadow.get('reason', 'computed')}). Impact-melt "
        f"context: {100 * fraction:.1f}% of valid Site01 window CPR pixels in 0.7 to 1.0 "
        f"({pooled['n_above_1']} above 1, kept valid), context only."
    )
    consequence = (
        "G3 is PARTIAL, not PASS: the shadow behaviour check is Unavailable (decision G0-10: no "
        "permanent-shadow product), so not every G3 part is recorded. The Neish et al. 2017 values "
        "are not compared (region and convention not matching). Claims narrowed to the Holuhraun "
        "facies-overlap plausibility check and a context-only melt range."
        if status == "PARTIAL"
        else f"G3 status {status}; see the evidence files."
    )
    gate: dict[str, Any] = {
        "gate_id": "G3",
        "status": status,
        "evidence_path": REPRODUCTION_REL,
        "run_id": provenance.run_id(
            REPO_ROOT / CONFIG_REL,
            [
                *((f"uavsar/{k}", v) for k, v in sorted(repro["raw_inputs_sha256"].items())),
                *(
                    (f"minirf/{layer['name']}", layer["sha256_of_rows_read"])
                    for layer in data["layers"].values()
                ),
            ],
        ),
        "scope": "Holuhraun UAVSAR CPR facies reproduction; lunar shadow behaviour "
        f"({SHADOW_REL}); impact-melt context ({IMPACT_MELT_REL})",
        "result": result,
        "consequence": consequence,
        "command": "python -m terraluna_pipeline.holuhraun run; "
        + provenance.command_line("terraluna_pipeline.lunar_context", ["run"]),
        "config_version": provenance.config_version(REPO_ROOT / CONFIG_REL),
        "input_hashes": provenance.file_hashes(
            [
                REPO_ROOT / PROTOCOL_REL,
                REPO_ROOT / REPRODUCTION_REL,
                REPO_ROOT / SHADOW_REL,
                REPO_ROOT / IMPACT_MELT_REL,
            ]
        ),
    }
    for item in gate["input_hashes"]:
        item["path"] = item["path"].split(REPO_ROOT.as_posix() + "/")[-1]
    _write(REPO_ROOT / G3_REL, gate)
    print(f"G3 {status}: {result}")
    return 0


def check() -> int:
    """Offline: the committed context records exist together and match the G3 record."""
    problems: list[str] = []
    paths = {rel: REPO_ROOT / rel for rel in (SHADOW_REL, IMPACT_MELT_REL)}
    present = {rel: p.exists() for rel, p in paths.items()}
    if any(present.values()) and not all(present.values()):
        problems.append("only one of the two context records exists")
    if all(present.values()):
        gate_path = REPO_ROOT / G3_REL
        if not gate_path.exists():
            problems.append("context records exist without outputs/gates/G3.json")
        else:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            for item in gate.get("input_hashes", []):
                target = REPO_ROOT / item["path"]
                if not target.is_file() or _sha256(target) != item["sha256"]:
                    problems.append(f"G3.json input hash is stale: {item['path']}")
            listed = {i["path"] for i in gate.get("input_hashes", [])}
            for rel in paths:
                if rel not in listed:
                    problems.append(f"G3.json does not hash {rel}")
        for rel, path in paths.items():
            record = json.loads(path.read_text(encoding="utf-8"))
            flat = json.dumps(record).lower()
            if "mock" in record or '"pass"' in flat:
                problems.append(f"{rel} carries a mock flag or a pass mark")
            for item in record.get("input_hashes", []):
                target = REPO_ROOT / item["path"]
                if not target.is_file() or _sha256(target) != item["sha256"]:
                    problems.append(f"{rel}: stale input hash {item['path']}")
    if problems:
        print("\n".join(problems))
        return 1
    print("ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terraluna_pipeline.lunar_context", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="read the Mini-RF mosaics, write the two records and the G3 record")
    sub.add_parser("check", help="offline consistency check of the committed records")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run() if args.command == "run" else check()


if __name__ == "__main__":
    sys.exit(main())
