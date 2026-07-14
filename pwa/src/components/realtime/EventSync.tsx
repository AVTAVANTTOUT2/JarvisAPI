'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

const QUERY_KEYS_BY_EVENT: Record<string, readonly (readonly unknown[])[]> = {
  'notification.created': [['notifications'], ['notifications-all']],
  'task.created': [['tasks']],
  'task.updated': [['tasks']],
  'conversation.updated': [['conversations']],
  'message.sent': [['conversations']],
  'memory.updated': [['briefing']],
  'person.upserted': [['people'], ['briefing']],
  'episode.saved': [['briefing']],
  'pattern.detected': [['briefing']],
  'fact.added': [['briefing']],
};

type EventEnvelope = {
  event_type?: string;
  type?: string;
};

/** Invalide les lectures React Query à partir du flux SSE du backend. */
export function RealtimeEventSync() {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined;

    const source = new EventSource('/api/events/stream');
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as EventEnvelope;
        const eventType = event.event_type ?? event.type;
        if (!eventType) return;
        for (const queryKey of QUERY_KEYS_BY_EVENT[eventType] ?? []) {
          void queryClient.invalidateQueries({ queryKey: [...queryKey] });
        }
      } catch {
        // EventSource se reconnecte automatiquement ; un message invalide est ignoré.
      }
    };

    return () => source.close();
  }, [queryClient]);

  return null;
}
