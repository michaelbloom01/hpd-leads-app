import React, { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { ApiLead } from '../services/api';

// Fix for default marker icons in React-Leaflet
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// Custom marker icon based on score
const createScoreIcon = (score: number) => {
  const color = score >= 60 ? '#10b981' : score >= 40 ? '#f59e0b' : '#64748b';
  return L.divIcon({
    className: 'custom-marker',
    html: `<div style="
      background: ${color};
      width: 24px;
      height: 24px;
      border-radius: 50%;
      border: 3px solid white;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      font-weight: bold;
      color: white;
    ">${Math.round(score)}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
};

// NYC borough approximate centers for fallback
const BOROUGH_CENTERS: Record<string, [number, number]> = {
  'MANHATTAN': [40.7831, -73.9712],
  'BROOKLYN': [40.6782, -73.9442],
  'QUEENS': [40.7282, -73.7949],
  'BRONX': [40.8448, -73.8648],
  'STATEN ISLAND': [40.5795, -74.1502],
};

// Simple address to coordinates lookup using NYC borough
const getApproximateCoords = (address: string, boro: string): [number, number] | null => {
  // Add some randomness within the borough for visual spread
  const center = BOROUGH_CENTERS[boro.toUpperCase()] || BOROUGH_CENTERS['MANHATTAN'];
  const jitter = () => (Math.random() - 0.5) * 0.05; // ~2-3 mile spread
  return [center[0] + jitter(), center[1] + jitter()];
};

interface GeocodedBuilding {
  address: string;
  coords: [number, number];
  leadId: string;
  leadName: string;
  score: number;
}

// Component to fit map bounds to markers
const FitBounds: React.FC<{ buildings: GeocodedBuilding[] }> = ({ buildings }) => {
  const map = useMap();
  
  useEffect(() => {
    if (buildings.length > 0) {
      const bounds = L.latLngBounds(buildings.map(b => b.coords));
      map.fitBounds(bounds, { padding: [50, 50] });
    }
  }, [buildings, map]);
  
  return null;
};

interface Props {
  leads: ApiLead[];
  onSelectLead?: (lead: ApiLead) => void;
}

const PropertyMap: React.FC<Props> = ({ leads, onSelectLead }) => {
  const [geocodedBuildings, setGeocodedBuildings] = useState<GeocodedBuilding[]>([]);
  
  // Geocode buildings (using approximate coords based on borough)
  useEffect(() => {
    const buildings: GeocodedBuilding[] = [];
    
    leads.forEach(lead => {
      // For each lead, plot their buildings (limit to first 5 per lead for performance)
      const buildingsToPlot = lead.buildings.slice(0, 5);
      
      buildingsToPlot.forEach(address => {
        const coords = getApproximateCoords(address, lead.boro);
        if (coords) {
          buildings.push({
            address,
            coords,
            leadId: lead.lead_id,
            leadName: lead.agent_name || lead.owner_name,
            score: lead.score,
          });
        }
      });
    });
    
    setGeocodedBuildings(buildings);
  }, [leads]);

  if (leads.length === 0) {
    return (
      <div className="h-96 bg-slate-900 rounded-3xl flex items-center justify-center">
        <p className="text-slate-500">No leads to display on map</p>
      </div>
    );
  }

  return (
    <div className="relative">
      <div className="h-[500px] rounded-3xl overflow-hidden border border-white/10">
        <MapContainer
          center={[40.7128, -74.0060]} // NYC center
          zoom={11}
          style={{ height: '100%', width: '100%' }}
          className="z-0"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          
          <FitBounds buildings={geocodedBuildings} />
          
          {geocodedBuildings.map((building, idx) => (
            <Marker
              key={`${building.leadId}-${idx}`}
              position={building.coords}
              icon={createScoreIcon(building.score)}
              eventHandlers={{
                click: () => {
                  const lead = leads.find(l => l.lead_id === building.leadId);
                  if (lead && onSelectLead) {
                    onSelectLead(lead);
                  }
                },
              }}
            >
              <Popup>
                <div className="text-slate-900 min-w-[200px]">
                  <p className="font-bold text-sm">{building.leadName}</p>
                  <p className="text-xs text-slate-600 mt-1">{building.address}</p>
                  <div className="flex items-center gap-2 mt-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-bold text-white ${
                      building.score >= 60 ? 'bg-emerald-500' : 
                      building.score >= 40 ? 'bg-amber-500' : 'bg-slate-500'
                    }`}>
                      Score: {building.score.toFixed(1)}
                    </span>
                  </div>
                </div>
              </Popup>
            </Marker>
          ))}
        </MapContainer>
      </div>
      
      {/* Legend */}
      <div className="absolute bottom-4 left-4 bg-slate-900/90 backdrop-blur-sm rounded-xl p-4 border border-white/10 max-w-xs max-h-48 overflow-y-auto">
        <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">
          Showing {geocodedBuildings.length} properties from {leads.length} leads
        </h4>
        <div className="flex gap-3 text-[10px]">
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 rounded-full bg-emerald-500"></div>
            <span className="text-slate-400">60+</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 rounded-full bg-amber-500"></div>
            <span className="text-slate-400">40-60</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 rounded-full bg-slate-500"></div>
            <span className="text-slate-400">&lt;40</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PropertyMap;
