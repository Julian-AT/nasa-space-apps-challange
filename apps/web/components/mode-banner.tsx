// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The shipping-mode banner on every page (PRD 3, 4, 8.6, D08). While no mode is chosen it gives
// the G0 status from validation.json; once G0 has chosen A, B or C it adds that mode's PRD 4 title
// wording, so a screenshot states the claim the mode permits and no stronger one. It is a slim hairline
// strip above the navigation; the bundle version sits beside the text, never inside it.
import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';

type ShippingMode = NonNullable<Bundle['manifest']['shipping_mode']>;

/** PRD 4, "Title wording", word for word (checked against PRD.md by mode-banner.test.tsx). */
export const MODE_TITLES: Readonly<Record<ShippingMode, string>> = {
  A: 'Radar-scattering analog candidates for Connecting Ridge',
  B: 'Terrain analog candidates for Connecting Ridge, with radar context',
  C: 'Terrain analog pilot for Connecting Ridge; radar comparison not completed',
};

/** The banner as one string, which starts with the shipping-mode line that web-smoke asserts. */
export function modeBannerText(bundle: Bundle): string {
  const line = shellText(bundle).shippingMode;
  const mode = bundle.manifest.shipping_mode;
  return mode === null ? line : `${line} — ${MODE_TITLES[mode]}`;
}

export function ModeBanner({ bundle }: Readonly<{ bundle: Bundle }>) {
  return (
    <div role="note" className="border-b bg-muted/40">
      <div className="mx-auto flex w-full max-w-[1400px] items-center gap-2.5 px-4 py-1.5 sm:px-6">
        <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-primary" />
        <p className="min-w-0 flex-1 text-xs leading-snug font-medium text-muted-foreground">{modeBannerText(bundle)}</p>
        <p className="hidden shrink-0 rounded-md border bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground md:block">
          {`Bundle ${bundle.manifest.bundle_version}`}
        </p>
      </div>
    </div>
  );
}
