'use client';

// The encoder architecture as a build-up diagram for /pitch, drawn in the paper figure's visual
// language (paper/figures/standalone/encoder-architecture.tex): pastel rounded boxes, thin outlines,
// orthogonal arrows. Facts follow that figure. `step` reveals (0) input, (1) backbone, (2) embedding,
// (3) the three heads; travelling pulses show data flowing along revealed edges.
import { AnimatePresence, motion } from 'motion/react';
import type { ReactNode } from 'react';

import { EASE } from './bits';

const INK = '#27272a';
const SUB = '#71717a';
const LINE = '#a1a1aa';
const GOLD = '#C8962E';
const CY = 300;

interface BoxProps {
  readonly x: number;
  readonly w: number;
  readonly h: number;
  readonly cy?: number;
  readonly fill: string;
  readonly stroke: string;
  readonly lines: readonly string[];
  readonly dashed?: boolean;
}

function Box({ x, w, h, cy = CY, fill, stroke, lines, dashed }: BoxProps) {
  const y = cy - h / 2;
  const top = cy - ((lines.length - 1) * 21) / 2;
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={12} fill={fill} stroke={stroke} strokeWidth={1.4} strokeDasharray={dashed ? '5 4' : undefined} />
      {lines.map((t, i) => (
        <text
          key={t}
          x={x + w / 2}
          y={top + i * 21}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={i === 0 ? 17 : 14.5}
          fontWeight={i === 0 ? 600 : 400}
          fill={i === 0 ? INK : SUB}
          fontFamily={i === 0 ? undefined : 'var(--font-geist-mono), ui-monospace, monospace'}
        >
          {t}
        </text>
      ))}
    </g>
  );
}

/** An orthogonal edge with an arrowhead and a gold pulse travelling along it. */
function Edge({ d, delay = 0 }: { readonly d: string; readonly delay?: number }) {
  return (
    <g>
      <path d={d} fill="none" stroke={LINE} strokeWidth={1.5} markerEnd="url(#pitch-arrow)" />
      <path
        d={d}
        fill="none"
        stroke={GOLD}
        strokeWidth={3.5}
        strokeLinecap="round"
        pathLength={1}
        strokeDasharray="0.22 1.5"
        className="pitch-beam"
        style={{ animationDelay: `${delay}s` }}
      />
    </g>
  );
}

function Reveal({ show, children, delay = 0 }: { readonly show: boolean; readonly children: ReactNode; readonly delay?: number }) {
  return (
    <AnimatePresence>
      {show ? (
        <motion.g initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.55, delay, ease: EASE }}>
          {children}
        </motion.g>
      ) : null}
    </AnimatePresence>
  );
}

const STAGES = [
  { x: 340, h: 120, ch: 32 },
  { x: 480, h: 160, ch: 64 },
  { x: 620, h: 200, ch: 128 },
  { x: 760, h: 240, ch: 256 },
] as const;

const HEAD_X = 1250;
const HEAD_W = 230;
const HEADS = [
  { cy: 120, fill: '#FBE4EC', stroke: '#E3A3B9', lines: ['Projector', '256→2048→2048→2048', 'VICReg loss'] },
  { cy: 300, fill: '#E0F0F8', stroke: '#93C4DD', lines: ['Physics head', '256→256→11', 'masked MSE'] },
  { cy: 480, fill: '#ECE5F8', stroke: '#B7A3DE', lines: ['Body head', '256→256→3', 'cross-entropy'] },
] as const;

