import {
  expect,
  test,
  type Page,
  type Route,
  type WebSocketRoute,
} from '@playwright/test'

type ApiOverride = (route: Route, pathname: string) => Promise<boolean>

type PageHealth = {
  assertClean: () => void
}

function monitorPageHealth(page: Page): PageHealth {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const failedRequests: string[] = []
  const errorResponses: string[] = []

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('requestfailed', (request) => {
    failedRequests.push(`${request.method()} ${request.url()} — ${request.failure()?.errorText ?? 'échec'}`)
  })
  page.on('response', (response) => {
    if (response.status() >= 400) {
      errorResponses.push(`${response.status()} ${response.url()}`)
    }
  })

  return {
    assertClean: () => {
      expect(consoleErrors, 'erreurs console').toEqual([])
      expect(pageErrors, 'exceptions page').toEqual([])
      expect(failedRequests, 'requêtes échouées').toEqual([])
      expect(errorResponses, 'réponses HTTP en erreur').toEqual([])
    },
  }
}

function authStatus(authenticated: boolean) {
  return {
    configured: true,
    authenticated,
    csrf_token: authenticated ? 'e2e-csrf-token' : null,
    locked_out: false,
    lockout_seconds: 0,
    lockout_scope: null,
    local_recovery_available: false,
    auto_lock_minutes: 5,
  }
}

function defaultApiResponse(pathname: string): object {
  if (pathname === '/api/tasks') return { tasks: [] }
  if (pathname === '/api/conversations') return { conversations: [] }
  if (pathname === '/api/status') {
    return {
      today: {
        msg_count: 0,
        turn_count: 0,
        total_in: 0,
        total_out: 0,
      },
    }
  }
  if (pathname === '/api/stats/weekly') {
    return {
      days: [],
      change: {
        messages_pct: null,
        voice_pct: null,
        turns_pct: null,
        interactions_pct: null,
        cost_pct: null,
      },
      totals: {
        msg_count: 0,
        voice_count: 0,
        turn_count: 0,
        tokens_in: 0,
        tokens_out: 0,
        cost: 0,
      },
    }
  }
  if (pathname === '/api/privacy/documents') {
    return {
      mode: 'strict_local',
      strict_local: true,
      cloud_provider: 'deepseek',
      cloud_summary_available: false,
      explicit_consent_required: true,
      cloud_max_chars: 0,
      pii_protection: 'local',
      features: {},
    }
  }
  if (pathname.startsWith('/api/notifications')) return { notifications: [] }
  return {}
}

async function mockApi(
  page: Page,
  options: {
    authenticated?: boolean | (() => boolean)
    override?: ApiOverride
    sseEvent?: object
  } = {},
) {
  const authenticated = options.authenticated ?? true

  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/events/stream') {
      const body = options.sseEvent
        ? `data: ${JSON.stringify(options.sseEvent)}\n\n`
        : ': jarvis-e2e keep-alive\n\n'
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: {
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        },
        body,
      })
      return
    }
    if (pathname === '/api/auth/status') {
      const active = typeof authenticated === 'function' ? authenticated() : authenticated
      await route.fulfill({ json: authStatus(active) })
      return
    }
    if (options.override && await options.override(route, pathname)) return
    await route.fulfill({ json: defaultApiResponse(pathname) })
  })
}

async function mockJarvisWebSocket(
  page: Page,
  onMessage?: (message: string, socket: WebSocketRoute) => void,
) {
  await page.routeWebSocket('**/ws', (socket) => {
    socket.onMessage((message) => onMessage?.(String(message), socket))
    setTimeout(() => {
      socket.send(JSON.stringify({ type: 'connected', conversation_id: 71 }))
    }, 50)
  })
}

test('@desktop unlocks through the real auth form before showing private content', async ({ page }) => {
  let authenticated = false
  let unlockPayload: object | null = null
  const health = monitorPageHealth(page)

  await mockJarvisWebSocket(page)
  await mockApi(page, {
    authenticated: () => authenticated,
    override: async (route, pathname) => {
      if (pathname !== '/api/auth/unlock') return false
      unlockPayload = route.request().postDataJSON() as object
      authenticated = true
      await route.fulfill({
        json: { ok: true, csrf_token: 'e2e-csrf-token' },
      })
      return true
    },
  })

  await page.goto('/dashboard')
  await expect(page.getByText('Application verrouillée')).toBeVisible()
  await page.getByLabel('Code de déverrouillage').fill('123456')
  await page.getByRole('button', { name: 'Déverrouiller' }).click()

  await expect(page.getByText('Tours utilisateur')).toBeVisible()
  expect(unlockPayload).toEqual({ secret: '123456' })
  health.assertClean()
})

