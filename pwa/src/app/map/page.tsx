'use client';

import { useState, useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { Map, Loader2 } from 'lucide-react';

import TimelineBar from '@/components/map/TimelineBar';
import DetailSheet from '@/components/map/DetailSheet';
import { jarvisFetch } from '@/lib/api';
import type {
  Place,
  LocationPoint,
  Trip,
  Visit,
  PlacesResponse,
  LocationHistoryResponse,
  VisitsResponse,
  TripsResponse,
} from '@/lib/map-types';

const MapView = dynamic(() => import('@/components/map/MapView'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center bg-[#0a0a0f]">
      <div className="flex flex-col items-center gap-3">
        <Loader2 size={28} className="text-[#4A9EFF] animate-spin" />
        <span className="text-[13px] text-[#666]">Chargement de la carte...</span>
      </div>
    </div>
  ),
});

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

const DAY_NAMES = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
const MONTH_NAMES = [
  'janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'aout', 'septembre', 'octobre', 'novembre', 'decembre',
];

function formatDateHeader(iso: string): string {
  const [y, mo, d] = iso.split('-').map(Number);
  const date = new Date(y!, (mo ?? 1) - 1, d!);
  return `${DAY_NAMES[date.getDay()]} ${d} ${MONTH_NAMES[date.getMonth()]}`;
}

export default function MapPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(isoDate(new Date()));
  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null);
  const [selectedTrip, setSelectedTrip] = useState<Trip | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<LocationPoint | null>(null);

  const places = useQuery<PlacesResponse>({
    queryKey: ['places-map'],
    queryFn: () => jarvisFetch<PlacesResponse>('/api/places'),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const history = useQuery<LocationHistoryResponse>({
    queryKey: ['location-history', selectedDate],
    queryFn: () => jarvisFetch<LocationHistoryResponse>('/api/location/history?hours=48'),
    staleTime: 60_000,
    retry: 1,
  });

  const visits = useQuery<VisitsResponse>({
    queryKey: ['visits-map'],
    queryFn: () => jarvisFetch<VisitsResponse>('/api/visits?days=14'),
    staleTime: 2 * 60_000,
    retry: 1,
  });

  const trips = useQuery<TripsResponse>({
    queryKey: ['trips-map'],
    queryFn: () => jarvisFetch<TripsResponse>('/api/trips?days=14'),
    staleTime: 2 * 60_000,
    retry: 1,
  });

  const placeList = places.data?.places ?? [];
  const historyPoints = history.data?.points ?? [];
  const visitList = visits.data?.visits ?? [];
  const tripList = trips.data?.trips ?? [];

  const filteredPoints = useMemo(() => {
    if (!selectedDate) return historyPoints;
    return historyPoints.filter((p) => isoDate(new Date(p.created_at)) === selectedDate);
  }, [historyPoints, selectedDate]);

  const filteredTrips = useMemo(() => {
    if (!selectedDate) return tripList;
    return tripList.filter((t) => isoDate(new Date(t.started_at)) === selectedDate);
  }, [tripList, selectedDate]);

  const handleDateSelect = useCallback((iso: string) => {
    setSelectedDate(iso);
    setSelectedPlace(null);
    setSelectedTrip(null);
    setSelectedPoint(null);
  }, []);

  const handlePlaceSelect = useCallback((place: Place) => {
    setSelectedPlace(place);
    setSelectedTrip(null);
    setSelectedPoint(null);
  }, []);

  const handleTripSelect = useCallback((trip: Trip) => {
    setSelectedTrip(trip);
    setSelectedPlace(null);
    setSelectedPoint(null);
  }, []);

  const handlePointSelect = useCallback((point: LocationPoint) => {
    setSelectedPoint(point);
    setSelectedPlace(null);
    setSelectedTrip(null);
  }, []);

  const handleCloseSheet = useCallback(() => {
    setSelectedPlace(null);
    setSelectedTrip(null);
    setSelectedPoint(null);
  }, []);

  const isLoading = history.isLoading || places.isLoading;

  return (
    <main className="h-screen flex flex-col bg-[#0a0a0f]">
      {/* Header */}
      <div className="flex-shrink-0 px-5 pt-[max(env(safe-area-inset-top),2rem)] pb-3 bg-gradient-to-b from-[#0a0a0f] to-transparent z-10">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-[22px] font-bold tracking-tight text-white">
              {selectedDate ? formatDateHeader(selectedDate) : 'Carte'}
            </h1>
            <p className="text-[12px] text-[#666] mt-0.5">
              {filteredPoints.length} point{filteredPoints.length > 1 ? 's' : ''}
              {' '}&middot;{' '}
              {visitList.filter((v) => selectedDate && isoDate(new Date(v.arrived_at)) === selectedDate).length} visites
              {' '}&middot;{' '}
              {filteredTrips.length} trajet{filteredTrips.length > 1 ? 's' : ''}
            </p>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-[#666] bg-[rgba(255,255,255,0.04)] rounded-full px-3 py-1.5">
            <Map size={12} />
            <span>
              {placeList.length} lieu{placeList.length > 1 ? 'x' : ''}
            </span>
          </div>
        </div>
      </div>

      {/* Carte */}
      <div className="flex-1 min-h-0 relative">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0a0a0f]/60 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-3">
              <Loader2 size={28} className="text-[#4A9EFF] animate-spin" />
              <span className="text-[13px] text-[#666]">Chargement des donnees...</span>
            </div>
          </div>
        )}
        <MapView
          places={placeList}
          points={filteredPoints}
          trips={filteredTrips}
          selectedPlaceId={selectedPlace?.id ?? null}
          onPlaceSelect={handlePlaceSelect}
          onTripSelect={handleTripSelect}
          onPointSelect={handlePointSelect}
        />
      </div>

      {/* Timeline */}
      <div className="flex-shrink-0" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        <TimelineBar
          visits={visitList}
          trips={tripList}
          selectedDate={selectedDate}
          onDateSelect={handleDateSelect}
          loading={visits.isLoading || trips.isLoading}
        />
      </div>

      {/* Detail Sheet */}
      <DetailSheet
        selectedPlace={selectedPlace}
        selectedPoint={selectedPoint}
        selectedTrip={selectedTrip}
        visits={visitList}
        trips={tripList}
        selectedDate={selectedDate}
        onClose={handleCloseSheet}
      />
    </main>
  );
}
