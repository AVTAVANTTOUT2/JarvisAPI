'use client';

import { MailItem } from './MailItem';
import type { NotificationItem } from './types';

interface Props {
  notifications: NotificationItem[];
  isLoading?: boolean;
}

export function MailList({ notifications, isLoading }: Props) {
  if (isLoading) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] px-4">
        {[1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="flex items-center gap-3 py-3 border-b border-[rgba(255,255,255,0.04)] last:border-b-0"
          >
            <div className="w-9 h-9 rounded-full bg-[rgba(255,255,255,0.05)] animate-pulse flex-shrink-0" />
            <div className="flex-1 space-y-1.5">
              <div className="h-3 w-3/4 rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
              <div className="h-2.5 w-1/2 rounded bg-[rgba(255,255,255,0.04)] animate-pulse" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (notifications.length === 0) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] py-12 px-4 text-center">
        <p className="text-[13px] text-[#666]">Aucune notification dans cette categorie.</p>
      </div>
    );
  }

  return (
    <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] px-4">
      {notifications.map((notif) => (
        <MailItem key={notif.id} notif={notif} />
      ))}
    </div>
  );
}
