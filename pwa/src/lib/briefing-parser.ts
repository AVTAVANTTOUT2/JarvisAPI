/**
 * Parser le briefing JARVIS en sections structurees.
 *
 * Le backend retourne un string brut avec :
 *   - "Bonjour Monsieur. Briefing du <date>."
 *   - Sections separees par "---" et un header "— EMAILS (N analyses)",
 *     "— AGENDA", "— PRIORITES DU JOUR", "— MESSAGES NON REPONDUS",
 *     "— METEO", "— TACHES (...)".
 *   - Eventuel paragraphe final "**Point d'attention** : ..."
 */

export type SectionType =
  | 'intro'
  | 'emails'
  | 'agenda'
  | 'messages'
  | 'priorities'
  | 'weather'
  | 'tasks'
  | 'attention'
  | 'other';

export interface BriefingSection {
  /** Type de section, utilise pour choisir l'icone et le rendu. */
  type: SectionType;
  /** Titre affiche dans la card. */
  title: string;
  /** Texte nettoye (sans markers Markdown). */
  content: string;
  /** Compteur extrait du header si present (ex. "EMAILS (12 analyses)" -> 12). */
  count?: number;
  /** Vrai si la section signale quelque chose d'urgent. */
  urgent?: boolean;
  /**
   * Liste d'items extraits pour les sections "priorities" / "messages".
   * Pour priorities : items numerotes "1. ... 2. ...".
   * Pour messages : noms apres tirets.
   */
  items?: string[];
}

const HEADER_REGEX =
  /(?:^|\n)\s*(?:---\s*\n\s*)?[—\-]+\s*(EMAILS?\s*\([^)]*\)|EMAILS?|AGENDA|MESSAGES?\s*NON\s*R[ÉE]PONDUS(?:\s*\([^)]*\))?|PRIORIT[ÉE]S?\s*DU\s*JOUR|M[ÉE]T[ÉE]O|T[ÂA]CHES?\s*[^\n]*)\s*\n/gi;

const ATTENTION_REGEX = /\*\*Point d'attention\*\*\s*:?\s*/i;

const NUMBERED_ITEM_REGEX = /(?:^|\n)\s*(\d+)\.\s+([^\n]+(?:\n(?!\s*\d+\.|\s*[—\-]+|\s*---).*)*)/g;

const NAMES_IN_PARENS_REGEX = /\(dont\s+([^)]+)\)/i;

/** Retire le contour Markdown sans toucher au contenu utile. */
function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1') // bold
    .replace(/\*(.+?)\*/g, '$1')     // italic
    .replace(/^\s*---\s*$/gm, '')    // separateurs
    .replace(/[ \t]+\n/g, '\n')      // trailing spaces
    .replace(/\n{3,}/g, '\n\n')      // collapse blank lines
    .trim();
}

