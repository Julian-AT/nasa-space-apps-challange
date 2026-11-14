import { defineConfig } from 'vitest/config';

export default defineConfig({
  // The @/* alias of tsconfig.json, which components.json gives shadcn/ui (PRD 8.2).
  resolve: { tsconfigPaths: true },
  test: {
    // Component tests sit next to their component, so each story's check can name its own file.
    include: ['tests/**/*.test.{ts,tsx}', 'components/**/*.test.{ts,tsx}'],
  },
});
