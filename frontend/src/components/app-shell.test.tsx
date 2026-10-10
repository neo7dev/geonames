import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { currentPath, renderApp } from '@/test/render'

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
  mockFetch(baseRoutes)
})

it('marks the current page in the navigation', async () => {
  renderApp('/keys')

  const nav = (await screen.findAllByRole('navigation', { name: 'Main' }))[0]
  expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent('API keys')
  expect(screen.getByRole('heading', { name: 'API keys' })).toBeInTheDocument()
})

it('opens the menu, navigates and closes it again', async () => {
  const router = renderApp('/')
  expect(await screen.findAllByRole('navigation', { name: 'Main' })).toHaveLength(1)

  await userEvent.click(screen.getByRole('button', { name: 'Open menu' }))
  const menus = screen.getAllByRole('navigation', { name: 'Main' })
  expect(menus).toHaveLength(2)

  await userEvent.click(menus[1].querySelector('a[href="/usage"]')!)
  expect(currentPath(router)).toBe('/usage')
  expect(screen.getAllByRole('navigation', { name: 'Main' })).toHaveLength(1)
})

it('links to the API docs in a new tab', async () => {
  renderApp('/')

  const docs = await screen.findByRole('link', { name: 'API docs' })
  expect(docs).toHaveAttribute('href', '/docs')
  expect(docs).toHaveAttribute('target', '_blank')
})

it("shows the user's email and tier, and signs out to a plain login page", async () => {
  const router = renderApp('/usage')

  expect((await screen.findAllByText('ada@example.com'))[0]).toBeInTheDocument()
  expect(await screen.findByText('basic tier')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

  expect(fakeAuth.signOut).toHaveBeenCalledWith({ scope: 'local' })
  expect(currentPath(router)).toBe('/login')
})