/** Detecte le type d'une section a partir du header brut. */
function classifyHeader(header: string): { type: SectionType; title: string; count?: number } {
  const h = header.toUpperCase();
  let count: number | undefined;
  const countMatch = header.match(/\((\d+)/);
  if (countMatch) count = parseInt(countMatch[1], 10);

  if (/EMAIL/.test(h)) return { type: 'emails', title: 'Emails', count };
  if (/AGENDA/.test(h)) return { type: 'agenda', title: 'Agenda', count };
  if (/MESSAGES?\s*NON\s*R[ÉE]PONDUS/.test(h)) return { type: 'messages', title: 'Messages en attente', count };
  if (/PRIORIT/.test(h)) return { type: 'priorities', title: 'Priorites du jour', count };
  if (/M[ÉE]T[ÉE]O/.test(h)) return { type: 'weather', title: 'Meteo', count };
  if (/T[ÂA]CHES?/.test(h)) return { type: 'tasks', title: 'Taches en retard', count };
  return { type: 'other', title: header.trim(), count };
}

/** Extrait les items numerotes "1. ... 2. ..." */
function extractNumberedItems(content: string): string[] {
  const items: string[] = [];
  for (const m of content.matchAll(NUMBERED_ITEM_REGEX)) {
    items.push(m[2].trim().replace(/\s+/g, ' '));
  }
  return items;
}

/** Extrait les noms cites entre parentheses "(dont X, Y, Z)". */
function extractNamesInParens(text: string): string[] | undefined {
  const m = text.match(NAMES_IN_PARENS_REGEX);
  if (!m) return undefined;
  return m[1]
    .split(/,| et /)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Extrait les noms apres tirets "- Name" en debut de ligne. */
function extractDashedNames(content: string): string[] {
  const items: string[] = [];
  for (const line of content.split('\n')) {
    const m = line.match(/^\s*[-–]\s+(.+?)\s*$/);
    if (m) items.push(m[1].trim());
  }
  return items;
}

/**
 * Parse le briefing en sections affichables.
 *
 * Robuste aux variations : header avec ou sans parentheses, separateurs
 * "---" optionnels, "Point d'attention" en fin.
 */
export function parseBriefing(raw: string): BriefingSection[] {
  if (!raw || typeof raw !== 'string') return [];

  // 1. Nettoyer le preambule
  let text = raw
    .replace(/^\s*Bonjour Monsieur\.\s*/i, '')
    .replace(/^\s*Bonsoir Monsieur\.\s*/i, '')
    .replace(/^\s*Briefing du[^.]+\.\s*/i, '')
    .replace(/^\s*Bilan du[^.]+\.\s*/i, '')
    .trim();

  if (!text) return [];

  const sections: BriefingSection[] = [];

  // 2. Extraire le "Point d'attention" final s'il existe — il finit le briefing
  let attentionContent: string | null = null;
  const attentionMatch = text.match(ATTENTION_REGEX);
  if (attentionMatch) {
    const idx = attentionMatch.index ?? 0;
    attentionContent = stripMarkdown(text.slice(idx + attentionMatch[0].length));
    text = text.slice(0, idx).trim();
  }

  // 3. Trouver tous les headers de section
  const matches = [...text.matchAll(HEADER_REGEX)];

  // 4. Intro = tout ce qui precede le premier header
  const introEnd = matches.length > 0 ? matches[0].index ?? text.length : text.length;
  const introRaw = text.slice(0, introEnd).replace(/^\s*-+\s*$/gm, '').trim();
  const intro = stripMarkdown(introRaw);
  if (intro && intro.length > 0) {
    sections.push({ type: 'intro', title: '', content: intro });
  }

  // 5. Chaque section
  for (let i = 0; i < matches.length; i++) {
    const header = matches[i][1];
    const start = (matches[i].index ?? 0) + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index ?? text.length : text.length;
    const rawContent = text.slice(start, end);
    const content = stripMarkdown(rawContent);

    if (!content) continue;

    const { type, title, count } = classifyHeader(header);
    let items: string[] | undefined;
    let urgent: boolean | undefined;

    if (type === 'priorities') {
      items = extractNumberedItems(content);
      // Si la priorite contient "(dont X, Y)", extraire aussi
    } else if (type === 'messages') {
      items = extractDashedNames(content);
    } else if (type === 'emails') {
      urgent = !/rien d'?urgent/i.test(content);
    } else if (type === 'tasks') {
      urgent = true;
      items = extractDashedNames(content);
    }

    sections.push({
      type,
      title,
      content,
      count,
      urgent,
      items: items && items.length > 0 ? items : undefined,
    });
  }

  // 6. Cas particulier : si "messages en attente" est mentionne dans les priorites
  //    mais pas en section dediee, on extrait quand meme les noms cites.
  const prioritiesSection = sections.find((s) => s.type === 'priorities');
  if (prioritiesSection) {
    const namesFromPrio = extractNamesInParens(prioritiesSection.content);
    if (namesFromPrio && !sections.some((s) => s.type === 'messages')) {
      const countMatch = prioritiesSection.content.match(/(\d+)\s+personnes?\s+attendent/i);
      const countNum = countMatch ? parseInt(countMatch[1], 10) : namesFromPrio.length;
      sections.push({
        type: 'messages',
        title: 'Messages en attente',
        content: prioritiesSection.content,
        count: countNum,
        items: namesFromPrio,
      });
    }
  }

  // 7. Ajouter le "Point d'attention" final
  if (attentionContent) {
    sections.push({
      type: 'attention',
      title: "Point d'attention",
      content: attentionContent,
    });
  }

  return sections;
}
