// The text alternative of a nu(L) chart: every stored lag with its value or its Unavailable reason.
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

import { type NamedSeries } from './series.ts';

export function NuTable({ caption, series }: { readonly caption: string; readonly series: readonly NamedSeries[] }) {
  const lags = [...new Set(series.flatMap((s) => [...s.series.points.map((p) => p.lag_m), ...s.series.gaps.map((g) => g.lag_m)]))].sort(
    (a, b) => a - b,
  );
  return (
    <Table>
      <TableCaption>{caption}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Lag L (m)</TableHead>
          {series.map((s) => (
            <TableHead scope="col" key={s.id}>
              {s.role}: ν(L) ({s.evidence})
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {lags.map((lag) => (
          <TableRow key={lag}>
            <TableCell>{lag}</TableCell>
            {series.map((s) => {
              const point = s.series.points.find((p) => p.lag_m === lag);
              const gap = s.series.gaps.find((g) => g.lag_m === lag);
              return (
                <TableCell key={s.id} data-lag={lag}>
                  {point !== undefined ? (
                    <span title={String(point.nu)}>{Number.isInteger(point.nu) ? point.nu : Number(point.nu.toPrecision(6))}</span>
                  ) : gap !== undefined ? (
                    <span>
                      <strong>Unavailable</strong>: {gap.reason}
                    </span>
                  ) : (
                    <span>
                      <strong>Unavailable</strong>: lag_not_in_record
                    </span>
                  )}
                </TableCell>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
