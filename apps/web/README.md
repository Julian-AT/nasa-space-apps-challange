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
