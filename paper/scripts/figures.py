"""Hillshades of real DEM windows for the paper (PRD 22.5), written as vector-wrapped PDFs.

Each panel is a hillshade of one 5 km analysis window, computed from the provider's elevation:

- the lunar target window, read in the PGDA product 78 Site01 grid (south polar stereographic
  metres, 5 m pixels) over the window ring of the G0 windows evidence, every second pixel;
- Earth windows, resampled from the Copernicus GLO-30 tile onto a 10 m D29 grid about the window
  centre with terraluna_pipeline.geometry.resample_window (bilinear, no other resampling).

The hillshade uses the gradient by central differences, a light from azimuth 315 degrees and
altitude 45 degrees, and no vertical exaggeration. Each PDF holds one 8-bit grey image and no
date, so the output is byte-identical across runs. A JSON sidecar records every input's SHA-256.
Raw data live outside the repository: set TERRALUNA_DATA to the directory holding raw/<source>/.
Run from paper/: PYTHONPATH=. uv run --frozen python -P -m scripts.figures
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import zlib
from pathlib import Path

import numpy as np
import numpy.typing as npt

PAPER = Path(__file__).resolve().parents[1]
ROOT = PAPER.parent
OUT = PAPER / "figures" / "generated"
AZIMUTH_DEG = 315.0
ALTITUDE_DEG = 45.0
EARTH_SPACING_M = 10.0
WINDOW_M = 5000.0

FloatArray = npt.NDArray[np.float64]


def hillshade(z: FloatArray, spacing_m: float) -> npt.NDArray[np.uint8]:
    """Grey levels 0 to 255 of a hillshade; nodata (NaN) cells and their neighbours are white."""
    dz_dy, dz_dx = np.gradient(z, spacing_m)  # rows run south, so dz_dy is the southward slope
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, dz_dy)
    az = math.radians(360.0 - AZIMUTH_DEG + 90.0)
    alt = math.radians(ALTITUDE_DEG)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    grey = np.clip(shade, 0.0, 1.0) * 255.0
    grey[~np.isfinite(grey)] = 255.0
    return np.asarray(np.round(grey), dtype=np.uint8)


def write_pdf(path: Path, grey: npt.NDArray[np.uint8], size_pt: float) -> None:
    """A one-page PDF holding the grey image at size_pt square; no metadata, no date."""
    rows, cols = grey.shape
    data = zlib.compress(grey.tobytes(), 9)
    content = f"q {size_pt:.2f} 0 0 {size_pt:.2f} 0 0 cm /Im0 Do Q".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size_pt:.2f} {size_pt:.2f}] "
            "/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        ).encode(),
        (
            f"<< /Type /XObject /Subtype /Image /Width {cols} /Height {rows} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(data)} >>\nstream\n"
        ).encode()
        + data
        + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def moon_window(raw: Path, ring_m: list[list[float]]) -> tuple[FloatArray, Path]:
    import rasterio
    from rasterio.windows import from_bounds

    path = raw / "pgda78" / "Site01" / "Site01_final_adj_5mpp_surf.tif"
    xs = [p[0] for p in ring_m]
    ys = [p[1] for p in ring_m]
    with rasterio.open(path) as src:
        window = from_bounds(min(xs), min(ys), max(xs), max(ys), src.transform)
        z = src.read(1, window=window.round_offsets().round_lengths()).astype(np.float64)
        if src.nodata is not None:
            z[z == src.nodata] = np.nan
    return z[::2, ::2], path


def earth_window(
    raw: Path, tile: str, centre_lonlat: tuple[float, float]
) -> tuple[FloatArray, Path]:
    import rasterio

    from terraluna_pipeline.geometry import GeographicLookup, resample_window, window_grid

    path = raw / "copernicus_glo30" / tile / f"{tile}.tif"
    with rasterio.open(path) as src:
        t = src.transform
        lookup = GeographicLookup(
            origin_lon_deg=t.c, origin_lat_deg=t.f, pixel_width_deg=t.a, pixel_height_deg=t.e
        )
        source = src.read(1).astype(np.float64)
        nodata = src.nodata
    grid = window_grid(WINDOW_M, EARTH_SPACING_M)
    result = resample_window(
        source,
        lookup,
        grid=grid,
        body="earth",
        lat0_rad=math.radians(centre_lonlat[1]),
        lon0_rad=math.radians(centre_lonlat[0]),
        nodata=nodata,
    )
    values = np.array(result.values, dtype=np.float64)
    values[~np.asarray(result.valid, dtype=bool)] = np.nan
    return values, path


def utm_centre(epsg: int, ring_m: list[list[float]]) -> tuple[float, float]:
    from pyproj import Transformer

    xs = [p[0] for p in ring_m[:4]]
    ys = [p[1] for p in ring_m[:4]]
    transformer = Transformer.from_crs(epsg, 4326, always_xy=True)
    lon, lat = transformer.transform((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    return float(lon), float(lat)


def main() -> int:
    data = os.environ.get("TERRALUNA_DATA")
    if not data:
        print(
            "figures.py: set TERRALUNA_DATA to the directory holding raw/<source>/", file=sys.stderr
        )
        return 1
    raw = Path(data) / "raw"
    evidence_path = ROOT / "outputs" / "g0" / "windows-evidence.json"
    evidence = json.loads(evidence_path.read_text("utf-8"))
    moon = evidence["g0_02_moon"]["proposed_primary"]
    target = json.loads((ROOT / "outputs" / "search" / "targets.json").read_text("utf-8"))[0]
    best_id = target["shortlist"]["earth_window_ids"][0]
    card_path = ROOT / "outputs" / "cards" / f"{moon['window_id']}__{best_id}.json"
    card = json.loads(card_path.read_text("utf-8"))
    polygon = card["earth"]["polygon"]
    best_centre = utm_centre(int(polygon["epsg"]), polygon["ring_m"])
    tile = "Copernicus_DSM_COG_10_N64_00_W017_00_DEM"

    OUT.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "generated_by": "paper/scripts/figures.py",
        "hillshade": {"azimuth_deg": AZIMUTH_DEG, "altitude_deg": ALTITUDE_DEG},
        "panels": {},
    }
    panels: dict[str, object] = {}
    z, src = moon_window(raw, moon["ring_m"])
    write_pdf(OUT / "hillshade-moon.pdf", hillshade(z, 10.0), 200.0)
    panels["hillshade-moon"] = {
        "window_id": moon["window_id"],
        "centre_lonlat_deg": moon["centre_lonlat_deg"],
        "product": "PGDA78/Site01/Site01_final_adj_5mpp_surf.tif",
        "input_sha256": sha256(src),
        "sampling": "provider grid, every second 5 m pixel (10 m)",
        "relief_range_m": [float(np.nanmin(z)), float(np.nanmax(z))],
    }
    z, src = earth_window(raw, tile, best_centre)
    write_pdf(OUT / "hillshade-earth.pdf", hillshade(z, EARTH_SPACING_M), 200.0)
    panels["hillshade-earth"] = {
        "window_id": best_id,
        "centre_lonlat_deg": list(best_centre),
        "product": f"COP30/{tile}.tif",
        "input_sha256": sha256(src),
        "sampling": "D29 bilinear resampling onto a 10 m grid about the window centre",
        "relief_range_m": [float(np.nanmin(z)), float(np.nanmax(z))],
    }
    record["panels"] = panels
    record["inputs"] = {
        "outputs/g0/windows-evidence.json": sha256(evidence_path),
        str(card_path.relative_to(ROOT)): sha256(card_path),
    }
    (OUT / "hillshades.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"figures.py: wrote {len(panels)} hillshades to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
