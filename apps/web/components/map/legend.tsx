// A layer legend read from TileLayer.legend (PRD 8.10): a numeric legend is a viridis or cividis
// bar with the bundle's min, max and unit; a categorical legend lists the bundle's classes. The
// range and the unit are never typed here.
import { scaleGradient } from './colour-scales.ts';
import type { TileLayerRecord } from './tile-frame.ts';

export function LayerLegend({ layer }: { readonly layer: TileLayerRecord }) {
  const { legend } = layer;
  if (legend.kind === 'numeric') {
    const unit = legend.unit ?? layer.units;
    return (
      <figure data-slot="legend" data-kind="numeric" data-scale={legend.scale} className="flex flex-col gap-1">
        <div
          role="img"
          aria-label={`${layer.layer} colour scale ${legend.scale} from ${String(legend.min)} to ${String(legend.max)}${unit === null ? '' : ` ${unit}`}`}
          className="h-3 w-full rounded-sm border border-border"
          style={{ backgroundImage: scaleGradient(legend.scale) }}
        />
        <figcaption className="flex justify-between text-xs text-muted-foreground">
          <span>{String(legend.min)}</span>
          <span>
            {legend.scale}
            {unit === null ? '' : `, ${unit}`}
          </span>
          <span>{String(legend.max)}</span>
        </figcaption>
      </figure>
    );
  }
  return (
    <ul data-slot="legend" data-kind="classes" className="grid grid-cols-1 gap-1 text-xs">
      {legend.classes.map((c) => (
        <li key={c.code} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block size-3 rounded-sm border border-border"
            style={{ backgroundColor: `rgb(${String(c.rgba[0])} ${String(c.rgba[1])} ${String(c.rgba[2])} / ${String((c.rgba[3] ?? 255) / 255)})` }}
          />
          <span>
            {c.label} <span className="text-muted-foreground">(code {String(c.code)})</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
