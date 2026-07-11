/**
 * Configuration Next.js pour la PWA mobile JARVIS.
 *
 * Deux modes :
 * 1. DEVELOPMENT (npm run dev) : next-pwa actif, rewrites proxy /api/* vers
 *    le backend. Service worker regenere a chaque build.
 * 2. PRODUCTION (npm run build) : export statique (output: 'export'), pas de
 *    next-pwa (incompatible avec l'export). Le service worker dans public/
 *    est servi directement ; il doit avoir ete genere au prealable.
 */

const isDev = process.env.NODE_ENV === 'development';

// En dev, on wrap avec next-pwa pour regenerer le SW. En export statique,
// next-pwa bloque l'export — on wrap conditionnellement.
const withPWA = !isDev
  ? (config) => config // no-op en prod
  : require('next-pwa')({
      dest: 'public',
      register: false,
      skipWaiting: true,
      disable: false,
    });

/** @type {import('next').NextConfig} */
const baseConfig = {
  // ── Static export (production uniquement) ──
  // La PWA est servie par FastAPI sous /m/ — meme origine HTTP que le
  // backend, donc l'auth (cookie jarvis_session) est automatiquement
  // transmise sans reconfiguration.
  ...(!isDev && {
    output: 'export',
    basePath: '/m',
  }),

  images: { unoptimized: true },
  reactStrictMode: true,
  trailingSlash: false,

  // ── Proxy API (development uniquement) ──
  ...(isDev && {
    async rewrites() {
      const jarvisUrl =
        process.env.NEXT_PUBLIC_JARVIS_API_URL || 'http://localhost:8081';
      return [
        {
          source: '/api/:path*',
          destination: `${jarvisUrl}/api/:path*`,
        },
      ];
    },
  }),

  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
    ];
  },
};

module.exports = withPWA(baseConfig);
