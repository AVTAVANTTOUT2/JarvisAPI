/** Provider-neutral contracts for long-running agentic work. */
export type AgenticRunStatus =
  | 'created'
  | 'classified'
  | 'queued'
  | 'provisioning'
  | 'planning'
  | 'awaiting_approval'
  | 'running'
  | 'verifying'
  | 'reviewing'
  | 'paused'
  | 'blocked'
  | 'cancelling'
  | 'cancelled'
  | 'failed'
  | 'completed'
  | 'expired'
  | 'provider_unavailable'
  | (string & {})

export type AgenticStepStatus =
  | 'pending'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'skipped'
  | (string & {})

export type AgenticApprovalStatus =
  | 'pending'
  | 'approved'
  | 'denied'
  | 'rejected'
  | 'resolved'
  | 'expired'
  | 'cancelled'
  | (string & {})

export interface AgenticStep {
  id: string
  title: string
  status: AgenticStepStatus
  kind?: string
  summary?: string
  progress?: number
  started_at?: string
  completed_at?: string
}

export type AgenticSanitizedArgumentValue =
  | string
  | number
  | boolean
  | null
  | AgenticSanitizedArgumentValue[]
  | { [key: string]: AgenticSanitizedArgumentValue }

export interface AgenticApproval {
  id: string
  title: string
  status: AgenticApprovalStatus
  summary?: string
  risk_level?: string
  sanitized_arguments?: Record<string, AgenticSanitizedArgumentValue>
  risks?: string[]
  requested_at?: string
  resolved_at?: string
  expires_at?: string
}

export interface AgenticArtifact {
  id: string
  name: string
  kind?: string
  mime_type?: string
  size_bytes?: number
  url?: string
  reference?: string
  created_at?: string
}

export interface AgenticRunError {
  code?: string
  category?: string
  message: string
  retryable?: boolean
}

export interface AgenticRun {
  id: string
  title: string
  status: AgenticRunStatus
  phase?: string
  progress?: number
  summary?: string
  channel?: string
  category?: string
  requires_attention?: boolean
  created_at?: string
  updated_at?: string
  started_at?: string
  completed_at?: string
  plan?: string[]
  steps: AgenticStep[]
  approvals: AgenticApproval[]
  artifacts: AgenticArtifact[]
  result?: unknown
  error?: AgenticRunError
}

export interface AgenticRuntimeStatus {
  available: boolean
  status: string
  mode?: string
  label?: string
  active_runs?: number
  queued_runs?: number
  checked_at?: string
  error_code?: string
}

export interface AgenticRunsPage {
  runs: AgenticRun[]
  total?: number
  next_cursor?: string
}

export interface AgenticRunCreateInput {
  title: string
  runtime_id?: string
  category?: string
  origin?: string
  channel?: string
  task_id?: string
  conversation_id?: string
  device?: string
  locale?: string
  timezone?: string
  permissions?: string[]
  selected_context?: Record<string, unknown>
  budget?: Record<string, unknown>
  run_id?: string
}

export type AgenticRunAction = 'pause' | 'resume' | 'cancel'
export type AgenticApprovalDecision = 'approved' | 'denied'

export interface AgenticRealtimeEvent {
  type: string
  event_id?: string
  run_id?: string
  sequence?: number
  timestamp?: string
  status?: AgenticRunStatus
  phase?: string
  progress?: number
  requires_attention?: boolean
  run?: unknown
  payload?: unknown
  data?: unknown
}
