import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // @terraluna/schema exports its TypeScript source (packages/schema/src/index.ts).
  transpilePackages: ['@terraluna/schema'],
  poweredByHeader: false,
  reactStrictMode: true,
};

export default nextConfig;
