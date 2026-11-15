'use client';

// Client-only loader for the ported ShadowAstra lab: Leaflet and WebGL need `window`.
import dynamic from 'next/dynamic';
import { useEffect } from 'react';

const LabPage = dynamic(() => import('./LabPage.tsx'), {
  ssr: false,
  loading: () => <p className="m-6">Loading the 3D comparison…</p>,
});

export default function LabClient() {
  // ShadowAstra's benchmark hook (upstream src/main.tsx): a separate chunk only ?bench=1 visits download.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has('bench')) {
      void import('../../../lib/shadowastra-lab/bench.ts').then((m) => {
        m.install();
      });
    }
  }, []);
  return <LabPage />;
}
