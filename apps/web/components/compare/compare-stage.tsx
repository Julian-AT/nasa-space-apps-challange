'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The compare stage: the page header (title, pair select, Side by side | Swipe), then one card with
// the two hillshade panes at equal size (target left, Earth right). Leaflet loads only here,
// client-side. The view mode and swipe position live in the URL (?view=swipe&swipe=50).
import { CenterFocusIcon, LayoutTwoColumnIcon, ArrowLeftRightIcon, Link01Icon, Unlink01Icon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

import type { HillshadeMapHandle, LinkedView } from './hillshade-map.tsx';
import { type PairOption, PairSelect, Seg, WorldDot, type World } from './kit.tsx';
import type { LabPair, PaneLayer } from './lab-data.ts';
import { SwipeStage } from './swipe.tsx';

const HillshadeMap = dynamic(() => import('./hillshade-map.tsx'), {
  ssr: false,
  loading: () => <Skeleton className="size-full rounded-none" />,
});

type Mode = 'split' | 'swipe';

const fmtMpp = (mpp: number): string => (mpp >= 1000 ? `${(mpp / 1000).toFixed(2)} km/px` : `${mpp.toFixed(mpp < 10 ? 1 : 0)} m/px`);
const fmtLonLat = ([lon, lat]: readonly [number, number]): string =>
  `${Math.abs(lat).toFixed(3)}° ${lat < 0 ? 'S' : 'N'}, ${Math.abs(lon).toFixed(3)}° ${lon < 0 ? 'W' : 'E'}`;

function NoTiles({ what }: { readonly what: string }) {
  return (
    <div className="grid size-full place-items-center bg-muted p-6 text-center text-sm text-muted-foreground">
      <p>No hillshade tiles for the {what} window in this bundle.</p>
    </div>
  );
}

function ScaleChip({ pane, mpp }: { readonly pane: PaneLayer; readonly mpp: number | null }) {
  return (
    <div
      className="pointer-events-none absolute bottom-3 left-3 z-[600] rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground tabular-nums shadow-sm backdrop-blur"
      title={`${pane.evidenceLabel} · ${pane.hillshade}`}
    >
      {mpp === null ? pane.evidenceLabel : `${fmtMpp(mpp)} · ${pane.evidenceLabel}`}
    </div>
  );
}

function MapPane({
  title,
  world,
  pane,
  mpp,
  children,
}: {
  readonly title: string;
  readonly world: World;
  readonly pane: PaneLayer | null;
  readonly mpp: number | null;
  readonly children: React.ReactNode;
}) {
  return (
    <div className="relative aspect-square min-w-0 overflow-hidden bg-muted md:aspect-auto md:h-full">
      {children}
      <div className="pointer-events-none absolute top-3 left-3 z-[600] flex max-w-[calc(100%-1.5rem)] items-center gap-2 rounded-md bg-background/90 px-2.5 py-1.5 shadow-sm backdrop-blur">
        <WorldDot world={world} />
        <span className="truncate text-xs font-medium">{title}</span>
        {pane === null ? null : <span className="hidden text-[11px] text-muted-foreground tabular-nums sm:inline">{fmtLonLat(pane.centreLonLat)}</span>}
      </div>
      {pane === null ? null : <ScaleChip pane={pane} mpp={mpp} />}
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

  const targetMap =
    pair.target === null ? <NoTiles what="target" /> : <HillshadeMap key={`t-${mode}-${pair.cardId}`} pane={pair.target} label={pair.targetName} onView={onT} control={tRef} />;
  const earthMap =
    pair.earth === null ? <NoTiles what="Earth" /> : <HillshadeMap key={`e-${mode}-${pair.cardId}`} pane={pair.earth} label={pair.earthName} onView={onE} control={eRef} />;

  return (
    <>
      <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Compare</h1>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <PairSelect value={current} options={options} />
          <Seg
            label="View mode"
            value={mode}
            onChange={(v) => {
              setMode(v === 'swipe' ? 'swipe' : 'split');
            }}
            options={[
              { id: 'split', label: (<><HugeiconsIcon icon={LayoutTwoColumnIcon} size={14} aria-hidden="true" /> Side by side</>) },
              { id: 'swipe', label: (<><HugeiconsIcon icon={ArrowLeftRightIcon} size={14} aria-hidden="true" /> Swipe</>) },
            ]}
          />
        </div>
      </header>

      <section aria-label="Compare stage" className="mt-6 overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        {mode === 'split' ? (
          <div className="grid gap-px bg-border md:h-[min(68dvh,640px)] md:min-h-[480px] md:grid-cols-2">
            <MapPane title={pair.targetName} world={pair.body} pane={pair.target} mpp={mppT}>
              {targetMap}
            </MapPane>
            <MapPane title={pair.earthName} world="earth" pane={pair.earth} mpp={mppE}>
              {earthMap}
            </MapPane>
          </div>
        ) : (
          <SwipeStage
            className="aspect-square bg-muted md:aspect-auto md:h-[min(68dvh,640px)] md:min-h-[480px]"
            value={swipe}
            onChange={setSwipe}
            left={targetMap}
            right={earthMap}
            leftLabel={pair.targetName}
            rightLabel={pair.earthName}
            leftWorld={pair.body}
          />
        )}
        <div className="flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            Gold outline: the 5 km analysis window. Each map stays in its own projection; scroll with Ctrl or ⌘ to zoom, arrow keys pan a focused map.
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              type="button"
              variant={locked ? 'secondary' : 'outline'}
              size="sm"
              aria-pressed={locked}
              onClick={() => {
                setLocked((l) => !l);
              }}
            >
              <HugeiconsIcon icon={locked ? Link01Icon : Unlink01Icon} size={14} aria-hidden="true" />
              Matched scale
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={reset}>
              <HugeiconsIcon icon={CenterFocusIcon} size={14} aria-hidden="true" />
              Recentre
            </Button>
          </div>
        </div>
      </section>
    </>
  );
}
