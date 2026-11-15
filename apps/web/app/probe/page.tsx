// /probe?lat=&lon= (PRD 8.7, T13-07): the live Earth probe. The page is static; the target
// fingerprints come from the release bundle at build time, and the window is read and measured
// in a Web Worker in the visitor's browser (workers/probe.worker.ts) through the GLO-30 proxy.
import type { Metadata } from 'next';
import { Suspense } from 'react';

import { PageHeader } from '../../components/page-header.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { probeEncoder } from '../../lib/probe/encoder-source.ts';
import { probeTargets } from '../../lib/probe/targets.ts';
import { SITE_TITLE } from '../../lib/shell.ts';
import { LIVE_PROBE_SENTENCE } from '../../lib/probe/result.ts';
import { ProbeClient } from './probe-client.tsx';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Live probe · ${SITE_TITLE}` };

export default function ProbePage() {
  const bundle = loadBundle();
  const targets = probeTargets(bundle);
  const encoder = probeEncoder(bundle);
  return (
    <>
      <PageHeader
        eyebrow="Live probe · browser worker"
        title="Live Earth probe"
        lead={`${LIVE_PROBE_SENTENCE} Enter a point on Earth. Your browser reads the 5 km window from Copernicus GLO-30 and measures it with the same resampler and terrain fingerprint as the pipeline. Nothing is stored or ranked.`}
      />
      <div>
      <p className="mt-3 max-w-3xl rounded-lg border bg-card px-4 py-3 text-sm text-muted-foreground">
        {encoder.encoder === null
          ? `Encoder (experimental): Unavailable: ${encoder.reason}.`
          : `Encoder: EXPERIMENTAL, ${encoder.encoder.spec.model_id}${encoder.encoder.spec.snapshot_note ? ` (${encoder.encoder.spec.snapshot_note}, not the final model)` : ''}, G5 ${encoder.encoder.spec.g5_status}. Its distances are Predicted and never rank anything.`}
        </p>
      </div>
      <Suspense fallback={<p className="mt-4">Loading the probe…</p>}>
        <ProbeClient
          targets={targets.fingerprints}
          targetsReason={targets.reason}
          bundleVersion={bundle.manifest.bundle_version}
          encoder={encoder.encoder}
        />
      </Suspense>
    </>
  );
}
