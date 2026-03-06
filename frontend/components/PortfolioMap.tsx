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

// Small blue circle marker for density
const smallMarker = new L.Icon({
  iconUrl: 'data:image/svg+xml;base64,' + btoa(`<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><circle cx="6" cy="6" r="5" fill="#3b82f6" stroke="#1e3a5f" stroke-width="1.5"/></svg>`),
  iconSize: [12, 12],
  iconAnchor: [6, 6],
  popupAnchor: [0, -6],
});

// NYC borough approximate coordinates for geocoding fallback
const NYC_BOROUGH_COORDS: Record<string, [number, number]> = {
  'MANHATTAN': [40.7831, -73.9712],
  'BROOKLYN': [40.6782, -73.9442],
  'QUEENS': [40.7282, -73.7949],
  'BRONX': [40.8448, -73.8648],
  'STATEN ISLAND': [40.5795, -74.1502],
};

// Simple address to approximate coordinates using street number + borough
function approximateCoords(address: string, boro?: string): [number, number] | null {
  const boroKey = boro?.toUpperCase() || '';
  const base = NYC_BOROUGH_COORDS[boroKey] || [40.7128, -73.95];
  
  // Use address hash to spread markers within the borough area
  let hash = 0;
  for (let i = 0; i < address.length; i++) {
    hash = ((hash << 5) - hash) + address.charCodeAt(i);
    hash |= 0;
  }
  
  // Spread within ~0.03 degrees (~2 miles) of borough center
  const latOffset = ((hash & 0xFF) / 255 - 0.5) * 0.06;
  const lngOffset = (((hash >> 8) & 0xFF) / 255 - 0.5) * 0.06;
  
  return [base[0] + latOffset, base[1] + lngOffset];
}

type GeocodeResult = { coords: [number, number]; source: 'planninglabs' | 'nominatim' } | null;
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

export type BuildingMapEntry = string | { address: string; borough?: string };

interface PortfolioMapProps {
  buildings: (string | BuildingMapEntry)[];
  boro?: string;
  boros?: string[];
  height?: string;
}

const PortfolioMap: React.FC<PortfolioMapProps> = ({ buildings, boro, boros, height = '250px' }) => {
  const mapRef = useRef<any>(null);
  const [isResolving, setIsResolving] = useState(false);
  const [positions, setPositions] = useState<Array<{ position: [number, number]; address: string; approximate: boolean; source: 'planninglabs' | 'nominatim' | 'borough_fallback' }>>([]);

  const normalizedBuildings = useMemo(() => {
    const seen = new Set<string>();
    const rows = (buildings || []).slice(0, 150).map((building) => {
      const addr = typeof building === 'string' ? building : building.address;
      const entryBoro = typeof building === 'object' && building.borough ? building.borough : undefined;
      const buildingBoro = entryBoro || boro || '';
      return { addr: (addr || '').trim(), buildingBoro };
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

      const resolved: Array<{ position: [number, number]; address: string; approximate: boolean; source: 'planninglabs' | 'nominatim' | 'borough_fallback' }> = [];
      for (let i = 0; i < normalizedBuildings.length; i++) {
        const { addr, buildingBoro } = normalizedBuildings[i];
        const geocoded = await geocodeAddress(addr, buildingBoro);
        let approximate = !geocoded;
        let source: 'planninglabs' | 'nominatim' | 'borough_fallback' = geocoded?.source || 'borough_fallback';
        let coords = geocoded?.coords || null;
        if (!coords) coords = approximateCoords(addr, buildingBoro);
        if (coords) resolved.push({ position: coords, address: addr, approximate, source });
      }

      if (!cancelled) setPositions(resolved);
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
  if (positions.length === 0) return null;

  const center: [number, number] = positions.length > 0 
    ? [
        positions.reduce((sum, p) => sum + p.position[0], 0) / positions.length,
        positions.reduce((sum, p) => sum + p.position[1], 0) / positions.length,
      ]
    : [40.7128, -73.95];

  return (
    <div style={{ height, width: '100%' }} className="rounded-lg overflow-hidden">
      {positions.some((p) => p.approximate) && (
        <div className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
          Marker provenance: Planning Labs and OpenStreetMap geocoders are used first; borough-center fallback markers are approximate.
        </div>
      )}
      {!positions.some((p) => p.approximate) && (
        <div className="mb-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-[11px] text-gray-600">
          Marker provenance: positions are geocoded from public map services and should be treated as directional, not survey-grade coordinates.
        </div>
      )}
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
                {p.approximate ? ' (approximate borough fallback)' : ` (${p.source})`}
              </span>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
};

export default PortfolioMap;
