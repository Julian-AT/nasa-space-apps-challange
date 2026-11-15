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
  input_name: z.string(),
  output_name: z.string(),
  l2_normalized: z.boolean(),
  mock: z.boolean().optional(),
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
  const model = bundle.manifest.model;
  if (model === null) return { encoder: null, reason: 'the bundle ships no encoder' };
  const listed = bundle.manifest.files.find((f) => f.path === MODEL_PATH);
  if (listed === undefined) return { encoder: null, reason: `the bundle manifest lists no ${MODEL_PATH}` };
  const recordPath = join(dir, 'export_record.json');
  if (!existsSync(recordPath) || !existsSync(join(dir, 'encoder.fp16.onnx'))) {
    return { encoder: null, reason: `the site has no copy of the encoder in ${PROBE_MODEL_DIR}` };
  }
  const record = ExportRecord.safeParse(JSON.parse(readFileSync(recordPath, 'utf8')));
  if (!record.success) return { encoder: null, reason: 'export_record.json is not readable' };
  if (record.data.mock === true) return { encoder: null, reason: 'the exported encoder is a mock' };
  if (record.data.files['encoder.fp16.onnx']?.sha256 !== listed.sha256) {
    return { encoder: null, reason: 'the export record and the bundle manifest disagree on the encoder SHA-256' };
  }
  const backgrounds: Record<string, ModelBackground | null> = {};
  for (const file of readdirSync(dir).filter((f) => /^background_.+\.json$/.test(f))) {
    const parsed = Background.safeParse(JSON.parse(readFileSync(join(dir, file), 'utf8')));
    if (parsed.success) backgrounds[parsed.data.target_id] = parsed.data;
  }
  return {
    encoder: {
      spec: {
        url: PROBE_MODEL_URL,
        model_id: model.id,
        onnx_sha256: listed.sha256,
        status: model.status,
        g5_status: model.g5,
        input_name: record.data.input_name,
        output_name: record.data.output_name,
        l2_normalized: record.data.l2_normalized,
      },
      backgrounds,
    },
    reason: null,
  };
}
