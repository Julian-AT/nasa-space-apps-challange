// GET /api/v1/targets/{id}/matches (PRD 8.5, T15-05). The ranked ComparisonRecords of a target
// live in comparisons/<target_id>.json, which has no @terraluna/schema schema yet, so the route
// always answers with a problem: 404 when the bundle does not hold the matches, 501 when it holds
// the comparison file but cannot serve it. Model-family matches are refused unless a model is in
// the bundle and G5 is PASS; any model-family answer carries the Predicted label.
import { describe, expect, it, vi } from 'vitest';

import { comparisonPath, type PulledBundle } from '../../lib/bundle.ts';
import { ALL_GATES_NOT_RUN, gate } from '../../tests/helpers.ts';
import {
  CURSOR_MAX_LENGTH,
  CURSOR_PATTERN,
  MATCH_BODIES,
  MATCH_FAMILIES,
  MATCH_LIMIT_DEFAULT,
  MATCH_LIMIT_MAX,
  MATCH_LIMIT_MIN,
  MatchesQuerySchema,
} from './matches.ts';
import { VALIDATION_STATUS } from './problem.ts';
import { TARGET_ID_MAX_LENGTH } from './targets.ts';
import {
  expectProblem,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseBundleReader,
  TEST_BYTES_MARKER,
  testBytes,
} from './test-support.ts';

/** Target ids for these tests only. They are test input, not targets of any bundle. */
const TARGET = 'test-target-alpha';
const OTHER_TARGET = 'test-target-beta';

/** A model entry for these tests only, not a model of any bundle. */
const TEST_MODEL_ID = 'test-model-alpha';

function matchesPath(id: string, query = 'body=earth&family=terrain'): string {
  return `/api/v1/targets/${id}/matches?${query}`;
}

function testModel(g5: string) {
  return { id: TEST_MODEL_ID, status: 'EXPERIMENTAL', g5 };
}

/** validation.json with G5 replaced by the given statuses (none: no G5 record at all). */
function gatesWithG5(...statuses: string[]) {
  return [
    ...ALL_GATES_NOT_RUN.filter((record) => record.gate_id !== 'G5'),
    ...statuses.map((status) => gate('G5', status)),
  ];
}

/** A release bundle that lists TARGET and holds its comparison file (test bytes). */
function withComparisons(options: Parameters<typeof releaseBundleReader>[0] = {}) {
  return releaseBundleReader({
    targets: [TARGET, OTHER_TARGET],
    files: { [comparisonPath(TARGET)]: testBytes('a comparison file') },
    ...options,
  });
}

const NON_PASS = ['NOT_RUN', 'FAIL', 'PARTIAL', 'NOT_APPLICABLE'];

