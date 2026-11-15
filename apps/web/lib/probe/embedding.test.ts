// Embedding helpers without a model file (T13-03, T13-06). The WASM session parity test needs
// the exported model and runs in test:parity once fast/ml exports it.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { EmbeddingError, MODEL_MAX_BYTES, type ModelBackground, cosineDistance, embeddingLabel, loadModelBytes, memoryModelCache, modelPercentile } from './embedding.ts';

const spec = { url: 'https://x.test/model.onnx', model_id: 'm1', onnx_sha256: '', status: 'EXPERIMENTAL', g5_status: 'NOT_RUN', input_name: 'x', output_name: 'e', l2_normalized: true };

async function sha(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, '0')).join('');
}

describe('cosineDistance', () => {
  it('is 0, 1 and 2 for identical, orthogonal and opposite vectors', () => {
    expect(Math.abs(cosineDistance(new Float32Array([1, 2, 3]), new Float32Array([1, 2, 3])).value ?? 1)).toBeLessThan(1e-12);
    expect(cosineDistance([1, 0], [0, 1]).value).toBe(1);
    expect(cosineDistance([1, 0], [-1, 0]).value).toBe(2);
  });
  it('a zero vector or a length mismatch is Unavailable', () => {
    expect(cosineDistance([0, 0], [1, 0])).toMatchObject({ value: null, reason: 'embedding_zero_vector' });
    expect(cosineDistance([1], [1, 0])).toMatchObject({ value: null, reason: 'embedding_length_mismatch' });
  });
});

describe('modelPercentile guard', () => {
  const bg: ModelBackground = { target_id: 't', family: 'model_embedding', model_id: 'm1', onnx_sha256: 'abc', bin_edges: [0, 0.5, 1], counts: [5, 5], percentile_definition: 'linear_within_bin_v1', direction: 'smaller_is_closer', target_embedding: [] };
  const s = { model_id: 'm1', onnx_sha256: 'abc' };
  it('uses only the same target, family, model and file', () => {
    expect(modelPercentile(0.25, 't', bg, s).value).toBeCloseTo(25, 12);
    expect(modelPercentile(0.25, 't', null, s).reason).toBe('encoder_not_evaluated');
    expect(modelPercentile(0.25, 't', { ...bg, family: 'terrain_relief' }, s).reason).toBe('background_family_mismatch');
    expect(modelPercentile(0.25, 'u', bg, s).reason).toBe('background_target_mismatch');
    expect(modelPercentile(0.25, 't', bg, { ...s, onnx_sha256: 'x' }).reason).toBe('background_model_mismatch');
    expect(modelPercentile(0.25, 't', { ...bg, counts: [0, 0] }, s).reason).toBe('background_empty');
  });
  it('is monotone in distance', () => {
    const ps = [0, 0.1, 0.3, 0.6, 0.9, 1.2].map((d) => modelPercentile(d, 't', bg, s).value ?? Number.NaN);
    expect([...ps].sort((a, b) => a - b)).toEqual(ps);
  });
  it('labels with the manifest G5 status', () => {
    expect(embeddingLabel({ g5_status: 'NOT_RUN' })).toBe('Predicted (encoder v1, G5 NOT_RUN)');
  });
});

describe('model download', () => {
  it('fetches once, then serves verified bytes from the cache', async () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const s2 = { ...spec, onnx_sha256: await sha(bytes) };
    let calls = 0;
    const doFetch = (() => {
      calls += 1;
      return Promise.resolve(new Response(bytes));
    }) as typeof fetch;
    const cache = memoryModelCache();
    await loadModelBytes(s2, cache, doFetch);
    await loadModelBytes(s2, cache, doFetch);
    expect(calls).toBe(1);
  });
  it('refuses a model over 10 MB and one whose SHA-256 differs', async () => {
    const big = (() => Promise.resolve(new Response(new Uint8Array(MODEL_MAX_BYTES + 1)))) as typeof fetch;
    await expect(loadModelBytes({ ...spec, onnx_sha256: 'x' }, memoryModelCache(), big)).rejects.toMatchObject({ reason: 'model_too_large' });
    const wrong = (() => Promise.resolve(new Response(new Uint8Array([9])))) as typeof fetch;
    const cache = memoryModelCache();
    await expect(loadModelBytes({ ...spec, onnx_sha256: 'beef' }, cache, wrong)).rejects.toBeInstanceOf(EmbeddingError);
    expect(cache.size()).toBe(0);
  });
});

describe('import boundary', () => {
  it('only lib/probe/embedding.ts imports onnxruntime-web, and only dynamically', () => {
    const root = join(import.meta.dirname, '..', '..');
    const hits: string[] = [];
    const walk = (dir: string): void => {
      for (const f of readdirSync(dir)) {
        if (f === 'node_modules' || f.startsWith('.')) continue;
        const p = join(dir, f);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f) && /onnxruntime-web/.test(readFileSync(p, 'utf8'))) hits.push(p.slice(root.length + 1));
      }
    };
    for (const d of ['app', 'components', 'lib', 'workers']) walk(join(root, d));
    expect(hits).toEqual(['lib/probe/embedding.ts']);
    const text = readFileSync(join(root, 'lib/probe/embedding.ts'), 'utf8');
    expect(text).not.toMatch(/^import[^;]*from ['"]onnxruntime-web/m);
  });
});
