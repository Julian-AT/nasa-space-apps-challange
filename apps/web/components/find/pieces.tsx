'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Step rail, step title and the inline option picker of the /find flow.
import clsx from 'clsx';
import { Check } from 'lucide-react';
import { motion } from 'motion/react';

import { EASE } from './kit.tsx';

export const STEPS = [
  { id: 'target', label: 'Target' },
  { id: 'place', label: 'Place on Earth' },
  { id: 'result', label: 'Result' },
] as const;
export type StepId = (typeof STEPS)[number]['id'];

export function StepRail({ step, onStep, can }: Readonly<{ step: StepId; onStep: (s: StepId) => void; can: Readonly<Record<StepId, boolean>> }>) {
  const idx = STEPS.findIndex((s) => s.id === step);
  return (
    <ol className="flex min-w-0 items-center gap-1" aria-label="Find steps">
      {STEPS.map((s, i) => {
        const on = s.id === step;
        const ok = i < idx;
        const enabled = can[s.id];
        return (
          <li key={s.id} className="flex items-center gap-1">
            <button
              type="button"
              disabled={!enabled}
              onClick={() => {
                onStep(s.id);
              }}
              aria-current={on ? 'step' : undefined}
              className={clsx(
                'relative flex items-center gap-1.5 rounded-full px-2 py-1.5 text-[12px] whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-ring',
                on ? 'text-void' : enabled ? 'text-chrome hover:text-platinum' : 'text-dim',
              )}
            >
              {on ? <motion.span layoutId="step-pill" className="absolute inset-0 rounded-full bg-platinum" transition={{ type: 'spring', stiffness: 420, damping: 34 }} /> : null}
              <span className={clsx('relative grid size-5 place-items-center rounded-full font-mono text-[10px]', on ? 'bg-void text-platinum' : ok ? 'bg-flare text-void' : 'border border-line-2')}>
                {ok && !on ? <Check size={11} aria-hidden="true" /> : i + 1}
              </span>
              <span className="sr-only sm:not-sr-only sm:relative">{s.label}</span>
            </button>
            {i < STEPS.length - 1 ? <span aria-hidden="true" className="h-px w-2 bg-line-2 sm:w-3" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

export function StepTitle({ kicker, title, text }: Readonly<{ kicker: string; title: string; text?: string }>) {
  return (
    <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: EASE }}>
      <div className="font-mono text-[10px] tracking-[0.24em] text-flare uppercase">{kicker}</div>
      <h2 className="mt-2 text-[clamp(24px,2.4vw,32px)] leading-[1.08] font-medium tracking-[-0.03em] text-platinum">{title}</h2>
      {text === undefined ? null : <p className="mt-2 text-[13.5px] leading-relaxed text-chrome">{text}</p>}
    </motion.div>
  );
}

/** Inline option picker (a styled native select). */
export function Pick<T extends string>({ value, options, onChange, label }: Readonly<{ value: T; options: readonly { v: T; label: string }[]; onChange: (v: T) => void; label: string }>) {
  return (
    <span className="relative inline-flex max-w-full align-baseline">
      <select
        aria-label={label}
        value={value}
        onChange={(e) => {
          const o = options.find((x) => x.v === e.target.value);
          if (o !== undefined) onChange(o.v);
        }}
        className="max-w-full cursor-pointer appearance-none rounded-lg border-b-2 border-flare/60 bg-flare/[0.08] px-2 py-0.5 font-medium text-flare outline-none hover:bg-flare/[0.14] focus:border-flare"
      >
        {options.map((o) => (
          <option key={o.v} value={o.v} className="bg-panel text-platinum">
            {o.label}
          </option>
        ))}
      </select>
    </span>
  );
}
