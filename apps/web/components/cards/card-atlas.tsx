'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The segmented target filter of the card atlas. Tiles arrive pre-rendered from the server; the
// filter only hides them. State lives in the URL (?target=<segment id>).
import { useEffect, useState, type ReactNode } from 'react';

export interface AtlasSegment {
  readonly id: string;
  readonly label: string;
  readonly colour: string;
}

export interface AtlasItem {
  readonly cardId: string;
  readonly segment: string;
  readonly node: ReactNode;
}

export function CardAtlas({ segments, items }: { readonly segments: readonly AtlasSegment[]; readonly items: readonly AtlasItem[] }) {
  const [active, setActive] = useState('all');
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('target');
    if (t !== null && segments.some((s) => s.id === t)) setActive(t);
  }, [segments]);
  const choose = (id: string): void => {
    setActive(id);
    const url = new URL(window.location.href);
    if (id === 'all') url.searchParams.delete('target');
    else url.searchParams.set('target', id);
    window.history.replaceState(null, '', url);
  };
  const shown = items.filter((i) => active === 'all' || i.segment === active);
  const all: AtlasSegment = { id: 'all', label: 'All', colour: 'transparent' };
  return (
    <div className="flex flex-col gap-6">
      <div role="group" aria-label="Filter cards by target" className="flex flex-wrap gap-2">
        {[all, ...segments].map((s) => {
          const n = s.id === 'all' ? items.length : items.filter((i) => i.segment === s.id).length;
          const on = s.id === active;
          return (
            <button
              key={s.id}
              type="button"
              aria-pressed={on}
              onClick={() => { choose(s.id); }}
              className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-sm transition-colors motion-reduce:transition-none ${on ? 'border-flare bg-flare/15 text-foreground' : 'border-line text-muted-foreground hover:border-line-2 hover:text-foreground'}`}
            >
              {s.id === 'all' ? null : <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: s.colour }} />}
              {s.label}
              <span className="font-mono text-xs text-muted-foreground">{n}</span>
            </button>
          );
        })}
      </div>
      <p role="status" className="sr-only">{shown.length} cards shown</p>
      <ul className="grid gap-5 md:grid-cols-2 2xl:grid-cols-3">
        {shown.map((i) => (
          <li key={i.cardId} className="min-w-0">{i.node}</li>
        ))}
      </ul>
    </div>
  );
}
