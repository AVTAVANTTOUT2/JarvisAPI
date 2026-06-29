'use client';

import { AlertTriangle, CreditCard, Mail, MessageCircle, User } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { NotificationItem } from './types';

interface MailIconConfig {
  icon: LucideIcon;
  bg: string;
  fg: string;
}

/** Choisit l'icone et la couleur selon le contenu et la source. */
function getIconConfig(notif: NotificationItem): MailIconConfig {
  const txt = `${notif.title || ''} ${notif.content || ''}`.toLowerCase();

  if (notif.priority === 'urgent' || notif.priority === 'high') {
    return {
      icon: AlertTriangle,
      bg: 'bg-[rgba(255,69,58,0.1)]',
      fg: 'text-[#FF453A]',
    };
  }

  if (notif.source === 'relationship') {
    return {
      icon: MessageCircle,
      bg: 'bg-[rgba(255,214,10,0.1)]',
      fg: 'text-[#FFD60A]',
    };
  }

  if (/paiement|facture|virement|stripe|paypal|prelevement|carte/i.test(txt)) {
    return {
      icon: CreditCard,
      bg: 'bg-[rgba(255,214,10,0.1)]',
      fg: 'text-[#FFD60A]',
    };
  }

  if (notif.source === 'email' || notif.source === 'email_watcher') {
    return {
      icon: Mail,
      bg: 'bg-[rgba(74,158,255,0.1)]',
      fg: 'text-[#4A9EFF]',
    };
  }

  return {
    icon: User,
    bg: 'bg-[rgba(48,209,88,0.1)]',
    fg: 'text-[#30D158]',
  };
}

/** Formate la date relative (ex. "10:32", "Hier", "12 juin"). */
function formatTime(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    if (isToday) {
      return date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    }
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return 'Hier';
    return date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
  } catch {
    return '';
  }
}

export function MailItem({ notif }: { notif: NotificationItem }) {
  const { icon: Icon, bg, fg } = getIconConfig(notif);
  const isUnread = notif.read === 0 || notif.read === false;
  const time = formatTime(notif.created_at);

  return (
    <div className="flex items-start gap-3 py-3 border-b border-[rgba(255,255,255,0.04)] last:border-b-0 active:bg-[rgba(255,255,255,0.02)] transition-colors">
      <div className="w-[7px] flex-shrink-0 flex justify-center pt-[10px]">
        {isUnread && <div className="w-[7px] h-[7px] rounded-full bg-[#4A9EFF]" />}
      </div>
      <div
        className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${bg}`}
      >
        <Icon size={16} className={fg} />
      </div>
      <div className="flex-1 min-w-0">
        <div
          className={`text-[13px] mb-0.5 truncate ${
            isUnread ? 'font-semibold text-white' : 'text-[#888]'
          }`}
        >
          {notif.title || 'Notification'}
        </div>
        {notif.content && (
          <div className="text-[12px] text-[#666] line-clamp-2 leading-relaxed">
            {notif.content}
          </div>
        )}
      </div>
      {time && (
        <span className="text-[11px] text-[#555] whitespace-nowrap flex-shrink-0 pt-[2px]">
          {time}
        </span>
      )}
    </div>
  );
}
