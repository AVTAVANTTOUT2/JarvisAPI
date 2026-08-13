import { jarvisFetch } from './api'
import type {
  AgenticApproval,
  AgenticApprovalDecision,
  AgenticArtifact,
  AgenticRealtimeEvent,
  AgenticRun,
  AgenticRunAction,
  AgenticRunCreateInput,
  AgenticRunError,
  AgenticRunsPage,
  AgenticRuntimeStatus,
  AgenticSanitizedArgumentValue,
  AgenticStep,
} from '../types/agentic'

type UnknownRecord = Record<string, unknown>

const APPROVAL_SECRET_PATTERNS = [
  /\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi,
  /\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b/gi,
  /\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)\s*[:=]\s*[^\s,;}\]]+/gi,
] as const
const APPROVAL_PATH_PATTERNS = [
  /(?<![:/])\/(?:[^/\s"'<>]+\/)*[^/\s"'<>]+/g,
  /\b[A-Za-z]:\\(?:[^\\\s"'<>]+\\)*[^\\\s"'<>]+/g,
  /~\/[^\s"'<>]+/g,
] as const
const SENSITIVE_APPROVAL_ARGUMENT_KEY =
  /(?:^|[_-])(?:api[_-]?key|token|secret|password|authorization|cookie|path|directory|workspace|filename|file|cwd|home)(?:$|[_-])/i
const PATH_APPROVAL_ARGUMENT_KEY =
  /(?:^|[_-])(?:path|directory|workspace|filename|file|cwd|home)(?:$|[_-])/i

function sanitizeApprovalText(value: unknown, maxChars = 240): string {
  let safe = String(value ?? '')
  for (const pattern of APPROVAL_SECRET_PATTERNS) safe = safe.replace(pattern, '[REDACTED]')
  for (const pattern of APPROVAL_PATH_PATTERNS) safe = safe.replace(pattern, '[REDACTED_PATH]')
  safe = safe.replace(/\s+/g, ' ').trim()
  return safe.length <= maxChars ? safe : `${safe.slice(0, Math.max(0, maxChars - 12))}…[tronqué]`
}

function sanitizeApprovalValue(value: unknown, depth = 0): AgenticSanitizedArgumentValue {
  if (value === null || typeof value === 'boolean') return value
  if (typeof value === 'number') return Number.isFinite(value) ? value : '[REDACTED]'
  if (typeof value === 'string') return sanitizeApprovalText(value, 500)
  if (depth >= 3) return '[STRUCTURED_DATA_REDACTED]'
  if (Array.isArray(value)) {
    return value.slice(0, 20).map((item) => sanitizeApprovalValue(item, depth + 1))
  }
  if (value !== null && typeof value === 'object') {
    const result: Record<string, AgenticSanitizedArgumentValue> = {}
    for (const [rawKey, item] of Object.entries(value).slice(0, 20)) {
      if (['__proto__', 'constructor', 'prototype'].includes(rawKey)) continue
      const key = sanitizeApprovalText(rawKey, 80)
      if (!key) continue
      if (SENSITIVE_APPROVAL_ARGUMENT_KEY.test(rawKey)) {
        result[key] = PATH_APPROVAL_ARGUMENT_KEY.test(rawKey)
          ? '[REDACTED_PATH]'
          : '[REDACTED]'
      } else {
        result[key] = sanitizeApprovalValue(item, depth + 1)
      }
    }
    return result
  }
  return '[REDACTED]'
}

function normalizeSanitizedArguments(source: UnknownRecord): Record<string, AgenticSanitizedArgumentValue> | undefined {
  const raw = first(source, 'sanitized_arguments', 'sanitizedArguments')
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const sanitized = sanitizeApprovalValue(raw)
  if (sanitized === null || Array.isArray(sanitized) || typeof sanitized !== 'object') return undefined
  return Object.keys(sanitized).length ? sanitized : undefined
}

function normalizeApprovalRisks(source: UnknownRecord): string[] | undefined {
  const raw = first(source, 'risks')
  if (!Array.isArray(raw)) return undefined
  const risks = raw
    .filter((item): item is string => typeof item === 'string')
    .slice(0, 20)
    .map((item) => sanitizeApprovalText(item, 240))
    .filter(Boolean)
  return risks.length ? risks : undefined
}

export const AGENTIC_EVENT_TYPES = [
  'agent.run.created',
  'agent.run.classified',
  'agent.run.queued',
  'agent.run.resource_wait',
  'agent.run.provisioning',
  'agent.run.started',
  'agent.run.phase_changed',
  'agent.run.awaiting_approval',
  'agent.run.paused',
  'agent.run.resumed',
  'agent.run.blocked',
  'agent.run.verifying',
  'agent.run.reviewing',
  'agent.run.cancelling',
  'agent.run.completed',
  'agent.run.failed',
  'agent.run.cancelled',
  'agent.run.expired',
  'agent.run.provider_unavailable',
  'agent.tool.started',
  'agent.tool.completed',
  'agent.tool.failed',
  'agent.approval.requested',
  'agent.approval.resolved',
  'agent.artifact.created',
] as const

function record(value: unknown): UnknownRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : {}
}

function first(source: UnknownRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null) return source[key]
  }
  return undefined
}

function text(source: UnknownRecord, ...keys: string[]): string | undefined {
  const value = first(source, ...keys)
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function number(source: UnknownRecord, ...keys: string[]): number | undefined {
  const value = first(source, ...keys)
  const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN
  return Number.isFinite(parsed) ? parsed : undefined
}

function boolean(source: UnknownRecord, ...keys: string[]): boolean | undefined {
  const value = first(source, ...keys)
  return typeof value === 'boolean' ? value : undefined
}

function progress(source: UnknownRecord): number | undefined {
  const raw = number(source, 'progress', 'progress_percent', 'progressPercent')
  if (raw === undefined) return undefined
  const percent = raw > 0 && raw <= 1 ? raw * 100 : raw
  return Math.max(0, Math.min(100, Math.round(percent)))
}

function normalizeStep(value: unknown, index: number): AgenticStep {
  const source = record(value)
  return {
    id: text(source, 'id', 'step_id', 'stepId') ?? `step-${index + 1}`,
    title: text(source, 'title', 'name', 'label', 'description') ?? `Étape ${index + 1}`,
    status: text(source, 'status', 'state') ?? 'pending',
    kind: text(source, 'kind', 'type'),
    summary: text(source, 'summary', 'message'),
    progress: progress(source),
    started_at: text(source, 'started_at', 'startedAt'),
    completed_at: text(source, 'completed_at', 'completedAt'),
  }
}

function normalizeApproval(value: unknown, index: number): AgenticApproval {
  const source = record(value)
  const title = text(source, 'title', 'label', 'action') ?? 'Autorisation requise'
  const summary = text(source, 'summary', 'description', 'reason')
  const riskLevel = text(source, 'risk_level', 'riskLevel', 'risk')
  return {
    id: text(source, 'id', 'approval_id', 'approvalId') ?? `approval-${index + 1}`,
    title: sanitizeApprovalText(title),
    status: text(source, 'status', 'state') ?? 'pending',
    summary: summary ? sanitizeApprovalText(summary, 1_000) : undefined,
    risk_level: riskLevel ? sanitizeApprovalText(riskLevel, 120) : undefined,
    sanitized_arguments: normalizeSanitizedArguments(source),
    risks: normalizeApprovalRisks(source),
    requested_at: text(source, 'requested_at', 'requestedAt', 'created_at', 'createdAt'),
    resolved_at: text(source, 'resolved_at', 'resolvedAt'),
    expires_at: text(source, 'expires_at', 'expiresAt'),
  }
}

function normalizeArtifact(value: unknown, index: number): AgenticArtifact {
  const source = record(value)
  const metadata = record(source.metadata)
  return {
    id: text(source, 'id', 'artifact_id', 'artifactId') ?? `artifact-${index + 1}`,
    name: text(source, 'name', 'title', 'filename')
      ?? text(metadata, 'name', 'title', 'filename')
      ?? text(source, 'type', 'kind')
      ?? `Artefact ${index + 1}`,
    kind: text(source, 'kind', 'type'),
    mime_type: text(source, 'mime_type', 'mimeType'),
    size_bytes: number(source, 'size_bytes', 'sizeBytes', 'size'),
    url: text(source, 'url', 'download_url', 'downloadUrl'),
    reference: text(source, 'reference', 'uri'),
    created_at: text(source, 'created_at', 'createdAt'),
  }
}

function historyProjection(value: unknown): {
  steps: AgenticStep[]
  approvals: AgenticApproval[]
  plan?: string[]
  result?: unknown
} {
  const source = record(value)
  const events = Array.isArray(value) ? value : array(source, 'events', 'items')
  const steps = new Map<string, AgenticStep>()
  const approvals = new Map<string, AgenticApproval>()
  let result: unknown

  events.forEach((value, index) => {
    const event = record(value)
    const payload = record(first(event, 'payload', 'data'))
    const details = { ...event, ...payload }
    const type = text(event, 'type', 'event_type', 'eventType')
      ?? text(details, 'type', 'event_type', 'eventType')
      ?? ''
    const timestamp = text(event, 'timestamp', 'occurred_at', 'occurredAt', 'created_at', 'createdAt')

    if (type.startsWith('agent.tool.')) {
      const id = text(details, 'tool_call_id', 'toolCallId', 'step_id', 'stepId', 'id')
        ?? text(event, 'event_id', 'eventId')
        ?? `tool-${index + 1}`
      const existing = steps.get(id)
      const status = type === 'agent.tool.started'
        ? 'running'
        : type === 'agent.tool.failed' ? 'failed' : 'completed'
      steps.set(id, {
        id,
        title: text(details, 'title', 'tool', 'name', 'action') ?? existing?.title ?? `Étape ${steps.size + 1}`,
        status,
        kind: text(details, 'kind', 'tool_type', 'toolType') ?? existing?.kind,
        summary: text(details, 'summary', 'message', 'result_summary', 'resultSummary') ?? existing?.summary,
        progress: progress(details) ?? existing?.progress,
        started_at: existing?.started_at ?? (type === 'agent.tool.started' ? timestamp : undefined),
        completed_at: type === 'agent.tool.started' ? existing?.completed_at : timestamp,
      })
    }

    if (type.startsWith('agent.approval.')) {
      const id = text(details, 'approval_id', 'approvalId', 'id')
        ?? text(event, 'event_id', 'eventId')
        ?? `approval-${index + 1}`
      const existing = approvals.get(id)
      const resolved = type === 'agent.approval.resolved'
      const title = text(details, 'title', 'action', 'tool') ?? existing?.title ?? 'Autorisation requise'
      const summary = text(details, 'summary', 'spoken_summary', 'spokenSummary', 'reason')
      const riskLevel = text(details, 'risk_level', 'riskLevel', 'risk')
      approvals.set(id, {
        id,
        title: sanitizeApprovalText(title),
        status: text(details, 'decision', 'status') ?? (resolved ? 'resolved' : 'pending'),
        summary: summary ? sanitizeApprovalText(summary, 1_000) : existing?.summary,
        risk_level: riskLevel ? sanitizeApprovalText(riskLevel, 120) : existing?.risk_level,
        sanitized_arguments: normalizeSanitizedArguments(details) ?? existing?.sanitized_arguments,
        risks: normalizeApprovalRisks(details) ?? existing?.risks,
        requested_at: existing?.requested_at ?? (resolved ? undefined : timestamp),
        resolved_at: resolved ? timestamp : existing?.resolved_at,
        expires_at: text(details, 'expires_at', 'expiresAt') ?? existing?.expires_at,
      })
    }

    if (type === 'agent.run.completed') {
      result = first(details, 'result', 'output', 'final_result', 'finalResult', 'summary', 'message') ?? result
    }
  })

  const normalizedSteps = [...steps.values()]
  return {
    steps: normalizedSteps,
    approvals: [...approvals.values()],
    plan: normalizedSteps.length ? normalizedSteps.map((step) => step.title) : undefined,
    result,
  }
}

function normalizeError(source: UnknownRecord): AgenticRunError | undefined {
  const raw = first(source, 'error', 'error_detail', 'errorDetail')
  if (typeof raw === 'string' && raw.trim()) return { message: raw.trim() }
  const error = record(raw)
  const message = text(error, 'message', 'detail') ?? text(source, 'error_message', 'errorMessage')
  if (!message) return undefined
  return {
    message,
    code: text(error, 'code') ?? text(source, 'error_code', 'errorCode'),
    category: text(error, 'category'),
    retryable: boolean(error, 'retryable'),
  }
}

function array(source: UnknownRecord, ...keys: string[]): unknown[] {
  const value = first(source, ...keys)
  return Array.isArray(value) ? value : []
}

function normalizePlan(source: UnknownRecord): string[] | undefined {
  const raw = first(source, 'plan')
  if (Array.isArray(raw)) {
    const entries = raw.map((item) => typeof item === 'string' ? item : text(record(item), 'title', 'name'))
      .filter((item): item is string => Boolean(item))
    return entries.length ? entries : undefined
  }
  const plan = record(raw)
  const entries = array(plan, 'steps', 'items')
    .map((item) => typeof item === 'string' ? item : text(record(item), 'title', 'name'))
    .filter((item): item is string => Boolean(item))
  return entries.length ? entries : undefined
}

export function normalizeAgenticRun(value: unknown): AgenticRun {
  const envelope = record(value)
  const source = Object.keys(record(envelope.run)).length ? record(envelope.run) : envelope
  const id = text(source, 'id', 'run_id', 'runId') ?? ''
  return {
    id,
    title: text(source, 'title', 'label', 'task_title', 'taskTitle') ?? 'Tâche agentique',
    status: text(source, 'status', 'state') ?? 'created',
    phase: text(source, 'phase', 'current_phase', 'currentPhase'),
    progress: progress(source),
    summary: text(source, 'summary', 'description'),
    channel: text(source, 'channel', 'origin_channel', 'originChannel'),
    category: text(source, 'category', 'task_category', 'taskCategory'),
    requires_attention: boolean(source, 'requires_attention', 'requiresAttention', 'needs_attention'),
    created_at: text(source, 'created_at', 'createdAt'),
    updated_at: text(source, 'updated_at', 'updatedAt'),
    started_at: text(source, 'started_at', 'startedAt'),
    completed_at: text(source, 'completed_at', 'completedAt', 'finished_at', 'finishedAt'),
    plan: normalizePlan(source),
    steps: array(source, 'steps').map(normalizeStep),
    approvals: array(source, 'approvals').map(normalizeApproval),
    artifacts: array(source, 'artifacts').map(normalizeArtifact),
    result: first(source, 'result', 'output', 'final_result', 'finalResult'),
    error: normalizeError(source),
  }
}

export function normalizeAgenticRunsPage(value: unknown): AgenticRunsPage {
  if (Array.isArray(value)) return { runs: value.map(normalizeAgenticRun).filter((run) => run.id) }
  const source = record(value)
  const rawRuns = array(source, 'runs', 'items', 'results')
  return {
    runs: rawRuns.map(normalizeAgenticRun).filter((run) => run.id),
    total: number(source, 'total', 'count'),
    next_cursor: text(source, 'next_cursor', 'nextCursor'),
  }
}

export function normalizeAgenticRuntimeStatus(value: unknown): AgenticRuntimeStatus {
  const envelope = record(value)
  const nested = record(first(envelope, 'runtime', 'agentic_runtime', 'agenticRuntime'))
  const candidates = array(envelope, 'runtimes').map(record)
  const selected = candidates.find((candidate) => {
    const candidateStatus = text(candidate, 'status', 'state', 'health')
    return candidateStatus !== 'unavailable' && candidateStatus !== 'offline'
  }) ?? candidates[0]
  const source = Object.keys(nested).length ? nested : selected ?? envelope
  const status = text(source, 'status', 'state', 'health') ?? 'unknown'
  const explicitAvailable = boolean(source, 'available', 'healthy', 'ready')
  return {
    available: explicitAvailable ?? ['available', 'healthy', 'ready', 'running'].includes(status),
    status,
    mode: text(source, 'mode'),
    label: text(source, 'label', 'display_name', 'displayName'),
    active_runs: number(source, 'active_runs', 'activeRuns'),
    queued_runs: number(source, 'queued_runs', 'queuedRuns'),
    checked_at: text(source, 'checked_at', 'checkedAt', 'updated_at', 'updatedAt'),
    error_code: text(source, 'error_code', 'errorCode'),
  }
}

export async function listAgenticRuns(limit = 50): Promise<AgenticRunsPage> {
  const payload = await jarvisFetch<unknown>(`/api/agentic/runs?limit=${Math.max(1, Math.min(100, limit))}`)
  return normalizeAgenticRunsPage(payload)
}

export async function createAgenticRun(input: AgenticRunCreateInput): Promise<AgenticRun> {
  const payload = await jarvisFetch<unknown>('/api/agentic/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey('web') },
    body: JSON.stringify({ category: 'direct_action', origin: 'user', channel: 'web', ...input }),
  })
  return normalizeAgenticRun(payload)
}

export async function listAgenticRunEvents(runId: string): Promise<unknown[]> {
  const payload = await jarvisFetch<unknown>(`/api/agentic/runs/${encodeURIComponent(runId)}/events`)
  return array(record(payload), 'events', 'items')
}

export async function listAgenticRunArtifacts(runId: string): Promise<AgenticArtifact[]> {
  const payload = await jarvisFetch<unknown>(`/api/agentic/runs/${encodeURIComponent(runId)}/artifacts`)
  return array(record(payload), 'artifacts', 'items').map(normalizeArtifact)
}

export async function getAgenticRun(runId: string): Promise<AgenticRun> {
  const encodedRunId = encodeURIComponent(runId)
  const [payload, events, artifacts] = await Promise.all([
    jarvisFetch<unknown>(`/api/agentic/runs/${encodedRunId}`),
    listAgenticRunEvents(runId).catch(() => []),
    listAgenticRunArtifacts(runId).catch(() => []),
  ])
  const run = normalizeAgenticRun(payload)
  const history = historyProjection(events)
  const approvals = run.approvals.length ? run.approvals : history.approvals
  return {
    ...run,
    plan: run.plan ?? history.plan,
    steps: run.steps.length ? run.steps : history.steps,
    approvals,
    artifacts: run.artifacts.length ? run.artifacts : artifacts,
    result: run.result ?? history.result,
    requires_attention: run.requires_attention ?? approvals.some((approval) => approval.status === 'pending'),
  }
}

export async function getAgenticRuntimeStatus(): Promise<AgenticRuntimeStatus> {
  const payload = await jarvisFetch<unknown>('/api/agentic/runtime/status')
  return normalizeAgenticRuntimeStatus(payload)
}

export async function runAgenticAction(runId: string, action: AgenticRunAction): Promise<void> {
  await jarvisFetch(`/api/agentic/runs/${encodeURIComponent(runId)}/${action}`, { method: 'POST' })
}

export async function decideAgenticApproval(
  runId: string,
  approvalId: string,
  decision: AgenticApprovalDecision,
): Promise<void> {
  await jarvisFetch(
    `/api/agentic/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}/decision`,
    {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey('web') },
      body: JSON.stringify({ decision }),
    },
  )
}

function idempotencyKey(origin: string): string {
  const random = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`
  return `${origin}:${random}`
}

function eventSource(value: AgenticRealtimeEvent | UnknownRecord): UnknownRecord {
  const source = record(value)
  const nested = record(source.data)
  return { ...nested, ...source }
}

function statusForEvent(type: string): string | undefined {
  if (type === 'agent.run.created') return 'created'
  if (type === 'agent.run.classified') return 'classified'
  if (type === 'agent.run.queued') return 'queued'
  if (type === 'agent.run.resource_wait') return 'queued'
  if (type === 'agent.run.provisioning') return 'provisioning'
  if (type === 'agent.run.started' || type === 'agent.run.resumed') return 'running'
  if (type === 'agent.run.awaiting_approval') return 'awaiting_approval'
  if (type === 'agent.run.paused') return 'paused'
  if (type === 'agent.run.blocked') return 'blocked'
  if (type === 'agent.run.verifying') return 'verifying'
  if (type === 'agent.run.reviewing') return 'reviewing'
  if (type === 'agent.run.cancelling') return 'cancelling'
  if (type === 'agent.run.completed') return 'completed'
  if (type === 'agent.run.failed') return 'failed'
  if (type === 'agent.run.cancelled') return 'cancelled'
  if (type === 'agent.run.expired') return 'expired'
  if (type === 'agent.run.provider_unavailable') return 'provider_unavailable'
  if (type === 'agent.approval.requested') return 'awaiting_approval'
  return undefined
}

/** Optimistic realtime projection; the next detail refresh remains authoritative. */
export function mergeAgenticEvent(runs: AgenticRun[], value: AgenticRealtimeEvent | UnknownRecord): AgenticRun[] {
  const source = eventSource(value)
  const type = text(source, 'type', 'event_type', 'eventType') ?? ''
  const rawRun = first(source, 'run')
  const normalized = rawRun ? normalizeAgenticRun(rawRun) : undefined
  const runId = normalized?.id || text(source, 'run_id', 'runId')
  if (!runId || !type.startsWith('agent.')) return runs

  const existing = runs.find((run) => run.id === runId)
  const inferredStatus = text(source, 'status', 'state') ?? statusForEvent(type)
  const merged: AgenticRun = {
    ...(existing ?? {
      id: runId,
      title: text(source, 'title', 'label') ?? 'Tâche agentique',
      status: 'created',
      steps: [],
      approvals: [],
      artifacts: [],
    }),
    ...(normalized ?? {}),
    id: runId,
    status: inferredStatus ?? normalized?.status ?? existing?.status ?? 'created',
    phase: text(source, 'phase', 'current_phase', 'currentPhase') ?? normalized?.phase ?? existing?.phase,
    progress: progress(source) ?? normalized?.progress ?? existing?.progress,
    requires_attention:
      boolean(source, 'requires_attention', 'requiresAttention')
      ?? (type === 'agent.approval.requested' ? true : type === 'agent.approval.resolved' ? false : undefined)
      ?? normalized?.requires_attention
      ?? existing?.requires_attention,
    updated_at: text(source, 'occurred_at', 'occurredAt', 'updated_at', 'updatedAt')
      ?? normalized?.updated_at
      ?? existing?.updated_at,
  }
  return [merged, ...runs.filter((run) => run.id !== runId)]
}
