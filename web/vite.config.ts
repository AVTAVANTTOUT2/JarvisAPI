import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'
import { VitePWA } from 'vite-plugin-pwa'

// Backend FastAPI — HTTPS si certs/cert.pem existe, HTTP sinon
import { existsSync } from 'fs'
const BACKEND_HTTPS = existsSync(path.resolve(__dirname, '../certs/cert.pem'))
const BACKEND_ORIGIN = BACKEND_HTTPS ? 'https://localhost:8081' : 'http://localhost:8081'

// Supervisor (port 9000) — toujours HTTP
const SUPERVISOR_ORIGIN = 'http://127.0.0.1:9000'

function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id: string) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

export default defineConfig({
  plugins: [
    figmaAssetResolver(),
    react(),
    tailwindcss(),
    basicSsl(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      injectRegister: 'auto',
      manifest: {
        name: 'JARVIS',
        short_name: 'JARVIS',
        description: 'Assistant personnel de Nolann',
        start_url: '/chat',
        scope: '/',
        display: 'standalone',
        background_color: '#0a0a0f',
        theme_color: '#0a0a0f',
        orientation: 'portrait',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      injectManifest: {
        // Le shell JS/CSS/HTML uniquement — jamais /api/* (données
        // personnelles) dans le précache du Service Worker.
        globPatterns: ['**/*.{js,css,html,svg,woff2}'],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  resolve: {
    alias: {
      '@desktop': path.resolve(__dirname, './src'),
      '@unified': path.resolve(__dirname, '../frontend/src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          motion: ['framer-motion'],
        },
      },
    },
  },
  server: {
    fs: { allow: [path.resolve(__dirname, '..')] },
    port: 5173,
    host: '0.0.0.0',
    https: {},
    proxy: {
      '/api/supervisor': { target: SUPERVISOR_ORIGIN, changeOrigin: true, secure: false },
      '/ws/supervisor': { target: SUPERVISOR_ORIGIN.replace('http', 'ws'), changeOrigin: true, secure: false, ws: true },
      '/api':    { target: BACKEND_ORIGIN, changeOrigin: true, secure: false },
      '/upload': { target: BACKEND_ORIGIN, changeOrigin: true, secure: false },
      '/ws':     { target: BACKEND_ORIGIN, changeOrigin: true, secure: false, ws: true },
    },
  },
})
