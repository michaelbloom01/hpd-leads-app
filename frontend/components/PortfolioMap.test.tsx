import { cleanup, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import PortfolioMap from './PortfolioMap';

const markerMock = vi.fn((props: { children?: React.ReactNode; position: [number, number] }) => (
  <div data-testid="map-marker">{props.children}</div>
));
const tileLayerMock = vi.fn((_props: Record<string, unknown>) => <div data-testid="tile-layer" />);
const fitBoundsMock = vi.fn();
const invalidateSizeMock = vi.fn();
const stopMock = vi.fn();

vi.mock('leaflet/dist/leaflet.css', () => ({}));

vi.mock('leaflet', () => ({
  default: {
    Icon: Object.assign(
      vi.fn(),
      {
        Default: {
          prototype: {},
          mergeOptions: vi.fn(),
        },
      },
    ),
    latLng: (lat: number, lon: number) => ({ lat, lon }),
    latLngBounds: vi.fn((points) => ({ points })),
  },
}));

vi.mock('react-leaflet', () => ({
  MapContainer: React.forwardRef<HTMLDivElement, { children: React.ReactNode }>(
    ({ children }, ref) => <div ref={ref} data-testid="map-container">{children}</div>,
  ),
  TileLayer: (props: any) => tileLayerMock(props),
  Marker: (props: any) => markerMock(props),
  Popup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  useMap: () => ({
    fitBounds: fitBoundsMock,
    invalidateSize: invalidateSizeMock,
    stop: stopMock,
  }),
}));

describe('PortfolioMap', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('btoa', (value: string) => Buffer.from(value).toString('base64'));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('rejects collapsed stored coordinates and geocodes the portfolio footprint', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const query = new URL(url).searchParams.get('text') || '';
      const index = Number((query.match(/Building (\d+)/i) || [])[1] || 0);
      return {
        ok: true,
        json: async () => ({
          features: [{
            geometry: {
              coordinates: [-73.99 + (index * 0.001), 40.7 + (index * 0.001)],
            },
          }],
        }),
      };
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <PortfolioMap
        allowClientGeocodingFallback
        buildings={Array.from({ length: 8 }, (_, i) => ({
          address: `Building ${i + 1} Street`,
          borough: 'BROOKLYN',
          latitude: 40.7128,
          longitude: -74.006,
          coordinate_source: 'stored',
          coordinate_precision: 'parcel',
        }))}
      />,
    );

    await waitFor(() => expect(markerMock).toHaveBeenCalledTimes(8));
    expect(screen.getByText(/8 saved markers needed refresh/i)).toBeInTheDocument();
    expect(screen.getByText(/0 stored, 8 geocoded/i)).toBeInTheDocument();
    const markerPositions = markerMock.mock.calls.map(([props]) => props.position.join(','));
    expect(new Set(markerPositions).size).toBe(8);
    expect(stopMock).toHaveBeenCalled();
    expect(fitBoundsMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ animate: false }),
    );
  });

  it('does not render impossible non-NYC coordinates as valid markers', async () => {
    render(
      <PortfolioMap
        buildings={[{
          address: '100 NORTH 3 STREET',
          borough: 'BROOKLYN',
          latitude: 0,
          longitude: 0,
          coordinate_source: 'stored',
          coordinate_precision: 'parcel',
        }]}
      />,
    );

    expect(await screen.findByText(/Portfolio map is not available yet/i)).toBeInTheDocument();
    expect(screen.getByText(/saved map markers need to be refreshed/i)).toBeInTheDocument();
    expect(markerMock).not.toHaveBeenCalled();
  });
});
