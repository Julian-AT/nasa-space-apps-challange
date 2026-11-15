// "Our model" on `/` (PRD 3, 8.6, 11.7): Terraluna's two models in the ShadowAstra numbered-section
// style. The physics fingerprint is Derived and the only thing that ranks; the learned encoder is
// Predicted, EXPERIMENTAL and never ranked while G5 is not PASS. Every number is a prop read from
// the bundle and the tracked probe records by readModelFacts; nothing scientific is typed here.
import Link from 'next/link';
import type { ReactNode } from 'react';

import { EvidenceLabel } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';

import { cosine, grouped, type Fact, type ModelFacts } from './model-facts.ts';

const G5_VARIANT = { PASS: 'pass', FAIL: 'fail', PARTIAL: 'partial', NOT_RUN: 'not-run', NOT_APPLICABLE: 'not-applicable' } as const;

export function G5Badge({ status, className }: { readonly status: string | null; readonly className?: string }) {
  if (status === null) return <EvidenceLabel label="Unavailable" />;
  return (
    <Badge variant={G5_VARIANT[status as keyof typeof G5_VARIANT]} className={className}>
      G5 {status}
    </Badge>
  );
}

function Tile({ label, fact, render, note }: { readonly label: string; readonly fact: Fact<unknown> | null; readonly render: ReactNode; readonly note?: ReactNode }) {
  return (
    <div className="surface flex min-h-[132px] flex-col justify-between gap-3 rounded-3xl p-4 sm:min-h-[168px] sm:gap-4 sm:p-6">
      <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">{label}</p>
      {fact !== null && !fact.ok ? (
        <div className="flex flex-col gap-2">
          <EvidenceLabel label="Unavailable" />
          <p className="text-sm text-chrome">{fact.reason}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="font-mono text-[clamp(22px,3vw,40px)] leading-none tracking-[-0.03em] text-platinum">{render}</p>
          {note === undefined ? null : <div className="text-xs leading-relaxed text-chrome">{note}</div>}
        </div>
      )}
    </div>
  );
}

function Bullet({ children }: { readonly children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="mt-[0.6em] h-px w-4 shrink-0 bg-chrome/60" />
      <span>{children}</span>
    </li>
  );
}

export function ModelStats({ facts }: { readonly facts: ModelFacts }) {
  const { parameters, trainingWindows, embeddingDim, wasmParity, g5 } = facts;
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-5">
      <Tile
        label="Parameters"
        fact={parameters}
        render={parameters.ok ? grouped(parameters.value) : null}
        note={parameters.ok ? `from ${parameters.source}` : undefined}
      />
      <Tile
        label="Training windows"
        fact={trainingWindows}
        render={trainingWindows.ok ? grouped(trainingWindows.value.total) : null}
        note={trainingWindows.ok ? trainingWindows.value.byBody.map(([b, n]) => `${b} ${grouped(n)}`).join(' · ') : undefined}
      />
      <Tile
        label="Embedding"
        fact={embeddingDim}
        render={
          embeddingDim.ok ? (
            <>
              {embeddingDim.value}
              <span className="ml-1 text-base text-chrome">-d</span>
            </>
          ) : null
        }
        note="L2-normalised, from the ONNX export record"
      />
      <Tile
        label="Browser parity"
        fact={wasmParity}
        render={wasmParity.ok ? cosine(wasmParity.value.minCosine) : null}
        note={
          wasmParity.ok
            ? `min cosine WASM vs Python over ${String(wasmParity.value.windows)} windows (threshold ${String(wasmParity.value.threshold)})`
            : undefined
        }
      />
      <div className="surface col-span-2 flex min-h-[132px] flex-col justify-between gap-3 rounded-3xl border-status-fail/40 p-4 sm:min-h-[168px] sm:gap-4 sm:p-6 lg:col-span-1">
        <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">Promotion gate</p>
        <div className="flex flex-col gap-2">
          <p className="font-mono text-[clamp(22px,3vw,40px)] leading-none tracking-[-0.03em] text-platinum">
            {g5 === null ? 'not recorded' : g5.status}
          </p>
          <p className="text-xs leading-relaxed text-chrome">
            {g5 === null ? 'validation.json has no G5 record' : g5.status === 'PASS' ? 'G5 passed; the encoder stays a Predicted second opinion' : 'G5, so the encoder is never ranked'}
          </p>
        </div>
      </div>
    </div>
  );
}