describe('GET /api/v1/targets/{id}/matches on the mock bundle', () => {
  for (const family of ['terrain', 'radar']) {
    it(`is a 404 problem for family=${family}, naming the target and the bundle`, async () => {
      const { api, log } = quietApi();
      const response = await api.request(matchesPath(TARGET, `body=earth&family=${family}`));
      const problem = await expectProblem(response, 404, 'Not Found');
      expect(problem.detail).toBe(`Target ${TARGET} is not in bundle mock-0.`);
      expect(problem.instance).toBe(`/api/v1/targets/${TARGET}/matches`);
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('explains for family=model that model-family matches need a model in the bundle', async () => {
    const { api, log } = quietApi();
    const response = await api.request(matchesPath(TARGET, 'body=earth&family=model'));
    const problem = await expectProblem(response, 404, 'Not Found');
    expect(problem.detail).toContain('Model-family matches are not in bundle mock-0');
    expect(problem.detail).toContain('exist only when a model is in the bundle');
    expect(log).not.toHaveBeenCalled();
  });
});

describe('GET /api/v1/targets/{id}/matches on a release bundle', () => {
  it('is a 404 problem for a target the manifest does not list', async () => {
    const { api } = quietApi(withComparisons());
    const problem = await expectProblem(
      await api.request(matchesPath('test-target-gamma')),
      404,
      'Not Found',
    );
    expect(problem.detail).toBe(`Target test-target-gamma is not in bundle ${RELEASE_VERSION}.`);
  });

  it('is a 404 problem for a listed target whose comparison file the manifest does not list', async () => {
    const { api, log } = quietApi(withComparisons());
    const problem = await expectProblem(
      await api.request(matchesPath(OTHER_TARGET)),
      404,
      'Not Found',
    );
    expect(problem.detail).toContain(`not in bundle ${RELEASE_VERSION}`);
    expect(problem.detail).toContain(comparisonPath(OTHER_TARGET));
    expect(log).not.toHaveBeenCalled();
  });

  for (const family of ['terrain', 'radar']) {
    it(`is a 501 problem for family=${family} when the comparison file is in the bundle`, async () => {
      const { api, log } = quietApi(withComparisons());
      const response = await api.request(matchesPath(TARGET, `body=earth&family=${family}`));
      const text = await response.clone().text();
      const problem = await expectProblem(response, 501, 'Not Implemented');
      expect(problem.detail).toContain(`${comparisonPath(TARGET)} is in bundle ${RELEASE_VERSION}`);
      expect(problem.detail).toContain('not served yet');
      expect(problem.detail).toContain('@terraluna/schema has no ComparisonRecord schema');
      expect(text).not.toContain(TEST_BYTES_MARKER);
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('matches the target id exactly, so another target never borrows a comparison file', async () => {
    const { api } = quietApi(
      releaseBundleReader({
        targets: [TARGET, `${TARGET}-x`],
        files: { [comparisonPath(`${TARGET}-x`)]: testBytes('a comparison file') },
      }),
    );
    await expectProblem(await api.request(matchesPath(TARGET)), 404, 'Not Found');
  });

  it('answers HEAD with the problem status and no body', async () => {
    const { api } = quietApi(withComparisons());
    const head = await api.request(matchesPath(TARGET), { method: 'HEAD' });
    expect(head.status).toBe(501);
    expect(await head.text()).toBe('');
  });

  it('refuses a manifest that fails its schema with a 500 problem, not 404 or 501', async () => {
    const bundle = withComparisons()();
    const corrupt = { ...bundle, manifest: { ...bundle.manifest, targets: [TARGET, 7] } };
    const { api, log } = quietApi(() => corrupt as unknown as PulledBundle);
    const problem = await expectProblem(
      await api.request(matchesPath(TARGET)),
      500,
      'Internal Server Error',
    );
    expect(problem.detail).toContain('manifest.json');
    expect(log).toHaveBeenCalledOnce();
  });

  it('reads the bundle on each request', async () => {
    const reader = vi.fn(mockReader);
    const { api } = quietApi(reader);
    await api.request(matchesPath(TARGET));
    await api.request(matchesPath(TARGET));
    expect(reader).toHaveBeenCalledTimes(2);
  });
});

describe('family=model matches', () => {
  const MODEL_QUERY = 'body=earth&family=model';

  it('are refused when manifest.json names no model, even with a comparison file', async () => {
    const { api } = quietApi(withComparisons({ gates: gatesWithG5('PASS') }));
    const response = await api.request(matchesPath(TARGET, MODEL_QUERY));
    const text = await response.clone().text();
    const problem = await expectProblem(response, 404, 'Not Found');
    expect(problem.detail).toContain(`Model-family matches are not in bundle ${RELEASE_VERSION}`);
    expect(problem.detail).toContain('exist only when a model is in the bundle');
    expect(text).not.toContain(TEST_BYTES_MARKER);
  });

  for (const status of NON_PASS) {
    it(`are refused while the manifest gives G5 as ${status}`, async () => {
      const reader = withComparisons({ model: testModel(status), gates: gatesWithG5('PASS') });
      const { api, log } = quietApi(reader);
      const response = await api.request(matchesPath(TARGET, MODEL_QUERY));
      const text = await response.clone().text();
      const problem = await expectProblem(response, 404, 'Not Found');
      expect(problem.detail).toContain(`Model-family matches are not in bundle ${RELEASE_VERSION}`);
      expect(problem.detail).toContain(TEST_MODEL_ID);
      expect(problem.detail).toContain('only when G5 is PASS');
      expect(problem.detail).toContain(`manifest.json gives G5 as ${status}`);
      expect(text).not.toContain(TEST_BYTES_MARKER);
      expect(log).not.toHaveBeenCalled();
    });

    it(`are refused while validation.json gives G5 as ${status}`, async () => {
      const reader = withComparisons({ model: testModel('PASS'), gates: gatesWithG5(status) });
      const { api } = quietApi(reader);
      const problem = await expectProblem(
        await api.request(matchesPath(TARGET, MODEL_QUERY)),
        404,
        'Not Found',
      );
      expect(problem.detail).toContain('only when G5 is PASS');
      expect(problem.detail).toContain(`validation.json gives G5 as ${status}`);
    });
  }

  it('are refused when validation.json has no G5 record', async () => {
    const reader = withComparisons({ model: testModel('PASS'), gates: gatesWithG5() });
    const { api } = quietApi(reader);
    const problem = await expectProblem(
      await api.request(matchesPath(TARGET, MODEL_QUERY)),
      404,
      'Not Found',
    );
    expect(problem.detail).toContain('validation.json has no G5 record');
  });

  it('are a 500 problem when validation.json lists G5 twice, which the loader refuses', async () => {
    const reader = withComparisons({
      model: testModel('PASS'),
      gates: gatesWithG5('PASS', 'PASS'),
    });
    const { api, log } = quietApi(reader);
    const response = await api.request(matchesPath(TARGET, MODEL_QUERY));
    const text = await response.clone().text();
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('validation.json');
    expect(text).not.toContain('Predicted');
    expect(log).toHaveBeenCalledOnce();
  });

  it('are refused when a reader passes validation.json with G5 twice', async () => {
    const bundle = withComparisons({ model: testModel('PASS'), gates: gatesWithG5('PASS') })();
    const doubled = { ...bundle, validation: gatesWithG5('PASS', 'PASS') };
    const { api } = quietApi(() => doubled as unknown as PulledBundle);
    const problem = await expectProblem(
      await api.request(matchesPath(TARGET, MODEL_QUERY)),
      404,
      'Not Found',
    );
    expect(problem.detail).toContain('validation.json records G5 2 times (PASS, PASS)');
  });

  it('are refused before the target is looked up', async () => {
    const reader = withComparisons({ model: testModel('NOT_RUN') });
    const { api } = quietApi(reader);
    const problem = await expectProblem(
      await api.request(matchesPath('test-target-gamma', MODEL_QUERY)),
      404,
      'Not Found',
    );
    expect(problem.detail).toContain('only when G5 is PASS');
  });

  it('pass the G5 check only when both files give G5 as PASS, and then carry Predicted', async () => {
    const reader = withComparisons({ model: testModel('PASS'), gates: gatesWithG5('PASS') });
    const { api } = quietApi(reader);
    const response = await api.request(matchesPath(TARGET, MODEL_QUERY));
    const text = await response.clone().text();
    const problem = await expectProblem(response, 501, 'Not Implemented');
    expect(problem.detail).toContain('Predicted');
    expect(problem.detail).toContain('@terraluna/schema has no ComparisonRecord schema');
    expect(text).not.toContain(TEST_BYTES_MARKER);
  });

  it('give a 404 for an unlisted target once G5 is PASS', async () => {
    const reader = withComparisons({ model: testModel('PASS'), gates: gatesWithG5('PASS') });
    const { api } = quietApi(reader);
    const problem = await expectProblem(
      await api.request(matchesPath('test-target-gamma', MODEL_QUERY)),
      404,
      'Not Found',
    );
    expect(problem.detail).toBe(`Target test-target-gamma is not in bundle ${RELEASE_VERSION}.`);
  });

  it('refuse a validation.json that fails its schema with a 500 problem', async () => {
    const bundle = withComparisons({ model: testModel('PASS'), gates: gatesWithG5('PASS') })();
    const corrupt = { ...bundle, validation: [{ gate_id: 'G5', status: 'PASSED' }] };
    const { api, log } = quietApi(() => corrupt as unknown as PulledBundle);
    const problem = await expectProblem(
      await api.request(matchesPath(TARGET, MODEL_QUERY)),
      500,
      'Internal Server Error',
    );
    expect(problem.detail).toContain('validation.json');
    expect(log).toHaveBeenCalledOnce();
  });

  for (const family of ['terrain', 'radar']) {
    it(`leave family=${family} untouched by the G5 state`, async () => {
      const reader = withComparisons({ model: testModel('NOT_RUN') });
      const { api } = quietApi(reader);
      const response = await api.request(matchesPath(TARGET, `body=earth&family=${family}`));
      const problem = await expectProblem(response, 501, 'Not Implemented');
      expect(problem.detail).not.toContain('G5');
    });
  }
});

describe('the matches query parameters', () => {
  it('have the documented values', () => {
    expect(MATCH_BODIES).toEqual(['earth']);
    expect(MATCH_FAMILIES).toEqual(['terrain', 'radar', 'model']);
    expect([MATCH_LIMIT_MIN, MATCH_LIMIT_MAX, MATCH_LIMIT_DEFAULT]).toEqual([1, 500, 50]);
    expect(CURSOR_PATTERN).toBe('^[A-Za-z0-9_-]*$');
    expect(CURSOR_MAX_LENGTH).toBe(256);
  });

  it('default limit to 50 and leave cursor unset', () => {
    expect(MatchesQuerySchema.parse({ body: 'earth', family: 'terrain' })).toEqual({
      body: 'earth',
      family: 'terrain',
      limit: 50,
    });
  });

  it('turn a limit in decimal digits into a number', () => {
    const query = { body: 'earth', family: 'radar', limit: '500', cursor: 'abc' };
    expect(MatchesQuerySchema.parse(query)).toEqual({ ...query, limit: 500 });
  });

  const ACCEPTED: readonly [string, string][] = [
    ['limit 1', 'body=earth&family=terrain&limit=1'],
    ['limit 50', 'body=earth&family=radar&limit=50'],
    ['limit 500', 'body=earth&family=model&limit=500'],
    ['an empty cursor', 'body=earth&family=terrain&cursor='],
    ['a cursor of url-safe characters', 'body=earth&family=terrain&cursor=Ab_9-z'],
    [
      `a cursor of ${String(CURSOR_MAX_LENGTH)} characters`,
      `body=earth&family=terrain&cursor=${'c'.repeat(CURSOR_MAX_LENGTH)}`,
    ],
    ['the parameters in any order', 'limit=7&family=terrain&body=earth'],
  ];

  for (const [what, query] of ACCEPTED) {
    it(`accept ${what}`, async () => {
      const { api } = quietApi();
      await expectProblem(await api.request(matchesPath(TARGET, query)), 404, 'Not Found');
    });
  }

  const REFUSED: readonly [string, string, string][] = [
    ['no body', 'family=terrain', 'body'],
    ['body=moon', 'body=moon&family=terrain', 'body'],
    ['body=Earth', 'body=Earth&family=terrain', 'body'],
    ['two bodies', 'body=earth&body=earth&family=terrain', 'body'],
    ['no family', 'body=earth', 'family'],
    ['family=Terrain', 'body=earth&family=Terrain', 'family'],
    ['family=all', 'body=earth&family=all', 'family'],
    ['two families in one value', 'body=earth&family=terrain,radar', 'family'],
    ['two families', 'body=earth&family=terrain&family=radar', 'family'],
    ['limit 0', 'body=earth&family=terrain&limit=0', 'limit'],
    ['limit 501', 'body=earth&family=terrain&limit=501', 'limit'],
    ['a negative limit', 'body=earth&family=terrain&limit=-1', 'limit'],
    ['a fractional limit', 'body=earth&family=terrain&limit=1.5', 'limit'],
    ['a limit in exponent form', 'body=earth&family=terrain&limit=1e2', 'limit'],
    ['a limit with a leading zero', 'body=earth&family=terrain&limit=050', 'limit'],
    ['a limit with a space', 'body=earth&family=terrain&limit=%205', 'limit'],
    ['a limit in hex', 'body=earth&family=terrain&limit=0x10', 'limit'],
    ['an empty limit', 'body=earth&family=terrain&limit=', 'limit'],
    ['a limit beyond 2^53', 'body=earth&family=terrain&limit=99999999999999999999', 'limit'],
    ['two limits', 'body=earth&family=terrain&limit=5&limit=6', 'limit'],
    [
      `a cursor of ${String(CURSOR_MAX_LENGTH + 1)} characters`,
      `body=earth&family=terrain&cursor=${'c'.repeat(CURSOR_MAX_LENGTH + 1)}`,
      'cursor',
    ],
    ['a cursor with a space', 'body=earth&family=terrain&cursor=a%20b', 'cursor'],
    ['a cursor with a plus sign', 'body=earth&family=terrain&cursor=a%2Bb', 'cursor'],
    ['a cursor with a slash', 'body=earth&family=terrain&cursor=a%2Fb', 'cursor'],
    ['two cursors', 'body=earth&family=terrain&cursor=a&cursor=b', 'cursor'],
    ['an unknown parameter', 'body=earth&family=terrain&famly=model', ''],
  ];

  for (const [what, query, name] of REFUSED) {
    it(`refuse ${what} with a ${String(VALIDATION_STATUS)} problem, without reading the bundle`, async () => {
      const reader = vi.fn(mockReader);
      const { api } = quietApi(reader);
      const problem = await expectProblem(
        await api.request(matchesPath(TARGET, query)),
        VALIDATION_STATUS,
        'Bad Request',
      );
      expect(problem.detail).toContain(`query parameter ${name}`);
      expect(reader).not.toHaveBeenCalled();
    });
  }

  for (const [what, id] of [
    ['a leading dot', '.test-target-alpha'],
    ['an encoded slash', 'test-target%2Falpha'],
    ['too many characters', 'a'.repeat(TARGET_ID_MAX_LENGTH + 1)],
  ] as const) {
    it(`refuse a target id with ${what}, without reading the bundle`, async () => {
      const reader = vi.fn(mockReader);
      const { api } = quietApi(reader);
      const problem = await expectProblem(
        await api.request(matchesPath(id)),
        VALIDATION_STATUS,
        'Bad Request',
      );
      expect(problem.detail).toContain('path parameter id');
      expect(reader).not.toHaveBeenCalled();
    });
  }
});
