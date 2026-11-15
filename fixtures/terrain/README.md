# Synthetic terrain fixtures

Everything here is synthetic. No real DEM data is stored here or read by the tests that use it.

T3-06 (`pipeline/tests/test_terrain_fractal.py`) generates its inputs in code and checks in no
files: seeded fractional Brownian surfaces by spectral synthesis (H = 0.3, 0.5 and 0.8), and an
equirectangular raster on the Moon sphere holding a plane plus a sinusoid, with injected nodata
blocks. Any file added to this directory must be synthetic and must say how it was generated.
