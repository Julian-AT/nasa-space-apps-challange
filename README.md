# Terraluna

**Team Terraluna · NASA Space Apps 2026**

Terrain-fingerprint analog candidates for Connecting Ridge, Jezero and Arcadia. Radar comparison not completed (Mode C); the terrain encoder is experimental (G5 FAIL) and unranked.

Live app: https://terraluna-lovat.vercel.app. Every number in the app is read from one versioned, checksummed release bundle, and the [paper](paper/) is generated from the same pipeline outputs; the gate board on `/methods` shows every release gate, including those that failed or were not run.

## What it is

Terraluna compares terrain roughness fingerprints, the RMS height difference ν(L) at lags of 270 to 1080 m, computed from public elevation models:

- **Moon:** a 5 km window of the PGDA product 78 Site01 LOLA-derived DEM at Connecting Ridge, an Artemis candidate region.
- **Mars:** CTX DTMs at Jezero crater and Arcadia Planitia, terrain only.
- **Earth:** 5 km Copernicus GLO-30 windows inside two declared regions, Holuhraun (Iceland) and Rosamond dry lake (California), masked with ESA WorldCover.

Relief and texture distances are kept separate; there is no combined score. Every value carries an evidence label (Observed, Derived, Predicted, Assumed, Unavailable). The shipping mode is C: the radar comparison was not completed. The learned terrain encoder failed its promotion gate (G5) and is shown on `/model` only, outside every ranking. A candidate window is a window this search identified, not a proven analog.

The live probe on `/probe` measures any Earth point in the browser with the same resampler and fingerprint as the Python pipeline (parity-tested).

[PRD.md](PRD.md) is the specification; [config/run.yaml](config/run.yaml) and [docs/ralph/DECISIONS.md](docs/ralph/DECISIONS.md) record the decisions taken.

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

The real evidence cards and bundle (Mode C, terrain only) were built with these commands on 15 November 2026. `TERRALUNA_RAW_DIR` points at the raw downloads (PGDA78 Site01, Copernicus GLO-30 and WorldCover tiles), kept outside the repository:

```
TERRALUNA_RAW_DIR=<raw dir> uv run python -m terraluna_pipeline.realcard search --also-card earth-utm27n-e280-n2880
uv run python -m terraluna_pipeline.gates merge        # the only writer of outputs/validation.json
uv run python -m terraluna_pipeline.export cards       # plus --target-window/--earth-window for the G0-05 pair
uv run python -m terraluna_pipeline.figures cards      # figures A to C, then export cards again
make bundle                                            # stage, version and assemble release/<version>/ from a clean tree
uv run python -m terraluna_pipeline.export validate release/<version> --production --contracts
```

[CI](.github/workflows/ci.yml) runs the jobs `py` (`make setup-py check-py test-py`), `ts` (`make setup-ts check-ts test-ts`), `schema` (`make schema-check mock-bundle-check`) and `commitlint` (the pull request's commits) on pull requests and on pushes to `main`. On GitHub (Ubuntu, x86_64) the first run on 15 November 2026 passed `ts` and `schema`. In `py`, 6 tests of `pipeline/tests/test_terrain_parity.py` still fail by design of the test: they require the committed parity fixtures, generated on macOS arm64, to be reproduced byte for byte. On Linux x86_64, regenerating them differs only in the last bits of floats (largest absolute difference 2.3e-12 m in the AEQD cases, largest relative difference 1.4e-15 in the terrain cases; the PRD 8.8 tolerances are 1e-9). The fractal input's sha256 differs for the same reason. Those tests pass on macOS. The tolerances stay unchanged; per-platform fixtures are not built yet. [apps/web/README.md](apps/web/README.md) explains how the web app selects its bundle and how the Vercel preview is deployed.

## Credits

- **Data:** NASA PGDA product 78 (LOLA-derived Site01 DEM) and LOLA polar products; USGS/ASU CTX DTMs; Copernicus DEM GLO-30 (ESA, © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA); ESA WorldCover 10 m 2021 v200 (CC BY 4.0); NASA/JPL UAVSAR (Iceland scene Icelnd_00208_15083_004_150530_L090_CX_01); LRO Mini-RF. Full source records with licences and checksums are on `/data` and in each bundle's `sources.json`.
- **Holuhraun facies:** Voigt & Hamilton (2021) facies map (CC BY-NC), used for the G3 reproduction; not redistributed here.
- **UI design ideas:** adapted from [ShadowAstra_Atlas](https://github.com/ABRUBAB/ShadowAstra_Atlas) (MIT, used with the authors' permission); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No data or results were copied.
- **AI assistance:** Claude Code (Anthropic) and Codex (OpenAI) were used extensively for code, analysis scripts and writing, under human direction. No AI-generated images are used; all figures are drawn from data.

## Optional references

[Five reference projects](reference-library/README.md) provided ideas for independent implementation (D17). Their local checkouts are ignored by Git; this repository keeps a small source index.

Original project code uses [Apache-2.0](LICENSE); third-party material retains its own terms.
