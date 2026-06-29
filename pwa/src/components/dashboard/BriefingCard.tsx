'use client';

import type { BriefingSection } from '@/lib/briefing-parser';
import { COLOR_CLASSES, COLOR_HEX, SECTION_CONFIG } from '@/lib/section-icons';

/** Card visuelle pour une section du briefing matin/soir.
 *
 * Rendu adapte au type :
 *  - priorities : liste numerotee
 *  - messages   : pills avec noms + counter
 *  - weather    : compact horizontal
 *  - emails     : counter + badge urgent eventuel
 *  - intro      : pas de header, texte simple
 *  - attention  : ton violet, encadre particulier
 */
export function BriefingCard({ section }: { section: BriefingSection }) {
  const config = SECTION_CONFIG[section.type];
  const Icon = config.icon;
  const iconCls = COLOR_CLASSES[config.color];

  // --- Intro : pas de header, texte direct ---
  if (section.type === 'intro') {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
        <p className="text-[13px] text-[#bbb] leading-relaxed whitespace-pre-line">
          {section.content}
        </p>
      </div>
    );
  }

  // --- Point d'attention : encadre violet ---
  if (section.type === 'attention') {
    return (
      <div className="rounded-[20px] bg-[rgba(156,89,255,0.06)] border border-[rgba(156,89,255,0.18)] p-4 space-y-2">
        <div className="flex items-center gap-2.5">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconCls}`}>
            <Icon size={16} />
          </div>
          <span className="text-[14px] font-semibold text-white">{section.title}</span>
        </div>
        <p className="text-[13px] text-[#bbb] leading-relaxed whitespace-pre-line pl-[42px]">
          {section.content}
        </p>
      </div>
    );
  }

  // --- Priorities : liste numerotee ---
  if (section.type === 'priorities' && section.items && section.items.length > 0) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4 space-y-3">
        <div className="flex items-center gap-2.5">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconCls}`}>
            <Icon size={16} />
          </div>
          <span className="text-[14px] font-semibold text-white">{section.title}</span>
        </div>
        <ol className="space-y-2.5">
          {section.items.map((item, i) => (
            <li key={i} className="flex gap-3 items-start">
              <span
                className="text-[12px] font-bold mt-0.5 w-6 text-right flex-shrink-0 tabular-nums"
                style={{ color: COLOR_HEX[config.color] }}
              >
                {i + 1}.
              </span>
              <span className="text-[13px] text-[#bbb] leading-relaxed flex-1">{item}</span>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  // --- Messages en attente : pills de noms + counter ---
  if (section.type === 'messages' && section.items && section.items.length > 0) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,214,10,0.04)] border border-[rgba(255,214,10,0.12)] p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconCls}`}>
              <Icon size={16} />
            </div>
            <span className="text-[14px] font-semibold text-white">{section.title}</span>
          </div>
          {section.count !== undefined && (
            <span className="text-[22px] font-bold text-[#FFD60A] tabular-nums leading-none">
              {section.count}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {section.items.map((name, i) => (
            <span
              key={i}
              className="text-[12px] px-2.5 py-1 rounded-full bg-[rgba(255,255,255,0.06)] text-[#ccc] border border-[rgba(255,255,255,0.06)]"
            >
              {name}
            </span>
          ))}
        </div>
      </div>
    );
  }

  // --- Weather : compact horizontal ---
  if (section.type === 'weather') {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
        <div className="flex items-center gap-2.5">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconCls}`}>
            <Icon size={16} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[14px] font-semibold text-white">{section.title}</div>
            <p className="text-[12px] text-[#888] mt-0.5 leading-relaxed">{section.content}</p>
          </div>
        </div>
      </div>
    );
  }

  // --- Emails : counter eventuel + texte ---
  if (section.type === 'emails') {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4 space-y-2">
        <div className="flex items-center gap-2.5">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconCls}`}>
            <Icon size={16} />
          </div>
          <span className="text-[14px] font-semibold text-white flex-1">{section.title}</span>
          {section.count !== undefined && (
            <span className="text-[20px] font-bold text-[#4A9EFF] tabular-nums leading-none">
              {section.count}
            </span>
          )}
          {section.urgent && (
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-[rgba(255,69,58,0.12)] text-[#FF453A] border border-[rgba(255,69,58,0.2)]">
              URGENT
            </span>
          )}
        </div>
        <p className="text-[13px] text-[#bbb] leading-relaxed whitespace-pre-line pl-[42px]">
          {section.content}
        </p>
      </div>
    );
  }

  // --- Default : card standard avec icone + titre + contenu ---
  return (
    <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4 space-y-2">
      <div className="flex items-center gap-2.5">
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${iconCls}`}>
          <Icon size={16} />
        </div>
        <span className="text-[14px] font-semibold text-white flex-1">{section.title}</span>
        {section.count !== undefined && (
          <span
            className="text-[20px] font-bold tabular-nums leading-none"
            style={{ color: section.urgent ? COLOR_HEX.red : COLOR_HEX[config.color] }}
          >
            {section.count}
          </span>
        )}
      </div>
      <p className="text-[13px] text-[#bbb] leading-relaxed whitespace-pre-line pl-[42px]">
        {section.content}
      </p>
    </div>
  );
}
