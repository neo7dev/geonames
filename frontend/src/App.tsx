import { QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'

import { createQueryClient } from '@/lib/query'
import { routes } from '@/routes'

const queryClient = createQueryClient()
const router = createBrowserRouter(routes, { basename: '/ui' })

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
