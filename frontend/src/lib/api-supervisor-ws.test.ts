/**
 * Contrat du plan de contrôle : **même origine, toujours**.
 *
 * Une version antérieure faisait viser `hostname:9000` au WebSocket depuis une
 * page servie par le backend, et un test figeait ce comportement. Le serveur le
 * refuse pourtant : `/ws/supervisor` exige `Origin == Host` et ferme en 4403.
 * Le test verrouillait donc une attente que rien ne pouvait satisfaire, pendant
 * que l'interface traduisait l'échec en « superviseur arrêté ».
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  SUPERVISOR_PORT,
  isServedBySupervisor,
  supervisorOrigin,
  supervisorWsUrl,
} from './api';

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
  it('utilise la même origine quand la page est servie par le superviseur', () => {
    mockLocation('http:', 'localhost:9000', '9000');
    expect(supervisorWsUrl()).toBe('ws://localhost:9000/ws/supervisor');
  });

  it('reste en même origine même servie ailleurs — jamais de saut de port', () => {
    // Sauter vers :9000 depuis :8081 produit une fermeture 4403 côté serveur.
    // Mieux vaut une URL cohérente et un diagnostic clair qu'une connexion
    // condamnée dont l'échec passe pour une panne du superviseur.
    mockLocation('http:', 'localhost:8081', '8081');
    expect(supervisorWsUrl()).toBe('ws://localhost:8081/ws/supervisor');
  });

  it('suit le schéma sécurisé de la page', () => {
    mockLocation('https:', 'jarvis.local:9000', '9000');
    expect(supervisorWsUrl()).toBe('wss://jarvis.local:9000/ws/supervisor');
  });
});

describe('isServedBySupervisor', () => {
  it('reconnaît le port du superviseur', () => {
    mockLocation('https:', 'localhost:9000', '9000');
    expect(isServedBySupervisor()).toBe(true);
  });

  it('refuse le port du backend', () => {
    mockLocation('https:', 'localhost:8081', '8081');
    expect(isServedBySupervisor()).toBe(false);
  });
});

describe('supervisorOrigin', () => {
  it('conserve le schéma et l’hôte, et impose le port du superviseur', () => {
    mockLocation('https:', 'jarvis.local:8081', '8081');
    expect(supervisorOrigin()).toBe(`https://jarvis.local:${SUPERVISOR_PORT}`);
  });
});
