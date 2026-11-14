import { defineConfig } from 'vitest/config';

export default defineConfig({
  // The @/* alias of tsconfig.json, which components.json gives shadcn/ui (PRD 8.2).
  resolve: { tsconfigPaths: true },
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
  },
});
