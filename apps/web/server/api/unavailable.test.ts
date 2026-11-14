// The routes for data the API cannot serve yet (PRD 8.5, T15-05): matches, windows, compare and
// model, with /targets/{id} from T15-04 under the same rule. Every answer is problem+json: 404 when
// the bundle does not hold the requested record or file, 501 when it holds the file but its records
// have no @terraluna/schema schema. No answer carries a distance, rank or fingerprint, nothing is
// computed in place of a missing record, and the OpenAPI document says what the routes do.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { comparisonPath, windowsTopNPath } from '../../lib/bundle.ts';
import { ALL_GATES_NOT_RUN, gate, WEB_DIR } from '../../tests/helpers.ts';
import type { BundleReader } from './app.ts';
import { PROBLEM_MEDIA_TYPE } from './problem.ts';
import {
  expectProblem,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseBundleReader,
  TEST_BYTES_MARKER,
  testBytes,
} from './test-support.ts';
import { NOT_IN_BUNDLE_STATUS, NOT_SERVED_STATUS } from './unavailable.ts';

/** Ids for these tests only. They are test input, not targets, windows or models of any bundle. */
const TARGET = 'test-target-alpha';
const WINDOW_A = 'test-window-alpha';
const WINDOW_B = 'test-window-beta';
const OTHER = 'test-target-gamma';
const TEST_MODEL = { id: 'test-model-alpha', status: 'EXPERIMENTAL', g5: 'PASS' };
const G5_PASS = [...ALL_GATES_NOT_RUN.filter((r) => r.gate_id !== 'G5'), gate('G5', 'PASS')];

const MATCHES = `/api/v1/targets/${TARGET}/matches`;

/** Requests to every unavailable-data route, with valid parameters. */
const VALID_REQUESTS = [
  `/api/v1/targets/${TARGET}`,
  `${MATCHES}?body=earth&family=terrain`,
  `${MATCHES}?body=earth&family=radar`,
  `${MATCHES}?body=earth&family=model`,
  `${MATCHES}?body=earth&family=terrain&limit=1&cursor=`,
  `${MATCHES}?body=earth&family=radar&limit=500&cursor=Ab_9-z`,
  `${MATCHES}?limit=50&family=model&body=earth`,
  `/api/v1/windows/${WINDOW_A}`,
  `/api/v1/compare?a=${WINDOW_A}&b=${WINDOW_B}`,
  `/api/v1/compare?a=${TARGET}&b=${WINDOW_B}`,
  '/api/v1/model',
];

/** Requests to the same routes with parameters that fail validation. */
const INVALID_REQUESTS = [
  `${MATCHES}?body=earth&family=all`,
  `${MATCHES}?body=moon&family=terrain`,
  `${MATCHES}?body=earth&family=terrain&limit=0`,
  `${MATCHES}?body=earth&family=terrain&limit=501`,
  `${MATCHES}?body=earth&family=terrain&cursor=a%2Fb`,
  '/api/v1/targets/.x/matches?body=earth&family=terrain',
  '/api/v1/windows/.x',
  `/api/v1/compare?a=${WINDOW_A}`,
];

/** Every number in a JSON value, with its path. */
function numbers(value: unknown, path: string[] = []): [string, number][] {
  if (typeof value === 'number') return [[path.join('.'), value]];
  if (Array.isArray(value)) return value.flatMap((item, i) => numbers(item, [...path, String(i)]));
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => numbers(item, [...path, key]));
  }
  return [];
}

/** Every object key in a JSON value. */
function keys(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(keys);
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => [key, ...keys(item)]);
  }
  return [];
}

const RESULT_KEY = /distance|rank|fingerprint|embedding|score|percentile|similar|match/i;

/** A decimal number in prose, as a fabricated distance or score would appear. */
const DECIMAL = /\d\.\d|\d+e[-+]?\d/i;

async function expectNoResult(api: ReturnType<typeof quietApi>['api'], path: string) {
  const response = await api.request(path);
  expect(response.headers.get('content-type'), path).toBe(PROBLEM_MEDIA_TYPE);
  const text = await response.text();
  const body = JSON.parse(text) as Record<string, unknown>;
  expect(numbers(body), path).toEqual([['status', response.status]]);
  expect(
    keys(body).filter((key) => RESULT_KEY.test(key)),
    path,
  ).toEqual([]);
  expect(String(body.detail), path).not.toMatch(DECIMAL);
  expect(text, path).not.toContain('Derived');
  expect(text, path).not.toContain(TEST_BYTES_MARKER);
  return response.status;
}

