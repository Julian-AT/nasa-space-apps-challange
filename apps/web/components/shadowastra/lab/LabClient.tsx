'use client';

// Client-only loader for the ported ShadowAstra lab: Leaflet and WebGL need `window`.
import dynamic from 'next/dynamic';

const LabPage = dynamic(() => import('./LabPage.tsx'), {
  ssr: false,
  loading: () => <p className="m-6">Loading the 3D comparison…</p>,
});

export default function LabClient() {
  return <LabPage />;
}
