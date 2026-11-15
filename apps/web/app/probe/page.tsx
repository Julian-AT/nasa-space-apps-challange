// /probe?lat=&lon= (PRD 8.7, T13-07): the live Earth probe. The page is static; the target
// fingerprints come from the release bundle at build time, and the window is read and measured
// in a Web Worker in the visitor's browser (workers/probe.worker.ts) through the GLO-30 proxy.
import type { Metadata } from 'next';
import { Suspense } from 'react';

import { loadBundle } from '../../lib/bundle.ts';
import { probeTargets } from '../../lib/probe/targets.ts';
import { SITE_TITLE } from '../../lib/shell.ts';
import { LIVE_PROBE_SENTENCE } from '../../lib/probe/result.ts';
import { ProbeClient } from './probe-client.tsx';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Live probe · ${SITE_TITLE}` };

export default function ProbePage() {
  const bundle = loadBundle();
  const targets = probeTargets(bundle);
  return (
    <>
      <h1>Live Earth probe</h1>
      <p className="mt-2 max-w-prose">{LIVE_PROBE_SENTENCE}</p>
      <p className="mt-1 max-w-prose text-sm">
        Enter a point on Earth. Your browser reads the 5 km window from Copernicus GLO-30 and measures it with the same
        resampler and terrain fingerprint as the pipeline. Nothing is stored or ranked.
      </p>
      <Suspense fallback={<p className="mt-4">Loading the probe…</p>}>
        <ProbeClient
          targets={targets.fingerprints}
          targetsReason={targets.reason}
          bundleVersion={bundle.manifest.bundle_version}
        />
      </Suspense>
    </>
  );
}
