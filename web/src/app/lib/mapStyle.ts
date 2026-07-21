/**
 * Configuration du style cartographique (MapLibre).
 *
 * Défaut : OpenFreeMap Dark (sans clé API).
 * Remplaçable par un style auto-hébergé ou, plus tard, un style
 * pointant vers des tuiles locales (ex. pmtiles:///maps/europe.pmtiles).
 *
 * Le service public OpenFreeMap peut être remplacé à tout moment par
 * un style / des tuiles hébergés localement — voir Architecture/01_CARTOGRAPHIE.md.
 */

export const OPENFREEMAP_DARK_STYLE_URL =
  'https://tiles.openfreemap.org/styles/dark';

export const MAP_ATTRIBUTION_HTML =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> &copy; <a href="https://openfreemap.org" target="_blank" rel="noopener noreferrer">OpenFreeMap</a>';

/**
 * Résout l'URL du style MapLibre depuis une valeur d'environnement
 * (Vite: `VITE_MAP_STYLE_URL`, Next DefinePlugin: même clé).
 *
 * Accepte déjà les préfixes futurs `pmtiles://` et les chemins locaux
 * `/styles/...` servis par le backend — sans enregistrer encore le
 * protocole PMTiles (dépendances reportées à une PR dédiée).
 */
export function resolveMapStyleUrl(
  envValue: string | undefined | null = readViteMapStyleEnv(),
): string {
  const raw = typeof envValue === 'string' ? envValue.trim() : '';
  if (!raw) return OPENFREEMAP_DARK_STYLE_URL;
  return raw;
}

/** Indique si l'URL cible un protocole PMTiles (préparation future). */
export function isPmtilesStyleUrl(styleUrl: string): boolean {
  return styleUrl.trim().toLowerCase().startsWith('pmtiles://');
}

function readViteMapStyleEnv(): string | undefined {
  try {
    // Vite / Next DefinePlugin injectent import.meta.env.VITE_MAP_STYLE_URL.
    const meta = import.meta as ImportMeta & {
      env?: { VITE_MAP_STYLE_URL?: string };
    };
    return meta.env?.VITE_MAP_STYLE_URL;
  } catch {
    return undefined;
  }
}
