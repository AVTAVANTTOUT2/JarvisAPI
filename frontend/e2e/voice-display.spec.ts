import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'
import path from 'node:path'

const now = '2026-08-29T10:00:00.000Z'

function session(overrides: Record<string, unknown> = {}) {
  return {
    session_id: 'voice-e2e',
    turn_id: 'turn-e2e',
    conversation_id: 42,
    state: 'idle',
    started_at: now,
    updated_at: now,
    locale: 'fr-FR',
    privacy_mode: false,
    microphone_state: 'listening',
    transcript_partial: '',
    transcript_final: '',
    understood_request: {},
    current_focus: null,
    navigation_stack: [],
    answer: null,
    activities: [],
    active_speech_segment_id: null,
    last_sequence: 0,
    ...overrides,
  }
}

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    enabled: true,
    generated_at: now,
    privacy_timeout_seconds: 300,
    session: session(overrides),
  }
}

function event(sequence: number, type: string, payload: Record<string, unknown> = {}) {
  return JSON.stringify({
    schema_version: 1,
    sequence,
    event_id: `evt-${sequence}`,
    emitted_at: now,
    session_id: 'voice-e2e',
    turn_id: 'turn-e2e',
    type,
    payload,
    privacy: 'private',
  })
}

async function mockBackend(page: Page) {
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === '/api/auth/status') {
      await route.fulfill({
        json: {
          configured: true,
          authenticated: true,
          csrf_token: 'e2e-token',
          locked_out: false,
          lockout_seconds: 0,
          lockout_scope: null,
          local_recovery_available: false,
          auto_lock_minutes: 30,
        },
      })
      return
    }
    if (pathname === '/api/voice-display/snapshot') {
      await route.fulfill({ json: snapshot() })
      return
    }
    await route.fulfill({ json: {} })
  })
}

