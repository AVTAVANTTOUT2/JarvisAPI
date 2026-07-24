import path from 'path'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@jarvis/auth': path.resolve(__dirname, './node_modules/@jarvis/auth'),
      '@desktop': path.resolve(__dirname, './src'),
      '@unified': path.resolve(__dirname, '../frontend/src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
