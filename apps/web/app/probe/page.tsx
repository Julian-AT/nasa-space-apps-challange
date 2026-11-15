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
    <div>
      <PageHeader
        eyebrow="Live probe · browser worker"
        title="Live Earth probe"
        lead="Enter any point on Earth; your browser measures its terrain fingerprint from Copernicus GLO-30."
      />
      <p className="max-w-3xl text-sm text-muted-foreground">
        The physics fingerprint (Derived) is measured for every probe.{' '}
        {encoder.encoder === null
          ? 'The experimental learned encoder is not loaded in this build.'
          : `The experimental learned encoder ${encoder.encoder.spec.model_id} adds a learned terrain similarity alongside it (Predicted).`}
      </p>
      <Suspense fallback={<p className="mt-4">Loading the probe…</p>}>
        <ProbeClient
          targets={targets.fingerprints}
          targetsReason={targets.reason}
          bundleVersion={bundle.manifest.bundle_version}
          encoder={encoder.encoder}
        />
      </Suspense>
    </div>
  );
}
