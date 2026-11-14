// @ts-check
// The shared root config (PRD 8.2), plus the file Next.js generates and overwrites.
import { defineConfig } from 'eslint/config';

import root from '../../eslint.config.mjs';

export default defineConfig(root, { ignores: ['next-env.d.ts'] });
