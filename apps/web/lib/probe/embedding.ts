// The encoder embedding for the live probe (PRD 11.8, 11.10; T13-03, T13-06). The only module
// that imports onnxruntime-web, and only through a dynamic import(), so the initial JavaScript
// stays small. Every result is labelled Predicted with the manifest's model status and G5 status;
// the encoder is EXPERIMENTAL while G5 is not PASS, and nothing here ranks.

import { type Family, familyPercentile } from '@terraluna/core';

import { predictedLabel } from './result.ts';

/** PRD 8.7: the model download is at most 10 MB. */
export const MODEL_MAX_BYTES = 10 * 1024 * 1024;
export const EMBEDDING_DIM = 256;

export type EmbeddingFailure =
  | 'model_too_large'
  | 'model_sha256_mismatch'
  | 'model_fetch_failed'
  | 'model_load_failed'
  | 'embedding_zero_vector'
  | 'embedding_length_mismatch'
  | 'encoder_not_evaluated'
  | 'background_missing'
  | 'background_empty'
  | 'background_family_mismatch'
  | 'background_target_mismatch'
  | 'background_model_mismatch';

export type Scalar =
  | { status: 'available'; value: number; reason: null }
  | { status: 'unavailable'; value: null; reason: string };

/** The encoder as the bundle manifest describes it (ModelSummary plus the export record). */
export interface EncoderSpec {
  readonly url: string;
  readonly model_id: string;
  readonly onnx_sha256: string;
  readonly status: string;
  readonly g5_status: string;
  readonly input_name: string;
  readonly output_name: string;
  /** From export_record.json: whether L2 normalisation is inside the graph. Never guessed. */
  readonly l2_normalized: boolean;
}

/** ModelBackground (CONTRACTS a.4) of reference/background_<target_id>.json. */
export interface ModelBackground {
  readonly target_id: string;
  readonly family: Family;
  readonly model_id: string;
  readonly onnx_sha256: string;
  readonly bin_edges: readonly number[];
  readonly counts: readonly number[];
  readonly percentile_definition: 'linear_within_bin_v1';
  readonly direction: 'smaller_is_closer';
  readonly target_embedding: readonly number[];
}

const unavailable = (reason: string): Scalar => ({ status: 'unavailable', value: null, reason });

/** 1 - cos similarity in float64; a zero vector or a length mismatch is Unavailable, never NaN or 0. */
export function cosineDistance(a: ArrayLike<number>, b: ArrayLike<number>): Scalar {
  if (a.length !== b.length || a.length === 0) return unavailable('embedding_length_mismatch');
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i] as number;
    const y = b[i] as number;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  if (na === 0 || nb === 0) return unavailable('embedding_zero_vector');
  const cos = Math.max(-1, Math.min(1, dot / (Math.sqrt(na) * Math.sqrt(nb))));
  return { status: 'available', value: 1 - cos, reason: null };
}

/**
 * The percentile of a model distance against the model_embedding background of the same target,
 * model_id and onnx_sha256 only; any other histogram is Unavailable with its reason.
 */
export function modelPercentile(distance: number, targetId: string, background: ModelBackground | null, session: Pick<EncoderSpec, 'model_id' | 'onnx_sha256'>): Scalar {
  if (background === null) return unavailable('encoder_not_evaluated');
  if (background.family !== 'model_embedding') return unavailable('background_family_mismatch');
  if (background.target_id !== targetId) return unavailable('background_target_mismatch');
  if (background.model_id !== session.model_id || background.onnx_sha256 !== session.onnx_sha256) {
    return unavailable('background_model_mismatch');
  }
  if (background.counts.reduce((s, c) => s + c, 0) === 0) return unavailable('background_empty');
  const out = familyPercentile(distance, 'model_embedding', { ...background, family: 'model_embedding' });
  return out.status === 'available' ? { status: 'available', value: out.value, reason: null } : unavailable(out.reason);
}

export function embeddingLabel(spec: Pick<EncoderSpec, 'g5_status'>): string {
  return predictedLabel(spec.g5_status);
}

