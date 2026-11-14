# Terraluna

**Gradient Descent · NASA Space Apps 2026**

Earth analogs for lunar and Martian sites, using terrain fingerprints, a cross-planet terrain encoder and qualified radar evidence.

## Start building

[PRD.md](PRD.md) defines the product and implementation backlog. Start with **section 12, T0** (repository and contracts) and **T1** (real-data pilot). Section 8 defines the architecture; section 23 defines the review workflow.

Build toward one real evidence card flowing through the offline pipeline, a versioned release bundle and the web app. The application, pipeline and trained model are still to be built; T0 establishes runnable setup, test commands and CI.

The PRD specifies **Python 3.12 + uv** for offline science and training, and **Node 22 + pnpm** for the web app and shared TypeScript packages.

[config/run.yaml](config/run.yaml) holds the starting configuration with kickoff decisions unset. [.env.example](.env.example) lists deployment variables. [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) provide working instructions; `.claude/agents/` contains the PRD's specialist roles.

## Build and test

You need uv, and Node 22.18 or a later 22.x with pnpm (the version is in `package.json`). Each command below has passed locally (macOS, 14 November 2026):

```
make setup              # uv sync --locked and pnpm install --frozen-lockfile; checks Python 3.12, GDAL and Node
make check              # ruff, ruff format --check and mypy; ESLint and tsc through Turborepo
make test               # pytest and Vitest
make schema             # regenerates the JSON Schema and zod in packages/schema from schema.py
make schema-check       # fails if the committed JSON Schema or zod differs from a regeneration
make mock-bundle        # regenerates the labelled mock bundle in fixtures/mock-bundle
make mock-bundle-check  # fails if fixtures/mock-bundle differs from a regeneration, then validates it
make web-build          # pulls the bundle (the mock unless BUNDLE_VERSION is set) and runs next build
make web-smoke          # builds, serves / with next start and checks the mock banner and footer
make web-dev            # Next.js dev server on the pulled bundle
actionlint .github/workflows/ci.yml
```

[CI](.github/workflows/ci.yml) runs the jobs `py` (`make setup-py check-py test-py`), `ts` (`make setup-ts check-ts test-ts`), `schema` (`make schema-check mock-bundle-check`) and `commitlint` (the pull request's commits) on pull requests and on pushes to `main`. CI has not run on GitHub yet, and the Vercel preview has not been deployed yet. [apps/web/README.md](apps/web/README.md) explains how the web app selects its bundle.

## Optional references

[Five reference projects](reference-library/README.md) provide ideas for independent implementation (D17). Their local checkouts are ignored by Git; this repository keeps a small source index. Reference research is optional and does not block the build.

Original project code uses [Apache-2.0](LICENSE); third-party material retains its own terms. Codex assisted with repository preparation and reference research. Record further AI assistance as required by the PRD.
