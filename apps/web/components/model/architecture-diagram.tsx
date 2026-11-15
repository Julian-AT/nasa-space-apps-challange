// The encoder architecture as an HTML diagram (no chart or 3D library, PRD 8.10): input window,
// stem, the four residual stages, the pooled embedding and the two training heads. Widths, block
// counts, input shape, embedding size and lambda_max are read from the model card and the ONNX
// export record by readModelFacts; a missing field leaves the box without the number.
import { Fragment, type ReactNode } from 'react';

import type { ModelFacts } from '@/components/home/model-facts';

function Box({ kicker, title, children, tone = 'default', className = '' }: { readonly className?: string; readonly kicker: string; readonly title: ReactNode; readonly children?: ReactNode; readonly tone?: 'default' | 'input' | 'embed' | 'head' | 'adv' }) {
  const toneClass = {
    default: 'border-line-2 bg-panel',
    input: 'border-earth/50 bg-earth/5',
    embed: 'border-flare/60 bg-flare/10',
    head: 'border-evidence-derived/50 bg-evidence-derived/5',
    adv: 'border-status-fail/50 bg-status-fail/5',
  }[tone];
  return (
    <li className={`flex min-w-0 flex-1 flex-col gap-1 rounded-2xl border p-3 ${toneClass} ${className}`}>
      <span className="font-mono text-[10px] tracking-[0.16em] text-chrome uppercase">{kicker}</span>
      <span className="text-sm leading-tight font-medium text-platinum">{title}</span>
      {children === undefined ? null : <span className="font-mono text-[11px] leading-snug text-chrome">{children}</span>}
    </li>
  );
}

function Arrow() {
  return (
    <li aria-hidden="true" className="hidden items-center justify-center text-chrome lg:flex">
      <svg width="18" height="12" viewBox="0 0 18 12" fill="none">
        <path d="M0 6h15M11 1l5 5-5 5" stroke="currentColor" strokeWidth="1.25" />
      </svg>
    </li>
  );
}

export function ArchitectureDiagram({ facts }: { readonly facts: ModelFacts }) {
  const widths = facts.stageWidths ?? [];
  const blocks = facts.blocksPerStage ?? [];
  const stages = Math.max(widths.length, blocks.length, 4);
  const stem = facts.architecture === null ? null : /(\d+) x (\d+) stem at stride (\d+)/.exec(facts.architecture);
  const dim = facts.embeddingDim.ok ? facts.embeddingDim.value : null;
  return (
    <figure className="surface flex flex-col gap-5 rounded-3xl p-5 sm:p-6">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">Encoder architecture</span>
        <span className="text-xs text-chrome">from model/model_card.json and the ONNX export record</span>
      </figcaption>
      <ol aria-label="Encoder layers from input to embedding" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:flex lg:flex-row lg:items-stretch">
        <Box kicker="Input" title={facts.windowKm === null ? 'DEM window' : `${String(facts.windowKm)} km DEM window`} tone="input" className="col-span-2 sm:col-span-3">
          {facts.inputShape.ok ? facts.inputShape.value : null}
        </Box>
        <Arrow />
        <Box kicker="Stem" className="col-span-2 sm:col-span-1" title={stem === null ? 'Conv stem' : `Conv ${stem[1]} × ${stem[2]}`}>
          {stem === null ? null : `stride ${stem[3]}`}
        </Box>
        <Arrow />
        {Array.from({ length: stages }, (_, i) => (
          <Fragment key={i}>
            <Box kicker={`Stage ${String(i + 1)}`} title={widths[i] === undefined ? 'Residual stage' : `${String(widths[i])} ch`}>
              {blocks[i] === undefined ? null : `${String(blocks[i])} × BasicBlock`}
            </Box>
            {i < stages - 1 ? <Arrow /> : null}
          </Fragment>
        ))}
        <Arrow />
        <Box kicker="Embedding" title={dim === null ? 'Embedding' : `${String(dim)}-d`} tone="embed" className="col-span-2 sm:col-span-1">
          avg pool, L2
        </Box>
      </ol>
      <ul aria-label="Training heads on the embedding" className="grid gap-3 sm:grid-cols-2">
          <Box kicker="Training head" title="Physics head" tone="head">
            regresses the ν(L) fingerprint targets
          </Box>
          <Box kicker="Adversarial head" title="Body head behind gradient reversal" tone="adv">
            {[
              facts.bodies.length === 0 ? null : facts.bodies.join(' / '),
              facts.lambdaMax === null ? null : `λ_max ${String(facts.lambdaMax)}`,
            ]
              .filter((x) => x !== null)
              .join(' · ')}
          </Box>
      </ul>
      {facts.preprocessing === null ? null : <p className="font-mono text-[11px] text-chrome">Input channels {facts.preprocessing}</p>}
    </figure>
  );
}
