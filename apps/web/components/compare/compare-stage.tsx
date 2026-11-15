'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The compare stage: the pair bar (every card of the bundle), Side by side | Swipe, linked views
// and the two hillshade panes (target left, Earth right). Leaflet loads only here, client-side.
// The view mode and swipe position live in the URL (?view=swipe&swipe=50).
import { clsx } from 'clsx';
import { Columns2, Link2, Link2Off, LocateFixed, SplitSquareHorizontal } from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import { buttonVariants } from '@/components/ui/button';

import type { HillshadeMapHandle, LinkedView } from './hillshade-map.tsx';
import { type PairOption, PairSelect, Seg, WorldDot, type World } from './kit.tsx';
import type { LabPair, PaneLayer } from './lab-data.ts';
import { SwipeStage } from './swipe.tsx';

const HillshadeMap = dynamic(() => import('./hillshade-map.tsx'), {
  ssr: false,
  loading: () => (
    <div className="grid size-full place-items-center font-mono text-[11px] tracking-widest text-chrome uppercase">Loading hillshade tiles</div>
  ),
});

type Mode = 'split' | 'swipe';

const fmtMpp = (mpp: number): string => (mpp >= 1000 ? `${(mpp / 1000).toFixed(2)} km/px` : `${mpp.toFixed(mpp < 10 ? 1 : 0)} m/px`);
const fmtLonLat = ([lon, lat]: readonly [number, number]): string =>
  `${Math.abs(lat).toFixed(3)}° ${lat < 0 ? 'S' : 'N'}, ${Math.abs(lon).toFixed(3)}° ${lon < 0 ? 'W' : 'E'}`;

function Unavailable({ what }: { readonly what: string }) {
  return (
    <div className="grid size-full place-items-center p-6 text-center text-sm text-chrome">
      <p>
        Unavailable — no bundle hillshade tile layer covers the {what} window (targets.json tile_layers).
      </p>
    </div>
  );
}

function PaneFooter({ pane, mpp }: { readonly pane: PaneLayer; readonly mpp: number | null }) {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 z-[600] max-w-[calc(100%-5rem)] rounded-md bg-background/85 px-2 py-1 font-mono text-[10px] leading-snug text-muted-foreground backdrop-blur">
      <span className="text-chrome">{pane.evidenceLabel}</span> · {pane.hillshade}
      {mpp === null ? null : ` · ${fmtMpp(mpp)}`}
    </div>
  );
}

function MapPane({
  title,
  world,
  pane,
  mpp,
  border,
  children,
}: {
  readonly title: string;
  readonly world: World;
  readonly pane: PaneLayer | null;
  readonly mpp: number | null;
  readonly border?: boolean;
  readonly children: React.ReactNode;
}) {
  return (
    <div className={clsx('relative h-[46vh] min-h-[320px] min-w-0 md:h-full', border === true && 'border-t border-line md:border-t-0 md:border-l')}>
      {children}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-[600] flex items-start justify-between gap-2 p-3">
        <div className="pointer-events-auto min-w-0 rounded-lg border bg-popover/95 px-3 py-2 shadow-sm">
          <div className="flex items-center gap-2 text-[13px] font-medium">
            <WorldDot world={world} /> <span className="truncate">{title}</span>
          </div>
          {pane === null ? null : <div className="font-mono text-[10px] text-dim tabular-nums">{fmtLonLat(pane.centreLonLat)}</div>}
        </div>
      </div>
      {pane === null ? null : <PaneFooter pane={pane} mpp={mpp} />}
    </div>
  );
}

export interface CompareStageProps {
  readonly pair: LabPair;
  readonly current: string;
  readonly options: readonly PairOption[];
}

