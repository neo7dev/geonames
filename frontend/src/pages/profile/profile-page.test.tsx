import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, ME, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { currentPath, renderApp } from '@/test/render'

/** /portal/me that remembers the display name it is sent. Returns the PATCH bodies. */
function portal(displayName: string | null = null) {
  let me = { ...ME, display_name: displayName }
  const patches: unknown[] = []
  mockFetch({
    ...baseRoutes,
    '/portal/me': (init) => {
      if (init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body))
        patches.push(body)
        me = { ...me, ...body }
      }
      return { body: me }
    },
  })
  return patches
}

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
})

describe('sidebar', () => {
  it('opens the profile from the name in the bottom left', async () => {
    portal('Ada Lovelace')
    const router = renderApp('/')

    const nav = (await screen.findAllByRole('complementary'))[0]
    const link = await within(nav).findByRole('link', { name: /Ada Lovelace/ })
    expect(link).toHaveTextContent('ada@example.com')
    await userEvent.click(link)

    await waitFor(() => expect(currentPath(router)).toBe('/profile'))
    expect(screen.getByRole('heading', { name: 'Profile' })).toBeInTheDocument()
  })

  it('shows the email when there is no name', async () => {
    portal()
    renderApp('/')

    const nav = (await screen.findAllByRole('complementary'))[0]
    expect(await within(nav).findByRole('link', { name: /ada@example.com/ })).toHaveAttribute('href', '/profile')
  })
})

describe('name', () => {
  it('saves a trimmed name', async () => {
    const patches = portal()
    renderApp('/profile')

    await userEvent.type(await screen.findByLabelText('Display name'), '  Ada Lovelace ')
    await userEvent.click(screen.getByRole('button', { name: 'Save name' }))

    expect(await screen.findByText('Name saved.')).toBeInTheDocument()
    expect(patches).toEqual([{ display_name: 'Ada Lovelace' }])
  })

  it('clears the name when emptied', async () => {
    const patches = portal('Ada')
    renderApp('/profile')

    const field = await screen.findByLabelText('Display name')
    expect(field).toHaveValue('Ada')
    await userEvent.clear(field)
    await userEvent.click(screen.getByRole('button', { name: 'Save name' }))

    await waitFor(() => expect(patches).toEqual([{ display_name: null }]))
  })
})

describe('email', () => {
  it('asks Supabase Auth to change it, with a link back to the profile', async () => {
    portal()
    renderApp('/profile')

    await userEvent.type(await screen.findByLabelText('New email'), 'new@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Change email' }))

    expect(await screen.findByText(/We sent a confirmation link to new@example.com/)).toBeInTheDocument()
    expect(fakeAuth.updateUser).toHaveBeenCalledWith(
      { email: 'new@example.com' },
      { emailRedirectTo: 'http://localhost:3000/ui/auth/callback?next=%2Fprofile' },
    )
  })

  it('refuses the current address', async () => {
    portal()
    renderApp('/profile')

    await userEvent.type(await screen.findByLabelText('New email'), 'ADA@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Change email' }))

    expect(await screen.findByText('That is already your email address.')).toBeInTheDocument()
    expect(fakeAuth.updateUser).not.toHaveBeenCalled()
  })
})

describe('password', () => {
  it('changes it once both fields match', async () => {
    portal()
    renderApp('/profile')

    await userEvent.type(await screen.findByLabelText('New password'), 'a-new-password')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'a-different-one')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('The passwords do not match.')).toBeInTheDocument()
    expect(fakeAuth.updateUser).not.toHaveBeenCalled()

    await userEvent.clear(screen.getByLabelText('Confirm new password'))
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'a-new-password')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('Password changed.')).toBeInTheDocument()
    expect(fakeAuth.updateUser).toHaveBeenCalledWith({ password: 'a-new-password' })
  })
})

it('shows the plan, read-only', async () => {
  portal()
  renderApp('/profile')

  expect(await screen.findByText('Only an admin can change your plan.', { exact: false })).toBeInTheDocument()
  expect(screen.getByText('basic')).toBeInTheDocument()
})
