const withPWA = require('next-pwa')({
  dest: 'public',
  register: false,
  skipWaiting: true,
  disable: process.env.NODE_ENV === 'development',
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Proxy /api/* vers le backend JARVIS (FastAPI :8081)
  // Evite CORS + mixed-content. Le backend doit tourner en HTTP (WEB_HTTPS=false).
  async rewrites() {
    const jarvisUrl = process.env.NEXT_PUBLIC_JARVIS_API_URL || 'http://localhost:8081';
    return [
      {
        source: '/api/:path*',
        destination: `${jarvisUrl}/api/:path*`,
      },
    ];
  },

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

module.exports = withPWA(nextConfig);
