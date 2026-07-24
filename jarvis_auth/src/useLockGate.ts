import { useCallback, useEffect, useRef, useState } from 'react'

import { AuthClient, AuthError, authClient, type AuthStatus } from './client'

const INACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'] as const

export interface UseLockGateOptions {
  client?: AuthClient
  onAuthenticated?: () => void | (() => void)
  onUnauthenticated?: () => void
}

export interface LockGateState {
  status: AuthStatus | null
  loading: boolean
  connectionError: boolean
  softLocked: boolean
  lockoutSeconds: number
  refresh: () => Promise<void>
  setup: (secret: string) => Promise<void>
  unlock: (secret: string) => Promise<void>
  localUnlock: (secret: string) => Promise<void>
  logout: () => Promise<void>
}

/** Orchestre le setup, le déverrouillage et l'auto-lock côté navigateur. */
export function useLockGate(options: UseLockGateOptions = {}): LockGateState {
  const client = options.client ?? authClient
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [connectionError, setConnectionError] = useState(false)
  const [softLocked, setSoftLocked] = useState(false)
  const [lockoutSeconds, setLockoutSeconds] = useState(0)
  const inactivityTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastActivityAt = useRef(Date.now())

  const refresh = useCallback(async () => {
    try {
      const nextStatus = await client.status()
      setStatus(nextStatus)
      setLockoutSeconds(nextStatus.lockout_seconds)
      setConnectionError(false)
      if (!nextStatus.authenticated) setSoftLocked(false)
    } catch {
      setStatus(null)
      setSoftLocked(true)
      setConnectionError(true)
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void refresh()
    const onAuthRequired = () => void refresh()
    window.addEventListener('jarvis:auth-required', onAuthRequired)
    return () => window.removeEventListener('jarvis:auth-required', onAuthRequired)
  }, [refresh])

  useEffect(() => {
    if (!status?.authenticated || softLocked) return
    const durationMs = Math.max(1, status.auto_lock_minutes || 5) * 60_000

    const armTimer = () => {
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current)
      const remaining = Math.max(0, durationMs - (Date.now() - lastActivityAt.current))
      inactivityTimer.current = setTimeout(() => setSoftLocked(true), remaining)
    }
    const recordActivity = () => {
      lastActivityAt.current = Date.now()
      armTimer()
    }
    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastActivityAt.current >= durationMs) setSoftLocked(true)
      else armTimer()
    }

    recordActivity()
    for (const eventName of INACTIVITY_EVENTS) window.addEventListener(eventName, recordActivity)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      for (const eventName of INACTIVITY_EVENTS) window.removeEventListener(eventName, recordActivity)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current)
    }
  }, [softLocked, status?.authenticated, status?.auto_lock_minutes])

  useEffect(() => {
    if (lockoutSeconds <= 0) return
    const timer = setInterval(() => setLockoutSeconds((seconds) => Math.max(0, seconds - 1)), 1000)
    return () => clearInterval(timer)
  }, [lockoutSeconds])

  // Les services privés (GPS, resynchronisation offline) ne doivent tourner
  // que lorsque la session est effectivement visible. Un soft lock conserve le
  // cookie serveur, mais doit néanmoins exécuter le cleanup du service.
  const privateServicesEnabled = Boolean(status?.authenticated) && !softLocked && !connectionError
  useEffect(() => {
    if (!privateServicesEnabled) return
    return options.onAuthenticated?.()
  }, [options.onAuthenticated, privateServicesEnabled])

  // La déconnexion serveur conserve sa sémantique distincte du verrouillage
  // local : les données offline sont seulement effacées sans session active.
  useEffect(() => {
    if (status?.authenticated === false) options.onUnauthenticated?.()
  }, [options.onUnauthenticated, status?.authenticated])

  const setup = useCallback(async (secret: string) => {
    await client.setup(secret)
    await refresh()
  }, [client, refresh])

  const unlock = useCallback(async (secret: string) => {
    if (softLocked && status?.authenticated) {
      const result = await client.verify(secret)
      if (!result.ok) throw new AuthError('Secret incorrect', 401)
      setSoftLocked(false)
      lastActivityAt.current = Date.now()
      return
    }
    await client.unlock(secret)
    await refresh()
  }, [client, refresh, softLocked, status?.authenticated])

  const localUnlock = useCallback(async (secret: string) => {
    await client.localUnlock(secret)
    setSoftLocked(false)
    lastActivityAt.current = Date.now()
    await refresh()
  }, [client, refresh])

  const logout = useCallback(async () => {
    await client.logout()
    setSoftLocked(false)
    await refresh()
  }, [client, refresh])

  return {
    status,
    loading,
    connectionError,
    softLocked,
    lockoutSeconds,
    refresh,
    setup,
    unlock,
    localUnlock,
    logout,
  }
}
