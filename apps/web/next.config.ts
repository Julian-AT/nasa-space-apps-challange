import type { NextConfig } from 'next';

import { securityHeaders } from './lib/headers.ts';

const nextConfig: NextConfig = {
  // @terraluna/schema exports its TypeScript source (packages/schema/src/index.ts).
  transpilePackages: ['@terraluna/schema'],
  poweredByHeader: false,
  // CSP on every route; COOP and COEP on /probe and /explore/earth only (lib/headers.ts).
  headers: () => Promise.resolve(securityHeaders(process.env)),
  reactStrictMode: true,
  // next dev otherwise writes apps/web/AGENTS.md when it detects a coding agent. The repository's
  // agent instructions are the root CLAUDE.md and AGENTS.md (PRD 23).
  agentRules: false,
  // The API route (app/api/[[...route]]/route.ts) reads the files bundle:pull writes into data/ at
  // a path built at run time (lib/bundle.ts loadBundle), so they are named here for its server
  // trace, which Vercel uses to decide what the function ships with (PRD 8.5, 8.9). Turbopack's
  // static analysis finds data/ as well, but that is a heuristic; this makes it a rule. The key
  // is a route glob (picomatch), with the brackets and dots of [[...route]] escaped, as in the
  // Next 16 docs (next.config.js output, outputFileTracingIncludes); the values are globs from
  // this project's directory. Only the API route gets them: the / page is static.
  outputFileTracingIncludes: {
    '/api/\\[\\[\\.\\.\\.route\\]\\]': ['./data/**/*.json', './data/**/*.svg'],
  },
};

export default nextConfig;