export function ModelSection({ facts }: { readonly facts: ModelFacts }) {
  const { g5, lagsM, windowKm, targets, firstProbeBudgetS } = facts;
  const failed = facts.conditions.filter((c) => c.passed === false);
  const unevaluated = facts.conditions.filter((c) => c.passed === null);
  const lagText = lagsM === null || lagsM.length === 0 ? 'common lags' : `lags of ${lagsM.map((l) => String(l)).join(', ')} m`;
  const targetText = targets.length === 0 ? 'the lunar and Martian targets' : targets.map((t) => `${t.name} (${t.body === 'moon' ? 'Moon' : 'Mars'})`).join(', ');
  return (
    <section aria-labelledby="home-model" className="mt-16 border-t border-line pt-10 sm:mt-20">
      <div className="mb-10 grid gap-4 md:grid-cols-[8rem_1fr]">
        <p className="font-mono text-[11px] tracking-[0.22em] text-flare uppercase">00 / Our model</p>
        <div className="flex flex-col gap-4">
          <h2 id="home-model" className="text-[clamp(32px,4.6vw,64px)] leading-[0.98] font-medium tracking-[-0.04em] text-balance">
            Our own physics model, <span className="font-serif font-normal tracking-[-0.02em] text-flare italic">measured live.</span>
          </h2>
          <p className="max-w-3xl text-[17px] leading-relaxed text-chrome">
            Terraluna is built on two models of its own. A deterministic physics fingerprint ranks every candidate; a learned encoder,
            trained by us, ships openly beside it as research with its failed promotion gate in plain view.
          </p>
        </div>
      </div>

      <ModelStats facts={facts} />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <article aria-labelledby="home-model-physics" className="surface flex flex-col gap-5 rounded-3xl p-6 sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">A / Ranks candidates</p>
            <EvidenceLabel label="Derived" />
          </div>
          <h3 id="home-model-physics" className="text-2xl font-medium tracking-tight">
            Physics fingerprint <span className="text-chrome">(Derived, used for ranking)</span>
          </h3>
          <ul className="flex flex-col gap-3 text-[15px] leading-relaxed">
            <Bullet>
              The structure function ν(L) of a {windowKm === null ? '' : `${String(windowKm)} km `}elevation window at {lagText}: how much
              the ground rises and falls over each distance.
            </Bullet>
            <Bullet>
              Compared as two distances, relief and texture, each the RMS of log ν, against {targetText}.
            </Bullet>
            <Bullet>Python and the in-browser TypeScript implementation are parity-tested on the same windows.</Bullet>
            <Bullet>
              Runs live on any Earth point in a browser worker
              {firstProbeBudgetS === null ? '' : `, inside the ${String(firstProbeBudgetS)} s first-probe budget`}.
            </Bullet>
          </ul>
        </article>

        <article aria-labelledby="home-model-encoder" className="surface flex flex-col gap-5 rounded-3xl border-evidence-predicted/40 p-6 sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">B / Never ranked</p>
            <span className="flex flex-wrap items-center gap-2">
              <EvidenceLabel label="Predicted" />
              <G5Badge status={g5?.status ?? null} />
            </span>
          </div>
          <h3 id="home-model-encoder" className="text-2xl font-medium tracking-tight">
            Learned encoder{' '}
            <span className="text-chrome">
              (Predicted, experimental, {g5 === null ? 'G5 not recorded' : `G5 ${g5.status}`}, never ranked)
            </span>
          </h3>
          <ul className="flex flex-col gap-3 text-[15px] leading-relaxed">
            <Bullet>
              A ResNet-18-layout network
              {facts.parameters.ok ? ` with ${grouped(facts.parameters.value)} parameters` : ''}, embedding each window into{' '}
              {facts.embeddingDim.ok ? `${String(facts.embeddingDim.value)} numbers` : 'a vector'}.
            </Bullet>
            <Bullet>
              Trained self-supervised with physics targets on Moon, Mars and Earth windows, with gradient reversal against the body.
            </Bullet>
            <Bullet>
              Exported to ONNX and run in the browser
              {facts.wasmParity.ok ? ` (WASM parity: min cosine ${cosine(facts.wasmParity.value.minCosine)})` : ''}.
            </Bullet>
            <Bullet>
              {g5 === null ? (
                'Its promotion gate G5 is not recorded, so it stays outside every ranking.'
              ) : g5.status === 'PASS' ? (
                'G5 passed; it remains a Predicted second opinion beside the fingerprint.'
              ) : (
                <>
                  G5 {g5.status}
                  {failed.length === 0 ? '' : `: failed ${failed.map((c) => c.testId.replaceAll('_', ' ')).join(', ')}`}
                  {unevaluated.length === 0 ? '' : `; not evaluable ${unevaluated.map((c) => `${c.testId.replaceAll('_', ' ')} (${c.statistic.replaceAll('_', ' ')})`).join(', ')}`}. Its
                  distances are never used in any ranking.
                </>
              )}
            </Bullet>
          </ul>
        </article>
      </div>

      <ul className="mt-8 flex flex-wrap gap-3">
        <li>
          <Link href="/find" className="btn-primary inline-flex h-12 items-center rounded-full px-7 text-sm font-semibold no-underline">
            <span className="relative z-10">Measure any point live {'→'}</span>
          </Link>
        </li>
        <li>
          <Link
            href="/model"
            className="inline-flex h-12 items-center rounded-full border border-border px-7 text-sm font-medium no-underline transition-colors hover:bg-platinum/5"
          >
            Read the model report
          </Link>
        </li>
        <li>
          <Link
            href="/paper"
            className="inline-flex h-12 items-center rounded-full border border-border px-7 text-sm font-medium no-underline transition-colors hover:bg-platinum/5"
          >
            Read the paper
          </Link>
        </li>
        <li>
          <a
            href="/paper/terraluna-paper.pdf"
            className="inline-flex h-12 items-center rounded-full px-4 text-sm font-medium text-chrome no-underline hover:text-platinum"
          >
            Paper (PDF)
          </a>
        </li>
      </ul>
    </section>
  );
}
