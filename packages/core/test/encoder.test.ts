// Encoder channel behaviour (T10-07). Parity against fixtures/parity/encoder_channels.json is
// added when t11-dataset T11-06 writes it; the coverage report fails once it lands untested.
import { describe, expect, it } from 'vitest';

import { ENCODER_GRID, encoderChannels, toOnnxInput } from '../src/index.ts';

const n = 64;

describe('encoderChannels', () => {
  it('an all-invalid window is all zeros, flagged empty', () => {
    const e = encoderChannels(new Float64Array(n * n).fill(Number.NaN), new Uint8Array(n * n));
    expect(e.empty).toBe(true);
    for (const ch of e.channels) expect(ch.every((v) => v === 0)).toBe(true);
  });
  it('a plane leaves channel 0 at about zero and gives its slope in channel 1', () => {
    const z = new Float64Array(n * n);
    for (let r = 0; r < n; r += 1) for (let c = 0; c < n; c += 1) z[r * n + c] = 100 + 0.1 * (ENCODER_GRID.xM[c] as number);
    const e = encoderChannels(z, new Uint8Array(n * n).fill(1));
    expect(Math.max(...Array.from(e.channels[0], Math.abs))).toBeLessThan(1e-9);
    const expected = (Math.atan(0.1) * 180) / Math.PI / 45;
    expect(Math.abs((e.channels[1][100] ?? 0) - expected)).toBeLessThan(1e-12);
    expect(e.validFraction).toBe(1);
  });
  it('invalid pixels are 0.0 with mask 0, and isolated pixels are dropped', () => {
    const z = new Float64Array(n * n).fill(5);
    const valid = new Uint8Array(n * n).fill(1);
    valid[0] = 0;
    valid[2] = 0;
    valid[n + 1] = 0; // pixel 1 keeps no north/south neighbour: dropped
    const e = encoderChannels(z, valid);
    for (const i of [0, 1, 2, n + 1]) {
      expect(e.channels[2][i]).toBe(0);
      expect(e.channels[0][i]).toBe(0);
      expect(e.channels[1][i]).toBe(0);
    }
    expect(toOnnxInput(e)).toHaveLength(3 * n * n);
  });
});
