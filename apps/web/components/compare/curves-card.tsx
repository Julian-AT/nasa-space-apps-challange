'use client';
// The one chart card of the compare page: the stored nu(L) curves of the target and the Earth
// window, with a Relief | Texture switch. Series are built on the server from the bundle's
// fingerprints (charts/series.ts); nothing here computes or resamples a value.
import { useState } from 'react';

import { NuChart } from '@/components/charts/nu-chart';
import type { NamedSeries } from '@/components/charts/series';
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

import { Seg } from './kit.tsx';

type Observable = 'relief' | 'texture';

function LegendItem({ dashed, label }: { readonly dashed: boolean; readonly label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg aria-hidden="true" width="28" height="10" viewBox="0 0 28 10" className={dashed ? 'text-muted-foreground' : 'text-primary'}>
        <line x1="1" y1="5" x2="27" y2="5" stroke="currentColor" strokeWidth="2" strokeDasharray={dashed ? '6 4' : undefined} />
        {dashed ? <rect x="10" y="1" width="8" height="8" fill="currentColor" /> : <circle cx="14" cy="5" r="4" fill="currentColor" />}
      </svg>
      {label}
    </span>
  );
}

export function CurvesCard({
  targetName,
  earthName,
  series,
}: {
  readonly targetName: string;
  readonly earthName: string;
  readonly series: Readonly<Record<Observable, readonly NamedSeries[]>>;
}) {
  const [obs, setObs] = useState<Observable>(series.relief.length > 0 ? 'relief' : 'texture');
  const shown = series[obs];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Structure function ν(L)</CardTitle>
        <CardDescription>How roughness grows with lag L, on log-log axes. Closer curves mean more similar terrain across scales.</CardDescription>
        <CardAction>
          <Seg
            label="Observable"
            value={obs}
            onChange={(v) => {
              setObs(v === 'texture' ? 'texture' : 'relief');
            }}
            options={[
              { id: 'relief', label: 'Relief' },
              { id: 'texture', label: 'Texture' },
            ]}
          />
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <LegendItem dashed={false} label={targetName} />
          <LegendItem dashed label={earthName} />
        </div>
        {shown.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">No {obs} fingerprint for these windows in this bundle.</p>
        ) : (
          <NuChart
            key={obs}
            compact
            name={`${obs === 'relief' ? 'Relief' : 'Texture'} ν(L), ${targetName} and ${earthName}`}
            series={shown}
          />
        )}
      </CardContent>
    </Card>
  );
}
