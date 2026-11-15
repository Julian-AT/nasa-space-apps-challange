'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Small controls of the compare lab: the world dot, the segmented switch and the pair select.
import { clsx } from 'clsx';
import { useRouter } from 'next/navigation';
import type { ReactNode } from 'react';

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

export type World = 'moon' | 'mars' | 'earth';

export function WorldDot({ world, className }: { readonly world: World; readonly className?: string }) {
  return <span aria-hidden="true" className={clsx('inline-block size-2 shrink-0 rounded-full', className)} style={{ background: `var(--${world})` }} />;
}

export function Seg({
  value,
  onChange,
  options,
  label,
}: {
  readonly value: string;
  readonly onChange: (v: string) => void;
  readonly options: readonly { readonly id: string; readonly label: ReactNode }[];
  readonly label: string;
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      spacing={0}
      aria-label={label}
      value={value}
      onValueChange={(v) => {
        if (v !== '') onChange(v);
      }}
    >
      {options.map((o) => (
        <ToggleGroupItem key={o.id} value={o.id} className="h-9 gap-1.5 px-3 text-xs">
          {o.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

export interface PairOption {
  readonly href: string;
  readonly label: string;
  readonly body: 'moon' | 'mars';
}

/** The pair switcher: every card of the bundle; choosing one navigates to its compare route. */
export function PairSelect({ value, options }: { readonly value: string; readonly options: readonly PairOption[] }) {
  const router = useRouter();
  return (
    <Select
      value={value}
      onValueChange={(v) => {
        router.push(v, { scroll: false });
      }}
    >
      <SelectTrigger aria-label="Card pair" className="h-9 max-w-[min(85vw,460px)] min-w-0 [&>span]:truncate">
        <SelectValue placeholder="Card pair" />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.href} value={o.href}>
            <WorldDot world={o.body} />
            <span className="truncate">{o.label}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
