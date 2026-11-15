// onnxruntime-web (WASM, one thread) on the exported fp16 encoder against Python onnxruntime on
// the same 256 real windows (PRD 8.8: per-window cosine >= 0.999). Files come from
// tests/probe-parity/make-windows.ts and expected.py; a missing one fails by name.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { cosineDistance, createSession } from '../../lib/probe/embedding.ts';

const DIR = resolve(import.meta.dirname, '../../../../ml/checkpoints/onnx-parity');
const MODEL_DIR = process.env.TERRALUNA_ONNX_DIR ?? resolve(import.meta.dirname, '../../public/probe-model');
const need = (path: string): Buffer => {
  if (!existsSync(path)) throw new Error(`missing ${path}: run tests/probe-parity/make-windows.ts and expected.py first`);
  return readFileSync(path);
};
const f32 = (b: Buffer) => new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));

describe('WASM parity of the fp16 encoder', () => {
  it('has per-window cosine >= 0.999 against Python onnxruntime fp32 and fp16', async () => {
    const record = JSON.parse(need(join(MODEL_DIR, 'export_record.json')).toString()) as { model_id: string; input_name: string; output_name: string; l2_normalized: boolean; status: string; files: Record<string, { sha256: string }> };
    const model = need(join(MODEL_DIR, 'encoder.fp16.onnx'));
    const windows = f32(need(join(DIR, 'windows.f32.bin')));
    const expected32 = f32(need(join(DIR, 'expected_fp32.f32.bin')));
    const expected16 = f32(need(join(DIR, 'expected_fp16.f32.bin')));
    const n = windows.length / (3 * 64 * 64);
    const session = await createSession(
      { url: '', model_id: record.model_id, onnx_sha256: record.files['encoder.fp16.onnx']?.sha256 ?? '', status: record.status, g5_status: 'NOT_RUN', input_name: record.input_name, output_name: record.output_name, l2_normalized: record.l2_normalized },
      model.buffer.slice(model.byteOffset, model.byteOffset + model.byteLength) as ArrayBuffer,
      { threads: 1 },
    );
    const cos32: number[] = [];
    const cos16: number[] = [];
    const t0 = performance.now();
    for (let start = 0; start < n; start += 32) {
      const b = Math.min(32, n - start);
      const out = await session.embed(windows.slice(start * 12288, (start + b) * 12288), b);
      for (let k = 0; k < b; k += 1) {
        const e = out.subarray(k * 256, (k + 1) * 256);
        cos32.push(1 - (cosineDistance(e, expected32.subarray((start + k) * 256, (start + k + 1) * 256)).value ?? 2));
        cos16.push(1 - (cosineDistance(e, expected16.subarray((start + k) * 256, (start + k + 1) * 256)).value ?? 2));
      }
    }
    const ms = performance.now() - t0;
    const min32 = Math.min(...cos32);
    const min16 = Math.min(...cos16);
    const ort = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../node_modules/onnxruntime-web/package.json'), 'utf8')) as { version: string };
    writeFileSync(
      join(DIR, 'wasm_parity.json'),
      `${JSON.stringify({ model_id: record.model_id, onnx_sha256: record.files['encoder.fp16.onnx']?.sha256, onnxruntime_web: ort.version, threads: 1, runtime: `Node ${process.version} (WASM)`, windows: n, min_cosine_vs_python_fp32: min32, mean_cosine_vs_python_fp32: cos32.reduce((a, b) => a + b, 0) / n, worst_window_vs_fp32: cos32.indexOf(min32), min_cosine_vs_python_fp16: min16, mean_cosine_vs_python_fp16: cos16.reduce((a, b) => a + b, 0) / n, per_window_ms: ms / n, threshold: 0.999 }, null, 2)}\n`,
    );
    expect(min32, `worst window ${String(cos32.indexOf(min32))}`).toBeGreaterThanOrEqual(0.999);
    expect(min16).toBeGreaterThanOrEqual(0.999);
  });
});
