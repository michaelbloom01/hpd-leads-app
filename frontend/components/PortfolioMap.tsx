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

const NYC_BOUNDS = {
  minLat: 40.45,
  maxLat: 40.95,
  minLon: -74.3,
  maxLon: -73.65,
};

function isNycCoordinate(latitude: number | null, longitude: number | null): boolean {
  if (latitude === null || longitude === null) return false;
  return (
    latitude >= NYC_BOUNDS.minLat &&
    latitude <= NYC_BOUNDS.maxLat &&
    longitude >= NYC_BOUNDS.minLon &&
    longitude <= NYC_BOUNDS.maxLon
  );
}

function coordinateKey(latitude: number, longitude: number): string {
  return `${latitude.toFixed(5)},${longitude.toFixed(5)}`;
}

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
    const invalidateTimer = window.setTimeout(() => {
      map.invalidateSize();
    }, 80);
    if (positions.length > 0) {
      const bounds = L.latLngBounds(positions.map(p => L.latLng(p[0], p[1])));
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
    }
    return () => window.clearTimeout(invalidateTimer);
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
  const [rejectedStoredCount, setRejectedStoredCount] = useState(0);
  const [tileErrorCount, setTileErrorCount] = useState(0);

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
      setTileErrorCount(0);
      if (!normalizedBuildings.length) {
        setPositions([]);
        setSkippedCount(0);
        setRejectedStoredCount(0);
        setIsResolving(false);
        return;
      }

      const resolved: MapPosition[] = [];
      let skipped = 0;
      let rejectedStored = 0;
      const storedCoordinateCounts = new Map<string, number>();
      normalizedBuildings.forEach(({ latitude, longitude }) => {
        if (isNycCoordinate(latitude, longitude)) {
          const key = coordinateKey(latitude, longitude);
          storedCoordinateCounts.set(key, (storedCoordinateCounts.get(key) || 0) + 1);
        }
      });
      const storedCandidateCount = Array.from(storedCoordinateCounts.values()).reduce((sum, count) => sum + count, 0);
      const uniqueStoredCoordinateCount = storedCoordinateCounts.size;
      const hasCollapsedStoredCoordinates =
        storedCandidateCount >= 5 &&
        uniqueStoredCoordinateCount <= Math.max(1, Math.ceil(storedCandidateCount * 0.1));
      const overloadedCoordinateLimit = Math.max(6, Math.ceil(storedCandidateCount * 0.25));
      const publishProgress = () => {
        if (cancelled) return;
        setPositions([...resolved]);
        setSkippedCount(skipped);
        setRejectedStoredCount(rejectedStored);
      };
      for (let i = 0; i < normalizedBuildings.length; i++) {
        const { addr, buildingBoro, latitude, longitude, coordinateSource, coordinatePrecision } = normalizedBuildings[i];
        const coordinateIsUsable =
          latitude !== null &&
          longitude !== null &&
          isNycCoordinate(latitude, longitude) &&
          !hasCollapsedStoredCoordinates &&
          (storedCoordinateCounts.get(coordinateKey(latitude, longitude)) || 0) <= overloadedCoordinateLimit;
        if (coordinateIsUsable) {
          resolved.push({
            position: [latitude, longitude],
            address: addr,
            source: coordinateSource || 'stored',
            precision: coordinatePrecision,
            persisted: true,
          });
          if (resolved.length === 1 || resolved.length % 10 === 0) publishProgress();
          continue;
        }
        if (latitude !== null || longitude !== null) {
          rejectedStored += 1;
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
          if (resolved.length === 1 || resolved.length % 10 === 0) publishProgress();
        } else {
          skipped += 1;
        }
      }

      if (!cancelled) setPositions(resolved);
      if (!cancelled) setSkippedCount(skipped);
      if (!cancelled) setRejectedStoredCount(rejectedStored);
      if (!cancelled) setIsResolving(false);
    };
    resolvePositions();
    return () => {
      cancelled = true;
    };
  }, [normalizedBuildings, allowClientGeocodingFallback]);

  if (isResolving && positions.length === 0) {
    return (
      <div style={{ height, width: '100%' }} className="rounded-lg border border-gray-200 bg-gray-50 flex items-center justify-center text-xs text-gray-500">
        Loading map…
      </div>
    );
  }
  if (positions.length === 0) {
    if (skippedCount > 0) {
      const fallbackLinks = normalizedBuildings.slice(0, 5);
      return (
        <div style={{ minHeight: height, width: '100%' }} className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
          <div className="font-semibold">Map markers are not available yet</div>
          <p className="mt-1">
            This portfolio has addresses but no stored coordinates available to the embedded map right now.
          </p>
          {fallbackLinks.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {fallbackLinks.map((building) => (
                <a
                  key={`${building.addr}-${building.buildingBoro}`}
                  href={`https://www.google.com/maps/search/${encodeURIComponent(`${building.addr}, ${building.buildingBoro || 'New York'}, NY`)}`}
                  target="_blank"
                  rel="noopener"
                  className="rounded border border-amber-300 bg-white px-2 py-1 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
                >
                  Map {building.addr}
                </a>
              ))}
            </div>
          )}
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
  const positionCountByKey = positions.reduce((acc, p) => {
    const key = coordinateKey(p.position[0], p.position[1]);
    acc.set(key, (acc.get(key) || 0) + 1);
    return acc;
  }, new Map<string, number>());
  const visiblePositions = positions.filter((p, index) => {
    const key = coordinateKey(p.position[0], p.position[1]);
    const duplicateIndex = positions.slice(0, index).filter((prior) => coordinateKey(prior.position[0], prior.position[1]) === key).length;
    return duplicateIndex < 8;
  });
  const hiddenDuplicateCount = positions.length - visiblePositions.length;

  return (
    <div style={{ width: '100%' }} className="space-y-2">
      <div className={`mb-2 rounded-lg px-3 py-2 text-[11px] ${(skippedCount > 0 || rejectedStoredCount > 0 || tileErrorCount > 0) ? 'border border-amber-200 bg-amber-50 text-amber-700' : 'border border-gray-200 bg-gray-50 text-gray-600'}`}>
        Marker provenance: {persistedCount} stored, {geocodedCount} geocoded.
        {rejectedStoredCount > 0 ? ` ${rejectedStoredCount} stored coordinate${rejectedStoredCount === 1 ? '' : 's'} were ignored because they were outside NYC or collapsed many buildings onto one point.` : ''}
        {skippedCount > 0 ? ` ${skippedCount} building${skippedCount === 1 ? '' : 's'} are omitted until usable coordinates are available.` : geocodedCount > 0 ? ' Stored coordinates are preferred; remaining markers were derived from public geocoding services.' : ' All visible markers are using persisted building coordinates.'}
        {hiddenDuplicateCount > 0 ? ` ${hiddenDuplicateCount} duplicate marker${hiddenDuplicateCount === 1 ? '' : 's'} are summarized in popups to keep the map readable.` : ''}
        {tileErrorCount > 0 ? ' Some map tiles failed to load; markers and links remain usable.' : ''}
      </div>
      <div style={{ height, width: '100%' }} className="rounded-lg overflow-hidden">
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
            eventHandlers={{
              tileerror: () => setTileErrorCount((count) => count + 1),
            }}
          />
          <FitBounds positions={positions.map(p => p.position)} />
          {visiblePositions.map((p, i) => (
            <Marker key={i} position={p.position} icon={smallMarker}>
              <Popup>
                <span className="text-xs">
                  {p.address}
                  {` (${p.source}${p.precision ? `, ${p.precision}` : ''})`}
                  {(positionCountByKey.get(coordinateKey(p.position[0], p.position[1])) || 0) > 1
                    ? `; ${(positionCountByKey.get(coordinateKey(p.position[0], p.position[1])) || 0) - 1} more at this point`
                    : ''}
                </span>
              </Popup>
            </Marker>
          ))}
        </MapContainer>
      </div>
    </div>
  );
};

export default PortfolioMap;
