'use client';
// The only way explore routes reach deck.gl (PRD 8.10): a dynamic import with ssr:false, so the
// deck.gl chunk is fetched in the browser on /explore/* and is in no other route's bundle.
import dynamic from 'next/dynamic';

export const DeckCanvas = dynamic(() => import('./deck-canvas.tsx'), {
  ssr: false,
  loading: () => (
    <p role="status" className="p-4 text-sm text-muted-foreground">
      Loading the map renderer…
    </p>
  ),
});
