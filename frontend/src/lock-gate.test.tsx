import { fireEvent, screen, waitFor } from '@testing-library/dom'
import { act, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { LockGate, type AuthClient, type AuthStatus } from '@jarvis/auth'

const lockedStatus: AuthStatus = {
  configured: true,
  authenticated: false,
  locked_out: false,
  lockout_seconds: 0,
  auto_lock_minutes: 5,
}

function fakeClient(statuses: AuthStatus[]): AuthClient {
  return {
    status: vi.fn(async () => statuses.shift() ?? lockedStatus),
    setup: vi.fn(async () => ({ ok: true })),
    unlock: vi.fn(async () => ({ ok: true })),
    verify: vi.fn(async () => ({ ok: true })),
    logout: vi.fn(async () => ({ ok: true })),
  } as unknown as AuthClient
}

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

    await waitFor(() => expect(screen.getByText('Données privées')).toBeInTheDocument())
    expect(client.unlock).toHaveBeenCalledWith('1234')
    expect(startPrivateServices).toHaveBeenCalledOnce()
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
})
