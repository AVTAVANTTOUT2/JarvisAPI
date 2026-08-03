import { describe, expect, it } from 'vitest';

import { parseProgramNumber, validateProgramSettings } from './programSettings';

const validSettings = {
  calories_min: 2_800,
  calories_max: 3_400,
  protein_min_g: 140,
  protein_max_g: 190,
  weekly_min_sessions: 4,
  reminder_interval_min: 90,
  reminder_time: '18:00',
};

describe('fitness program settings', () => {
  it('keeps an empty numeric field empty instead of coercing it to zero', () => {
    expect(parseProgramNumber('')).toBeUndefined();
    expect(parseProgramNumber('   ')).toBeUndefined();
    expect(parseProgramNumber('42')).toBe(42);
  });

  it('rejects missing, invalid and inverted targets before PATCH', () => {
    expect(validateProgramSettings({ ...validSettings, calories_min: undefined })).toContain('requis');
    expect(validateProgramSettings({ ...validSettings, weekly_min_sessions: 0 })).toContain('entre 1 et 7');
    expect(validateProgramSettings({ ...validSettings, reminder_time: '' })).toContain('heure valide');
    expect(validateProgramSettings({ ...validSettings, protein_min_g: 200 })).toContain('inférieur ou égal');
  });

  it('accepts a complete payload matching the backend constraints', () => {
    expect(validateProgramSettings(validSettings)).toBeNull();
  });
});
