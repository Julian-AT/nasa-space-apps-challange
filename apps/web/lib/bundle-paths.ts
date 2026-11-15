// The bundle paths the web copies and the @terraluna/schema contract each one is validated with
// (PRD 8.4, CONTRACTS a.3 BUNDLE_PATHS). The Python side owns the canonical map
// (BUNDLE_PATHS in pipeline schema.py, generated to packages/schema/json/bundle-paths.json); when
// that generated file exists, tests/bundle-paths.test.ts checks that every template here is in it
// with the same contract name. Until it exists this list is the CONTRACTS a.3 table, restricted to
// the files the web reads and that have a generated schema.
//
// A path that matches no entry is never copied. Large artifacts (tiles/, windows/*.parquet,
// model/*.onnx, model/embeddings.*, report/umap.bin, reference/) stay in object storage.
import {
  CardRecordSchema,
  ComparisonsSchema,
  MetricsSchema,
  ModelRecordSchema,
  SourcesSchema,
  TargetsSchema,
  ValidationSchema,
  WindowsSchema,
} from '@terraluna/schema';

/** One segment of a bundle path, as BundlePath in @terraluna/schema allows it. */
export const SEGMENT = '[A-Za-z0-9_-][A-Za-z0-9._-]*';

export interface SchemaLike {
  safeParse(value: unknown):
    | { readonly success: true; readonly data: unknown }
    | {
        readonly success: false;
        readonly error: { readonly issues: readonly { path: readonly PropertyKey[]; message: string }[] };
      };
}

export interface BundlePathEntry {
  /** The PRD 8.4 path template, as CONTRACTS a.3 writes it. */
  readonly template: string;
  /** The contract name in BUNDLE_PATHS. */
  readonly contract: string;
  /** Matches a concrete path; group 1 is the template's key (body, target_id, card_id, name). */
  readonly pattern: RegExp;
  /** The zod schema, or 'svg_allowlist' for figures. */
  readonly schema: SchemaLike | 'svg_allowlist';
}

function exact(path: string): RegExp {
  return new RegExp(`^${path.replace(/[.]/g, '\\.')}$`);
}

export const BUNDLE_PATH_ENTRIES: readonly BundlePathEntry[] = [
  { template: 'validation.json', contract: 'Validation', pattern: exact('validation.json'), schema: ValidationSchema },
  { template: 'sources.json', contract: 'Sources', pattern: exact('sources.json'), schema: SourcesSchema },
  { template: 'targets.json', contract: 'Targets', pattern: exact('targets.json'), schema: TargetsSchema },
  {
    template: 'windows/{body}.json',
    contract: 'Windows',
    pattern: /^windows\/(moon|mars|earth)\.json$/,
    schema: WindowsSchema,
  },
  {
    template: 'comparisons/{target_id}.json',
    contract: 'Comparisons',
    pattern: new RegExp(`^comparisons/(${SEGMENT})\\.json$`),
    schema: ComparisonsSchema,
  },
  {
    template: 'cards/{card_id}.json',
    contract: 'CardRecord',
    pattern: new RegExp(`^cards/(${SEGMENT})\\.json$`),
    schema: CardRecordSchema,
  },
  {
    template: 'model/model_card.json',
    contract: 'ModelRecord',
    pattern: exact('model/model_card.json'),
    schema: ModelRecordSchema,
  },
  { template: 'report/metrics.json', contract: 'Metrics', pattern: exact('report/metrics.json'), schema: MetricsSchema },
  {
    template: 'figures/{name}.svg',
    contract: 'svg_allowlist',
    pattern: new RegExp(`^figures/(${SEGMENT})\\.svg$`),
    schema: 'svg_allowlist',
  },
];

/** The entry for a concrete bundle path, or null when the web never copies it. */
export function bundlePathEntry(path: string): { entry: BundlePathEntry; key: string | null } | null {
  for (const entry of BUNDLE_PATH_ENTRIES) {
    const match = entry.pattern.exec(path);
    if (match !== null) return { entry, key: match[1] ?? null };
  }
  return null;
}

/**
 * The figure allowlist (PRD 8.4, CONTRACTS a.3): an SVG with a script element, an on* event
 * attribute, a foreignObject or any href that is not a fragment reference is refused. Returns the
 * reason, or null when the SVG passes.
 */
export function svgRefusal(text: string): string | null {
  if (!/<svg[\s>]/i.test(text)) return 'it has no <svg> element';
  if (/<script[\s>/]/i.test(text)) return 'it contains a <script> element';
  if (/<foreignObject[\s>/]/i.test(text)) return 'it contains a foreignObject element';
  if (/<!ENTITY/i.test(text)) return 'it declares an XML entity';
  if (/\son[a-z]+\s*=/i.test(text)) return 'it has an on* event attribute';
  const hrefs = text.matchAll(/(?:xlink:)?href\s*=\s*(["'])(.*?)\1/gi);
  for (const match of hrefs) {
    if (!(match[2] ?? '').startsWith('#')) return `it has a non-fragment href ${JSON.stringify(match[2])}`;
  }
  if (/url\(\s*['"]?(?!#)/i.test(text)) return 'it has a url() reference that is not a fragment';
  return null;
}
