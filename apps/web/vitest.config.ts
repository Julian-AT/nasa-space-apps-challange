import { defineConfig } from 'vitest/config';

export default defineConfig({
  // The @/* alias of tsconfig.json, which components.json gives shadcn/ui (PRD 8.2).
  resolve: { tsconfigPaths: true },
  test: {
    // T0-05 tests in tests/, the T15 loader tests in lib/, the API route tests in server/api/ and the
    // T7 component tests next to their components.
    include: [
      'tests/**/*.test.{ts,tsx}',
      'lib/**/*.test.{ts,tsx}',
      'server/**/*.test.{ts,tsx}',
      'components/**/*.test.{ts,tsx}',
    ],
  },
});
