import path from 'path'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  oxc: {
    jsx: {
      runtime: 'automatic',
    },
  },
  resolve: {
    alias: {
      '@frontend': path.resolve(__dirname, './src'),
      '@unified': path.resolve(__dirname, './src'),
      '@desktop': path.resolve(__dirname, '../web/src'),
      '@mobile': path.resolve(__dirname, '../pwa/src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test-setup.ts'],
  },
})
