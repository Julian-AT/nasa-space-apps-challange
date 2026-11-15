// "Our model" on `/` (PRD 3, 8.6, 11.7): Terraluna's two models in the ShadowAstra numbered-section
// style. The physics fingerprint is Derived and the only thing that ranks; the learned encoder is
// Predicted, EXPERIMENTAL and never ranked while G5 is not PASS. Every number is a prop read from
// the bundle and the tracked probe records by readModelFacts; nothing scientific is typed here.
import Link from 'next/link';
import type { ReactNode } from 'react';

import { EvidenceLabel } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

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
    <Card className="h-full justify-between gap-3">
      <CardHeader>
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
      </CardHeader>
      <CardContent>
      {fact !== null && !fact.ok ? (
        <div className="flex flex-col gap-2">
          <EvidenceLabel label="Unavailable" />
          <p className="text-sm text-muted-foreground">{fact.reason}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-2xl leading-none font-semibold tracking-tight tabular-nums sm:text-3xl">{render}</p>
          {note === undefined ? null : <div className="text-xs leading-relaxed text-muted-foreground">{note}</div>}
        </div>
      )}
      </CardContent>
    </Card>
  );
}

function Bullet({ children }: { readonly children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="mt-[0.55em] size-1.5 shrink-0 rounded-full bg-primary/70" />
      <span>{children}</span>
    </li>
  );
}

export function ModelStats({ facts, compact = false }: { readonly facts: ModelFacts; readonly compact?: boolean }) {
  const { parameters, trainingWindows, embeddingDim, wasmParity } = facts;
  return (
    <div className={compact ? 'grid grid-cols-2 gap-3 sm:gap-4' : 'grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4'}>
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
              <span className="ml-1 text-base font-normal text-muted-foreground">-d</span>
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
    </div>
  );
}

export function ModelSection({ facts }: { readonly facts: ModelFacts }) {
  const { lagsM, windowKm, targets, firstProbeBudgetS } = facts;
  const lagText = lagsM === null || lagsM.length === 0 ? 'common lags' : `lags of ${lagsM.map((l) => String(l)).join(', ')} m`;
  const targetText = targets.length === 0 ? 'the lunar and Martian targets' : targets.map((t) => `${t.name} (${t.body === 'moon' ? 'Moon' : 'Mars'})`).join(', ');
  return (
    <section aria-labelledby="home-model" className="mt-16 border-t pt-12 sm:mt-20 sm:pt-16">
      <div className="mb-8 flex flex-col gap-3">
        <p className="text-xs font-medium tracking-wide text-primary uppercase">Our model</p>
        <div className="flex flex-col gap-3">
          <h2 id="home-model" className="text-3xl leading-tight font-semibold tracking-tight text-balance sm:text-4xl">
            Our own physics model, <span className="text-primary">measured live.</span>
          </h2>
          <p className="max-w-3xl text-base leading-relaxed text-muted-foreground sm:text-lg">
            A physics fingerprint ranks every candidate; our learned encoder runs alongside it in your browser.
          </p>
        </div>
      </div>

      <ModelStats facts={facts} />

      <Tabs defaultValue="physics" className="mt-6">
        <TabsList>
          <TabsTrigger value="physics">Physics fingerprint</TabsTrigger>
          <TabsTrigger value="encoder">Learned encoder</TabsTrigger>
        </TabsList>
        <TabsContent value="physics" forceMount className="data-[state=inactive]:hidden">
        <article aria-labelledby="home-model-physics">
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-3">
          <h3 id="home-model-physics" className="text-xl font-semibold tracking-tight">
            Physics fingerprint <span className="font-normal text-muted-foreground">(Derived, used for ranking)</span>
          </h3>
            <EvidenceLabel label="Derived" />
          </CardHeader>
          <CardContent>
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
          </CardContent>
        </Card>
        </article>
        </TabsContent>

        <TabsContent value="encoder" forceMount className="data-[state=inactive]:hidden">
        <article aria-labelledby="home-model-encoder">
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-3">
          <h3 id="home-model-encoder" className="text-xl font-semibold tracking-tight">
            Learned encoder{' '}
            <span className="font-normal text-muted-foreground">
              (experimental)
            </span>
          </h3>
            <span className="flex flex-wrap items-center gap-2">
              <EvidenceLabel label="Predicted" />
            </span>
          </CardHeader>
          <CardContent>
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
            <Bullet>The physics fingerprint drives the rankings; the encoder adds a learned view of terrain similarity.</Bullet>
          </ul>
          </CardContent>
        </Card>
        </article>
        </TabsContent>
      </Tabs>

      <ul className="mt-8 flex flex-wrap gap-3">
        <li>
          <Button asChild size="lg">
            <Link href="/find" className="no-underline">
              Measure any point live {'→'}
            </Link>
          </Button>
        </li>
        <li>
          <Button asChild size="lg" variant="outline">
            <Link href="/model" className="no-underline">
              Read the model report
            </Link>
          </Button>
        </li>
        <li>
          <Button asChild size="lg" variant="outline">
            <Link href="/paper" className="no-underline">
              Read the paper
            </Link>
          </Button>
        </li>
        <li>
          <Button asChild size="lg" variant="ghost">
            <a href="/paper/terraluna-paper.pdf" className="no-underline">
              Paper (PDF)
            </a>
          </Button>
        </li>
      </ul>
    </section>
  );
}
