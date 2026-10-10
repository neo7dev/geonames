import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, mockFetch, USAGE } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { currentPath, renderApp } from '@/test/render'

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
})

const usageCalls = (fetch: ReturnType<typeof mockFetch>) =>
  fetch.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith('/portal/usage'))

it('asks for 30 days by default and totals them', async () => {
  const fetch = mockFetch(baseRoutes)
  renderApp('/usage')

  expect(await screen.findByText('1,234 in the last 30 days')).toBeInTheDocument()
  expect(usageCalls(fetch)).toContain('/portal/usage?days=30')
  expect(screen.getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true')
})

it('switches the range and keeps it in the URL', async () => {
  const fetch = mockFetch(baseRoutes)
  const router = renderApp('/usage')
  await screen.findByText('1,234 in the last 30 days')

  await userEvent.click(screen.getByRole('button', { name: '7 days' }))

  await waitFor(() => expect(usageCalls(fetch)).toContain('/portal/usage?days=7'))
  expect(currentPath(router)).toBe('/usage?days=7')
  expect(await screen.findByText('1,234 in the last 7 days')).toBeInTheDocument()
})

it("labels the old range's total as such until the new range arrives", async () => {
  let release = () => {}
  const gate = new Promise<void>((resolve) => (release = resolve))
  const respond = mockFetch(baseRoutes)
  vi.stubGlobal('fetch', vi.fn(async (...args: Parameters<typeof respond>) => {
    if (String(args[0]).endsWith('days=7')) await gate
    return respond(...args)
  }))
  renderApp('/usage')
  await screen.findByText('1,234 in the last 30 days')

  await userEvent.click(screen.getByRole('button', { name: '7 days' }))

  expect(screen.getByText('1,234 in the last 30 days')).toBeInTheDocument()
  release()
  expect(await screen.findByText('1,234 in the last 7 days')).toBeInTheDocument()
})

it('ignores a days value the API would refuse', async () => {
  const fetch = mockFetch(baseRoutes)
  renderApp('/usage?days=91')

  await screen.findByText('1,234 in the last 30 days')
  expect(usageCalls(fetch)).not.toContain('/portal/usage?days=91')
})

it('lists every day in a table, newest first, in UTC', async () => {
  mockFetch(baseRoutes)
  renderApp('/usage')
  await userEvent.click(await screen.findByText('Show as a table'))

  const table = screen.getByRole('columnheader', { name: 'Day (UTC)' }).closest('table')!
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows).toHaveLength(USAGE.daily.length)
  expect(rows[0]).toHaveTextContent(/Oct 9, 2026\s*34/)
  expect(rows[2]).toHaveTextContent(/Oct 7, 2026\s*0/)
})

it('shows this month by key, marking revoked keys', async () => {
  mockFetch(baseRoutes)
  renderApp('/usage')

  const laptop = (await screen.findByText('laptop')).closest('tr')!
  expect(laptop).toHaveTextContent('4,000')
  const old = screen.getByText('old').closest('tr')!
  expect(old).toHaveTextContent('Revoked')
  expect(old).toHaveTextContent('100')
})
