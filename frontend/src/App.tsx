import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'

import { AppShell } from '@/components/app-shell'
import { NotFound } from '@/pages/not-found'
import { Placeholder } from '@/pages/placeholder'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true } },
})

const router = createBrowserRouter(
  [
    {
      element: <AppShell />,
      children: [
        { index: true, element: <Placeholder title="Overview" /> },
        { path: 'keys', element: <Placeholder title="API keys" /> },
        { path: 'usage', element: <Placeholder title="Usage" /> },
        { path: '*', element: <NotFound /> },
      ],
    },
  ],
  { basename: '/ui' },
)

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
