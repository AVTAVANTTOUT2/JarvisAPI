import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { HealthReport, MetricHistoryResponse, VoiceLatencyMetrics } from '@unified/lib/api';

const getHealthDetail = vi.fn();
const getVoiceMetrics = vi.fn();
const getMetricHistory = vi.fn();

vi.mock('@unified/lib/api', () => ({
  api: {
    getHealthDetail: (...args: unknown[]) => getHealthDetail(...args),
    getVoiceMetrics: (...args: unknown[]) => getVoiceMetrics(...args),
    getMetricHistory: (...args: unknown[]) => getMetricHistory(...args),
  },
}));

const { HealthView, HEALTH_POLL_INTERVAL_MS, formatDetailValue } = await import('./HealthView');

function report(overrides: Partial<HealthReport> = {}): HealthReport {
  return {
    status: 'healthy',
    checked_at: '2026-08-10T12:00:00+00:00',
    duration_ms: 8.4,
    summary: { healthy: 2, degraded: 0, unavailable: 0, unknown: 1 },
    components: [
      {
        name: 'backend',
        state: 'healthy',
        critical: true,
        reason: null,
        details: { uptime_s: 3720 },
      },
      {
        name: 'database',
        state: 'healthy',
        critical: true,
        reason: null,
        details: { latency_ms: 0.4, journal_mode: 'wal' },
      },
      {
        name: 'text_to_speech',
        state: 'unknown',
        critical: false,
        reason: 'tts_engine_not_probed',
        details: { provider: 'qwen3_local' },
      },
    ],
    ...overrides,
  };
}

const noVoiceMetrics: VoiceLatencyMetrics = { samples: 0, days: 7, stages: {} };
const noMetricHistory: MetricHistoryResponse = {
  hours: 24,
  bucket_seconds: 300,
  retention_days: 90,
  series: [],
};

