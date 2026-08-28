import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LockGate, setActiveProfileId, type AuthClient, type AuthStatus } from '@jarvis/auth'

const lockedStatus: AuthStatus = {
  configured: true,
  authenticated: false,
  csrf_token: null,
  locked_out: false,
  lockout_seconds: 0,
  lockout_scope: null,
  local_recovery_available: false,
  auto_lock_minutes: 5,
}

function fakeClient(statuses: AuthStatus[]): AuthClient {
  return {
    status: vi.fn(async () => statuses.shift() ?? lockedStatus),
    setup: vi.fn(async () => ({ ok: true })),
    unlock: vi.fn(async () => ({ ok: true })),
    localUnlock: vi.fn(async () => ({ ok: true, recovered: true })),
    verify: vi.fn(async () => ({ ok: true })),
    logout: vi.fn(async () => ({ ok: true })),
    selectProfile: vi.fn(),
  } as unknown as AuthClient
}

const storageValues = new Map<string, string>()
const localStorageMock: Storage = {
  get length() { return storageValues.size },
  clear: () => storageValues.clear(),
  getItem: (key) => storageValues.get(key) ?? null,
  key: (index) => [...storageValues.keys()][index] ?? null,
  removeItem: (key) => { storageValues.delete(key) },
  setItem: (key, value) => { storageValues.set(key, String(value)) },
}

beforeEach(() => {
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: localStorageMock,
  })
  localStorageMock.clear()
  setActiveProfileId('default')
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

