# @terraluna/web

The Terraluna web app: Next.js 16 App Router, React 19.2 and strict TypeScript (PRD 8.2, 8.6). It renders from the release bundle, which is the only interface to the Python pipeline (PRD 8.4, D23). There is no Tailwind or UI kit yet; T7 adds styling.

## Bundle selection

| `BUNDLE_VERSION` | Source |
| --- | --- |
| unset or empty | `fixtures/mock-bundle/` (labelled `"mock": true`) |
| `<version>` | `release/<version>/`, whose manifest must have that `bundle_version` and `"mock": false` |

`bundle:pull` validates `manifest.json` and `validation.json` with `@terraluna/schema`. It also checks `validation.json` against the byte count and SHA-256 that the manifest lists, and refuses invalid UTF-8, duplicate keys and duplicate gates. Only then does it copy both files into `apps/web/data/`, which is git-ignored. A missing or invalid requested bundle is an error and never falls back to the mock.

With `VERCEL_ENV=production`, a mock bundle is refused, by `bundle:pull` and again by `next build`. `next build` reads `apps/web/data/` in the root layout and repeats every check against the current environment, so it also fails if the pulled bundle is not the one `BUNDLE_VERSION` selects. `VERCEL_ENV` must be `production`, `preview`, `development` or unset.

## Commands (from the repository root)

| Command | Does |
| --- | --- |
| `make web-build` | `bundle:pull`, then `next build` |
| `make web-smoke` | `make web-build`, then `next start` on a free port. It fetches `/` and checks that the page is prerendered, shows the mock banner exactly when the bundle is mock, the shipping-mode line and the bundle version in the footer, and nothing else. The server is always stopped. |
| `make web-dev` | `bundle:pull`, then `next dev` on the current bundle (`PORT` sets the port) |
| `make check-ts` / `make test-ts` | ESLint, `next typegen` and `tsc --noEmit`, and Vitest, through Turborepo |

For example, `make web-build BUNDLE_VERSION=2026-11-15T0900Z-3f9c2a1 VERCEL_ENV=production` builds a release bundle for production.

## What `/` shows

Every page shows the same shell, from `app/layout.tsx` and `lib/shell.ts`:

- for a mock bundle, a red banner reading "Mock bundle — placeholder data, not results";
- "Shipping mode: not chosen (G0 <status>)" while `shipping_mode` is null, with the G0 status read from `validation.json` (or "G0 not recorded" if it has no G0 record), otherwise "Shipping mode: <mode>";
- the bundle version in the footer.

`/` adds only the title. It shows no numbers, targets or claims, and `dynamic = 'error'` fails the build if it would stop being static.

## Vercel preview

The Vercel project `terraluna` has Root Directory `apps/web`, framework Next.js, Node.js 22.x and files outside the root directory included in the build; it is not connected to Git. [`vercel.json`](vercel.json) runs `pnpm install --frozen-lockfile` (the workspace, with the pnpm version from `packageManager`) and then `bundle:pull` and `next build`. [`.vercelignore`](../../.vercelignore) keeps local caches, pulled data, release artifacts and `.env` files out of the upload.

Link once from the repository root with `vercel link --project terraluna`, then deploy a preview from the repository root with `vercel deploy`. Vercel sets `VERCEL_ENV=preview`, so the mock bundle builds; never pass `--prod` while `BUNDLE_VERSION` is unset, because `bundle:pull` refuses the mock for production. Previews use Vercel Authentication, so `curl` needs `vercel curl` or a protection bypass.

## Live probe (`/probe?lat=&lon=`)

The probe runs in a Web Worker: it reads Copernicus GLO-30 through `/api/v1/dem/cop30/{tile}` (decision G0-03: proxy), measures the window with `@terraluna/core` and, when `public/probe-model/` holds an encoder export, embeds it with onnxruntime-web. The encoder is EXPERIMENTAL, labelled Predicted with the bundle's G5 status, and never ranks anything.

| Command (in `apps/web`) | Does |
| --- | --- |
| `pnpm probe:model [--from <export dir>]` | Copies `encoder.fp16.onnx`, `export_record.json` and `background_*.json` from an encoder export (default `outputs/ml/model/`) into `public/probe-model/`, after checking the SHA-256, the 10 MB budget, `mock: false`, status EXPERIMENTAL and ONNX IR version 13 or lower. Swapping in a later export is this command plus a commit of `public/probe-model/`. |
| `TERRALUNA_RAW_DIR=… node tests/probe-parity/make-windows.ts` | Cuts 256 real 64 x 64 encoder windows from the local GLO-30 tiles into `ml/checkpoints/onnx-parity/` |
| `uv run --locked python -I tests/probe-parity/expected.py <export dir> ../../ml/checkpoints/onnx-parity` | Python onnxruntime embeddings of those windows (fp32 and fp16); needs onnxruntime in the environment |
| `pnpm test:parity` | onnxruntime-web (WASM) on `public/probe-model/encoder.fp16.onnx` against them; requires per-window cosine ≥ 0.999 and writes `wasm_parity.json` |
| `pnpm probe:bench --out <file> --point lat,lon[,window_id]` | Node timings of the probe against the public bucket, with an optional comparison to pipeline fingerprints |
