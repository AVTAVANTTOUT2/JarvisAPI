import { describe, expect, it } from 'vitest'
import { initialVoiceHudState, voiceHudReducer } from './state'
import type { VoiceDisplayEvent, VoiceDisplaySnapshot } from './types'

const emittedAt = '2026-08-29T10:00:00.000Z'

function event(sequence: number, type: string, payload: Record<string, unknown> = {}): VoiceDisplayEvent {
  return {
    schema_version: 1,
    sequence,
    event_id: `evt-${sequence}`,
    emitted_at: emittedAt,
    session_id: 'voice-1',
    turn_id: 'turn-1',
    type,
    payload,
    privacy: 'private',
  }
}

function snapshot(sequence = 0): VoiceDisplaySnapshot {
  return {
    schema_version: 1,
    enabled: true,
    generated_at: emittedAt,
    privacy_timeout_seconds: 120,
    session: {
      session_id: 'voice-1',
      turn_id: 'turn-1',
      state: 'idle',
      started_at: emittedAt,
      updated_at: emittedAt,
      locale: 'fr-FR',
      privacy_mode: false,
      microphone_state: 'unknown',
      transcript_partial: '',
      transcript_final: '',
      understood_request: {},
      navigation_stack: [],
      activities: [],
      last_sequence: sequence,
    },
  }
}

describe('voiceHudReducer', () => {
  it('restores a snapshot and ignores duplicate or late events', () => {
    const restored = voiceHudReducer(initialVoiceHudState(), { type: 'snapshot', snapshot: snapshot(10) })
    const late = voiceHudReducer(restored, { type: 'event', event: event(9, 'voice.listening.started') })
    const duplicate = voiceHudReducer(restored, { type: 'event', event: event(10, 'voice.listening.started') })
    const current = voiceHudReducer(restored, { type: 'event', event: event(11, 'voice.listening.started') })

    expect(late).toBe(restored)
    expect(duplicate).toBe(restored)
    expect(current.session.state).toBe('listening')
  })

  it('clears previous turn content when a new turn starts', () => {
    const previous = snapshot(3)
    previous.session.state = 'result'
    previous.session.privacy_mode = true
    previous.session.transcript_final = 'ancienne demande'
    previous.session.activities = [{ label: 'ancienne analyse' }]
    previous.session.answer = {
      title: 'Ancienne réponse',
      spoken_summary: 'ancienne réponse',
      visual_summary: 'ancienne réponse',
      sections: [],
      sources: [],
      claims: [],
      suggested_voice_actions: [],
      speech_segments: [],
      status: 'complete',
      created_at: emittedAt,
      completed_at: emittedAt,
    }
    let state = voiceHudReducer(initialVoiceHudState(), { type: 'snapshot', snapshot: previous })
    const started = event(4, 'voice.session.started', { conversation_id: 1 })
    started.turn_id = 'turn-2'

    state = voiceHudReducer(state, { type: 'event', event: started })

    expect(state.session).toMatchObject({
      turn_id: 'turn-2',
      conversation_id: 1,
      state: 'listening',
      privacy_mode: true,
      transcript_final: '',
      activities: [],
      last_sequence: 4,
    })
    expect(state.session.answer).toBeUndefined()
  })

  it('moves from partial transcript to final understanding', () => {
    let state = voiceHudReducer(initialVoiceHudState(), { type: 'event', event: event(1, 'voice.transcript.partial', { text: 'trois écr' }) })
    expect(state.session.transcript_partial).toBe('trois écr')
    state = voiceHudReducer(state, { type: 'event', event: event(2, 'voice.transcript.final', { text: 'trois écrans' }) })
    expect(state.session).toMatchObject({
      state: 'understanding',
      transcript_partial: '',
      transcript_final: 'trois écrans',
    })
  })

  it('restores source focus, back stack, TTS highlight and privacy', () => {
    let state = initialVoiceHudState()
    state = voiceHudReducer(state, { type: 'event', event: event(1, 'voice.display.focus.changed', { view: 'result', index: 1 }) })
    state = voiceHudReducer(state, { type: 'event', event: event(2, 'voice.display.view.opened', { view: 'source', source_id: 's2' }) })
    expect(state.session.navigation_stack).toEqual([{ view: 'result', index: 1 }])
    state = voiceHudReducer(state, { type: 'event', event: event(3, 'voice.display.back') })
    expect(state.session.current_focus).toEqual({ view: 'result', index: 1 })
    state = voiceHudReducer(state, { type: 'event', event: event(4, 'voice.speech.segment.started', { segment_id: 'speech-2' }) })
    expect(state.session.active_speech_segment_id).toBe('speech-2')
    state = voiceHudReducer(state, { type: 'event', event: event(5, 'voice.speech.interrupted') })
    expect(state.session.active_speech_segment_id).toBeNull()
    state = voiceHudReducer(state, { type: 'event', event: event(6, 'voice.display.privacy.enabled') })
    expect(state.session.privacy_mode).toBe(true)
  })

  it('keeps connection loss visible and bounds event memory', () => {
    let state = voiceHudReducer(initialVoiceHudState(), { type: 'connection', value: 'disconnected', at: 42 })
    expect(state).toMatchObject({ connection: 'disconnected', disconnectedAt: 42 })
    for (let sequence = 1; sequence <= 2_000; sequence += 1) {
      state = voiceHudReducer(state, { type: 'event', event: event(sequence, 'voice.transcript.partial', { text: String(sequence) }) })
    }
    expect(state.seenSequences).toHaveLength(512)
    expect(state.session.last_sequence).toBe(2_000)
  })

  it('keeps contradictory and missing-source answers explicit', () => {
    const answer = {
      title: 'Horaires',
      spoken_summary: 'Les horaires divergent.',
      visual_summary: 'Les horaires divergent.',
      sections: [],
      sources: [],
      claims: [{
        id: 'c1',
        text: 'Fermeture à 22 h ou 23 h',
        certainty: 'conflicting' as const,
        source_ids: ['s1', 's2'],
        status: 'conflicting',
        conflict: true,
      }],
      suggested_voice_actions: [],
      speech_segments: [],
      status: 'complete' as const,
      created_at: emittedAt,
      completed_at: emittedAt,
    }
    const state = voiceHudReducer(initialVoiceHudState(), {
      type: 'event',
      event: event(1, 'voice.result.final', { answer }),
    })
    expect(state.session.answer?.claims[0].certainty).toBe('conflicting')
    expect(state.session.answer?.sources).toEqual([])
  })
})
