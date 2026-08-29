import type {
  VisualAnswer,
  VoiceDisplayEvent,
  VoiceDisplaySession,
  VoiceDisplaySnapshot,
} from './types'

export type ConnectionState = 'connecting' | 'connected' | 'disconnected'

export type VoiceHudState = {
  session: VoiceDisplaySession
  privacyTimeoutSeconds: number
  connection: ConnectionState
  disconnectedAt: number | null
  lastEventAt: number | null
  seenSequences: number[]
}

export type VoiceHudAction =
  | { type: 'snapshot'; snapshot: VoiceDisplaySnapshot }
  | { type: 'event'; event: VoiceDisplayEvent }
  | { type: 'connection'; value: ConnectionState; at?: number }
  | { type: 'privacy-timeout' }

const nowIso = () => new Date(0).toISOString()

export function initialVoiceHudState(): VoiceHudState {
  return {
    session: {
      session_id: 'voice-display-idle',
      state: 'idle',
      started_at: nowIso(),
      updated_at: nowIso(),
      locale: 'fr-FR',
      privacy_mode: false,
      microphone_state: 'unknown',
      transcript_partial: '',
      transcript_final: '',
      understood_request: {},
      navigation_stack: [],
      activities: [],
      last_sequence: 0,
    },
    privacyTimeoutSeconds: 300,
    connection: 'connecting',
    disconnectedAt: null,
    lastEventAt: null,
    seenSequences: [],
  }
}

function eventAnswer(payload: Record<string, unknown>): VisualAnswer | null {
  const answer = payload.answer
  return answer && typeof answer === 'object' ? answer as VisualAnswer : null
}

function applyEvent(session: VoiceDisplaySession, event: VoiceDisplayEvent): VoiceDisplaySession {
  const next = { ...session, updated_at: event.emitted_at, last_sequence: event.sequence }
  const payload = event.payload

  switch (event.type) {
    case 'voice.session.started':
      return {
        ...next,
        session_id: event.session_id,
        turn_id: event.turn_id,
        state: 'listening',
        microphone_state: 'listening',
      }
    case 'voice.session.completed':
      return { ...next, state: 'idle', microphone_state: 'unknown' }
    case 'voice.listening.started':
      return { ...next, state: 'listening', microphone_state: 'listening' }
    case 'voice.microphone.muted':
      return { ...next, microphone_state: 'muted' }
    case 'voice.microphone.unmuted':
      return { ...next, microphone_state: 'listening' }
    case 'voice.transcript.partial':
      return { ...next, transcript_partial: String(payload.text ?? '') }
    case 'voice.transcript.final':
      return {
        ...next,
        state: 'understanding',
        transcript_partial: '',
        transcript_final: String(payload.text ?? ''),
      }
    case 'voice.request.understood':
      return { ...next, state: 'understanding', understood_request: payload }
    case 'voice.tool.started':
      return { ...next, state: 'researching', activities: [...session.activities, payload].slice(-20) }
    case 'voice.tool.completed':
    case 'voice.tool.failed':
      return { ...next, activities: [...session.activities, payload].slice(-20) }
    case 'voice.result.final':
      return { ...next, state: 'result', answer: eventAnswer(payload) }
    case 'voice.speech.started':
    case 'voice.speech.resumed':
      return { ...next, state: 'speaking' }
    case 'voice.speech.segment.started':
      return { ...next, state: 'speaking', active_speech_segment_id: String(payload.segment_id ?? '') }
    case 'voice.speech.interrupted':
    case 'voice.speech.completed':
      return { ...next, state: session.answer ? 'result' : 'idle', active_speech_segment_id: null }
    case 'voice.display.focus.changed':
      return { ...next, current_focus: payload }
    case 'voice.display.view.opened':
      return {
        ...next,
        current_focus: payload,
        navigation_stack: session.current_focus
          ? [...session.navigation_stack, session.current_focus].slice(-20)
          : session.navigation_stack,
      }
    case 'voice.display.back': {
      const stack = [...session.navigation_stack]
      return { ...next, current_focus: stack.pop() ?? null, navigation_stack: stack }
    }
    case 'voice.display.privacy.enabled':
      return { ...next, privacy_mode: true }
    case 'voice.display.privacy.disabled':
      return { ...next, privacy_mode: false }
    case 'voice.display.cleared':
      return {
        ...initialVoiceHudState().session,
        privacy_mode: session.privacy_mode,
        last_sequence: event.sequence,
        updated_at: event.emitted_at,
      }
    case 'voice.error':
    case 'voice.result.failed':
      return { ...next, state: 'error', activities: [...session.activities, payload].slice(-20) }
    default:
      return next
  }
}

export function voiceHudReducer(state: VoiceHudState, action: VoiceHudAction): VoiceHudState {
  if (action.type === 'snapshot') {
    return {
      ...state,
      session: action.snapshot.session,
      privacyTimeoutSeconds: action.snapshot.privacy_timeout_seconds,
      connection: 'connected',
      disconnectedAt: null,
      lastEventAt: Date.now(),
      seenSequences: action.snapshot.session.last_sequence
        ? [action.snapshot.session.last_sequence]
        : [],
    }
  }
  if (action.type === 'connection') {
    return {
      ...state,
      connection: action.value,
      disconnectedAt: action.value === 'disconnected' ? action.at ?? Date.now() : null,
    }
  }
  if (action.type === 'privacy-timeout') {
    return { ...state, session: { ...state.session, privacy_mode: true } }
  }
  if (action.event.sequence <= state.session.last_sequence || state.seenSequences.includes(action.event.sequence)) {
    return state
  }
  return {
    ...state,
    session: applyEvent(state.session, action.event),
    connection: 'connected',
    disconnectedAt: null,
    lastEventAt: Date.now(),
    seenSequences: [...state.seenSequences, action.event.sequence].slice(-512),
  }
}
