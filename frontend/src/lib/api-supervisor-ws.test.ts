/**
 * Contrat : supervisorWsUrl reste même-origine sur le port 9000.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { supervisorWsUrl } from './api';

function mockLocation(protocol: string, host: string, port: string) {
  vi.stubGlobal('window', {
    location: {
      protocol,
      host,
      hostname: host.split(':')[0],
      port,
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('supervisorWsUrl', () => {
  it('utilise la même origine quand la page est sur :9000', () => {
    mockLocation('http:', 'localhost:9000', '9000');
    expect(supervisorWsUrl()).toBe('ws://localhost:9000/ws/supervisor');
  });

  it('cible :9000 quand la page est servie par FastAPI :8081', () => {
    mockLocation('http:', 'localhost:8081', '8081');
    expect(supervisorWsUrl()).toBe('ws://localhost:9000/ws/supervisor');
  });
});
