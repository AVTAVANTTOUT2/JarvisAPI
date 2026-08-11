import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { clearOfflineDB } from '@desktop/lib/offline/db';
import { enqueueWrite } from '@desktop/lib/offline/queue';
import { OfflineStatus } from './OfflineStatus';

describe('OfflineStatus', () => {
  beforeEach(async () => {
    await clearOfflineDB();
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  });

  afterEach(async () => {
    cleanup();
    await clearOfflineDB();
  });

  it('stays hidden while online with an empty queue', async () => {
    render(<OfflineStatus />);
    await waitFor(() => expect(screen.queryByTestId('offline-status')).toBeNull());
  });

  it('announces offline mode and pending mutations', async () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    await enqueueWrite({
      method: 'POST',
      path: '/api/tasks',
      body: { title: 'Test' },
      label: 'Nouvelle tâche',
    });

    render(<OfflineStatus />);

    await waitFor(() => {
      expect(screen.getByText(/Hors ligne/)).toBeTruthy();
      expect(screen.getByText(/1 modification en attente/)).toBeTruthy();
    });
  });

  it('announces when a view falls back to cached data', async () => {
    render(<OfflineStatus />);
    window.dispatchEvent(new CustomEvent('jarvis:offline-cache-hit', {
      detail: { staleMs: 120_000 },
    }));

    await waitFor(() => expect(screen.getByText(/il y a 2 min/)).toBeTruthy());
  });
});
