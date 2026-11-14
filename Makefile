# Terraluna build entry point (PRD 12, Commands). Recipes are POSIX sh for GNU Make 3.81.

SHELL := /bin/sh
.SUFFIXES:

UV ?= uv
UV_RUN := $(UV) run --locked

.PHONY: setup check test setup-py check-py test-py

setup: setup-py
check: check-py
test: test-py

# uv sync on the virtual workspace root installs both members and the dev group from uv.lock.
setup-py:
	$(UV) sync --locked
	@$(UV_RUN) python -c 'import sys; v = sys.version_info; print(f"Python {v.major}.{v.minor}.{v.micro}"); sys.exit(None if v[:2] == (3, 12) else "setup-py: Python 3.12 is required")'
	@$(UV_RUN) python -c 'import rasterio; print(f"GDAL {rasterio.__gdal_version__} (rasterio {rasterio.__version__})")' \
		|| { echo "setup-py: rasterio cannot be imported, so GDAL is unavailable" >&2; exit 1; }

check-py:
	$(UV_RUN) ruff check
	$(UV_RUN) ruff format --check
	$(UV_RUN) mypy

test-py:
	$(UV_RUN) pytest
