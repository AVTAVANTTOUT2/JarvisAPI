import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  getConversations: vi.fn(),
  getPeople: vi.fn(),
  getTasks: vi.fn(),
  getMemory: vi.fn(),
  search: vi.fn(),
}));

vi.mock('@unified/lib/api', () => ({ api: apiMocks }));
vi.mock('@desktop/services/websocket', () => ({
  ws: {
    on: vi.fn(() => () => undefined),
    sendText: vi.fn(),
  },
}));

import { SearchView } from './SearchView';

function renderView() {
  return render(
    <MemoryRouter initialEntries={['/search']}>
      <SearchView />
    </MemoryRouter>,
  );
}

describe('SearchView unified experience', () => {
  beforeEach(() => {
    apiMocks.getConversations.mockResolvedValue({ conversations: [] });
    apiMocks.getPeople.mockResolvedValue({ people: [] });
    apiMocks.getTasks.mockResolvedValue({ tasks: [] });
    apiMocks.getMemory.mockResolvedValue({ school_documents: [] });
    apiMocks.search.mockReset();
  });

  afterEach(() => cleanup());

  it('debounces the backend search and renders ranked categories', async () => {
    apiMocks.search.mockResolvedValue({
      query: 'alpha',
      total: 2,
      categories: { tasks: 1, memory: 1 },
      results: [
        { type: 'task', category: 'tasks', id: 1, title: 'alpha', subtitle: 'Préparer Atlas', meta: 'todo', url: '/tasks?task=1', score: 110 },
        { type: 'fact', category: 'memory', id: 2, title: 'Projet Atlas', subtitle: 'alpha est validé', meta: 'high', url: '/data?entry=fact-2', score: 35 },
      ],
    });

    const { container } = renderView();
    fireEvent.change(screen.getByPlaceholderText(/Rechercher dans conversations/), {
      target: { value: 'alpha' },
    });

    await waitFor(() => expect(apiMocks.search).toHaveBeenCalledWith('alpha', expect.any(AbortSignal)));
    await waitFor(() => {
      expect(container.textContent).toContain('Préparer Atlas');
      expect(container.textContent).toContain('alpha est validé');
      expect(screen.getAllByText('Mémoire (1)')).toHaveLength(2);
    });
  });

  it('falls back to locally loaded data when the server is unreachable', async () => {
    apiMocks.getTasks.mockResolvedValue({
      tasks: [{ id: 3, title: 'Relire le plan alpha', status: 'todo', priority: 'high' }],
    });
    apiMocks.search.mockRejectedValue(new TypeError('Failed to fetch'));

    const { container } = renderView();
    fireEvent.change(screen.getByPlaceholderText(/Rechercher dans conversations/), {
      target: { value: 'alpha' },
    });

    await waitFor(() => expect(screen.getByText(/résultats limités aux données déjà chargées/)).toBeTruthy());
    expect(container.textContent).toContain('Relire le plan alpha');
  });
});
