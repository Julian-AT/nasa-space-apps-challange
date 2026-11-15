# Paper verification log (2026-11-15)

Read-only review of every file input by `paper/main.tex` plus the figure captions
(`figures/encoder-architecture.tex` and `figures/styles.tex` were not edited). No commands were
run; macros were not regenerated and the PDF was not compiled.

## What was checked

1. Numbers in prose against `paper/generated/numbers.tex` and, where no macro exists, against
   `config/run.yaml`, `outputs/search/summary_moon-cr-site01-w000.json`,
   `outputs/comparisons/moon-cr-site01-w000.json` and the abstracts in `reference-verification.json`.
2. Claims against records: acceptance outcome (physics lower bound 0.87 >= 0.8 pass; body 0.71 >
   0.60 fail; agreement 0.20 < 0.5 fail; seed Jaccard 0.32 < 0.5 fail; morphology and baselines
   `\NotAvailable`), 28,843 windows (19,393 train + 5,542 val + 3,907 test + 1 target window),
   2,795,040 parameters, terrain only, Moon best relief 0.68 > site reference max 0.44, Mars
   shortlist relief maxima (0.094, 0.289) below site reference medians (0.93, 0.39), ONNX/WASM
   parity, throughput medians (34, 1157, 1077 windows/s, 391 s), benchmark-table ranges (body
   0.62 to 0.66, trained agreement max 0.47, 40-epoch agreement 0.28 to 0.34 vs 0.39 to 0.47).
   All macro values agree with these.
3. Citations: every cited key is `verified` in `reference-verification.json`; `refs.bib` is
   generated from it. The three `unresolved` entries (neish2017, raney2011, tolometti2020) are not
   cited. Literature numbers (88 to 89 %, 0.95/0.76, 17 m to 2.7 km, 115 m to 1.8 km, 0.6 to 20 km,
   30 degrees/0.2, CPR 0.8 -> 0.7 % or 10 %, 1.01, 35 flows, 18 channels/768/70 S to 70 N, 30+
   layers, 1 m/100 m, 94 million, 60 S to 60 N) match the recorded `supports`.
4. Project/process language and overclaiming.

## Changes

- abstract.tex: Connecting Ridge described as a candidate Artemis III region "that NASA announced"
  (the source supports only the August 2022 list; no literal year added because of the
  no-digit rule in arxiv.sty); explicit per-criterion encoder outcome, with morphology and
  baselines "not available"; Mars shortlist limited to terrain statistics at hectometre lags.
- introduction.tex: same qualifier; contribution three states pass/fail per criterion and the
  unavailable probes.
- New prose adds no literal digits (arxiv.sty notes that measurement sections may hold none outside
  macros); `scripts/test_manuscript.py` should still be run.
- data.tex: hand-typed "100 error clones" -> `\ConfigClones`; "parity to floating point error"
  (no record checked) -> tolerance stated in each fixture; removed unrecorded "two hundred thousand
  windows per body".
- embedding.tex: baselines described as planned and not evaluated (results not available); table
  "Use in the browser" column corrected (encoder was exported and checked in ONNX Runtime Web).
- training.tex: morphology probe stated as not available because labels were not built.
- results.tex: per-criterion outcome sentence; "could not be run ... ingested/fetched" -> "not
  available ... not evaluated"; probes table: blank cells -> `\NotAvailable`, added the
  no-gradient-reversal physics and agreement values (`\VarLambdaZeroPhysR`, `\VarLambdaZeroRho`,
  same test record), added a Seeds column (`\EncoderSeedJaccard` vs `\GFiveJaccardMin`) and an
  outcome row; "not comparable in this mode" -> no radar distance exists, not available;
  "this pilot" -> "this study"; "Apple M4 Pro" (not in the macro records) -> laptop accelerator
  through the MPS backend; browser latency of fingerprint and encoder stated as not available
  (the 5.9 ms is Node.js WASM); held-out Mars run stated as absent from the evaluation record.
- limitations.tex: removed unrecorded "almost every 90 m cell contains a return" (the recorded
  observed fraction uses the nearest 5 m pixel only); encoder paragraph lists outcomes and the
  unavailable morphology, baseline and held-out probes; added the lunar "not a close analog" and
  terrain-statistics-only limit.
- conclusion.tex: "Moon and Earth" -> "Moon, Mars and Earth"; lunar candidate stated as not a close
  analog; encoder outcome includes seed stability and unavailable probes; body probe "near 0.65"
  -> recorded range `\ValFortyCBody` to `\ValLamZeroSBody`; browser latency not available.
- figures/structure-functions.tex: "gate G0 pilot" removed. figures/candidate-distances.tex:
  "committed" -> "recorded".

## Still uncertain (for the orchestrator)

- `numbers.tex` header says "no release bundle was read"; confirm whether macros should be
  regenerated from `release/2026-11-15T0456Z-7e3c74c/`. `\TrainDevice` contains "team rule" and
  `\ConfigShippingMode`/`\CardShippingMode` expose "Mode C"; none is used in prose, but
  `paper/scripts/numbers.py` should drop or reword them.
- Method constants typed in prose without a macro and not found in `config/run.yaml`: 10 km
  exclusion radius and 2 degree Earth blocks (data.tex); asinh scale 50 m, slope /45 degrees,
  channel widths 32 to 256, projector 2048, eleven targets, VICReg 25/25/1, 1 m view noise, 10 %
  holes (architecture.tex); bootstrap blocks of two windows (appendix-data-analysis.tex); 99th
  percentile OOD cut (appendix-embeddings.tex); 10 m hillshade grid (figure). They should be
  checked against the code or turned into macros.
- The land-cover/region sensitivity sentence in results.tex (rank unchanged under a moderate
  tightening; window leaves the set at 100 % allowed or Rosamond only) has no macro; its record,
  presumably `analysis/g4_candidates.json`, was not read in full.
- Pass/fail words in the new outcome row are derived from the macros and the task brief, not from
  a generated macro per criterion; `outputs/ml/evaluation/test/g5_conditions.json` was not opened.
- `\NotAvailable` is assumed to be defined in the class/style (it was already used); compile to
  confirm, and check that the six-column probes table fits the text width.
- Hardware wording "8 virtual CPUs" and the MPS backend come from macros; the chip model was
  removed because no record was checked.
