import { useState, type CSSProperties, type FormEvent, type ReactNode } from 'react'

import { AuthError } from './client'
import { useLockGate, type UseLockGateOptions } from './useLockGate'

export interface LockGateProps extends UseLockGateOptions {
  children: ReactNode
  title?: string
}

const styles: Record<string, CSSProperties> = {
  screen: {
    minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 20,
    background: '#0a0a0f', color: '#f4f4f5', fontFamily: 'system-ui, sans-serif',
  },
  panel: {
    width: '100%', maxWidth: 360, padding: 24, borderRadius: 18,
    border: '1px solid rgba(255,255,255,.12)', background: 'rgba(255,255,255,.05)',
    boxSizing: 'border-box', boxShadow: '0 24px 80px rgba(0,0,0,.35)',
  },
  title: { margin: 0, textAlign: 'center', fontSize: 20, letterSpacing: '.12em' },
  subtitle: { margin: '8px 0 24px', textAlign: 'center', color: '#a1a1aa', fontSize: 13 },
  form: { display: 'grid', gap: 12 },
  input: {
    width: '100%', boxSizing: 'border-box', borderRadius: 10, padding: '12px 14px',
    border: '1px solid rgba(255,255,255,.14)', background: '#18181b', color: '#fafafa',
    fontSize: 16, outline: 'none',
  },
  button: {
    border: 0, borderRadius: 10, padding: '12px 14px', background: '#f4f4f5',
    color: '#09090b', fontWeight: 700, fontSize: 14, cursor: 'pointer',
  },
  secondaryButton: {
    border: '1px solid rgba(255,255,255,.2)', borderRadius: 10, padding: '12px 14px',
    background: 'transparent', color: '#f4f4f5', fontWeight: 700, fontSize: 14,
    cursor: 'pointer',
  },
  error: { color: '#f87171', fontSize: 13, margin: 0 },
  warning: { color: '#fbbf24', fontSize: 13, margin: 0 },
}

/** Écran de verrouillage partagé par les interfaces desktop, mobile et unifiée. */
export function LockGate({ children, title = 'JARVIS', ...options }: LockGateProps) {
  const gate = useLockGate(options)
  const [secret, setSecret] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!gate.loading && gate.status?.authenticated && !gate.softLocked) return <>{children}</>

  const mode = gate.status?.configured ? 'unlock' : 'setup'
  const lockedOut = gate.lockoutSeconds > 0
  const localRecovery = Boolean(gate.status?.local_recovery_available)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (!secret) {
      setError('Le secret est requis.')
      return
    }
    if (mode === 'setup' && ((/^\d+$/.test(secret) && secret.length < 6) || (!/^\d+$/.test(secret) && secret.length < 10))) {
      setError('Utilisez un PIN de 6 chiffres ou une passphrase de 10 caractères.')
      return
    }
    if (mode === 'setup' && secret !== confirmation) {
      setError('Les deux saisies ne correspondent pas.')
      return
    }
    setBusy(true)
    try {
      if (mode === 'setup') await gate.setup(secret)
      else await gate.unlock(secret)
      setSecret('')
      setConfirmation('')
    } catch (caught) {
      if (caught instanceof AuthError && caught.status === 429) {
        setError('Trop de tentatives — réessayez plus tard.')
      } else if (mode === 'unlock') {
        setError('Secret incorrect.')
      } else {
        setError('Échec de la configuration.')
      }
      await gate.refresh()
    } finally {
      setBusy(false)
    }
  }

  async function recoverLocally() {
    setError('')
    if (!secret) {
      setError('Le secret est requis.')
      return
    }
    setBusy(true)
    try {
      await gate.localUnlock(secret)
      setSecret('')
    } catch (caught) {
      if (caught instanceof AuthError && caught.status === 429) {
        setError('Récupération temporairement bloquée — réessayez plus tard.')
      } else {
        setError('Secret incorrect ou récupération locale indisponible.')
      }
      await gate.refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={styles.screen} data-testid="lock-gate">
      <section style={styles.panel} aria-live="polite">
        <h1 style={styles.title}>{title}</h1>
        <p style={styles.subtitle}>
          {gate.loading
            ? 'Vérification de la session…'
            : gate.connectionError
              ? 'Connexion au serveur impossible'
              : mode === 'setup'
                ? 'Définissez votre code de déverrouillage'
                : 'Application verrouillée'}
        </p>

        {gate.connectionError && !gate.status ? (
          <button type="button" style={styles.button} onClick={() => void gate.refresh()}>
            Réessayer
          </button>
        ) : !gate.loading ? (
          <form style={styles.form} onSubmit={submit}>
            <input
              style={styles.input}
              type="password"
              autoComplete={mode === 'setup' ? 'new-password' : 'current-password'}
              autoFocus
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              placeholder={mode === 'setup' ? 'PIN 6 chiffres ou passphrase 10+ caractères' : 'Code de déverrouillage'}
              disabled={busy || (lockedOut && !localRecovery)}
              aria-label={mode === 'setup' ? 'Nouveau code' : 'Code de déverrouillage'}
            />
            {mode === 'setup' && (
              <input
                style={styles.input}
                type="password"
                autoComplete="new-password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                placeholder="Confirmez le code"
                disabled={busy}
                aria-label="Confirmation du code"
              />
            )}
            {error ? (
              <p style={styles.error}>{error}</p>
            ) : lockedOut ? (
              <p style={styles.warning}>Verrouillé — réessayez dans {gate.lockoutSeconds}s.</p>
            ) : null}
            <button type="submit" style={styles.button} disabled={busy || lockedOut}>
              {busy ? 'Vérification…' : mode === 'setup' ? 'Configurer' : 'Déverrouiller'}
            </button>
            {lockedOut && localRecovery ? (
              <button
                type="button"
                style={styles.secondaryButton}
                disabled={busy}
                onClick={() => void recoverLocally()}
              >
                Récupérer depuis ce Mac
              </button>
            ) : null}
          </form>
        ) : null}
      </section>
    </div>
  )
}
