// Reads wording that the app copies from PRD.md, so a test can compare each copy with the PRD.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { REPO_ROOT } from './helpers.ts';

/** PRD.md section 8.12: the GateRecord field names, in the order the PRD lists them. */
export function prd812GateFields(): string[] {
  const prd = readFileSync(join(REPO_ROOT, 'PRD.md'), 'utf8');
  const start = prd.indexOf('\n### 8.12 Records\n');
  const end = prd.indexOf('\n### ', start + 1);
  if (start < 0 || end < 0) throw new Error('PRD.md has no section 8.12');
  const line = prd
    .slice(start, end)
    .split('\n')
    .find((text) => text.startsWith('- **GateRecord:**'));
  if (line === undefined) throw new Error('PRD 8.12 lists no GateRecord');
  // Field names are the backticked words outside the parentheses that list the status values.
  return [...line.replace(/\([^)]*\)/g, '').matchAll(/`([a-z_]+)`/g)].map((match) => match[1] ?? '');
}

/** PRD.md section 4: the "Title wording" cell of the A, B and C rows of the mode table. */
export function prd4Titles(): Record<string, string> {
  const prd = readFileSync(join(REPO_ROOT, 'PRD.md'), 'utf8');
  const start = prd.indexOf('\n## 4. Shipping modes and fallbacks\n');
  const end = prd.indexOf('\n## ', start + 1);
  if (start < 0 || end < 0) throw new Error('PRD.md has no section 4');
  const rows = prd
    .slice(start, end)
    .split('\n')
    .filter((line) => line.startsWith('| '))
    .map((line) => line.split('|').slice(1, -1).map((cell) => cell.trim()));
  const header = rows.find((cells) => cells[0] === 'Mode');
  const column = header?.indexOf('Title wording') ?? -1;
  if (column < 0) throw new Error('PRD 4 mode table has no "Title wording" column');
  const titles: Record<string, string> = {};
  for (const cells of rows) {
    const mode = /^\*\*([A-Z]):/.exec(cells[0] ?? '')?.[1];
    if (mode !== undefined) titles[mode] = cells[column] ?? '';
  }
  return titles;
}
