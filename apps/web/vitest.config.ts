import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // T0-05's tests live in tests/; the loader's T15-01 tests sit beside it as lib/bundle*.test.ts,
    // so `vitest run lib/bundle` selects them.
    include: ['tests/**/*.test.ts', 'lib/**/*.test.ts'],
  },
});
