/** Helpers de formatage temporel partagés par toutes les vues. */

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const diff = Date.now() - t
  const s = Math.floor(diff / 1000)
  if (s < 60) return `il y a ${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `il y a ${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return `il y a ${h}h`
  const d = Math.floor(h / 24)
  if (d < 7) return `il y a ${d}j`
  return new Date(t).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
}

export function formatHoursFromMinutes(min?: number | null): string {
  if (min == null || Number.isNaN(min)) return '—'
  if (min < 60) return `${Math.round(min)} min`
  const h = Math.floor(min / 60)
  const r = Math.round(min % 60)
  return r > 0 ? `${h}h${String(r).padStart(2, '0')}` : `${h}h`
}

export function timeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return '—'
  const diff = Date.now() - new Date(dateStr).getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return "à l'instant"
  if (min < 60) return `il y a ${min} min`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `il y a ${hours}h`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'hier'
  if (days < 7) return `il y a ${days}j`
  const months = Math.floor(days / 30)
  if (months > 0 && months < 12) return `il y a ${months} mois`
  return new Date(dateStr).toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'short',
    year: days > 365 ? 'numeric' : undefined,
  })
}

export function formatDurationMin(minutes: number): string {
  if (!minutes || minutes < 1) return '—'
  if (minutes < 60) return `${Math.round(minutes)} min`
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return m > 0 ? `${h}h ${m}min` : `${h}h`
}

export function formatDurationSec(sec: number | undefined): string {
  if (!sec || sec < 1) return '—'
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m > 0 ? `${h}h ${m}min` : `${h}h`
}
