'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Small display primitives of the /find flow (Chip, Meter, Ring, Seg, Counter). They only draw
// values handed to them; a null value draws as unavailable, never as zero.
import clsx from 'clsx';
import { animate, motion, useInView, useReducedMotion } from 'motion/react';
import { type ReactNode, useEffect, useId, useRef } from 'react';

export const EASE = [0.22, 1, 0.36, 1] as const;

const CHIP = {
  default: 'border-line-2 bg-platinum/[0.035] text-chrome',
  flare: 'border-flare/35 bg-flare/10 text-flare',
  moon: 'border-moon/35 bg-moon/10 text-moon',
  mars: 'border-mars/35 bg-mars/10 text-mars',
  earth: 'border-earth/35 bg-earth/10 text-earth',
  derived: 'border-evidence-derived/40 bg-evidence-derived/10 text-evidence-derived',
  predicted: 'border-evidence-predicted/40 bg-evidence-predicted/10 text-evidence-predicted',
  unavailable: 'border-evidence-unavailable/40 bg-evidence-unavailable/10 text-evidence-unavailable',
  fail: 'border-status-fail/40 bg-status-fail/10 text-status-fail',
} as const;
export type ChipTone = keyof typeof CHIP;

export function Chip({ children, tone = 'default', className }: Readonly<{ children: ReactNode; tone?: ChipTone; className?: string }>) {
  return (
    <span className={clsx('inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] leading-tight', CHIP[tone], className)}>
      {children}
    </span>
  );
}

export function Counter({ value, decimals = 0, suffix = '', duration = 1.2, className }: Readonly<{ value: number; decimals?: number; suffix?: string; duration?: number; className?: string }>) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const prev = useRef(0);
  const reduce = useReducedMotion() ?? false;
  const fmt = (v: number): string => `${v.toFixed(decimals)}${suffix}`;
  useEffect(() => {
    const el = ref.current;
    if (el === null || !inView) return;
    if (reduce) {
      el.textContent = fmt(value);
      return;
    }
    const c = animate(prev.current, value, {
      duration,
      ease: EASE,
      onUpdate: (v) => {
        el.textContent = fmt(v);
      },
    });
    prev.current = value;
    return () => {
      c.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, inView, reduce]);
  return (
    <span ref={ref} className={clsx('tabular-nums', className)}>
      {fmt(value)}
    </span>
  );
}

/** A 0..1 ring. `value` null draws an empty ring with "··". */
export function Ring({
  value,
  size = 112,
  stroke = 6,
  label,
  sub,
  display,
  ariaLabel,
  color,
  className,
}: Readonly<{ value: number | null; size?: number; stroke?: number; label?: ReactNode; sub?: ReactNode; display?: ReactNode; ariaLabel: string; color?: string; className?: string }>) {
  const id = useId().replace(/:/g, '');
  const R = 50 - stroke;
  const C = 2 * Math.PI * R;
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div ref={ref} className={clsx('relative shrink-0', className)} style={{ width: size, height: size }} role="img" aria-label={ariaLabel}>
      <svg viewBox="0 0 100 100" className="size-full -rotate-90" aria-hidden="true">
        <defs>
          <linearGradient id={`rg-${id}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style={{ stopColor: color ?? 'var(--color-flare)' }} />
            <stop offset="1" style={{ stopColor: color ?? 'var(--color-mars)', stopOpacity: 0.9 }} />
          </linearGradient>
        </defs>
        <circle cx="50" cy="50" r={R} fill="none" style={{ stroke: 'var(--color-line)' }} strokeWidth={stroke} />
        {[0, 0.25, 0.5, 0.75].map((t) => (
          <line key={t} x1="50" y1={50 - R - stroke / 2 - 1.5} x2="50" y2={50 - R - stroke / 2 + 1} transform={`rotate(${String(t * 360)} 50 50)`} style={{ stroke: 'var(--color-line-2)' }} strokeWidth="0.8" />
        ))}
        <motion.circle
          cx="50"
          cy="50"
          r={R}
          fill="none"
          stroke={`url(#rg-${id})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={{ strokeDashoffset: inView && value !== null ? C * (1 - v) : C }}
          transition={{ duration: 1.2, ease: EASE }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          {label === undefined ? null : <div className="font-mono text-[9px] tracking-[0.18em] text-dim uppercase">{label}</div>}
          <div className="font-mono leading-none font-light text-platinum" style={{ fontSize: size * 0.22 }}>
            {value === null ? '··' : display}
          </div>
          {sub === undefined ? null : <div className="mt-1 text-[10.5px] text-chrome">{sub}</div>}
        </div>
      </div>
    </div>
  );
}

/** A 0..1 bar with a label row. `value` null draws an empty track. */
export function Meter({
  value,
  label,
  right,
  hint,
  tone = 'var(--color-flare)',
  marker,
  className,
}: Readonly<{ value: number | null; label?: ReactNode; right?: ReactNode; hint?: ReactNode; tone?: string; marker?: number; className?: string }>) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  return (
    <div ref={ref} className={className}>
      {label === undefined && right === undefined ? null : (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[13px]">
          <span className="flex min-w-0 items-center gap-2 text-platinum">{label}</span>
          <span className="font-mono text-platinum tabular-nums">{right}</span>
        </div>
      )}
      <div className="relative mt-2 h-1.5 overflow-hidden rounded-full bg-line">
        <motion.div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ background: `linear-gradient(90deg, color-mix(in oklab, ${tone} 55%, transparent), ${tone})` }}
          initial={{ width: 0 }}
          animate={{ width: inView && value !== null ? `${String(Math.max(0, Math.min(1, value)) * 100)}%` : 0 }}
          transition={{ duration: 1.0, ease: EASE }}
        />
        {marker === undefined ? null : <span aria-hidden="true" className="absolute inset-y-0 w-px bg-platinum" style={{ left: `${String(marker * 100)}%` }} />}
      </div>
      {hint === undefined ? null : <div className="mt-1.5 text-[11.5px] leading-snug text-dim">{hint}</div>}
    </div>
  );
}

export function Seg<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: Readonly<{ value: T; onChange: (v: T) => void; options: readonly { id: T; label: ReactNode; title?: string }[]; label: string; className?: string }>) {
  const group = useId();
  return (
    <div className={clsx('relative flex w-fit max-w-full flex-wrap gap-0.5 rounded-full border border-line bg-ink p-0.5', className)} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          title={o.title}
          onClick={() => {
            onChange(o.id);
          }}
          aria-pressed={value === o.id}
          className={clsx(
            'relative flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] transition-colors focus-visible:outline-2 focus-visible:outline-ring',
            value === o.id ? 'text-void' : 'text-chrome hover:text-platinum',
          )}
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

/** The small coloured dot for a world (Moon, Mars, Earth). */
export function WorldDot({ world }: Readonly<{ world: string }>) {
  const colour = world === 'moon' ? 'var(--world-moon)' : world === 'mars' ? 'var(--world-mars)' : 'var(--world-earth)';
  return <span aria-hidden="true" className="inline-block size-2 shrink-0 rounded-full" style={{ background: colour, boxShadow: `0 0 8px ${colour}` }} />;
}
