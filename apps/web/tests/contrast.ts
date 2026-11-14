// WCAG 2.2 contrast for the design-token tests (PRD 8.10): relative luminance and contrast ratio
// as defined in WCAG 2.2, section "Definitions" (relative luminance, contrast ratio), plus the HSL
// hue and saturation used to tell the reserved mock red apart from every other token.

export interface Rgb {
  readonly r: number;
  readonly g: number;
  readonly b: number;
}

/** Parses #rrggbb only, so every token has one exact sRGB value. */
export function parseHex(value: string): Rgb {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value.trim());
  if (match === null) throw new Error(`not a #rrggbb colour: ${value}`);
  const [, r = '', g = '', b = ''] = match;
  return { r: Number.parseInt(r, 16), g: Number.parseInt(g, 16), b: Number.parseInt(b, 16) };
}

function linear(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance({ r, g, b }: Rgb): number {
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(parseHex(a));
  const lb = relativeLuminance(parseHex(b));
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** HSL hue in degrees [0, 360) and saturation in [0, 1]. */
export function hueSaturation(value: string): { hue: number; saturation: number } {
  const { r, g, b } = parseHex(value);
  const [rn, gn, bn] = [r / 255, g / 255, b / 255];
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const delta = max - min;
  const lightness = (max + min) / 2;
  if (delta === 0) return { hue: 0, saturation: 0 };
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  let hue: number;
  if (max === rn) hue = ((gn - bn) / delta) % 6;
  else if (max === gn) hue = (bn - rn) / delta + 2;
  else hue = (rn - gn) / delta + 4;
  hue *= 60;
  if (hue < 0) hue += 360;
  return { hue, saturation };
}

/** Within `degrees` of pure red (0°) and saturated enough to read as a colour, not a grey. */
export function isRed(value: string, degrees = 30, minSaturation = 0.2): boolean {
  const { hue, saturation } = hueSaturation(value);
  const distance = Math.min(hue, 360 - hue);
  return saturation >= minSaturation && distance <= degrees;
}
