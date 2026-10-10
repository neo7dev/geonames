import { screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { renderApp } from '@/test/render'

// What a tab opened before a deploy sees: the old chunk is gone.
vi.mock('@/pages/usage/usage-page', () => {
  throw new Error('Failed to fetch dynamically imported module')
})

beforeEach(() => {
  prefersColorScheme('light')
  fakeAuth.setSession(fakeSession())
  mockFetch(baseRoutes)
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

it('offers a reload inside the shell when a page fails to load', async () => {
  renderApp('/usage')

  expect(await screen.findByText('This page could not load')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
  expect(screen.getAllByRole('navigation', { name: 'Main' }).length).toBeGreaterThan(0)
})
