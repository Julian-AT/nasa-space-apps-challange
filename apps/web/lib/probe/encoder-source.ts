// The probe's encoder from the release bundle (server side, build time). The bundle manifest
// names the model (id, status EXPERIMENTAL, G5 status) and lists model/encoder.fp16.onnx with its
// SHA-256; the export record states the graph's input and output names and whether L2
// normalisation is inside it; each target's ModelBackground carries its embedding and histogram.
// The browser fetches the model from the site at PROBE_MODEL_URL and verifies that SHA-256 before
// use. Anything missing gives a stated reason and the encoder distance stays Unavailable.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { z } from 'zod';

import type { PulledBundle } from '../bundle.ts';
import type { EncoderSpec, ModelBackground } from './embedding.ts';

/** Where the site serves the bundle's fp16 encoder (copied into public/ at build time). */
export const PROBE_MODEL_URL = '/probe-model/encoder.fp16.onnx';
export const PROBE_MODEL_DIR = join('public', 'probe-model');
const MODEL_PATH = 'model/encoder.fp16.onnx';

const ExportRecord = z.object({
  model_id: z.string(),
  status: z.literal('EXPERIMENTAL'),
  interim_snapshot: z.object({ epoch: z.number(), of_epochs: z.number() }).nullish(),
  input_name: z.string(),
  output_name: z.string(),
  l2_normalized: z.boolean(),
  mock: z.boolean(),
  files: z.record(z.string(), z.object({ sha256: z.string() })),
});

const Background = z.object({
  target_id: z.string(),
  family: z.literal('model_embedding'),
  model_id: z.string(),
  onnx_sha256: z.string(),
  bin_edges: z.array(z.number()),
  counts: z.array(z.number().int().nonnegative()),
  percentile_definition: z.literal('linear_within_bin_v1'),
  direction: z.literal('smaller_is_closer'),
  target_embedding: z.union([z.array(z.number()), z.object({ vector: z.array(z.number()) }).transform((e) => e.vector)]),
});

export type ProbeEncoderSource =
  | { encoder: { spec: EncoderSpec; backgrounds: Record<string, ModelBackground | null> }; reason: null }
  | { encoder: null; reason: string };

export function probeEncoder(bundle: PulledBundle, dir: string = join(process.cwd(), PROBE_MODEL_DIR)): ProbeEncoderSource {
  const recordPath = join(/* turbopackIgnore: true */ dir, 'export_record.json');
  if (!existsSync(recordPath) || !existsSync(join(/* turbopackIgnore: true */ dir, 'encoder.fp16.onnx'))) {
    return { encoder: null, reason: `the site has no copy of the encoder in ${PROBE_MODEL_DIR}` };
  }
  const record = ExportRecord.safeParse(JSON.parse(readFileSync(recordPath, 'utf8')));
  if (!record.success) return { encoder: null, reason: 'export_record.json is not a readable EXPERIMENTAL export record' };
  if (record.data.mock) return { encoder: null, reason: 'the exported encoder is a mock' };
  const fp16 = record.data.files['encoder.fp16.onnx']?.sha256;
  if (fp16 === undefined) return { encoder: null, reason: 'export_record.json has no encoder.fp16.onnx entry' };
  // The bundle's model summary wins when it has one; it must name the same file.
  const model = bundle.manifest.model;
  const listed = bundle.manifest.files.find((f) => f.path === MODEL_PATH);
  if (model !== null && listed !== undefined && listed.sha256 !== fp16) {
    return { encoder: null, reason: 'the export record and the bundle manifest disagree on the encoder SHA-256' };
  }
  // G5 status: the manifest's model summary, else the bundle's G5 GateRecord, else NOT_RUN.
  const g5Gate = (bundle.validation as readonly { gate_id?: string; status?: string }[]).find((g) => g.gate_id === 'G5');
  const g5 = model?.g5 ?? g5Gate?.status ?? 'NOT_RUN';
  const backgrounds: Record<string, ModelBackground | null> = {};
  for (const file of readdirSync(/* turbopackIgnore: true */ dir).filter((f) => /^background_.+\.json$/.test(f))) {
    const parsed = Background.safeParse(JSON.parse(readFileSync(join(/* turbopackIgnore: true */ dir, file), 'utf8')));
    if (parsed.success) backgrounds[parsed.data.target_id] = parsed.data;
  }
  const snapshot = record.data.interim_snapshot;
  return {
    encoder: {
      spec: {
        url: PROBE_MODEL_URL,
        model_id: model?.id ?? record.data.model_id,
        onnx_sha256: fp16,
        status: 'EXPERIMENTAL',
        g5_status: g5,
        input_name: record.data.input_name,
        output_name: record.data.output_name,
        l2_normalized: record.data.l2_normalized,
        export_sha256s: Object.values(record.data.files).map((f) => f.sha256),
        snapshot_note: snapshot ? `interim snapshot, epoch ${String(snapshot.epoch)} of ${String(snapshot.of_epochs)}` : null,
      },
      backgrounds,
    },
    reason: null,
  };
}
