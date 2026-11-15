// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The visual pair of a card: the target hillshade next to the Earth hillshade, both bundle tiles
// (Derived, hillshade 45 deg altitude / 315 deg azimuth) with the 5 km window outlined.
import { BODY_COLOURS } from './card-geo.ts';
import type { CardVisual, TileView } from './card-visual.ts';

function Frame({ tile, body, label, name, big }: { readonly tile: TileView | null; readonly body: keyof typeof BODY_COLOURS; readonly label: string; readonly name: string; readonly big: boolean }) {
  return (
    <figure className="m-0 min-w-0">
      <div className="relative aspect-square overflow-hidden rounded-lg border bg-muted">
        {tile === null ? (
          <div className="flex size-full items-center justify-center p-3 text-center font-mono text-[10px] tracking-[0.18em] text-muted-foreground uppercase">
            Tile unavailable
          </div>
        ) : (
          <>
            {(() => {
              const { box, view, z } = tile.pick;
              const pct = (n: number): string => `${String(n * 100)}%`;
              return (
                <>
                  {tile.tiles.map((t) => (
                    <img
                      key={`${String(t.x)}/${String(t.y)}`}
                      src={t.src}
                      alt={`${tile.caption}, tile ${String(z)}/${String(t.x)}/${String(t.y)}`}
                      loading="lazy"
                      decoding="async"
                      className="absolute max-w-none"
                      style={{
                        width: pct(1 / view.side),
                        height: pct(1 / view.side),
                        left: pct((t.x - view.x0) / view.side),
                        top: pct((t.y - view.y0) / view.side),
                      }}
                    />
                  ))}
                  <span
                    role="img"
                    aria-label="5 km analysis window outline"
                    className="absolute rounded-[2px] border-2 border-primary"
                    style={{ left: pct(box.left), top: pct(box.top), width: pct(box.width), height: pct(box.height) }}
                  />
                </>
              );
            })()}
          </>
        )}
      </div>
      <figcaption className={`mt-2 flex items-center gap-2 ${big ? 'text-sm' : 'text-xs'}`}>
        <span aria-hidden="true" className="size-2.5 shrink-0 rounded-full" style={{ background: BODY_COLOURS[body] }} />
        <span className="min-w-0 truncate text-muted-foreground capitalize">{label}</span>
        <span className="min-w-0 truncate">{name}</span>
      </figcaption>
    </figure>
  );
}

export function CardPair({ visual, big = false }: { readonly visual: CardVisual; readonly big?: boolean }) {
  return (
    <div className="grid grid-cols-2 gap-3" data-slot="card-pair">
      <Frame tile={visual.targetTile} body={visual.body} label={visual.body} name={visual.targetName} big={big} />
      <Frame tile={visual.earthTile} body="earth" label="Earth" name={visual.earthName} big={big} />
    </div>
  );
}
