import { describe, expect, it } from 'vitest';

import {
  colorForPlaceCategory,
  colorForTransportMode,
  downsampleChronologicalPoints,
  filterPointsByLocalDate,
  historyPointsToGeoJSON,
  historyTrailToGeoJSON,
  parseRoutePoints,
  placesToGeoJSON,
  tripLineCoordinates,
  tripsToGeoJSON,
  type CartographyPlace,
  type CartographyLocationPoint,
  type CartographyTrip,
} from './cartographyGeojson';

const place = (partial: Partial<CartographyPlace> & Pick<CartographyPlace, 'id' | 'name' | 'latitude' | 'longitude'>): CartographyPlace => ({
  category: 'other',
  ...partial,
});

const point = (partial: Partial<CartographyLocationPoint> & Pick<CartographyLocationPoint, 'id' | 'latitude' | 'longitude' | 'created_at'>): CartographyLocationPoint => ({
  ...partial,
});

describe('placesToGeoJSON', () => {
  it('converts valid places and skips invalid coordinates', () => {
    const fc = placesToGeoJSON([
      place({ id: 1, name: 'Maison', category: 'home', latitude: 50.6, longitude: 3.0 }),
      place({ id: 2, name: 'Bad', category: 'work', latitude: Number.NaN, longitude: 3.0 }),
      place({ id: 3, name: 'École', category: 'school', latitude: 91, longitude: 3.0 }),
    ]);
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].geometry).toEqual({
      type: 'Point',
      coordinates: [3.0, 50.6],
    });
    expect(fc.features[0].properties.category).toBe('home');
    expect(fc.features[0].properties.color).toBe(colorForPlaceCategory('home'));
  });
});

describe('historyPointsToGeoJSON', () => {
  it('marks the latest point and downsamples large histories', () => {
    const points = Array.from({ length: 20 }, (_, i) =>
      point({
        id: i + 1,
        latitude: 50 + i * 0.001,
        longitude: 3 + i * 0.001,
        created_at: `2026-07-20T10:${String(i).padStart(2, '0')}:00Z`,
      }),
    );
    const fc = historyPointsToGeoJSON(points, { latestId: 20, maxPoints: 5 });
    expect(fc.features).toHaveLength(5);
    const latest = fc.features.find((f) => f.properties.isLatest === true);
    expect(latest?.properties.pointId).toBe(20);
  });

  it('ignores points without coordinates', () => {
    const fc = historyPointsToGeoJSON([
      point({ id: 1, latitude: 50, longitude: 3, created_at: '2026-07-20T10:00:00Z' }),
      point({ id: 2, latitude: Number.NaN, longitude: 3, created_at: '2026-07-20T10:01:00Z' }),
    ]);
    expect(fc.features).toHaveLength(1);
  });
});

describe('historyTrailToGeoJSON', () => {
  it('returns empty collection with fewer than two points', () => {
    expect(historyTrailToGeoJSON([
      point({ id: 1, latitude: 50, longitude: 3, created_at: '2026-07-20T10:00:00Z' }),
    ]).features).toHaveLength(0);
  });

  it('builds a chronological LineString', () => {
    const fc = historyTrailToGeoJSON([
      point({ id: 1, latitude: 50, longitude: 3, created_at: '2026-07-20T10:00:00Z' }),
      point({ id: 2, latitude: 50.1, longitude: 3.1, created_at: '2026-07-20T10:01:00Z' }),
    ]);
    expect(fc.features[0].geometry.type).toBe('LineString');
    expect(fc.features[0].geometry.coordinates).toEqual([
      [3, 50],
      [3.1, 50.1],
    ]);
  });
});

describe('tripsToGeoJSON', () => {
  const places: CartographyPlace[] = [
    place({ id: 10, name: 'A', latitude: 50.0, longitude: 3.0, category: 'home' }),
    place({ id: 11, name: 'B', latitude: 50.1, longitude: 3.1, category: 'work' }),
  ];

  it('uses route_points when present ([lat,lng] → GeoJSON [lng,lat])', () => {
    const trip: CartographyTrip = {
      id: 7,
      transport_mode: 'pied',
      route_points: JSON.stringify([
        [50.0, 3.0],
        [50.05, 3.05],
        [50.1, 3.1],
      ]),
    };
    const fc = tripsToGeoJSON([trip], places);
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].properties.transportMode).toBe('pied');
    expect(fc.features[0].properties.color).toBe(colorForTransportMode('pied'));
    expect(fc.features[0].geometry.coordinates).toEqual([
      [3.0, 50.0],
      [3.05, 50.05],
      [3.1, 50.1],
    ]);
  });

  it('falls back to place endpoints when route_points is missing', () => {
    const trip: CartographyTrip = {
      id: 8,
      from_place_id: 10,
      to_place_id: 11,
      transport_mode: 'voiture',
    };
    const coords = tripLineCoordinates(trip, new Map(places.map((p) => [p.id, p])));
    expect(coords).toEqual([
      [3.0, 50.0],
      [3.1, 50.1],
    ]);
    const fc = tripsToGeoJSON([trip], places);
    expect(fc.features[0].properties.tripId).toBe(8);
  });

  it('skips trips without drawable geometry', () => {
    const fc = tripsToGeoJSON([{ id: 9, transport_mode: 'inconnu' }], places);
    expect(fc.features).toHaveLength(0);
  });
});

describe('parseRoutePoints', () => {
  it('returns empty for invalid JSON or malformed entries', () => {
    expect(parseRoutePoints([[1, 2], ['a', 'b'] as unknown as [number, number]])).toEqual([[2, 1]]);
    expect(parseRoutePoints('not-json')).toEqual([]);
    expect(parseRoutePoints([[Number.NaN, 1] as [number, number]])).toEqual([]);
  });
});

describe('filterPointsByLocalDate', () => {
  it('filters by local YYYY-MM-DD when provided', () => {
    const points = [
      point({ id: 1, latitude: 50, longitude: 3, created_at: '2026-07-20T10:00:00' }),
      point({ id: 2, latitude: 50, longitude: 3, created_at: '2026-07-21T10:00:00' }),
    ];
    expect(filterPointsByLocalDate(points, '2026-07-20')).toHaveLength(1);
    expect(filterPointsByLocalDate(points, null)).toHaveLength(2);
  });
});

describe('downsampleChronologicalPoints', () => {
  it('keeps first and last when sampling', () => {
    const values = Array.from({ length: 100 }, (_, i) => i);
    const sampled = downsampleChronologicalPoints(values, 10);
    expect(sampled[0]).toBe(0);
    expect(sampled[sampled.length - 1]).toBe(99);
    expect(sampled.length).toBeLessThanOrEqual(10);
  });
});
