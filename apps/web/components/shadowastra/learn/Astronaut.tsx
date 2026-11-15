'use client';

// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Terraluna does not ship the Astra mascot drawing; this keeps only its speech text, so the copied
// pages keep their words without the character.
import type { ReactNode } from 'react';

export function Astronaut({ say }: { say?: ReactNode; size?: number; mood?: string; action?: string; sayAlign?: string }) {
  if (!say) return null;
  return (
    <p className="max-w-[260px] rounded-2xl border border-line-2 bg-panel/70 px-4 py-3 text-[13px] leading-relaxed text-chrome">{say}</p>
  );
}
