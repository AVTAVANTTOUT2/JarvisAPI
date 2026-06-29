/** Types partages des notifications utilisees dans la page Mails. */

export interface NotificationItem {
  id: number;
  source: string;
  title: string;
  content: string;
  priority: 'urgent' | 'high' | 'medium' | 'low';
  read: 0 | 1 | boolean;
  email_id: string | null;
  created_at: string;
}

export interface NotificationsResponse {
  notifications: NotificationItem[];
}

export type MailFilter = 'all' | 'urgent' | 'todo' | 'fyi';
