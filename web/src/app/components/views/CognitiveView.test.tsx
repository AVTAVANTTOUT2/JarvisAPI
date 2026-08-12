import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgenticRun } from '@unified/types/agentic'

const mocks = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getRun: vi.fn(),
  getRuntime: vi.fn(),
  action: vi.fn(),
  decide: vi.fn(),
  on: vi.fn(() => vi.fn()),
  connect: vi.fn(),
}))

vi.mock('@unified/lib/agenticApi', () => ({
  AGENTIC_EVENT_TYPES: ['agent.run.started', 'agent.approval.requested'],
  listAgenticRuns: mocks.listRuns,
  getAgenticRun: mocks.getRun,
  getAgenticRuntimeStatus: mocks.getRuntime,
  runAgenticAction: mocks.action,
  decideAgenticApproval: mocks.decide,
  mergeAgenticEvent: (runs: AgenticRun[]) => runs,
}))

vi.mock('@desktop/services/websocket', () => ({
  ws: { on: mocks.on, connect: mocks.connect },
}))

const CognitiveView = (await import('./CognitiveView')).default

const run: AgenticRun = {
  id: 'run-1',
  title: 'Préparer la livraison',
  status: 'running',
  phase: 'verifying',
  progress: 60,
  steps: [{ id: 'step-1', title: 'Lancer les tests', status: 'completed' }],
  approvals: [{
    id: 'approval-1',
    title: 'Publier le résultat',
    status: 'pending',
    risk_level: 'élevé',
    sanitized_arguments: {
      target: 'staging',
      output_path: '[REDACTED_PATH]',
      api_token: '[REDACTED]',
    },
    risks: ['Publication externe', 'Écriture distante'],
  }],
  artifacts: [{ id: 'artifact-1', name: 'rapport.md', kind: 'report' }],
  result: { summary: 'Les validations sont terminées.' },
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.listRuns.mockResolvedValue({ runs: [run] })
  mocks.getRun.mockResolvedValue(run)
  mocks.getRuntime.mockResolvedValue({ available: true, status: 'ready', active_runs: 1, queued_runs: 0 })
  mocks.action.mockResolvedValue(undefined)
  mocks.decide.mockResolvedValue(undefined)
})

afterEach(() => cleanup())

describe('CognitiveView agentique', () => {
  it('affiche le run, ses validations et des actions sans nom de fournisseur', async () => {
    render(<CognitiveView />)

    await waitFor(() => expect(screen.getAllByText('Préparer la livraison').length).toBeGreaterThan(0))
    expect(screen.getByText('Lancer les tests')).toBeTruthy()
    expect(screen.getByText('Publier le résultat')).toBeTruthy()
    expect(screen.getByText('Paramètres sanitisés')).toBeTruthy()
    expect(screen.getByText('staging')).toBeTruthy()
    expect(screen.getByText('[REDACTED_PATH]')).toBeTruthy()
    expect(screen.getByText('[REDACTED]')).toBeTruthy()
    expect(screen.getByText('Publication externe')).toBeTruthy()
    expect(screen.getByText('Écriture distante')).toBeTruthy()
    expect(screen.getByText(/Niveau de risque/)).toBeTruthy()
    expect(screen.getByText('rapport.md')).toBeTruthy()
    expect(screen.getByText('Les validations sont terminées.')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/Cursor|OpenCode/i)

    fireEvent.click(screen.getByRole('button', { name: 'Autoriser' }))
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith('run-1', 'approval-1', 'approved'))

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(mocks.action).toHaveBeenCalledWith('run-1', 'pause'))
  })
})
