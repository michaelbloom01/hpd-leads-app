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

type GeocodeResult = [number, number] | null;
const geocodeCache = new Map<string, GeocodeResult>();

async function geocodeAddress(address: string, borough?: string): Promise<GeocodeResult> {
  const query = `${address}, ${borough || 'New York'}, NY`;
  const cacheKey = query.toUpperCase();
  if (geocodeCache.has(cacheKey)) return geocodeCache.get(cacheKey) ?? null;

  try {
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
    const coords: [number, number] = [lat, lon];
    geocodeCache.set(cacheKey, coords);
    return coords;
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
  const [positions, setPositions] = useState<Array<{ position: [number, number]; address: string; approximate: boolean }>>([]);

  const normalizedBuildings = useMemo(() => {
    return (buildings || []).slice(0, 100).map((building, i) => {
      const addr = typeof building === 'string' ? building : building.address;
      const entryBoro = typeof building === 'object' && building.borough ? building.borough : undefined;
      let buildingBoro = entryBoro || boro || '';
      if (!entryBoro && boros && boros.length > 0) {
        buildingBoro = boros[Math.floor((i / Math.max(buildings.length, 1)) * boros.length)] || boros[0] || '';
      }
      return { addr, buildingBoro };
    });
  }, [buildings, boro, boros]);

  useEffect(() => {
    let cancelled = false;
    const resolvePositions = async () => {
      if (!normalizedBuildings.length) {
        setPositions([]);
        return;
      }

      const resolved: Array<{ position: [number, number]; address: string; approximate: boolean }> = [];
      for (let i = 0; i < normalizedBuildings.length; i++) {
        const { addr, buildingBoro } = normalizedBuildings[i];
        let coords: [number, number] | null = null;
        let approximate = true;

        // Keep external geocoding bounded; fallback for the rest.
        if (i < 25) {
          coords = await geocodeAddress(addr, buildingBoro);
          approximate = !coords;
        }
        if (!coords) coords = approximateCoords(addr, buildingBoro);
        if (coords) resolved.push({ position: coords, address: addr, approximate });
      }

      if (!cancelled) setPositions(resolved);
    };
    resolvePositions();
    return () => {
      cancelled = true;
    };
  }, [normalizedBuildings]);

  if (positions.length === 0) return null;

  const center: [number, number] = positions.length > 0 
    ? [
        positions.reduce((sum, p) => sum + p.position[0], 0) / positions.length,
        positions.reduce((sum, p) => sum + p.position[1], 0) / positions.length,
      ]
    : [40.7128, -73.95];

  return (
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
        />
        <FitBounds positions={positions.map(p => p.position)} />
        {positions.map((p, i) => (
          <Marker key={i} position={p.position} icon={smallMarker}>
            <Popup>
              <span className="text-xs">
                {p.address}
                {p.approximate ? ' (approximate)' : ''}
              </span>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
};

export default PortfolioMap;
