'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The target filter of the card atlas. Tiles arrive pre-rendered from the server; the filter only
// hides them. State lives in the URL (?target=<segment id>): "all", a body ("moon", "mars") or a
// target id.
import { useEffect, useState, type ReactNode } from 'react';

import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

export interface AtlasSegment {
  readonly id: string;
  readonly label: string;
  readonly body: 'moon' | 'mars';
}

export interface AtlasItem {
  readonly cardId: string;
  readonly segment: string;
  readonly body: 'moon' | 'mars';
  readonly node: ReactNode;
}

const BODIES = [
  { id: 'moon', label: 'Moon' },
  { id: 'mars', label: 'Mars' },
] as const;

export function CardAtlas({ segments, items }: { readonly segments: readonly AtlasSegment[]; readonly items: readonly AtlasItem[] }) {
  const [active, setActive] = useState('all');
  const bodies = BODIES.filter((b) => items.some((i) => i.body === b.id));
  const options = [
    { id: 'all', label: 'All' },
    ...bodies,
    ...(segments.length > bodies.length ? segments : []),
  ];
  const ids = options.map((o) => o.id).join('|');
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('target');
    if (t !== null && ids.split('|').includes(t)) setActive(t);
  }, [ids]);
  const choose = (id: string): void => {
    setActive(id);
    const url = new URL(window.location.href);
    if (id === 'all') url.searchParams.delete('target');
    else url.searchParams.set('target', id);
    window.history.replaceState(null, '', url);
  };
  const match = (i: AtlasItem, id: string): boolean => id === 'all' || i.body === id || i.segment === id;
  const shown = items.filter((i) => match(i, active));
  return (
    <div className="flex flex-col gap-6">
      <ToggleGroup
        type="single"
        variant="outline"
        value={active}
        onValueChange={(v) => { if (v !== '') choose(v); }}
        aria-label="Filter cards by body or target"
        className="flex-wrap"
      >
        {options.map((o, k) => {
          const n = items.filter((i) => match(i, o.id)).length;
          return (
            <ToggleGroupItem
              key={o.id}
              value={o.id}
              aria-label={`${o.label}, ${String(n)} cards`}
              className={k === bodies.length + 1 ? 'h-9 gap-2 rounded-full px-4 text-sm sm:ml-4' : 'h-9 gap-2 rounded-full px-4 text-sm'}
            >
              {o.label}
              <span className="text-xs text-muted-foreground tabular-nums">{n}</span>
            </ToggleGroupItem>
          );
        })}
      </ToggleGroup>
      <p role="status" className="sr-only">{shown.length} cards shown</p>
      <ul className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {shown.map((i) => (
          <li key={i.cardId} className="min-w-0">{i.node}</li>
        ))}
      </ul>
    </div>
  );
}
