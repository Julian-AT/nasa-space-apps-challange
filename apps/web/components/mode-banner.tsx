// The shipping-mode banner on every page (PRD 3, 4, 8.6, D08). While no mode is chosen it gives
// the G0 status from validation.json; once G0 has chosen A, B or C it adds that mode's PRD 4 title
// wording, so a screenshot states the claim the mode permits and no stronger one.
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
    <div className="border-b border-line bg-card/60">
      <div className="mx-auto w-full max-w-6xl px-4 py-2 sm:px-6">
        <p className="inline-flex items-start gap-2 rounded-full border border-evidence-derived/50 bg-evidence-derived/10 px-3 py-1 text-sm font-semibold text-foreground">
          {modeBannerText(bundle)}
        </p>
      </div>
    </div>
  );
}
