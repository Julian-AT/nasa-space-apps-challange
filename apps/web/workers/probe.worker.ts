// The live-probe Web Worker (PRD 8.7, T13-05). It only exposes lib/probe/pipeline.runProbe over
// postMessage; every result is plain data (structured clone). A newer request aborts and
// supersedes the one in flight, whose result is discarded. No DOM, React or Next import.

import { type EmbeddingSession, type EncoderSpec, type ModelBackground, type ModelCache, createSession, loadModelBytes, memoryModelCache } from '../lib/probe/embedding.ts';
import { runProbe } from '../lib/probe/pipeline.ts';
import type { ProbeRequest, ProbeResult, ProbeTargetFingerprint } from '../lib/probe/result.ts';

export interface ProbeWorkerInit {
  type: 'init';
  mode: 'direct' | 'proxy';
  targets: ProbeTargetFingerprint[];
  targetsReason: string | null;
  bundleVersion: string | null;
  /** The bundle's exported encoder and each target's ModelBackground, or null if it ships none. */
  encoder: { spec: EncoderSpec; backgrounds: Record<string, ModelBackground | null> } | null;
}

export type ProbeWorkerMessage = ProbeWorkerInit | ({ type: 'probe' } & ProbeRequest) | { type: 'cancel' };

interface WorkerScope {
  onmessage: ((event: MessageEvent<ProbeWorkerMessage>) => void) | null;
  postMessage(message: ProbeResult): void;
  location: { origin: string };
}

const scope = globalThis as unknown as WorkerScope;
let init: ProbeWorkerInit | null = null;
let inFlight: { id: number; controller: AbortController } | null = null;
let session: Promise<EmbeddingSession> | null = null;

/** Cache API storage for verified model bytes; an in-memory map where the Cache API is absent. */
function modelCache(): ModelCache {
  if (typeof caches === 'undefined') return memoryModelCache();
  return {
    get: async (key) => {
      const hit = await (await caches.open('terraluna-model')).match(key);
      return hit ? hit.arrayBuffer() : undefined;
    },
    put: async (key, bytes) => {
      await (await caches.open('terraluna-model')).put(key, new Response(bytes));
    },
  };
}

/** One session per worker: the model is fetched, verified and loaded once, then reused. */
function sessionFor(spec: EncoderSpec): Promise<EmbeddingSession> {
  session ??= loadModelBytes(spec, modelCache())
    .then((bytes) => createSession(spec, bytes, { threads: self.crossOriginIsolated ? 4 : 1 }))
    .catch((error: unknown) => {
      session = null;
      throw error;
    });
  return session;
}

scope.onmessage = (event) => {
  const message = event.data;
  if (message.type === 'init') {
    init = message;
    return;
  }
  if (message.type === 'cancel') {
    inFlight?.controller.abort();
    return;
  }
  if (init === null) {
    scope.postMessage({ kind: 'failure', request_id: message.id, reason: 'invalid_request', detail: 'the worker was not initialised' });
    return;
  }
  inFlight?.controller.abort();
  const controller = new AbortController();
  const current = { id: message.id, controller };
  inFlight = current;
  const { mode, targets, targetsReason, bundleVersion, encoder } = init;
  void runProbe(
    { id: message.id, latDeg: message.latDeg, lonDeg: message.lonDeg },
    {
      mode,
      origin: scope.location.origin,
      targets: targetsReason === null ? { fingerprints: targets, reason: null } : { fingerprints: [], reason: targetsReason },
      bundleVersion,
      encoder: encoder === null ? null : { spec: encoder.spec, backgrounds: encoder.backgrounds, session: () => sessionFor(encoder.spec) },
    },
    controller.signal,
  ).then(
    (result) => {
      if (inFlight !== current) return; // superseded: discard
      inFlight = null;
      scope.postMessage(result);
    },
    (error: unknown) => {
      if (inFlight !== current) return;
      inFlight = null;
      scope.postMessage({ kind: 'failure', request_id: message.id, reason: 'reader_failed', detail: String(error) });
    },
  );
};
