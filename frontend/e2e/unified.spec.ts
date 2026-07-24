import { expect, test, type Page } from '@playwright/test'

function emptyApiResponse(pathname: string): object {
  if (pathname === '/api/tasks') return { tasks: [] }
  if (pathname.startsWith('/api/notifications')) return { notifications: [] }
  if (pathname === '/api/calendar') return { events: [] }
  if (pathname === '/api/briefing') return { kind: 'morning', content: '' }
  if (pathname === '/api/status') {
    return {
      today: {
        msg_count: 12,
        turn_count: 5,
        total_in: 999,
        total_out: 1001,
      },
    }
  }
  if (pathname === '/api/stats/weekly') {
    return {
      days: [],
      change: {
        messages_pct: null,
        voice_pct: null,
        turns_pct: 25,
        interactions_pct: 25,
        cost_pct: null,
      },
      totals: {
        msg_count: 12,
        voice_count: 0,
        turn_count: 5,
        tokens_in: 999,
        tokens_out: 1001,
        cost: 0,
      },
    }
  }
  if (pathname === '/api/places') return { places: [] }
  if (pathname.startsWith('/api/visits')) return { visits: [] }
  if (pathname === '/api/trips') return { trips: [] }
  if (pathname === '/api/location/patterns') return { patterns: [] }
  if (pathname === '/api/location/status') {
    return {
      tracking_enabled: true,
      current_location: null,
      points_24h: 1,
    }
  }
  if (pathname === '/api/location/history') {
    return {
      points: [{
        id: 1,
        latitude: 48.8566,
        longitude: 2.3522,
        accuracy: 8,
        source: 'e2e',
        created_at: new Date().toISOString(),
      }],
    }
  }
  return {}
}

async function mockApi(page: Page, authenticated: boolean) {
  await page.route('**/api/events/stream', (route) => route.abort())
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/auth/status') {
      await route.fulfill({
        json: {
          configured: true,
          authenticated,
          locked_out: false,
          lockout_seconds: 0,
          auto_lock_minutes: 5,
        },
      })
      return
    }
    await route.fulfill({ json: emptyApiResponse(pathname) })
  })
}

test('@desktop serves the complete navigation behind the shared auth gate', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/chat')

  await expect(page.getByText('Conversation', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Mission Control' }).first()).toBeVisible()
  await expect(page.getByTestId('lock-gate')).toHaveCount(0)

  await page.goto('/dashboard')
  const turns = page.getByText('Tours utilisateur').locator('..')
  await expect(turns.getByText('5', { exact: true })).toBeVisible()
})

test('@mobile selects the responsive dashboard', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/dashboard')

  await expect(page.getByRole('heading', { name: /^(Bonjour|Bonsoir), Elias\.$/ })).toBeVisible()
  await expect(page.getByRole('navigation')).toBeVisible()
  await expect(page.getByTestId('lock-gate')).toHaveCount(0)
})

test('@mobile never reveals private content without an authenticated session', async ({ page }) => {
  await mockApi(page, false)
  await page.goto('/dashboard')

  await expect(page.getByText('Application verrouillée')).toBeVisible()
  await expect(page.getByRole('heading', { name: /Elias/ })).toHaveCount(0)
})

test('@static-csp shows initial PIN setup after static export with security headers', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })

  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/auth/status') {
      await route.fulfill({
        json: {
          configured: false,
          authenticated: false,
          locked_out: false,
          lockout_seconds: 0,
          auto_lock_minutes: 5,
        },
      })
      return
    }
    await route.fulfill({ status: 428, json: { error: 'setup_required' } })
  })

  await page.goto('/')

  await expect(page.getByTestId('lock-gate')).toBeVisible()
  await expect(page.getByText('Définissez votre code de déverrouillage')).toBeVisible()
  await expect(page.getByPlaceholder('Nouveau code (4+ caractères)')).toBeVisible()
  expect(consoleErrors.some((line) => line.includes('Connection closed'))).toBe(false)
})

test('@static-csp loads MapLibre workers and OpenFreeMap resources without CSP violations', async ({ page }) => {
  const openFreeMapRequests: string[] = []
  const consoleCspErrors: string[] = []

  await page.addInitScript(() => {
    const target = window as typeof window & {
      __jarvisCspViolations?: Array<{ directive: string; blockedUri: string }>
    }
    target.__jarvisCspViolations = []
    document.addEventListener('securitypolicyviolation', (event) => {
      target.__jarvisCspViolations?.push({
        directive: event.effectiveDirective,
        blockedUri: event.blockedURI,
      })
    })
  })
  page.on('console', (message) => {
    if (
      message.type() === 'error'
      && /content security policy|violates the following directive/i.test(message.text())
    ) {
      consoleCspErrors.push(message.text())
    }
  })

  await mockApi(page, true)
  await page.route('https://tiles.openfreemap.org/**', async (route) => {
    const url = route.request().url()
    openFreeMapRequests.push(url)
    if (url.endsWith('/styles/dark')) {
      await route.fulfill({
        contentType: 'application/json',
        json: {
          version: 8,
          sources: {
            openfreemap_csp_test: {
              type: 'raster',
              tileSize: 512,
              tiles: ['https://tiles.openfreemap.org/csp-test/{z}/{x}/{y}.png'],
            },
          },
          layers: [{
            id: 'openfreemap-csp-test',
            type: 'raster',
            source: 'openfreemap_csp_test',
          }],
        },
      })
      return
    }
    if (url.includes('/csp-test/')) {
      await route.fulfill({
        contentType: 'image/png',
        path: `${process.cwd()}/public/icons/icon-512.png`,
      })
      return
    }
    await route.abort()
  })

  await page.goto('/map')

  await expect(page.getByText('Carte Interactive')).toBeVisible()
  await expect(page.locator('canvas.maplibregl-canvas')).toBeVisible()
  await expect.poll(
    () => openFreeMapRequests.some((url) => url.includes('/csp-test/')),
    { timeout: 15_000 },
  ).toBe(true)

  const violations = await page.evaluate(() => {
    const target = window as typeof window & {
      __jarvisCspViolations?: Array<{ directive: string; blockedUri: string }>
    }
    return target.__jarvisCspViolations ?? []
  })
  expect(violations).toEqual([])
  expect(consoleCspErrors).toEqual([])
  await expect(page.getByText(/Tuiles OpenFreeMap indisponibles/)).toHaveCount(0)
})