export function ArchDiagram({ step }: { readonly step: number }) {
  return (
    <svg viewBox="0 0 1500 600" className="h-auto w-full" role="img" aria-label="Terrain encoder: 64 by 64 by 3 input window, ResNet backbone with four stages, 256-d embedding, and three heads: VICReg projector, physics head, and a body adversary behind gradient reversal">
      <defs>
        <marker id="pitch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,1 L9,5 L0,9 z" fill={LINE} />
        </marker>
        <style>{`
          .pitch-beam { stroke-dashoffset: 1.72; animation: pitch-beam 2.2s linear infinite; }
          @keyframes pitch-beam { from { stroke-dashoffset: 1.72; } to { stroke-dashoffset: 0; } }
          @media (prefers-reduced-motion: reduce) { .pitch-beam { animation: none; opacity: 0; } }
        `}</style>
      </defs>

      {/* (a) input window: three channel planes */}
      <Reveal show>
        <g>
          {[2, 1, 0].map((k) => (
            <rect key={k} x={40 + k * 14} y={222 + k * 14} width={92} height={92} rx={8} fill={['#DCEAFB', '#E6F0FC', '#F0F6FE'][k]} stroke="#8FB3E0" strokeWidth={1.4} />
          ))}
          <text x={100} y={190} textAnchor="middle" fontSize={17} fontWeight={600} fill={INK}>
            Input window
          </text>
          <text x={100} y={385} textAnchor="middle" fontSize={14.5} fill={SUB} fontFamily="var(--font-geist-mono), ui-monospace, monospace">
            64 × 64 × 3
          </text>
          <text x={100} y={410} textAnchor="middle" fontSize={13.5} fill={SUB} fontFamily="var(--font-geist-mono), ui-monospace, monospace">
            asinh(z/s) · slope · mask
          </text>
        </g>
      </Reveal>

      {/* (b) ResNet backbone */}
      <Reveal show={step >= 1}>
        <g>
          <path d="M200,150 v-12 h785 v12" fill="none" stroke={LINE} strokeWidth={1.2} />
          <text x={592} y={124} textAnchor="middle" fontSize={15} fill={SUB} letterSpacing="0.08em">
            RESNET BACKBONE · 4 STAGES
          </text>
          <Edge d={`M172,${CY} H198`} />
          <Box x={200} w={110} h={120} fill="#FBE6D6" stroke="#E5B08A" lines={['Stem', 'conv 3×3', '32 · stride 2']} />
          {STAGES.map((s, k) => (
            <g key={s.ch}>
              <Edge d={`M${k === 0 ? 310 : (STAGES[k - 1]?.x ?? 0) + 110},${CY} H${s.x - 2}`} delay={0.15 * (k + 1)} />
              <Box x={s.x} w={110} h={s.h} fill="#FCF0D2" stroke="#E2C27A" lines={[`Stage ${k + 1}`, 'BasicBlock ×2', `${s.ch} ch`]} />
            </g>
          ))}
          <Edge d={`M870,${CY} H903`} delay={0.75} />
          <Box x={905} w={80} h={120} fill="#ECECF4" stroke="#B4B4CC" lines={['GAP', 'global', 'avg pool']} />
        </g>
      </Reveal>

      {/* (c) embedding */}
      <Reveal show={step >= 2}>
        <g>
          <Edge d={`M985,${CY} H1018`} delay={0.9} />
          <rect x={1020} y={CY - 140} width={90} height={280} rx={12} fill="#DDF2E3" stroke={GOLD} strokeWidth={2} />
          <text x={1065} y={CY - 30} textAnchor="middle" fontSize={26} fontWeight={700} fill={INK}>
            256
          </text>
          <text x={1065} y={CY - 4} textAnchor="middle" fontSize={15} fontWeight={600} fill={INK}>
            embedding
          </text>
          <text x={1065} y={CY + 26} textAnchor="middle" fontSize={13.5} fill={SUB} fontFamily="var(--font-geist-mono), ui-monospace, monospace">
            L2-norm
          </text>
        </g>
      </Reveal>

      {/* (d) three heads; the body adversary sits behind gradient reversal */}
      <Reveal show={step >= 3}>
        <g>
          <Edge d={`M1110,${CY} H1135 V${HEADS[0].cy} H${HEAD_X - 2}`} delay={1.05} />
          <Edge d={`M1110,${CY} H${HEAD_X - 2}`} delay={1.15} />
          <Edge d={`M1135,${CY} V${HEADS[2].cy} H1148`} delay={1.25} />
          <rect x={1150} y={HEADS[2].cy - 30} width={76} height={60} rx={10} fill="#FFF7E3" stroke={GOLD} strokeWidth={1.4} strokeDasharray="5 4" />
          <text x={1188} y={HEADS[2].cy - 7} textAnchor="middle" fontSize={14} fontWeight={600} fill={INK}>
            GRL
          </text>
          <text x={1188} y={HEADS[2].cy + 14} textAnchor="middle" fontSize={13.5} fill={SUB} fontFamily="var(--font-geist-mono), ui-monospace, monospace">
            ×(−λ)
          </text>
          <Edge d={`M1226,${HEADS[2].cy} H${HEAD_X - 2}`} delay={1.4} />
          {HEADS.map((h, k) => (
            <motion.g key={h.lines[0]} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 + k * 0.12, duration: 0.5, ease: EASE }}>
              <Box x={HEAD_X} w={HEAD_W} h={104} cy={h.cy} fill={h.fill} stroke={h.stroke} lines={h.lines} />
            </motion.g>
          ))}
        </g>
      </Reveal>
    </svg>
  );
}

export const ARCH_STEPS = [
  { key: 'input', label: 'Input window', hint: '64×64 · 3 channels' },
  { key: 'backbone', label: 'ResNet backbone', hint: '4 stages · 32→256' },
  { key: 'embedding', label: 'Embedding', hint: '256-d · L2-normalised' },
  { key: 'heads', label: 'Three heads', hint: 'VICReg · physics · adversary' },
] as const;
