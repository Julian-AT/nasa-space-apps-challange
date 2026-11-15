// Encoder input channels (PRD 11.2, 8.7 step 3; T10-07): the TypeScript mirror of
// terraluna_ml.dataset.encode_channels for the 64 x 64 window at 78.125 m. Float64 throughout;
// toOnnxInput is the only Float32Array cast, made for the ONNX input tensor alone.
//
// Channel 0 asinh(z / 50 m) of the plane-removed elevation, channel 1 slope magnitude / 45
// degrees from the elevation before plane removal, channel 2 the valid mask. Pixels without a
// valid neighbour along each axis are dropped until none is left; invalid pixels hold 0.0 in
// every channel (a network-input fill, never an elevation), and the mask carries validity.

import { windowGrid } from './geometry.ts';

export const ENCODER_WINDOW_PX = 64;
export const ENCODER_PIXEL_M = 78.125;
export const ELEVATION_SCALE_M = 50.0;
export const SLOPE_SCALE_DEG = 45.0;
export const INVALID_FILL = 0.0;
export const ENCODER_CHANNELS = Object.freeze(['asinh_z_over_50m', 'slope_over_45deg', 'valid'] as const);

const N = ENCODER_WINDOW_PX;
const GRID = windowGrid(N * ENCODER_PIXEL_M, ENCODER_PIXEL_M);

export interface EncoderChannels {
  /** [channel][row * 64 + column], float64. */
  readonly channels: readonly [Float64Array, Float64Array, Float64Array];
  readonly validCells: number;
  readonly validFraction: number;
  /** True when no pixel is usable: the window is flagged and must not be embedded. */
  readonly empty: boolean;
}

function slopeSupport(mask: Uint8Array): Uint8Array {
  let use = Uint8Array.from(mask);
  for (;;) {
    const kept = new Uint8Array(N * N);
    let changed = false;
    for (let r = 0; r < N; r += 1) {
      for (let c = 0; c < N; c += 1) {
        const i = r * N + c;
        if (!use[i]) continue;
        const ew = (c + 1 < N && use[i + 1] === 1) || (c > 0 && use[i - 1] === 1);
        const ns = (r > 0 && use[i - N] === 1) || (r + 1 < N && use[i + N] === 1);
        if (ew && ns) kept[i] = 1;
        else changed = true;
      }
    }
    if (!changed) return use;
    use = kept;
  }
}

function difference(z: number, ahead: number, behind: number, hasAhead: boolean, hasBehind: boolean): number {
  if (hasAhead && hasBehind) return (ahead - behind) / (2 * ENCODER_PIXEL_M);
  if (hasAhead) return (ahead - z) / ENCODER_PIXEL_M;
  return (z - behind) / ENCODER_PIXEL_M;
}

/** terraluna_ml.dataset.encode_channels on a 64 x 64 window (row-major values and mask). */
export function encoderChannels(values: ArrayLike<number>, valid: ArrayLike<number | boolean>): EncoderChannels {
  if (values.length !== N * N || valid.length !== N * N) throw new RangeError('the encoder window is 64 x 64');
  const mask = new Uint8Array(N * N);
  for (let i = 0; i < N * N; i += 1) {
    if (valid[i]) {
      if (!Number.isFinite(values[i])) throw new RangeError('a valid pixel must hold a finite elevation');
      mask[i] = 1;
    }
  }
  const use = slopeSupport(mask);
  const ch0 = new Float64Array(N * N).fill(INVALID_FILL);
  const ch1 = new Float64Array(N * N).fill(INVALID_FILL);
  const ch2 = new Float64Array(N * N).fill(INVALID_FILL);
  const idx: number[] = [];
  for (let i = 0; i < N * N; i += 1) if (use[i]) idx.push(i);
  const xOf = (i: number): number => GRID.xM[i % N] as number;
  const yOf = (i: number): number => GRID.yM[Math.floor(i / N)] as number;
  const z = (i: number): number => values[i] as number;
  const empty = { channels: [ch0, ch1, ch2] as const, validCells: 0, validFraction: 0, empty: true };
  if (idx.length === 0) return empty;
  let sx = 0;
  let sy = 0;
  let sz = 0;
  for (const i of idx) {
    sx += xOf(i);
    sy += yOf(i);
    sz += z(i);
  }
  const n = idx.length;
  const xMean = sx / n;
  const yMean = sy / n;
  const zMean = sz / n;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  let sxz = 0;
  let syz = 0;
  for (const i of idx) {
    const dx = xOf(i) - xMean;
    const dy = yOf(i) - yMean;
    const dz = z(i) - zMean;
    sxx += dx * dx;
    syy += dy * dy;
    sxy += dx * dy;
    sxz += dx * dz;
    syz += dy * dz;
  }
  const det = sxx * syy - sxy * sxy;
  if (!(det > 0)) return empty;
  const slopeEast = (sxz * syy - syz * sxy) / det;
  const slopeNorth = (syz * sxx - sxz * sxy) / det;
  const has = (i: number): boolean => use[i] === 1;
  for (const i of idx) {
    const r = Math.floor(i / N);
    const c = i % N;
    const residual = z(i) - zMean - slopeEast * (xOf(i) - xMean) - slopeNorth * (yOf(i) - yMean);
    const hasE = c + 1 < N && has(i + 1);
    const hasW = c > 0 && has(i - 1);
    const hasN = r > 0 && has(i - N);
    const hasS = r + 1 < N && has(i + N);
    const dzdx = difference(z(i), hasE ? z(i + 1) : 0, hasW ? z(i - 1) : 0, hasE, hasW);
    const dzdy = difference(z(i), hasN ? z(i - N) : 0, hasS ? z(i + N) : 0, hasN, hasS);
    const slopeDeg = Math.atan(Math.hypot(dzdx, dzdy)) * (180 / Math.PI);
    ch0[i] = Math.asinh(residual / ELEVATION_SCALE_M);
    ch1[i] = slopeDeg / SLOPE_SCALE_DEG;
    ch2[i] = 1;
  }
  return { channels: [ch0, ch1, ch2], validCells: n, validFraction: n / (N * N), empty: false };
}

/** The ONNX input tensor data, NCHW [1, 3, 64, 64] float32: the only float32 cast. */
export function toOnnxInput(encoded: EncoderChannels): Float32Array {
  const out = new Float32Array(3 * N * N);
  encoded.channels.forEach((ch, k) => {
    out.set(ch, k * N * N);
  });
  return out;
}