beforeEach(() => {
  getHealthDetail.mockReset();
  getVoiceMetrics.mockReset();
  getMetricHistory.mockReset();
  getHealthDetail.mockResolvedValue(report());
  getVoiceMetrics.mockResolvedValue(noVoiceMetrics);
  getMetricHistory.mockResolvedValue(noMetricHistory);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('HealthView', () => {
  it('affiche l’état de chargement avant la première réponse', async () => {
    let resolve!: (value: HealthReport) => void;
    getHealthDetail.mockReturnValue(new Promise<HealthReport>((r) => { resolve = r; }));

    render(<HealthView />);
    expect(screen.getByTestId('health-loading')).toBeTruthy();

    await act(async () => { resolve(report()); });
    await waitFor(() => expect(screen.queryByTestId('health-loading')).toBeNull());
  });

  it('rend l’état global et chaque composant avec son état', async () => {
    render(<HealthView />);

    await waitFor(() => expect(screen.getByTestId('health-overall')).toBeTruthy());
    expect(screen.getByTestId('health-overall').getAttribute('data-state')).toBe('healthy');
    expect(screen.getByTestId('health-component-backend').getAttribute('data-state')).toBe('healthy');
    expect(screen.getByTestId('health-component-database').getAttribute('data-state')).toBe('healthy');
    expect(screen.getByTestId('health-component-text_to_speech').getAttribute('data-state')).toBe('unknown');
    expect(screen.getByTestId('health-checked-at').textContent).toContain('2026-08-10T12:00:00+00:00');
  });

  it('n’affiche jamais un composant non vérifié comme opérationnel', async () => {
    render(<HealthView />);

    await waitFor(() => expect(screen.getByTestId('health-component-text_to_speech')).toBeTruthy());
    const card = screen.getByTestId('health-component-text_to_speech');
    expect(card.textContent).toContain('Non vérifié');
    expect(card.textContent).not.toContain('Opérationnel');
    expect(card.textContent).toContain('Moteur non exercé depuis le démarrage');
  });

  it('rend une panne partielle sans masquer les composants sains', async () => {
    getHealthDetail.mockResolvedValue(
      report({
        status: 'degraded',
        summary: { healthy: 1, degraded: 0, unavailable: 1, unknown: 1 },
        components: [
          { name: 'backend', state: 'healthy', critical: true, reason: null, details: {} },
          {
            name: 'speech_to_text',
            state: 'unavailable',
            critical: false,
            reason: 'stt_unavailable',
            details: {},
          },
        ],
      }),
    );

    render(<HealthView />);

    await waitFor(() => expect(screen.getByTestId('health-overall').getAttribute('data-state')).toBe('degraded'));
    expect(screen.getByTestId('health-component-backend').getAttribute('data-state')).toBe('healthy');
    expect(screen.getByTestId('health-component-speech_to_text').textContent).toContain(
      'Moteur de transcription indisponible',
    );
  });

  it('affiche une erreur de transport sans casser la page', async () => {
    getHealthDetail.mockRejectedValue(new Error('API 502'));

    render(<HealthView />);

    await waitFor(() => expect(screen.getByTestId('health-error')).toBeTruthy());
    expect(screen.getByTestId('health-error').textContent).toContain('API 502');
  });

  it('affiche l’état vide des latences vocales quand rien n’a été mesuré', async () => {
    render(<HealthView />);
    await waitFor(() => expect(screen.getByTestId('voice-metrics-empty')).toBeTruthy());
  });

  it('réutilise /api/voice/metrics au lieu de recalculer les latences', async () => {
    getVoiceMetrics.mockResolvedValue({
      samples: 12,
      days: 7,
      stages: { stt: { p50_ms: 2478, p95_ms: 2700, count: 12 } },
    } satisfies VoiceLatencyMetrics);

    render(<HealthView />);

    await waitFor(() => expect(screen.getByTestId('voice-metrics')).toBeTruthy());
    expect(screen.getByTestId('voice-metrics').textContent).toContain('2478 ms');
    expect(getVoiceMetrics).toHaveBeenCalled();
  });

  it('affiche les tendances santé persistées par le serveur', async () => {
    getMetricHistory.mockResolvedValue({
      ...noMetricHistory,
      series: [
        {
          metric: 'health.score',
          unit: 'percent',
          points: [
            { timestamp: '2026-08-10 10:00:00Z', value: 50, last_value: 50, samples: 1 },
            { timestamp: '2026-08-10 10:05:00Z', value: 100, last_value: 100, samples: 2 },
          ],
          summary: {
            latest: 100,
            average: 83.3,
            minimum: 50,
            maximum: 100,
            trend_pct: 100,
            samples: 3,
          },
        },
      ],
    } satisfies MetricHistoryResponse);

    render(<HealthView />);

    await waitFor(() => expect(screen.getByTestId('metric-history')).toBeTruthy());
    expect(screen.getByTestId('metric-history').textContent).toContain('Disponibilité');
    expect(screen.getByTestId('metric-history').textContent).toContain('100 %');
    expect(getMetricHistory).toHaveBeenCalledWith(24, expect.any(AbortSignal));
  });

  it('interroge le serveur périodiquement puis arrête le timer au démontage', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { unmount } = render(<HealthView />);

    await waitFor(() => expect(getHealthDetail).toHaveBeenCalledTimes(1));

    await act(async () => { await vi.advanceTimersByTimeAsync(HEALTH_POLL_INTERVAL_MS); });
    expect(getHealthDetail).toHaveBeenCalledTimes(2);

    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(HEALTH_POLL_INTERVAL_MS * 3); });
    expect(getHealthDetail).toHaveBeenCalledTimes(2);
  });

  it('annule la requête en vol au démontage', async () => {
    const { unmount } = render(<HealthView />);
    await waitFor(() => expect(getHealthDetail).toHaveBeenCalled());

    const signal = getHealthDetail.mock.calls[0][0].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    unmount();
    expect(signal.aborted).toBe(true);
  });

  it('retire son écouteur de visibilité au démontage', () => {
    const add = vi.spyOn(document, 'addEventListener');
    const remove = vi.spyOn(document, 'removeEventListener');

    const { unmount } = render(<HealthView />);
    expect(add.mock.calls.some(([type]) => type === 'visibilitychange')).toBe(true);

    unmount();
    expect(remove.mock.calls.some(([type]) => type === 'visibilitychange')).toBe(true);

    add.mockRestore();
    remove.mockRestore();
  });
});

describe('formatDetailValue', () => {
  it('rend les durées, les latences et les tailles avec leur unité', () => {
    expect(formatDetailValue('uptime_s', 3720)).toBe('1 h 2 min');
    expect(formatDetailValue('uptime_s', 45)).toBe('45 s');
    expect(formatDetailValue('latency_ms', 0.4)).toBe('0.4 ms');
    expect(formatDetailValue('free_mb', 2048.4)).toBe('2048 Mo');
  });

  it('rend les booléens et les valeurs absentes lisiblement', () => {
    expect(formatDetailValue('loop_bound', true)).toBe('oui');
    expect(formatDetailValue('loop_bound', false)).toBe('non');
    expect(formatDetailValue('journal_mode', null)).toBe('—');
  });
});
