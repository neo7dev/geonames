import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import type { AdminUser } from '@/api/client'
import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, ME, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { renderApp } from '@/test/render'

const ADMIN_ME = { ...ME, role: 'admin', tier: 'max', is_admin: true }

const user = (email: string, changes: Partial<AdminUser> = {}): AdminUser => ({
  id: `id-${email}`,
  email,
  display_name: null,
  role: 'free',
  tier: 'basic',
  is_admin: false,
  is_active: true,
  is_deleted: false,
  auth_suspended: false,
  active_keys: 1,
  created_at: '2026-10-01T12:00:00Z',
  ...changes,
})

const ROLES = [
  { name: 'free', tier: 'basic', is_admin: false, is_default: true },
  { name: 'dev', tier: 'pro', is_admin: false, is_default: false },
  { name: 'admin', tier: 'max', is_admin: true, is_default: false },
]

/**
 * The admin endpoints as a tiny in-memory server. Returns the requests made,
 * as "METHOD path?query body".
 */
function adminApi(initial: AdminUser[]) {
  let users = [...initial]
  const calls: string[] = []
  const record = (init: RequestInit | undefined, url: string) =>
    calls.push(`${init?.method ?? 'GET'} ${url}${init?.body ? ` ${init.body}` : ''}`)
  const fetch = mockFetch({
    ...baseRoutes,
    '/portal/me': { body: ADMIN_ME },
    '/portal/admin/roles': { body: ROLES },
    '/portal/admin/users': (init, url) => {
      record(init, url)
      if (init?.method === 'POST') {
        const { email, role } = JSON.parse(String(init.body))
        const created = user(email, { role })
        users = [created, ...users]
        return { status: 201, body: created }
      }
      const params = new URL(url, 'http://test').searchParams
      const q = params.get('q') ?? ''
      const found = users.filter((u) => u.email!.includes(q))
      return { body: { users: found, total: found.length } }
    },
    ...Object.fromEntries(
      initial.map((u) => [
        `/portal/admin/users/${u.id}`,
        (init: RequestInit | undefined, url: string) => {
          record(init, url)
          if (init?.method === 'DELETE') {
            users = users.map((x) => (x.id === u.id ? { ...x, is_deleted: true } : x))
            return { status: 204 }
          }
          const changes = JSON.parse(String(init!.body))
          if (changes.role) changes.tier = ROLES.find((r) => r.name === changes.role)!.tier
          users = users.map((x) => (x.id === u.id ? { ...x, ...changes } : x))
          return { body: users.find((x) => x.id === u.id) }
        },
      ]),
    ),
  })
  return { calls, fetch }
}

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
})

describe('access', () => {
  it('shows the Administration tab to admins only', async () => {
    adminApi([])
    renderApp('/')

    const nav = (await screen.findAllByRole('navigation', { name: 'Main' }))[0]
    expect(await within(nav).findByRole('link', { name: 'Administration' })).toHaveAttribute('href', '/admin')
  })

  it('hides the tab and the page from everyone else, without asking the API', async () => {
    const fetch = mockFetch(baseRoutes)
    renderApp('/admin')

    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    const nav = screen.getAllByRole('navigation', { name: 'Main' })[0]
    expect(within(nav).queryByRole('link', { name: 'Administration' })).not.toBeInTheDocument()
    expect(fetch.mock.calls.map(([url]) => String(url))).not.toContainEqual(expect.stringContaining('/portal/admin'))
  })
})

describe('list', () => {
  it('lists users with their plan and status', async () => {
    adminApi([
      user('eve@example.com', { role: 'dev', tier: 'pro' }),
      user('bob@example.com', { is_active: false }),
      user('cy@example.com', { is_deleted: true }),
    ])
    renderApp('/admin')

    const eve = (await screen.findByText('eve@example.com')).closest('tr')!
    expect(within(eve).getByText('Active')).toBeInTheDocument()
    expect(eve).toHaveTextContent('dev· pro')
    expect(within(screen.getByText('bob@example.com').closest('tr')!).getByText('Inactive')).toBeInTheDocument()
    const cy = screen.getByText('cy@example.com').closest('tr')!
    expect(within(cy).getByText('Deleted')).toBeInTheDocument()
    expect(within(cy).getByRole('button', { name: 'Restore cy@example.com' })).toBeInTheDocument()
    expect(screen.getByText('1–3 of 3')).toBeInTheDocument()
  })

  it('searches by email', async () => {
    const { calls } = adminApi([user('eve@example.com'), user('bob@example.com')])
    renderApp('/admin')
    await screen.findByText('bob@example.com')

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search by name or email' }), 'eve')

    await waitFor(() => expect(screen.queryByText('bob@example.com')).not.toBeInTheDocument())
    expect(screen.getByText('eve@example.com')).toBeInTheDocument()
    expect(calls).toContain('GET /portal/admin/users?limit=50&offset=0&q=eve')
  })

  it('filters by status', async () => {
    const { calls } = adminApi([user('eve@example.com')])
    renderApp('/admin')
    await screen.findByText('eve@example.com')

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Status' }), 'Deleted')

    await waitFor(() => expect(calls).toContain('GET /portal/admin/users?limit=50&offset=0&status=deleted'))
  })

  it('says when nothing matches', async () => {
    adminApi([])
    renderApp('/admin')

    expect(await screen.findByText('No users found')).toBeInTheDocument()
  })
})

