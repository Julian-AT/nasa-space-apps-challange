# Terraluna: final build specification (team Gradient Descent)

Nov 14, 2026 · @Julian Schmidt

## 1. Summary

This tab is the single master specification for the NASA Space Apps 2026 challenge "Identify Earth Locations that Analog the Permanent Moon Base Locations and Mars". It replaces every other tab in this document. An engineer who has not seen any earlier discussion should be able to start from section 12, task T0, and build the whole product from an empty repository.

**Product promise.** Terraluna finds Earth terrain windows that match documented Moon and Mars base-candidate footprints. The primary target is the lunar south-pole Connecting Ridge footprint (PGDA product 78, Site01); two Mars targets, Jezero crater and Arcadia Planitia, use the same terrain method. Every candidate carries deterministic terrain fingerprints, a second opinion from Terraluna's own trained terrain encoder, radar-scattering evidence where it exists, a clear statement of which quantities are observed, derived, predicted or unavailable, and the field measurement that would confirm or reject it. A public web app shows all of it and lets anyone probe any spot on Earth live.

**One-line description for the submission:** Earth analogs for Moon and Mars base sites, found with physics-based terrain fingerprints and a self-trained cross-planet terrain encoder, backed by radar evidence and a live probe for any spot on Earth.

**What it is for.**

1. Interpreting lunar radar signatures, by pointing to Earth surfaces with comparable scattering and terrain where the cause can be studied on the ground.
2. Planning follow-up field characterization at those surfaces.
3. Shortlisting Earth field sites for mission rehearsal whose landscape-scale terrain (about 270 m to 1 km) resembles the target, as one input beside logistics and other properties.

**What it is not.** It is not a mechanical, trafficability or safety assessment, not an ice detector, and not a global analog map. Similar radar returns do not establish matching wheel, boot, landing-pad or regolith contact conditions.

**Status.** This is a specification, extended with desk checks (section 19) and the architecture review (D21–D33) on 14 November. The team has not yet downloaded or decoded any data, and every empirical gate is `NOT_RUN`. The shipping mode (A, B or C, section 4) is chosen at gate G0, about two hours after work starts.

### Scope at a glance

| Area | Core, must ship | Optional, only after the core passes its gates | Out of scope |
| --- | --- | --- | --- |
| Moon target | One Connecting Ridge footprint from PGDA product 78, Site01 | Other south-polar windows as context | Other lunar regions as targets |
| Mars targets | Jezero crater and Arcadia Planitia windows from USGS CTX DTMs, terrain only (D27) | Deuteronilus Mensae; HiRISE detail views | Mars radar comparison |
| Earth search | 3 to 5 candidate windows per target inside declared regions; radar-qualified regions for the Moon target | Terrain-only screening of more regions | A global analog raster |
| Radar | Observed UAVSAR full polarimetry on Earth; Mini-RF S-band and, if obtained, Chandrayaan-2 L-band on the Moon | PALSAR-2, RCM or Sentinel-1 as labelled context | Inferred radar values presented as measured |
| Machine learning | Terraluna encoder trained, evaluated and reported (section 11); no ranking depends on it | Co-ranking if G5 passes; held-out-body run | T8 radar predictor (deferred, D30); foundation-scale models |
| Software | Public Next.js app on Vercel, read-only Hono API with OpenAPI docs, live in-browser probe of any Earth spot | 3D twin viewer, interactive UMAP | Accounts, uploads, server-side Python |
| Output | Web app, evidence cards, model card and training report, written report | PDF export of cards | Chat agent, rover or thermal simulation |

### Users and success criteria

| User | What they need from Terraluna |
| --- | --- |
| Hackathon judges (scored on impact, creativity, validity, relevance and presentation) | To see quickly what was measured, what was learned, how certain it is, and why the candidates matter |
| Planetary scientists interpreting lunar radar and terrain | The observables, conventions and comparison basis behind every number |
| Field-campaign planners | Exact polygons, access questions and a concrete experiment per candidate |
| ML reviewers | Training data, objectives, held-out evaluation, baselines and failure cases for the encoder |
| Public visitors and students | A live site that works without login and says plainly what a match does and does not mean |

**The submission is done when:**

1. The shipping mode is recorded and shown in the title, map, cards and report.
2. At least one complete, correctly labelled evidence card exists from real data. The target is 3 to 5 candidate windows, or the actual count with exclusion reasons.
3. Every gate has a status in `outputs/validation.json`, and failures are visible.
4. Report, views and cards agree (G6).
5. The project page, demo and repository link are submitted before `effective_cutoff`.
6. The web app is deployed at a public URL from a real (non-mock) bundle, and the live probe works in a current desktop browser.
7. The encoder's model card and training report are published from real runs, with its G5 status shown whatever the outcome.

**Minimum viable submission**, if most things fail: a Mode C terrain pilot with one evidence card for the Moon target, the radar availability audit, the deployed app on the real bundle (live probe hidden if it fails), the encoder report with its honest G5 status, and the written report.

## 2. Decision log

| ID | Decision | Reason |
| --- | --- | --- |
| D01 | Primary target: one documented Connecting Ridge footprint from PGDA product 78, Site01 | A regional 5 m elevation model with count, error and slope-error maps, 100 error clones and ROI shapefiles exists. This does not imply NASA has selected it as the permanent base. |
| D02 | Primary use: radar interpretation and planning of follow-up field characterization | These uses follow from the observables without asserting mechanical equivalence. |
| D03 | Regional search; aim for 3 to 5 Earth candidate windows | A global raster is not needed for a complete challenge response. |
| D04 | Directly observed Earth full polarimetry is the radar baseline | Global inferred CPR must not be a critical scientific dependency. |
| D05 | Same-band, compatible-geometry radar comparison is preferred (Mode A). Cross-band radar is context only (Mode B). | Cross-band numerical closeness has no settled interpretation. |
| D06 | No ranking depends on a neural model (amended by D25: the encoder is built and reported, but ranks only through G5) | Machine learning must improve a working baseline under independent checks. |
| D07 | Radar and terrain distances stay separate; there is no universal analog score | Different experiments need different properties, and some observables will remain incomparable. |
| D08 | Mode and evidence status appear in the map, card and export | A fallback must change the stated claim as well as the code path. |
| D09 | Small raster subsets and precomputed outputs (amended by D22 and D26: the only live computation is the in-browser probe) | Data handling and scientific verification dominate the risk. |
| D10 | Every number and figure comes from versioned computed artifacts | The report, interface and validation board must agree. |
| D11 | No automatic wavelength correction of CPR from a terrain-derived Hurst exponent | The scaling expression describes slopes under an assumed surface model, not CPR, and terrain at tens of metres cannot establish behaviour at decimetres. |
| D12 | The earlier ML plan is audited, not adopted wholesale (details in section 11) | Its completion network survives only as deferred T8. Its cross-scale "Earth law / Moon law" model stays dropped. Its texture encoder is replaced by the physics-supervised, gated encoder of D25. |
| D13 | The incidence-corrected Mini-RF mosaics of Fassett et al. (2024) are lunar context only | Their δCPR and δS1 layers are empirically normalized quantities, not CPR values, so they never enter a cross-body distance. Their definitions must be read from the paper and recorded before use. |
| D14 | The schedule is relative to `working_start` and `effective_cutoff` from config | No hard-coded "hours remain" figure. |
| D15 | Wording rules in section 3 are binding for the report, interface and pitch | Several earlier phrases overstated what radar or the search can show. |

**Decisions added after the 14 November desk checks**

| ID | Decision | Reason |
| --- | --- | --- |
| D16 | Team name: Gradient Descent. Product name: Terraluna (renamed from RadarTwin, D21). | Team choice |
| D17 | No code is copied from competing teams, whatever its licence | The submission asks the team to confirm its work is original with all external resources cited; judges compare entries in the same challenge. Facts about public NASA products seen in other repos are leads to verify, then cite the NASA product. |
| D18 | SC, OC and the m-χ components are optional radar observables beside CPR | Rivera-Valentín et al. (2026) report that CPR alone is ambiguous but several polarimetric properties together resolve it |
| D19 | The open-source package polsartools (GPL-3.0) is a test-time oracle, not a runtime dependency | It independently converts UAVSAR full polarimetry to compact polarimetry and computes m-χ; keeping it out of the shipped code avoids GPL obligations unless the team licenses the repo GPL-3.0 |
| D20 | Expect ties between Earth windows and design the cards for them | At Holuhraun, published CPR distributions of different lava facies overlap by 88–89% |

**Decisions from the architecture review (14 November)**

| ID | Decision | Reason |
| --- | --- | --- |
| D21 | Product, repository and packages are named Terraluna: repo `terraluna`, `apps/web` (`@terraluna/web`), `terraluna_pipeline`, `terraluna_ml`, `@terraluna/core`, `@terraluna/schema` | Team choice; radar is now one of several evidence families |
| D22 | Python runs only offline (pipeline, training, export). Nothing in the web request path needs Python | Vercel Python functions have a 500 MB bundle limit and cold starts of seconds; free Python container hosting changed in 2026; the live maths is small enough to mirror in TypeScript |
| D23 | One versioned release bundle is the only interface between Python and the web (8.4), validated against JSON Schemas generated from the Python models | Web work starts at hour 1 on a mock bundle; extends D10 |
| D24 | Next.js 16 App Router on Vercel; public read-only API in Hono mounted at `app/api/[[...route]]` with @hono/zod-openapi and Scalar docs | One deploy and domain, typed and validated routes, OpenAPI from the same definitions, routes testable without Next |
| D25 | Headline ML: the Terraluna encoder, self-supervised, physics-supervised and body-adversarial, trained on Moon, Mars and Earth DEMs (section 11). The fingerprint stays the primary ranking | The team's USP; the closest published model (LunarFM) is Moon-only, stops at ±70° and does no Earth–Moon work |
| D26 | The live probe of any Earth spot runs in the browser: Copernicus GLO-30 COG range reads, TypeScript fingerprint, ONNX Runtime Web embedding | No server compute; same numbers as Python through parity tests (8.8) |
| D27 | Mars is in scope with two targets, Jezero crater and Arcadia Planitia, terrain only, from USGS CTX DTMs (20 m, CC0) | The challenge title names Mars; no comparable Mars polarimetric radar is used |
| D28 | One map engine for all bodies: deck.gl 9 with OrthographicView in projected metres for the Moon (polar stereographic) and Mars (local grids), MapView with a MapLibre basemap for Earth; React Three Fiber for 3D twins | MapLibre has no custom projections; avoids a Web Mercator hole at the pole |
| D29 | Windows are resampled with the same closed-form spherical azimuthal-equidistant formulas in Python and TypeScript, using fixed body radii | Exact parity up to float error; the sphere-versus-ellipsoid scale error on a 5 km Earth window is documented |
| D30 | T8, the radar cross-sensor model, is deferred and built only if every other task is done | Team choice at the review; data-limited, with access risk |
| D31 | Copernicus GLO-30 is a surface model, so Earth training and search windows are masked with ESA WorldCover to bare or sparse cover | Buildings and vegetation inflate roughness |
| D32 | The model and bundle are mirrored to Hugging Face (model and dataset repositories with a model card); the app reads its own CDN copy | Public and citable after the event; no runtime dependency on Hugging Face |
| D33 | The GPU cluster is used for breadth: parallel seeds and ablations with confidence intervals, not one large model | A credible report; a small model fits the browser |

**Decisions for the paper, the agent workflow and quantum (14 November, evening)**

| ID | Decision | Reason |
| --- | --- | --- |
| D34 | Write a research paper on the encoder and the data in the form and depth of Vaswani et al. (2017), built from the bundle with generated number macros, following the team's medlyn paper pattern (section 22) | A citable showcase in which every number is traceable |
| D35 | No AI-generated images or illustrations in the paper or the app: TikZ diagrams, pgfplots or PGF-backend plots, maps rendered from real DEMs | Team requirement; the paper must read and look like a research paper |
| D36 | Prose rules are enforced by a linter, and a text-overlap check runs against cited abstracts | Avoid model-style prose; build on prior work by citation, never by copied text |
| D37 | Claude Code agents build everything: an orchestrator plus project subagents with limited tools; deterministic checks first, then an independent verifier subagent, then a Codex CLI review with `gpt-6.1-sol` in a read-only sandbox (section 23) | Team choice; a second model family catches errors one model repeats |
| D38 | Humans approve the G0 mode, the G5 reading, production deploy, Hugging Face publish, paper release, the submission, and every account or credential step | Irreversible or account-bound steps |
| D39 | Qiskit is a stretch only (section 24), simulator first, at most one hardware block on a team member's IBM Quantum Open Plan | Team choice; no quantum advantage is expected at this scale |

## 3. Claims, wording and evidence labels

### Evidence labels

Every displayed or exported quantity carries one of these labels.

| Label | Meaning |
| --- | --- |
| Observed | Supplied by a qualified instrument product |
| Derived | Computed from named observations by a documented transformation |
| Predicted | Estimated by a fitted statistical model |
| Assumed | Scenario input, tolerance or unverified physical simplification |
| Unavailable | Observation or supported inference is absent |

The mode and these labels must be prominent enough that a screenshot cannot be mistaken for a stronger result.

### What radar similarity can and cannot show

- **Can:** two surfaces return comparable scattering signatures at a stated band and geometry, which makes the Earth surface a useful **candidate** for interpreting the lunar signature.
- **Cannot:** establish matching block sizes, height variation, soil strength, cohesion, compaction, buried structure or low-gravity mobility.
- **Why:** lunar S-band CPR is not monotonic with metre-scale rock abundance. In the highlands, CPR near 0.8 can correspond to roughly 0.7% or 10% rock abundance (Rivera-Valentín et al. 2026, R01). Viewing geometry, material properties and competing scattering processes all matter.
- **Field confirmation is always future work:** surveyed surface topography, clast-size and coverage counts, moisture and material notes, and repeat radar observation under controlled geometry.

**Expect overlap.** At Holuhraun, three mapped lava facies were statistically separable by UAVSAR CPR, yet their CPR distributions overlapped by 88–89% (Tolometti et al. 2022). Distances between Earth windows will often be small relative to their spread, so ties are a normal, honest result.

### Wording rules

| Do not say | Say instead |
| --- | --- |
| "Measured the same way on both worlds" | Name each observable, whether it is observed, derived or predicted, and the instrument configuration |
| "Which places feel like this lunar site" or "boot scale" | Which Earth surfaces show comparable scattering signatures and supported terrain descriptors, and what follow-up would test them |
| "Radar measures exactly that scale" | Radar scattering is sensitive to structure near the wavelength; inverting it to a size distribution is ambiguous |
| "Match from 10 cm to 1 km" | List the discrete observables and their valid baselines; there is no continuous measured roughness spectrum |
| "All bare ground is screened" | State the region polygons, masks, coverage and number of eligible windows |
| "No Earth surface matches" | No qualifying candidate was found among the evaluated windows under the stated observables, filters and uncertainty |
| "One number carries most of the signal" | CPR is kept together with SC, OC and total power where the products support them |
| "Nobody else uses radar" | Omit. ShadowAstra Atlas now reports Mini-RF CPR statistics near its lunar targets. Describe what this project adds: observed Earth full polarimetry compared under declared conventions. |
| "The Resources tab is mostly radar" | The listed resources include relevant radar missions and a radar tutorial |
| "Cameras see nothing in permanent shadow" | Radar does not require solar illumination |
| "NASA's selected base" | Candidate Artemis region at Connecting Ridge |
| "Discovered a new analog" | Candidate window identified by this search, until a literature check and field evidence exist |

**Wording for the encoder and the live probe.** Say "the encoder's second opinion", "learned similarity" and "Predicted". Never say "AI-verified analog", "the model found the best match" or "best place on Earth". Live-probe results always carry the line "Live estimate from public elevation data; not a site assessment."

## 4. Shipping modes and fallbacks

&#91;embedded content: shipping-mode decision · 1 pilot, 2 checks, 3 modes\]

The mode is chosen at G0 and stored as `shipping_mode` in every comparison record, card and export. It is a field, not a footnote.

| Mode | Entry conditions | Permitted outputs | Prohibited claims | Title wording |
| --- | --- | --- | --- | --- |
| **A: compatible-band radar candidates** | Usable lunar L-band polarimetry (Chandrayaan-2 DFSAR) and Earth L-band full polarimetry (UAVSAR), with documented conventions, geometry, spatial support and quality | Radar distance per observable, a separate terrain distance, the candidate shortlist, limits and a field-test proposal | Mechanical equivalence, unique roughness inversion, ice detection, mission safety certification | Radar-scattering analog candidates for Connecting Ridge |
| **B: terrain candidates with cross-band radar context** | Mini-RF S-band and Earth L-band (or C-band) data are usable; cross-band calibration is unresolved | Ranking on supported terrain; observed radar signatures shown separately with band labels; proposed radar and field validation | A quantitative cross-band radar match verdict, any Hurst-based CPR correction, matching contact conditions | Terrain analog candidates for Connecting Ridge, with radar context |
| **C: terrain pilot with radar availability audit** | Radar inputs cannot be decoded and qualified within the pilot | A bounded terrain analog study, the missing radar evidence stated explicitly, the next experiment documented | Calling an absent or unverified radar layer a completed Terraluna radar method | Terrain analog pilot for Connecting Ridge; radar comparison not completed |

**Expectation, not a result.** Mode B is the likely starting mode, because the Chandrayaan-2 data sits behind a registration with unknown turnaround. Mode A is the preferred scientific direction.

### Fallback rules

- Register on ISRO's PRADAN portal at hour 0, and never block other work on it. If DFSAR data arrives before the freeze, run its pilot and G2. Switching from B to A rebuilds every output under a new `run_id`.
- A failed G2 for one radar pair disables that comparison and shows the reason. If every radar pair fails, the run drops to Mode C.
- **Never silently promote a fallback.** A Sentinel-1 cross-polarization ranking is not measured CPR. A CPR predicted from PALSAR-2 is not an observed polarimetric reconstruction. A screenshot from a map viewer is not an analytical raster.
- A missing optional dataset triggers its stated fallback, never an invented measurement.

## 5. Data inputs and the access pilot

**Entry rule.** A dataset enters the critical path only after a real sample and its metadata have been downloaded, decoded, masked and plotted. Documentation alone does not pass. Every input below starts as `NOT_CHECKED`.

