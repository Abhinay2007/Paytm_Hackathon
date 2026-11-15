export function formatRelativeTime(value) {
  if (!value) return 'just now'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return 'just now'
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000))
  if (seconds < 10) return 'now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function formatClock(value) {
  if (!value) return 'now'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'now'
    : new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date)
}

export function formatPercent(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round((value <= 1 ? value * 100 : value) * 10) / 10}%`
}

export function formatRupees(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '₹—'
  return `₹${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(value)}`
}

export function formatDuration(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const remaining = Math.round(seconds % 60)
  return remaining ? `${minutes}m ${remaining}s` : `${minutes}m`
}

export function formatLatency(milliseconds) {
  if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds)) return '—'
  return milliseconds < 1000
    ? `${Math.round(milliseconds)} ms`
    : `${(milliseconds / 1000).toFixed(1)} s`
}