describe('the response bodies on the mock bundle', () => {
  it('carry no numeric distance, rank or fingerprint field, for any valid request', async () => {
    const { api } = quietApi(mockReader);
    for (const path of VALID_REQUESTS) {
      expect(await expectNoResult(api, path), path).toBe(NOT_IN_BUNDLE_STATUS);
    }
  });

  it('carry none for a request that fails validation either', async () => {
    const { api } = quietApi(mockReader);
    for (const path of INVALID_REQUESTS) {
      expect(await expectNoResult(api, path), path).toBe(400);
    }
  });

  it('are empty for HEAD', async () => {
    const { api } = quietApi(mockReader);
    for (const path of VALID_REQUESTS) {
      const head = await api.request(path, { method: 'HEAD' });
      expect(head.status, path).toBe(NOT_IN_BUNDLE_STATUS);
      expect(await head.text(), path).toBe('');
    }
  });

  it('pass nothing through from a bundle whose files hold test numbers', async () => {
    const files = {
      [comparisonPath(TARGET)]: testBytes('a comparison file'),
      'targets.json': testBytes('a target window file'),
      [windowsTopNPath('earth')]: testBytes('a window file'),
    };
    const reader = releaseBundleReader({
      targets: [TARGET],
      model: TEST_MODEL,
      gates: G5_PASS,
      files,
    });
    const { api } = quietApi(reader);
    for (const path of VALID_REQUESTS) {
      expect(await expectNoResult(api, path), path).toBe(NOT_SERVED_STATUS);
    }
  });
});

describe('the one rule for unavailable data', () => {
  it('is 404 when the bundle does not hold the record or file and 501 when it cannot serve it', () => {
    expect(NOT_IN_BUNDLE_STATUS).toBe(404);
    expect(NOT_SERVED_STATUS).toBe(501);
  });

  /** For each route: a bundle without the data, and one holding the file the data would come from. */
  const CASES: readonly [string, BundleReader, () => BundleReader][] = [
    [`/api/v1/targets/${TARGET}`, mockReader, () => releaseBundleReader({ targets: [TARGET] })],
    [
      `${MATCHES}?body=earth&family=terrain`,
      mockReader,
      () =>
        releaseBundleReader({
          targets: [TARGET],
          files: { [comparisonPath(TARGET)]: testBytes('a comparison file') },
        }),
    ],
    [
      `${MATCHES}?body=earth&family=model`,
      mockReader,
      () =>
        releaseBundleReader({
          targets: [TARGET],
          model: TEST_MODEL,
          gates: G5_PASS,
          files: { [comparisonPath(TARGET)]: testBytes('a comparison file') },
        }),
    ],
    [
      `/api/v1/windows/${WINDOW_A}`,
      mockReader,
      () => releaseBundleReader({ files: { [windowsTopNPath('earth')]: testBytes('a window') } }),
    ],
    [
      `/api/v1/compare?a=${WINDOW_A}&b=${WINDOW_B}`,
      mockReader,
      () =>
        releaseBundleReader({
          targets: [WINDOW_A],
          files: { [comparisonPath(WINDOW_A)]: testBytes('a comparison file') },
        }),
    ],
    ['/api/v1/model', mockReader, () => releaseBundleReader({ model: TEST_MODEL })],
  ];

  for (const [path, absent, held] of CASES) {
    it(`gives ${path} a 404 saying "not in bundle", then a 501 saying "not served yet"`, async () => {
      const missing = await expectProblem(
        await quietApi(absent).api.request(path),
        NOT_IN_BUNDLE_STATUS,
        'Not Found',
      );
      expect(missing.detail).toMatch(/(is|are) not in bundle mock-0/);
      expect(missing.detail).not.toContain('not served yet');

      const unserved = await expectProblem(
        await quietApi(held()).api.request(path),
        NOT_SERVED_STATUS,
        'Not Implemented',
      );
      expect(unserved.detail).toContain(`in bundle ${RELEASE_VERSION}`);
      expect(unserved.detail).not.toContain('not in bundle');
      expect(unserved.detail).toContain('not served yet');
      expect(unserved.detail).toMatch(/@terraluna\/schema has no [A-Za-z]+Record schema/);
    });
  }

  /** For each route with an id: a bundle listing files of the kind, none that could hold the id. */
  const UNKNOWN_IDS: readonly [string, () => BundleReader][] = [
    [
      `/api/v1/targets/${TARGET}`,
      () =>
        releaseBundleReader({
          targets: [OTHER],
          files: { 'targets.json': testBytes('a target window file') },
        }),
    ],
    [
      `${MATCHES}?body=earth&family=terrain`,
      () =>
        releaseBundleReader({
          targets: [OTHER],
          files: { [comparisonPath(OTHER)]: testBytes('a comparison file') },
        }),
    ],
    [
      `/api/v1/windows/${WINDOW_A}`,
      () =>
        releaseBundleReader({
          targets: [OTHER],
          files: { 'targets.json': testBytes('a target window file') },
        }),
    ],
    [
      `/api/v1/compare?a=${WINDOW_A}&b=${WINDOW_B}`,
      () =>
        releaseBundleReader({
          targets: [OTHER],
          files: {
            [comparisonPath(OTHER)]: testBytes('a comparison file'),
            'targets.json': testBytes('a target window file'),
          },
        }),
    ],
  ];

  for (const [path, reader] of UNKNOWN_IDS) {
    it(`gives ${path} a 404 for ids that no file the bundle lists could hold`, async () => {
      const problem = await expectProblem(
        await quietApi(reader()).api.request(path),
        NOT_IN_BUNDLE_STATUS,
        'Not Found',
      );
      expect(problem.detail).toContain(`not in bundle ${RELEASE_VERSION}`);
      expect(problem.detail).not.toContain('not served yet');
    });
  }
});

