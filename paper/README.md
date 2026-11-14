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

- `make -C paper numbers` writes `generated/numbers.tex`.
- `make -C paper numbers-check` fails with a diff if `generated/numbers.tex` is out of date.
- `make -C paper pdf` builds `main.pdf` and fails on undefined citations, references or log errors.
