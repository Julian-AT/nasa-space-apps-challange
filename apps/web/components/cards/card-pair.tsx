// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The visual pair of a card: the target hillshade next to the Earth hillshade, both bundle tiles
// (Derived, hillshade 45 deg altitude / 315 deg azimuth) with the 5 km window outlined. Each frame
// carries a small overlay label ("Moon · Connecting Ridge"), drawn with theme tokens only.
import { cn } from '@/lib/utils';

import type { CardVisual, TileView } from './card-visual.ts';

const BODY_NAMES = { moon: 'Moon', mars: 'Mars', earth: 'Earth' } as const;

const pct = (n: number): string => `${String(n * 100)}%`;

function Frame({ tile, body, name, big }: { readonly tile: TileView | null; readonly body: keyof typeof BODY_NAMES; readonly name: string; readonly big: boolean }) {
  return (
    <figure className="m-0 min-w-0" data-body={body}>
      <div className="relative aspect-square overflow-hidden rounded-lg border bg-muted">
        {tile === null ? (
          <div className="flex size-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
            No hillshade tile in this bundle
          </div>
        ) : (
          <>
            {tile.tiles.map((t) => (
              <img
                key={`${String(t.x)}/${String(t.y)}`}
                src={t.src}
                alt={`${tile.caption}, tile ${String(tile.pick.z)}/${String(t.x)}/${String(t.y)}`}
                loading="lazy"
                decoding="async"
                className="absolute max-w-none"
                style={{
                  width: pct(1 / tile.pick.view.side),
                  height: pct(1 / tile.pick.view.side),
                  left: pct((t.x - tile.pick.view.x0) / tile.pick.view.side),
                  top: pct((t.y - tile.pick.view.y0) / tile.pick.view.side),
                }}
              />
            ))}
            <span
              role="img"
              aria-label="5 km analysis window outline"
              className="absolute rounded-[3px] border-2 border-primary"
              style={{ left: pct(tile.pick.box.left), top: pct(tile.pick.box.top), width: pct(tile.pick.box.width), height: pct(tile.pick.box.height) }}
            />
          </>
        )}
        <figcaption
          className={cn(
            'absolute bottom-2 left-2 flex max-w-[calc(100%-1rem)] items-center gap-1 rounded-md border bg-background/85 px-2 py-0.5 text-foreground shadow-sm backdrop-blur-sm',
            big ? 'text-sm' : 'text-xs',
          )}
        >
          <span className="shrink-0 font-medium">{BODY_NAMES[body]}</span>
          <span aria-hidden="true" className="text-muted-foreground">·</span>
          <span className="min-w-0 truncate">{name}</span>
        </figcaption>
      </div>
    </figure>
  );
}

export function CardPair({ visual, big = false }: { readonly visual: CardVisual; readonly big?: boolean }) {
  return (
    <div className={cn('grid grid-cols-2', big ? 'gap-4' : 'gap-2')} data-slot="card-pair">
      <Frame tile={visual.targetTile} body={visual.body} name={visual.targetName} big={big} />
      <Frame tile={visual.earthTile} body="earth" name={visual.earthName} big={big} />
    </div>
  );
}