describe('/compare computes nothing', () => {
  const sources = () =>
    readdirSync(join(WEB_DIR, 'server', 'api'))
      .filter((name) => name.endsWith('.ts') && !name.endsWith('.test.ts'))
      .map((name) => [name, readFileSync(join(WEB_DIR, 'server', 'api', name), 'utf8')] as const);

  it('imports no @terraluna/core module anywhere in the API', () => {
    const files = sources();
    expect(files.map(([name]) => name)).toContain('compare.ts');
    for (const [name, text] of files) {
      expect(text, name).not.toMatch(/from\s+['"]@terraluna\/core/);
      expect(text, name).not.toMatch(/import\(\s*['"]@terraluna\/core/);
    }
  });

  it('has no @terraluna/core dependency in the web app', () => {
    const pkg = JSON.parse(readFileSync(join(WEB_DIR, 'package.json'), 'utf8')) as Record<
      string,
      Record<string, string> | undefined
    >;
    for (const field of ['dependencies', 'devDependencies', 'peerDependencies']) {
      expect(Object.keys(pkg[field] ?? {}), field).not.toContain('@terraluna/core');
    }
  });
});

describe('the unavailable-data routes in the OpenAPI document', () => {
  const doc = () =>
    quietApi().api.getOpenAPI31Document({
      openapi: '3.1.0',
      info: { title: 'test', version: '0' },
    });

  const PROBLEM_CONTENT = {
    content: { 'application/problem+json': { schema: { $ref: '#/components/schemas/Problem' } } },
  };

  const ROUTES: readonly [string, string[]][] = [
    ['/api/v1/targets/{id}/matches', ['400', '404', '500', '501']],
    ['/api/v1/windows/{id}', ['400', '404', '500', '501']],
    ['/api/v1/compare', ['400', '404', '500', '501']],
    ['/api/v1/model', ['404', '500', '501']],
  ];

  for (const [path, statuses] of ROUTES) {
    it(`lists ${path} as a get with only problem+json responses: ${statuses.join(', ')}`, () => {
      const item = doc().paths?.[path];
      expect(Object.keys(item ?? {})).toEqual(['get']);
      const responses = item?.get?.responses ?? {};
      expect(Object.keys(responses).sort()).toEqual(statuses);
      for (const status of statuses)
        expect(responses[status], status).toMatchObject(PROBLEM_CONTENT);
      expect(item?.get?.description ?? '').toContain('@terraluna/schema');
    });
  }

  it('documents the matches parameters with their values, limits and default', () => {
    const parameters = doc().paths?.['/api/v1/targets/{id}/matches']?.get?.parameters ?? [];
    expect(parameters).toHaveLength(5);
    expect(parameters).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'id', in: 'path', required: true }),
        expect.objectContaining({
          name: 'body',
          in: 'query',
          required: true,
          schema: expect.objectContaining({ type: 'string', enum: ['earth'] }) as unknown,
        }),
        expect.objectContaining({
          name: 'family',
          in: 'query',
          required: true,
          schema: expect.objectContaining({
            type: 'string',
            enum: ['terrain', 'radar', 'model'],
          }) as unknown,
        }),
        expect.objectContaining({
          name: 'limit',
          in: 'query',
          required: false,
          schema: expect.objectContaining({
            type: 'integer',
            minimum: 1,
            maximum: 500,
            default: 50,
          }) as unknown,
        }),
        expect.objectContaining({
          name: 'cursor',
          in: 'query',
          required: false,
          schema: expect.objectContaining({
            type: 'string',
            pattern: '^[A-Za-z0-9_-]*$',
            maxLength: 256,
          }) as unknown,
        }),
      ]),
    );
  });

  it('documents the window id and the compare ids with the id pattern', () => {
    const window = doc().paths?.['/api/v1/windows/{id}']?.get?.parameters ?? [];
    expect(window).toEqual([
      expect.objectContaining({
        name: 'id',
        in: 'path',
        required: true,
        schema: expect.objectContaining({ pattern: '^[A-Za-z0-9_-][A-Za-z0-9._-]*$' }) as unknown,
      }),
    ]);
    const compare = doc().paths?.['/api/v1/compare']?.get?.parameters ?? [];
    expect(compare.map((p) => ('name' in p ? [p.name, p.in, p.required] : []))).toEqual([
      ['a', 'query', true],
      ['b', 'query', true],
    ]);
  });

  it('says that model-family matches are Predicted and need G5 PASS', () => {
    const description = doc().paths?.['/api/v1/targets/{id}/matches']?.get?.description ?? '';
    expect(description).toContain('Predicted');
    expect(description).toContain('G5');
  });

  it('says that /compare computes nothing on request yet', () => {
    const description = doc().paths?.['/api/v1/compare']?.get?.description ?? '';
    expect(description).toContain('not computed on request');
  });

  it('defines no comparison, window or model record component', () => {
    const names = Object.keys(doc().components?.schemas ?? {});
    expect(names.filter((name) => /comparison|window|model|match/i.test(name))).toEqual([]);
  });
});
