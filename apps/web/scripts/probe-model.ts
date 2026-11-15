// pnpm --filter @terraluna/web probe:model [--from <export dir>] (T13): copies an encoder export
// (encoder.fp16.onnx, export_record.json, background_*.json) into public/probe-model/, which the
// live probe serves. It refuses a mock export, a file whose SHA-256 differs from the export
// record, a model over the 10 MB budget and an ONNX IR version above what onnxruntime-web reads.
// Swapping in a later export is this one command, then a commit of public/probe-model/.
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parseArgs } from 'node:util';

import { MODEL_MAX_BYTES } from '../lib/probe/embedding.ts';
import { PROBE_MODEL_DIR } from '../lib/probe/encoder-source.ts';

/** onnxruntime-web 1.31.1 reads ONNX IR versions up to 13. */
export const MAX_IR_VERSION = 13;

/** The ir_version of an ONNX ModelProto: field 1, a varint, written first by the serializer. */
export function onnxIrVersion(bytes: Uint8Array): number | null {
  if (bytes[0] !== 0x08) return null;
  let value = 0;
  for (let i = 1, shift = 0; i < 10; i += 1, shift += 7) {
    const b = bytes[i] ?? 0;
    value += (b & 0x7f) * 2 ** shift;
    if ((b & 0x80) === 0) return value;
  }
  return null;
}

const WEB_DIR = resolve(import.meta.dirname, '..');
const { values } = parseArgs({ options: { from: { type: 'string', default: '../../outputs/ml/model' } } });
const from = resolve(WEB_DIR, values.from);
const fail = (message: string): never => {
  console.error(`probe:model: ${message}`);
  process.exit(1);
};
const record = JSON.parse(readFileSync(join(from, 'export_record.json'), 'utf8')) as {
  mock?: boolean;
  status?: string;
  model_id?: string;
  interim_snapshot?: { epoch: number; of_epochs: number };
  files: Record<string, { sha256: string }>;
};
if (record.mock !== false) fail('the export is a mock or does not say it is not; refused');
if (record.status !== 'EXPERIMENTAL') fail(`the export status is ${String(record.status)}, not EXPERIMENTAL`);
const model = readFileSync(join(from, 'encoder.fp16.onnx'));
if (model.byteLength > MODEL_MAX_BYTES) fail(`encoder.fp16.onnx is ${String(model.byteLength)} bytes, over 10 MB`);
const sha = createHash('sha256').update(model).digest('hex');
if (sha !== record.files['encoder.fp16.onnx']?.sha256) fail('encoder.fp16.onnx does not match its SHA-256 in export_record.json');
const ir = onnxIrVersion(model);
if (ir === null || ir > MAX_IR_VERSION) fail(`ONNX IR version ${String(ir)} is not readable by onnxruntime-web (at most ${String(MAX_IR_VERSION)})`);
const out = join(WEB_DIR, PROBE_MODEL_DIR);
if (existsSync(out)) rmSync(out, { recursive: true });
mkdirSync(out, { recursive: true });
const files = ['encoder.fp16.onnx', 'export_record.json', ...readdirSync(from).filter((f) => /^background_.+\.json$/.test(f))];
for (const f of files) copyFileSync(join(from, f), join(out, f));
const snapshot = record.interim_snapshot ? ` (interim snapshot, epoch ${String(record.interim_snapshot.epoch)} of ${String(record.interim_snapshot.of_epochs)})` : '';
console.log(`probe:model: copied ${files.join(', ')} from ${from} to ${out}; ${String(record.model_id)}${snapshot}, IR ${String(ir)}, sha256 ${sha}`);
