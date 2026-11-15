# Terraluna paper build (PRD 22.6): make -C paper. Recipes are POSIX sh for GNU Make 3.81.

SHELL := /bin/sh
.SUFFIXES:
.NOTPARALLEL:

TECTONIC ?= tectonic
UV ?= uv
# Release bundle under ../release/ that numbers.py reads; empty means no bundle is read.
BUNDLE_VERSION =
# The default bundle of Tectonic 0.17.0. Its cache digest was
# 6ffe055852f8faf66c0acbe1a7fb27f87b869a90bad1204f3bf4d9683f597c7c when checked on 14 November.
TECTONIC_BUNDLE ?= https://relay.fullyjustified.net/default_bundle_v33.tar

UV_RUN = $(UV) run --frozen
# -P keeps scripts/ off sys.path, so scripts/numbers.py cannot shadow the numbers module.
PYTHON = $(UV_RUN) python -P
# Modules of the scripts package that import each other run from paper/ with -m; PYTHONPATH=. finds
# the package, since -P adds neither the working directory nor scripts/ to sys.path.
PYTHON_MODULE = PYTHONPATH=. $(PYTHON) -m
NUMBERS_ARGS = $(if $(BUNDLE_VERSION),--bundle ../release/$(BUNDLE_VERSION))

.PHONY: all numbers numbers-check tables bib refs refs-check figures lint overlap test check pdf

all: numbers tables bib refs lint overlap test pdf

numbers:
	$(PYTHON) scripts/numbers.py $(NUMBERS_ARGS) --out generated/numbers.tex

# Regenerates into a temporary file and fails with the diff if generated/numbers.tex drifted.
numbers-check:
	@tmp=$$(mktemp) || exit 1; \
	trap 'rm -f "$$tmp"' EXIT; \
	$(PYTHON) scripts/numbers.py $(NUMBERS_ARGS) --out "$$tmp" >/dev/null || exit 1; \
	if diff -u generated/numbers.tex "$$tmp"; then \
		echo "numbers-check: generated/numbers.tex is current"; \
	else \
		echo "numbers-check: generated/numbers.tex drifted; run make -C paper numbers" >&2; \
		exit 1; \
	fi

# pgfplots data from the committed artifacts (generated/distance-plot.tex).
tables:
	$(PYTHON_MODULE) scripts.tables

# refs.bib from reference-verification.json, for exactly the cited keys (PRD 22.6 check 3).
bib:
	$(PYTHON_MODULE) scripts.references bib

refs:
	$(PYTHON_MODULE) scripts.references check

# Validates the record and fails with the diff if refs.bib drifted from the generator output.
refs-check: refs
	@tmp=$$(mktemp) || exit 1; \
	trap 'rm -f "$$tmp"' EXIT; \
	$(PYTHON_MODULE) scripts.references bib --out "$$tmp" >/dev/null || exit 1; \
	if diff -u refs.bib "$$tmp"; then \
		echo "refs-check: refs.bib is current"; \
	else \
		echo "refs-check: refs.bib drifted; run make -C paper bib" >&2; \
		exit 1; \
	fi

# Hillshades from the raw DEMs (outside the repository); needs TERRALUNA_DATA. Not part of all,
# because the raw data are not in the repository; the generated PDFs are committed.
figures:
	$(PYTHON_MODULE) scripts.figures

# PRD 22.6 check 2: the prose rules, on every .tex file except figures/styles.tex.
lint:
	$(PYTHON_MODULE) scripts.lint_prose

# PRD 22.6 check 4: no run of 12 or more words shared with an abstract in the reference record.
overlap:
	$(PYTHON_MODULE) scripts.overlap_check

test:
	$(UV_RUN) pytest scripts

check:
	$(UV_RUN) ruff check --config ruff.toml scripts
	$(UV_RUN) ruff format --check --config ruff.toml scripts
	$(UV_RUN) mypy --config-file mypy.ini --strict scripts

# Tectonic 0.17.0 exits 0 on undefined citations and BibTeX errors; check_log.py fails on them.
pdf:
	@command -v $(TECTONIC) >/dev/null 2>&1 || { \
		echo 'tectonic not found: install it with brew install tectonic (tested with 0.17.0), see paper/README.md' >&2; \
		exit 1; \
	}
	$(TECTONIC) --version
	rm -f main.log main.blg
	$(TECTONIC) --keep-logs --bundle $(TECTONIC_BUNDLE) main.tex
	$(PYTHON) scripts/check_log.py main.log --blg main.blg
