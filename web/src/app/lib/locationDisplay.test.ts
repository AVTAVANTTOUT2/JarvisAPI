import { describe, expect, it } from 'vitest';

import { getLocationDisplayStatus, mapLocationHistory } from '@unified/lib/locationDisplay';

describe('getLocationDisplayStatus', () => {
  const now = Date.parse('2026-07-16T14:00:00Z');

  it('returns the honest empty state without a point', () => {
    expect(getLocationDisplayStatus(undefined, now)).toEqual({
      freshness: 'empty',
      label: 'Aucune position reçue depuis le téléphone',
    });
  });

  it('labels a point received less than five minutes ago as recent', () => {
    const [point] = mapLocationHistory({
      points: [{ id: 1, latitude: 50.1, longitude: 3.1, created_at: '2026-07-16T13:59:18Z' }],
    });

    expect(getLocationDisplayStatus(point, now)).toEqual({
      freshness: 'recent',
      label: 'Position reçue il y a 42 secondes',
    });
  });

  it('labels an old point as stale', () => {
    const [point] = mapLocationHistory({
      points: [{ id: 1, latitude: 50.1, longitude: 3.1, created_at: '2026-07-16T13:42:00Z' }],
    });

    expect(getLocationDisplayStatus(point, now)).toEqual({
      freshness: 'stale',
      label: 'Dernière position trop ancienne — il y a 18 minutes',
    });
  });
});

describe('mapLocationHistory', () => {
  it('maps numeric coordinates and timestamp from the backend response', () => {
    const result = mapLocationHistory({
      points: [{
        id: 7,
        latitude: '50.123',
        longitude: '3.456',
        accuracy: 12,
        source: 'android_background',
        created_at: '2026-07-16T13:59:18',
      }],
    });

    expect(result[0]).toMatchObject({
      id: 7,
      latitude: 50.123,
      longitude: 3.456,
      source: 'android_background',
      created_at: '2026-07-16T13:59:18',
    });
  });

  it('drops malformed coordinates instead of rendering a fake marker', () => {
    const result = mapLocationHistory({
      points: [{ id: 7, latitude: null, longitude: 'invalid', created_at: '2026-07-16T13:59:18' }],
    });

    expect(result).toEqual([]);
  });
});
