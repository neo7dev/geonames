import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { baseRoutes, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { renderApp } from '@/test/render'

beforeEach(() => {
  prefersColorScheme('light')
  mockFetch(baseRoutes)
})

async function expectCredit() {
  const footer = (await screen.findByText(/Place data from/)).closest('footer')!
  expect(footer).toHaveTextContent('Place data from GeoNames, licensed under CC BY 4.0.')
  expect(screen.getByRole('link', { name: 'GeoNames' })).toHaveAttribute('href', 'https://www.geonames.org')
  expect(screen.getByRole('link', { name: 'CC BY 4.0' })).toHaveAttribute(
    'href',
    'https://creativecommons.org/licenses/by/4.0/',
  )
}

describe('GeoNames attribution', () => {
  it('is on the login page', async () => {
    renderApp('/login')
    await expectCredit()
  })

  it('is on signed-in pages', async () => {
    fakeAuth.setSession(fakeSession())
    renderApp('/keys')
    expect(await screen.findByRole('heading', { name: 'API keys' })).toBeInTheDocument()
    await expectCredit()
  })
})
