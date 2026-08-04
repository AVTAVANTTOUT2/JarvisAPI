/** Helpers de parsing et de formatage temporel partagés par toutes les vues. */

/**
 * Parse les dates civiles et les timestamps du backend sans dépendre du moteur JS.
 *
 * - une date seule reste une date civile locale ;
 * - un timestamp avec offset conserve son instant ;
 * - un timestamp SQLite sans offset suit le contrat backend et représente UTC.
 */
export function parseBackendTimestamp(value: string | number | Date): number {
  if (typeof value === 'number') return Number.isFinite(value) ? value : Number.NaN
  if (value instanceof Date) return value.getTime()
  const trimmed = value.trim()
  if (!trimmed) return Number.NaN

  const calendarDate = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (calendarDate) {
    const [, year, month, day] = calendarDate
    return new Date(Number(year), Number(month) - 1, Number(day)).getTime()
  }
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(trimmed)) return Date.parse(trimmed)
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(trimmed)) {
    return Date.parse(`${trimmed.replace(' ', 'T')}Z`)
  }
  return Date.parse(trimmed)
}

export function formatRelativeTime(
  value?: string | null,
  nowMs: number = Date.now(),
): string {
  if (!value) return '—'
  const timestamp = parseBackendTimestamp(value)
  if (Number.isNaN(timestamp)) return '—'
  const diffMs = nowMs - timestamp
  const future = diffMs < 0
  const minutes = Math.floor(Math.abs(diffMs) / 60_000)
  if (minutes < 1) return "à l'instant"
  if (minutes < 60) return future ? `dans ${minutes} min` : `il y a ${minutes} min`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return future ? `dans ${hours}h` : `il y a ${hours}h`

  const days = Math.floor(hours / 24)
  if (days === 1) return future ? 'demain' : 'hier'
  if (days < 7) return future ? `dans ${days}j` : `il y a ${days}j`

  const months = Math.floor(days / 30)
  if (months > 0 && months < 12) {
    return future ? `dans ${months} mois` : `il y a ${months} mois`
  }
  return new Date(timestamp).toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'short',
    year: days > 365 ? 'numeric' : undefined,
  })
}

export function formatHoursFromMinutes(min?: number | null): string {
  if (min == null || Number.isNaN(min)) return '—'
  if (min < 60) return `${Math.round(min)} min`
  const hours = Math.floor(min / 60)
  const remainder = Math.round(min % 60)
  return remainder > 0
    ? `${hours}h${String(remainder).padStart(2, '0')}`
    : `${hours}h`
}

export function formatDurationMin(minutes: number): string {
  if (!minutes || minutes < 1) return '—'
  if (minutes < 60) return `${Math.round(minutes)} min`
  const hours = Math.floor(minutes / 60)
  const remainder = Math.round(minutes % 60)
  return remainder > 0 ? `${hours}h ${remainder}min` : `${hours}h`
}

export function formatDurationSec(seconds: number | undefined): string {
  if (!seconds || seconds < 1) return '—'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder > 0 ? `${hours}h ${remainder}min` : `${hours}h`
}
