// The text every page shows (PRD 8.4, 8.6): the red mock banner for a mock bundle, the shipping
// mode (with the G0 status while none is chosen) and the bundle version for the footer. Each is
// one string, so React renders it as one text node.
import type { Bundle } from './bundle.ts';

export const MOCK_BANNER = 'Mock bundle — placeholder data, not results';

export interface ShellText {
  readonly mockBanner: string | null;
  readonly shippingMode: string;
  readonly footer: string;
}

export function shellText({ manifest, validation }: Bundle): ShellText {
  let shippingMode: string;
  if (manifest.shipping_mode === null) {
    // A missing G0 record is said to be missing; it is never shown as NOT_RUN or any other status.
    const g0 = validation.find((record) => record.gate_id === 'G0');
    shippingMode = `Shipping mode: not chosen (G0 ${g0?.status ?? 'not recorded'})`;
  } else {
    shippingMode = `Shipping mode: ${manifest.shipping_mode}`;
  }
  return {
    mockBanner: manifest.mock ? MOCK_BANNER : null,
    shippingMode,
    footer: `Bundle version: ${manifest.bundle_version}`,
  };
}
