import { describe, expect, it } from 'vitest';

import {
  getLocationDisplayStatus,
  mapLocationHistory,
  parseLocationTimestamp,
  resolveDisplayLocationPoint,
} from '@unified/lib/locationDisplay';

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

describe('parseLocationTimestamp', () => {
  it('parses timezone-less backend timestamps as local wall clock', () => {
    const ms = parseLocationTimestamp('2026-07-16T17:23:50');
    const d = new Date(ms);
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(6);
    expect(d.getDate()).toBe(16);
    expect(d.getHours()).toBe(17);
    expect(d.getMinutes()).toBe(23);
  });
});

describe('resolveDisplayLocationPoint', () => {
  it('prefers the latest history point over status', () => {
    const history = mapLocationHistory({
      points: [
        { id: 1, latitude: 1, longitude: 1, created_at: '2026-07-16T10:00:00' },
        { id: 2, latitude: 2, longitude: 2, created_at: '2026-07-16T11:00:00' },
      ],
    });
    const status = mapLocationHistory({
      points: [{ id: 9, latitude: 9, longitude: 9, created_at: '2026-07-16T09:00:00' }],
    })[0];
    expect(resolveDisplayLocationPoint(history, status)?.id).toBe(2);
  });

  it('falls back to status when history is empty', () => {
    const status = mapLocationHistory({
      points: [{ id: 9, latitude: 9, longitude: 9, created_at: '2026-07-16T09:00:00' }],
    })[0];
    expect(resolveDisplayLocationPoint([], status)?.id).toBe(9);
  });
});
