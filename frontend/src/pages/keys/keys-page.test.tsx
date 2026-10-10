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
const ROLLED_SECRET = 'gn_new99999-the-rest-of-a-rolled-key'

/**
 * The portal as a tiny in-memory server: keys can be created (up to
 * `maxKeys`), revoked and rolled, and /portal/me counts them.
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
    [`/portal/keys/${KEY.id}/roll`]: () => {
      const rolled = { ...KEY, id: 'rolled', prefix: 'gn_new99999' }
      list = [rolled, ...list.filter((k) => k.id !== KEY.id)]
      return { status: 201, body: { ...rolled, key: ROLLED_SECRET } }
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

describe('profile', () => {
  it('says why Create is disabled when the profile fails to load', async () => {
    mockFetch({
      ...baseRoutes,
      '/portal/keys': { body: [KEY] },
      '/portal/me': { status: 500, body: { detail: 'Profile unavailable' } },
    })
    renderApp('/keys')

    expect(await screen.findByText('Profile unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create API key' })).toBeDisabled()
  })
})

describe('roll', () => {
  const isRoll = ([url, init]: unknown[]) =>
    String(url).endsWith('/roll') && (init as RequestInit | undefined)?.method === 'POST'

  it('asks first, then shows the new key once and lists it instead of the old one', async () => {
    const fetch = portal({ keys: [KEY], maxKeys: 1 })
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Roll laptop' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Roll "laptop"?')
    expect(fetch.mock.calls.some(isRoll)).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: 'Roll key' }))

    expect(await screen.findByText('You will not see this key again')).toBeInTheDocument()
    expect(screen.getByLabelText('Your new API key')).toHaveValue(ROLLED_SECRET)
    await userEvent.keyboard('{Escape}')
    expect(screen.getByLabelText('Your new API key')).toHaveValue(ROLLED_SECRET)

    await userEvent.click(screen.getByRole('button', { name: 'Done' }))

    expect(await screen.findByText('gn_new99999…')).toBeInTheDocument()
    expect(screen.queryByText('gn_abcd1234…')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(ROLLED_SECRET)
  })

  it('does nothing when cancelled', async () => {
    const fetch = portal({ keys: [KEY] })
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Roll laptop' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('gn_abcd1234…')).toBeInTheDocument()
    expect(fetch.mock.calls.some(isRoll)).toBe(false)
  })

  it('stays open while the roll is in flight, so the new key is not lost', async () => {
    const respond = portal({ keys: [KEY] })
    let release = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    vi.stubGlobal('fetch', vi.fn(async (...args: Parameters<typeof respond>) => {
      if (isRoll(args)) await gate
      return respond(...args)
    }))
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Roll laptop' }))
    await userEvent.click(screen.getByRole('button', { name: 'Roll key' }))
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    release()
    expect(await screen.findByLabelText('Your new API key')).toHaveValue(ROLLED_SECRET)
  })

  it("shows the API's message when the roll fails", async () => {
    mockFetch({
      ...baseRoutes,
      '/portal/keys': () => ({ body: [KEY] }),
      [`/portal/keys/${KEY.id}/roll`]: () => ({ status: 404, body: { detail: `API key '${KEY.id}' not found` } }),
    })
    renderApp('/keys')

    await userEvent.click(await screen.findByRole('button', { name: 'Roll laptop' }))
    await userEvent.click(screen.getByRole('button', { name: 'Roll key' }))

    expect(await screen.findByText(/not found/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Your new API key')).not.toBeInTheDocument()
  })
})
