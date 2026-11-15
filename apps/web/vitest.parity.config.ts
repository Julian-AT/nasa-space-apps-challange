import { defineConfig } from 'vitest/config';

// test:parity (T13-03): onnxruntime-web WASM against Python onnxruntime on the exported encoder.
// It needs the git-ignored ml/checkpoints/onnx-parity files, so the default `test` never runs it.
export default defineConfig({
  resolve: { tsconfigPaths: true },
  test: { include: ['tests/probe-parity/**/*.parity.ts'], testTimeout: 300_000 },
});
