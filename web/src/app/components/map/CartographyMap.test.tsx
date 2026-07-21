import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';

type Handler = (...args: unknown[]) => void;

const handlers = new Map<string, Handler[]>();
const layerHandlers = new Map<string, Handler[]>();

function on(event: string, ...rest: unknown[]) {
  if (typeof rest[0] === 'string') {
    const layerId = rest[0];
    const fn = rest[1] as Handler;
    const key = `${event}:${layerId}`;
    const list = layerHandlers.get(key) ?? [];
    list.push(fn);
    layerHandlers.set(key, list);
    return;
  }
  const fn = rest[0] as Handler;
  const list = handlers.get(event) ?? [];
  list.push(fn);
  handlers.set(event, list);
}

function off(event: string, ...rest: unknown[]) {
  if (typeof rest[0] === 'string') {
    const layerId = rest[0];
    const fn = rest[1] as Handler;
    const key = `${event}:${layerId}`;
    layerHandlers.set(
      key,
      (layerHandlers.get(key) ?? []).filter((h) => h !== fn),
    );
    return;
  }
  const fn = rest[0] as Handler;
  handlers.set(event, (handlers.get(event) ?? []).filter((h) => h !== fn));
}

function emit(event: string, payload?: unknown) {
  for (const fn of handlers.get(event) ?? []) fn(payload);
}

const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
const layers = new Set<string>();

const mapInstance = {
  on,
  off,
  addControl: vi.fn(),
  addSource: vi.fn((id: string) => {
    sources.set(id, { setData: vi.fn() });
  }),
  addLayer: vi.fn((layer: { id: string }) => {
    layers.add(layer.id);
  }),
  getSource: vi.fn((id: string) => sources.get(id)),
  getLayer: vi.fn((id: string) => (layers.has(id) ? { id } : undefined)),
  setLayoutProperty: vi.fn(),
  setFilter: vi.fn(),
  fitBounds: vi.fn(),
  easeTo: vi.fn(),
  getZoom: vi.fn(() => 12),
  resize: vi.fn(),
  remove: vi.fn(),
  getCanvas: vi.fn(() => ({ style: { cursor: '' } })),
};

vi.mock('maplibre-gl', () => ({
  default: {
    Map: vi.fn(function MapMock() {
      return mapInstance;
    }),
    NavigationControl: vi.fn(function NavMock() {
      return {};
    }),
  },
}));

vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));

import { CartographyMap } from './CartographyMap';

const samplePlace = {
  id: 1,
  name: 'Maison',
  category: 'home',
  latitude: 50.6,
  longitude: 3.05,
};

const sampleTrip = {
  id: 9,
  from_place_id: 1,
  to_place_id: 1,
  transport_mode: 'pied',
  route_points: JSON.stringify([
    [50.6, 3.05],
    [50.61, 3.06],
  ]),
};

describe('CartographyMap', () => {
  beforeEach(() => {
    handlers.clear();
    layerHandlers.clear();
    sources.clear();
    layers.clear();
    vi.clearAllMocks();
    class ResizeObserverMock {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders a loading state before the style loads', () => {
    render(
      <CartographyMap
        places={[]}
        historyPoints={[]}
        trips={[]}
        showPlaces
        showGps
        showTrips
      />,
    );
    expect(screen.getByTestId('cartography-map-loading')).toBeTruthy();
  });

  it('shows an error state when MapLibre emits an error', async () => {
    render(
      <CartographyMap
        places={[samplePlace]}
        historyPoints={[]}
        trips={[]}
        showPlaces
        showGps
        showTrips
      />,
    );

    await act(async () => {
      emit('error', { error: new Error('tiles failed') });
    });

    expect(await screen.findByTestId('cartography-map-error')).toBeTruthy();
    expect(screen.getByText(/tiles failed/i)).toBeTruthy();
  });

  it('becomes ready after load and wires GeoJSON sources', async () => {
    render(
      <CartographyMap
        places={[samplePlace]}
        historyPoints={[
          {
            id: 2,
            latitude: 50.6,
            longitude: 3.05,
            created_at: '2026-07-20T12:00:00Z',
          },
        ]}
        trips={[sampleTrip]}
        latestPointId={2}
        showPlaces
        showGps
        showTrips
      />,
    );

    await act(async () => {
      emit('load');
    });

    await waitFor(() => {
      expect(screen.queryByTestId('cartography-map-loading')).toBeNull();
    });
    expect(mapInstance.addSource).toHaveBeenCalled();
    expect(mapInstance.addLayer).toHaveBeenCalled();
  });

  it('invokes onSelect for place and trip clicks', async () => {
    const onSelect = vi.fn();
    render(
      <CartographyMap
        places={[samplePlace]}
        historyPoints={[]}
        trips={[sampleTrip]}
        showPlaces
        showGps
        showTrips
        onSelect={onSelect}
      />,
    );

    await act(async () => {
      emit('load');
    });

    const placeClick = (layerHandlers.get('click:jarvis-places-circle') ?? [])[0];
    const tripClick = (layerHandlers.get('click:jarvis-trips-line') ?? [])[0];
    expect(placeClick).toBeTypeOf('function');
    expect(tripClick).toBeTypeOf('function');

    await act(async () => {
      placeClick({
        features: [{ properties: { kind: 'place', placeId: 1 } }],
      });
      tripClick({
        features: [{ properties: { kind: 'trip', tripId: 9 } }],
      });
    });

    expect(onSelect).toHaveBeenCalledWith({ kind: 'place', place: samplePlace });
    expect(onSelect).toHaveBeenCalledWith({ kind: 'trip', trip: sampleTrip });
  });

  it('reports PMTiles styles as unavailable until protocol support lands', () => {
    render(
      <CartographyMap
        places={[]}
        historyPoints={[]}
        trips={[]}
        showPlaces
        showGps
        showTrips
        styleUrl="pmtiles:///maps/europe.pmtiles"
      />,
    );
    expect(screen.getByTestId('cartography-map-error')).toBeTruthy();
    expect(screen.getByText(/PMTiles non activé/i)).toBeTruthy();
  });
});
