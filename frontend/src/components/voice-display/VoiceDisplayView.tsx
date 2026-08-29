'use client'

import { useEffect, useMemo, useState } from 'react'
import { AnswerRenderer } from './renderers'
import { useVoiceDisplay } from './useVoiceDisplay'
import './voice-display.css'

const phaseLabels: Record<string, string> = {
  idle: 'Prêt à vous écouter',
  listening: 'Je vous écoute',
  understanding: 'Demande comprise',
  researching: 'Recherche en cours',
  result: 'Résultat prêt',
  speaking: 'JARVIS vous répond',
  error: 'Une erreur est survenue',
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`vd-status ${ok ? 'is-ok' : 'is-off'}`}><i />{label}</span>
}

export default function VoiceDisplayView() {
  const { state, staleSeconds } = useVoiceDisplay()
  const [clock, setClock] = useState(() => new Date())
  const kiosk = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('kiosk') === '1'
  const session = state.session

  useEffect(() => {
    const timer = setInterval(() => setClock(new Date()), 1_000)
    document.documentElement.classList.add('voice-display-active')
    document.documentElement.classList.toggle('voice-display-kiosk', kiosk)
    return () => {
      clearInterval(timer)
      document.documentElement.classList.remove('voice-display-active')
      document.documentElement.classList.remove('voice-display-kiosk')
    }
  }, [kiosk])

  const activeTargets = useMemo(() => {
    const segment = session.answer?.speech_segments.find(
      (item) => item.segment_id === session.active_speech_segment_id,
    )
    return new Set(segment?.visual_target_ids ?? [])
  }, [session.active_speech_segment_id, session.answer])

  if (session.privacy_mode) {
    return (
      <main className={`voice-display vd-private ${kiosk ? 'is-kiosk' : ''}`} aria-live="polite">
        <div className="vd-private-mark" aria-hidden="true">J</div>
        <p className="vd-kicker">JARVIS EST ACTIF</p>
        <h1>Contenu masqué</h1>
        <p>Dites « désactive le mode privé » pour restaurer l’affichage.</p>
        <StatusDot ok={state.connection === 'connected'} label={state.connection === 'connected' ? 'Connecté' : 'Connexion perdue'} />
      </main>
    )
  }

  return (
    <main className={`voice-display state-${session.state} ${kiosk ? 'is-kiosk' : ''}`} aria-live="polite">
      <header className="vd-header">
        <div className="vd-brand">
          <span className="vd-monogram">J</span>
          <div><b>JARVIS</b><small>VOICE HUD</small></div>
        </div>
        <div className="vd-system-status">
          <StatusDot ok={state.connection === 'connected'} label={state.connection === 'connected' ? 'JARVIS connecté' : 'Connexion perdue'} />
          <StatusDot ok={session.microphone_state === 'listening'} label={session.microphone_state === 'muted' ? 'Micro coupé' : 'Micro'} />
        </div>
        <time dateTime={clock.toISOString()}>
          <b>{clock.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</b>
          <span>{clock.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })}</span>
        </time>
      </header>

      {state.connection === 'disconnected' && (
        <aside className="vd-disconnected" role="alert">
          <b>Connexion à JARVIS perdue</b>
          <span>Dernier événement reçu il y a {staleSeconds} seconde{staleSeconds > 1 ? 's' : ''}. L’état actuel est inconnu.</span>
        </aside>
      )}

      <section className="vd-stage">
        <div className="vd-intent-panel">
          <div className="vd-orb" aria-hidden="true"><i /><i /><i /></div>
          <p className="vd-kicker">{phaseLabels[session.state]}</p>
          {session.state === 'idle' && !session.answer ? (
            <div className="vd-idle-copy">
              <h1>Prêt à vous écouter</h1>
              <p>Dites « Jarvis » pour commencer.</p>
            </div>
          ) : (
            <>
              <p className={`vd-transcript ${session.transcript_partial ? 'is-partial' : ''}`}>
                {session.transcript_partial || session.transcript_final || '…'}
              </p>
              {Object.keys(session.understood_request).length > 0 && (
                <div className="vd-understood">
                  <p className="vd-eyebrow">J’ai compris</p>
                  <dl>
                    {Object.entries(session.understood_request).slice(0, 6).map(([key, value]) => (
                      <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>
                    ))}
                  </dl>
                </div>
              )}
              {session.activities.length > 0 && (
                <div className="vd-activity">
                  {session.activities.slice(-4).map((activity, index) => (
                    <p key={`${String(activity.id ?? 'activity')}-${index}`}>
                      <i />{String(activity.label ?? 'Analyse des résultats')}
                    </p>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <div className="vd-result-panel">
          {session.answer ? (
            <AnswerRenderer answer={session.answer} focus={session.current_focus} activeTargets={activeTargets} />
          ) : (
            <div className="vd-waiting">
              <span>01</span><p>La réponse structurée apparaîtra ici.</p>
            </div>
          )}
        </div>
      </section>

      <footer className="vd-footer">
        <span>Vous pouvez dire</span>
        {(session.answer?.suggested_voice_actions ?? []).slice(0, 4).map((action) => (
          <q key={action.id}>{action.label}</q>
        ))}
        {!session.answer && <q>Masque l’écran</q>}
      </footer>

      {process.env.NODE_ENV === 'development' && new URLSearchParams(window.location.search).get('debug') === '1' && (
        <pre className="vd-debug">séquence {session.last_sequence} · session {session.session_id} · état {session.state}</pre>
      )}
    </main>
  )
}
