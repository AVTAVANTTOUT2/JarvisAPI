/** Configuration visuelle de chaque type de section briefing.
 *
 * Tous les icones sont Lucide React. ZERO emoji.
 */

import {
  AlertTriangle,
  Calendar,
  Cloud,
  Info,
  Mail,
  MessageCircle,
  Star,
  Target,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { SectionType } from './briefing-parser';

export type SectionColor = 'blue' | 'green' | 'yellow' | 'red' | 'purple';

export interface SectionConfig {
  icon: LucideIcon;
  color: SectionColor;
  label: string;
}

export const SECTION_CONFIG: Record<SectionType, SectionConfig> = {
  intro: { icon: Info, color: 'blue', label: '' },
  emails: { icon: Mail, color: 'blue', label: 'Emails' },
  agenda: { icon: Calendar, color: 'green', label: 'Agenda' },
  messages: { icon: MessageCircle, color: 'yellow', label: 'Messages en attente' },
  priorities: { icon: Target, color: 'red', label: 'Priorites du jour' },
  weather: { icon: Cloud, color: 'blue', label: 'Meteo' },
  tasks: { icon: AlertTriangle, color: 'red', label: 'Taches en retard' },
  attention: { icon: Star, color: 'purple', label: "Point d'attention" },
  other: { icon: Info, color: 'blue', label: 'Note' },
};

/** Classes Tailwind pour le bloc icone (bg + couleur foreground). */
export const COLOR_CLASSES: Record<SectionColor, string> = {
  blue: 'bg-[rgba(74,158,255,0.12)] text-[#4A9EFF]',
  green: 'bg-[rgba(48,209,88,0.12)] text-[#30D158]',
  yellow: 'bg-[rgba(255,214,10,0.12)] text-[#FFD60A]',
  red: 'bg-[rgba(255,69,58,0.12)] text-[#FF453A]',
  purple: 'bg-[rgba(156,89,255,0.12)] text-[#9C59FF]',
};

/** Hex de la couleur (pour bordures custom ou stats). */
export const COLOR_HEX: Record<SectionColor, string> = {
  blue: '#4A9EFF',
  green: '#30D158',
  yellow: '#FFD60A',
  red: '#FF453A',
  purple: '#9C59FF',
};
