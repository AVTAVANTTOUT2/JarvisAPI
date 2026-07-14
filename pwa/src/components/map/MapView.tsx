'use client';

import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import type { Map as LeafletMap } from 'leaflet';
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Polyline,
  CircleMarker,
  Tooltip,
  useMap,
} from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import type { LocationPoint, Place, Trip } from '@mobile/lib/map-types';

// ─────────────────────────────────────────────────────────────
// Icône custom pour les lieux nommés (circlee, couleur par cat)
// ─────────────────────────────────────────────────────────────

const CAT_COLORS: Record<string, string> = {
  home: '#FF9500',
  work: '#007AFF',
  school: '#9C59FF',
  gym: '#30D158',
  sport: '#30D158',
  restaurant: '#FFD60A',
  shop: '#FFD60A',
  transport: '#5AC8FA',
  other: '#8E8E93',
};

function colorForCategory(cat: string | null): string {
  if (!cat) return CAT_COLORS.other;
  return CAT_COLORS[cat.toLowerCase()] ?? CAT_COLORS.other;
}

function createPlaceIcon(cat: string | null, selected: boolean): L.DivIcon {
  const color = colorForCategory(cat);
  const size = selected ? 16 : 13;
  const border = selected ? 3 : 2;
  return L.divIcon({
    className: '', // supprime le style leaflet par defaut
    html: `<div style="
      width:${size}px;height:${size}px;
      background:${color};
      border:${border}px solid #0a0a0f;
      border-radius:50%;
      box-shadow:0 0 ${selected ? 12 : 6}px ${color}66;
      ${selected ? 'transform:scale(1.3);' : ''}
    "></div>`,
    iconSize: [size + border * 2, size + border * 2],
    iconAnchor: [(size + border * 2) / 2, (size + border * 2) / 2],
    popupAnchor: [0, -(size + border * 2) / 2],
  });
}

// ─────────────────────────────────────────────────────────────
// Sous-composant : ajuste automatiquement la bounding box
// ─────────────────────────────────────────────────────────────

function FitBounds({
  points,
  places,
}: {
  points: LocationPoint[];
  places: Place[];
}) {
  const map = useMap();

  // memo: rerun uniquement si les points ou lieux changent d'identite
  const allCoords = useMemo(() => {
    const coords: [number, number][] = [];
    places.forEach((p) => coords.push([p.latitude, p.longitude]));
    points.forEach((p) => coords.push([p.latitude, p.longitude]));
    return coords;
  }, [points, places]);

  useEffect(() => {
    if (allCoords.length === 0) return;
    const b = L.latLngBounds(allCoords.map(([lat, lng]) => L.latLng(lat, lng)));
    if (b.isValid()) {
      map.fitBounds(b.pad(0.15), { maxZoom: 16, animate: false });
    }
  }, [map, allCoords]);

  return null;
}

// ─────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────

export interface MapViewProps {
  /** Lieux nommes (marqueurs colores) */
  places: Place[];
  /** Points GPS bruts (path de deplacement) */
  points: LocationPoint[];
  /** Trajets (polylines colorees) */
  trips: Trip[];
  /** Lieu selectionne dans la timeline, mis en evidence sur la carte */
  selectedPlaceId: number | null;
  /** Callback quand l'utilisateur clique sur un marqueur de lieu */
  onPlaceSelect: (place: Place) => void;
  /** Callback quand l'utilisateur clique sur une polyline de trajet */
  onTripSelect: (trip: Trip) => void;
  /** Callback quand l'utilisateur clique sur un point de passage */
  onPointSelect: (point: LocationPoint) => void;
}

// ─────────────────────────────────────────────────────────────
// Composant principal
// ─────────────────────────────────────────────────────────────

