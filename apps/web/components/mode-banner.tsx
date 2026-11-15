// The shipping-mode banner on every page (PRD 3, 4, 8.6, D08). While no mode is chosen it gives
// the G0 status from validation.json; once G0 has chosen A, B or C it adds that mode's PRD 4 title
// wording, so a screenshot states the claim the mode permits and no stronger one. It is a compact
// status pill in the header; the bundle version sits beside the text, never inside it.
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
    <div className="order-2 ml-auto flex max-w-full items-center gap-2 rounded-3xl border border-line-2 bg-panel px-3 py-1.5 xl:order-3 xl:ml-0">
      <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-flare" />
      <p className="text-xs leading-snug font-semibold text-foreground">{modeBannerText(bundle)}</p>
      <p className="hidden shrink-0 border-l border-line-2 pl-2 font-mono text-[11px] text-chrome sm:block">
        {`Bundle ${bundle.manifest.bundle_version}`}
      </p>
    </div>
  );
}
