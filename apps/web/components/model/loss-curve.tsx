// A loss curve drawn as server-side inline SVG (no chart library on /model, PRD 8.10): the mean
// line and the min-max band across seeds, straight from the LossEpoch records, with a text table.
import { Card } from '@/components/ui/card';
import type { Curves } from '@terraluna/schema';

import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

export type LossEpoch = Curves['loss_terms'][number]['epochs'][number];

const W = 320;
const H = 120;
const PAD = 8;

/** Plot coordinates of the records: x is the epoch, y the value, both linear, y flipped. */
export function curveGeometry(epochs: readonly LossEpoch[]) {
  const xs = epochs.map((e) => e.epoch);
  const lo = Math.min(...epochs.map((e) => e.min));
  const hi = Math.max(...epochs.map((e) => e.max));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const sx = (x: number) => PAD + (x1 === x0 ? (W - 2 * PAD) / 2 : ((x - x0) / (x1 - x0)) * (W - 2 * PAD));
  const sy = (y: number) => PAD + (hi === lo ? (H - 2 * PAD) / 2 : (1 - (y - lo) / (hi - lo)) * (H - 2 * PAD));
  const pts = (f: (e: LossEpoch) => number) => epochs.map((e) => [sx(e.epoch), sy(f(e))] as const);
  return { mean: pts((e) => e.mean), min: pts((e) => e.min), max: pts((e) => e.max) };
}

const d = (p: readonly (readonly [number, number])[]) => p.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');

export function LossCurve({ term, epochs }: { readonly term: string; readonly epochs: readonly LossEpoch[] }) {
  if (epochs.length === 0) return <p>{term}: no epochs in the record.</p>;
  const g = curveGeometry(epochs);
  const band = `${d(g.max)} ${d([...g.min].reverse()).replace(/^M/, 'L')} Z`;
  const name = `Loss term ${term}: mean over seeds with min to max band, ${String(epochs.length)} epochs`;
  return (
    <Card size="sm">
    <figure data-loss-term={term} className="flex flex-col gap-2 px-4">
      <figcaption className="flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
        <span className="font-mono font-medium text-foreground">{term}</span>
        <span className="font-mono text-[11px]">
          epochs {epochs[0]?.epoch}–{epochs[epochs.length - 1]?.epoch} · {Number(Math.min(...epochs.map((e) => e.min)).toPrecision(3))} to{' '}
          {Number(Math.max(...epochs.map((e) => e.max)).toPrecision(3))}
        </span>
      </figcaption>
      <svg role="img" aria-label={name} viewBox={`0 0 ${String(W)} ${String(H)}`} className="h-auto w-full rounded-md bg-muted/40">
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={PAD} x2={W - PAD} y1={PAD + f * (H - 2 * PAD)} y2={PAD + f * (H - 2 * PAD)} stroke="currentColor" strokeWidth={1} className="text-border" />
        ))}
        <path d={band} fill="currentColor" className="text-primary" opacity={0.18} />
        <path data-series="mean" d={d(g.mean)} fill="none" stroke="currentColor" strokeWidth={2.25} strokeLinejoin="round" className="text-primary" />
      </svg>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">Values as a table</summary>
        <Table>
          <TableCaption>{name}</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Epoch</TableHead>
              <TableHead scope="col">Mean</TableHead>
              <TableHead scope="col">Min</TableHead>
              <TableHead scope="col">Max</TableHead>
              <TableHead scope="col">Seeds</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {epochs.map((e) => (
              <TableRow key={e.epoch}>
                <TableCell>{e.epoch}</TableCell>
                <TableCell>{e.mean}</TableCell>
                <TableCell>{e.min}</TableCell>
                <TableCell>{e.max}</TableCell>
                <TableCell>{e.n_seeds}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </details>
    </figure>
    </Card>
  );
}