export function CompareStage({ pair, current, options }: CompareStageProps) {
  const [mode, setMode] = useState<Mode>('split');
  const [swipe, setSwipe] = useState(0.5);
  const [locked, setLocked] = useState(true);
  const [mppT, setMppT] = useState<number | null>(null);
  const [mppE, setMppE] = useState<number | null>(null);
  const tRef = useRef<HillshadeMapHandle>(null);
  const eRef = useRef<HillshadeMapHandle>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get('view') === 'swipe') setMode('swipe');
    const s = Number(q.get('swipe'));
    if (Number.isFinite(s) && s >= 3 && s <= 97) setSwipe(s / 100);
  }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (mode === 'split') {
      url.searchParams.delete('view');
      url.searchParams.delete('swipe');
    } else {
      url.searchParams.set('view', 'swipe');
      url.searchParams.set('swipe', String(Math.round(swipe * 100)));
    }
    window.history.replaceState(window.history.state, '', url);
  }, [mode, swipe]);

  const onT = useCallback(
    (v: LinkedView) => {
      setMppT(v.mpp);
      if (locked) {
        eRef.current?.setLinkedView(v);
        setMppE(v.mpp);
      }
    },
    [locked],
  );
  const onE = useCallback(
    (v: LinkedView) => {
      setMppE(v.mpp);
      if (locked) {
        tRef.current?.setLinkedView(v);
        setMppT(v.mpp);
      }
    },
    [locked],
  );
  const reset = (): void => {
    tRef.current?.reset();
    eRef.current?.reset();
  };

  const targetMap = pair.target === null ? <Unavailable what="target" /> : <HillshadeMap key={`t-${mode}-${pair.cardId}`} pane={pair.target} label={pair.targetName} onView={onT} control={tRef} />;
  const earthMap = pair.earth === null ? <Unavailable what="Earth" /> : <HillshadeMap key={`e-${mode}-${pair.cardId}`} pane={pair.earth} label={pair.earthName} onView={onE} control={eRef} />;

  return (
    <section aria-label="Compare stage" className="border-y border-line">
      <div className="border-b bg-background">
        <div className="flex flex-wrap items-center gap-2.5 px-4 py-3 sm:px-6">
          <PairSelect value={current} options={options} />
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
            <Seg
              label="View mode"
              value={mode}
              onChange={(v) => {
                setMode(v === 'swipe' ? 'swipe' : 'split');
              }}
              options={[
                { id: 'split', label: (<><Columns2 size={13} aria-hidden="true" /> Side by side</>) },
                { id: 'swipe', label: (<><SplitSquareHorizontal size={13} aria-hidden="true" /> Swipe</>) },
              ]}
            />
            <button
              type="button"
              onClick={() => {
                setLocked((l) => !l);
              }}
              aria-pressed={locked}
              className={clsx(buttonVariants({ variant: locked ? 'secondary' : 'outline', size: 'sm' }), 'h-9 gap-1.5 text-xs', locked && 'text-primary')}
            >
              {locked ? <Link2 size={13} aria-hidden="true" /> : <Link2Off size={13} aria-hidden="true" />} Matched scale
            </button>
            <button type="button" onClick={reset} className={clsx(buttonVariants({ variant: 'outline', size: 'sm' }), 'h-9 gap-1.5 text-xs')}>
              <LocateFixed size={13} aria-hidden="true" /> Window
            </button>
          </div>
        </div>
        <nav aria-label="All card pairs" className="scrollbar-thin flex items-center gap-2 overflow-x-auto border-t border-line px-4 py-2 sm:px-6">
          <span className="shrink-0 text-xs font-medium text-muted-foreground">Cards</span>
          {options.map((o, i) => {
            const on = o.href === current;
            return (
              <Link
                key={o.href}
                href={o.href}
                scroll={false}
                aria-current={on ? 'page' : undefined}
                className={clsx(buttonVariants({ variant: on ? 'secondary' : 'ghost', size: 'sm' }), 'h-7 shrink-0 gap-1.5 font-mono text-xs no-underline', on && 'text-primary')}
              >
                <WorldDot world={o.body} />
                {String(i + 1).padStart(2, '0')}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="relative">
        {mode === 'split' ? (
          <div className="relative grid md:h-[min(72dvh,720px)] md:min-h-[520px] md:grid-cols-2">
            <MapPane title={pair.targetName} world={pair.body} pane={pair.target} mpp={mppT}>
              {targetMap}
            </MapPane>
            <MapPane title={pair.earthName} world="earth" pane={pair.earth} mpp={mppE} border>
              {earthMap}
            </MapPane>
            <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-1/2 z-[650] hidden w-[2px] -translate-x-px bg-[linear-gradient(180deg,transparent,rgba(242,200,121,0.55)_30%,rgba(242,200,121,0.55)_70%,transparent)] md:block" />
          </div>
        ) : (
          <SwipeStage
            className="h-[70dvh] md:h-[min(72dvh,720px)] md:min-h-[520px]"
            value={swipe}
            onChange={setSwipe}
            left={targetMap}
            right={earthMap}
            leftLabel={pair.targetName}
            rightLabel={pair.earthName}
            leftWorld={pair.body}
          />
        )}
      </div>
      <p className="px-4 py-2 text-[12px] text-dim sm:px-6">
        Gold outline: the 5 km analysis window. Each pane stays in its own frame ({pair.target?.units ?? 'target frame unavailable'}; {pair.earth?.units ?? 'Earth frame unavailable'}); Matched scale links
        ground metres per pixel and the offset from each window centre. Ctrl or ⌘ + scroll zooms; focus a map to pan with the arrow keys and zoom with + and −.
      </p>
    </section>
  );
}