describe('shared LockGate', () => {
  it('fails closed when the server status cannot be verified', async () => {
    const client = {
      status: vi.fn(async () => { throw new Error('offline') }),
    } as unknown as AuthClient
    render(
      <LockGate client={client}>
        <div>Données privées</div>
      </LockGate>,
    )

    expect(await screen.findByText('Connexion au serveur impossible')).toBeInTheDocument()
    expect(screen.queryByText('Données privées')).not.toBeInTheDocument()
  })

  it('never renders protected mobile content before authentication', async () => {
    const startPrivateServices = vi.fn()
    render(
      <LockGate client={fakeClient([lockedStatus])} onAuthenticated={startPrivateServices}>
        <div>Données privées</div>
      </LockGate>,
    )

    expect(screen.queryByText('Données privées')).not.toBeInTheDocument()
    expect(await screen.findByText('Application verrouillée')).toBeInTheDocument()
    expect(startPrivateServices).not.toHaveBeenCalled()
  })

  it('unlocks through the shared client and then reveals content', async () => {
    const authenticated = { ...lockedStatus, authenticated: true }
    const client = fakeClient([lockedStatus, authenticated])
    const startPrivateServices = vi.fn()
    render(
      <LockGate client={client} onAuthenticated={startPrivateServices}>
        <div>Données privées</div>
      </LockGate>,
    )

    fireEvent.change(await screen.findByLabelText('Code de déverrouillage'), { target: { value: '1234' } })
    fireEvent.click(screen.getByRole('button', { name: 'Déverrouiller' }))

    // Le contenu est un rendu ; onAuthenticated est un useEffect. Attendre
    // seulement le DOM restaure IS_REACT_ACT_ENVIRONMENT avant l'effet.
    await waitFor(() => {
      expect(screen.getByText('Données privées')).toBeInTheDocument()
      expect(startPrivateServices).toHaveBeenCalledOnce()
    })
    expect(client.unlock).toHaveBeenCalledWith('1234')
  })

  it('requires a four-digit PIN or a ten-character passphrase during setup', async () => {
    const client = fakeClient([{ ...lockedStatus, configured: false }])
    render(
      <LockGate client={client}>
        <div>Données privées</div>
      </LockGate>,
    )

    fireEvent.change(await screen.findByLabelText('Nouveau code'), { target: { value: '123' } })
    fireEvent.change(screen.getByLabelText('Confirmation du code'), { target: { value: '123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Configurer' }))

    expect(
      await screen.findByText('Utilisez un PIN de 4 chiffres ou une passphrase de 10 caractères.'),
    ).toBeInTheDocument()
    expect(client.setup).not.toHaveBeenCalled()
  })

  it('offers recovery only when the server confirms a local client', async () => {
    const lockedLocally = {
      ...lockedStatus,
      locked_out: true,
      lockout_seconds: 60,
      lockout_scope: 'global' as const,
      local_recovery_available: true,
    }
    const authenticated = {
      ...lockedStatus,
      authenticated: true,
      local_recovery_available: true,
    }
    const client = fakeClient([lockedLocally, authenticated])
    render(
      <LockGate client={client}>
        <div>Données privées</div>
      </LockGate>,
    )

    fireEvent.change(await screen.findByLabelText('Code de déverrouillage'), {
      target: { value: 'correct-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Récupérer depuis ce Mac' }))

    await waitFor(() => expect(screen.getByText('Données privées')).toBeInTheDocument())
    expect(client.localUnlock).toHaveBeenCalledWith('correct-secret')
  })

  it('stops private services when the local auto-lock engages', async () => {
    vi.useFakeTimers()
    try {
      const authenticated = { ...lockedStatus, authenticated: true, auto_lock_minutes: 1 }
      const stopPrivateServices = vi.fn()
      const startPrivateServices = vi.fn(() => stopPrivateServices)
      render(
        <LockGate client={fakeClient([authenticated])} onAuthenticated={startPrivateServices}>
          <div>Données privées</div>
        </LockGate>,
      )

      await act(async () => {
        await Promise.resolve()
      })
      expect(startPrivateServices).toHaveBeenCalledOnce()
      expect(screen.getByText('Données privées')).toBeInTheDocument()

      await act(async () => {
        vi.advanceTimersByTime(60_000)
      })
      expect(screen.getByText('Application verrouillée')).toBeInTheDocument()
      expect(stopPrivateServices).toHaveBeenCalledOnce()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps an auto-lock closed across reload and verifies the secret before remounting', async () => {
    window.localStorage.setItem('jarvis:soft-lock', '1')
    const authenticated = { ...lockedStatus, authenticated: true }
    const client = fakeClient([authenticated])

    render(
      <LockGate client={client}>
        <div>Données privées</div>
      </LockGate>,
    )

    expect(await screen.findByText('Application verrouillée')).toBeInTheDocument()
    expect(screen.queryByText('Données privées')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Code de déverrouillage'), {
      target: { value: 'correct-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Déverrouiller' }))

    await waitFor(() => expect(screen.getByText('Données privées')).toBeInTheDocument())
    expect(client.verify).toHaveBeenCalledWith('correct-secret')
    expect(client.unlock).not.toHaveBeenCalled()
    expect(window.localStorage.getItem('jarvis:soft-lock')).toBeNull()
  })

  it('revokes the active session before switching profiles from a soft lock', async () => {
    window.localStorage.setItem('jarvis:soft-lock', '1')
    const authenticated = { ...lockedStatus, authenticated: true }
    const client = fakeClient([authenticated, lockedStatus])
    const logout = vi.mocked(client.logout)
    const selectProfile = vi.mocked(client.selectProfile)
    Object.assign(client, {
      profiles: vi.fn(async () => ({
        active_profile: 'default',
        profiles: [
          { id: 'default', display_name: 'Principal', is_active: 1 },
          { id: 'alice-1234', display_name: 'Alice', is_active: 1 },
        ],
      })),
    })

    render(
      <LockGate client={client}>
        <div>Données privées</div>
      </LockGate>,
    )

    fireEvent.change(await screen.findByLabelText('Profil utilisateur'), {
      target: { value: 'alice-1234' },
    })

    await waitFor(() => expect(selectProfile).toHaveBeenCalledWith('alice-1234'))
    expect(logout).toHaveBeenCalledOnce()
    expect(logout.mock.invocationCallOrder[0]).toBeLessThan(
      selectProfile.mock.invocationCallOrder[0],
    )
  })
})
