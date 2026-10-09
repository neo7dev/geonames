import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { AppShell } from '@/components/app-shell'
import { prefersColorScheme } from '@/test/media'

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <p>overview page</p> },
          { path: 'keys', element: <p>keys page</p> },
          { path: 'usage', element: <p>usage page</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  )
  render(<RouterProvider router={router} />)
}

beforeEach(() => prefersColorScheme('light'))
afterEach(() => vi.unstubAllGlobals())

it('marks the current page in the navigation', () => {
  renderAt('/keys')

  const nav = screen.getAllByRole('navigation', { name: 'Main' })[0]
  expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent('API keys')
  expect(screen.getByText('keys page')).toBeInTheDocument()
})

it('opens the menu, navigates and closes it again', async () => {
  renderAt('/')
  expect(screen.getAllByRole('navigation', { name: 'Main' })).toHaveLength(1)

  await userEvent.click(screen.getByRole('button', { name: 'Open menu' }))
  const menus = screen.getAllByRole('navigation', { name: 'Main' })
  expect(menus).toHaveLength(2)

  await userEvent.click(menus[1].querySelector('a[href="/usage"]')!)
  expect(screen.getByText('usage page')).toBeInTheDocument()
  expect(screen.getAllByRole('navigation', { name: 'Main' })).toHaveLength(1)
})

it('links to the API docs in a new tab', () => {
  renderAt('/')

  const docs = screen.getByRole('link', { name: 'API docs' })
  expect(docs).toHaveAttribute('href', '/docs')
  expect(docs).toHaveAttribute('target', '_blank')
})
