import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  X,
  Clock,
  MapPin,
  Calendar as CalendarIcon,
} from 'lucide-react';
import {
  startOfMonth,
  endOfMonth,
  startOfWeek,
  endOfWeek,
  addMonths,
  subMonths,
  addWeeks,
  subWeeks,
  eachDayOfInterval,
  format,
  isSameMonth,
  isToday,
  parseISO,
  setHours,
  setMinutes,
  startOfDay,
  differenceInMinutes,
  addHours,
} from 'date-fns';
import { fr } from 'date-fns/locale';
import { api } from '@unified/lib/api';

interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  location: string;
  notes: string;
  calendar: string;
}

type ViewMode = 'month' | 'week';

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const HOUR_HEIGHT = 60;

export function CalendarView() {
  const [viewMode, setViewMode] = useState<ViewMode>('month');
  const [currentDate, setCurrentDate] = useState(new Date());
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [selectedDay, setSelectedDay] = useState<Date | null>(null);

  const fetchEvents = useCallback(async (date: Date, mode: ViewMode) => {
    setLoading(true);
    try {
      let start: Date;
      let end: Date;
      if (mode === 'month') {
        const monthStart = startOfMonth(date);
        const monthEnd = endOfMonth(date);
        start = startOfWeek(monthStart, { weekStartsOn: 1 });
        end = endOfWeek(monthEnd, { weekStartsOn: 1 });
      } else {
        start = startOfWeek(date, { weekStartsOn: 1 });
        end = endOfWeek(date, { weekStartsOn: 1 });
      }
      const res = await api.getCalendarEvents(start.toISOString(), end.toISOString());
      setEvents((res as { events: CalendarEvent[] }).events || []);
    } catch (e) {
      console.error('[Calendar] fetch error', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEvents(currentDate, viewMode);
  }, [currentDate, viewMode, fetchEvents]);

  const navigatePrev = () => {
    setCurrentDate((d) => (viewMode === 'month' ? subMonths(d, 1) : subWeeks(d, 1)));
  };
  const navigateNext = () => {
    setCurrentDate((d) => (viewMode === 'month' ? addMonths(d, 1) : addWeeks(d, 1)));
  };
  const goToday = () => setCurrentDate(new Date());

  const handleEventCreated = () => {
    setShowModal(false);
    fetchEvents(currentDate, viewMode);
  };

  return (
    <div className="flex flex-col h-full min-h-0 p-6 gap-6">
      {/* Header */}
      <header className="flex items-center justify-between animate-slide-up">
        <div>
          <h1 className="text-2xl font-mono font-bold tracking-tight">CALENDAR SYSTEM</h1>
          <p className="text-sm text-muted-foreground font-mono mt-1">
            {format(currentDate, viewMode === 'month' ? 'MMMM yyyy' : "'Semaine du' d MMMM yyyy", { locale: fr })}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* View toggle */}
          <div className="flex rounded-lg border border-white/10 overflow-hidden">
            <button
              onClick={() => setViewMode('month')}
              className={`px-4 py-2 text-xs font-mono transition-colors ${
                viewMode === 'month'
                  ? 'bg-white text-black'
                  : 'bg-white/5 text-muted-foreground hover:bg-white/10'
              }`}
            >
              MOIS
            </button>
            <button
              onClick={() => setViewMode('week')}
              className={`px-4 py-2 text-xs font-mono transition-colors ${
                viewMode === 'week'
                  ? 'bg-white text-black'
                  : 'bg-white/5 text-muted-foreground hover:bg-white/10'
              }`}
            >
              SEMAINE
            </button>
          </div>
          {/* Navigation */}
          <div className="flex items-center gap-1">
            <button
              onClick={navigatePrev}
              className="p-2 rounded-lg border border-white/10 hover:bg-white/10 transition-colors"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              onClick={goToday}
              className="px-3 py-2 rounded-lg border border-white/10 hover:bg-white/10 transition-colors text-xs font-mono"
            >
              Aujourd&apos;hui
            </button>
            <button
              onClick={navigateNext}
              className="p-2 rounded-lg border border-white/10 hover:bg-white/10 transition-colors"
            >
              <ChevronRight size={16} />
            </button>
          </div>
          {/* New event */}
          <button
            onClick={() => { setSelectedDay(null); setShowModal(true); }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black font-mono text-xs hover:bg-white/90 transition-colors"
          >
            <Plus size={14} />
            Nouvel Événement
          </button>
        </div>
      </header>

      {/* Loading indicator */}
      {loading && (
        <div className="absolute top-4 right-4 z-50">
          <div className="w-2 h-2 rounded-full bg-white animate-pulse" />
        </div>
      )}

      {/* Calendar body */}
      <div className="flex-1 min-h-0 animate-fade-in">
        {viewMode === 'month' ? (
          <MonthView
            currentDate={currentDate}
            events={events}
            onDayClick={(d) => { setSelectedDay(d); setShowModal(true); }}
          />
        ) : (
          <WeekView currentDate={currentDate} events={events} />
        )}
      </div>

      {/* Modal */}
      {showModal && (
        <EventModal
          defaultDate={selectedDay}
          onClose={() => setShowModal(false)}
          onCreated={handleEventCreated}
        />
      )}
    </div>
  );
}

/* ─── Month View ─────────────────────────────────────────────── */

function MonthView({
  currentDate,
  events,
  onDayClick,
}: {
  currentDate: Date;
  events: CalendarEvent[];
  onDayClick: (d: Date) => void;
}) {
  const monthStart = startOfMonth(currentDate);
  const monthEnd = endOfMonth(currentDate);
  const calStart = startOfWeek(monthStart, { weekStartsOn: 1 });
  const calEnd = endOfWeek(monthEnd, { weekStartsOn: 1 });
  const days = eachDayOfInterval({ start: calStart, end: calEnd });

  const dayNames = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];

  const eventsByDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const ev of events) {
      if (!ev.start) continue;
      const key = format(parseISO(ev.start), 'yyyy-MM-dd');
      const arr = map.get(key) || [];
      arr.push(ev);
      map.set(key, arr);
    }
    return map;
  }, [events]);

  return (
    <div className="h-full flex flex-col">
      {/* Day headers */}
      <div className="grid grid-cols-7 gap-px mb-2">
        {dayNames.map((d) => (
          <div key={d} className="text-center text-xs font-mono text-muted-foreground py-2">
            {d}
          </div>
        ))}
      </div>
      {/* Day cells */}
      <div className="grid grid-cols-7 gap-px flex-1 auto-rows-fr">
        {days.map((day) => {
          const key = format(day, 'yyyy-MM-dd');
          const dayEvents = eventsByDay.get(key) || [];
          const inMonth = isSameMonth(day, currentDate);
          const today = isToday(day);

          return (
            <div
              key={key}
              onClick={() => onDayClick(day)}
              className={`glass-panel rounded-lg p-2 min-h-[80px] cursor-pointer transition-all hover:border-white/30 group ${
                !inMonth ? 'opacity-40' : ''
              } ${today ? 'border-white/40 bg-white/8' : ''}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span
                  className={`text-xs font-mono ${
                    today
                      ? 'bg-white text-black w-6 h-6 rounded-full flex items-center justify-center font-bold'
                      : 'text-muted-foreground'
                  }`}
                >
                  {format(day, 'd')}
                </span>
                {dayEvents.length > 0 && (
                  <span className="text-[10px] font-mono text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
                    {dayEvents.length}
                  </span>
                )}
              </div>
              <div className="space-y-0.5 overflow-hidden">
                {dayEvents.slice(0, 3).map((ev, i) => (
                  <EventBadge key={i} event={ev} />
                ))}
                {dayEvents.length > 3 && (
                  <div className="text-[10px] font-mono text-muted-foreground">
                    +{dayEvents.length - 3} autres
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EventBadge({ event }: { event: CalendarEvent }) {
  const time = event.start ? format(parseISO(event.start), 'HH:mm') : '';
  return (
    <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/10 border border-white/5 truncate">
      <span className="text-[10px] font-mono text-muted-foreground shrink-0">{time}</span>
      <span className="text-[10px] font-mono truncate">{event.title}</span>
    </div>
  );
}

/* ─── Week View ──────────────────────────────────────────────── */

function WeekView({
  currentDate,
  events,
}: {
  currentDate: Date;
  events: CalendarEvent[];
}) {
  const weekStart = startOfWeek(currentDate, { weekStartsOn: 1 });
  const weekDays = eachDayOfInterval({
    start: weekStart,
    end: endOfWeek(currentDate, { weekStartsOn: 1 }),
  });

  const eventsByDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const ev of events) {
      if (!ev.start) continue;
      const key = format(parseISO(ev.start), 'yyyy-MM-dd');
      const arr = map.get(key) || [];
      arr.push(ev);
      map.set(key, arr);
    }
    return map;
  }, [events]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Day headers */}
      <div className="grid grid-cols-[60px_repeat(7,1fr)] gap-px mb-1 shrink-0">
        <div />
        {weekDays.map((day) => (
          <div
            key={day.toISOString()}
            className={`text-center py-2 rounded-lg ${
              isToday(day) ? 'bg-white/10 border border-white/20' : ''
            }`}
          >
            <div className="text-[10px] font-mono text-muted-foreground uppercase">
              {format(day, 'EEE', { locale: fr })}
            </div>
            <div className={`text-sm font-mono ${isToday(day) ? 'font-bold' : ''}`}>
              {format(day, 'd')}
            </div>
          </div>
        ))}
      </div>
      {/* Time grid */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="grid grid-cols-[60px_repeat(7,1fr)] gap-px relative" style={{ height: `${HOURS.length * HOUR_HEIGHT}px` }}>
          {/* Hour labels */}
          <div className="relative">
            {HOURS.map((h) => (
              <div
                key={h}
                className="absolute left-0 right-0 text-[10px] font-mono text-muted-foreground text-right pr-2"
                style={{ top: `${h * HOUR_HEIGHT}px`, height: `${HOUR_HEIGHT}px` }}
              >
                {String(h).padStart(2, '0')}:00
              </div>
            ))}
          </div>
          {/* Day columns */}
          {weekDays.map((day) => {
            const key = format(day, 'yyyy-MM-dd');
            const dayEvents = eventsByDay.get(key) || [];
            return (
              <div key={key} className="relative border-l border-white/5">
                {/* Hour lines */}
                {HOURS.map((h) => (
                  <div
                    key={h}
                    className="absolute left-0 right-0 border-t border-white/5"
                    style={{ top: `${h * HOUR_HEIGHT}px` }}
                  />
                ))}
                {/* Events */}
                {dayEvents.map((ev, i) => (
                  <WeekEventBlock key={i} event={ev} day={day} />
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function WeekEventBlock({ event, day }: { event: CalendarEvent; day: Date }) {
  const startDt = event.start ? parseISO(event.start) : null;
  const endDt = event.end ? parseISO(event.end) : null;
  if (!startDt) return null;

  const dayStart = startOfDay(day);
  const effectiveEnd = endDt || addHours(startDt, 1);
  const topMinutes = differenceInMinutes(startDt, dayStart);
  const durationMinutes = Math.max(differenceInMinutes(effectiveEnd, startDt), 30);

  const top = (topMinutes / 60) * HOUR_HEIGHT;
  const height = Math.max((durationMinutes / 60) * HOUR_HEIGHT, 24);

  return (
    <div
      className="absolute left-0.5 right-0.5 rounded-md bg-white/15 border border-white/20 px-1.5 py-0.5 overflow-hidden hover:bg-white/25 transition-colors cursor-pointer group"
      style={{ top: `${top}px`, height: `${height}px` }}
      title={`${event.title}\n${format(startDt, 'HH:mm')} - ${endDt ? format(endDt, 'HH:mm') : ''}\n${event.location || ''}`}
    >
      <div className="text-[10px] font-mono font-medium truncate">{event.title}</div>
      {height > 30 && (
        <div className="text-[9px] font-mono text-muted-foreground truncate">
          {format(startDt, 'HH:mm')} - {endDt ? format(endDt, 'HH:mm') : ''}
        </div>
      )}
      {height > 50 && event.location && (
        <div className="text-[9px] font-mono text-muted-foreground truncate opacity-0 group-hover:opacity-100 transition-opacity">
          {event.location}
        </div>
      )}
    </div>
  );
}

/* ─── Event Creation Modal ───────────────────────────────────── */

function EventModal({
  defaultDate,
  onClose,
  onCreated,
}: {
  defaultDate: Date | null;
  onClose: () => void;
  onCreated: () => void;
}) {
  const now = defaultDate || new Date();
  const defaultStart = format(setMinutes(setHours(now, new Date().getHours() + 1), 0), "yyyy-MM-dd'T'HH:mm");
  const defaultEnd = format(setMinutes(setHours(now, new Date().getHours() + 2), 0), "yyyy-MM-dd'T'HH:mm");

  const [title, setTitle] = useState('');
  const [start, setStart] = useState(defaultStart);
  const [end, setEnd] = useState(defaultEnd);
  const [location, setLocation] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !start || !end) {
      setError('Titre, début et fin sont requis.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await api.createCalendarEvent({ title: title.trim(), start, end, location, notes });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur lors de la création');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="glass-panel rounded-2xl w-full max-w-md p-6 border border-white/15 animate-scale-in">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-mono font-bold">Nouvel Événement</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 transition-colors">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Title */}
          <div>
            <label className="text-xs font-mono text-muted-foreground block mb-1.5">Titre</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Réunion, cours, rendez-vous..."
              className="w-full px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:border-white/30 transition-colors"
              autoFocus
            />
          </div>
          {/* Start / End */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-mono text-muted-foreground block mb-1.5 flex items-center gap-1">
                <Clock size={10} /> Début
              </label>
              <input
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                className="w-full px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono focus:outline-none focus:border-white/30 transition-colors"
              />
            </div>
            <div>
              <label className="text-xs font-mono text-muted-foreground block mb-1.5 flex items-center gap-1">
                <Clock size={10} /> Fin
              </label>
              <input
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                className="w-full px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono focus:outline-none focus:border-white/30 transition-colors"
              />
            </div>
          </div>
          {/* Location */}
          <div>
            <label className="text-xs font-mono text-muted-foreground block mb-1.5 flex items-center gap-1">
              <MapPin size={10} /> Lieu
            </label>
            <input
              type="text"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Optionnel"
              className="w-full px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:border-white/30 transition-colors"
            />
          </div>
          {/* Notes */}
          <div>
            <label className="text-xs font-mono text-muted-foreground block mb-1.5 flex items-center gap-1">
              <CalendarIcon size={10} /> Notes
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optionnel"
              rows={3}
              className="w-full px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono placeholder:text-muted-foreground focus:outline-none focus:border-white/30 transition-colors resize-none"
            />
          </div>
          {/* Error */}
          {error && (
            <div className="text-xs font-mono text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
          {/* Submit */}
          <button
            type="submit"
            disabled={submitting}
            className="w-full py-3 rounded-lg bg-white text-black font-mono text-sm font-medium hover:bg-white/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? 'Création...' : 'Créer l\'événement'}
          </button>
        </form>
      </div>
    </div>
  );
}
