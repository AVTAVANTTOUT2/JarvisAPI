import { describe, expect, it } from 'vitest';

import {
  OPENFREEMAP_DARK_STYLE_URL,
  isPmtilesStyleUrl,
  resolveMapStyleUrl,
} from './mapStyle';

describe('resolveMapStyleUrl', () => {
  it('falls back to OpenFreeMap Dark when env is empty', () => {
    expect(resolveMapStyleUrl(undefined)).toBe(OPENFREEMAP_DARK_STYLE_URL);
    expect(resolveMapStyleUrl(null)).toBe(OPENFREEMAP_DARK_STYLE_URL);
    expect(resolveMapStyleUrl('')).toBe(OPENFREEMAP_DARK_STYLE_URL);
    expect(resolveMapStyleUrl('   ')).toBe(OPENFREEMAP_DARK_STYLE_URL);
  });

  it('uses the provided environment value when set', () => {
    expect(resolveMapStyleUrl('https://example.local/styles/dark.json')).toBe(
      'https://example.local/styles/dark.json',
    );
  });

  it('preserves pmtiles:// URLs for future local hosting', () => {
    const url = 'pmtiles:///maps/europe.pmtiles';
    expect(resolveMapStyleUrl(url)).toBe(url);
    expect(isPmtilesStyleUrl(url)).toBe(true);
    expect(isPmtilesStyleUrl(OPENFREEMAP_DARK_STYLE_URL)).toBe(false);
  });
});