| # | Input | Role and mode | Access | Must be read or verified in the pilot |
| --- | --- | --- | --- | --- |
| 1 | Mini-RF map-projected single-swath data, `LRO-L-MRFLRO-5-CDR-MAP-V1.0` | Lunar S-band observation with identifiable geometry. Mode B; context in A. | PDS Geosciences Node or Lunar ODE; no login | Stokes and SC/OC definitions from the product label and the data product SIS (R04); transmit handedness (sources disagree, so record what the product states); per-swath incidence; resolution; quality flags; frame |
| 2 | Mini-RF polar mosaic, `LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0` | Quick pilot and context; fallback if single swaths cannot be placed in time | Same | Blends several acquisitions. The catalog documents 32 and 256 pixels per degree, about 948 m and 118 m nominal sampling (R10). Not a native 30 m raster; viewing geometry is mixed. |
| 3 | Fassett et al. 2024 south-pole δCPR and δS1 mosaics, v3, 90 m | Lunar context only (D13) | Johns Hopkins APL download page | Normalization definition from the paper; residual artifacts. Never used in a cross-body distance. |
| 4 | Chandrayaan-2 DFSAR Level-3C polar mosaics (L-band, 25 m; CPR, SERD, T-ratio, decomposition) | Lunar observable for Mode A | ISRO PRADAN; registration required | Centre frequency, polarimetric convention and handedness, incidence and processing, which layers are model-derived (R08) |
| 5 | PGDA product 78, Site01 Connecting Ridge: 5 m DEM, count map, slope, height and slope error maps, 100 clones, ROI shapefiles | Primary lunar terrain, uncertainty and target footprint. All modes. | PGDA; no login | About 90% of 5 m pixels are interpolated; use the count map to judge effective support (R11) |
| 6 | PGDA product 90: 20 m, 80–90°S, with error and effective-resolution maps | Context windows; optional | PGDA; no login | Read the effective-resolution map, not only the grid spacing (R12) |
| 7 | UAVSAR PolSAR ground-projected products: L-band 23.79 cm; HHHH, HVHV, VVVV real; HHHV, HHVV, HVVV complex; `.ann` annotation | Earth radar baseline. Modes A and B. | ASF Vertex; free Earthdata login | Linear power units; all three complex products; incidence in radians; look direction; calibration and cross-talk flag; acquisition date (R05) |
| 8 | PALSAR-2 yearly mosaic, Earth Engine `JAXA/ALOS/PALSAR/YEARLY/SAR_EPOCH` | Optional screening context and ML target | Earth Engine account | Amplitude DN conversion; local incidence in degrees; observation epoch; QA mask; pinned catalog version (R13) |
| 9 | RCM compact-polarimetry CEOS-ARD over Canada, 30 m | Optional measured C-band context | Open data registry | The actual polarimetric assets, not RGB display layers (R14) |
| 10 | Sentinel-1 global seasonal coherence and backscatter, Dec 2019 to Nov 2020 | Optional descriptive context | Open bucket; no account | Polarizations vary by region. Its coherence is temporal, not the HH-VV polarimetric correlation (R15). |
| 11 | Copernicus DEM GLO-30 | Earth terrain. All modes. | Open bucket | It is a surface model including vegetation and buildings; record version and effective support (R16) |
| 12 | ESA WorldCover (pinned year), plus snow and water masks | Eligibility | Open bucket | A bare-ground class does not establish dry bare mineral soil on the radar acquisition date |

### Earth radar scenes to start from

- **Holuhraun, Iceland, UAVSAR, May 2015.** One cited acquisition date is 30 May 2015. Use it for archive discovery and for the reproduction in gate G3. Verify the acquisition ID and footprint before coding against it.
- **Craters of the Moon:** Neish et al. used AIRSAR there, not UAVSAR. Do not assume UAVSAR covers those flows.
- **Further regions** only after an ASF search shows full-polarimetric coverage over bare ground. Several windows in one scene are not independent sites.

### Environment records

For each radar acquisition, record the date, snow or freeze status, plausible moisture state and known surface change since acquisition. A dry-acquisition preference reduces some artifacts but does not make Earth ground lunar-dry. Sampling depth is unknown at the candidate level and depends on frequency and material; do not assume a common one metre.

### Pilot procedure (task T1, about 2 hours)

1. Start the PRADAN registration and the Earthdata login.
2. Download PGDA78 Site01 (DEM, counts, errors, ROI shapefiles). Plot the DEM with ROI outlines.
3. Download one Mini-RF product covering Site01. Decode the Stokes layers, compute SC, OC and CPR with validity masks, overlay on the DEM and note visible misregistration.
4. Find UAVSAR PolSAR scenes over Holuhraun in ASF Vertex. Download one ground-projected set, decode all six products and plot HHHH, HVHV and CPR.
5. Fill a `ProductRecord` for each product, marking unknown fields as unknown.
6. Choose the mode (section 4) and record it with its evidence as gate G0 in `outputs/validation.json`.

### Download locations and formats

