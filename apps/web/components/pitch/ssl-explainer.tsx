'use client';

// The self-supervised terrain model as a research-paper style figure for the /pitch "Our model"
// slide. `step` (0..5) builds the figure up: input window, two augmented views, the shared
// encoder, VICReg, the physics head and the body adversary. Earlier parts stay and dim slightly.
// Everything drawn here is a schematic of the architecture (paper/figures/standalone/
// encoder-architecture.tex); no measured values are shown, and steps 4 and 5 say so on the figure.
import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

import { EASE } from './bits';

const INK = '#1A1613';
const SUB = '#4D4843';
const MUTE = '#A39E95';
const HI = '#CC5800';
const GREEN = '#4A8044';
const BLUE = '#7AA7C7';
const RULE = 'rgba(26,22,19,0.10)';
const STROKE = 'rgba(26,22,19,0.55)';

const W = 1280;
const H = 460;
const L1 = 130; // lane of view x
const L2 = 330; // lane of view x'
const MID = (L1 + L2) / 2;

export const SSL_STEPS = [
  { title: 'Input', caption: 'The input is one 5 km elevation window, resampled to 64 x 64 with three channels.' },
  { title: 'Two views', caption: "Two augmented views x and x' are drawn from the same window by rotation, cropping and noise." },
  { title: 'Shared encoder', caption: "One encoder with shared weights maps both views to 256-d embeddings z and z'." },
  { title: 'VICReg', caption: "VICReg pulls z and z' together, while variance and covariance terms keep features spread out." },
  { title: 'Physics head', caption: 'A physics head predicts the 11-target roughness fingerprint from z, trained with a masked MSE.' },
  { title: 'Body adversary', caption: 'A body classifier behind gradient reversal removes planet identity, so z describes terrain.' },
  { title: 'Full architecture', caption: 'Full encoder architecture.' },
] as const;

const FULL = SSL_STEPS.length - 1;

