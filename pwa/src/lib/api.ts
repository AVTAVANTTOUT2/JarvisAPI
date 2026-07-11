/** Configuration de connexion au backend JARVIS.
 *
 * En export statique, la PWA est servie depuis le meme serveur HTTP
 * que le backend (FastAPI) — meme origine, meme port.
 * Les appels /api/* atteignent directement le backend, sans proxy.
 * Le cookie d'auth (jarvis_session) est automatiquement transmis
 * car il est SameSite=Lax et meme origine.
 *
 * La variable d'environnement JARVIS_API_URL permet de pointer vers
 * un backend different en dev (ex: http://localhost:8081) mais n'est
 * pas utilisee en export statique (les chemins relatifs suffisent).
 */

export const JARVIS_API_URL =
  process.env.NEXT_PUBLIC_JARVIS_API_URL || '';

/**
 * Wrapper fetch vers le backend JARVIS.
 * Les chemins relatifs (/api/...) atteignent le backend directement
 * puisque la PWA est servie depuis la meme origine HTTP que FastAPI.
 */
export async function jarvisFetch<T = unknown>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  try {
    const url = JARVIS_API_URL ? `${JARVIS_API_URL}${path}` : path;
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      console.warn(`[jarvisFetch] ${path} → ${res.status}: ${body.slice(0, 200)}`);
      throw new Error(`JARVIS ${res.status}`);
    }

    return (await res.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}
