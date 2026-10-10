const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' })
const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const numberFormat = new Intl.NumberFormat()

export const formatNumber = (n: number) => numberFormat.format(n)

export const formatDate = (iso: string) => dateFormat.format(new Date(iso))

export const formatDateTime = (iso: string) => dateTimeFormat.format(new Date(iso))

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

/** "3 minutes ago", "in 42 seconds", "yesterday". */
export function formatRelative(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000)
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size || unit === 'second') {
      return relativeFormat.format(Math.round(seconds / size), unit)
    }
  }
  return relativeFormat.format(0, 'second')
}
