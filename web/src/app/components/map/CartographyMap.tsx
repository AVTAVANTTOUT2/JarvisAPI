import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
} from 'react';
import maplibregl, { type GeoJSONSource, type Map as MapLibreMap, type MapLayerMouseEvent } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

import {
  collectBounds,
  historyPointsToGeoJSON,
  historyTrailToGeoJSON,
  placesToGeoJSON,
  tripsToGeoJSON,
  type CartographyLocationPoint,
  type CartographyPlace,
  type CartographyTrip,
  type GeoJsonFeatureCollection,
} from '@desktop/app/lib/cartographyGeojson';
import {
  isPmtilesStyleUrl,
  MAP_ATTRIBUTION_HTML,
  resolveMapStyleUrl,
} from '@desktop/app/lib/mapStyle';

const SOURCE_PLACES = 'jarvis-places';
const SOURCE_GPS = 'jarvis-gps';
const SOURCE_TRAIL = 'jarvis-trail';
const SOURCE_TRIPS = 'jarvis-trips';

const LAYER_TRAIL = 'jarvis-trail-line';
const LAYER_TRIPS = 'jarvis-trips-line';
const LAYER_GPS = 'jarvis-gps-circle';
const LAYER_GPS_LATEST = 'jarvis-gps-latest';
const LAYER_PLACES = 'jarvis-places-circle';
const LAYER_PLACES_SELECTED = 'jarvis-places-selected';

export type CartographySelection =
  | { kind: 'place'; place: CartographyPlace }
  | { kind: 'gps'; point: CartographyLocationPoint }
  | { kind: 'trip'; trip: CartographyTrip }
  | null;

export interface CartographyMapProps {
  places: CartographyPlace[];
  historyPoints: CartographyLocationPoint[];
  trips: CartographyTrip[];
  latestPointId?: number | null;
  showPlaces: boolean;
  showGps: boolean;
  showTrips: boolean;
  selectedPlaceId?: number | null;
  /** Incrémente pour forcer un fitBounds (ex. bouton recentrer). */
  fitToken?: number;
  recenterToken?: number;
  styleUrl?: string;
  className?: string;
  onSelect?: (selection: CartographySelection) => void;
  onReadyChange?: (ready: boolean) => void;
  onErrorChange?: (message: string | null) => void;
}

function setSourceData(map: MapLibreMap, sourceId: string, data: GeoJsonFeatureCollection): void {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined;
  if (source) {
    // GeoJSON FeatureCollection compatible MapLibre (pas de dépendance @types/geojson).
    source.setData(data as Parameters<GeoJSONSource['setData']>[0]);
  }
}