export default function MapView({
  places,
  points,
  trips,
  selectedPlaceId,
  onPlaceSelect,
  onTripSelect,
  onPointSelect,
}: MapViewProps) {
  const mapRef = useRef<LeafletMap | null>(null);

  // Découpage des trips en segments exploitables
  const tripPolylines = useMemo(() => {
    return trips
      .filter((t) => t.route_points)
      .map((trip) => {
        let rp: [number, number][] = [];
        try {
          const raw = JSON.parse(trip.route_points!);
          if (Array.isArray(raw)) {
            rp = raw.filter(
              (p: unknown): p is [number, number] =>
                Array.isArray(p) && p.length >= 2 && typeof p[0] === 'number' && typeof p[1] === 'number'
            );
          }
        } catch {
          // route_points invalide, on ignore
        }
        return { trip, points: rp };
      })
      .filter((t) => t.points.length >= 2);
  }, [trips]);

  // Points GPS simplifies (on ne trace que les points avec intervalle pour eviter la surcharge)
  const breadcrumbPoints = useMemo(() => {
    if (points.length <= 200) return points;
    // echantillonnage : 200 points max
    const step = Math.ceil(points.length / 200);
    return points.filter((_, i) => i % step === 0);
  }, [points]);

  const [selectedMarker, setSelectedMarker] = useState<number | null>(null);

  const handlePlaceClick = useCallback(
    (place: Place) => {
      setSelectedMarker(place.id);
      onPlaceSelect(place);
    },
    [onPlaceSelect]
  );

  return (
    <div className="w-full h-full relative">
      <MapContainer
        center={[48.8566, 2.3522]} // Paris par defaut, FitBounds override
        zoom={13}
        className="w-full h-full"
        zoomControl={false}
        attributionControl={false}
        ref={mapRef}
      >
        {/* Fond de carte sombre */}
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          maxZoom={19}
        />

        {/* Ajustement auto du viewport */}
        <FitBounds points={breadcrumbPoints} places={places} />

        {/* Marqueurs des lieux nommes */}
        {places.map((place) => {
          const isSelected = selectedPlaceId === place.id || selectedMarker === place.id;
          return (
            <Marker
              key={place.id}
              position={[place.latitude, place.longitude]}
              icon={createPlaceIcon(place.category, isSelected)}
              eventHandlers={{
                click: () => handlePlaceClick(place),
              }}
            >
              <Popup closeButton={false} className="custom-popup">
                <div className="text-[13px] font-semibold text-[#0a0a0f]">
                  {place.name}
                </div>
                <div className="text-[11px] text-[#555] mt-0.5">
                  {place.visit_count} visite{place.visit_count > 1 ? 's' : ''}
                  {place.avg_duration_min != null && (
                    <> &middot; ~{Math.round(place.avg_duration_min)} min</>
                  )}
                </div>
              </Popup>
            </Marker>
          );
        })}

        {/* Breadcrumb des positions GPS (points transparents) */}
        {breadcrumbPoints.map((pt) => (
          <CircleMarker
            key={pt.id}
            center={[pt.latitude, pt.longitude]}
            radius={pt.accuracy != null && pt.accuracy < 5 ? 3 : 2.5}
            pathOptions={{
              color: pt.place_id ? '#4A9EFF' : '#888',
              fillColor: pt.place_id ? '#4A9EFF' : '#666',
              fillOpacity: 0.25,
              weight: 0.5,
            }}
            eventHandlers={{
              click: () => onPointSelect(pt),
            }}
          >
            {pt.place_name && (
              <Tooltip direction="top" offset={[0, -4]} opacity={0.9}>
                <span className="text-[11px] font-medium">{pt.place_name}</span>
                {pt.accuracy != null && (
                  <span className="text-[10px] text-[#888] ml-1">
                    ±{Math.round(pt.accuracy)}m
                  </span>
                )}
              </Tooltip>
            )}
          </CircleMarker>
        ))}

        {/* Polylines des trajets */}
        {tripPolylines.map(({ trip, points: coords }) => (
          <Polyline
            key={trip.id}
            positions={coords}
            pathOptions={{
              color: transportColor(trip.transport_mode),
              weight: 3,
              opacity: 0.7,
              dashArray: trip.transport_mode === 'pied' ? '6 4' : undefined,
            }}
            eventHandlers={{
              click: () => onTripSelect(trip),
            }}
          >
            <Tooltip sticky direction="top">
              <span className="text-[11px]">
                {transportLabel(trip.transport_mode)} &middot;{' '}
                {trip.distance_km != null
                  ? `${trip.distance_km.toFixed(1)} km`
                  : `${Math.round(trip.duration_min)} min`}
              </span>
            </Tooltip>
          </Polyline>
        ))}
      </MapContainer>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function transportColor(mode: string | null): string {
  switch (mode) {
    case 'pied':
      return '#30D158';
    case 'vélo':
      return '#5AC8FA';
    case 'voiture':
      return '#FF9500';
    case 'transport':
      return '#9C59FF';
    default:
      return '#8E8E93';
  }
}

function transportLabel(mode: string | null): string {
  switch (mode) {
    case 'pied':
      return 'A pied';
    case 'vélo':
      return 'Velo';
    case 'voiture':
      return 'Voiture';
    case 'transport':
      return 'Transport';
    default:
      return mode || 'Trajet';
  }
}