async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** A cache of verified model bytes (Cache API in a browser, a Map in tests). */
export interface ModelCache {
  get(key: string): Promise<ArrayBuffer | undefined>;
  put(key: string, bytes: ArrayBuffer): Promise<void>;
}

export function memoryModelCache(): ModelCache & { size: () => number } {
  const map = new Map<string, ArrayBuffer>();
  return {
    get: (key) => Promise.resolve(map.get(key)),
    put: (key, bytes) => {
      map.set(key, bytes);
      return Promise.resolve();
    },
    size: () => map.size,
  };
}

export class EmbeddingError extends Error {
  override name = 'EmbeddingError';
  readonly reason: EmbeddingFailure;
  constructor(reason: EmbeddingFailure, message: string) {
    super(message);
    this.reason = reason;
  }
}

/** Fetch the model once, refuse more than 10 MB, verify its SHA-256 before anything uses it. */
export async function loadModelBytes(spec: EncoderSpec, cache: ModelCache, doFetch: typeof fetch = fetch): Promise<ArrayBuffer> {
  const cached = await cache.get(spec.onnx_sha256);
  if (cached !== undefined && (await sha256Hex(cached)) === spec.onnx_sha256) return cached;
  let response: Response;
  try {
    response = await doFetch(spec.url);
  } catch (error) {
    throw new EmbeddingError('model_fetch_failed', `the model could not be fetched: ${(error as Error).message}`);
  }
  if (!response.ok) throw new EmbeddingError('model_fetch_failed', `the model answered HTTP ${String(response.status)}`);
  const declared = Number(response.headers.get('content-length') ?? Number.NaN);
  if (declared > MODEL_MAX_BYTES) {
    await response.body?.cancel();
    throw new EmbeddingError('model_too_large', `the model is ${String(declared)} bytes, over the 10 MB budget`);
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > MODEL_MAX_BYTES) throw new EmbeddingError('model_too_large', 'the model is over the 10 MB budget');
  if ((await sha256Hex(bytes)) !== spec.onnx_sha256) {
    throw new EmbeddingError('model_sha256_mismatch', 'the model bytes do not match the manifest SHA-256');
  }
  await cache.put(spec.onnx_sha256, bytes);
  return bytes;
}

export interface EmbeddingSession {
  readonly spec: EncoderSpec;
  embed(input: Float32Array, batch: number): Promise<Float32Array>;
}

/** Create the onnxruntime-web WASM session (dynamic import; worker only). */
export async function createSession(spec: EncoderSpec, bytes: ArrayBuffer, options: { wasmPaths?: string; threads?: number } = {}): Promise<EmbeddingSession> {
  let ort: typeof import('onnxruntime-web/wasm');
  try {
    ort = await import('onnxruntime-web/wasm');
    if (options.wasmPaths !== undefined) ort.env.wasm.wasmPaths = options.wasmPaths;
    ort.env.wasm.numThreads = options.threads ?? 1;
  } catch (error) {
    throw new EmbeddingError('model_load_failed', `onnxruntime-web could not load: ${(error as Error).message}`);
  }
  let session: import('onnxruntime-web/wasm').InferenceSession;
  try {
    session = await ort.InferenceSession.create(new Uint8Array(bytes), { executionProviders: ['wasm'] });
  } catch (error) {
    throw new EmbeddingError('model_load_failed', `the ONNX session could not be created: ${(error as Error).message}`);
  }
  return {
    spec,
    async embed(input: Float32Array, batch: number): Promise<Float32Array> {
      const tensor = new ort.Tensor('float32', input, [batch, 3, 64, 64]);
      const out = await session.run({ [spec.input_name]: tensor });
      const result = out[spec.output_name];
      if (result === undefined) throw new EmbeddingError('model_load_failed', `the model has no output ${spec.output_name}`);
      const data = result.data;
      if (!(data instanceof Float32Array) || data.length !== batch * EMBEDDING_DIM) {
        throw new EmbeddingError('embedding_length_mismatch', 'the model output is not [N, 256] float32');
      }
      return data;
    },
  };
}
