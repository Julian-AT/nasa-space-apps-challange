# Terraluna paper

The research paper on the encoder and the data (PRD section 22), built into `paper/main.pdf`.

## Prerequisites

- `make setup` at the repository root.
- `brew install tectonic` (tested with 0.17.0).
- Network access on the first build, so that Tectonic can fill its cache.

The build pins Tectonic's bundle `https://relay.fullyjustified.net/default_bundle_v33.tar`, the
default bundle of Tectonic 0.17.0. The scripts in `scripts/` write `generated/`; never edit it by
hand.

## Commands

- `make -C paper` runs `numbers`, `lint`, `overlap`, `test` and `pdf` in that order.
- `make -C paper check` runs ruff and mypy --strict on `scripts/`.
- `make -C paper test` runs the paper's pytest suite in `scripts/`.
- `make -C paper numbers` writes `generated/numbers.tex`.
- `make -C paper numbers-check` fails with a diff if `generated/numbers.tex` is out of date.
- `make -C paper lint` applies the prose rules of PRD 22.6 to every `.tex` file except
  `figures/styles.tex` and prints `file:line: rule: text` for each finding. Only `robust` can be
  allowed, by a `% lint-allow robust: <reason>` comment on its line.
- `make -C paper overlap` fails on any run of 12 or more consecutive words shared with an abstract
  in `reference-verification.json`.
- `make -C paper pdf` builds `main.pdf` and fails on undefined citations, references or log errors.
- `make -C paper bib` regenerates `refs.bib` for the cited keys from `reference-verification.json`;
  `make -C paper refs` validates the record and `make -C paper refs-check` fails if `refs.bib` drifted.
- `TERRALUNA_DATA=<dir> make -C paper figures` redraws the hillshades in `figures/generated/` from the
  raw DEMs under `<dir>/raw/`; the PDFs and their input digests are committed.
- `make -C paper tables` writes `generated/distance-plot.tex` from the committed comparison record.
