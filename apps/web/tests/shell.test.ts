// The text every page shows (PRD 8.4, 8.6): the red mock banner, the shipping-mode line with the
// G0 status while no mode is chosen, and the bundle version in the footer.
import type { BundleManifest, Validation } from '@terraluna/schema';
import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { describe, expect, it } from 'vitest';

import { MOCK_BANNER, shellText } from '../lib/shell.ts';
import { ALL_GATES_NOT_RUN, gate, manifestFor, toBytes } from './helpers.ts';

function bundle(
  fields: Record<string, unknown>,
  records: unknown[] = ALL_GATES_NOT_RUN,
): { manifest: BundleManifest; validation: Validation } {
  const validation = ValidationSchema.parse(records);
  const manifest = BundleManifestSchema.parse(manifestFor(toBytes(records), fields));
  return { manifest, validation };
}

describe('shellText', () => {
  it('uses the exact banner wording', () => {
    expect(MOCK_BANNER).toBe('Mock bundle — placeholder data, not results');
  });

  it('shows the mock banner only for a mock bundle', () => {
    expect(shellText(bundle({ mock: true })).mockBanner).toBe(MOCK_BANNER);
    expect(shellText(bundle({ mock: false })).mockBanner).toBeNull();
  });

  it('shows the G0 status from validation.json while no shipping mode is chosen', () => {
    expect(shellText(bundle({ shipping_mode: null })).shippingMode).toBe(
      'Shipping mode: not chosen (G0 NOT_RUN)',
    );
    const records = [gate('G1', 'PASS'), gate('G0', 'PARTIAL')];
    expect(shellText(bundle({ shipping_mode: null }, records)).shippingMode).toBe(
      'Shipping mode: not chosen (G0 PARTIAL)',
    );
  });

  it('says so when validation.json has no G0 record, rather than inventing a status', () => {
    expect(shellText(bundle({ shipping_mode: null }, [gate('G1')])).shippingMode).toBe(
      'Shipping mode: not chosen (G0 not recorded)',
    );
  });

  it.each(['A', 'B', 'C'])('shows a chosen shipping mode %s', (mode) => {
    const records = [gate('G0', 'PASS')];
    expect(shellText(bundle({ shipping_mode: mode }, records)).shippingMode).toBe(
      `Shipping mode: ${mode}`,
    );
  });

  it('puts the bundle version in the footer', () => {
    expect(shellText(bundle({ bundle_version: 'mock-0' })).footer).toBe('Bundle version: mock-0');
  });
});
