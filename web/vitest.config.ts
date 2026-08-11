import path from 'path'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@jarvis/auth': path.resolve(__dirname, './node_modules/@jarvis/auth'),
      '@desktop': path.resolve(__dirname, './src'),
      '@unified': path.resolve(__dirname, '../frontend/src'),
      // Les modules @unified vivent sous frontend/ ; Vite résout sinon leurs
      // dépendances depuis frontend/node_modules (absent du job CI web).
      idb: path.resolve(__dirname, './node_modules/idb'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
