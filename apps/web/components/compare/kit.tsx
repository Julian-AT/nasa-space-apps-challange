'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Small controls of the compare lab: the world dot, the segmented switch and the pair select.
import { clsx } from 'clsx';
import { motion } from 'motion/react';
import { useRouter } from 'next/navigation';
import type { ReactNode } from 'react';

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
  const group = options.map((o) => o.id).join('');
  return (
    <div role="group" aria-label={label} className="relative flex w-fit gap-0.5 rounded-full border border-line bg-ink p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => {
            onChange(o.id);
          }}
          aria-pressed={value === o.id}
          className={clsx('relative flex min-h-9 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[12px] transition-colors', value === o.id ? 'text-void' : 'text-chrome hover:text-platinum')}
        >
          {value === o.id ? (
            <motion.span layoutId={`seg-${group}`} className="absolute inset-0 rounded-full bg-platinum" transition={{ type: 'spring', stiffness: 420, damping: 36 }} />
          ) : null}
          <span className="relative flex items-center gap-1.5">{o.label}</span>
        </button>
      ))}
    </div>
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
  const current = options.find((o) => o.href === value);
  return (
    <label className="flex min-w-0 max-w-full items-center gap-2 rounded-full border border-line-2 bg-ink py-1 pr-1 pl-2.5 transition-colors hover:border-chrome/50">
      <WorldDot world={current?.body ?? 'moon'} />
      <span className="sr-only">Card pair</span>
      <select
        value={value}
        onChange={(e) => {
          router.push(e.target.value, { scroll: false });
        }}
        className="min-w-0 max-w-[min(70vw,420px)] cursor-pointer truncate rounded-full bg-transparent py-1.5 pr-2 text-[13px] text-platinum outline-none"
      >
        {options.map((o) => (
          <option key={o.href} value={o.href} className="bg-panel">
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
