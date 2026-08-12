import { beforeEach, describe, expect, it, vi } from 'vitest'

const { jarvisFetch } = vi.hoisted(() => ({ jarvisFetch: vi.fn() }))

vi.mock('./api', () => ({ jarvisFetch }))

import {
  createAgenticRun,
  decideAgenticApproval,
  getAgenticRun,
  mergeAgenticEvent,
  normalizeAgenticRun,
  normalizeAgenticRunsPage,
  normalizeAgenticRuntimeStatus,
  runAgenticAction,
} from './agenticApi'

describe('agenticApi contracts', () => {
  beforeEach(() => jarvisFetch.mockReset())

  it('normalizes provider-neutral snake_case run details', () => {
    const run = normalizeAgenticRun({
      run: {
        run_id: 'run-42',
        title: 'Préparer la livraison',
        status: 'running',
        current_phase: 'verifying',
        progress: 0.55,
        requires_attention: true,
        plan: ['Analyser', 'Tester'],
        steps: [{ step_id: 's1', name: 'Analyser', status: 'completed' }],
        approvals: [{ approval_id: 'a1', action: 'Publier', status: 'pending' }],
        artifacts: [{ artifact_id: 'f1', filename: 'rapport.md', type: 'report' }],
        error_detail: { code: 'quality_gate', message: 'Une vérification reste à faire' },
      },
    })

    expect(run).toMatchObject({
      id: 'run-42',
      phase: 'verifying',
      progress: 55,
      requires_attention: true,
      plan: ['Analyser', 'Tester'],
      error: { code: 'quality_gate' },
    })
    expect(run.steps[0]).toMatchObject({ id: 's1', title: 'Analyser' })
    expect(run.approvals[0]).toMatchObject({ id: 'a1', title: 'Publier' })
    expect(run.artifacts[0]).toMatchObject({ id: 'f1', name: 'rapport.md' })
  })

  it('keeps only sanitized approval details and redacts secrets and filesystem paths', () => {
    const run = normalizeAgenticRun({
      run_id: 'approval-redaction',
      approvals: [
        {
          approval_id: 'approval-safe',
          action: 'Publier depuis /Users/alice/projet avec Bearer abcdefghijklmnop',
          status: 'pending',
          sanitized_arguments: {
            target: 'staging',
            output_path: '/Users/alice/projet/rapport.md',
            api_token: 'sk-supersecretvalue',
            nested: { note: 'Visible', source: '/private/tmp/source.txt' },
          },
          arguments: { raw: 'raw-payload-must-not-appear' },
          risks: [
            'Publication externe',
            'Lecture de /Users/alice/projet avec Bearer anothersecretvalue',
          ],
        },
      ],
    })

    const approval = run.approvals[0]
    expect(approval).toMatchObject({
      id: 'approval-safe',
      title: 'Publier depuis [REDACTED_PATH] avec [REDACTED]',
      sanitized_arguments: {
        target: 'staging',
        output_path: '[REDACTED_PATH]',
        api_token: '[REDACTED]',
        nested: { note: 'Visible', source: '[REDACTED_PATH]' },
      },
      risks: ['Publication externe', 'Lecture de [REDACTED_PATH] avec [REDACTED]'],
    })
    const serialized = JSON.stringify(approval)
    expect(serialized).not.toContain('raw-payload-must-not-appear')
    expect(serialized).not.toContain('supersecretvalue')
    expect(serialized).not.toContain('/Users/')
    expect(serialized).not.toContain('/private/')
    expect(approval).not.toHaveProperty('arguments')
  })

  it('accepts list envelopes and runtime status aliases', () => {
    expect(normalizeAgenticRunsPage({ items: [{ id: 'r1', status: 'queued' }], count: 1 })).toMatchObject({
      total: 1,
      runs: [{ id: 'r1', status: 'queued' }],
    })
    expect(normalizeAgenticRuntimeStatus({ runtimes: [{ status: 'unavailable' }, { status: 'healthy' }] })).toEqual({
      available: true,
      status: 'healthy',
      mode: undefined,
      label: undefined,
      active_runs: undefined,
      queued_runs: undefined,
      checked_at: undefined,
      error_code: undefined,
    })
  })

  it('projects generic realtime events without provider fields', () => {
    const projected = mergeAgenticEvent([], {
      type: 'agent.approval.requested',
      run_id: 'r2',
      title: 'Mettre à jour le projet',
      phase: 'reviewing',
    })
    expect(projected[0]).toMatchObject({
      id: 'r2',
      status: 'awaiting_approval',
      phase: 'reviewing',
      requires_attention: true,
    })

    const resumed = mergeAgenticEvent(projected, { type: 'agent.run.resumed', run_id: 'r2' })
    expect(resumed[0]).toMatchObject({ status: 'running' })
  })

  it('enriches a run detail from history and artifact endpoints', async () => {
    jarvisFetch
      .mockResolvedValueOnce({ run: { run_id: 'r3', title: 'Préparer', status: 'running' } })
      .mockResolvedValueOnce({
        events: [
          {
            event_id: 'e1',
            type: 'agent.tool.started',
            timestamp: '2026-08-11T10:00:00Z',
            payload: { tool_call_id: 'tool-1', tool: 'Analyse' },
          },
          {
            event_id: 'e2',
            type: 'agent.tool.completed',
            timestamp: '2026-08-11T10:01:00Z',
            payload: { tool_call_id: 'tool-1', tool: 'Analyse', summary: 'Terminé' },
          },
          {
            event_id: 'e3',
            type: 'agent.approval.requested',
            timestamp: '2026-08-11T10:02:00Z',
            payload: {
              approval_id: 'approval-1',
              action: 'Publier',
              spoken_summary: 'Confirmer',
              sanitized_arguments: { target: 'staging' },
              risks: ['Publication externe'],
            },
          },
        ],
      })
      .mockResolvedValueOnce({
        artifacts: [{ artifact_id: 'artifact-1', type: 'report', reference: 'artifact://report' }],
      })

    const run = await getAgenticRun('r3')

    expect(run).toMatchObject({
      id: 'r3',
      plan: ['Analyse'],
      requires_attention: true,
      steps: [{ id: 'tool-1', status: 'completed', summary: 'Terminé' }],
      approvals: [{
        id: 'approval-1',
        title: 'Publier',
        status: 'pending',
        sanitized_arguments: { target: 'staging' },
        risks: ['Publication externe'],
      }],
      artifacts: [{ id: 'artifact-1', kind: 'report', reference: 'artifact://report' }],
    })
    expect(jarvisFetch).toHaveBeenNthCalledWith(1, '/api/agentic/runs/r3')
    expect(jarvisFetch).toHaveBeenNthCalledWith(2, '/api/agentic/runs/r3/events')
    expect(jarvisFetch).toHaveBeenNthCalledWith(3, '/api/agentic/runs/r3/artifacts')
  })

  it('creates a provider-neutral run with an idempotency key', async () => {
    jarvisFetch.mockResolvedValue({ run: { run_id: 'created-1', title: 'Analyser', status: 'created' } })

    const run = await createAgenticRun({ title: 'Analyser' })

    expect(run.id).toBe('created-1')
    expect(jarvisFetch).toHaveBeenCalledWith('/api/agentic/runs', {
      method: 'POST',
      headers: { 'Idempotency-Key': expect.stringMatching(/^web:/) },
      body: JSON.stringify({ category: 'direct_action', origin: 'user', channel: 'web', title: 'Analyser' }),
    })
  })

  it('uses the generic action and approval endpoints', async () => {
    jarvisFetch.mockResolvedValue({ ok: true })
    await runAgenticAction('run / 1', 'pause')
    await decideAgenticApproval('run / 1', 'approval / 2', 'denied')

    expect(jarvisFetch).toHaveBeenNthCalledWith(
      1,
      '/api/agentic/runs/run%20%2F%201/pause',
      { method: 'POST' },
    )
    expect(jarvisFetch).toHaveBeenNthCalledWith(
      2,
      '/api/agentic/runs/run%20%2F%201/approvals/approval%20%2F%202/decision',
      {
        method: 'POST',
        headers: { 'Idempotency-Key': expect.stringMatching(/^web:/) },
        body: JSON.stringify({ decision: 'denied' }),
      },
    )
  })
})
