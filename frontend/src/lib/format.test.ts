import { expect, it } from 'vitest'

import { formatRelative } from '@/lib/format'

const NOW = Date.parse('2026-10-09T12:00:00Z')

it.each([
  ['2026-10-09T11:59:30Z', '30 seconds ago'],
  ['2026-10-09T11:57:00Z', '3 minutes ago'],
  ['2026-10-09T09:00:00Z', '3 hours ago'],
  ['2026-10-08T12:00:00Z', 'yesterday'],
  ['2026-10-09T12:00:42Z', 'in 42 seconds'],
  ['2026-11-01T00:00:00Z', 'in 3 weeks'],
])('%s is %s', (iso, expected) => {
  expect(formatRelative(iso, NOW)).toBe(expected)
})
