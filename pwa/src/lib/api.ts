/** Configuration de connexion au backend JARVIS.
 *
 * En dev comme en prod, les appels passent par le proxy inverse
 * defini dans next.config.js (rewrites /api/* -> backend JARVIS).
 * Pas de CORS, pas de mixed-content, pas de HTTPS self-signed.
 */

export const JARVIS_API_URL =
  process.env.NEXT_PUBLIC_JARVIS_API_URL || 'https://localhost:8081';

/**
 * Wrapper fetch vers le backend JARVIS.
 * Les chemins relatifs (/api/...) sont proxies par Next.js.
 */
export async function jarvisFetch<T = unknown>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  try {
    const res = await fetch(path, {
      ...options,
      signal: controller.signal,
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
