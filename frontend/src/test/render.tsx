import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { createQueryClient } from '@/lib/query'
import { routes } from '@/routes'

/** Render the whole app at `path` (inside /ui), with real routes and guards. */
export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

/** Where the app is now, e.g. "/login?next=%2Fkeys". */
export function currentPath(router: ReturnType<typeof renderApp>) {
  const { pathname, search } = router.state.location
  return pathname + search
}

export { screen }
