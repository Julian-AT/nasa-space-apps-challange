// @terraluna/core: the browser-side mirror of the Terraluna numerical contract (PRD 8.3, 8.8).
export {
  BODY_RADII_M,
  EARTH_RADIUS_M,
  GeometryError,
  MARS_RADIUS_M,
  MOON_RADIUS_M,
  aeqdInverse,
  bilinearResample,
  bodyRadiusM,
  geographicLookup,
  geographicToPixel,
  gridMesh,
  resampleWindow,
  windowGrid,
} from './geometry.ts';
export type { AeqdCentre, Body, GeographicLookup, ResampledGrid, SourceRaster, WindowGrid } from './geometry.ts';
export {
  DIRECTIONS,
  ISOTROPIC_RULES,
  TERRAIN_REASONS,
  TerrainError,
  VARIANT_NAMES,
  hurstFit,
  logNu,
  planeFit,
  structureFunction,
  terrainFingerprint,
} from './terrain.ts';
export type {
  DetrendPolicy,
  Direction,
  HurstFit,
  HurstReason,
  IsotropicRule,
  LagReason,
  PlaneFit,
  StructureFunction,
  TerrainConfig,
  TerrainFingerprint,
  TerrainVariant,
  VariantReason,
  WindowReason,
} from './terrain.ts';
export {
  ANALYSIS_GRID,
  ENCODER_GRID,
  PRD_TERRAIN_CONFIG,
  PROBE_MIN_VALID_FRACTION,
  PROBE_STOP_REASON,
  probeQuality,
  windowFingerprint,
} from './probe.ts';
export type { ProbeQuality } from './probe.ts';
export { MATCHING_REASONS, backgroundPercentile, familyPercentile, sharedLags, terrainDistance } from './matching.ts';
export type { BackgroundHistogram, DistanceVariant, Family, MatchingReason, Outcome } from './matching.ts';
export {
  ELEVATION_SCALE_M,
  ENCODER_CHANNELS,
  ENCODER_PIXEL_M,
  ENCODER_WINDOW_PX,
  INVALID_FILL,
  SLOPE_SCALE_DEG,
  encoderChannels,
  toOnnxInput,
} from './encoder.ts';
export type { EncoderChannels } from './encoder.ts';