| Input | Where | Format and reading notes |
| --- | --- | --- |
| PGDA78 Site01 | [pgda.gsfc.nasa.gov/data/LOLA\_5mpp](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/), Site01 folder; file list in the README (A1) | 5 m GeoTIFFs in south-polar stereographic metres, MOON\_ME frame (DE421): `*_surf.tif` height, `*_ldec.tif` counts, slope and error maps, 100 clones, `*_ROIs.tgz` shapefiles. Read the CRS from the file. |
| 87–90°S 5 m mosaic (context only) | [pgda.gsfc.nasa.gov/data/LOLA\_5mpp/87S](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/87S/) | The height file is 3.3 GB and the slope file 4.9 GB. Use windowed GDAL reads; never download whole for the pilot. |
| PGDA90 | [pgda.gsfc.nasa.gov/products/90](https://pgda.gsfc.nasa.gov/products/90) | Cloud-optimized GeoTIFFs such as `LDEM_80S_20MPP_ADJ.TIF`, `LDSM_80S_20MPP_ADJ_ERR.TIF`, `LDEC_80S_20MPP_ADJ.TIF`; effective-resolution files end in `_ADJ_EFFRES.TIF` where provided |
| Mini-RF polar mosaic and map-projected swaths | PDS landing pages for `LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0` and `LRO-L-MRFLRO-5-CDR-MAP-V1.0`; Lunar ODE product search | PDS3 `.IMG` with detached labels; mosaic product types include Stokes layers and CPR. Read with GDAL's PDS driver or a PDS reader, and confirm every field against the label. |
| Fassett et al. 2024 | Johns Hopkins APL page (A3), `southpole-minirfdeltageotiffs-v3.zip` | GeoTIFF, 90 m, δS1 and δCPR. The page states no projection; read it from the file. |
| Chandrayaan-2 DFSAR | [PRADAN SAR browse](https://pradan.issdc.gov.in/ch2/protected/browse.xhtml?id=sar) (login); viewer [CH2 MapBrowse](https://chmapbrowse.issdc.gov.in/MapBrowse/) | Level-3C polar mosaics; format unknown until downloaded. The viewer is not an analytical source. |
| UAVSAR | [ASF Vertex](https://search.asf.alaska.edu/), dataset UAVSAR PolSAR (DOI 10.5067/7PEQV8SVR4DM) | Ground-projected `.grd` files: HHHH, HVHV, VVVV as 4-byte real; HHHV, HHVV, HVVV as 8-byte complex; little-endian; linear power. Grid size and spacing come from the `.ann` annotation (`grd_mag.set_rows`, `set_cols`, `row_mult`, `col_mult`, `row_addr`, `col_addr`). The open-source `uavsar_pytools` package can download and read these; verify its conventions before relying on it. |
| PALSAR-2 | Earth Engine `JAXA/ALOS/PALSAR/YEARLY/SAR_EPOCH` | Bands HH and HV (16-bit amplitude DN), angle (degrees), epoch, QA. Export regional subsets as GeoTIFF. |
| Sentinel-1 global seasonal | `s3://sentinel-1-global-coherence-earthbigdata/data/tiles/` (us-west-2, `--no-sign-request`) | 1° tiles at 3 arcseconds; seasonal backscatter and coherence |
| RCM ARD | [registry.opendata.aws/rcm-ceos-ard](https://registry.opendata.aws/rcm-ceos-ard/) | Canada only |
| Copernicus GLO-30 | `s3://copernicus-dem-30m/` (eu-central-1, no account) | 1° cloud-optimized tiles in EPSG:4326, heights relative to the EGM2008 geoid; `tileList.txt` lists tiles; a few countries' tiles are withheld |
| ESA WorldCover | `s3://esa-worldcover/` (eu-central-1, no account) | 10 m, 3° tiles in EPSG:4326; v100 (2020) and v200 (2021); pin one |

Earth windows are computed in the local UTM zone with pyproj. Lunar windows stay in the product's own polar stereographic metres.

### Mini-RF polar CPR mosaic: layout to confirm in the pilot

A competing project's working pipeline (ShadowAstra Atlas, `pipeline/s26_lend_minirf.py`) reads the south-polar CPR mosaic with these values. Treat them as leads (D17): confirm each against the product label before use, and cite the NASA product, not the other team.

| Item | Value as used by that pipeline |
| --- | --- |
| File | `lsz_xxxxx_3cp_piu_90s000_v1.img`, volume LROMRF\_0005 of the Mini-RF CDR archive at PDS Geosciences |
| Data set | `LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0` |
| Size | 10,346 × 10,346 pixels |
| Sample type | Little-endian 32-bit float, CPR values |
| Projection | South-polar stereographic, 256 pixels per degree, about 118.45 m per pixel, projection centre at sample and line 5173.5 (1-based) |
| Missing data | ISIS null values near −3.4e38; non-positive values treated as missing |
| Acquisitions | East- and west-looking passes combined, 2009 to 2011 |

Because look directions are mixed, per-pixel incidence is not recoverable from this mosaic. It is acceptable for the pilot and for Mode B context, not for incidence-binned quantitative comparison. That pipeline reads windows with HTTP range requests, so a Site01 window should be readable without downloading the whole mosaic.

### Earth regions to check for UAVSAR coverage

UAVSAR scene IDs could not be listed from the research workspace; search ASF Vertex in the first hour. Regions in priority order:

| Region | Why | What is already known | Check |
| --- | --- | --- | --- |
| Holuhraun lava field, Iceland | Fresh lava facies; the G3 reference | A published study extracted CPR from UAVSAR here (Tolometti et al. 2022); UAVSAR flew in Iceland in late May 2015 | Scene IDs, dates, snow, the facies map from that study |
| Rosamond Dry Lake, California | Smooth playa control | UAVSAR's corner-reflector calibration array is on this dry lake (Fore et al.) | Mask the reflectors; many acquisitions expected |
| Death Valley, California | Playas, salt pans, desert pavement, rough fans; decades of field roughness data | Long airborne radar history (AIRSAR) with field roughness measurements | UAVSAR full-polarimetric coverage unknown |
| Cima volcanic field, California | Young, unvegetated lava flows | Studied with C- and L-band satellite radar and a 5 cm drone DEM | UAVSAR coverage unknown |
| Craters of the Moon, Idaho | Lava textures already separated by L-band CPR | Studied with AIRSAR and L-band CPR | UAVSAR coverage unknown |

Hawaiʻi is lower priority: the 2018 and 2022 UAVSAR deployments there included Ka-band topography flights, L-band polarimetric coverage is unknown, and vegetation complicates masking.

### Mars, Earth-DEM and model-training inputs (added at the architecture review)

| Input | Product and access | Used for | Licence and citation | G0 check |
| --- | --- | --- | --- | --- |
| Mars DTMs | USGS ASP CTX DTMs, 20 m, COGs on AWS Open Data; STAC API `https://stac.astrogeology.usgs.gov/api`, collection `mro_ctx_controlled_usgs_dtms` (4,815 DTMs) | Mars targets; Mars training windows | CC0; cite DOI 10.5066/P9JKVWR3 | Items over the Jezero box (the USGS tutorial finds 80 for a 2° box) and over Arcadia; `qa_metrics.txt` present |
| Mars context | HRSC–MOLA blended DEM, 200 m global (USGS Astrogeology) | Basemap and context only; too coarse for the 270 m lag | USGS; cite Fergason, Hare and Laura (2018) | None |
| Earth DEM | Copernicus GLO-30 Public COGs, bucket `copernicus-dem-30m` (eu-central-1, no account) | Earth search windows, training, live probe | Copernicus DEM licence; attribution text on `/data` | Browser-origin Range GET returns CORS headers (sets `cop30_mode`); tiles withheld from the public release |
| Land cover | ESA WorldCover 10 m on AWS Open Data | Bare and sparse-cover mask (D31) | CC BY 4.0 | Bucket path; CORS for the live check |
| Lunar training DEM | LOLA south-polar gridded DEM, 80–90°S (PDS Geosciences or PGDA) | Moon training windows | NASA; cite | Exact file, grid, projection, nodata value |
| Crater catalogues | Robbins (2018) lunar crater database (USGS Astropedia, 93 MB); Robbins and Hynek (2012) Mars crater database | Morphology-probe labels only | Cite both | Diameter and centre fields |

**Mars target windows** (frozen at G0): Jezero, the CTX-DTM window on the crater floor nearest the Mars 2020 landing site with valid fraction ≥ 0.9; Arcadia Planitia, the CTX-DTM window inside the shallow-ice region mapped by NASA's Subsurface Water Ice Mapping project with valid fraction ≥ 0.9. Mars targets have no published error clones; their uncertainty uses CTX QA, the detrend choice and overlapping DTMs where they exist. Neither is described as a selected base.

**Declared Earth regions for the Mars targets** (terrain only, no radar needed): Atacama Desert (Chile), Devon Island and Haughton crater (Canada), Icelandic lava plains, the Hanksville area of Utah, the McMurdo Dry Valleys (Antarctica), and the Mojave and Death Valley. The live probe covers everywhere else.

## 6. Method

### 6.1 Lunar target footprint

- **Source:** the PGDA78 Site01 DEM extent and its ROI shapefiles.
- **Windows:** 5 km × 5 km in the product's own south-polar stereographic metric coordinates, tiled with a 2.5 km stride over the Site01 extent.
- **Eligibility:** at least 80% quality-qualified coverage for every mandatory observable; the exact covered area is recorded.
- **Primary target window:** the eligible window containing the centroid of the Site01 ROI union. Chosen at G0, written to config, then frozen.
- **Target reference set:** the other eligible Site01 windows. They measure how much the target varies internally.
- **Permanent shadow:** recorded as an area fraction per window, not a yes/no label.

### 6.2 Radar observables

**Mini-RF**, from Stokes parameters, following the data product SIS:

```
SC  = (S1 - S4) / 2
OC  = (S1 + S4) / 2
CPR = SC / OC
```

The factor 2 cancels in CPR but not in absolute powers. Use the definition stated in the selected product's own label.

**UAVSAR**, from the full covariance as stored in the product:

```
A = <HH HH*>   B = <VV VV*>   V = <HV HV*>
C = <HH VV*>   D = <HH HV*>   E = <HV VV*>

base_SC = (A + B - 2 Re C) / 4 + V
OC      = (A + B + 2 Re C) / 4
SC_LL   = base_SC - Im D - Im E    # LL/LR convention of Neish et al.
SC_opp  = base_SC + Im D + Im E    # opposite transmit handedness, same basis
```

- Implement the conversion as an explicit basis transformation with a declared convention. Use the expanded formulas only as a test oracle.
- While writing this spec, the fixture values in section 7 were checked against a single-scatterer calculation in which the transmit vector (1, −i)/√2 reproduces SC\_LL. That checks the spec's arithmetic only; it does not pass G1.
- No Earth result is labelled Mini-RF-equivalent until handedness and basis are verified on both sides.
- The reduced formula without D and E is allowed only where the omitted term is shown to be negligible for the evaluated data.
- **CPR is SC/OC**, not the share of total echo. That share is CPR/(1 + CPR).
- SC, OC and total power are kept beside CPR wherever the product supports them.

**Aggregation.**

- Analytical work uses linear power and complex covariance; dB is for display and summaries only.
- Aggregate powers or covariance first, then derive. **Ratio of mean powers** and **mean of pixelwise ratios** are reported as separate, named statistics. The primary radar statistic is the ratio of mean powers per common-support block.
- Common support starts at about 250 m blocks if both products' effective resolution allows. Looks and footprint are recorded, and blocks are not treated as independent samples.
- Every acquisition needs local incidence, or a look vector plus surface normals; a DEM alone does not give the beam direction. Comparisons stay within declared incidence bins.

**Use per mode.**

- **Mode A:** per-observable distances between lunar and Earth L-band distributions, after G2.
- **Mode B:** Mini-RF S-band and Earth L-band signatures shown side by side with band labels. No radar distance enters the ranking, and no wavelength correction is applied.
- **Mode C:** radar fields are `Unavailable`, with reasons.

**Additional hybrid-polarimetric observables (optional, D18).** Where all four Stokes parameters are available:

```
m          = sqrt(S2^2 + S3^2 + S4^2) / S1    # degree of polarization
sin(2 chi) = -S4 / (m * S1)                    # sign depends on transmit handedness
```

- The m-χ decomposition then splits the return into single-bounce, double-bounce and randomly polarized parts. Follow Raney et al. (2012) and record the handedness used.
- **Lunar sign check (T1):** under the SIS convention S4 = OC − SC, so ordinary lunar terrain with CPR below 1 has S4 > 0. If most Site01 pixels come out with CPR above 1, the selected product uses a different S4 sign: stop and resolve before any comparison.
- **Earth side:** synthesize the hybrid-polarimetric 2×2 covariance from the UAVSAR 3×3 covariance with a declared transmit ellipticity (χ = +45° or −45°), then compute the same Stokes parameters and m-χ. polsartools implements both steps and is the independent oracle for G1 (D19).
- **Handedness term:** for natural ground close to reflection symmetry, D and E are near zero, so the handedness-dependent term Im D + Im E is small. Report its ratio to base\_SC per window. Where it is not small (threshold set on development data), resolve handedness before any radar distance.
- These observables are context in Mode B. They enter Mode A distances only after G2.

### 6.3 Terrain observables

```
nu(L) = sqrt( mean( (z(x + L) - z(x))^2 ) )
```

- The same definition is used on both bodies, with explicit direction sampling (both grid axes and both diagonals, summarized isotropically, per-direction values kept).
- Each window is measured twice: raw (**relief**, including tilt) and after removing a best-fit plane (**texture**). Both are named; detrending must not silently erase the property being compared.
- **Analysis grid:** 90 m where both products' effective support is adequate; otherwise coarsen and record the change. Upsampling is never treated as information.
- **Lags:** at least three analysis cells and at most a quarter of the window width. At 90 m in a 5 km window: 270, 360, 540, 720 and 1080 m, keeping only locally supported lags.
- **Hurst exponent H:** fitted only with at least four supported lags spanning at least a factor of four. Otherwise, or if nu is zero at any lag, H is `Unavailable` with a reason.
- **Optional 1 km fine view:** descriptive only, unless it passes its own support checks.
- These are proposed engineering defaults, not NASA requirements. They live in one config file and change only on development data with a recorded reason.

### 6.4 Earth search universe

- **Declared regions** in config: polygons around UAVSAR full-polarimetric scenes over bare ground found in T1, plus optional terrain-only regions marked radar-unavailable.
- **Windows:** 5 km × 5 km in the local UTM zone, 2.5 km stride. Overlapping windows are not independent.
- **Masks:** water, snow and ice, vegetation and built-up areas from pinned products and years, plus radar layover and shadow.
- **Reporting:** windows generated, eligible and rejected, with reasons. Invalid or unknown windows stay distinct from poor matches.
- **Frames:** every statistic is computed in metric units within each body's own frame. Earth and lunar coordinates are never mixed.

### 6.5 Comparison and shortlist

1. **Eligibility.** `qualify_pair` decides per observable whether a pair is comparable (band, convention, geometry, calibration, support, coverage) and records a reason for every exclusion.
2. **Radar distance, Mode A only.** One-dimensional Wasserstein distance between CPR block distributions after harmonizing support and noise treatment, with SC, OC and total power kept as context.
3. **Terrain distance.** Root-mean-square difference of log nu over the shared supported lags, computed separately for relief and texture. Any normalization is fixed on development data.
4. **Shortlist.** A Pareto set over the separate distances. If a single order is needed, the plan declares which family sorts first and a deterministic tie-break.
5. **Relative signature closeness.** Each distance is shown next to the spread of distances within the target reference set. This is a reference, not proof of suitability.
6. **Ties.** Candidates whose distances differ by less than their combined uncertainty spread (6.6) are shown as tied.
7. **Background.** Each candidate is shown against a random sample of eligible windows from the same search universe. This is an empirical background comparison, not a physical truth test.

### 6.6 Uncertainty

| Category | Example | Treatment |
| --- | --- | --- |
| Within-window heterogeneity | Spread of radar or terrain values across one window | Distributions and spreads; not a confidence interval |
| Measurement error | PGDA78's 100 elevation clones | Recompute terrain descriptors on these spatially correlated realizations. If only a subset fits in time, state how many were used. |
| Statistical prediction error | Optional dual-polarization-to-CPR predictor | Independent group and target-sensor evaluation only (section 11) |
| Scenario sensitivity | Incidence bin, masks, footprint choice, band assumption | Named scenario runs, with no probability attached |

- The clones cover the published topographic error model only. They say nothing about radar calibration, sub-pixel rocks or Earth field truth.
- Rank stability is reported as a frequency, never as the probability that a site is a valid analog.
- Correlated terrain error is never imitated with independent pixel noise.

## 7. Numerical contract and test fixtures

### Decoding rules

PALSAR-2 yearly mosaic (Earth Engine product):

```
gamma0_db     = 20*log10(DN) - 83
gamma0_linear = DN**2 * 10**(-8.3)
x_power       = (DN_HV / DN_HH)**2
```

- The ratio assumes both channels share the stated calibration and pass quality and noise checks. Zero, nodata and noise-dominated values are not valid denominators.
- Amplitude DN is never averaged as if it were power.
- **Angles:** keep source units on ingest (UAVSAR radians, PALSAR degrees), then convert explicitly to one internal unit. Never infer units from magnitude.
- **Cross-sensor harmonization** (sigma-zero versus gamma-zero, effective resolution, looks, geometry, acquisition conditions) comes before any cross-sensor model test. A fitted gain or offset is estimated on development data only and checked on held-out regions.

### Validity rules

- Keep the product's validity and noise flags; any added SNR rule is written in config.
- Numerator and denominator must be finite and supported. A nonpositive or unreliable OC makes CPR unavailable.
- No epsilon is added to a near-zero OC.
- Valid high CPR is never truncated. Terrestrial CPR reaches about 2 and lunar values reach 2 to 3 (R07).
- A materially nonphysical covariance (not Hermitian positive semidefinite within tolerance) is flagged with its cause and excluded from quantitative comparison, not silently repaired.
- Stokes inputs must satisfy S1 ≥ |S4| within tolerance before conversion, and S1² ≥ S2² + S3² + S4² where all four exist.
- Three states stay distinct: missing observation, invalid measurement, valid low CPR.

### Tolerances

Unit tests on normalized float64 fixtures use `rtol = 1e-10`, `atol = 1e-12`. These are arithmetic tolerances, not instrument accuracy. Real-data tolerances come from the product, its calibration and the reference-reproduction conditions.

### Fixtures (all must pass for G1)

| # | Fixture | Inputs | Expected |
| --- | --- | --- | --- |
| F1 | Mini-RF, zero CPR | S1 = 2, S4 = 2 | SC = 0, OC = 2, CPR = 0 |
| F2 | Mini-RF, unit CPR | S1 = 2, S4 = 0 | SC = 1, OC = 1, CPR = 1 |
| F3 | Valid CPR above the old 1.5 bound | S1 = 4, S4 = −2 | SC = 3, OC = 1, CPR = 3, valid |
| F4 | Invalid Stokes pair | S1 = 1, S4 = 2 | Invalid status; no ratio published |
| F5 | Zero opposite-sense power | S1 = 2, S4 = −2 | OC = 0; ratio unavailable, not clipped or regularized |
| F6 | Full-covariance handedness | A = 1, B = 0, V = 0.04, C = 0, D = −0.2i, E = 0 | OC = 0.25; SC\_LL = 0.49; SC\_opp = 0.09 |
| F7 | Missing-correlation ambiguity | A = B = 1, V = 0.1, D = E = 0; C = 0.9 versus C = 0.1 | Same HH and HV powers; CPR = 0.1578947 versus 1.0 |
| F8 | PALSAR amplitude | DN = 100 | −43 dB; about 5.011872e−5 linear |
| F9 | PALSAR power ratio | DN\_HH = 1000, DN\_HV = 100 | x\_power = 0.01, not 0.1 |
| F10 | Angle conversion | π/3 radians | 60 degrees |

- Covariance fixtures are built from a declared reciprocal scattering vector or a positive semidefinite matrix, and that property is asserted first. F6 corresponds to HH = 1, HV = 0.2i, VV = 0 (rank one). F7's two matrices have eigenvalues (0.1, 0.1, 1.9) and (0.1, 0.9, 1.1).
- The expanded formula is tested against a separately implemented basis transformation, never against a copy of itself.
- **F7 is why two-channel Earth data cannot determine CPR alone.** Under the reduced model CPR = (1 − ρ + 2x) / (1 + ρ), the same x = 0.1 gives 0.158 when ρ = 0.9 and 1.0 when ρ = 0.1. Here ρ is the real co-polarized correlation used in the derivation, with equal HH and VV powers and negligible co/cross correlations assumed.

### Literature reproduction target (gate G3)

Neish et al. (2017) report, for their Holuhraun region and processing, about **0.47 as ratio of mean powers** and **0.51 as mean pixelwise CPR**, at incidence near 50–53° (R06). These are targets only when acquisition, convention, region and aggregation match. Archive the chosen polygon and the comparison tolerance before looking at the result. If the published polygon cannot be reconstructed, say so; do not claim exact replication from a different window.

### Window resampler (D29)

Both `terraluna_pipeline.geometry` and `@terraluna/core` implement exactly this, in float64.

- **Radii (spheres):** Moon 1,737,400 m; Mars 3,396,190 m; Earth 6,371,008.8 m (mean radius). The Earth sphere approximation changes scale by under 0.5% on a 5 km window; the card says so.
- **Grid:** for window width W and spacing Δ, pixel centres sit at x\_i = (i + 0.5)·Δ − W/2 and y\_j = W/2 − (j + 0.5)·Δ (row 0 at the top).
- **Inverse spherical AEQD** about the window centre (φ0, λ0): ρ = √(x² + y²), c = ρ / R; φ = asin(cos c · sin φ0 + y · sin c · cos φ0 / ρ); λ = λ0 + atan2(x · sin c, ρ · cos φ0 · cos c − y · sin φ0 · sin c). At ρ = 0, (φ, λ) = (φ0, λ0).
- **Source lookup:** (φ, λ) is mapped into the source raster's pixel space by one of three closed forms: geographic latitude–longitude (Copernicus, WorldCover), polar stereographic on the sphere (LOLA, Mini-RF, PGDA), or equirectangular on the sphere (CTX). The live probe needs only the geographic form; the other two are Python-only and unit-tested.
- **Interpolation:** bilinear on the four neighbouring source pixels; the output pixel is nodata if any neighbour is nodata. No other resampling (no cubic, no averaging) is used anywhere, so parity holds to float error.
- **Tests:** round trip against the forward AEQD formula; distances from the centre equal ρ to 1e-9 relative; a synthetic plane stays a plane; nodata propagates as specified.

## 8. Architecture, repository, interfaces and records

### 8.1 System overview

Terraluna has three workloads, and each runs where it fits best. **Rule: no Python in the web request path** (D22).

| Workload | What it does | Where it runs |
| --- | --- | --- |
| Heavy science (offline) | Ingest DEM and radar products, polarimetry, fingerprints for all windows, comparisons, uncertainty, tiles | Python (`terraluna_pipeline`) on team machines and the GPU cluster |
| Model training (offline) | Build window datasets, train and evaluate the encoder, export ONNX, write report metrics | Python (`terraluna_ml`) on the 8-GPU cluster |
| Serving results | Maps, rankings, cards, model report, public API | Next.js 16 on Vercel, reading one versioned release bundle (D23) |
| Live interaction | Probe any Earth spot, compare any two windows | Visitor's browser: `@terraluna/core` and ONNX Runtime Web in a Web Worker (D26) |

```
GPU cluster and laptops (Python, offline)
  pipeline ──► fingerprints, comparisons, cards, tiles ─┐
  ml ───────► encoder.onnx, embeddings, metrics, curves ─┼─► release bundle (versioned, checksummed)
                                                          │
                              object storage + CDN ◄──────┘──► HF Hub mirror (model + dataset)
                                       │
Visitor's browser ◄── Next.js 16 on Vercel: pages (RSC) + Hono /api/v1 + /api/docs
  deck.gl maps · R3F 3D twins · Web Worker (TS core + ONNX Runtime Web)
  └─ live probe: Copernicus GLO-30 COG range reads (direct, or via /api/v1/dem proxy)
```

### 8.2 Stack

Pin exact versions in `uv.lock` and `pnpm-lock.yaml` at hour 0. No major upgrades during the event; Next.js stays on the newest 16.x security patch.

| Layer | Choice | Why |
| --- | --- | --- |
| Python runtime | Python 3.12, uv workspace (`pipeline/`, `ml/`), ruff, mypy, pytest with hypothesis | One lockfile, fast installs on the cluster |
| Geodata | NumPy, SciPy, rasterio/GDAL, pyproj (product reprojection only), pandas, pyarrow/GeoParquet, pystac-client, pydantic v2 | Standard, COG-native; pydantic models generate the JSON Schemas |
| ML | PyTorch 2.x (bf16), torchvision ResNet, scikit-learn probes, umap-learn, onnx, onnxruntime; W&B optional for internal tracking only | Small model, many runs; report does not depend on W&B |
| Web runtime | Node 22 LTS, pnpm workspaces, Turborepo | Next.js 16 needs Node ≥ 20.9 |
| Web app | Next.js 16 App Router, React 19.2, TypeScript strict, Tailwind CSS v4, shadcn/ui (Radix) | Server components for static pages, accessible primitives |
| Maps and 3D | deck.gl 9 (OrthographicView for Moon and Mars, MapView with MapLibre basemap for Earth), React Three Fiber + drei | One engine for all bodies; no Web Mercator pole hole (D28) |
| Charts and maths | Observable Plot, KaTeX, MDX | Log–log curves and equations in the methods page |
| Live compute | `@terraluna/core`, geotiff.js, onnxruntime-web (WASM default, WebGPU when available), Comlink | Same numbers as Python; UI thread never blocks |
| API | Hono 4, `hono/vercel`, @hono/zod-openapi, zod 4, @scalar/hono-api-reference | Typed, validated routes and OpenAPI 3.1 docs from one definition |
| URL state | nuqs | Every view is a shareable link |
| Tests | pytest; Vitest (unit and parity); Playwright with @axe-core/playwright | Same fixtures drive both languages |
| Hosting | Vercel (preview per PR, production from `main`); object storage with CORS and Range for large assets (Cloudflare R2 preferred, Vercel Blob fallback); Hugging Face Hub mirror (D32) | Free tiers suffice; static assets need no server |

### 8.3 Repository layout

```
terraluna/
├─ LICENSE                       # Apache-2.0 (OSI-approved), added at hour 0
├─ README.md                     # quickstart, AI-use disclosure, citations
├─ CLAUDE.md, AGENTS.md          # agent instructions for Claude Code and Codex (section 23)
├─ .claude/agents/*.md           # project subagents (23.2)
├─ Makefile                      # drives both toolchains (section 12, Commands)
├─ config/run.yaml               # 8.13
├─ pyproject.toml, uv.lock       # uv workspace: pipeline/, ml/
├─ package.json, pnpm-workspace.yaml, turbo.json
├─ pipeline/src/terraluna_pipeline/
│   ├─ io.py            # ProductRecords; COG, PDS and STAC readers; checksums
│   ├─ geometry.py      # body radii; AEQD window resampler (D29, section 7)
│   ├─ polarimetry.py   # SC, OC, CPR, m-chi (section 6)
│   ├─ terrain.py       # nu(L), H, detrending, support masks
│   ├─ quality.py       # validity; WorldCover and CTX QA filters
│   ├─ fingerprints.py  # FingerprintRecord builders
│   ├─ matching.py      # distances, Pareto shortlist, background sample
│   ├─ uncertainty.py   # clones and scenarios
│   ├─ tiles.py         # hillshade and overlay tile pyramids
│   ├─ export.py        # cards, bundle writer, parity fixtures
│   └─ schema.py        # pydantic models → JSON Schema
├─ ml/src/terraluna_ml/
│   ├─ dataset.py       # window sampling and shards (11.2)
│   ├─ model.py         # encoder, heads, gradient reversal (11.3)
│   ├─ train.py         # one run = one config + one seed
│   ├─ evaluate.py      # probes, retrieval, agreement, baselines (11.6)
│   ├─ export_onnx.py   # ONNX export and parity
│   └─ report.py        # metrics.json, curves, UMAP, model card (11.9)
├─ ml/sweeps/*.yaml     # round 1 and round 2 run definitions
├─ analysis/            # Appendix A analyses (22.3), JSON outputs
├─ quantum/             # section 24 stretch, if started
├─ paper/               # LaTeX paper (22.6)
├─ reviews/             # verifier and Codex review files (23.5)
├─ logs/agents/         # agent run logs (23.7)
├─ packages/schema/     # @terraluna/schema: generated JSON Schema + zod
├─ packages/core/       # @terraluna/core: TS mirror of the numerical contract
├─ apps/web/            # @terraluna/web: Next.js app
│   ├─ app/             # routes (8.6); app/api/[[...route]]/route.ts
│   ├─ server/api/      # Hono app, routes, OpenAPI
│   ├─ workers/probe.worker.ts
│   └─ components/      # maps, cards, charts, twins
├─ fixtures/            # F1–F10, parity/*.json, mock-bundle/
├─ data/{raw,interim,processed}/  # git-ignored
└─ release/<bundle_version>/      # git-ignored; uploaded by make publish
```

Build a vertical slice first: one real card through the bundle into the deployed app. The layout does not require every module before the first result.

### 8.4 Release bundle contract

The bundle is the only interface between Python and the web (D23). It is immutable once published; any change gets a new `bundle_version`.

| Path | Format | Producer | Consumer | Budget |
| --- | --- | --- | --- | --- |
| `manifest.json` | JSON | `export.py` | web build, API, CI | < 50 kB |
| `sources.json` | ProductRecords | `io.py` | `/data`, API | < 200 kB |
| `targets.json` | target windows | `export.py` | everything | < 50 kB |
| `windows/{body}.parquet`, `windows/{body}.json` | GeoParquet; JSON top-N subset | `fingerprints.py` | analysis; API | JSON ≤ 5 MB per body |
| `comparisons/{target_id}.json` | ComparisonRecords, top 500 | `matching.py` | API, cards | ≤ 2 MB each |
| `cards/{card_id}.json`, `figures/*.svg` | JSON, SVG | `export.py` | `/site` pages | — |
| `validation.json` | GateRecords | every gate | `/methods` board | — |
| `tiles/{layer}/{z}/{x}/{y}.webp` | 512 px WebP | `tiles.py` | deck.gl TileLayer | ≤ 300 MB total |
| `model/encoder.onnx`, `model/encoder.fp16.onnx`, `model/model_card.json` | ONNX, JSON | `export_onnx.py` | probe worker, `/model` | fp16 ≤ 10 MB |
| `model/embeddings.f16.bin`, `model/embeddings.index.json` | binary, JSON | `evaluate.py` | percentiles, UMAP | ≤ 20 MB |
| `report/metrics.json`, `curves.json`, `umap.bin`, `examples.json` | JSON, binary | `report.py` | `/model` | ≤ 15 MB |
| `reference/background_{target_id}.json` | distance histograms | `matching.py` | live-probe percentiles | < 1 MB |

```json
{
  "bundle_version": "2026-11-15T0900Z-3f9c2a1",
  "schema_version": "1.0.0",
  "mock": false,
  "run_id": "sha256:…",
  "git_sha": "3f9c2a1",
  "created_utc": "2026-11-15T09:00:00Z",
  "shipping_mode": "B",
  "targets": ["moon-cr-site01-w000", "mars-jezero-w000", "mars-arcadia-w000"],
  "model": {"id": "terraluna-enc-v1", "status": "EXPERIMENTAL", "g5": "NOT_RUN"},
  "files": [{"path": "comparisons/moon-cr-site01-w000.json", "sha256": "…", "bytes": 412331}]
}
```

Rules: every file is listed with its SHA-256; CI rejects a bundle with a schema error, an unlisted file or a blown budget. The production build pins `BUNDLE_VERSION`. The mock bundle in `fixtures/mock-bundle/` uses the same schemas with `"mock": true`; the UI shows a red banner for it and CI refuses it on production.

### 8.5 Public API (Hono, read-only)

Base path `/api/v1`. The Hono app lives in `apps/web/server/api/` and is mounted by `app/api/[[...route]]/route.ts` through `handle(app)` from `hono/vercel`. Route tests call `app.request()` without starting Next. Known gotcha: Next 15+ route types reject the exported `handle(app)` signature at `tsc` time (hono issue 4877). It works at runtime; wrap it in a typed `(req: Request) => app.fetch(req)`.

| Endpoint | Returns | Cache |
| --- | --- | --- |
| `GET /health` | status, `bundle_version`, `schema_version` | no-store |
| `GET /manifest` | `manifest.json` | per deploy |
| `GET /targets` | target list with body, footprint and shipping mode | per bundle |
| `GET /targets/{id}` | target windows, fingerprints, radar context | per bundle |
| `GET /targets/{id}/matches?body=earth&family=terrain\|radar\|model&limit=50&cursor=` | ranked ComparisonRecords; `family=model` exists only when a model is in the bundle, labelled Predicted | per bundle |
| `GET /windows/{id}` | WindowRecord, fingerprint, card link | per bundle |
| `GET /compare?a=&b=` | ComparisonRecord; computed with `@terraluna/core` from stored fingerprints if not precomputed, labelled Derived (on request) | per bundle |
| `GET /model` | model card, summary metrics, G5 status | per bundle |
| `GET /gates` | `validation.json` | per bundle |
| `GET /dem/cop30/{tile}` | byte-range proxy to the public Copernicus GLO-30 COG, only if direct CORS fails at G0 | 1 day |
| `GET /openapi.json`, `GET /docs` | OpenAPI 3.1 document; Scalar reference | per deploy |

Rules: request and response schemas come from `@terraluna/schema`; errors use RFC 9457 problem+json; there are no write endpoints. The DEM proxy accepts only tile names matching `^Copernicus_DSM_COG_10_[NS]\d{2}_00_[EW]\d{3}_00_DEM$`, forwards only Range requests of at most 4 MB, and sets cache headers. Small JSON (manifest, targets, comparisons, top-N windows) is copied into `apps/web/data/` by `pnpm bundle:pull` at build time and read server-side. Large assets are fetched by the browser from `NEXT_PUBLIC_ASSET_BASE/<bundle_version>/`.

### 8.6 Web routes

| Route | Rendering | Content |
| --- | --- | --- |
| `/` | static | Hero: 3D twin of the Moon target and its best Earth match (poster image first); three-line explanation; entry points |
| `/explore/moon` | client map | Polar-stereographic view: LOLA hillshade, permanent shadow, Mini-RF CPR, 5 km window grid, target |
| `/explore/mars` | client map | Jezero and Arcadia local views from CTX DTM hillshade, window grid, targets |
| `/explore/earth` | client map | Declared regions, candidate windows coloured by rank per distance family, live-probe entry |
| `/probe?lat=&lon=` | client | Live probe result card (8.7) |
| `/site/[cardId]` | static (`generateStaticParams`), OG image via `next/og` | Evidence card (section 10) |
| `/compare/[a]/[b]` | client | Side-by-side 3D twin, nu(L) curves, distances |
| `/model` | static | Model card and training report (11.9) |
| `/methods` | static MDX with KaTeX | Numerical contract, fixtures, gates board |
| `/data` | static | Sources, licences, checksums, attribution |
| `/api/docs` | Scalar | API reference |

Also: `/paper` (static) serves `paper/main.pdf` with its abstract and BibTeX entry.

On every page: the shipping-mode banner, the bundle version in the footer, evidence labels on every number, view state in the URL, keyboard-operable map controls.

### 8.7 Live probe flow

1. On `/explore/earth` the visitor clicks or searches a place; a 5 km × 5 km window appears there and can be dragged.
2. The probe worker finds the 1° × 1° Copernicus GLO-30 tiles touching the window and reads only the needed internal COG tiles by range request with geotiff.js: straight from the public bucket if it sends CORS headers (checked at G0), otherwise through `/api/v1/dem/cop30`.
3. `@terraluna/core` resamples with the D29 resampler: the 90 m analysis grid for fingerprints, and 64 × 64 pixels at 78.125 m for the encoder.
4. Quality: valid fraction at least 0.8, or the probe stops and says why. A notice explains that Copernicus is a surface model (vegetation and buildings); a WorldCover check runs if those COGs are also CORS-readable.
5. Fingerprint: nu(L) relief and texture at the five lags, H where supported, and distances to each target under the Python rules.
6. Embedding: an onnxruntime-web session (model fetched once, then cached) gives cosine distances to each target embedding and percentiles against `reference/background_{target_id}.json`.
7. Result card: curves over the target's, distances, percentiles, labels "Derived (live, in browser)" and "Predicted (encoder v1, G5 status)", radar "Unavailable for live probes", and a shareable URL.

Budgets: first probe ≤ 6 s on a mid-range laptop at 50 Mbit/s (DEM reads ≤ 4 MB, model ≤ 10 MB), repeat probe ≤ 1.5 s.

### 8.8 Python–TypeScript parity

- One numerical contract (section 7), two implementations: `terraluna_pipeline` is the reference and `@terraluna/core` the mirror for live use. Both work in float64.
- `make parity` writes `fixtures/parity/*.json`: synthetic inputs (plane, sinusoid, fractal, nodata patterns) and 20 real windows per body, with the Python outputs.
- Vitest asserts: resampled grids within 1e-9 relative; log nu, H and distances within 1e-9 absolute; identical validity reasons.
- ONNX: PyTorch against onnxruntime (Python, fp32) within 1e-4 maximum absolute difference; the fp16 web model reaches cosine ≥ 0.999 against PyTorch on 256 fixture windows, run in Vitest with onnxruntime-web on WASM.
- Any parity failure blocks the merge.

### 8.9 CI/CD and deployment

| Job | Runs |
| --- | --- |
| `py` | ruff, mypy, pytest including F1–F10 |
| `ts` | lint, `tsc --noEmit`, Vitest including parity |
| `schema` | regenerates JSON Schema and zod; fails on any diff |
| `bundle` | validates a bundle: schemas, checksums, budgets, no mock on production |
| `onnx` | export and parity on CPU |
| `e2e` | Playwright and axe against the Vercel preview URL |

Environments: a preview deployment per pull request (mock or latest real bundle); production from `main`, pinned to `BUNDLE_VERSION`. Environment variables: `BUNDLE_VERSION`, `NEXT_PUBLIC_ASSET_BASE`, `NEXT_PUBLIC_COP30_MODE` (`direct` or `proxy`). No runtime secrets exist.

Release runbook: `make bundle` → `make validate` → `make publish` (object storage and HF mirror) → pull request bumping `BUNDLE_VERSION` → check the preview → merge → smoke test (`/api/v1/health`, one card, one probe) → paste links into the submission. Rollback: Vercel instant rollback, or revert `BUNDLE_VERSION`.

### 8.10 Performance, accessibility and security budgets

- Landing page LCP ≤ 2.5 s; initial JavaScript ≤ 250 kB gzip; deck.gl, three.js and ONNX Runtime load only on the pages that use them.
- WCAG 2.2 AA for non-map content; zero serious axe violations; colour-blind-safe scales (viridis, cividis) with numeric legends.
- Read-only site: no accounts, cookies or stored user data. Strict CSP allowing only self, the asset base and the Copernicus bucket. Dependency audit in CI. No credentials or signed URLs in any bundle.

### 8.11 Pipeline interfaces (Python)

| Function | Input | Output and invariant |
| --- | --- | --- |
| `read_product` | Product ID, local files, metadata | Arrays plus an explicit `ProductRecord`; no guessed units |
| `derive_circular` | Real or complex covariance, or Stokes; declared convention; validity mask | SC, OC, CPR in defined linear units, plus an invalid-reason mask |
| `qualify_pair` | Two product records and the requested observable | Comparable flag and machine-readable exclusion reasons |
| `terrain_fingerprint` | Metric DEM subset, effective-support and quality masks, valid lags, detrend policy | nu values, supported lag list, H or an unavailable reason |
| `radar_fingerprint` | Quality-qualified SC, OC, CPR or a directly supplied observable; common support | Distribution summaries plus support and aggregation metadata |
| `compare_windows` | Two fingerprints and pair eligibility | Separate radar and terrain distances; never a fabricated score for a missing observable |
| `summarize_uncertainty` | Published clones or declared sensitivity scenarios | Named uncertainty components and rank or distance spread |
| `export_card` | Window, comparison, sources, limits, gate states | A card carrying exactly the numbers in the app and report |

### 8.12 Records

- **ProductRecord:** `product_id`, `provider`, `version`, `source_url`, `local_path`, `checksum`, `body`, `acquisition_time`, `frequency_or_wavelength`, `transmit_receive_convention`, `units`, `calibration_type`, `native_grid`, `effective_resolution`, `crs_and_frame`, `incidence_source`, `qa_definition`, `license_or_attribution`, `verification_status`
- **WindowRecord:** `window_id`, `body`, `polygon`, `area_m2`, `analysis_grid_m`, `product_ids`, `valid_fraction_by_observable`, `observed_interpolated_quality`, `environment_flags`, `evidence_status_by_field`
- **ComparisonRecord:** `target_window_id`, `earth_window_id`, `shipping_mode`, `comparable_observables`, `excluded_observables_and_reasons`, `distances_by_family`, `uncertainty_components`, `sort_rule`, `claim_limits`, `source_ids`, `run_id`
- **GateRecord:** `gate_id`, `status` (`NOT_RUN`, `PASS`, `FAIL`, `PARTIAL`, `NOT_APPLICABLE`), `evidence_path`, `run_id`, `scope`, `result`, `consequence`, `command`, `config_version`, `input_hashes`
- **EmbeddingRecord:** `window_id`, `body`, `model_id`, `embedding_f16` (256 values), `input_hash`, `valid_fraction`, `ood_score`
- **ModelRecord** (`model_card.json`): `model_id`, `version`, `architecture`, `parameter_count`, `training_data` (bodies, product IDs, window counts, split groups), `objectives_and_weights`, `seeds`, `metrics_with_ci`, `g5_status`, `intended_use`, `limitations`, `licence`, `onnx_sha256`
- **BundleManifest:** the fields shown in 8.4
- **ProbeResult** (browser only, never stored): `window_geometry`, `dem_tiles`, `valid_fraction`, `fingerprint`, `distances_by_target`, `embedding_distance_by_target`, `percentiles`, `labels`, `warnings`

Rules: fields are explicitly nullable; missing values are never zero; an untested gate is never a pass; credentials and signed download URLs never appear in published manifests.

### 8.13 Config skeleton

```yaml
run:
  working_start: null            # set at kickoff
  assigned_event_timezone: null  # from the team's event page, not the laptop clock
  global_submission_cutoff: null
  local_judging_cutoff: null
  effective_cutoff: null         # the earlier of the two
  shipping_mode: null            # A | B | C, set at G0
targets:
  moon:
    product: PGDA78/Site01
    primary_window_id: null      # frozen at G0
  mars:
    - {name: jezero, collection: mro_ctx_controlled_usgs_dtms, bbox: [76.69, 17.41, 78.69, 19.41], primary_window_id: null}
    - {name: arcadia, collection: mro_ctx_controlled_usgs_dtms, bbox: null, primary_window_id: null}  # bbox chosen at G0 from CTX coverage
  window_m: 5000
  stride_m: 2500
terrain:
  analysis_grid_m: 90
  lags_m: [270, 360, 540, 720, 1080]
  min_lags_for_H: 4
  min_lag_span_factor: 4
  detrend: [none, plane]
  min_valid_fraction: 0.8
  resampler: aeqd_sphere          # D29; radii in geometry.py
radar:
  common_support_m: 250
  incidence_bins_deg: null       # set on development data
  snr_rule: null
  convention: {moon: null, earth: null}
earth_search:
  regions: []                    # polygons with source scene IDs
  window_m: 5000
  stride_m: 2500
  masks: {worldcover_allowed: [bare_sparse, snow_ice, grassland, shrubland], min_allowed_fraction: 0.9, water: null}
uncertainty:
  clones_used: 100
  background_sample_size: null
ml:                              # section 11
  input_px: 64
  input_m_per_px: 78.125
  embedding_dim: 256
  bodies: [moon, mars, earth]
  lambda_adv: [0.0, 0.1, 0.3, 1.0]
  alpha_phys: 1.0
  seeds: [0, 1, 2]
  g5: {phys_r2_min: 0.8, morph_gain_min: 0.10, body_acc_max: 0.60, agreement_rho_min: 0.5, seed_jaccard20_min: 0.5}
web:
  bundle_version: null
  asset_base: null
  cop30_mode: null               # direct | proxy, set at G0
```

`run_id` is a hash of the config and all product checksums. Any change to a polygon, setting or input invalidates cached results.

## 9. Validation and release gates

Every gate starts as `NOT_RUN`. When a gate is executed, its evidence file, command and config version, and result go into `outputs/validation.json`. Writing or reviewing this plan does not execute any gate.

| Gate | Status | Budget from start | Evidence required | Failure action |
| --- | --- | --- | --- | --- |
| **G0** data pilot | `NOT_RUN` | 2 h | One real lunar ROI and one Earth radar ROI decoded with metadata, masks, units and a preview; actual coverage known; one Mars CTX DTM read; one Copernicus COG read from a browser origin (CORS result recorded); the mock bundle rendering on a preview deployment | Choose Mode B or C at once; stop waiting for optional access |
| **G1** conversion correctness | `NOT_RUN` | 4 h | Fixtures F1–F10 pass against an independent basis transformation; conventions labelled; invalid-data behaviour tested | No quantitative CPR output until fixed; terrain work continues |
| **G2** comparison eligibility | `NOT_RUN` | 6 h | Product IDs, band, convention, geometry, spatial support, noise and QA, and calibration documented for each evaluated pair | Disable the unsupported radar comparison and show the reason |
| **G3** literature and measurement check | `NOT_RUN` | 12 h | Holuhraun reproduction where feasible (section 7); discrepancies explained; processing saved | Label it partial or failed and narrow claims; never tune to force agreement |
| **G4** shortlist and uncertainty | `NOT_RUN` | 18 h | Candidate table, masks, per-family distances, rank sensitivity, at least one inspected counterexample | Simplify to a defensible pilot; remove unresolved rankings |
| **G5** encoder promotion | `NOT_RUN` | After the training freeze (about 20 h) | The six conditions of 11.7 on test blocks with intervals; ONNX parity (8.8). T8, if ever run, keeps its own rule in 11.11 | The encoder stays EXPERIMENTAL with the failed tests named; its report and probe output still ship; rankings stay on M0 |
| **G6** release consistency | `NOT_RUN` | Before the freeze | App, API, exports and report agree; provenance and failed gates visible; production runs the real bundle and passes the smoke test; links work; claims match the mode | Fix packaging and claims before submitting |

Budgets shrink to fit the actual cutoff. The final hours are never spent chasing an optional gate.

### What is held out

- Geographic regions and connected acquisitions stay together. Overlapping windows are not independent sites.
- Preprocessing choices, calibration and any model are fitted on development data only.
- Prediction intervals are calibrated on separate groups.
- Final test groups stay untouched until all choices are frozen.
- A lunar holdout is used only to test generalization across lunar regions. It is not a test of an Earth-only CPR predictor.
- With too few independent groups, the result is reported as a pilot. Thousands of correlated pixels are not independent validation.

### Controls and inference limits

- Controls have documented relevant measurements, and plausible lookalikes are added as stress tests. A class name (playa, dune, lava) is not the measurement, and no class is fixed as universally low or high CPR.
- The Neish sites are a bounded reproduction exercise, not a required outcome for Connecting Ridge.
- Known analog lists are incomplete positives. Other Earth windows are not proven negatives.
- The strongest negative statement allowed is: no qualifying candidate among the evaluated windows under the stated observables, filters and uncertainty. The search mask and excluded areas are always shown.

### Further literature checks for G3

- **Holuhraun facies overlap:** Tolometti et al. (2022) found the three mapped facies separable by UAVSAR CPR but with 88–89% distribution overlap (Sentinel-1 VH/VV: 64–67%). If our facies-level CPR distributions are far better separated, suspect a processing error.
- **Lunar shadow behaviour:** after topographic correction, Fassett et al. (2024) found higher median radar reflectivity but similar polarization behaviour in permanently shadowed areas. Compare shadowed and sunlit Site01 windows qualitatively.
- **Context ranges only:** fresh lunar impact melts typically have CPR between 0.7 and 1.0, even in ponded areas (Carter et al. 2012). This is a context range, not a pass mark.

### What must be reported

| Evidence type | Required reporting |
| --- | --- |
| Measurement | Units, supported baselines, native, effective and analysis resolution, acquisition mode, masks, validity counts |
| Radar reconstruction | Convention, linear-power handling, denominator and SNR exclusions, comparison against reference calculations |
| Retrieval | Candidate area and count, rank and distance definitions, coverage, per-observable behaviour, inspected examples |
| Uncertainty | Heterogeneity, measurement error, model error and assumption sensitivity, kept separate |
| Encoder and optional predictor | Encoder: every 11.6 test with 95% intervals, baselines, ablations, failure cases and the G5 verdict. T8, if run: site-balanced MAE and bias, simple-baseline comparison, error by region, incidence and material, empirical coverage and interval width where evaluated |
| Performance | Actual bytes read, elapsed processing time, successful and rejected windows; no unmeasured throughput claims |

## 10. Product requirements

The interface reads the release bundle. The only live computation is the in-browser probe (8.7), whose outputs carry their own labels.

| View | Required content | Scientific constraint |
| --- | --- | --- |
| **Lunar target** | The actual target polygon; terrain descriptors; radar context; quality and frame information; target reference set | No continuous "10 cm to 1 km measured terrain" claim |
| **Mars targets** | Jezero and Arcadia windows on CTX hillshade; terrain descriptors; CTX QA | Terrain only; no radar panel except an "unavailable" note |
| **Earth candidates** | Declared search regions and windows; eligibility; separate radar, terrain and (if promoted) model views; named exclusions | Nothing shown outside processed regions; no predicted CPR painted as measured |
| **Evidence card** | Compared distributions and curves with units, uncertainty, provenance, limits, access status and a field experiment | Mode and evidence labels survive export and screenshots |
| **Live probe** | The probed window, its fingerprint and encoder distances to each target, percentiles, warnings | Labelled Derived (live) and Predicted; radar unavailable; never stored or ranked |
| **Model report** | Every item in 11.9 | All numbers from `report/*.json`; G5 verdict shown whatever it is |

**Static fallback.** If the interactive maps slip, ship the same routes with static figures, the candidate table and the cards from the same bundle. If the live probe is unreliable, hide it behind its feature flag rather than ship it broken. Do not spend the submission buffer on new live features.

### Each evidence card answers seven questions

1. Which exact Earth polygon and lunar target window are compared?
2. What task or scientific interpretation could this candidate help investigate?
3. Which measurements support it, at which band, geometry and spatial support?
4. Which properties differ, are ambiguous or are not observed?
5. How stable are the distances and ranks under the supported uncertainty and declared scenarios?
6. What are the practical campaign questions: access, permits, season, land status, logistics? Unchecked entries say **unknown**.
7. Which field observation would most efficiently confirm or reject the recommendation?

**Field-experiment template.** A surveyed surface patch with a clast-size and coverage distribution, material and moisture notes, and calibrated radar observations under controlled geometry. Dimensions and sampling are chosen for the sensor and site; they are never presented as completed measurements.

### Demo story

- **Allowed:** a visual lookalike whose measured radar signature conflicts with the target; or two similar CPR signatures with clearly different terrain or unresolved physical interpretations.
- **Not allowed:** "looks right but fails at boot scale", because no contact-scale measurement exists.

### Candidate count and language

- Aim for 3 to 5 qualified Earth windows, preferably in at least two geographic regions where data allow.
- Several windows in one lava flow are a small pilot, not independent global evidence.
- If fewer qualify, show the actual count and the exclusion reasons. Never weaken a gate silently.
- Use "candidate window identified by this search" until a literature check and field evidence exist.
- The result must include Earth locations and their characterization; a failure-only story does not replace that deliverable.

### Earth's limits on every card

The "what differs" answer (question 4) always lists these, however close the match:

- **Gravity:** about one-sixth of Earth's on the Moon.
- **Atmosphere:** wind, rain, weathering and erosion shape Earth surfaces; none act on the Moon.
- **Water and life:** Earth ground carries moisture and biology; lunar regolith is dry apart from possible polar volatiles.
- **Radiation and space weathering** act directly on the lunar surface.
- **Temperature:** permanently shadowed lunar ground is far colder than any Earth surface.
- **Illumination:** the Sun stays near the horizon at the lunar pole.
- **Regolith origin:** lunar soil is made by impacts, with sharp, unweathered grains unlike most Earth sediments.

**For Mars targets** question 4 lists instead: gravity about 0.38 of Earth's; a thin CO₂ atmosphere in which wind and dust act but rain does not; no liquid surface water or biology today; lower temperatures and higher surface radiation; and CTX DTMs that resolve only features of roughly 100 m and larger, so finer roughness is not compared.

### Figures and interface components

Card figures A–C are generated by the pipeline as SVG, so the app and the report show identical images. Interactive views (deck.gl maps, Observable Plot curves, React Three Fiber twins) read the same numbers from the bundle. The stack is fixed in 8.2.

| Component | Contents |
| --- | --- |
| Lunar target view | Hillshade of the Site01 DEM; Mini-RF CPR overlay with validity mask; 5 km window grid coloured by eligibility; primary window highlighted. Side panel: target nu(L) curves (relief and texture) with clone envelope, CPR block distribution, permanent-shadow fraction, product and frame details. |
| Earth candidates view | Declared regions; windows coloured by status (eligible, excluded with reason, shortlisted); a switch between radar and terrain distance families; candidate table with per-family distances, background percentile and tie flags; mode banner always visible. |
| Evidence card, figure A | Location maps: Earth polygon on a basemap and lunar window on hillshade, each with a scale bar |
| Evidence card, figure B | nu(L) on log–log axes for target and candidate, relief and texture, with the target clone envelope and the spread of the target reference set |
| Evidence card, figure C | Radar block distributions. Mode A: overlaid cumulative distributions with the Wasserstein value. Mode B: side-by-side panels with band labels and no distance. Mode C: an "unavailable" panel with the reason. |
| Evidence card, table D | One row per observable: value, evidence label, support, product ID |
| Evidence card, text | Answers to the seven questions, Earth's limits, access table with unknowns, the proposed field experiment |
| Validation board | Gate table read from `validation.json`, fixture status, the reproduction result |

**Added components:** Mars target view (CTX hillshade, window grid, targets); live probe card (8.7); model report page (11.9); 3D twin viewer (two heightmaps with synchronised cameras, the vertical exaggeration printed on screen); API reference at `/api/docs`.

## 11. Terraluna encoder (headline ML)

The Terraluna encoder is the team's own trained model and the headline ML result (D25). The deterministic fingerprint (M0) stays the primary, auditable ranking; the encoder is a labelled Predicted second opinion, promoted to co-rank only through G5.

### 11.1 Question and allowed claim

**Question.** Does a terrain encoder trained on Moon, Mars and Earth elevation models, supervised by our own physics fingerprints and pushed not to encode which body a window comes from, (a) reproduce the physics fingerprint on held-out regions, (b) carry morphology the fingerprint lacks, and (c) give cross-body nearest neighbours that agree with physics where physics is defined?

**Allowed claim if G5 passes:** a second, learned similarity that agrees with the physics fingerprint on held-out regions and adds morphology information (crater density). **Not allowed:** "AI found the best analog", "the model understands geology", or anything beyond 5 km windows at 78 m sampling.

**Positioning.** LunarFM (FDL 2025) is a Moon-only multimodal model on 0.5° chips limited to ±70° latitude, with no south pole and no Earth–Moon work. topo2vec learns Earth-only DEM embeddings. GEAR retrieves Earth-to-Earth terrain analogs. The Qaidam Basin study matches Mars and Earth imagery with a Siamese network. Terraluna differs by using elevation only, three bodies, the lunar south pole, physics supervision and held-out probes.

### 11.2 Data and sampling

| Body | Source | Native grid | Used | Training windows | Licence |
| --- | --- | --- | --- | --- | --- |
| Moon | LOLA south-polar DEM 80–90°S (exact product and file chosen at G0); PGDA78 Site01 for the target only | 5–20 m | 80–90°S, about 288,000 km² | 200,000 sampled centres | NASA PDS, cite |
| Mars | USGS ASP CTX DTMs, STAC collection `mro_ctx_controlled_usgs_dtms` (4,815 DTMs as COGs) | 20 m | every DTM passing QA | 200,000 | CC0, cite DOI 10.5066/P9JKVWR3 |
| Earth | Copernicus GLO-30 public COGs on AWS, masked with ESA WorldCover (D31) | 30 m | stratified by continent and climate zone | 200,000 | Copernicus licence with attribution; WorldCover CC BY 4.0 |

- **Preprocessing per window:** cut a 5.5 km context crop, resample with the D29 AEQD resampler to 64 × 64 pixels at 78.125 m, remove a best-fit plane. Channels: `asinh(z / 50 m)`, slope magnitude / 45°, valid mask. The same code (Python and `@terraluna/core`, parity-tested) feeds training and the live probe.
- **Quality filters:** valid fraction ≥ 0.8; CTX DTMs dropped when their `qa_metrics` CTX–MOLA standard deviation exceeds a threshold fixed at G0 (start at 10 m) and recorded; Earth windows need ≥ 90% allowed WorldCover classes.
- **Physics targets:** log nu(L) for relief and texture at the five lags (10 values) and H with its mask, computed by `terraluna_pipeline` on the 90 m grid with the ranking's own code.
- **Morphology labels (probe only, never used in training):** craters ≥ 1 km per 100 km² from Robbins (2018, Moon) and Robbins and Hynek (2012, Mars).
- **Storage:** float16 shards of 3 × 64 × 64 (24.6 kB per window, about 15 GB for 600,000 windows) as memory-mapped `.npy` plus Parquet metadata; fits in host memory.
- **Splits by space, never at random.** Moon: four 90° longitude sectors (one test, one validation). Mars: DTMs grouped by 10° × 10° tile, 15% of tiles test, 10% validation. Earth: 2° × 2° blocks, 15% test, 10% validation. Training blocks that touch a test block are dropped. Every window overlapping a target footprint or its 10 km buffer is excluded from training and from every probe.

### 11.3 Model

- **Backbone:** ResNet-18 at half width (32, 64, 128, 256 channels), stride-1 stem for 64 × 64 input, global average pooling to a 256-value embedding, L2-normalised for retrieval. About 2.8 million parameters; about 6 MB as fp16 ONNX.
- **Heads:** a 3-layer MLP projector (2048 wide) for VICReg; a physics head (2-layer MLP, 11 masked outputs); a body head (2-layer MLP, 3 logits) behind a gradient-reversal layer.
- Only the backbone is exported.

### 11.4 Objectives

`L = L_VICReg(z1, z2) + α · L_phys + λ · L_body`

- **Views:** two augmentations of one window: the eight rotations and flips, a sub-crop offset of at most 250 m inside the context crop, Gaussian noise of 1 m, and 0–10% extra nodata holes. No amplitude or scale changes, because those change the physics.
- **L\_phys:** masked mean squared error on standardised physics targets for both views; α = 1.
- **L\_body:** cross-entropy through gradient reversal; λ ramps from 0 to λ\_max with the DANN schedule; λ\_max swept over 0, 0.1, 0.3 and 1.0.
- **Balanced batches:** a third of each batch from each body.

### 11.5 Training plan and compute

- AdamW, learning rate 1e-3 scaled by batch / 1024, 2 warm-up epochs, cosine decay, weight decay 0.05, batch 1024, bf16, 40 epochs. One run per GPU; no multi-GPU training needed. Measure throughput in the first run and quote only measured numbers.
- **Round 1 (8 GPUs at once):** main configuration (λ\_max = 0.3) with seeds 0, 1 and 2; no physics head; λ\_max = 0; VICReg only; physics head only (no VICReg); λ\_max = 1.0.
- **Round 2 (8 GPUs):** λ\_max = 0.1; a Moon-and-Earth-only run tested on Mars (held-out body); frozen-feature extraction for the ImageNet ResNet-18 and DINO ViT-S/8 baselines; spare GPUs rerun any failed run.
- **Logged per run:** config hash, seed, data-manifest hash, git SHA, each loss term, validation probe metrics per epoch, GPU hours.
- Model selection uses validation blocks only. Test blocks are read once, after the configuration is frozen, for G5.

### 11.6 Evaluation protocol

All tests use frozen embeddings on test blocks. 95% intervals come from 1,000 bootstrap resamples over spatial blocks.

| Test | Measure | Compared with |
| --- | --- | --- |
| Physics probe | Ridge regression from embedding to each log nu(L) and H; R² per target | Random-init encoder, ImageNet ResNet-18, DINO ViT-S/8 (all frozen) |
| Morphology probe | Ridge regression to log crater density on Moon and Mars test blocks; R² gain over a ridge on the 11 fingerprint values | Fingerprint-only ridge, ImageNet ResNet-18, DINO |
| Body invariance | Balanced accuracy of a logistic probe predicting the body (chance 0.33) | λ\_max = 0 run |
| Physics agreement | Spearman ρ between embedding cosine distance and RMS log-nu distance on 20,000 random cross-body test pairs | — |
| Target retrieval | Jaccard@20 between the encoder's and the deterministic top-20 Earth windows per target | Random ranking |
| Seed stability | Jaccard@20 of encoder top-20 across the three seeds | — |
| Held-out body | Physics probe R² on Mars for the Moon-and-Earth-only run | Main run |
| Out of distribution | Mahalanobis distance to the training embeddings; above the 99th percentile is flagged on cards and probes | — |

The 20 largest disagreements between encoder and fingerprint rankings are shown with hillshades as failure cases.

### 11.7 Promotion rule (G5)

The encoder becomes a third distance family (always labelled Predicted) only if every condition holds on test blocks:

1. Physics probe R² ≥ 0.8 for log nu relief at every lag (lower 95% bound).
2. Morphology gain ≥ 0.10 R² over the fingerprint-only ridge on the Moon or Mars, with the lower 95% bound above 0.
3. Body-probe balanced accuracy ≤ 0.60.
4. Physics agreement ρ ≥ 0.5.
5. Seed stability Jaccard@20 ≥ 0.5.
6. It beats ImageNet ResNet-18 and DINO on tests 1 and 2 (paired bootstrap interval excludes 0).

These thresholds are engineering defaults set in config before training and never changed after the test blocks are read. If any fails, the encoder stays `EXPERIMENTAL`: the report and live probe still show it with the failed tests named, and rankings stay on M0.

### 11.8 Export and live use

- ONNX opset 17 with dynamic batch, in fp32 and fp16; parity as in 8.8. Preprocessing (resampling, detrend, channels) lives in `@terraluna/core` and Python, not in the graph, so it stays inspectable and parity-tested.
- Target embeddings, background distance histograms and 50,000 test embeddings for the UMAP go into the bundle.
- Budget: ≤ 50 ms per window on WASM with 4 threads.
- The model, model card and window dataset index are mirrored to the Hugging Face Hub (D32).

### 11.9 Model report (`/model`)

1. Model card (Mitchell et al. 2019 structure): intended and out-of-scope use, data, metrics, caveats.
2. Data: windows per body and split, maps of split blocks, licences.
3. Training: loss curves per term (mean and range over seeds), GPU hours.
4. Results for every test in 11.6 with 95% intervals, baselines side by side.
5. Ablation table and the invariance trade-off chart (body-probe accuracy against physics R² across λ\_max).
6. Interactive UMAP of 50,000 test windows coloured by body, H, relief or crater density; clicking a point shows its hillshade.
7. Retrieval gallery: each target with encoder top-5 and deterministic top-5 Earth windows.
8. Failure cases and out-of-distribution examples.
9. The G5 verdict, generated from `validation.json`.

Every number comes from `report/*.json` in the bundle (D10). W&B, if used, is internal only.

### 11.10 What the encoder must never do

- Enter a ranking as Observed or Derived; it is always Predicted.
- Replace the deterministic fingerprint on any card.
- Support any claim about radar, trafficability, regolith mechanics or ice.
- Be evaluated on windows overlapping a target footprint.

### 11.11 Deferred: radar cross-sensor experiment (T8)

T8 is built only if every other task is done (D30). Its ladder, rules and specification are kept unchanged below.

The radar ranking (M0) needs no model. Run this ladder in order only once T8 is started.

| Level | Method | Allowed role |
| --- | --- | --- |
| M0 | Observed full-polarimetric CPR and eligible terrain | The shippable regional baseline |
| M1 | Constant-median predictor; a simple regression on correctly calibrated dual-polarization powers, ratios and incidence; the compact-polarimetry assumption baseline CPR = 2x / (1 − x), labelled Assumed | Baselines for the inference experiment |
| M2 | A small regularized or quantile model | Optional empirical predictor; promoted only through G5 |
| M3 | A neural network | Only with a concrete specification, usable labels and independent evidence |

**Rules.**

- All levels use identical held-out groups and preprocessing.
- Training matches the inference product's radiometry, support and polarization. C-band VV/VH input is not L-band HH/HV with renamed fields.
- Transfer is evaluated on paired target-sensor observations, including temporal and material mismatch. A whole-site UAVSAR holdout is necessary evidence for that domain, not sufficient evidence for PALSAR-2 or all bare Earth.
- Predicted CPR is always labelled `Predicted` and never enters a scientific ranking before G5.
- No fixed thresholds are adopted. A model is promoted only if its errors and intervals are small enough to separate the candidates, it beats the M1 baselines, and its held-out behaviour is stable. Otherwise its diagnostics are shown and ranking stays on M0.
- No claim of guaranteed global 90% interval coverage. Empirical coverage and interval width are shown only where evaluated, and out-of-domain status elsewhere.
- Sentinel-1 temporal coherence is not the HH-VV polarimetric correlation, and is never used to fill it in.

### Audit of the earlier ML plan (D12)

| Earlier proposal | Verdict | Reason |
| --- | --- | --- |
| Completion network: two channels to CPR, U-Net ensemble trained on UAVSAR degraded to look like PALSAR-2 | Optional M2 or M3, behind G5 | Synthetic degradation is development evidence only. Promotion needs paired PALSAR-2 observations over UAVSAR-covered ground, grouped by geography. Its fixed pass marks (median error 0.1, 85–95% coverage) are dropped. |
| Physics baseline from the compact-polarimetry relation ρ = 1 − 2x | Kept in M1, labelled Assumed | A modelling assumption; F7 shows why it cannot be exact |
| Cross-scale "Earth law / Moon law" model | Dropped | Depends on predicted global Earth CPR and cross-band comparison. Its "un-Earth-like" output invites the prohibited "no analog exists" claim. |
| Self-supervised texture encoder | Replaced by the Terraluna encoder (D25) | The objection (no validated physical meaning) is answered by physics supervision, held-out probes and G5 (11.6–11.7) |
| Incidence-corrected Mini-RF mosaics as the primary lunar input | Context only (D13) | Normalized quantities, not CPR values |
| Co-registered lunar benchmark stack (SomBench) | Optional convenience | Release status was pending when checked |
| Scattering-model table of moisture effects | Optional, labelled Assumed | A scenario, not a measurement |
| Using the GPU cluster | Used for encoder training (D33) | No scientific claim depends on GPUs; the deterministic ranking runs on a laptop |

### T8 experiment specification

**Question.** Can PALSAR-2 dual-polarization data predict the block CPR that UAVSAR observes, well enough to separate candidates?

- **Target:** observed CPR (ratio of mean powers) per 250 m block from UAVSAR, in the declared convention.
- **Inputs per block:** PALSAR-2 gamma-zero HH and HV in linear power (block-aggregated from DN as in section 7), `x_power`, local incidence from both sensors, and optionally Copernicus slope and texture nu. No input derived from UAVSAR.
- **Pairing:** the same blocks on both sensors; the PALSAR-2 yearly epoch nearest the UAVSAR date; the time gap recorded; blocks with water, snow, vegetation, layover or shadow, or land-cover change excluded.
- **Groups:** one group per geographic region (one lava field, one playa basin). All windows and acquisitions in a region stay together. Groups are split into development, calibration and test. With fewer than about five independent groups (an engineering default), T8 is reported as a pilot and nothing is promoted.
- **Development-only augmentation:** UAVSAR degraded to PALSAR-like inputs may help develop features, but never counts as evaluation.
- **Models:** M1a constant median; M1b the assumed formula 2x / (1 − x); M1c ridge regression on log features; M2 gradient-boosted or linear quantile regression for the 5th, 50th and 95th percentiles.
- **Metrics:** site-balanced MAE and bias; error by region, incidence bin and surface type; empirical coverage and width of 90% intervals on the test groups; abstention rate.
- **Abstention:** predictions only inside the training range of every input; outside it, `Unavailable`.
- **Promotion (G5):** M2 beats the best M1 by more than a bootstrap-over-groups interval, its errors are smaller than the typical separation between candidates, and it is stable across groups.
- **Use if promoted:** predicted CPR may screen windows outside UAVSAR coverage, always labelled `Predicted`. A candidate supported only by predicted radar says so on its card.

## 12. Implementation backlog

Build in vertical slices. For T2 to T6, write the failing numerical or behaviour test before implementing the transformation or guard, then run the focused check. Do not build a large test framework for presentation-only changes.

| Task | Owner role | Depends on | Files and components | Deliverable and acceptance |
| --- | --- | --- | --- | --- |
| **T0** Repository and contracts | Platform (SWE-2) | None | Monorepo layout (8.3), `LICENSE`, `Makefile`, `schema.py` → `packages/schema`, mock bundle, CI jobs `py`, `ts`, `schema` | Within hour 1: `make test` green on empty modules; mock bundle validates; Vercel preview renders `/` with the mock banner |
| **T1** Data pilot and product records | Data engineer, science | None | `io`, `quality`, `sources.json`; PGDA78 and Mini-RF; CTX STAC search; Copernicus COG and CORS check; WorldCover | G0 recorded with every item in section 9; mode and `cop30_mode` chosen |
| **T2** Numerical polarimetry | Science (ML 2) | The canonical array convention; starts on synthetic inputs at hour 0 | `polarimetry`, independent fixture tests | F1–F10 pass; conventions and invalid-data behaviour explicit; G1 recorded |
| **T3** Supported terrain measurements | Analysis | T1 terrain inputs | `terrain`, `geometry` (D29), support and mask tests | Common-lag curves; H unavailable when unsupported; tests include a plane, a sinusoid, nodata, a synthetic fractal and AEQD round trips |
| **T4** First complete comparison | Analysis, export | T1–T3 | `fingerprints`, `matching`, `export`, pair-eligibility tests | One real, correctly labelled card in a real bundle on the preview deployment; Mode B cannot emit a cross-band match; G2 recorded |
| **T5** Bounded candidate search | Analysis, data engineer | T4 | Windows, masks including WorldCover, comparisons, background samples, for the Moon and both Mars targets | Actual search universe and shortlist per target; invalid or unknown windows stay distinct from poor matches |
| **T6** Uncertainty and scientific checks | Analysis, science | T4–T5 | `uncertainty`, `validation.json` | Clone and scenario runs; Holuhraun reproduction (G3); G4 recorded |
| **T7** Web app and exports | Frontend (SWE-1), product (SWE-3) | T0 mock bundle; real data from T4–T6 | `apps/web` routes (8.6), maps, cards, charts, twins | Every route on preview; card values equal serialized values; static fallback works; zero serious axe findings |
| **T8** Radar cross-sensor model (deferred) | ML | Everything else done (D30) | Separate predictor module | As in 11.11; otherwise not built |
| **T9** Release | Integration | All | Make targets, README, citations, HF mirror, submission materials | G6 passes; production smoke test; deadline and public links checked |
| **T10** TypeScript core and parity | Platform (SWE-2) | T3 contract | `packages/core`, `fixtures/parity` | 8.8 suite green in CI |
| **T11** Encoder dataset | ML, data engineer | T1, T3 | `ml/dataset.py`, shards, splits, labels | 600,000 windows with physics targets; split maps; leakage check finds no window within 10 km of a target |
| **T12** Encoder training and evaluation | ML | T11 | `model`, `train`, `evaluate` | Rounds 1 and 2 done; every 11.6 metric with intervals; G5 recorded |
| **T13** ONNX export and live probe | ML, platform | T10; a round-1 checkpoint is enough to start | `export_onnx`, `workers/probe.worker.ts` | ONNX parity; 8.7 budgets met; final model swapped in after G5 |
| **T14** Model report | Product (SWE-3), ML | T12 | `report.py`, `/model` | Every 11.9 item rendered from `report/*.json` |
| **T15** Public API | Platform (SWE-2) | T0 | `server/api`, OpenAPI, Scalar | Every 8.5 endpoint with route tests; `/api/docs` live; DEM proxy only if G0 needs it |

**Added tasks (paper, agents, quantum)**

| Task | Owner role | Depends on | Files and components | Deliverable and acceptance |
| --- | --- | --- | --- | --- |
| **T16** Research paper | paper-writer, paper-figures, citation-checker agents; product (SWE-3) supervises | T5, T6, T12, T14 for results; Background starts at hour 2 | `paper/` (22.6) | `make -C paper` passes every check in 22.6; PDF linked from `/paper` |
| **T17** Agent setup | Platform (SWE-2) with the orchestrator | T0 | `CLAUDE.md`, `AGENTS.md`, `.claude/agents/*.md`, `reviews/schema.json`, review scripts | Every agent in 23.2 defined; one task completes the full 23.3 loop, including a Codex review file, within hour 2 |
| **T18** Qiskit stretch | ML | Section 24 start condition | `quantum/` | Appendix C numbers generated like the rest; or recorded as not started |

**Start in parallel at hour 0:** T0, T1, T2 on synthetic inputs, the T7 shell and T15 on the mock bundle, and T10 on synthetic parity fixtures. ML starts T11 on synthetic DEMs at once and on real data as soon as G0 confirms DEM access (aim: hour 3). Mock records live in `fixtures/mock-bundle/` and never in a release.

**Added to T1 and T2 after the desk checks.** T1 runs the lunar S4-sign check (6.2) and reads the Site01 window from the Mini-RF polar mosaic by range request. T2 adds polsartools as a development-only dependency and checks the team's own covariance-to-circular and m-χ code against it on one real UAVSAR scene.

### End-to-end checks

- [ ] A valid CPR of 3 survives to export unclipped.
- [ ] A zero or noise-dominated OC cannot enter a distance as a valid zero or huge value.
- [ ] A missing source channel or unknown convention yields an explicit `Unavailable` status.
- [ ] A Mode B record can never show "radar match: passed".
- [ ] Changing angle units in metadata cannot silently move incidence bins.
- [ ] A changed polygon or config invalidates cached results.
- [ ] An unsupported H stays unavailable in the interface and report.
- [ ] Unknown access or permit status stays unknown after report generation.
- [ ] Each card's displayed values equal the serialized comparison values.
- [ ] A bundle with `"mock": true` cannot deploy to production.
- [ ] An encoder distance never appears without the Predicted label, and never in a ranking while G5 is not `PASS`.
- [ ] A live probe on a window with valid fraction below 0.8 stops with a stated reason.
- [ ] The same window gives the same fingerprint in the pipeline, the API and the live probe (parity fixture).
- [ ] No training window lies within 10 km of a target footprint.

### Commands

```
make setup         # uv sync + pnpm install; checks GDAL and Node versions
make test          # Python and TypeScript unit tests, fixtures F1–F10
make pilot         # G0: small real-data reads for Moon, Mars, Earth; CORS check
make build-data    # selected mode: windows, fingerprints, comparisons, tiles
make dataset       # T11: encoder shards, splits, physics targets, labels
make train RUN=main SEED=0   # one training run on one GPU
make sweep         # round 1 or 2: one run per GPU from ml/sweeps/*.yaml
make evaluate      # 11.6 tests on frozen embeddings; writes report/*.json
make export-model  # ONNX fp32 and fp16 + parity
make parity        # writes fixtures/parity/*.json and runs both test suites
make validate      # empirical gates; writes actual gate states
make bundle        # assembles release/<bundle_version>/ with manifest and checksums
make publish       # uploads the bundle to object storage and the HF mirror
make web-dev       # Next.js dev server on the current bundle (or the mock)
make all           # reproduces the available run, prerequisites documented
```

Downloading is kept separate from computing. Authentication and availability are prerequisites, not reasons for a misleading green offline test. A failed optional download may select a fallback; it never manufactures replacement evidence.

## 13. Schedule, ownership and cut order

### Fix the deadline first

At kickoff, fill `working_start`, `assigned_event_timezone`, `global_submission_cutoff`, `local_judging_cutoff` and `effective_cutoff` in config, from the team's event page or its organizers. Do not infer the event from a laptop clock. The official event page says submission closes Sunday 15 November at 23:59 local time, and the participant FAQ tells teams to submit according to the ending time of their local event, which may be earlier.

### Elapsed-time plan

Thirty working hours plus a protected submission buffer, in three parallel tracks, shortened to fit the actual `effective_cutoff`.

| Elapsed time | Science and data | ML | Web and platform | Stop rule |
| --- | --- | --- | --- | --- |
| 0–2 h | G0 data pilot: Moon, Mars, Earth DEM, CORS | Dataset code on synthetic DEMs | T0 repository, mock bundle, first preview deploy | Do not block on registration or global exports |
| 2–6 h | G1, G2: conversions, terrain, first real card | T11 shards built on the cluster | T7 explorer pages and cards on the mock bundle; T10, T15 | No model result before the first defensible card |
| 6–12 h | G3: bounded search per target, reference checks | Round 1: eight runs in parallel | First real bundle on preview; live probe with fingerprint only | Stop adding data sources if ingest is unstable |
| 12–18 h | G4: shortlists, initial uncertainty | Round 1 evaluation; round 2 | Probe with ONNX from a round-1 checkpoint; `/model` on interim metrics | Remove an unsupported radar ranking rather than weaken its label |
| 18–24 h | Final uncertainty, geographic checks, cards | Freeze config; read test blocks once; G5; final export | Model report, 3D twins, polish, accessibility | No new model variants after the freeze |
| 24–30 h | Freeze results; written report | Model card; Hugging Face mirror | Production deploy, demo video, G6 | No new subsystem |
| Protected buffer | Link and access checks, rehearsal, submission confirmation | — | — | Finish before the earlier applicable cutoff |

**If less than about 12 hours remain** at any point: measured regional samples, the simplest supported terrain comparison, the deployed app on static figures and cards, and the encoder report from whatever runs finished, marked EXPERIMENTAL. Cut T8, probe polish and new model variants immediately.

### Ownership

| Role | Owns | Backs up | Suggested fit |
| --- | --- | --- | --- |
| ML 1 (lead) | T11, T12, T13 export, G5, model card | T2 | ML specialist |
| ML 2 / science | T2, T4, T6, G1–G4, Holuhraun reproduction | T12 evaluation | ML specialist |
| Data engineer | T1, T3, T5, tiles, bundle export, G0 | T11 | Data engineer |
| Frontend (SWE-1) | T7 maps and explorer pages, cards | T14 | Software engineer |
| Platform (SWE-2) | T0, T10, T13 worker, T15, CI/CD, deployment | T7 | Software engineer |
| Product (SWE-3) | T14, landing page, 3D twins, `/methods`, `/data`, demo video, submission text (T9) | T7 | Software engineer |

Agents from section 23.2 do the work in each role; the person in the role supervises them, answers their questions and owns the human checkpoints for that role.

Roles are responsibilities, not job titles; combine them for a smaller team. The GPU cluster runs the encoder (D33); no scientific claim depends on it.

### Cut order

1. The Qiskit stretch (section 24), then paper appendices B and C.
2. T8, further learned models and global CPR inference.
3. Global candidate coverage and additional lunar regions.
4. Extra radar instruments not needed by the chosen mode.
5. 3D twin viewer and UMAP interactivity (their static figures stay).
6. Route connectivity, if it is unvalidated or distracting.

**Never cut:** correct units and conventions, mode labels, provenance, the actual Earth candidate characterization, failed-gate reporting, the written submission.

## 14. Release checklist

- [ ] `effective_cutoff` recorded, and the submission confirmed before it with buffer.
- [ ] Shipping mode shown in the title, map, cards and report.
- [ ] Every gate has a status; unexecuted gates show `NOT_RUN` and failed gates are visible.
- [ ] Every number in the pitch and report appears in the exported run artifacts (G6).
- [ ] At least one NASA product connected to the challenge's listed resources is used, and its contribution explained: LRO Mini-RF and LOLA through PDS and PGDA, UAVSAR through Earthdata, MRO CTX DTMs through USGS Astrogeology.
- [ ] All reused data, code, figures and tools cited, with product-specific attribution and redistribution conditions respected.
- [ ] AI use disclosed in the README; AI-generated images or video visibly labelled; no NASA insignia in AI-made material.
- [ ] "Candidate Artemis region at Connecting Ridge", never "NASA's selected base".
- [ ] Novelty described as the implemented method; no priority claim and no award promise.
- [ ] The current submission form's demo format and public-link requirements checked.
- [ ] Static fallback works from the exported artifacts alone.
- [ ] Links open without credentials; no signed URLs or secrets in manifests.
- [ ] Expected results and proposed experiments clearly marked as future work.
- [ ] `make all` reproduces the run with documented prerequisites.
- [ ] Production runs a real bundle (`"mock": false`) pinned by `BUNDLE_VERSION`, and the smoke test passed.
- [ ] `/api/docs` loads and every 8.5 endpoint returns schema-valid responses.
- [ ] The live probe works in current Chrome, Firefox and Safari, or is hidden behind its flag with a note.
- [ ] Model card and report published with the G5 status; Hugging Face mirror links open.
- [ ] Copernicus, WorldCover, CTX, LOLA and crater-catalogue attributions on `/data` and in the README.
- [ ] Mars targets described as "candidate region studied by NASA" only where a source says so; Jezero described as the Perseverance landing site.
- [ ] `make -C paper` passes every 22.6 check; `paper/main.pdf` is linked from `/paper` and the README.
- [ ] Every merged pull request and every paper section has Claude-verifier and Codex review files with resolutions.
- [ ] The AI-use disclosure names Claude Code, its subagents and Codex (`gpt-6.1-sol` or the recorded substitute), and says what humans approved.

### Submission form contents

As recorded in another team's notes on the 2026 global form (verify on your own project page):

| Field | What to put in it |
| --- | --- |
| Project name | Terraluna, plus a short subtitle such as "Earth twins for Moon and Mars base sites" |
| Summary | At most 1,500 characters |
| Project demonstration | A video of 30 seconds or less, as a public link |
| Project link | Public repository, the live app URL and the model card |
| Project details | Rich text: what it does, how it works, benefits, what makes it creative, factors considered, tools and technologies |
| Use of AI | Checkboxes for data collection, plus a disclosure in the project text |
| NASA data | One link per NASA product actually used |
| Space agency partner and other data | One link per other product, library or paper actually used |
| Required confirmations | Agreement to the Terms and Conditions, and confirmation that the project is the team's original work with all external resources cited |

The official terms require that the team's original content be openly available or carry an open-source licence approved by the Open Source Initiative, and that every source used be cited. Add a `LICENSE` file at hour 0.

**Readiness.** Development can start now: scope, mode branches, contracts, conventions, task order and fallbacks are explicit, and the first task is the data pilot. The scientific result is ready to submit only when its mode-specific gates are evidenced and its claims match them. This document does not substitute for running those checks.

## 15. Open items to settle at kickoff

| Item | Why it matters | Owner | Settle by |
| --- | --- | --- | --- |
| Local judging cutoff and `effective_cutoff` | Every schedule decision | Integration/submission | Hour 0 |
| PRADAN registration outcome | Decides whether Mode A is possible | Data/science 1 | Checked at G0 and every few hours; never blocks |
| Mini-RF transmit handedness in the selected product | Sources disagree; the convention must be stored, not assumed | Data/science 1 | G1 |
| UAVSAR scene IDs over Holuhraun and other bare ground | Defines the Earth search universe | Data/science 2 | G0 |
| Site01 ROI choice and primary target window | Fixes the target | Data/science 1 | G0, then frozen |
| Definitions of the Fassett et al. δ layers | Needed before they appear even as context | Data/science 1 | Before first use |
| Incidence bins and SNR rule | Required for G2 | Analysis | On development data, before G4 |
| Reconstructing the Neish Holuhraun polygon | Required for an honest G3 | Data/science 2 | G3 |

**Also at hour 0:** choose and commit the project licence (section 20), and confirm on the Space Apps team list that the name Gradient Descent is not already taken at your event.

**Added at the architecture review (14 November):**

- Does the Copernicus GLO-30 bucket return CORS headers to a browser Range request? Sets `cop30_mode` (direct or proxy).
- WorldCover COG paths, and whether they are CORS-readable for the live check.
- The exact LOLA south-polar DEM file for training: grid, projection, nodata.
- Arcadia Planitia target box from CTX coverage inside the shallow-ice region; Jezero window near the landing site.
- CTX QA threshold (start at 10 m CTX–MOLA standard deviation).
- Object storage choice (Cloudflare R2 or Vercel Blob) and its CORS rules; Vercel function bandwidth if the DEM proxy is needed.
- Repository licence: Apache-2.0 proposed (polsartools stays development-only, D19).
- A team member's Hugging Face account or organisation for the mirror (D32).
- Optional custom domain.

## 16. Challenge requirements traceability

Each consideration in the official brief, and where this spec answers it.

| The brief asks | Where Terraluna answers | Coverage |
| --- | --- | --- |
| Identify Earth locations analogous to permanent Moon base locations and Mars | Connecting Ridge target and 3–5 Earth windows (sections 6, 10) | Moon: yes. Mars: out of scope, explained below. |
| Explain what makes them analogous and what does not | Card questions 3 and 4, plus Earth's limits (section 10) | Yes |
| Think beyond visual similarity | Radar scattering and terrain statistics, not imagery (section 6) | Yes |
| Consider geology, terrain, temperature, ice, dust, biology, caves and operations | Terrain and radar-sensitive surface structure are measured. Thermal, ice, biology and caves are stated as not assessed. | Partial, by design |
| An analog need not match everything | Separate distance families; no single score (D07) | Yes |
| Think about processes as well as places | Card question 2, the field experiment, and the radar-interpretation use (D02) | Yes |
| Look beyond well-known analog sites | Search universe set by data coverage; known sites are context only (section 9) | Yes, within declared regions |
| Consider scale | 5 km windows, explicit lags, effective resolution (section 6.3) | Yes |
| Use multiple datasets | Mini-RF and LOLA on the Moon; UAVSAR, Copernicus DEM and WorldCover on Earth (section 5) | Yes |
| Connect the analog to exploration | Field experiment and campaign questions on every card | Yes |
| Remember Earth's limits | Fixed card section (section 10) | Yes |
| Document findings in writing | `outputs/report.md` (sections 8, 14) | Yes |
| Surprise us | Demo story: a visual lookalike whose radar signature conflicts (section 10) | Planned |

**Mars.** The "and Mars" in the challenge title is answered by the two Mars targets and their Earth shortlists (D27), the Mars rows of the encoder evaluation (11.6) and `/explore/mars`.

Space Apps rules on NASA data, AI disclosure and the deadline are in sections 13 and 14.

**Mars.** The brief allows a single site or process, so the submission states plainly that it covers the Moon only. Mars has no orbital imaging radar comparable to Mini-RF; Earth-based radar maps of Mars are coarse. The report names Mars as the next extension: a 2020 study already compared Martian lava flows with terrestrial flows using UAVSAR and AIRSAR at decimetre scale, so the same pipeline could be applied there.

## 17. Related work and positioning

### Competing Space Apps projects

Figures are as each project reports them; none was re-run here.

| Project | What it does | What it reports | Relation to Terraluna |
| --- | --- | --- | --- |
| [SolMate](https://github.com/Tanbin-CS/solmate) | About 1.9 million land cells at 0.1°; 7 physical dimensions from 42 NASA layers; no deep learning; a rover drive over real terrain | Jezero AUC 0.84; Moon median AUC 0.76; Arcadia AUC 0.58. Its own validation notes report that terrain alone separates Moon analog sites from controls at about 1 km (median AUC 0.745) but not at 100 m (0.360), so the 100 m score was taken out of its main index. | Global multi-layer screening. Terraluna is regional, terrain- and radar-based, and does not compete on SolMate's benchmark. |
| [ShadowAstra Atlas](https://github.com/ABRUBAB/ShadowAstra_Atlas) | 80,206 cells at 0.5°; positive-unlabelled model on 38 known sites; a vision-transformer terrain model | Hidden known sites median top 2.4%; terrain model added nothing to the full score; best-10 km-spot test median rank 42% (p = 0.13). Since a 14 November commit it also reads the Mini-RF polar CPR mosaic and reports median CPR within 10 km of each lunar target, as unscored facts; it uses no Earth radar. Open [dataset](https://huggingface.co/datasets/ABRUBAB/shadowastra-atlas), CC BY 4.0. | Closest in direction on radar; credit it. Compare only by adopting its protocol exactly. |
| [EarthTwin](https://github.com/es-ratt/earthtwin) | 18 hand-picked sites; weighted exponential similarity over slope, roughness, temperature, ice, light and logistics | Top pick flips when weights move 10 points | That fragility is why Terraluna keeps distance families separate |

What these projects already show, and Terraluna must also show: same-unit comparisons, "what Earth cannot copy" labels, held-out validation, and leads away from famous sites.

**Learned terrain representations.** LunarFM (FDL LunarLab 2025) is the closest planetary model: about 110 million parameters trained on three H100s, Moon-only, ±70° latitude, 0.5° chips, with weaker results under contiguous spatial holdouts. topo2vec learns Earth DEM embeddings with fractal self-supervision. GEAR retrieves Earth-to-Earth terrain analogs with expert labels. A Qaidam Basin study pairs Mars and Earth imagery with a Siamese network. CraterBench-R benchmarks Mars crater retrieval and finds that supervised metric-learning fine-tuning can hurt retrieval. ShadowAstra uses a pretrained DINO ViT rather than a model trained for the task. Terraluna's encoder differs as set out in 11.1, and its report includes the spatial-holdout tests that LunarFM found hard.

### Scientific foundations

| Work | What it established | Use here |
| --- | --- | --- |
| Neish et al. 2017 | Lunar impact melts are radar-rough at decimetres yet smooth at metres; none of the Earth lavas studied matched | Holuhraun reproduction target; covariance conversion |
| Tolometti et al. 2020 | L-band CPR separates smoother and rougher flows at Craters of the Moon | Evidence that L-band CPR discriminates lava textures |
| Tolometti et al. 2022 | Holuhraun facies are separable by UAVSAR CPR, yet their CPR distributions overlap by 88–89% | Ground context for the Holuhraun scenes |
| Martian lava flow roughness study, 2020 | Linked Martian metre-scale roughness to terrestrial decimetre-scale radar | Mars extension |
| Campbell & Shepard 1996 | Roughness scaling holds only over finite ranges | Why no Hurst-based correction (D11) |
| Campbell 2012 | Valid CPR well above 1.5 | No clipping |
| Rivera-Valentín et al. 2026 | CPR is not monotonic with rock abundance | Claim limits (section 3) |
| Gao et al. 2023 | CPR tracks thermal rock abundance at blocky ejecta; volumetric rock concept | Interpretation context |
| Fa & Cai 2013 | Incidence angle shifts CPR; high CPR in anomalous polar craters is mostly rocks | Incidence bins; not an ice detector |
| Fassett et al. 2024 | Mini-RF orthorectified and topographically corrected; shadowed areas brighter but with similar polarization | Lunar context (D13) |
| Barker et al. 2021, 2023 | LOLA polar DEMs with error models | Terrain and clones |
| Shukla et al. 2024 | Deep-learning dielectric inversion; look direction alone shifts the result by about 1.0 | Caution about in-distribution scores |
| Souyris et al. 2005; Deng et al. 2023; Zhang et al. 2022 | Reconstructing fuller polarimetry from compact or dual-pol data | Background for M1 and M2 |
| LunarFM; NASA-IBM lunar model; SomBench | Lunar foundation models and benchmark | Not used: poles excluded, or radar only as a tile average |
| Aussel et al. 2025 | About 94 million boulders larger than 4.5 m mapped between 60°S and 60°N | Not used: no polar coverage; coarser than the radar scale |

## 18. Glossary

| Term | Meaning |
| --- | --- |
| SAR | Synthetic aperture radar: an imaging radar that sends its own pulses, so it needs no sunlight |
| Band | Wavelength class. S-band about 12.6 cm (Mini-RF), L-band about 24 cm (UAVSAR, PALSAR-2, DFSAR), C-band about 5.6 cm (Sentinel-1, RCM) |
| H, V | Horizontal and vertical linear polarization |
| Full (quad) polarimetry | All four transmit/receive combinations recorded with phase; allows any polarization to be synthesized |
| Dual-pol | Two channels such as HH and HV, without the phase between them |
| Compact or hybrid polarimetry | Transmit circular, receive H and V (Mini-RF, RCM) |
| Stokes S1–S4 | Four numbers describing the received wave; S1 is total power |
| SC, OC | Same-sense and opposite-sense circular received power |
| CPR | SC divided by OC. Near 0 for mirror-like surfaces; near 1 or above for rough or multiply scattering surfaces |
| Covariance products | HHHH, VVVV, HVHV (powers) and HHHV, HHVV, HVVV (complex correlations) |
| BSA, handedness | The backscatter alignment convention and the sense of circular rotation; they decide the sign of terms in SC |
| Local incidence angle | Angle between the radar beam and the local surface normal |
| Sigma-zero, gamma-zero | Backscatter normalized by ground area or by area projected perpendicular to the beam |
| Layover, shadow | Steep-terrain distortions where radar returns overlap or vanish |
| DN | Digital number stored in a file; converted to physical units by a stated formula |
| DEM, DSM | Elevation of bare ground, or of the top surface including vegetation and buildings |
| Effective resolution | The size of detail actually resolved, which can be coarser than the grid spacing |
| nu(L) | Root-mean-square height difference between points a distance L apart |
| Hurst exponent H | Slope of log nu against log L; describes how roughness changes with scale |
| PSR | Permanently shadowed region |
| ROI | Region of interest |
| PDS, PGDA, ODE | NASA's Planetary Data System, Goddard's Planetary Geodesy Data Archive, and the Orbital Data Explorer |
| Wasserstein distance | A distance between two distributions: how far one must be moved to match the other |
| Pareto set | Candidates that no other candidate beats on every distance at once |
| Conformal prediction | A method that calibrates prediction intervals on held-out data |

| More terms | Meaning |
| --- | --- |
| Degree of polarization m | Share of the received power that is polarized; 1 for a pure reflection, 0 for a fully random return |
| Ellipticity χ | Angle describing how circular the received polarization is; its sign separates single-bounce from double-bounce returns under circular transmission |
| m-χ decomposition | Splits a hybrid-polarimetric return into single-bounce, double-bounce and random parts (Raney et al. 2012) |
| Hybrid-pol synthesis | Computing what a circular-transmit radar would record from a full-polarimetric covariance, for a declared transmit handedness |

**Terms added at the architecture review**

| Term | Meaning here |
| --- | --- |
| Release bundle | The versioned, checksummed set of files that Python produces and the web reads (8.4) |
| Mock bundle | A schema-valid fake bundle for building the web before real data exists; never shipped |
| COG | Cloud-Optimized GeoTIFF: a GeoTIFF laid out so a client can fetch only the tiles it needs by HTTP range request |
| AEQD | Azimuthal equidistant projection centred on a window; distances from the centre are true |
| Encoder | The network that turns a 64 × 64 elevation window into a 256-value embedding |
| Embedding | The encoder's output; nearby embeddings mean similar windows to the model |
| VICReg | A self-supervised loss that makes two views of the same window agree while keeping embeddings varied and decorrelated |
| Gradient reversal (DANN) | A layer that flips gradients from a body classifier, pushing the encoder to hide which body a window came from |
| Probe | A simple model (ridge or logistic regression) trained on frozen embeddings to test what they contain |
| Spatial block split | Train, validation and test sets made of whole geographic blocks, so nearby windows never cross splits |
| ONNX | Open model format; ONNX Runtime Web runs it in the browser on WASM or WebGPU |
| Parity test | A test that the Python and TypeScript code give the same numbers on the same inputs |
| Route handler | A Next.js file that answers HTTP requests; here it only hands requests to Hono |

## 19. Desk checks and readiness

On 14 November the research workspace cloned the three competing repositories, searched the literature and the official rules, and recomputed the test fixtures. The data archives themselves (PDS, PGDA, ASF and the AWS buckets) were blocked from that workspace, so no data file was opened. No gate is passed by these checks.

| Check | Result | Consequence |
| --- | --- | --- |
| Mini-RF polar CPR mosaic | File name, size, sample type, projection and missing-value handling found in a working competitor pipeline | Pilot input specified (section 5); confirm against the label |
| Polarimetry beyond CPR | A 2026 study resolves CPR ambiguity with SC, OC and m-χ components | Optional observables added (6.2, D18) |
| Independent implementation | polsartools (GPL-3.0) imports UAVSAR products, synthesizes compact polarimetry with a declared handedness, and computes m-χ | Test oracle for G1 (D19) |
| Holuhraun reference | Published UAVSAR CPR study confirmed; facies overlap of 88–89% | G3 check and tie expectations added (D20) |
| Other UAVSAR coverage | Not listable from the workspace | Prioritized region list for the pilot (section 5) |
| Competing projects | Licences checked; ShadowAstra now uses Mini-RF; SolMate's 100 m result confirmed from its own notes | Positioning updated (section 17); reuse policy (section 20) |
| Official rules | Open-source licence and citation of every source required; originality confirmation on the form | Release checklist and form contents (section 14) |
| Fixture arithmetic | All ten fixtures recomputed and consistent | G1 remains `NOT_RUN` until the team's code passes them |

### Readiness estimate

These are judgment calls, not measurements.

| Area | Before the checks | After | Why |
| --- | --- | --- | --- |
| Architecture, schemas, tests, task order | 9/10 | 9/10 | Unchanged |
| Radar conversion | 8/10 | 8.5/10 | An independent library oracle exists; the handedness term should be small for natural ground and is now measured per window |
| Reading real files | 6/10 | 7/10 | The Mini-RF mosaic layout and a working UAVSAR reader are known |
| Enough Earth candidates | 5/10 | 5/10 | Holuhraun confirmed; other coverage still unchecked |
| Mode A in time | 3/10 | 3/10 | Still depends on PRADAN approval |
| A complete, honest submission | 8/10 | 8.5/10 | Form fields and licence rules known |

**Overall: about 8.5 out of 10 that this document is enough to build the product from zero.** The largest remaining risk is UAVSAR coverage, which only the first hour of the pilot can settle.

**Readiness after the architecture review: 9/10 for building from an empty repository.** The repository layout, Python–web contract, API, routes, live-probe flow, model, training plan, evaluation, promotion rule, gates, backlog, owners, schedule and commands are all specified. The remaining risk is empirical rather than missing specification: data access and CORS (G0), whether the encoder passes G5 (the product ships either way), and the local judging cutoff.

## 20. Reuse and licensing

- **Project licence:** add an OSI-approved licence at hour 0. Apache-2.0 is a reasonable default; choose GPL-3.0 only if polsartools is imported by shipped code.
- **Competing teams' code:** not copied (D17), even where the licence would allow it. Cite their projects as related work.

| Repository | Licence | What may be used |
| --- | --- | --- |
| ShadowAstra Atlas | MIT | Ideas and pointers to public NASA products, verified and cited at the source; its open site dataset (CC BY 4.0) as cited context |
| SolMate | Apache-2.0 with a NOTICE file | Ideas and published validation results, cited |
| EarthTwin | No licence file (all rights reserved) | Ideas only, cited |

| Library | Licence | Use |
| --- | --- | --- |
| NumPy, SciPy, pandas, scikit-learn | BSD-3-Clause | Runtime |
| rasterio | BSD-3-Clause | Runtime |
| GDAL, pyproj | MIT | Runtime |
| LightGBM (only for T8) | MIT | Runtime, optional |
| polsartools | GPL-3.0 | Tests and development only (D19) |
| uavsar\_pytools | Not checked | Check before use |

Every data product, library, paper and AI tool used is cited in the submission, as the terms require.

**Added at the architecture review.** Code is released under Apache-2.0. The Copernicus DEM requires its attribution text on `/data` and in the README; ESA WorldCover needs CC BY 4.0 attribution; USGS CTX DTMs are CC0 with a requested citation; LOLA, Mini-RF and the crater catalogues are cited per product. JavaScript and Python dependencies must be permissive (MIT, BSD, Apache-2.0), checked in CI with `license-checker` and `pip-licenses`. The ImageNet ResNet-18 and DINO ViT-S/8 baselines are used only for evaluation, under their published licences, and are never shipped.

## 22. Research paper

### 22.1 Goal and model

A technical paper in the form and density of Vaswani et al. (2017), "Attention Is All You Need": a short main text with an exact method section, a training section with hardware and schedule, results tables against baselines, a model-variations table and appendix visualisations. Its subject is the encoder and the data; the product appears only in the introduction and conclusion. The build pattern comes from the team's own medlyn paper (`Julian-AT/medlyn/paper`): arXiv-style one-column LaTeX, natbib, TikZ and pgfplots figures that read generated number macros, a prose linter, a reference-verification record and a Tectonic build.

- **Working title:** "Terraluna: Body-Invariant Terrain Embeddings for Finding Earth Analogs of Lunar and Martian Sites". The final title is set after G5 and claims nothing the results do not show.
- **Length:** 9 to 11 pages plus appendices.
- **Authors:** the team members who agree to be listed. Claude and Codex are tools, not authors, and are disclosed in the acknowledgements and the AI-use statement.

### 22.2 Structure

| Section | Content | Analogue in Vaswani et al. |
| --- | --- | --- |
| Abstract | At most 200 words; every number is a macro | Abstract |
| 1 Introduction | The analog problem; why second-order roughness statistics are not enough; three contributions stated as measured results | 1 |
| 2 Background | Planetary roughness statistics (Shepard et al. 2001; Rosenburg et al. 2011; Kreslavsky et al. 2013; Kreslavsky and Head 2000 for Mars); radar CPR context; quantitative analog-site studies; self-supervised and domain-adversarial learning; planetary representation models (LunarFM, topo2vec, GEAR) | 2 |
| 3 Data | Moon, Mars and Earth products; window construction and resampler; masks; spatial-block splits; Table 1 data inventory; split-block maps | 5.1 |
| 4 Physics fingerprint | Structure function, H and distances with equations; nu(L) curves per body against published ranges | — |
| 5 Model architecture | Figure 1 architecture in TikZ; VICReg, physics and gradient-reversal losses as equations | 3 |
| 6 Why learn a terrain embedding | Table 2 comparing fingerprint, ImageNet ResNet-18, DINO and our encoder: what each encodes, invariances, parameters, cost per window, browser feasibility | 4 (Table 1) |
| 7 Training | Data and batching; hardware and schedule with measured GPU hours; optimiser; augmentation and regularisation | 5 |
| 8 Results | 8.1 probes (Table 3); 8.2 cross-body retrieval per target with Earth shortlists and hillshade pairs; 8.3 model variations (Table 5); 8.4 held-out body; 8.5 radar context for the Moon target | 6 (Table 3 for variations) |
| 9 Limitations | Scale range, surface-model vegetation, CTX artefacts, no contact-scale claims, the G5 outcome | — |
| 10 Conclusion | Results and what would test them in the field | 7 |
| Appendix A | Data analysis (22.3) | — |
| Appendix B | Embedding visualisations: UMAP panels, neighbour galleries, failure cases | Attention visualisations |
| Appendix C | Exploratory quantum experiment, only if section 24 ran | — |

### 22.3 Data analysis the paper must contain

- **Per body:** distributions of log nu(L) at every lag (relief and texture), H and valid fraction, with medians, interquartile ranges and block-bootstrap intervals.
- **Scale behaviour:** deviograms with fitted H and a test for a breakover near 1 km, which Rosenburg et al. (2011) report for LOLA. Compare the south-polar H with their published medians (0.95 in highlands, 0.76 in maria) as a sanity check, noting the different baselines.
- **Mars:** CTX-derived roughness set against published MOLA-based roughness patterns (Kreslavsky and Head 2000), and the effect of the CTX QA threshold on every distribution.
- **Earth:** roughness by WorldCover class and climate zone; the effect of the surface-model mask; the declared analog regions against the global background.
- **Targets:** each target window's percentile in each body's distribution, with clone or scenario spread.
- **Across bodies:** overlap of the three distributions in fingerprint space and in embedding space, with and without gradient reversal.
- Every number and figure is generated from `release/<bundle_version>/report/` and `analysis/*.json`; nothing is typed by hand.

### 22.4 Building on prior work

- Definitions follow Shepard et al. (2001) (RMS deviation, deviogram, Hurst exponent) and Rosenburg et al. (2011) (baselines, breakover). Evaluation follows Vaswani et al. (2017) (variations table, training cost). Model reporting follows Mitchell et al. (2019).
- Reuse definitions, notation and evaluation designs with citation; never reuse sentences. The overlap check in 22.6 enforces this.
- The citation agent finds every reference at a primary source (publisher, arXiv, NASA ADS) and records it in `paper/reference-verification.json`, as medlyn does. Aim for 35 to 60 references, each cited at a specific claim.

### 22.5 Figures and visual style

- **Diagrams** (Figure 1 and any other): TikZ from one shared `figures/styles.tex`, flat pastel fills with thin dark outlines in the manner of the Transformer paper's Figure 1. No icons, gradients, 3D effects or emoji.
- **Plots:** pgfplots reading generated CSV and macros, or matplotlib with the PGF backend and Computer Modern fonts, saved as vector PDF; colour-blind-safe palettes; axes with units.
- **Maps and hillshades:** rendered from the real DEMs by `paper/scripts/figures.py`, with scale bar, orientation, coordinates and product ID in the caption.
- **Never used:** AI-generated images, model-drawn illustrations, stock imagery, or app screenshots as scientific figures.
- **Visual check:** a verifier agent renders every page to PNG with `pdftoppm` and inspects each figure for overlap, clipping, unreadable text and unlabelled axes before release.

### 22.6 Build, checks and tests

```
paper/
  main.tex, arxiv.sty, refs.bib, appendix-*.tex
  figures/*.tex               # TikZ and pgfplots sources; styles.tex
  generated/numbers.tex       # macros from scripts/numbers.py; never hand-edited
  generated/*.tex             # tables from report/*.json and analysis/*.json
  reference-verification.json # primary URL and verified fields per reference
  measurement-review.json     # which reviewer approved which numbers, with input hashes
  scripts/numbers.py, figures.py, lint_prose.py, overlap_check.py
  scripts/test_manuscript.py, test_numbers.py
  Makefile                    # make -C paper: numbers, figures, lint, overlap, tectonic
```

Every check below blocks the build:

1. `numbers.py` validates input hashes against the bundle manifest; a changed input fails the build rather than silently changing a number.
2. Prose lint: no dash punctuation; no banned words (delve, seamless, robust unless justified in a comment, leverage, cutting-edge, comprehensive, crucial, pivotal, showcase, innovative, harness, groundbreaking, game-changing, tapestry, testament, underscore, unlock, empower); no "it is important to note", rhetorical questions, bullet lists in the main body or bold inside sentences.
3. Manuscript tests: section order as in 22.2; every bibliography entry cited and present in `reference-verification.json`; no placeholders (TODO, XX, ??, lorem); no literal measurements in the results sections outside macros and generated tables; exactly the planned figures exist, all vector.
4. Overlap check: no run of 12 or more consecutive words shared with any cited abstract stored in the reference record.
5. Page render reviewed by the figures verifier.
6. Cross-model review of every section by Codex (section 23.4).

Output: `paper/main.pdf`, linked from `/paper` in the app and from the README. An arXiv submission is optional after the event; first-time submitters may need an endorsement, so check before promising it.

### 22.7 Paper schedule

| Elapsed time | Paper work |
| --- | --- |
| 2–8 h | Skeleton, styles, Background with verified references, Figure 1 |
| 8–18 h | Data, fingerprint and architecture sections; Appendix A analyses as soon as T5 data exist |
| 18–26 h | Results after the training freeze and G5; tables from `report/*.json` |
| 26–30 h | Full review cycle, page render, final PDF |

Cut order inside the paper: Appendix C, then the Appendix B galleries, then a shorter Background. Limitations and the honest G5 outcome are never cut.

## 23. Agent workflow and verification

### 23.1 Principle

Agents do the work, and someone other than the author checks it: first deterministic checks, then a fresh verifier agent that did not write the work, then a second model family (Codex with GPT-6.1 Sol), then a human at the checkpoints in 23.6.

### 23.2 Agent roster

Project subagents live in `.claude/agents/<name>.md` as Markdown with YAML frontmatter (`name`, `description`, `tools`, `model`); the main session delegates to them by description. `CLAUDE.md` at the repository root summarises this spec, the commands and the rules; `AGENTS.md` mirrors it for Codex.

| Agent | Owns | Tools | Writes to |
| --- | --- | --- | --- |
| orchestrator (main session) | Plan, task list, delegation, merges | All | Task list, merge commits |
| data-engineer | T1, T3 ingest, T5 data, tiles, bundle | Read, Edit, Write, Bash | `pipeline/`, `data/`, `release/` |
| science-analyst | T2, T4, T6, Appendix A analyses | Read, Edit, Write, Bash | `pipeline/`, `analysis/` |
| ml-engineer | T11–T13 | Read, Edit, Write, Bash | `ml/` |
| web-frontend | T7 | Read, Edit, Write, Bash | `apps/web/` |
| web-platform | T0, T10, T15, CI | Read, Edit, Write, Bash | `packages/`, `apps/web/server/`, `.github/` |
| paper-writer | Paper sections | Read, Edit, Write | `paper/*.tex` |
| paper-figures | TikZ, plots, rendered maps | Read, Edit, Write, Bash | `paper/figures/`, `paper/scripts/figures.py` |
| citation-checker | References and their verification | Read, Write, WebSearch, WebFetch | `paper/refs.bib`, `paper/reference-verification.json` |
| verifier (read-only) | Acceptance checks for every task; page renders | Read, Grep, Glob, Bash for tests only | `reviews/*.json` |
| claims-auditor (read-only) | Section 3 wording rules, evidence labels, overclaims in app, paper and pitch | Read, Grep, Glob | `reviews/*.json` |

### 23.3 Task loop

1. The orchestrator copies the task and its acceptance criteria from section 12 into the task list.
2. The owner agent works on a branch, writes the failing test first where section 12 asks, and runs `make test`.
3. The verifier agent, in a fresh context and read-only, reruns the checks against the acceptance criteria and writes `reviews/<task>-claude.json`.
4. Codex reviews the same change (23.4) and writes `reviews/<task>-codex.json`.
5. The owner fixes or rebuts every finding in the review file. An unresolved disagreement goes to a human.
6. CI green, then merge. No agent merges its own unreviewed work.

### 23.4 Cross-model review with Codex CLI

- **Code:** `codex exec -m gpt-6.1-sol -s read-only --output-schema reviews/schema.json -o reviews/<task>-codex.json "Review the diff against main for correctness against spec section <n>; report findings as JSON."` (or `codex exec review --base main` for a plain diff review).
- **Paper sections:** `codex exec -m gpt-6.1-sol -s read-only -o reviews/paper-<section>-codex.json "Check paper/<section>.tex against paper/generated/numbers.tex and spec section 7: every equation, every number, the support for every claim, and overlap with cited work."`
- Read-only is `codex exec`'s default sandbox; keep it for all reviews and wrap each run in `timeout 900`.
- Record the model ID and Codex version in every review file. If `gpt-6.1-sol` is unavailable on the account (staged rollout and ChatGPT-sign-in errors have been reported), use API-key sign-in or the next model Codex lists, and record the substitution.
- Coverage: every merged pull request, every paper section, and the numerical core (polarimetry, terrain, resampler) twice.

### 23.5 Review record

Each `reviews/*.json` holds the task ID; the reviewer (agent name, or model ID and Codex version); the inputs (commit SHA, file hashes); findings with severity, location, claim and evidence; the resolution (fixed in commit, rebutted with a reason, or escalated); and timestamps. The paper's `measurement-review.json` points to these files.

### 23.6 Human checkpoints

- G0: shipping mode and target windows.
- G5: reading the test blocks (once) and the verdict.
- Production deploy, Hugging Face publish, paper release and the submission.
- Any account, credential, payment or licence decision. Agents never type credentials.

### 23.7 Guardrails

- Agents never hand-edit numbers into the app, paper or pitch; they regenerate them.
- Agents never weaken a test, threshold or gate to make it pass; changing a threshold in `config/run.yaml` needs a human.
- No secrets in the repository, only environment variables. No destructive command on `data/` or `release/` without a human.
- Each agent run logs its commands and outcomes to `logs/agents/<date>/<agent>.jsonl`, so the AI-use disclosure and the paper's acknowledgements describe the process accurately.

## 24. Stretch: exploratory quantum experiment (Qiskit)

**Start condition:** a G6 dry run has passed, the paper results are frozen and at least 4 hours remain. Otherwise it is not started; nothing depends on it.

### 24.1 Experiments

- **Q1, quantum kernel probe:** PCA, fitted on training blocks, reduces the frozen embeddings to 6 to 8 components. A ZZ feature-map fidelity kernel (qiskit-machine-learning 0.9 on Qiskit 2.x, statevector simulator) feeds a kernel SVM for one small balanced task: crater-dense versus crater-sparse Moon test windows, at most 400 examples. Baselines: an RBF SVM and logistic regression on the same features and splits.
- **Q2, QAOA shortlist selection:** choose k of n Earth candidate windows (n ≤ 16) to maximise similarity to a target minus a penalty for windows in the same region, written as a QUBO. QAOA at depth 1 to 3 on the simulator, compared with exact enumeration and greedy selection; report the approximation ratio.
- **Optional hardware block:** one small kernel block or one QAOA instance on an IBM Quantum backend under a team member's Open Plan (free, up to 10 minutes per 28-day window), recording backend, shots, transpiled depth and runtime.

### 24.2 Reporting rules

- Appendix C only; never in the main results or the product ranking.
- Report kernel fidelity against the exact simulator, the classical baselines and the approximation ratio. Make no claim of quantum advantage, scaling or better analog selection.
- Pin `qiskit`, `qiskit-aer`, `qiskit-machine-learning` and `qiskit-ibm-runtime`. Implement QAOA directly on Qiskit primitives unless `qiskit-optimization` supports the pinned Qiskit 2.x at hour 0.
- The IBM account and its token belong to a team member; agents never create the account or handle the token.

## 25. Sources

The R-numbered sources were inspected during the external review on 14 November 2026; the additions were inspected during this team's own research. "Documented" does not mean that a sample was downloaded or a test run.

| ID | Source | Supports |
| --- | --- | --- |
| R01 | [Rivera-Valentín et al. 2026, empirical S-band polarimetry and rock abundance](https://doi.org/10.3847/PSJ/ae6fa4) | Non-monotonic CPR–rock relation |
| R02 | [Campbell & Shepard 1996, surface roughness and radar scattering](https://repository.si.edu/server/api/core/bitstreams/b0339db4-869f-44ec-bf8f-0e7a48711077/content) | Finite scaling ranges of roughness |
| R03 | [Angelopoulos & Bates, conformal prediction tutorial](https://arxiv.org/pdf/2107.07511) | Exchangeability and distribution-shift limits |
| R04 | [Mini-RF data product SIS](https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-2_3_5-bistatic-v3/lromrf_3xxx/document/mrflro_dp_sis.htm) | Stokes, SC, OC and CPR conventions |
| R05 | [UAVSAR PolSAR product format](https://uavsar.jpl.nasa.gov/science/documents/polsar-format.html) | Real and complex products, units, angles |
| R06 | [Neish et al. 2017, terrestrial analogues for lunar impact melt flows](https://www.jrskok.com/_files/ugd/9b32f3_881d06436c43458ca2a3413b21ada34c.pdf) | Covariance-to-circular conversion; Holuhraun reference values |
| R07 | [Campbell 2012, high circular polarization ratios](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2012JE004061) | Valid CPR above 1.5 |
| R08 | [ISRO release of Chandrayaan-2 polar products](https://www.isro.gov.in/Lunar_Polar_Region.html) | L-band polar mosaics and PRADAN route |
| R09 | [ShadowAstra repository](https://github.com/ABRUBAB/ShadowAstra_Atlas) | Mini-RF listed as a planned addition |
| R10 | [PDS catalog for the Mini-RF polar mosaic](https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-4-cdr-v1/lromrf_0001/catalog/mrflro_5_cdr_mosaic_v1_0_ds.cat) | Mosaic composition and sampling |
| R11 | [PGDA product 78](https://pgda.gsfc.nasa.gov/products/78) | Connecting Ridge Site01 terrain, errors and clones |
| R12 | [PGDA product 90](https://pgda.gsfc.nasa.gov/products/90) | 20 m south-polar terrain; effective resolution |
| R13 | [Earth Engine PALSAR yearly mosaic](https://developers.google.com/earth-engine/datasets/catalog/JAXA_ALOS_PALSAR_YEARLY_SAR_EPOCH) | DN conversion, incidence, epochs, QA |
| R14 | [RCM CEOS-ARD registry](https://registry.opendata.aws/rcm-ceos-ard/) | Open Canadian compact-polarimetric data |
| R15 | [Sentinel-1 global seasonal dataset](https://registry.opendata.aws/ebd-sentinel-1-global-coherence-backscatter/) | C-band metrics and coverage |
| R16 | [Copernicus DEM registry](https://registry.opendata.aws/copernicus-dem/) | Earth surface model properties |
| R17 | [Space Apps GO-Team discussion 1827](https://github.com/nasa/spaceapps/discussions/1827) | NASA-source eligibility and attribution |
| R18 | [Space Apps GO-Team discussion 1849](https://github.com/nasa/spaceapps/discussions/1849) | AI disclosure and watermarking |
| R19 | [Space Apps GO-Team discussion 1916](https://github.com/nasa/spaceapps/discussions/1916) and [2026 event page](https://www.spaceappschallenge.org/2026/) | Submission dates and timezone |
| R20 | [NASA Moon Base phases](https://www.nasa.gov/moonbase-phases/) | Connecting Ridge context; not base selection |
| R21 | [Official challenge page](https://www.spaceappschallenge.org/2026/challenges/permanent-moon-base-locations-and-mars/) | Challenge scope and resources |
| A1 | [PGDA south-pole site README](https://core2.gsfc.nasa.gov/PGDA/LOLA_5mpp/README) | Site01 file list, including ROI shapefiles |
| A2 | [Barker et al. 2021, improved LOLA maps for south-pole sites](https://doi.org/10.1016/j.pss.2020.105119) | Site01 error model and clones |
| A3 | [Fassett et al. 2024, Mini-RF orthorectification and topographic correction](https://doi.org/10.3847/PSJ/ad0a61) and [download page](https://lib.jhuapl.edu/papers/improved-orthorectification-and-empirical-reductio) | δCPR and δS1 context layers |
| A4 | [Space Apps participant FAQ](https://www.spaceappschallenge.org/resources/participant-faqs/) | Submit by the local event's ending time |
| A5 | [UAVSAR PolSAR collection in Earthdata](https://www.earthdata.nasa.gov/data/catalog/asf-uavsar-polsar-1) | NASA data access route |

### Further reading

| ID | Source | Supports |
| --- | --- | --- |
| B1 | [Tolometti et al. 2020, lava flow properties from radar](https://www.sciencedirect.com/science/article/abs/pii/S0032063319302958) | L-band CPR at Craters of the Moon |
| B2 | [Tolometti et al. 2022, Holuhraun lava types from radar and lidar](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2021JB023419) | Holuhraun context |
| B3 | [Surface texture of Martian lava flows, PSJ 2020](https://iopscience.iop.org/article/10.3847/PSJ/abbfac) | Mars extension |
| B4 | [Gao et al. 2023, Mini-RF CPR and rock abundance](https://doaj.org/article/ae960256726f4663b052e3f45dab3cd3) | Interpretation context |
| B5 | Fa & Cai 2013, CPR of lunar craters and implications for ice detection, JGR Planets | Incidence and rock effects |
| B6 | Carter et al. 2012, Mini-RF observations of impact melts, JGR Planets | Lunar melt CPR |
| B7 | [Barker et al. 2023, new view of the lunar south pole from LOLA](https://doi.org/10.3847/PSJ/acf3e1) | PGDA90 products |
| B8 | [Shukla et al. 2024, Mini-RF dielectric constant by deep learning](https://doi.org/10.3390/rs16173208) | Model caution |
| B9 | [Souyris et al. 2005, compact polarimetry](https://doi.org/10.1109/TGRS.2004.842486) | M1 assumed baseline |
| B10 | [Deng et al. 2023, quad-pol reconstruction from dual-pol](https://doi.org/10.3390/RS15174182) | M2/M3 background |
| B11 | [Zhang et al. 2022, pseudo quad-pol from compact-pol](https://doaj.org/article/666903bd664d43f994ba2590895edab4) | M2/M3 background |
| B12 | [Brisco et al. 2020, hybrid compact polarimetry on RCM](https://doi.org/10.3390/rs12203283) | RCM design |
| B13 | [LunarFM](https://arxiv.org/html/2607.22408), [NASA-IBM lunar foundation model](https://arxiv.org/pdf/2609.13283), [SomBench](https://arxiv.org/html/2609.13277v1) | Related ML, not used |
| B14 | [Global lunar boulder map, Eos summary of Aussel et al. 2025](https://eos.org/editor-highlights/nearly-94-million-boulders-mapped-on-the-moon-using-deep-learning) | Related ML, not used |
| B15 | [Copernicus DEM on AWS](https://registry.opendata.aws/copernicus-dem/) and [ESA WorldCover on AWS](https://registry.opendata.aws/esa-worldcover-vito) | Earth terrain and masks |
| B16 | [UAVSAR PolSAR format (JPL mirror)](https://uavdb.jpl.nasa.gov/science/documents/polsar-format.html) | `.grd` and `.ann` details |

### Added on 14 November

| ID | Source | Supports |
| --- | --- | --- |
| C1 | [Rivera-Valentín et al. 2026, PSJ 7, 144](https://experts.arizona.edu/en/publications/empirical-evaluation-of-s-band-radar-polarimetric-response-to-sur/) | CPR non-monotonic; m-χ and SC/OC resolve ambiguity |
| C2 | [Tolometti et al. 2022, Holuhraun facies with UAVSAR CPR](https://repository.arizona.edu/handle/10150/665980) | 88–89% CPR overlap; UAVSAR at Holuhraun |
| C3 | [Raney et al. 2012, m-χ decomposition](https://unpaywall.org/10.1109%2FIGARSS.2012.6352465) | Optional observables |
| C4 | [Raney et al. 2011, Mini-RF hybrid polarimetric architecture](https://typeset.io/pdf/the-lunar-mini-rf-radars-hybrid-polarimetric-architecture-3rd210wqzd.pdf) | Circular transmit, linear receive; Stokes-based products |
| C5 | [polsartools documentation, m-χ module](https://polsartools.readthedocs.io/en/latest/_modules/polsartools/polsar/cp/m_chi.html) | Test oracle |
| C6 | [Carter et al. 2012, Mini-RF impact melts](https://experts.arizona.edu/en/publications/initial-observations-of-lunar-impact-melts-and-ejecta-flows-with-/) | Melt CPR context range |
| C7 | [UAVSAR polarimetric calibration, Fore et al.](https://uavsar.jpl.nasa.gov/science/documents/uavsar_polcal_ieee.pdf) | Rosamond calibration array |
| C8 | [Holuhraun SAR monitoring, Frontiers 2018](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2018.00231/full) | UAVSAR in Iceland, May 2015 |
| C9 | [Space Apps 2026 terms and conditions](https://www.spaceappschallenge.org/legal/) | Open licence and citation rules |
| C10 | [Space Apps judging and awards guide](https://www.spaceappschallenge.org/resources/judging-awards-guide) | Originality confirmation |
| C11 | [ShadowAstra Mini-RF step](https://github.com/ABRUBAB/ShadowAstra_Atlas/blob/main/pipeline/s26_lend_minirf.py) and [SolMate validation notes](https://github.com/Tanbin-CS/solmate/blob/main/docs/VALIDATION.md) | Competitor status |
| C12 | [Mini-RF data availability for landing sites, EGU 2025](https://meetingorganizer.copernicus.org/EGU25/EGU25-14090.html) | Orthorectified products and 30 m radar rock abundance in development |
| C13 | [Chandrayaan-2 DFSAR data product user manual](https://archive.org/download/chandrayaan-2-high-resolution-images-of-the-moon/OtherDownloads/DFSAR/ch2_dfsar_user_manual_v1.0.pdf) | PDS4 product structure for Mode A |

**D. Added at the architecture review (architecture, Mars data, ML)**

| ID | Source | Used for |
| --- | --- | --- |
| D1 | [Hono: Next.js getting started](https://hono.dev/docs/getting-started/nextjs) | Mounting Hono in the App Router (8.5) |
| D2 | [Hono issue 4877: handle() types under Next 15](https://github.com/honojs/hono/issues/4877) | Known type gotcha (8.5) |
| D3 | [Vercel template: Hono and Next.js](https://vercel.com/templates/hono/hono-nextjs) | Reference setup |
| D4 | [Scalar: API reference for Hono](https://scalar.com/products/api-references/integrations/hono) | OpenAPI docs (8.5) |
| D5 | [Vercel: Python runtime](https://vercel.com/docs/functions/runtimes/python); [Vercel: FastAPI limits](https://vercel.com/docs/frameworks/backend/fastapi) | D22 |
| D6 | [Hugging Face forum: CPU Basic quota reports, 2026](https://discuss.huggingface.co/t/cpu-basic-spaces-hitting-quota-limits-despite-unlimited-hosting-inconsistent-behavior-with-restart-and-factory-rebuild/175681) | D22 |
| D7 | [Next.js 16.4 release](https://nextjs.org/blog/next-16-4) | Stack (8.2) |
| D8 | [onnxruntime-web](https://www.npmjs.com/package/onnxruntime-web) | Browser inference (8.7) |
| D9 | [deck.gl OrthographicView](https://deck.gl/docs/api-reference/core/orthographic-view); [non-geospatial TileLayer example (LROC WAC)](https://deck.gl/examples/tile-layer-non-geospatial) | D28 |
| D10 | [MapLibre roadmap: non-Mercator projection](https://maplibre.org/roadmap/maplibre-gl-js/non-mercator-projection) | D28 |
| D11 | [Copernicus DEM on AWS](https://registry.opendata.aws/copernicus-dem/); [bucket readme](https://copernicus-dem-30m.s3.amazonaws.com/readme.html) | Earth DEM, live probe |
| D12 | [USGS CTX DTMs documentation](https://stac.astrogeology.usgs.gov/docs/data/mars/ctxdtms); [STAC collection](https://stac.astrogeology.usgs.gov/api/collections/mro_ctx_controlled_usgs_dtms); [release note: 4,815 DTMs](https://openplanetary.discourse.group/t/new-usgs-derived-ctx-hirise-datasets-stac/626) | Mars targets and training |
| D13 | [USGS STAC tutorial with the Jezero search](https://stac.astrogeology.usgs.gov/docs/tutorials/mosaic_json) | Jezero box |
| D14 | [HRSC–MOLA blended DEM 200 m](https://astrogeology.usgs.gov/search/map/mars_mgs_mola_mex_hrsc_blended_dem_global_200m) | Mars context |
| D15 | [Robbins (2018) lunar crater database](https://astrogeology.usgs.gov/search/map/moon_crater_database_v1_robbins) | Morphology probe |
| D16 | [Robbins and Hynek (2012) Mars craters](https://doi.org/10.1029/2011JE003966) | Morphology probe |
| D17 | [NASA JPL: where astronauts should land on Mars (ice map)](https://www.jpl.nasa.gov/news/where-should-future-astronauts-land-on-mars-follow-the-water/) | Arcadia target rationale |
| D18 | [LunarFM (2026)](https://arxiv.org/pdf/2607.22408) | Positioning (11.1, 17) |
| D19 | [topo2vec](https://arxiv.org/pdf/2108.08870) | Related work |
| D20 | [GEAR analog recognition](https://arxiv.org/pdf/2603.18626) | Related work |
| D21 | [Qaidam Basin Mars analog selection](https://arxiv.org/pdf/2501.08584) | Related work |
| D22 | [CraterBench-R](https://arxiv.org/html/2604.06245v1) | Related work |
| D23 | [VICReg (Bardes et al. 2022)](https://arxiv.org/abs/2105.04906) | Objective (11.4) |
| D24 | [Domain-adversarial training (Ganin et al. 2016)](https://arxiv.org/abs/1505.07818) | Gradient reversal (11.4) |
| D25 | [Model cards (Mitchell et al. 2019)](https://arxiv.org/abs/1810.03993) | Report structure (11.9) |

**E. Added for the paper, the agent workflow and quantum**

| ID | Source | Used for |
| --- | --- | --- |
| E1 | [Vaswani et al. (2017), Attention Is All You Need](https://arxiv.org/abs/1706.03762) | Paper form and evaluation style (22) |
| E2 | [Team's medlyn paper folder](https://github.com/Julian-AT/medlyn/tree/main/paper) | Build pattern: macros, lint, reference verification, TikZ (22.6) |
| E3 | [Shepard et al. (2001), The roughness of natural terrain](https://doi.org/10.1029/2000JE001429) | RMS deviation, deviogram and Hurst definitions |
| E4 | [Rosenburg et al. (2011), Global surface slopes and roughness of the Moon from LOLA](https://thesis.library.caltech.edu/8408/13/Rosenburg_Margaret_2014_Ch2.pdf); [PGDA product 70](https://pgda.gsfc.nasa.gov/products/70) | Baselines, breakover, published H medians (22.3) |
| E5 | [Kreslavsky et al. (2013), Lunar topographic roughness maps from LOLA](https://cris.iucc.ac.il/en/publications/lunar-topographic-roughness-maps-from-lunar-orbiter-laser-altimet/) | Background, scale dependence |
| E6 | [Claude Code subagents](https://code.claude.com/docs/en/sub-agents) | Agent definitions (23.2) |
| E7 | [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive) | `codex exec`, read-only default, output files (23.4) |
| E8 | [Using GPT-6.1 Sol in Codex](https://flaviocopes.com/gpt-6-1-sol/) | Model ID and availability caveat (23.4) |
| E9 | [Qiskit Machine Learning release notes](https://qiskit-community.github.io/qiskit-machine-learning/release_notes.html) | Qiskit 2.x compatibility (24) |
| E10 | [IBM Quantum plans overview](https://quantum.cloud.ibm.com/docs/guides/plans-overview) | Open Plan limit (24) |
