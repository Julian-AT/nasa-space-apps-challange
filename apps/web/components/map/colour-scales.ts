// The two colour-blind-safe numeric scales a layer legend may name (PRD 8.10): viridis and
// cividis, as evenly spaced stops of the published matplotlib maps. Tiles are coloured by the
// pipeline; these stops only draw the legend bar and the Earth candidate colours.
export type ScaleName = 'viridis' | 'cividis';

type Rgb = readonly [number, number, number];

const STOPS: Readonly<Record<ScaleName, readonly Rgb[]>> = {
  viridis: [
    [68, 1, 84],
    [71, 45, 123],
    [59, 82, 139],
    [44, 114, 142],
    [33, 145, 140],
    [40, 174, 128],
    [94, 201, 98],
    [173, 220, 48],
    [253, 231, 37],
  ],
  cividis: [
    [0, 32, 77],
    [0, 52, 110],
    [49, 68, 107],
    [92, 108, 120],
    [124, 123, 120],
    [166, 157, 117],
    [208, 192, 99],
    [255, 234, 70],
  ],
};

export function scaleColour(scale: ScaleName, t: number): Rgb {
  const stops = STOPS[scale];
  const clamped = Math.min(1, Math.max(0, t));
  const position = clamped * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(position));
  const frac = position - index;
  const a = stops[index] as Rgb;
  const b = stops[index + 1] as Rgb;
  return [0, 1, 2].map((i) => Math.round((a[i] as number) + ((b[i] as number) - (a[i] as number)) * frac)) as unknown as Rgb;
}

/** A CSS linear-gradient for the legend bar. */
export function scaleGradient(scale: ScaleName): string {
  const stops = Array.from({ length: 9 }, (_, i) => {
    const [r, g, b] = scaleColour(scale, i / 8);
    return `rgb(${String(r)} ${String(g)} ${String(b)}) ${String((i / 8) * 100)}%`;
  });
  return `linear-gradient(to right, ${stops.join(', ')})`;
}