describe('edit', () => {
  it('changes the plan and deactivates the account in one request', async () => {
    const { calls } = adminApi([user('eve@example.com')])
    renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Edit eve@example.com' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit user' })
    await waitFor(() => expect(within(dialog).getByLabelText('Plan')).toBeEnabled())
    await userEvent.selectOptions(within(dialog).getByLabelText('Plan'), 'dev (pro tier)')
    await userEvent.click(within(dialog).getByLabelText('Active'))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls).toContain('PATCH /portal/admin/users/id-eve@example.com {"role":"dev","is_active":false}')
    const row = (await screen.findByText('eve@example.com')).closest('tr')!
    await waitFor(() => expect(within(row).getByText('Inactive')).toBeInTheDocument())
    expect(row).toHaveTextContent('dev· pro')
  })

  it('shows and edits the display name', async () => {
    const { calls } = adminApi([user('eve@example.com', { display_name: 'Eve Example' })])
    renderApp('/admin')

    const row = (await screen.findByText('Eve Example')).closest('tr')!
    expect(within(row).getByText('eve@example.com')).toBeInTheDocument()

    await userEvent.click(within(row).getByRole('button', { name: 'Edit eve@example.com' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit user' })
    const name = within(dialog).getByLabelText('Name')
    expect(name).toHaveValue('Eve Example')
    await userEvent.clear(name)
    await userEvent.type(name, 'Eve Renamed')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls).toContain('PATCH /portal/admin/users/id-eve@example.com {"display_name":"Eve Renamed"}')
    expect(await screen.findByText('Eve Renamed')).toBeInTheDocument()
  })

  it('keeps admins from locking themselves out', async () => {
    adminApi([user(ME.email, { id: ME.id, role: 'admin', tier: 'max', is_admin: true })])
    renderApp('/admin')

    const row = within(await screen.findByRole('table')).getByText(ME.email).closest('tr')!
    expect(within(row).getByText('(you)')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument()

    await userEvent.click(within(row).getByRole('button', { name: `Edit ${ME.email}` }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit user' })
    expect(within(dialog).getByLabelText('Active')).toBeDisabled()
    await waitFor(() => expect(within(dialog).getByLabelText('Plan')).toBeEnabled())
    expect(within(dialog).getAllByRole('option').map((o) => o.textContent)).toEqual(['admin (max tier)'])
  })
})

describe('delete and restore', () => {
  it('soft-deletes after confirming', async () => {
    const { calls } = adminApi([user('eve@example.com')])
    renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Delete eve@example.com' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete eve@example.com?' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete user' }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(calls).toContain('DELETE /portal/admin/users/id-eve@example.com')
    const row = screen.getByText('eve@example.com').closest('tr')!
    await waitFor(() => expect(within(row).getByText('Deleted')).toBeInTheDocument())
  })

  it('removes a deleted user for good', async () => {
    const { calls } = adminApi([user('eve@example.com', { is_deleted: true })])
    renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Permanently delete eve@example.com' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Permanently delete eve@example.com?' })
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))

    await waitFor(() => expect(calls).toContain('DELETE /portal/admin/users/id-eve@example.com?permanent=true'))
  })

  it('restores a deleted user', async () => {
    const { calls } = adminApi([user('eve@example.com', { is_deleted: true })])
    renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Restore eve@example.com' }))

    const row = screen.getByText('eve@example.com').closest('tr')!
    await waitFor(() => expect(within(row).getByText('Active')).toBeInTheDocument())
    expect(calls).toContain('PATCH /portal/admin/users/id-eve@example.com {"is_deleted":false}')
  })
})

describe('create', () => {
  it('creates a user on the default plan unless another is picked', async () => {
    const { calls } = adminApi([])
    renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Create user' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create a user' })
    await userEvent.type(within(dialog).getByLabelText('Name'), ' New Person ')
    await userEvent.type(within(dialog).getByLabelText('Email'), ' new@example.com ')
    await userEvent.type(within(dialog).getByLabelText('Password'), 'long-enough')
    await waitFor(() => expect(within(dialog).getByLabelText('Plan')).toHaveValue('free'))
    await userEvent.selectOptions(within(dialog).getByLabelText('Plan'), 'dev (pro tier)')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls).toContain(
      'POST /portal/admin/users {"email":"new@example.com","password":"long-enough","role":"dev","display_name":"New Person"}',
    )
    expect(await screen.findByText('new@example.com')).toBeInTheDocument()
  })

  it('checks the password length before asking the API', async () => {
    const { calls } = adminApi([])
    renderApp('/admin')

    await userEvent.click(await screen.findByRole('button', { name: 'Create user' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create a user' })
    await userEvent.type(within(dialog).getByLabelText('Email'), 'new@example.com')
    await userEvent.type(within(dialog).getByLabelText('Password'), 'short')
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Create user' })).toBeEnabled())
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create user' }))

    expect(within(dialog).getByText('Use a password of at least 8 characters.')).toBeInTheDocument()
    expect(calls.filter((c) => c.startsWith('POST'))).toEqual([])
  })
})
