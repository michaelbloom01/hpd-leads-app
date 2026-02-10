import React, { useMemo, useEffect, useRef } from 'react';
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

interface PortfolioMapProps {
  buildings: string[];
  boro?: string;
  boros?: string[];
  height?: string;
}

const PortfolioMap: React.FC<PortfolioMapProps> = ({ buildings, boro, boros, height = '250px' }) => {
  const mapRef = useRef<any>(null);
  
  const positions = useMemo(() => {
    if (!buildings || buildings.length === 0) return [];
    
    return buildings.slice(0, 100).map((building, i) => {
      // Try to determine borough from building address or use provided boro
      let buildingBoro = boro || '';
      if (boros && boros.length > 0) {
        // Distribute buildings across boroughs proportionally
        buildingBoro = boros[Math.floor(i / buildings.length * boros.length)] || boros[0] || '';
      }
      
      const coords = approximateCoords(building, buildingBoro);
      return {
        position: coords as [number, number],
        address: building,
      };
    }).filter(p => p.position !== null);
  }, [buildings, boro, boros]);

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
              <span className="text-xs">{p.address}</span>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
};

export default PortfolioMap;
