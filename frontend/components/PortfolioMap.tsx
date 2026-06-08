import React, { useMemo, useEffect, useRef, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Fix Leaflet default marker icons in bundled builds
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// Compact blue marker with enough hit area for portfolio maps.
const smallMarker = new L.Icon({
  iconUrl: 'data:image/svg+xml;base64,' + btoa(`<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18"><circle cx="9" cy="9" r="7" fill="#2563eb" stroke="#ffffff" stroke-width="3"/><circle cx="9" cy="9" r="7" fill="none" stroke="#1e3a8a" stroke-width="1"/></svg>`),
  iconSize: [18, 18],
  iconAnchor: [9, 9],
  popupAnchor: [0, -9],
});

type GeocodeResult = { coords: [number, number]; source: 'planninglabs' | 'nominatim' } | null;
type MapPosition = {
  position: [number, number];
  address: string;
  source: string;
  precision?: string | null;
  persisted: boolean;
};
const geocodeCache = new Map<string, GeocodeResult>();

async function geocodeAddress(address: string, borough?: string): Promise<GeocodeResult> {
  const query = `${address}, ${borough || 'New York'}, NY`;
  const cacheKey = query.toUpperCase();
  if (geocodeCache.has(cacheKey)) return geocodeCache.get(cacheKey) ?? null;

  try {
    // NYC Planning geosearch is usually more accurate for NYC addresses.
    const geosearchUrl = new URL('https://geosearch.planninglabs.nyc/v2/search');
    geosearchUrl.searchParams.set('text', query);
    const geoResp = await fetch(geosearchUrl.toString(), {
      headers: { Accept: 'application/json' },
    });
    if (geoResp.ok) {
      const payload = await geoResp.json() as {
        features?: Array<{ geometry?: { coordinates?: [number, number] } }>;
      };
      const coordinates = payload?.features?.[0]?.geometry?.coordinates;
      if (Array.isArray(coordinates) && coordinates.length >= 2) {
        const lon = Number(coordinates[0]);
        const lat = Number(coordinates[1]);
        if (Number.isFinite(lat) && Number.isFinite(lon)) {
          const result = { coords: [lat, lon] as [number, number], source: 'planninglabs' as const };
          geocodeCache.set(cacheKey, result);
          return result;
        }
      }
    }

    // Secondary fallback: Nominatim.
    const url = new URL('https://nominatim.openstreetmap.org/search');
    url.searchParams.set('q', query);
    url.searchParams.set('format', 'jsonv2');
    url.searchParams.set('limit', '1');
    url.searchParams.set('countrycodes', 'us');

    const response = await fetch(url.toString(), {
      headers: {
        Accept: 'application/json',
      },
    });
    if (!response.ok) {
      geocodeCache.set(cacheKey, null);
      return null;
    }
    const payload = await response.json() as Array<{ lat: string; lon: string }>;
    const first = payload?.[0];
    if (!first) {
      geocodeCache.set(cacheKey, null);
      return null;
    }
    const lat = Number(first.lat);
    const lon = Number(first.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      geocodeCache.set(cacheKey, null);
      return null;
    }
    const result = { coords: [lat, lon] as [number, number], source: 'nominatim' as const };
    geocodeCache.set(cacheKey, result);
    return result;
  } catch {
    geocodeCache.set(cacheKey, null);
    return null;
  }
}

// Auto-fit bounds component
function FitBounds({ positions }: { positions: [number, number][] }) {
  const map = useMap();
  
  useEffect(() => {
    if (positions.length > 0) {
      const bounds = L.latLngBounds(positions.map(p => L.latLng(p[0], p[1])));
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
    }
  }, [map, positions]);
  
  return null;
}

export type BuildingMapEntry =
  | string
  | {
      address: string;
      borough?: string;
      latitude?: number | null;
      longitude?: number | null;
      coordinate_source?: string | null;
      coordinate_precision?: string | null;
    };

interface PortfolioMapProps {
  buildings: (string | BuildingMapEntry)[];
  boro?: string;
  boros?: string[];
  height?: string;
  allowClientGeocodingFallback?: boolean;
}

const PortfolioMap: React.FC<PortfolioMapProps> = ({
  buildings,
  boro,
  boros,
  height = '250px',
  allowClientGeocodingFallback = false,
}) => {
  const mapRef = useRef<any>(null);
  const [isResolving, setIsResolving] = useState(false);
  const [positions, setPositions] = useState<MapPosition[]>([]);
  const [skippedCount, setSkippedCount] = useState(0);

  const normalizedBuildings = useMemo(() => {
    const seen = new Set<string>();
    const rows = (buildings || []).slice(0, 150).map((building) => {
      const addr = typeof building === 'string' ? building : building.address;
      const entryBoro = typeof building === 'object' && building.borough ? building.borough : undefined;
      const buildingBoro = entryBoro || boro || '';
      const latitude = typeof building === 'object' ? Number(building.latitude) : NaN;
      const longitude = typeof building === 'object' ? Number(building.longitude) : NaN;
      return {
        addr: (addr || '').trim(),
        buildingBoro,
        latitude: Number.isFinite(latitude) ? latitude : null,
        longitude: Number.isFinite(longitude) ? longitude : null,
        coordinateSource: typeof building === 'object' ? building.coordinate_source ?? null : null,
        coordinatePrecision: typeof building === 'object' ? building.coordinate_precision ?? null : null,
      };
    });
    return rows.filter(({ addr, buildingBoro }) => {
      if (!addr) return false;
      const key = `${addr.toUpperCase()}|${(buildingBoro || '').toUpperCase()}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [buildings, boro, boros]);

  useEffect(() => {
    let cancelled = false;
    const resolvePositions = async () => {
      setIsResolving(true);
      if (!normalizedBuildings.length) {
        setPositions([]);
        setIsResolving(false);
        return;
      }

      const resolved: MapPosition[] = [];
      let skipped = 0;
      for (let i = 0; i < normalizedBuildings.length; i++) {
        const { addr, buildingBoro, latitude, longitude, coordinateSource, coordinatePrecision } = normalizedBuildings[i];
        if (latitude !== null && longitude !== null) {
          resolved.push({
            position: [latitude, longitude],
            address: addr,
            source: coordinateSource || 'stored',
            precision: coordinatePrecision,
            persisted: true,
          });
          continue;
        }
        if (!allowClientGeocodingFallback) {
          skipped += 1;
          continue;
        }
        const geocoded = await geocodeAddress(addr, buildingBoro);
        if (geocoded?.coords) {
          resolved.push({
            position: geocoded.coords,
            address: addr,
            source: geocoded.source,
            precision: null,
            persisted: false,
          });
        } else {
          skipped += 1;
        }
      }

      if (!cancelled) setPositions(resolved);
      if (!cancelled) setSkippedCount(skipped);
      if (!cancelled) setIsResolving(false);
    };
    resolvePositions();
    return () => {
      cancelled = true;
    };
  }, [normalizedBuildings]);

  if (isResolving && positions.length === 0) {
    return (
      <div style={{ height, width: '100%' }} className="rounded-lg border border-gray-200 bg-gray-50 flex items-center justify-center text-xs text-gray-500">
        Loading map…
      </div>
    );
  }
  if (positions.length === 0) {
    if (skippedCount > 0) {
      return (
        <div style={{ height, width: '100%' }} className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          Map unavailable right now because this portfolio does not yet have persisted building coordinates. Run the coordinate sync job to materialize stored markers instead of relying on approximate browser geocoding.
        </div>
      );
    }
    return null;
  }

  const center: [number, number] = positions.length > 0 
    ? [
        positions.reduce((sum, p) => sum + p.position[0], 0) / positions.length,
        positions.reduce((sum, p) => sum + p.position[1], 0) / positions.length,
      ]
    : [40.7128, -73.95];
  const persistedCount = positions.filter((p) => p.persisted).length;
  const geocodedCount = positions.length - persistedCount;

  return (
    <div style={{ height, width: '100%' }} className="rounded-lg overflow-hidden">
      <div className={`mb-2 rounded-lg px-3 py-2 text-[11px] ${skippedCount > 0 ? 'border border-amber-200 bg-amber-50 text-amber-700' : 'border border-gray-200 bg-gray-50 text-gray-600'}`}>
        Marker provenance: {persistedCount} stored, {geocodedCount} geocoded. {skippedCount > 0 ? `${skippedCount} building${skippedCount === 1 ? '' : 's'} are omitted until persisted coordinates are available.` : geocodedCount > 0 ? 'Stored coordinates are preferred; remaining markers were derived from public geocoding services.' : 'All visible markers are using persisted building coordinates.'}
      </div>
      <MapContainer
        ref={mapRef}
        center={center}
        zoom={11}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom={false}
        attributionControl={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds positions={positions.map(p => p.position)} />
        {positions.map((p, i) => (
          <Marker key={i} position={p.position} icon={smallMarker}>
            <Popup>
              <span className="text-xs">
                {p.address}
                {` (${p.source}${p.precision ? `, ${p.precision}` : ''})`}
              </span>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
};

export default PortfolioMap;
