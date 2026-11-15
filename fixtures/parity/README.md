# Parity fixtures (PRD 8.8)

Python reference outputs that the TypeScript mirror (`@terraluna/core`, T10) checks itself against.
The files here are synthetic (`"data_kind": "synthetic"`). No real window has been added yet; that
comes in a later task.

- `terrain_synthetic.json`: six geographic source rasters stored exactly, with the sha256 of each.
  They hold a plane, a sinusoid, a seeded Weierstrass-Mandelbrot fractal (SplitMix64 seed) and
  three nodata patterns (hole, edge strip, checkerboard). For each one the file holds the D29
  resampled grid, nu and ln nu per lag and direction, the window fingerprint (relief and texture,
  H or its reason, supported lags, valid fraction) under both isotropic rules, and the run
  settings used.
- `geometry_synthetic.json`: sphere radii, the analysis and encoder window pixel centres, and
  inverse-then-forward spherical AEQD cases for the Moon, Mars and Earth. Each body has centres at
  the equator, at mid-latitude, at the antimeridian and near both poles.

Both files carry `schema_version`, `producing_commit` (the last commit to the producing modules),
`config_sha256` of `config/run.yaml`, `numpy_version`, and the PRD 8.8 tolerances with the fields
each one applies to. Every float is written as its shortest round-trip repr. Null marks an
unavailable value and is never 0.

Regenerate them after committing any change to `terrain.py`, `geometry.py` or `config.py`:

```
uv run python -m terraluna_pipeline.terrain
```

`pipeline/tests/test_terrain_parity.py` regenerates the files into a temporary directory and
requires them to be byte-identical to the committed ones.
