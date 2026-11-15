// Writes the encoder parity windows (T13-03): 256 real 64 x 64 encoder windows from the local
// Copernicus GLO-30 tiles in $TERRALUNA_RAW_DIR/copernicus_glo30, cut by @terraluna/core exactly
// as the browser probe cuts them, to ml/checkpoints/onnx-parity/ (git-ignored). Run by hand:
//   TERRALUNA_RAW_DIR=... node tests/probe-parity/make-windows.ts
import { mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { ENCODER_GRID, encoderChannels, geographicLookup, resampleWindow, toOnnxInput } from '@terraluna/core';
import { fromFile } from 'geotiff';

const RAW = process.env.TERRALUNA_RAW_DIR;
if (!RAW) {
  console.error('set TERRALUNA_RAW_DIR to the raw-data directory (holding copernicus_glo30/)');
  process.exit(2);
}
const OUT = resolve(import.meta.dirname, '../../../../ml/checkpoints/onnx-parity');
const COUNT = 256;
const DEG = Math.PI / 180;

const tileDir = join(RAW, 'copernicus_glo30');
const tiles = readdirSync(tileDir).filter((d) => d.startsWith('Copernicus_DSM_COG_10_')).sort();
const perTile = Math.ceil(COUNT / tiles.length);
const windows: Float32Array[] = [];
const meta: { tile: string; lat_deg: number; lon_deg: number; valid_fraction: number }[] = [];
for (const tile of tiles) {
  const image = await (await fromFile(join(tileDir, tile, `${tile}.tif`))).getImage(0);
  const [tieLon, tieLat] = image.getOrigin() as [number, number];
  const [resLon, resLatS] = image.getResolution() as [number, number];
  const resLat = Math.abs(resLatS);
  const values = Float64Array.from((await image.readRasters({ samples: [0], interleave: true })) as ArrayLike<number>);
  // GDAL's PixelIsPoint convention: the tie point is the centre of pixel (0, 0).
  const lookup = geographicLookup({ originLonDeg: tieLon - resLon / 2, originLatDeg: tieLat + resLat / 2, pixelWidthDeg: resLon, pixelHeightDeg: -resLat });
  const source = { values, rows: image.getHeight(), columns: image.getWidth(), nodata: null };
  // A fixed grid of centres inside the tile, 0.08 degrees from its edges; never random.
  const side = Math.ceil(Math.sqrt(perTile * 1.5));
  let taken = 0;
  for (let i = 0; i < side && taken < perTile; i += 1) {
    for (let j = 0; j < side && taken < perTile; j += 1) {
      const lat = tieLat - 0.08 - ((1 - 0.16) * (i + 0.5)) / side;
      const lon = tieLon + 0.08 + ((1 - 0.16) * (j + 0.5)) / side;
      const w = resampleWindow(source, lookup, { grid: ENCODER_GRID, body: 'earth', lat0Rad: lat * DEG, lon0Rad: lon * DEG });
      const ch = encoderChannels(w.values, w.valid);
      if (ch.empty || ch.validFraction < 0.8) continue;
      windows.push(toOnnxInput(ch));
      meta.push({ tile, lat_deg: lat, lon_deg: lon, valid_fraction: ch.validFraction });
      taken += 1;
    }
  }
}
const n = Math.min(COUNT, windows.length);
const flat = new Float32Array(n * 3 * 64 * 64);
for (let k = 0; k < n; k += 1) flat.set(windows[k] as Float32Array, k * 3 * 64 * 64);
mkdirSync(OUT, { recursive: true });
writeFileSync(join(OUT, 'windows.f32.bin'), new Uint8Array(flat.buffer));
writeFileSync(
  join(OUT, 'windows.json'),
  `${JSON.stringify({ data_kind: 'real Copernicus GLO-30 windows cut by @terraluna/core', count: n, shape: [n, 3, 64, 64], dtype: 'float32 little-endian', windows: meta.slice(0, n) }, null, 2)}\n`,
);
console.log(`make-windows: wrote ${String(n)} windows to ${OUT}`);
