// nu(L) of the probed window over a target's (PRD 8.7 step 7): log-log, one chart per target and
// variant, the window solid blue and the target dashed orange, with a legend and per-point
// tooltips. The numbers are also in the table above, so the chart is never the only view.
import type { ProbeVariant } from '../../lib/probe/result.ts';

type Curve = Pick<ProbeVariant, 'nu_m'>;

const W = 320;
const H = 180;
const PAD = { left: 44, right: 12, top: 10, bottom: 30 };

function points(curve: Curve): [number, number][] {
  return Object.entries(curve.nu_m)
    .filter((e): e is [string, number] => e[1] !== null && e[1] > 0)
    .map(([lag, nu]) => [Number(lag), nu] as [number, number])
    .sort((a, b) => a[0] - b[0]);
}

export function ProbeCurves({ title, window, target, targetName }: Readonly<{ title: string; window: Curve; target: Curve; targetName: string }>) {
  const a = points(window);
  const b = points(target);
  const all = [...a, ...b];
  if (all.length === 0) return null;
  const lx = all.map(([x]) => Math.log10(x));
  const ly = all.map(([, y]) => Math.log10(y));
  const [x0, x1] = [Math.min(...lx), Math.max(...lx)];
  const [y0r, y1r] = [Math.min(...ly), Math.max(...ly)];
  const [y0, y1] = [Math.floor(y0r * 2) / 2, Math.ceil(y1r * 2) / 2 || y0r + 0.5];
  const sx = (x: number) => PAD.left + ((Math.log10(x) - x0) / (x1 - x0 || 1)) * (W - PAD.left - PAD.right);
  const sy = (y: number) => H - PAD.bottom - ((Math.log10(y) - y0) / (y1 - y0 || 1)) * (H - PAD.top - PAD.bottom);
  const path = (p: [number, number][]) => p.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join('');
  const yTicks: number[] = [];
  for (let e = y0; e <= y1 + 1e-9; e += 0.5) yTicks.push(10 ** e);
  return (
    <figure className="probe-curves mt-2">
      <style>{`.probe-curves{--series-1:#2a78d6;--series-2:#eb6834;--grid:#d9d8d4}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])) .probe-curves{--series-1:#3987e5;--series-2:#d95926;--grid:#3a3a38}}
:root[data-theme="dark"] .probe-curves{--series-1:#3987e5;--series-2:#d95926;--grid:#3a3a38}`}</style>
      <figcaption className="text-sm">{title}</figcaption>
      <ul className="flex gap-4 text-xs" aria-label="Legend">
        <li>
          <svg width="24" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="24" y2="4" stroke="var(--series-1)" strokeWidth="2" />
          </svg>{' '}
          This window
        </li>
        <li>
          <svg width="24" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="24" y2="4" stroke="var(--series-2)" strokeWidth="2" strokeDasharray="5 3" />
          </svg>{' '}
          {targetName}
        </li>
      </ul>
      <svg viewBox={`0 0 ${String(W)} ${String(H)}`} className="w-full max-w-sm" role="img" aria-label={`${title}: nu against lag, log-log`}>
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" strokeWidth="1" />
            <text x={PAD.left - 4} y={sy(t) + 3} textAnchor="end" fontSize="9" fill="currentColor">
              {t >= 10 ? t.toFixed(0) : t.toPrecision(2)}
            </text>
          </g>
        ))}
        {a.map(([x]) => (
          <text key={x} x={sx(x)} y={H - PAD.bottom + 12} textAnchor="middle" fontSize="9" fill="currentColor">
            {x}
          </text>
        ))}
        <text x={(W + PAD.left) / 2} y={H - 4} textAnchor="middle" fontSize="9" fill="currentColor">
          lag L (m)
        </text>
        <text x={10} y={(H - PAD.bottom) / 2} textAnchor="middle" fontSize="9" fill="currentColor" transform={`rotate(-90 10 ${String((H - PAD.bottom) / 2)})`}>
          nu (m)
        </text>
        <path d={path(b)} fill="none" stroke="var(--series-2)" strokeWidth="2" strokeDasharray="5 3" />
        <path d={path(a)} fill="none" stroke="var(--series-1)" strokeWidth="2" />
        {b.map(([x, y]) => (
          <circle key={`t${String(x)}`} cx={sx(x)} cy={sy(y)} r="4" fill="var(--series-2)">
            <title>{`${targetName}: nu(${String(x)} m) = ${y.toPrecision(4)} m`}</title>
          </circle>
        ))}
        {a.map(([x, y]) => (
          <circle key={`w${String(x)}`} cx={sx(x)} cy={sy(y)} r="4" fill="var(--series-1)">
            <title>{`This window: nu(${String(x)} m) = ${y.toPrecision(4)} m`}</title>
          </circle>
        ))}
      </svg>
    </figure>
  );
}
