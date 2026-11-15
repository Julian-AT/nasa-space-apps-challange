// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The visual pair of a card: the target hillshade next to the Earth hillshade, both bundle tiles
// (Derived, hillshade 45 deg altitude / 315 deg azimuth) with the 5 km window outlined.
import { BODY_COLOURS } from './card-geo.ts';
import type { CardVisual, TileView } from './card-visual.ts';

function Frame({ tile, body, label, name, big }: { readonly tile: TileView | null; readonly body: keyof typeof BODY_COLOURS; readonly label: string; readonly name: string; readonly big: boolean }) {
  return (
    <figure className="m-0 min-w-0">
      <div className="relative aspect-square overflow-hidden rounded-xl border border-line bg-background">
        {tile === null ? (
          <div className="flex size-full items-center justify-center p-3 text-center font-mono text-[10px] tracking-[0.18em] text-muted-foreground uppercase">
            Tile unavailable
          </div>
        ) : (
          <>
            <img src={tile.src} alt={`${tile.caption}, ${String(tile.pick.z)}/${String(tile.pick.x)}/${String(tile.pick.y)}, window outlined`} loading="lazy" decoding="async" className="size-full object-cover" />
            <span
              aria-hidden="true"
              className="absolute rounded-[2px] border border-flare shadow-[0_0_0_1px_rgba(0,0,0,0.6)]"
              style={{
                left: `${String(tile.pick.box.left * 100)}%`,
                top: `${String(tile.pick.box.top * 100)}%`,
                width: `${String(tile.pick.box.width * 100)}%`,
                height: `${String(tile.pick.box.height * 100)}%`,
              }}
            />
          </>
        )}
      </div>
      <figcaption className={`mt-2 flex items-center gap-2 ${big ? 'text-sm' : 'text-xs'}`}>
        <span aria-hidden="true" className="size-2.5 shrink-0 rounded-full" style={{ background: BODY_COLOURS[body] }} />
        <span className="min-w-0 truncate font-mono tracking-[0.12em] text-muted-foreground uppercase">{label}</span>
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