/** Deterministic pseudo-random numbers in [0, 1) for schematic textures (never data). */
function rand(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

const R = rand(7);
const BARS = 48;
const Z = Array.from({ length: BARS }, () => 0.12 + 0.88 * R());
const Z2 = Z.map((v) => Math.min(1, Math.max(0.08, v + (R() - 0.5) * 1.1)));
const COV = Array.from({ length: 64 }, (_, k) => (k % 9 === 0 ? 1 : 0.15 + 0.6 * R()));
const NOISE = Array.from({ length: 26 }, () => [R(), R()] as const);

type Body = 'moon' | 'mars' | 'earth';
const BODY_COLOR: Record<Body, string> = { moon: MUTE, mars: HI, earth: BLUE };
const CENTERS: Record<Body, readonly [number, number]> = { moon: [0.22, 0.25], mars: [0.78, 0.3], earth: [0.5, 0.8] };
const PTS = (['moon', 'mars', 'earth'] as const).flatMap((b) =>
  Array.from({ length: 12 }, () => {
    const [cx, cy] = CENTERS[b];
    const a = R() * Math.PI * 2;
    const r = 0.11 * Math.sqrt(R());
    return { b, x0: cx + r * Math.cos(a), y0: cy + r * Math.sin(a), x1: 0.5 + 0.32 * (R() - 0.5) * 2, y1: 0.52 + 0.3 * (R() - 0.5) * 2 };
  }),
);

/** One build-up layer: hidden before its step, full on its step, slightly dimmed afterwards. */
function Layer({ at, step, children }: { at: number; step: number; children: ReactNode }) {
  const opacity = step < at ? 0 : step === at ? 1 : 0.5;
  return (
    <motion.g initial={false} animate={{ opacity }} transition={{ duration: 0.6, ease: EASE }} style={{ pointerEvents: 'none' }}>
      {children}
    </motion.g>
  );
}

function Label({ x, y, children, anchor = 'middle', mono = false, color = SUB, size }: { x: number; y: number; children: ReactNode; anchor?: 'start' | 'middle' | 'end'; mono?: boolean; color?: string; size?: number }) {
  return (
    <text x={x} y={y} textAnchor={anchor} fontSize={size ?? (mono ? 12.5 : 15)} fontWeight={400} fill={color} className={mono ? 'font-mono' : undefined} style={mono ? { letterSpacing: '0.02em' } : undefined}>
      {children}
    </text>
  );
}

function Arrow({ d, dashed = false }: { d: string; dashed?: boolean }) {
  return <path d={d} fill="none" stroke={STROKE} strokeWidth={1} strokeDasharray={dashed ? '4 4' : undefined} markerEnd="url(#ssl-head)" />;
}

/** A square schematic height grid (lines only, no values). */
function Grid({ x, y, s, n = 8, fill = '#FFFFFF' }: { x: number; y: number; s: number; n?: number; fill?: string }) {
  const lines = [];
  for (let k = 1; k < n; k++) {
    const t = (s * k) / n;
    lines.push(<line key={`v${k}`} x1={x + t} y1={y} x2={x + t} y2={y + s} />, <line key={`h${k}`} x1={x} y1={y + t} x2={x + s} y2={y + t} />);
  }
  return (
    <g>
      <rect x={x} y={y} width={s} height={s} fill={fill} stroke={INK} strokeWidth={1} />
      <g stroke={RULE} strokeWidth={1}>{lines}</g>
    </g>
  );
}

/** A 256-d embedding drawn as a fine barcode strip. */
function Barcode({ x, y, values, label, animateTo, reduce }: { x: number; y: number; values: readonly number[]; label: string; animateTo?: readonly number[] | undefined; reduce: boolean }) {
  const bw = 3;
  return (
    <g>
      {values.map((v, k) => (
        <motion.rect
          key={k}
          x={x + k * bw}
          y={y - 22}
          width={bw - 0.8}
          height={44}
          fill={INK}
          initial={false}
          animate={{ opacity: animateTo ? (animateTo[k] ?? v) : v }}
          transition={{ duration: reduce ? 0 : 1.4, delay: reduce ? 0 : 0.5 + k * 0.012, ease: EASE }}
        />
      ))}
      <Label x={x - 12} y={y + 5} anchor="end" size={17} color={INK}>{label}</Label>
      <Label x={x + (BARS * bw) / 2} y={y + 40} mono color={MUTE}>256-d</Label>
    </g>
  );
}

export function SslExplainer({ step }: { step: number }) {
  const s = Math.max(0, Math.min(SSL_STEPS.length - 1, step));
  const reduce = useReducedMotion() ?? false;
  const cur = SSL_STEPS[s] ?? SSL_STEPS[0];
  const zx = 700;
  const zEnd = zx + BARS * 3;

  // Encoder geometry: four stages per lane, spatial size shrinks while channels grow.
  const STAGES = [
    { w: 18, h: 74, ch: '32' },
    { w: 24, h: 58, ch: '64' },
    { w: 30, h: 44, ch: '128' },
    { w: 36, h: 32, ch: '256' },
  ];
  const ex = 410;
  const ew = 220;
  const stageX = (k: number) => ex + 28 + k * 46;

  const chart = { x: 1010, y: 44, w: 230, h: 130 };
  const box = { x: 1000, y: L2 - 30, w: 120, h: 60 };
  const sc = { x: 1150, y: L2 - 66, w: 110, h: 110 };

  return (
    <figure className="flex w-full flex-col gap-4">
      <div className="relative w-full rounded-xl border bg-white px-8 pb-6 pt-14" style={{ borderColor: RULE }}>
        <div className="absolute right-6 top-5 flex items-center gap-4">
          <div className="flex items-center gap-1.5" aria-hidden>
            {SSL_STEPS.map((_, k) => (
              <motion.span
                key={k}
                className="block size-1.5 rounded-full"
                initial={false}
                animate={{ backgroundColor: k === s ? HI : k < s ? SUB : 'rgba(26,22,19,0.15)', scale: k === s ? 1.25 : 1 }}
                transition={{ duration: 0.3 }}
              />
            ))}
          </div>
          <span className="font-mono text-xs tracking-wide" style={{ color: SUB }}>
            Step {s + 1} of {SSL_STEPS.length}: {cur.title}
          </span>
        </div>
        <span className="absolute left-8 top-5 font-mono text-xs tracking-wide" style={{ color: MUTE }}>
          self-supervised terrain encoder
        </span>

        <div className="relative w-full" style={{ aspectRatio: `${W} / ${H}` }}>
        <motion.div className="absolute inset-0 flex items-center justify-center" initial={false} animate={{ opacity: s === FULL ? 1 : 0 }} transition={{ duration: 0.6, ease: EASE }} aria-hidden={s !== FULL}>
          {/* The paper's architecture figure, rendered from paper/figures/standalone/encoder-architecture.tex. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/figures/encoder-architecture.svg" alt="Full encoder architecture: input, ResNet stages, embedding and the three heads" className="h-full w-auto max-w-full object-contain" />
        </motion.div>
        <motion.svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 block h-full w-full" initial={false} animate={{ opacity: s === FULL ? 0 : 1 }} transition={{ duration: 0.6, ease: EASE }} aria-hidden={s === FULL} role="img" aria-label={`Model figure, step ${s + 1} of ${SSL_STEPS.length}: ${cur.caption}`}>
          <defs>
            <marker id="ssl-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 1 L9 5 L0 9" fill="none" stroke={STROKE} strokeWidth={1.2} />
            </marker>
          </defs>

          {/* 0 Input: three stacked channel planes of one window */}
          <Layer at={0} step={s}>
            <Grid x={70} y={MID - 52} s={104} fill="#FBFAF7" />
            <Grid x={62} y={MID - 60} s={104} fill="#FDFCFA" />
            <Grid x={54} y={MID - 68} s={104} />
            <Label x={110} y={MID + 76} color={INK}>5 km window</Label>
            <Label x={110} y={MID + 98} mono color={MUTE}>64 x 64 x 3</Label>
            <Label x={110} y={MID + 116} mono color={MUTE}>height, slope, mask</Label>
          </Layer>

          {/* 1 Two augmented views */}
          <Layer at={1} step={s}>
            <Arrow d={`M182 ${MID - 30} C 220 ${MID - 30}, 220 ${L1}, 252 ${L1}`} />
            <Arrow d={`M182 ${MID + 30} C 220 ${MID + 30}, 220 ${L2}, 252 ${L2}`} />
            <Label x={212} y={L1 + 8} mono color={MUTE} anchor="end">rotate</Label>
            <Label x={212} y={L2 - 22} mono color={MUTE} anchor="end">crop</Label>
            <Label x={212} y={L2 - 6} mono color={MUTE} anchor="end">noise</Label>
            <g transform={`rotate(14 ${300} ${L1})`}>
              <Grid x={262} y={L1 - 38} s={76} />
            </g>
            <Grid x={262} y={L2 - 38} s={76} n={6} />
            <g fill={SUB}>
              {NOISE.map(([a, b], k) => (
                <circle key={k} cx={264 + a * 72} cy={L2 - 36 + b * 72} r={1.1} opacity={0.55} />
              ))}
            </g>
            <Label x={300} y={L1 + 66} size={17} color={INK}>x</Label>
            <Label x={300} y={L2 + 62} size={17} color={INK}>{"x'"}</Label>
          </Layer>

          {/* 2 Shared encoder with two lanes */}
          <Layer at={2} step={s}>
            <Arrow d={`M352 ${L1} L ${ex - 6} ${L1}`} />
            <Arrow d={`M352 ${L2} L ${ex - 6} ${L2}`} />
            <rect x={ex} y={36} width={ew} height={H - 72} rx={10} fill="#FDFCFA" stroke={INK} strokeWidth={1} />
            <Label x={ex + ew / 2} y={62} color={INK}>Encoder</Label>
            <Label x={ex + ew / 2} y={80} mono color={MUTE}>ResNet-18 layout, 4 stages</Label>
            {[L1, L2].map((ly) =>
              STAGES.map((st, k) => <rect key={`${ly}-${k}`} x={stageX(k)} y={ly - st.h / 2 + 10} width={st.w} height={st.h} rx={2} fill="#FFFFFF" stroke={STROKE} strokeWidth={1} />),
            )}
            {STAGES.map((st, k) => (
              <g key={k}>
                <line x1={stageX(k) + st.w / 2} y1={L1 + st.h / 2 + 14} x2={stageX(k) + st.w / 2} y2={L2 - st.h / 2 + 6} stroke={HI} strokeWidth={1} strokeDasharray="2 4" opacity={0.7} />
                <Label x={stageX(k) + st.w / 2} y={L1 - st.h / 2 + 2} mono size={11} color={MUTE}>{st.ch}</Label>
              </g>
            ))}
            <rect x={ex + ew / 2 - 56} y={MID - 3} width={112} height={22} fill="#FDFCFA" />
            <Label x={ex + ew / 2} y={MID + 12} mono color={HI}>shared weights</Label>
            <Arrow d={`M${ex + ew + 4} ${L1} L ${zx - 30} ${L1}`} />
            <Arrow d={`M${ex + ew + 4} ${L2} L ${zx - 30} ${L2}`} />
            <Barcode x={zx} y={L1} values={Z} label="z" reduce={reduce} />
            <Barcode x={zx} y={L2} values={Z2} label="z'" reduce={reduce} animateTo={s >= 3 ? Z : undefined} />
          </Layer>

          {/* 3 VICReg: invariance pull and covariance decorrelation */}
          <Layer at={3} step={s}>
            <motion.g
              initial={false}
              animate={s >= 3 && !reduce ? { y: [0, 8, 0] } : { y: 0 }}
              transition={{ duration: 1.8, repeat: s >= 3 && !reduce ? Infinity : 0, ease: 'easeInOut' }}
            >
              <Arrow d={`M${zx + 72} ${L1 + 50} L ${zx + 72} ${MID - 22}`} />
            </motion.g>
            <motion.g
              initial={false}
              animate={s >= 3 && !reduce ? { y: [0, -8, 0] } : { y: 0 }}
              transition={{ duration: 1.8, repeat: s >= 3 && !reduce ? Infinity : 0, ease: 'easeInOut' }}
            >
              <Arrow d={`M${zx + 72} ${L2 - 30} L ${zx + 72} ${MID + 18}`} />
            </motion.g>
            <Label x={zx + 72} y={MID + 4} mono color={INK}>invariance</Label>
            <Label x={zx + 160} y={MID - 6} anchor="start" size={13.5} color={SUB}>views must agree</Label>

            <g transform={`translate(${zx + 168} ${MID + 22})`}>
              {COV.map((v, k) => {
                const i = Math.floor(k / 8);
                const j = k % 8;
                const diag = i === j;
                return (
                  <motion.rect
                    key={k}
                    x={j * 9}
                    y={i * 9}
                    width={8}
                    height={8}
                    fill={diag ? GREEN : SUB}
                    initial={false}
                    animate={{ opacity: s >= 3 ? (diag ? 0.95 : 0.05) : v }}
                    transition={{ duration: reduce ? 0 : 1.6, delay: reduce ? 0 : 0.6 + (i + j) * 0.04, ease: EASE }}
                  />
                );
              })}
              <rect x={-1} y={-1} width={73} height={73} fill="none" stroke={RULE} strokeWidth={1} />
              <Label x={84} y={14} anchor="start" mono color={MUTE}>covariance</Label>
              <Label x={84} y={30} anchor="start" mono color={MUTE}>8 x 8 slice</Label>
            </g>
            <Label x={zx + 168} y={MID + 116} anchor="start" size={13.5} color={SUB}>variance and covariance keep</Label>
            <Label x={zx + 168} y={MID + 134} anchor="start" size={13.5} color={SUB}>features spread and independent</Label>
          </Layer>

          {/* 4 Physics head */}
          <Layer at={4} step={s}>
            <Arrow d={`M${zEnd + 10} ${L1 - 30} C ${zEnd + 60} ${L1 - 30}, ${chart.x - 60} ${chart.y + chart.h / 2}, ${chart.x - 8} ${chart.y + chart.h / 2}`} />
            <Label x={chart.x - 70} y={chart.y + 26} mono color={MUTE} anchor="middle">physics head</Label>
            <line x1={chart.x} y1={chart.y + chart.h} x2={chart.x + chart.w} y2={chart.y + chart.h} stroke={STROKE} strokeWidth={1} />
            <line x1={chart.x} y1={chart.y} x2={chart.x} y2={chart.y + chart.h} stroke={STROKE} strokeWidth={1} />
            <Label x={chart.x + chart.w} y={chart.y + chart.h + 18} anchor="end" mono color={MUTE}>L</Label>
            <Label x={chart.x + 6} y={chart.y + 4} anchor="start" mono color={MUTE}>nu(L)</Label>
            <Label x={chart.x + chart.w} y={chart.y + 4} anchor="end" mono color={HI}>schematic</Label>
            <path
              d={`M${chart.x + 10} ${chart.y + 112} C ${chart.x + 70} ${chart.y + 96}, ${chart.x + 130} ${chart.y + 62}, ${chart.x + 220} ${chart.y + 30}`}
              fill="none"
              stroke={INK}
              strokeWidth={1.2}
            />
            <motion.path
              d={`M${chart.x + 10} ${chart.y + 106} C ${chart.x + 74} ${chart.y + 94}, ${chart.x + 128} ${chart.y + 66}, ${chart.x + 220} ${chart.y + 36}`}
              fill="none"
              stroke={BLUE}
              strokeWidth={1.6}
              strokeDasharray="5 4"
              initial={false}
              animate={{ pathLength: s >= 4 ? 1 : 0 }}
              transition={{ duration: reduce ? 0 : 1.4, delay: reduce ? 0 : 0.4, ease: EASE }}
            />
            <line x1={chart.x + 120} y1={chart.y + 112} x2={chart.x + 138} y2={chart.y + 112} stroke={INK} strokeWidth={1.2} />
            <Label x={chart.x + 144} y={chart.y + 116} anchor="start" mono size={11} color={SUB}>measured</Label>
            <line x1={chart.x + 120} y1={chart.y + 96} x2={chart.x + 138} y2={chart.y + 96} stroke={BLUE} strokeWidth={1.6} strokeDasharray="5 4" />
            <Label x={chart.x + 144} y={chart.y + 100} anchor="start" mono size={11} color={SUB}>predicted</Label>
            <Label x={chart.x} y={chart.y + chart.h + 40} anchor="start" size={13.5} color={SUB}>predicts the fingerprint,</Label>
            <Label x={chart.x} y={chart.y + chart.h + 58} anchor="start" size={13.5} color={SUB}>11 targets, masked MSE</Label>
          </Layer>

          {/* 5 Body adversary with gradient reversal */}
          <Layer at={5} step={s}>
            <Arrow d={`M${zEnd + 10} ${L2} L ${box.x - 8} ${L2}`} />
            <rect x={zEnd + 46} y={L2 - 30} width={86} height={20} rx={3} fill="#FFFFFF" />
            <Label x={zEnd + 89} y={L2 - 15} mono color={HI}>x(-lambda)</Label>
            <Label x={zEnd + 89} y={L2 + 22} mono size={11} color={MUTE}>gradient reversal</Label>
            <rect x={box.x} y={box.y} width={box.w} height={box.h} rx={8} fill="#FDFCFA" stroke={INK} strokeWidth={1} />
            <Label x={box.x + box.w / 2} y={box.y + 26} size={14} color={INK}>Moon / Mars</Label>
            <Label x={box.x + box.w / 2} y={box.y + 44} size={14} color={INK}>/ Earth?</Label>
            <rect x={sc.x} y={sc.y} width={sc.w} height={sc.h} fill="none" stroke={RULE} strokeWidth={1} />
            {PTS.map((p, k) => (
              <motion.circle
                key={k}
                r={2.6}
                fill={BODY_COLOR[p.b]}
                initial={false}
                animate={{ cx: sc.x + (s >= 5 ? p.x1 : p.x0) * sc.w, cy: sc.y + (s >= 5 ? p.y1 : p.y0) * sc.h }}
                transition={{ duration: reduce ? 0 : 2.2, delay: reduce ? 0 : 0.6 + (k % 12) * 0.03, ease: EASE }}
              />
            ))}
            <Label x={sc.x + sc.w} y={sc.y - 8} anchor="end" mono color={HI}>schematic</Label>
            {(['moon', 'mars', 'earth'] as const).map((b, k) => (
              <g key={b}>
                <circle cx={sc.x + 4 + k * 40} cy={sc.y + sc.h + 14} r={2.6} fill={BODY_COLOR[b]} />
                <Label x={sc.x + 10 + k * 40} y={sc.y + sc.h + 18} anchor="start" mono size={11} color={MUTE}>{b}</Label>
              </g>
            ))}
            <Label x={box.x} y={sc.y + sc.h + 46} anchor="start" size={13.5} color={SUB}>the encoder learns terrain, not which planet</Label>
          </Layer>
        </motion.svg>
        </div>
      </div>
      <figcaption className="text-base leading-snug" style={{ color: SUB }}>
        <span style={{ color: INK }}>Figure {s === FULL ? 4 : 3}.</span> {cur.caption}
      </figcaption>
    </figure>
  );
}
