'use client';

// The encoder architecture as a step-by-step explainer for /pitch: one horizontal row (input, stem,
// the four ResNet stages as one block, the 256-d embedding) and three heads stacked on the right.
// Facts follow the paper figure (paper/figures/standalone/encoder-architecture.tex). `step` (0..5)
// highlights one part and dims the rest; ARCH_STEPS carries the caption for each step.
import { motion } from 'motion/react';
import type { ReactNode } from 'react';

import { EASE } from './bits';

const INK = '#1A1613';
const SUB = '#4D4843';
const LINE = '#A39E95';
const CY = 260;

export const ARCH_STEPS = [
  { key: 'input', title: 'Input', line: '64 x 64 window, 3 channels: height, slope, valid mask', num: '64 x 64 x 3' },
  { key: 'backbone', title: 'Backbone', line: 'ResNet-18 layout, 4 stages, stride-2 stem', num: '32 to 256 ch' },
  { key: 'embedding', title: 'Embedding', line: '256 numbers per window, L2-normalised, used for similarity', num: '256-d' },
  { key: 'vicreg', title: 'VICReg head', line: 'self-supervised, two augmented views must agree', num: '2 views' },
  { key: 'physics', title: 'Physics head', line: 'predicts the fingerprint, 11 targets, masked loss', num: '11 targets' },
  { key: 'body', title: 'Body adversary', line: 'gradient reversal, learns to ignore Moon, Mars or Earth', num: '3 bodies' },
] as const;

/** Fades a part in or out of focus. */
function Part({ on, children }: { readonly on: boolean; readonly children: ReactNode }) {
  return (
    <motion.g initial={false} animate={{ opacity: on ? 1 : 0.4 }} transition={{ duration: 0.45, ease: EASE }}>
      {children}
    </motion.g>
  );
}

function Box({ x, y, w, h, on, title, sub }: { x: number; y: number; w: number; h: number; on: boolean; title: string; sub?: string }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={14} fill={on ? '#ffffff' : '#F3F1EC'} stroke={on ? INK : 'rgba(26,22,19,0.18)'} strokeWidth={on ? 2.5 : 1.5} />
      <text x={x + w / 2} y={y + h / 2 + (sub ? -12 : 0)} textAnchor="middle" dominantBaseline="central" fontSize={22} fontWeight={700} fill={INK}>
        {title}
      </text>
      {sub ? (
        <text x={x + w / 2} y={y + h / 2 + 18} textAnchor="middle" dominantBaseline="central" fontSize={16} fill={SUB}>
          {sub}
        </text>
      ) : null}
    </g>
  );
}

function Arrow({ d }: { readonly d: string }) {
  return <path d={d} fill="none" stroke={LINE} strokeWidth={1.5} markerEnd="url(#pitch-arrow)" />;
}

const HEAD_X = 1130;
const HEAD_W = 330;
const HEADS = [
  { cy: 85, title: 'VICReg head', sub: 'projector, two views agree' },
  { cy: 260, title: 'Physics head', sub: 'predicts the fingerprint' },
  { cy: 435, title: 'Body adversary', sub: 'Moon, Mars or Earth?' },
] as const;

export function ArchDiagram({ step }: { readonly step: number }) {
  const s = Math.max(0, Math.min(ARCH_STEPS.length - 1, step));
  return (
    <svg
      viewBox="0 0 1500 520"
      className="h-auto max-h-full w-full"
      role="img"
      aria-label="Terrain encoder: 64 by 64 by 3 input window, stem and four ResNet stages, 256-d embedding, and three heads: VICReg, physics head, and a body adversary behind gradient reversal"
    >
      <defs>
        <marker id="pitch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,1 L9,5 L0,9 z" fill={LINE} />
        </marker>
      </defs>

      {/* input: three channel planes */}
      <Part on={s === 0}>
        {[2, 1, 0].map((k) => (
          <rect key={k} x={30 + k * 16} y={CY - 62 + k * 16} width={100} height={100} rx={10} fill={['#ECE9E3', '#F3F1EC', '#ffffff'][k]} stroke={s === 0 ? INK : 'rgba(26,22,19,0.18)'} strokeWidth={s === 0 ? 2 : 1.5} />
        ))}
        <text x={96} y={CY - 90} textAnchor="middle" fontSize={22} fontWeight={700} fill={INK}>Input</text>
        <text x={96} y={CY + 100} textAnchor="middle" fontSize={16} fill={SUB}>64 x 64 x 3</text>
      </Part>
      <Arrow d={`M180,${CY} H238`} />

      {/* backbone: stem and the four stages as one block */}
      <Part on={s === 1}>
        <Box x={240} y={CY - 60} w={130} h={120} on={s === 1} title="Stem" sub="stride 2" />
        <rect x={430} y={CY - 110} width={300} height={220} rx={18} fill={s === 1 ? '#ffffff' : '#F3F1EC'} stroke={s === 1 ? INK : 'rgba(26,22,19,0.18)'} strokeWidth={s === 1 ? 2.5 : 1.5} />
        {[0, 1, 2, 3].map((k) => {
          const h = 50 + k * 26;
          return <rect key={k} x={462 + k * 62} y={CY + 28 - h} width={42} height={h} rx={8} fill="#ECE9E3" />;
        })}
        <text x={580} y={CY + 62} textAnchor="middle" fontSize={20} fontWeight={700} fill={INK}>ResNet stages 1 to 4</text>
        <text x={580} y={CY + 88} textAnchor="middle" fontSize={16} fill={SUB}>4 stages, 32 to 256 channels</text>
      </Part>
      <Arrow d={`M370,${CY} H428`} />
      <Arrow d={`M730,${CY} H798`} />

      {/* embedding */}
      <Part on={s === 2}>
        <rect x={800} y={CY - 130} width={120} height={260} rx={16} fill={s === 2 ? INK : '#F3F1EC'} stroke={s === 2 ? INK : 'rgba(26,22,19,0.18)'} strokeWidth={s === 2 ? 2.5 : 1.5} />
        <text x={860} y={CY - 10} textAnchor="middle" fontSize={34} fontWeight={800} fill={s === 2 ? '#ffffff' : INK}>256</text>
        <text x={860} y={CY + 22} textAnchor="middle" fontSize={17} fill={s === 2 ? 'rgba(26,22,19,0.18)' : SUB}>embedding</text>
      </Part>

      {/* three heads; the body adversary sits behind gradient reversal */}
      <Arrow d={`M920,${CY} H1000 V${HEADS[0].cy} H${HEAD_X - 2}`} />
      <Arrow d={`M1000,${CY} H${HEAD_X - 2}`} />
      <Arrow d={`M1000,${CY} V${HEADS[2].cy} H${HEAD_X - 2}`} />
      <Part on={s === 5}>
        <rect x={1022} y={HEADS[2].cy - 18} width={84} height={36} rx={18} fill="#ffffff" stroke={s === 5 ? INK : 'rgba(26,22,19,0.18)'} strokeWidth={1.5} strokeDasharray="4 4" />
        <text x={1064} y={HEADS[2].cy + 1} textAnchor="middle" dominantBaseline="central" fontSize={15} fontWeight={700} fill={INK}>GRL</text>
      </Part>
      {HEADS.map((h, k) => (
        <Part key={h.title} on={s === 3 + k}>
          <Box x={HEAD_X} y={h.cy - 55} w={HEAD_W} h={110} on={s === 3 + k} title={h.title} sub={h.sub} />
        </Part>
      ))}
    </svg>
  );
}
