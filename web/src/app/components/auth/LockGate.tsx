import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, type AuthStatus } from '@/services/api'
import { clearOfflineDB } from '@/lib/offline/db'
import { initOfflineSync } from '@/lib/offline/queue'

const INACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'] as const

/**
 * Verrouille l'app tant qu'aucun secret n'est configuré (setup) ou que la
 * session n'est pas valide (unlock) — et re-verrouille localement après
 * `auto_lock_minutes` d'inactivité (ré-authentification sans perdre la
 * session serveur tant qu'elle est encore valide).
 */
export function LockGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [softLocked, setSoftLocked] = useState(false)
  const [secret, setSecret] = useState('')
  const [confirmSecret, setConfirmSecret] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [lockoutSeconds, setLockoutSeconds] = useState(0)
  const inactivityTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.getAuthStatus()
      setStatus(s)
      setLockoutSeconds(s.lockout_seconds)
      if (s.authenticated) setSoftLocked(false)
    } catch {
      // Le statut lui-même ne devrait jamais échouer (route publique) — on retentera plus tard.
    }
  }, [])

  useEffect(() => {
    refreshStatus()
    const onAuthRequired = () => refreshStatus()
    window.addEventListener('jarvis:auth-required', onAuthRequired)
    return () => window.removeEventListener('jarvis:auth-required', onAuthRequired)
  }, [refreshStatus])

  // Verrouillage automatique après inactivité (uniquement pertinent une fois authentifié)
  useEffect(() => {
    if (!status?.authenticated) return
    const minutes = status.auto_lock_minutes || 5

    const reset = () => {
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current)
      inactivityTimer.current = setTimeout(() => setSoftLocked(true), minutes * 60_000)
    }
    reset()
    for (const evt of INACTIVITY_EVENTS) window.addEventListener(evt, reset)
    return () => {
      for (const evt of INACTIVITY_EVENTS) window.removeEventListener(evt, reset)
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current)
    }
  }, [status?.authenticated, status?.auto_lock_minutes])

  // Décompte du verrouillage anti-brute-force affiché à l'utilisateur
  useEffect(() => {
    if (lockoutSeconds <= 0) return
    const t = setInterval(() => setLockoutSeconds((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(t)
  }, [lockoutSeconds])

  // Resynchronisation de la file d'écritures hors ligne — uniquement une fois authentifié.
  useEffect(() => {
    if (!status?.authenticated) return
    return initOfflineSync(() => {
      window.dispatchEvent(new CustomEvent('jarvis:offline-sync-done'))
    })
  }, [status?.authenticated])

  // Hygiène de confidentialité : purge le cache/la file hors ligne à la déconnexion.
  useEffect(() => {
    if (status && !status.authenticated) {
      void clearOfflineDB()
    }
  }, [status?.authenticated])

  if (!status) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-muted-foreground font-mono text-sm">
        Chargement…
      </div>
    )
  }

  if (status.authenticated && !softLocked) {
    return <>{children}</>
  }

  const mode: 'setup' | 'unlock' = status.configured ? 'unlock' : 'setup'

  async function handleSetup(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (secret.length < 4) {
      setError('4 caractères minimum.')
      return
    }
    if (secret !== confirmSecret) {
      setError('Les deux saisies ne correspondent pas.')
      return
    }
    setBusy(true)
    try {
      await api.authSetup(secret)
      setSecret('')
      setConfirmSecret('')
      await refreshStatus()
    } catch (err) {
      setError(err instanceof ApiError ? `Échec (${err.status})` : 'Échec de la configuration.')
    } finally {
      setBusy(false)
    }
  }

  async function handleUnlock(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (softLocked && status?.authenticated) {
        const r = await api.authVerify(secret)
        if (!r.ok) throw new ApiError('bad secret', 401)
        setSoftLocked(false)
      } else {
        await api.authUnlock(secret)
      }
      setSecret('')
      await refreshStatus()
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Trop de tentatives — réessayez plus tard.')
      } else {
        setError('Secret incorrect.')
      }
      await refreshStatus()
    } finally {
      setBusy(false)
    }
  }

  const locked = lockoutSeconds > 0

  return (
    <div className="flex h-screen items-center justify-center bg-background px-4">
      <div className="glass-panel w-full max-w-sm rounded-xl p-6 animate-fade-in">
        <div className="mb-6 text-center">
          <div className="text-lg font-semibold tracking-wide text-foreground">JARVIS</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {mode === 'setup' ? 'Définissez votre code de déverrouillage' : 'Application verrouillée'}
          </div>
        </div>

        {mode === 'setup' ? (
          <form onSubmit={handleSetup} className="space-y-3">
            <input
              type="password"
              autoFocus
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="Nouveau code (4+ caractères)"
              className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground outline-none focus:border-foreground/40"
            />
            <input
              type="password"
              value={confirmSecret}
              onChange={(e) => setConfirmSecret(e.target.value)}
              placeholder="Confirmez le code"
              className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground outline-none focus:border-foreground/40"
            />
            {error && <div className="text-xs text-red-400">{error}</div>}
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-foreground/90 px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground disabled:opacity-50"
            >
              {busy ? 'Configuration…' : 'Configurer'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleUnlock} className="space-y-3">
            <input
              type="password"
              autoFocus
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="Code de déverrouillage"
              disabled={locked}
              className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground outline-none focus:border-foreground/40 disabled:opacity-50"
            />
            {locked ? (
              <div className="text-xs text-amber-400">
                Verrouillé — réessayez dans {lockoutSeconds}s.
              </div>
            ) : (
              error && <div className="text-xs text-red-400">{error}</div>
            )}
            <button
              type="submit"
              disabled={busy || locked}
              className="w-full rounded-lg bg-foreground/90 px-3 py-2 text-sm font-medium text-background transition hover:bg-foreground disabled:opacity-50"
            >
              {busy ? 'Vérification…' : 'Déverrouiller'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
