// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The three-column Maps layout (PRD 8.6, 10): a left rounded panel, a centre stage with the world
// switcher and a large world title over the map, and a right rounded panel. The planet chips are
// plain CSS gradients in the world colours; no image is used. Server component.
import Link from 'next/link';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export type World = 'earth' | 'moon' | 'mars';

const WORLDS: readonly { readonly id: World; readonly label: string; readonly dot: string }[] = [
  { id: 'earth', label: 'Earth', dot: 'radial-gradient(circle at 35% 30%, #cfe0ff, #8fb4ea 45%, #24406e)' },
  { id: 'moon', label: 'Moon', dot: 'radial-gradient(circle at 35% 30%, #f1eee7, #c9c5bc 45%, #55524b)' },
  { id: 'mars', label: 'Mars', dot: 'radial-gradient(circle at 35% 30%, #f6b28d, #e08a5f 45%, #6d2f17)' },
];

export function WorldSwitcher({ active }: { readonly active: World }) {
  return (
    <nav aria-label="World" className="pointer-events-auto flex gap-1 rounded-full border border-line-2 bg-void/70 p-1 backdrop-blur-xl">
      {WORLDS.map((w) => (
        <Link
          key={w.id}
          href={`/explore/${w.id}`}
          aria-current={w.id === active ? 'page' : undefined}
          className={cn(
            'flex items-center gap-2 rounded-full py-1 pr-3 pl-1 text-[13px] no-underline transition-colors focus-visible:outline-2 focus-visible:outline-ring sm:pr-4',
            w.id === active ? 'bg-platinum/[0.1] text-platinum ring-1 ring-platinum/15' : 'text-chrome hover:text-platinum',
          )}
        >
          <span aria-hidden="true" className="size-6 rounded-full" style={{ backgroundImage: w.dot }} />
          {w.label}
        </Link>
      ))}
    </nav>
  );
}

export function AtlasPanel({ label, className, children }: { readonly label: string; readonly className?: string; readonly children: ReactNode }) {
  return (
    <aside
      aria-label={label}
      className={cn(
        'flex min-w-0 flex-col gap-5 rounded-3xl border border-line-2 bg-panel/95 p-4 text-card-foreground shadow-[0_30px_80px_-40px_rgba(0,0,0,0.9)] lg:min-h-0 lg:overflow-y-auto [&_[data-slot=labelled-value]]:max-w-full [&_[data-slot=source]]:[overflow-wrap:anywhere]',
        className,
      )}
    >
      {children}
    </aside>
  );
}

export function PanelHeading({ children, count }: { readonly children: ReactNode; readonly count?: number | string | undefined }) {
  return (
    <h2 className="flex items-baseline justify-between font-mono text-[0.7rem] font-semibold tracking-[0.2em] text-dim uppercase">
      <span>{children}</span>
      {count === undefined ? null : <span className="text-chrome">{count}</span>}
    </h2>
  );
}

/** A pill chip button used for filters (flare outline when active). */
export function FilterChip({
  active,
  onClick,
  children,
}: {
  readonly active: boolean;
  readonly onClick: () => void;
  readonly children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1.5 text-[12px] transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-ring',
        active ? 'border-flare/60 bg-flare/10 text-platinum' : 'border-line-2 text-chrome hover:border-chrome',
      )}
    >
      {children}
    </button>
  );
}

export interface AtlasShellProps {
  readonly world: World;
  readonly title: string;
  /** Mono subtitle: real counts from the bundle. */
  readonly subtitle: string;
  readonly left: ReactNode;
  readonly right: ReactNode;
  readonly leftLabel: string;
  readonly rightLabel: string;
  readonly children: ReactNode;
}

export function AtlasShell({ world, title, subtitle, left, right, leftLabel, rightLabel, children }: AtlasShellProps) {
  return (
    <div data-full-bleed data-slot="atlas" className="px-3 pb-4 sm:px-4 lg:h-[calc(100dvh-7.75rem)] lg:pb-3">
      <div className="grid grid-cols-1 gap-3 lg:h-full lg:grid-cols-[19rem_minmax(0,1fr)_21.5rem]">
        <AtlasPanel label={leftLabel} className="order-2 lg:order-1">
          {left}
        </AtlasPanel>
        <section aria-label={`${title} map`} className="relative order-1 h-[72svh] min-h-[26rem] min-w-0 overflow-hidden rounded-3xl border border-line-2 bg-void lg:order-2 lg:h-full">
          <div className="absolute inset-x-0 top-[9.75rem] bottom-0 sm:top-[11.5rem]">{children}</div>
          <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex flex-col gap-3 border-b border-line bg-void/80 p-3 sm:p-4">
            <div className="flex justify-center">
              <WorldSwitcher active={world} />
            </div>
            <div className="px-1 sm:px-2">
              <h1 className="font-serif text-4xl leading-none tracking-tight text-platinum sm:text-6xl">{title}</h1>
              <p className="mt-2 font-mono text-[11px] tracking-wide text-chrome sm:text-xs">{subtitle}</p>
            </div>
          </div>
        </section>
        <AtlasPanel label={rightLabel} className="order-3">
          {right}
        </AtlasPanel>
      </div>
    </div>
  );
}
