// probeEncoder reads the bundle's model summary and the site copy of the export (temporary
// files written by the test; no model weights).
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { PulledBundle } from '../bundle.ts';
import { PROBE_MODEL_URL, probeEncoder } from './encoder-source.ts';

const sha = 'a'.repeat(64);
const bundle = (model: PulledBundle['manifest']['model'], files: { path: string; sha256: string; bytes: number }[] = [], g5 = 'NOT_RUN') =>
  ({ manifest: { model, files }, validation: [{ gate_id: 'G5', status: g5 }], checksumOnly: new Map() }) as unknown as PulledBundle;

describe('probeEncoder', () => {
  it('states why there is no encoder', () => {
    expect(probeEncoder(bundle(null), mkdtempSync(join(tmpdir(), 'probe-empty-'))).reason).toContain('no copy of the encoder');
  });
  it('builds the spec from the manifest and the export record, with the manifest G5 status', () => {
    const dir = mkdtempSync(join(tmpdir(), 'probe-model-'));
    writeFileSync(join(dir, 'encoder.fp16.onnx'), 'x');
    writeFileSync(join(dir, 'export_record.json'), JSON.stringify({ model_id: 'm', status: 'EXPERIMENTAL', interim_snapshot: { epoch: 1, of_epochs: 12 }, input_name: 'windows', output_name: 'embedding', l2_normalized: true, mock: false, files: { 'encoder.fp16.onnx': { sha256: sha } } }));
    writeFileSync(join(dir, 'background_t1.json'), JSON.stringify({ target_id: 't1', family: 'model_embedding', model_id: 'm', onnx_sha256: sha, bin_edges: [0, 1], counts: [3], percentile_definition: 'linear_within_bin_v1', direction: 'smaller_is_closer', target_embedding: [1, 0] }));
    const out = probeEncoder(bundle({ id: 'm', status: 'EXPERIMENTAL', g5: 'NOT_RUN' }, [{ path: 'model/encoder.fp16.onnx', sha256: sha, bytes: 1 }]), dir);
    expect(out.reason).toBeNull();
    expect(out.encoder?.spec).toMatchObject({ url: PROBE_MODEL_URL, model_id: 'm', onnx_sha256: sha, g5_status: 'NOT_RUN', input_name: 'windows' });
    expect(out.encoder?.backgrounds.t1?.target_embedding).toEqual([1, 0]);
    expect(out.encoder?.spec.snapshot_note).toBe('interim snapshot, epoch 1 of 12');
    const fromGate = probeEncoder(bundle(null, [], 'NOT_RUN'), dir);
    expect(fromGate.encoder?.spec).toMatchObject({ model_id: 'm', status: 'EXPERIMENTAL', g5_status: 'NOT_RUN' });
    const other = probeEncoder(bundle({ id: 'm', status: 'EXPERIMENTAL', g5: 'NOT_RUN' }, [{ path: 'model/encoder.fp16.onnx', sha256: 'b'.repeat(64), bytes: 1 }]), dir);
    expect(other.reason).toContain('disagree');
  });
  it('reads a final export whose record has interim_snapshot null, with the failed G5 status', () => {
    const dir = mkdtempSync(join(tmpdir(), 'probe-model-final-'));
    writeFileSync(join(dir, 'encoder.fp16.onnx'), 'x');
    writeFileSync(join(dir, 'export_record.json'), JSON.stringify({ model_id: 'm', status: 'EXPERIMENTAL', interim_snapshot: null, input_name: 'windows', output_name: 'embedding', l2_normalized: true, mock: false, files: { 'encoder.fp16.onnx': { sha256: sha } } }));
    const out = probeEncoder(bundle({ id: 'm', status: 'EXPERIMENTAL', g5: 'FAIL' }, [{ path: 'model/encoder.fp16.onnx', sha256: sha, bytes: 1 }]), dir);
    expect(out.reason).toBeNull();
    expect(out.encoder?.spec).toMatchObject({ model_id: 'm', status: 'EXPERIMENTAL', g5_status: 'FAIL' });
    expect(out.encoder?.spec.snapshot_note ?? null).toBeNull();
  });
});
