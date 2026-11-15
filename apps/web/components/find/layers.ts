// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Earth map layers of the /find picker, as ShadowAstra lists them: NASA GIBS and the EOX
// Sentinel-2 mosaic (Web Mercator, hosts allowed by lib/headers.ts), plus the self-hosted NASA Blue
// Marble texture (equirectangular, this origin) that the map falls back to when tiles are refused.

export type Projection = 'mercator' | 'equirect';

export interface EarthLayer {
  id: string;
  name: string;
  instrument: string;
  provider: string;
  projection: Projection;
  /** Tile URL template, or a single equirectangular image for `image`. */
  url: string;
  image?: boolean;
  maxNativeZoom: number;
  link: string;
}

const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best';
/** A recent date with full MODIS coverage; the daily layers need an explicit date. */
const MODIS_DATE = '2026-09-01';

export const EARTH_LAYERS: readonly EarthLayer[] = [
  {
    id: 'earth-bluemarble-local',
    name: 'Blue Marble',
    instrument: 'NASA Blue Marble (self-hosted texture)',
    provider: 'NASA',
    projection: 'equirect',
    url: '/textures/earth_day_2k.webp',
    image: true,
    maxNativeZoom: 3,
    link: 'https://visibleearth.nasa.gov/collection/1484/blue-marble',
  },
  {
    id: 'earth-bluemarble',
    name: 'Blue Marble relief',
    instrument: 'Terra/Aqua MODIS composite + SRTM',
    provider: 'NASA GIBS',
    projection: 'mercator',
    url: `${GIBS}/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`,
    maxNativeZoom: 8,
    link: 'https://earthobservatory.nasa.gov/features/BlueMarble',
  },
  {
    id: 'earth-s2',
    name: 'Sentinel-2 cloudless',
    instrument: 'ESA Copernicus Sentinel-2 (2020 cloudless, EOX)',
    provider: 'ESA Copernicus · EOX',
    projection: 'mercator',
    url: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg',
    maxNativeZoom: 14,
    link: 'https://s2maps.eu',
  },
  {
    id: 'earth-modis',
    name: 'MODIS true colour',
    instrument: 'Terra MODIS corrected reflectance',
    provider: 'NASA GIBS',
    projection: 'mercator',
    url: `${GIBS}/MODIS_Terra_CorrectedReflectance_TrueColor/default/${MODIS_DATE}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`,
    maxNativeZoom: 9,
    link: 'https://modis.gsfc.nasa.gov/',
  },
  {
    id: 'earth-aster',
    name: 'ASTER hillshade',
    instrument: 'Terra ASTER global DEM',
    provider: 'NASA GIBS',
    projection: 'mercator',
    url: `${GIBS}/ASTER_GDEM_Greyscale_Shaded_Relief/default/GoogleMapsCompatible_Level12/{z}/{y}/{x}.jpg`,
    maxNativeZoom: 12,
    link: 'https://asterweb.jpl.nasa.gov/gdem.asp',
  },
];

export const DEFAULT_EARTH_LAYER = 'earth-bluemarble';
export const FALLBACK_EARTH_LAYER = 'earth-bluemarble-local';