test('@desktop Voice HUD follows a complete voice session without pointer input', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await mockBackend(page)
  let socket: WebSocketRoute | null = null
  await page.routeWebSocket('**/ws/voice-display?**', (route) => {
    socket = route
    route.send(JSON.stringify({
      type: 'voice.display.snapshot',
      sequence: 0,
      snapshot: snapshot(),
    }))
  })

  await page.goto('/voice-display?kiosk=1')
  await expect(page.getByRole('heading', { name: 'Prêt à vous écouter' })).toBeVisible()
  expect(socket).not.toBeNull()
  const capture = async (name: string) => {
    const directory = process.env.VOICE_DISPLAY_CAPTURE_DIR
    if (directory) await page.screenshot({ path: path.join(directory, `${name}.png`) })
  }
  await capture('01-idle')

  socket!.send(event(1, 'voice.listening.started'))
  socket!.send(event(2, 'voice.transcript.partial', { text: 'trouve trois écrans trente-deux' }))
  await expect(page.getByText('trouve trois écrans trente-deux')).toBeVisible()
  await capture('02-listening')
  socket!.send(event(3, 'voice.transcript.final', { text: 'Trouve trois écrans 32 pouces adaptés au développement.' }))
  socket!.send(event(4, 'voice.request.understood', {
    produit: 'Écran', taille: '32 pouces', usage: 'Développement', résultats: 3,
  }))
  socket!.send(event(5, 'voice.tool.started', { id: 'search', label: 'Consultation des fiches officielles', status: 'running' }))
  await expect(page.getByText('Consultation des fiches officielles')).toBeVisible()
  await capture('03-researching')

  const answer = {
    title: 'Trois écrans 32 pouces',
    spoken_summary: 'Le Dell UltraSharp est le meilleur équilibre.',
    visual_summary: 'Le Dell UltraSharp est le meilleur équilibre.',
    sections: [
      {
        id: 'summary', type: 'summary', title: 'Recommandation', order: 0,
        data: { text: 'Le Dell UltraSharp est le meilleur équilibre pour le développement.' },
        focusable_ids: [], source_ids: ['source-dell'],
      },
      {
        id: 'results', type: 'ranked_results', title: 'Comparatif', order: 1,
        data: { items: [
          { id: 'dell', title: 'Dell UltraSharp U3225QE', prix: '949 €', définition: '4K' },
          { id: 'lg', title: 'LG 32UN880', prix: '599 €', définition: '4K' },
          { id: 'benq', title: 'BenQ PD3205U', prix: '699 €', définition: '4K' },
        ] },
        focusable_ids: ['result-1', 'result-2', 'result-3'], source_ids: ['source-dell', 'source-lg'],
      },
    ],
    sources: [
      {
        id: 'source-dell', kind: 'web', title: 'Fiche officielle Dell', provider: 'web',
        domain: 'dell.com', url: 'https://www.dell.com/', locator: null, fetched_at: now,
        status: 'verified', used: true, excerpt: 'Écran 31,5 pouces 4K avec Thunderbolt 4.', error: null,
      },
      {
        id: 'source-lg', kind: 'web', title: 'Fiche officielle LG', provider: 'web',
        domain: 'lg.com', url: 'https://www.lg.com/', locator: null, fetched_at: now,
        status: 'verified', used: true, excerpt: 'Écran 31,5 pouces 4K avec pied Ergo.', error: null,
      },
    ],
    claims: [
      {
        id: 'claim-1', text: 'Le Dell propose Thunderbolt 4.', certainty: 'confirmed',
        source_ids: ['source-dell'], status: 'verified', conflict: false,
      },
    ],
    suggested_voice_actions: [
      { id: 'compare', label: 'Compare les deux premiers', intent: 'compare' },
      { id: 'source', label: 'Ouvre la source 1', intent: 'source.open' },
      { id: 'privacy', label: 'Masque l’écran', intent: 'privacy' },
    ],
    speech_segments: [
      {
        segment_id: 'speech-1', text: 'Le Dell UltraSharp est le meilleur équilibre.',
        visual_target_ids: ['summary'], source_ids: ['source-dell'], order: 1,
      },
    ],
    status: 'complete', created_at: now, completed_at: now,
  }
  const renderStarted = performance.now()
  socket!.send(event(6, 'voice.result.final', { answer }))
  socket!.send(event(7, 'voice.speech.segment.started', {
    segment_id: 'speech-1', visual_target_ids: ['summary'], source_ids: ['source-dell'],
  }))
  await expect(page.getByText('Dell UltraSharp U3225QE')).toBeVisible()
  const resultRenderMs = Math.round((performance.now() - renderStarted) * 10) / 10
  await expect(page.getByText('Confirmé · source-dell')).toBeVisible()
  await capture('04-result')
  await testInfo.attach('voice-hud-result', { body: await page.screenshot(), contentType: 'image/png' })

  socket!.send(event(8, 'voice.display.view.opened', { view: 'source', source_id: 'source-lg', index: 1 }))
  await expect(page.getByRole('heading', { name: 'Fiche officielle LG' })).toBeVisible()
  await expect(page.getByText('Écran 31,5 pouces 4K avec pied Ergo.')).toBeVisible()
  await capture('05-source-reader')

  socket!.send(event(9, 'voice.display.back'))
  await expect(page.getByText('Dell UltraSharp U3225QE')).toBeVisible()
  socket!.send(event(10, 'voice.speech.interrupted'))
  socket!.send(event(11, 'voice.display.privacy.enabled'))
  await expect(page.getByRole('heading', { name: 'Contenu masqué' })).toBeVisible()
  await capture('06-private')
  await testInfo.attach('voice-hud-private', { body: await page.screenshot(), contentType: 'image/png' })

  socket!.send(event(12, 'voice.display.privacy.disabled'))
  const disconnectStarted = performance.now()
  socket!.close()
  await expect(page.getByText('Connexion à JARVIS perdue', { exact: true })).toBeVisible()
  const disconnectRenderMs = Math.round((performance.now() - disconnectStarted) * 10) / 10
  await capture('07-disconnected')
  console.log(`[voice-display] result_render_ms=${resultRenderMs} disconnect_render_ms=${disconnectRenderMs}`)
})
