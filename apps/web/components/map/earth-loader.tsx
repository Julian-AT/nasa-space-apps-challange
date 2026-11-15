'use client';
// Dynamic import of the Earth deck.gl canvas (PRD 8.10): browser only, explore routes only.
import dynamic from 'next/dynamic';

export const EarthCanvas = dynamic(() => import('./earth-canvas.tsx'), {
  ssr: false,
  loading: () => (
    <p role="status" className="p-4 text-sm text-muted-foreground">
      Loading the map renderer…
    </p>
  ),
});
