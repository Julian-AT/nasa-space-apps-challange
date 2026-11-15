// The record routes in the OpenAPI document (T15B-02, -03, -05): generated from the route schemas,
// with the schema package's records as components and the 409 of the model family documented.
import { describe, expect, it } from 'vitest';

import { quietApi } from './test-support.ts';

interface Operation {
  readonly responses: Record<string, unknown>;
  readonly parameters?: readonly { readonly name: string }[];
}
interface Doc {
  readonly components: { readonly schemas: Record<string, unknown> };
  readonly paths: Record<string, { readonly get: Operation }>;
}

async function document(): Promise<Doc> {
  const response = await quietApi().api.request('/api/v1/openapi.json');
  return (await response.json()) as Doc;
}

describe('record routes in the OpenAPI document', () => {
  it('defines the generated records as components', async () => {
    const { components } = await document();
    for (const name of ['TargetRecord', 'WindowEntry', 'ComparisonRecord', 'Matches', 'ModelResponse', 'TargetDetail', 'WindowResponse']) {
      expect(components.schemas, name).toHaveProperty(name);
    }
  });

  it('documents 409 on matches and no 501 on any route but compare', async () => {
    const { paths } = await document();
    expect(paths['/api/v1/targets/{id}/matches']?.get.responses).toHaveProperty('409');
    for (const [path, item] of Object.entries(paths)) {
      if (path === '/api/v1/compare') continue;
      expect(item.get.responses, path).not.toHaveProperty('501');
    }
  });

  it('documents measure, limit and cursor on matches', async () => {
    const { paths } = await document();
    const names = (paths['/api/v1/targets/{id}/matches']?.get.parameters ?? []).map((p) => p.name);
    expect(names).toEqual(expect.arrayContaining(['family', 'measure', 'limit', 'cursor']));
  });
});
