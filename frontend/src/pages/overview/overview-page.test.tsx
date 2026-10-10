import { screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, ME, mockFetch, USAGE } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { renderApp } from '@/test/render'

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
})

const meter = (name: string) => screen.findByRole('meter', { name: `${name}: requests used` })

it('shows the current windows against the limits', async () => {
  const fetch = mockFetch(baseRoutes)
  renderApp('/')

  expect(await meter('This minute')).toHaveAttribute('aria-valuetext', '3 of 10')
  expect(await meter('This hour')).toHaveAttribute('aria-valuetext', '42 of 100')
  expect(await meter('This month (UTC)')).toHaveAttribute('aria-valuetext', '4,100 of 5,000')
  // The history is not shown here, so it asks for the least of it
  expect(fetch.mock.calls.map(([url]) => String(url))).toContain('/portal/usage?days=1')
})

it('turns the meter amber near the limit and red at it', async () => {
  const at = (used: number) => ({ ...USAGE.current.minute, used })
  mockFetch({
    ...baseRoutes,
    '/portal/usage': { body: { ...USAGE, current: { minute: at(10), hour: at(8), month: at(1) } } },
  })
  renderApp('/')

  expect((await meter('This minute')).firstElementChild).toHaveClass('bg-destructive')
  expect((await meter('This hour')).firstElementChild).toHaveClass('bg-meter-warn')
  expect((await meter('This month (UTC)')).firstElementChild).toHaveClass('bg-chart-1')
})

it('says Unlimited for the max tier, with no meter', async () => {
  const unlimited = { ...ME, tier: 'max', role: 'admin', limits: { per_minute: null, per_hour: null, per_month: null, max_keys: null } }
  const window_ = { ...USAGE.current.minute, limit: null }
  mockFetch({
    ...baseRoutes,
    '/portal/me': { body: unlimited },
    '/portal/usage': { body: { ...USAGE, tier: 'max', current: { minute: window_, hour: window_, month: window_ } } },
  })
  renderApp('/')

  expect(await screen.findByText('Per month')).toBeInTheDocument()
  expect(screen.getAllByText('Unlimited')).toHaveLength(3)
  expect(screen.getAllByText('requests, unlimited')).toHaveLength(3)
  expect(screen.queryByRole('meter')).not.toBeInTheDocument()
})

it('shows a dash and a note when live counts are unavailable', async () => {
  mockFetch({
    ...baseRoutes,
    '/portal/usage': {
      body: { ...USAGE, live: false, current: { ...USAGE.current, minute: { ...USAGE.current.minute, used: null } } },
    },
  })
  renderApp('/')

  expect(await screen.findByText(/Live counts are unavailable right now/)).toBeInTheDocument()
  expect(await meter('This minute')).toHaveAttribute('aria-valuetext', 'Unknown')
  expect(screen.getByText('—')).toBeInTheDocument()
})

it('gives a curl example against this server', async () => {
  mockFetch(baseRoutes)
  renderApp('/')

  expect(await screen.findByText(new RegExp(`${window.location.origin}/cities15000/\\?limit=5`))).toBeInTheDocument()
  expect(screen.getByText(/X-API-Key: YOUR_API_KEY/)).toBeInTheDocument()
})

it('refetches the usage every 10 seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetch = mockFetch(baseRoutes)
  renderApp('/')
  await meter('This minute')
  const calls = () => fetch.mock.calls.filter(([url]) => String(url).startsWith('/portal/usage')).length
  const before = calls()

  await vi.advanceTimersByTimeAsync(10_000)

  await waitFor(() => expect(calls()).toBe(before + 1))
  vi.useRealTimers()
})
