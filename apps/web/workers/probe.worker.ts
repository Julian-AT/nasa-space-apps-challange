// The live-probe Web Worker (PRD 8.7, T13-05). It only exposes lib/probe/pipeline.runProbe over
// postMessage; every result is plain data (structured clone). A newer request aborts and
// supersedes the one in flight, whose result is discarded. No DOM, React or Next import.

import { runProbe } from '../lib/probe/pipeline.ts';
import type { ProbeRequest, ProbeResult, ProbeTargetFingerprint } from '../lib/probe/result.ts';

export interface ProbeWorkerInit {
  type: 'init';
  mode: 'direct' | 'proxy';
  targets: ProbeTargetFingerprint[];
  targetsReason: string | null;
  bundleVersion: string | null;
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
  const { mode, targets, targetsReason, bundleVersion } = init;
  void runProbe(
    { id: message.id, latDeg: message.latDeg, lonDeg: message.lonDeg },
    {
      mode,
      origin: scope.location.origin,
      targets: targetsReason === null ? { fingerprints: targets, reason: null } : { fingerprints: [], reason: targetsReason },
      bundleVersion,
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
