import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import type { AccountEvent } from '@/api/client'
import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, ME, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { currentPath, renderApp } from '@/test/render'

const ADMIN_ME = { ...ME, role: 'admin', tier: 'max', is_admin: true }

let nextId = 1
const event = (changes: Partial<AccountEvent>): AccountEvent => ({
  id: nextId++,
  created_at: '2026-10-01T12:00:00Z',
  user_id: 'id-bob',
  user_email: 'bob@example.com',
  event: 'signed_up',
  details: { role: 'free' },
  actor_id: 'id-bob',
  actor_email: 'bob@example.com',
  source: 'user',
  ...changes,
})

const EVENTS = [
  event({
    event: 'upgraded',
    details: { from_role: 'free', to_role: 'dev', from_tier: 'basic', to_tier: 'pro' },
    actor_id: ME.id,
    actor_email: ME.email,
    source: 'admin',
  }),
  event({
    event: 'downgraded',
    user_id: 'id-amy',
    user_email: 'amy@example.com',
    details: { from_role: 'dev', to_role: 'free', from_tier: 'pro', to_tier: 'basic' },
    actor_id: 'id-other',
    actor_email: 'other-admin@example.com',
    source: 'admin',
  }),
  event({
    event: 'suspended',
    details: { banned_until: '2026-12-01T00:00:00Z' },
    actor_id: null,
    actor_email: null,
    source: 'external',
  }),
  event({}),
]

/** /portal/admin/events, recording the query strings it was asked for. */
function eventsApi(events: AccountEvent[] = EVENTS) {
  const queries: URLSearchParams[] = []
  mockFetch({
    ...baseRoutes,
    '/portal/me': { body: ADMIN_ME },
    '/portal/admin/events': (_init, url) => {
      const params = new URL(url, 'http://test').searchParams
      queries.push(params)
      const user = params.get('user_id')
      const kinds = params.getAll('event')
      const found = events.filter(
        (e) => (!user || e.user_id === user) && (kinds.length === 0 || kinds.includes(e.event)),
      )
      return { body: { events: found, total: found.length } }
    },
  })
  return queries
}

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
})

const rows = async () => (await screen.findAllByRole('row')).slice(1)

describe('activity', () => {
  it('lists what happened, which way plans changed, and who did it', async () => {
    eventsApi()
    renderApp('/admin/activity')

    const [upgrade, downgrade, suspension, signup] = await rows()
    expect(upgrade).toHaveTextContent('bob@example.com')
    expect(upgrade).toHaveTextContent('Upgraded free → dev (basic → pro)')
    expect(within(upgrade).getByText('You')).toBeInTheDocument()
    expect(downgrade).toHaveTextContent('Downgraded dev → free (pro → basic)')
    expect(downgrade).toHaveTextContent('other-admin@example.com')
    expect(suspension).toHaveTextContent('Suspended banned or deleted in Supabase Auth')
    expect(suspension).toHaveTextContent('Supabase Auth or Studio')
    expect(signup).toHaveTextContent('Signed up as free')
    expect(signup).toHaveTextContent('Themselves')
  })

  it('filters by a group of events', async () => {
    const queries = eventsApi()
    renderApp('/admin/activity')
    await rows()

    await userEvent.selectOptions(screen.getByLabelText('Events'), 'Plan changes')

    await waitFor(() => expect(queries.at(-1)!.getAll('event')).toEqual(['upgraded', 'downgraded', 'role_changed']))
    await waitFor(async () => expect(await rows()).toHaveLength(2))
  })

  it("shows one account's history, then everyone's again", async () => {
    const queries = eventsApi()
    const router = renderApp('/admin/activity')
    await rows()

    await userEvent.click(screen.getByRole('button', { name: 'amy@example.com' }))

    expect(await screen.findByText(/History of/)).toHaveTextContent('History of amy@example.com')
    await waitFor(() => expect(queries.at(-1)!.get('user_id')).toBe('id-amy'))
    expect(currentPath(router)).toBe('/admin/activity?user=id-amy&email=amy%40example.com')

    await userEvent.click(screen.getByRole('button', { name: 'Show every account' }))
    await waitFor(() => expect(queries.at(-1)!.get('user_id')).toBeNull())
  })

  it("ignores the search in one account's history, where it is hidden", async () => {
    const queries = eventsApi()
    renderApp('/admin/activity')
    await rows()

    await userEvent.type(screen.getByLabelText('Search by email'), 'other-admin')
    await waitFor(() => expect(queries.at(-1)!.get('q')).toBe('other-admin'))
    await userEvent.click(screen.getByRole('button', { name: 'amy@example.com' }))

    await waitFor(() => expect(queries.at(-1)!.get('user_id')).toBe('id-amy'))
    expect(queries.at(-1)!.get('q')).toBeFalsy()
    expect(screen.queryByLabelText('Search by email')).not.toBeInTheDocument()
  })

  it('opens from a user on the Users tab', async () => {
    mockFetch({
      ...baseRoutes,
      '/portal/me': { body: ADMIN_ME },
      '/portal/admin/roles': { body: [] },
      '/portal/admin/users': {
        body: {
          users: [
            {
              id: 'id-bob',
              email: 'bob@example.com',
              display_name: null,
              role: 'free',
              tier: 'basic',
              is_admin: false,
              is_active: true,
              is_deleted: false,
              auth_suspended: false,
              active_keys: 0,
              created_at: '2026-10-01T12:00:00Z',
            },
          ],
          total: 1,
        },
      },
      '/portal/admin/events': { body: { events: [], total: 0 } },
    })
    const router = renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'History of bob@example.com' }))

    expect(currentPath(router)).toBe('/admin/activity?user=id-bob&email=bob%40example.com')
    expect(await screen.findByText('No events found')).toBeInTheDocument()
  })

  it('is not there for non-admins', async () => {
    mockFetch(baseRoutes)
    renderApp('/admin/activity')

    expect(await screen.findByText(/not found/i)).toBeInTheDocument()
  })
})
