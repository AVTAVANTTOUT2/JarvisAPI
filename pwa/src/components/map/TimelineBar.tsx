'use client';

import { useRef, useEffect, useMemo, useState, useCallback } from 'react';
import { ChevronLeft, ChevronRight, MapPin, Navigation } from 'lucide-react';

import type { Visit, Trip } from '@/lib/map-types';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export interface TimelineBarProps {
  visits: Visit[];
  trips: Trip[];
  selectedDate: string | null; // ISO date (YYYY-MM-DD)
  onDateSelect: (isoDate: string) => void;
  loading?: boolean;
}

interface DaySlot {
  iso: string;         // "2026-06-16"
  label: string;       // "Lun 16"
  sublabel: string;    // "Juin"
  isToday: boolean;
  visitCount: number;
  tripCount: number;
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

const DAY_NAMES_SHORT = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'];
const MONTH_NAMES_SHORT = [
  'Janv', 'Fevr', 'Mars', 'Avr', 'Mai', 'Juin',
  'Juil', 'Aout', 'Sept', 'Oct', 'Nov', 'Dec',
];

function buildDaySlots(visits: Visit[], trips: Trip[], daysBack: number): DaySlot[] {
  const now = new Date();
  const todayIso = isoDate(now);
  const slots: DaySlot[] = [];

  // Compteurs par jour
  const visitByDay = new Map<string, number>();
  const tripByDay = new Map<string, number>();
  visits.forEach((v) => {
    const d = isoDate(new Date(v.arrived_at));
    visitByDay.set(d, (visitByDay.get(d) || 0) + 1);
  });
  trips.forEach((t) => {
    const d = isoDate(new Date(t.started_at));
    tripByDay.set(d, (tripByDay.get(d) || 0) + 1);
  });

  for (let i = daysBack - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const iso = isoDate(d);
    slots.push({
      iso,
      label: `${DAY_NAMES_SHORT[d.getDay()]} ${d.getDate()}`,
      sublabel: MONTH_NAMES_SHORT[d.getMonth()],
      isToday: iso === todayIso,
      visitCount: visitByDay.get(iso) || 0,
      tripCount: tripByDay.get(iso) || 0,
    });
  }

  return slots;
}

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// ─────────────────────────────────────────────────────────────
// Composant
// ─────────────────────────────────────────────────────────────

export default function TimelineBar({
  visits,
  trips,
  selectedDate,
  onDateSelect,
  loading,
}: TimelineBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScrolled, setAutoScrolled] = useState(false);

  const daysBack = 14; // 2 semaines de timeline
  const slots = useMemo(() => buildDaySlots(visits, trips, daysBack), [visits, trips]);

  // Scroll automatique vers aujourd'hui au premier rendu
  useEffect(() => {
    if (autoScrolled || loading) return;
    const todayIdx = slots.findIndex((s) => s.isToday);
    if (todayIdx >= 0 && scrollRef.current) {
      const el = scrollRef.current.children[todayIdx] as HTMLElement | undefined;
      if (el) {
        scrollRef.current.scrollTo({
          left: el.offsetLeft - scrollRef.current.clientWidth / 2 + el.clientWidth / 2,
          behavior: 'smooth',
        });
      }
      setAutoScrolled(true);
    }
  }, [slots, autoScrolled, loading]);

  const scrollBy = useCallback((dir: 'left' | 'right') => {
    if (!scrollRef.current) return;
    const amt = scrollRef.current.clientWidth * 0.7;
    scrollRef.current.scrollBy({
      left: dir === 'left' ? -amt : amt,
      behavior: 'smooth',
    });
  }, []);

  return (
    <div className="relative w-full bg-[rgba(10,10,15,0.95)] backdrop-blur-[30px] border-t border-[rgba(255,255,255,0.06)]">
      {/* Fleches de scroll */}
      <button
        type="button"
        onClick={() => scrollBy('left')}
        className="absolute left-0 top-1/2 -translate-y-1/2 z-10 w-7 h-full flex items-center justify-center bg-gradient-to-r from-[rgba(10,10,15,0.95)] to-transparent active:opacity-60"
        aria-label="Jours precedents"
      >
        <ChevronLeft size={16} className="text-[#666]" />
      </button>
      <button
        type="button"
        onClick={() => scrollBy('right')}
        className="absolute right-0 top-1/2 -translate-y-1/2 z-10 w-7 h-full flex items-center justify-center bg-gradient-to-l from-[rgba(10,10,15,0.95)] to-transparent active:opacity-60"
        aria-label="Jours suivants"
      >
        <ChevronRight size={16} className="text-[#666]" />
      </button>

      {/* Slots jours */}
      <div
        ref={scrollRef}
        className="flex gap-0.5 overflow-x-auto scrollbar-none px-2 py-2 snap-x snap-mandatory"
      >
        {loading
          ? Array.from({ length: 7 }).map((_, i) => (
              <div
                key={i}
                className="w-[52px] h-[58px] rounded-xl bg-[rgba(255,255,255,0.03)] animate-pulse flex-shrink-0"
              />
            ))
          : slots.map((slot) => {
              const active = slot.iso === selectedDate;
              const dotCount = slot.visitCount + slot.tripCount;
              return (
                <button
                  key={slot.iso}
                  type="button"
                  onClick={() => onDateSelect(slot.iso)}
                  className={`
                    flex-shrink-0 w-[52px] h-[60px] rounded-xl flex flex-col items-center justify-center gap-0.5
                    snap-center transition-all duration-150 active:scale-95
                    ${active
                      ? 'bg-[#4A9EFF] text-white shadow-[0_0_16px_rgba(74,158,255,0.3)]'
                      : 'bg-[rgba(255,255,255,0.04)] text-[#888] hover:bg-[rgba(255,255,255,0.07)]'
                    }
                  `}
                  aria-label={`${slot.label} ${slot.sublabel} - ${dotCount} activite${dotCount > 1 ? 's' : ''}`}
                  aria-current={active ? 'date' : undefined}
                >
                  <span className="text-[10px] font-semibold leading-none opacity-70">
                    {slot.sublabel}
                  </span>
                  <span className={`text-[13px] font-bold leading-none ${active ? 'text-white' : 'text-[#ccc]'}`}>
                    {slot.label.split(' ')[1]}
                  </span>
                  <span className="text-[10px] leading-none opacity-60">
                    {slot.label.split(' ')[0]}
                  </span>
                  {dotCount > 0 && (
                    <div className="flex gap-0.5 mt-0.5">
                      {Array.from({ length: Math.min(dotCount, 4) }).map((_, i) => (
                        <div
                          key={i}
                          className={`w-1 h-1 rounded-full ${active ? 'bg-white/70' : 'bg-[#4A9EFF]/60'}`}
                        />
                      ))}
                    </div>
                  )}
                </button>
              );
            })}
      </div>

      {/* Compteur du jour selectionne */}
      {selectedDate && (
        <div className="flex items-center justify-center gap-3 px-3 pb-2 text-[11px] text-[#666]">
          <span className="inline-flex items-center gap-1">
            <MapPin size={10} />
            {visits.filter((v) => isoDate(new Date(v.arrived_at)) === selectedDate).length} visites
          </span>
          <span className="inline-flex items-center gap-1">
            <Navigation size={10} />
            {trips.filter((t) => isoDate(new Date(t.started_at)) === selectedDate).length} trajets
          </span>
        </div>
      )}
    </div>
  );
}
