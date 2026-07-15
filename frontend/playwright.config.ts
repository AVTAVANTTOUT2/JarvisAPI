import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:3106',
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      grep: /@desktop/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'mobile',
      grep: /@mobile/,
      use: { ...devices['iPhone 14'], browserName: 'chromium', channel: 'chrome' },
    },
    {
      name: 'static-csp',
      grep: /@static-csp/,
      use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:3107' },
    },
  ],
  webServer: [
    {
      command: 'pnpm exec next dev --hostname 127.0.0.1 --port 3106',
      url: 'http://127.0.0.1:3106',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'python3.12 e2e/serve-static-csp.py 3107',
      url: 'http://127.0.0.1:3107',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
