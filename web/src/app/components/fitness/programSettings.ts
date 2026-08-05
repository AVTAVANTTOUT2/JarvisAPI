export const PROGRAM_NUMBER_FIELDS = [
  { key: 'calories_min', label: 'Calories min', min: 0, max: 20_000 },
  { key: 'calories_max', label: 'Calories max', min: 0, max: 20_000 },
  { key: 'protein_min_g', label: 'Protéines min', min: 0, max: 1_000 },
  { key: 'protein_max_g', label: 'Protéines max', min: 0, max: 1_000 },
  { key: 'weekly_min_sessions', label: 'Séances minimum', min: 1, max: 7 },
  { key: 'reminder_interval_min', label: 'Rappel toutes les min', min: 30, max: 720 },
] as const;

export type ProgramNumberKey = (typeof PROGRAM_NUMBER_FIELDS)[number]['key'];

export function parseProgramNumber(rawValue: string): number | undefined {
  const normalized = rawValue.trim();
  if (!normalized) return undefined;
  const value = Number(normalized);
  return Number.isFinite(value) ? value : undefined;
}

export function validateProgramSettings(settings: Record<string, unknown>): string | null {
  for (const field of PROGRAM_NUMBER_FIELDS) {
    const value = settings[field.key];
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      return `${field.label} est requis.`;
    }
    if (!Number.isInteger(value) || value < field.min || value > field.max) {
      return `${field.label} doit être compris entre ${field.min} et ${field.max}.`;
    }
  }

  const reminderTime = settings.reminder_time;
  if (typeof reminderTime !== 'string' || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(reminderTime)) {
    return 'Premier rappel doit contenir une heure valide.';
  }
  if (Number(settings.calories_min) > Number(settings.calories_max)) {
    return 'Calories min doit être inférieur ou égal à Calories max.';
  }
  if (Number(settings.protein_min_g) > Number(settings.protein_max_g)) {
    return 'Protéines min doit être inférieur ou égal à Protéines max.';
  }
  return null;
}
