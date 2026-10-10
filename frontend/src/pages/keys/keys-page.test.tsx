import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, ME, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { renderApp } from '@/test/render'

const KEY = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'laptop',
  prefix: 'gn_abcd1234',
  created_at: '2026-10-01T12:00:00Z',
  last_used_at: null,
}
const SECRET = 'gn_abcd1234-the-rest-of-a-secret-key'

/**
 * The portal as a tiny in-memory server: keys can be created (up to
 * `maxKeys`) and revoked, and /portal/me counts them.
 */
function portal({ keys = [] as (typeof KEY)[], maxKeys = 1 as number | null } = {}) {
  let list = [...keys]
  return mockFetch({
    ...baseRoutes,
    '/portal/me': () => ({ body: { ...ME, active_keys: list.length, limits: { ...ME.limits, max_keys: maxKeys } } }),
    '/portal/keys': (init) => {
      if (init?.method === 'POST') {
        if (maxKeys !== null && list.length >= maxKeys) {
          return { status: 409, body: { detail: `The basic tier allows ${maxKeys} active API key(s); revoke one to create another` } }
        }
        const { name } = JSON.parse(String(init.body))
        const created = { ...KEY, id: `id-${list.length}`, name }
        list = [created, ...list]
        return { status: 201, body: { ...created, key: SECRET } }
      }
      return { body: list }
    },
    [`/portal/keys/${KEY.id}`]: () => {
      list = list.filter((k) => k.id !== KEY.id)
      return { status: 204 }
    },
  })
}

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
})

describe('list', () => {
  it('shows each key by name and prefix, never the key itself', async () => {
    portal({ keys: [KEY] })
    renderApp('/keys')

    const row = (await screen.findByText('laptop')).closest('tr')!
    expect(within(row).getByText('gn_abcd1234…')).toBeInTheDocument()
    expect(within(row).getByText('Never')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(SECRET)
  })

  it('says when there are no keys', async () => {
    portal()
    renderApp('/keys')

    expect(await screen.findByText('No API keys yet')).toBeInTheDocument()
  })
})

describe('create', () => {
  it('shows the new key once, and never again after closing', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    portal()
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Create API key' }))
    await userEvent.type(screen.getByLabelText('Name'), '  ci  ')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))

    expect(await screen.findByText('You will not see this key again')).toBeInTheDocument()
    expect(screen.getByLabelText('Your new API key')).toHaveValue(SECRET)
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith(SECRET)
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Done' }))

    expect(await screen.findByText('ci')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(SECRET)
  })

  it('sends the trimmed name', async () => {
    const fetch = portal()
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Create API key' }))
    await userEvent.type(screen.getByLabelText('Name'), '  ci  ')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    await screen.findByText('You will not see this key again')

    const post = fetch.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST')!
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ name: 'ci' })
  })

  it('does not close on Escape while the key is shown', async () => {
    portal()
    renderApp('/keys')
    await userEvent.click(await screen.findByRole('button', { name: 'Create API key' }))
    await userEvent.type(screen.getByLabelText('Name'), 'ci')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    await screen.findByText('You will not see this key again')

    await userEvent.keyboard('{Escape}')

    expect(screen.getByLabelText('Your new API key')).toHaveValue(SECRET)
  })

  it('refuses a name the API would refuse, before sending it', async () => {
    const fetch = portal()
    renderApp('/keys')
    await userEvent.click(await screen.findByRole('button', { name: 'Create API key' }))

    await userEvent.type(screen.getByLabelText('Name'), 'x'.repeat(65))
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))

    expect(screen.getByText('Use at most 64 characters.')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(false)
  })

  it("is disabled at the tier's key limit, with the reason", async () => {
    portal({ keys: [KEY], maxKeys: 1 })
    renderApp('/keys')

    expect(await screen.findByText(/The basic tier allows 1 active API key\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create API key' })).toBeDisabled()
  })

  it('stays enabled for an unlimited tier', async () => {
    portal({ keys: [KEY], maxKeys: null })
    renderApp('/keys')

    await screen.findByText('laptop')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create API key' })).toBeEnabled())
  })

  it("shows the API's message when it refuses the key", async () => {
    // The page thinks there is room (stale count), the API knows better.
    mockFetch({
      ...baseRoutes,
      '/portal/keys': (init) =>
        init?.method === 'POST'
          ? { status: 409, body: { detail: 'The basic tier allows 1 active API key(s); revoke one to create another' } }
          : { body: [] },
    })
    renderApp('/keys')
    await userEvent.click(await screen.findByRole('button', { name: 'Create API key' }))
    await userEvent.type(screen.getByLabelText('Name'), 'ci')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))

    expect(await screen.findByText(/revoke one to create another/)).toBeInTheDocument()
  })
})

describe('revoke', () => {
  it('asks first, then revokes and refreshes the list', async () => {
    const fetch = portal({ keys: [KEY] })
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Revoke laptop' }))
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Revoke "laptop"?')
    expect(fetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'DELETE')).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: 'Revoke key' }))

    expect(await screen.findByText('No API keys yet')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('does nothing when cancelled', async () => {
    const fetch = portal({ keys: [KEY] })
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Revoke laptop' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByText('laptop')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'DELETE')).toBe(false)
  })
})
