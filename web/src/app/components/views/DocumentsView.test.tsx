import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getOutputs: vi.fn(),
  getMemory: vi.fn(),
  getRecordings: vi.fn(),
  getRecording: vi.fn(),
  uploadFile: vi.fn(),
  getOutputUrl: vi.fn(),
  getRecordingSession: vi.fn(),
  startRecordingSession: vi.fn(),
  uploadRecordingChunk: vi.fn(),
  completeRecordingSession: vi.fn(),
  cancelRecordingSession: vi.fn(),
  retryRecordingSession: vi.fn(),
}))

vi.mock('@unified/lib/api', () => ({ api: apiMocks }))

import { DocumentsView } from './DocumentsView'

describe('DocumentsView recording contract', () => {
  beforeEach(() => {
    window.localStorage.clear()
    apiMocks.getOutputs.mockResolvedValue({ files: [] })
    apiMocks.getMemory.mockResolvedValue({ school_documents: [] })
    apiMocks.getRecordings.mockResolvedValue({
      recordings: [{
        id: 17,
        title: 'Réunion Orion',
        duration_seconds: 1800,
        summary: 'Décision prise',
        created_at: '2026-08-27T08:00:00Z',
      }],
    })
    apiMocks.getRecording.mockResolvedValue({
      id: 17,
      title: 'Réunion Orion',
      duration_seconds: 1800,
      summary: 'Décision prise',
      synthesis: { key_points: ['Budget validé', 'Livraison vendredi'] },
      transcription: 'Transcription locale',
      created_at: '2026-08-27T08:00:00Z',
    })
  })

  afterEach(() => cleanup())

  it('unwraps the recordings envelope and renders an object synthesis safely', async () => {
    render(<DocumentsView />)

    expect(await screen.findByText('Réunion Orion')).toBeTruthy()
    expect(screen.getByTestId('long-recording-control')).toBeTruthy()
    fireEvent.click(screen.getByText('Réunion Orion'))

    await waitFor(() => expect(apiMocks.getRecording).toHaveBeenCalledWith(17))
    expect(await screen.findByText('Budget validé • Livraison vendredi')).toBeTruthy()
  })
})