function ensureLayers(map: MapLibreMap): void {
  if (!map.getSource(SOURCE_TRAIL)) {
    map.addSource(SOURCE_TRAIL, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
  }
  if (!map.getSource(SOURCE_TRIPS)) {
    map.addSource(SOURCE_TRIPS, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
  }
  if (!map.getSource(SOURCE_GPS)) {
    map.addSource(SOURCE_GPS, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
  }
  if (!map.getSource(SOURCE_PLACES)) {
    map.addSource(SOURCE_PLACES, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });
  }

  if (!map.getLayer(LAYER_TRAIL)) {
    map.addLayer({
      id: LAYER_TRAIL,
      type: 'line',
      source: SOURCE_TRAIL,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': 'rgba(148, 163, 184, 0.55)',
        'line-width': 2,
        'line-opacity': 0.7,
      },
    });
  }

  if (!map.getLayer(LAYER_TRIPS)) {
    map.addLayer({
      id: LAYER_TRIPS,
      type: 'line',
      source: SOURCE_TRIPS,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ['coalesce', ['get', 'color'], '#8E8E93'],
        'line-width': 3.5,
        'line-opacity': 0.85,
      },
    });
  }

  if (!map.getLayer(LAYER_GPS)) {
    map.addLayer({
      id: LAYER_GPS,
      type: 'circle',
      source: SOURCE_GPS,
      filter: ['!=', ['get', 'isLatest'], true],
      paint: {
        'circle-radius': 3,
        'circle-color': 'rgba(148, 163, 184, 0.7)',
        'circle-stroke-width': 0,
      },
    });
  }

  if (!map.getLayer(LAYER_GPS_LATEST)) {
    map.addLayer({
      id: LAYER_GPS_LATEST,
      type: 'circle',
      source: SOURCE_GPS,
      filter: ['==', ['get', 'isLatest'], true],
      paint: {
        'circle-radius': 8,
        'circle-color': 'rgba(59, 130, 246, 0.9)',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    });
  }

  if (!map.getLayer(LAYER_PLACES)) {
    map.addLayer({
      id: LAYER_PLACES,
      type: 'circle',
      source: SOURCE_PLACES,
      paint: {
        'circle-radius': 8,
        'circle-color': ['coalesce', ['get', 'color'], '#8E8E93'],
        'circle-stroke-color': '#0a0a0f',
        'circle-stroke-width': 2,
      },
    });
  }

  if (!map.getLayer(LAYER_PLACES_SELECTED)) {
    map.addLayer({
      id: LAYER_PLACES_SELECTED,
      type: 'circle',
      source: SOURCE_PLACES,
      filter: ['==', ['get', 'placeId'], -1],
      paint: {
        'circle-radius': 14,
        'circle-color': 'transparent',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
        'circle-opacity': 1,
      },
    });
  }
}

function setLayerVisibility(map: MapLibreMap, layerId: string, visible: boolean): void {
  if (!map.getLayer(layerId)) return;
  map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
}

export function CartographyMap({
  places,
  historyPoints,
  trips,
  latestPointId = null,
  showPlaces,
  showGps,
  showTrips,
  selectedPlaceId = null,
  fitToken = 0,
  recenterToken = 0,
  styleUrl,
  className,
  onSelect,
  onReadyChange,
  onErrorChange,
}: CartographyMapProps): ReactElement {
  const reactId = useId();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const placesRef = useRef(places);
  const pointsRef = useRef(historyPoints);
  const tripsRef = useRef(trips);
  const latestPointIdRef = useRef(latestPointId);
  const showPlacesRef = useRef(showPlaces);
  const showGpsRef = useRef(showGps);
  const showTripsRef = useRef(showTrips);
  const onSelectRef = useRef(onSelect);
  const onReadyChangeRef = useRef(onReadyChange);
  const onErrorChangeRef = useRef(onErrorChange);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  placesRef.current = places;
  pointsRef.current = historyPoints;
  tripsRef.current = trips;
  latestPointIdRef.current = latestPointId;
  showPlacesRef.current = showPlaces;
  showGpsRef.current = showGps;
  showTripsRef.current = showTrips;
  onSelectRef.current = onSelect;
  onReadyChangeRef.current = onReadyChange;
  onErrorChangeRef.current = onErrorChange;

  const styleUrlRef = useRef(resolveMapStyleUrl(styleUrl));

  const applyData = useCallback((map: MapLibreMap) => {
    const placesFc = placesToGeoJSON(placesRef.current);
    const gpsFc = historyPointsToGeoJSON(pointsRef.current, {
      latestId: latestPointIdRef.current,
    });
    const trailFc = historyTrailToGeoJSON(pointsRef.current);
    const tripsFc = tripsToGeoJSON(tripsRef.current, placesRef.current);
    setSourceData(map, SOURCE_PLACES, placesFc);
    setSourceData(map, SOURCE_GPS, gpsFc);
    setSourceData(map, SOURCE_TRAIL, trailFc);
    setSourceData(map, SOURCE_TRIPS, tripsFc);
  }, []);

  const fitToData = useCallback((map: MapLibreMap, animate: boolean) => {
    const tripsFc = tripsToGeoJSON(tripsRef.current, placesRef.current);
    const bounds = collectBounds(placesRef.current, pointsRef.current, tripsFc);
    if (!bounds) return;
    const [[minLng, minLat], [maxLng, maxLat]] = bounds;
    if (minLng === maxLng && minLat === maxLat) {
      map.easeTo({
        center: [minLng, minLat],
        zoom: 14,
        duration: animate ? 500 : 0,
      });
      return;
    }
    map.fitBounds(
      [
        [minLng, minLat],
        [maxLng, maxLat],
      ],
      { padding: 56, maxZoom: 16, duration: animate ? 600 : 0 },
    );
  }, []);

  const flyToLatest = useCallback((map: MapLibreMap) => {
    const points = pointsRef.current;
    const id = latestPointIdRef.current;
    const target =
      (id != null ? points.find((p) => p.id === id) : undefined)
      ?? points[points.length - 1];
    if (!target) {
      fitToData(map, true);
      return;
    }
    map.easeTo({
      center: [target.longitude, target.latitude],
      zoom: Math.max(map.getZoom(), 14),
      duration: 600,
    });
  }, [fitToData]);

  // Init MapLibre une seule fois (Strict Mode safe via cleanup).
  useEffect(() => {
    const el = containerRef.current;
    if (!el || mapRef.current) return;

    const styleAtMount = resolveMapStyleUrl(styleUrlRef.current);
    if (isPmtilesStyleUrl(styleAtMount)) {
      const msg =
        'Style PMTiles non activé dans cette version — configurez un style HTTP ou attendez le protocole pmtiles.';
      setStatus('error');
      setErrorMessage(msg);
      onErrorChangeRef.current?.(msg);
      onReadyChangeRef.current?.(false);
      return;
    }

    let cancelled = false;
    setStatus('loading');
    setErrorMessage(null);
    onErrorChangeRef.current?.(null);
    onReadyChangeRef.current?.(false);

    const map = new maplibregl.Map({
      container: el,
      style: styleAtMount,
      center: [2.3522, 48.8566],
      zoom: 11,
      attributionControl: {
        compact: true,
        customAttribution: MAP_ATTRIBUTION_HTML,
      },
    });

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
    mapRef.current = map;

    const handleError = (event: { error?: Error | { message?: string } }) => {
      if (cancelled) return;
      const message =
        event.error instanceof Error
          ? event.error.message
          : typeof event.error?.message === 'string'
            ? event.error.message
            : 'Impossible de charger le fond de carte.';
      setStatus('error');
      setErrorMessage(message);
      onErrorChangeRef.current?.(message);
      onReadyChangeRef.current?.(false);
    };

    const handleLoad = () => {
      if (cancelled) return;
      try {
        ensureLayers(map);
        applyData(map);
        setLayerVisibility(map, LAYER_PLACES, showPlacesRef.current);
        setLayerVisibility(map, LAYER_PLACES_SELECTED, showPlacesRef.current);
        setLayerVisibility(map, LAYER_GPS, showGpsRef.current);
        setLayerVisibility(map, LAYER_GPS_LATEST, showGpsRef.current);
        setLayerVisibility(map, LAYER_TRAIL, showGpsRef.current);
        setLayerVisibility(map, LAYER_TRIPS, showTripsRef.current);
        fitToData(map, false);
        setStatus('ready');
        setErrorMessage(null);
        onErrorChangeRef.current?.(null);
        onReadyChangeRef.current?.(true);
        requestAnimationFrame(() => map.resize());
      } catch (err) {
        handleError({ error: err instanceof Error ? err : new Error(String(err)) });
      }
    };

    map.on('load', handleLoad);
    map.on('error', handleError);

    const interactiveLayers = [LAYER_PLACES, LAYER_GPS, LAYER_GPS_LATEST, LAYER_TRIPS];

    const onClick = (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature?.properties) {
        onSelectRef.current?.(null);
        return;
      }
      const props = feature.properties;
      if (props.kind === 'place') {
        const place = placesRef.current.find((p) => p.id === Number(props.placeId));
        onSelectRef.current?.(place ? { kind: 'place', place } : null);
        return;
      }
      if (props.kind === 'gps') {
        const point = pointsRef.current.find((p) => p.id === Number(props.pointId));
        onSelectRef.current?.(point ? { kind: 'gps', point } : null);
        return;
      }
      if (props.kind === 'trip') {
        const trip = tripsRef.current.find((t) => t.id === Number(props.tripId));
        onSelectRef.current?.(trip ? { kind: 'trip', trip } : null);
      }
    };

    for (const layerId of interactiveLayers) {
      map.on('click', layerId, onClick);
      map.on('mouseenter', layerId, () => {
        map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', layerId, () => {
        map.getCanvas().style.cursor = '';
      });
    }

    const observer = new ResizeObserver(() => {
      map.resize();
    });
    observer.observe(el);

    return () => {
      cancelled = true;
      observer.disconnect();
      for (const layerId of interactiveLayers) {
        map.off('click', layerId, onClick);
      }
      map.off('load', handleLoad);
      map.off('error', handleError);
      map.remove();
      mapRef.current = null;
    };
  }, [applyData, fitToData]);

  // Sync data / visibility without recreating the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || status !== 'ready') return;
    applyData(map);
    setLayerVisibility(map, LAYER_PLACES, showPlaces);
    setLayerVisibility(map, LAYER_PLACES_SELECTED, showPlaces);
    setLayerVisibility(map, LAYER_GPS, showGps);
    setLayerVisibility(map, LAYER_GPS_LATEST, showGps);
    setLayerVisibility(map, LAYER_TRAIL, showGps);
    setLayerVisibility(map, LAYER_TRIPS, showTrips);
    if (map.getLayer(LAYER_PLACES_SELECTED)) {
      map.setFilter(
        LAYER_PLACES_SELECTED,
        selectedPlaceId == null
          ? ['==', ['get', 'placeId'], -1]
          : ['==', ['get', 'placeId'], selectedPlaceId],
      );
    }
  }, [
    applyData,
    places,
    historyPoints,
    trips,
    latestPointId,
    showPlaces,
    showGps,
    showTrips,
    selectedPlaceId,
    status,
  ]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || status !== 'ready' || fitToken === 0) return;
    fitToData(map, true);
  }, [fitToken, fitToData, status]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || status !== 'ready' || recenterToken === 0) return;
    flyToLatest(map);
  }, [recenterToken, flyToLatest, status]);

  return (
    <div
      className={`relative h-full w-full min-h-0 bg-black ${className ?? ''}`}
      data-cartography-map={reactId}
    >
      <div ref={containerRef} className="absolute inset-0" data-testid="cartography-map-canvas" />

      {status === 'loading' && (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center bg-black/60"
          data-testid="cartography-map-loading"
          role="status"
        >
          <p className="font-mono text-xs text-muted-foreground">Chargement de la carte…</p>
        </div>
      )}

      {status === 'error' && (
        <div
          className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-black/80 px-6 text-center"
          data-testid="cartography-map-error"
          role="alert"
        >
          <p className="text-sm text-red-400">Fond de carte indisponible</p>
          <p className="max-w-sm font-mono text-xs text-muted-foreground">
            {errorMessage ?? 'Le fournisseur de tuiles ne répond pas. Les données GPS restent chargées.'}
          </p>
        </div>
      )}
    </div>
  );
}

export default CartographyMap;
