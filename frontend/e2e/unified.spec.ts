import { expect, test, type Page } from '@playwright/test'

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
    const response = pathname === '/api/tasks'
      ? { tasks: [] }
      : pathname.startsWith('/api/notifications')
        ? { notifications: [] }
        : pathname === '/api/calendar'
          ? { events: [] }
          : pathname === '/api/briefing'
            ? { kind: 'morning', content: '' }
            : {}
    await route.fulfill({ json: response })
  })
}

test('@desktop serves the complete navigation behind the shared auth gate', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/chat')

  await expect(page.getByText('Conversation', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Mission Control' }).first()).toBeVisible()
  await expect(page.getByTestId('lock-gate')).toHaveCount(0)
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