test('@desktop keeps an idle lock across reload and reauthenticates with verify', async ({ page }) => {
  let verifyPayload: object | null = null
  let unlockCalled = false
  const health = monitorPageHealth(page)

  await page.addInitScript(() => localStorage.setItem('jarvis:soft-lock', '1'))
  await mockJarvisWebSocket(page)
  await mockApi(page, {
    override: async (route, pathname) => {
      if (pathname === '/api/auth/unlock') {
        unlockCalled = true
        await route.fulfill({ json: { ok: true, csrf_token: 'e2e-csrf-token' } })
        return true
      }
      if (pathname !== '/api/auth/verify') return false
      verifyPayload = route.request().postDataJSON() as object
      await route.fulfill({ json: { ok: true } })
      return true
    },
  })

  await page.goto('/dashboard')
  await expect(page.getByText('Application verrouillée')).toBeVisible()
  await expect(page.getByText('Tours utilisateur')).not.toBeVisible()
  await page.getByLabel('Code de déverrouillage').fill('passphrase-correcte')
  await page.getByRole('button', { name: 'Déverrouiller' }).click()

  await expect(page.getByText('Tours utilisateur')).toBeVisible()
  expect(verifyPayload).toEqual({ secret: 'passphrase-correcte' })
  expect(unlockCalled).toBe(false)
  await expect.poll(() => page.evaluate(() => localStorage.getItem('jarvis:soft-lock'))).toBeNull()
  health.assertClean()
})

test('@desktop sends a chat turn over WebSocket and renders the response', async ({ page }) => {
  const outgoing: object[] = []
  let chatSocket: WebSocketRoute | null = null
  let conversationLoads = 0
  const health = monitorPageHealth(page)

  await mockApi(page, {
    override: async (route, pathname) => {
      if (pathname !== '/api/conversations') return false
      conversationLoads += 1
      await route.fulfill({ json: { conversations: [] } })
      return true
    },
  })
  await mockJarvisWebSocket(page, (message, socket) => {
    chatSocket = socket
    const payload = JSON.parse(message) as object & { type?: string }
    outgoing.push(payload)
  })

  await page.goto('/chat')
  await expect.poll(() => conversationLoads).toBeGreaterThan(0)
  const composer = page.getByPlaceholder('Message a JARVIS...')
  await composer.fill('Bonjour depuis Playwright')
  await composer.press('Enter')

  await expect(page.getByText('Bonjour depuis Playwright')).toBeVisible()
  await expect.poll(() => outgoing).toContainEqual({
    type: 'text',
    content: 'Bonjour depuis Playwright',
    stream: true,
    tts: false,
  })
  expect(chatSocket).not.toBeNull()
  chatSocket!.send(JSON.stringify({
    type: 'response',
    content: 'Réponse WebSocket validée',
    agent: 'info',
  }))
  await expect(page.getByText('Réponse WebSocket validée')).toBeVisible()
  await expect(page.getByText('info', { exact: true })).toHaveCount(0)
  health.assertClean()
})

test('@desktop creates and updates a task with CSRF on direct navigation', async ({ page }) => {
  const writes: Array<{ method: string; body: object; csrf: string | null }> = []
  const health = monitorPageHealth(page)

  await mockJarvisWebSocket(page)
  await mockApi(page, {
    override: async (route, pathname) => {
      const request = route.request()
      if (pathname !== '/api/tasks' && !pathname.startsWith('/api/tasks/')) return false
      if (request.method() === 'GET') {
        await route.fulfill({ json: { tasks: [] } })
        return true
      }

      const body = request.postDataJSON() as Record<string, unknown>
      writes.push({
        method: request.method(),
        body,
        csrf: request.headers()['x-csrf-token'] ?? null,
      })
      if (request.method() === 'POST') {
        await route.fulfill({
          json: {
            task: {
              id: 91,
              title: body.title,
              description: null,
              priority: body.priority,
              status: 'todo',
              due_date: null,
              category: null,
              created_at: new Date().toISOString(),
              completed_at: null,
            },
          },
        })
        return true
      }
      await route.fulfill({ json: { ok: true } })
      return true
    },
  })

  await page.goto('/tasks')
  await expect(page.getByRole('heading', { name: 'Tâches' })).toBeVisible()
  await page.getByRole('button', { name: 'Nouvelle tâche' }).click()
  await page.getByPlaceholder('Titre de la tâche...').fill('Valider Playwright en CI')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()

  await expect(page.getByText('Valider Playwright en CI')).toBeVisible()
  const todoButton = page.locator('button[title^="Statut: À faire"]')
  await todoButton.click()
  await expect(page.locator('button[title^="Statut: En cours"]')).toBeVisible()

  expect(writes).toEqual([
    {
      method: 'POST',
      body: { title: 'Valider Playwright en CI', priority: 'medium' },
      csrf: 'e2e-csrf-token',
    },
    {
      method: 'PATCH',
      body: { status: 'doing' },
      csrf: 'e2e-csrf-token',
    },
  ])
  health.assertClean()
})

test('@desktop consumes an SSE event on the direct Mission Control route', async ({ page }) => {
  const health = monitorPageHealth(page)
  const event = {
    type: 'agent.response',
    agent: 'info',
    timestamp: 1_700_000_000,
    data: {
      content: 'Réponse SSE validée',
      tokens_in: 12,
      tokens_out: 8,
      cost: 0,
      latency_ms: 42,
    },
  }

  await mockJarvisWebSocket(page)
  await mockApi(page, { sseEvent: event })
  await page.goto('/mission')

  await expect(page.getByText('JARVIS MISSION CONTROL')).toBeVisible()
  await expect(page.getByText(/Réponse SSE validée/).first()).toBeVisible()
  await expect(page.getByText('1 events')).toBeVisible()
  health.assertClean()
})
