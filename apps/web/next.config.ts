import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // @terraluna/schema exports its TypeScript source (packages/schema/src/index.ts).
  transpilePackages: ['@terraluna/schema'],
  poweredByHeader: false,
  reactStrictMode: true,
  // next dev otherwise writes apps/web/AGENTS.md when it detects a coding agent. The repository's
  // agent instructions are the root CLAUDE.md and AGENTS.md (PRD 23).
  agentRules: false,
};

export default nextConfig;
