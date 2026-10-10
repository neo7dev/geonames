import { Outlet, type RouteObject } from 'react-router'

import { AuthProvider } from '@/auth/auth-provider'
import { GuestOnly, RequireAuth } from '@/auth/guards'
import { AppShell } from '@/components/app-shell'
import { AuthLayout } from '@/components/auth-layout'
import { AuthCallbackPage } from '@/pages/auth-callback'
import { KeysPage } from '@/pages/keys/keys-page'
import { LoginPage } from '@/pages/login'
import { NotFound } from '@/pages/not-found'
import { OverviewPage } from '@/pages/overview/overview-page'
import { ResetPasswordPage } from '@/pages/reset-password'
import { RouteError } from '@/pages/route-error'
import { SignupPage } from '@/pages/signup'
import { UpdatePasswordPage } from '@/pages/update-password'

export const routes: RouteObject[] = [
  {
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    errorElement: (
      <main className="mx-auto max-w-xl p-6">
        <RouteError />
      </main>
    ),
    children: [
      {
        element: <AuthLayout />,
        children: [
          {
            element: <GuestOnly />,
            children: [
              { path: 'login', element: <LoginPage /> },
              { path: 'signup', element: <SignupPage /> },
            ],
          },
          { path: 'reset-password', element: <ResetPasswordPage /> },
          { path: 'auth/callback', element: <AuthCallbackPage /> },
          { path: 'update-password', element: <UpdatePasswordPage /> },
        ],
      },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              {
                // Inside the shell, so the navigation stays usable.
                errorElement: <RouteError />,
                children: [
                  { index: true, element: <OverviewPage /> },
                  { path: 'keys', element: <KeysPage /> },
                  // The chart library loads only when this page opens.
                  {
                    path: 'usage',
                    lazy: async () => ({ Component: (await import('@/pages/usage/usage-page')).UsagePage }),
                  },
                  { path: '*', element: <NotFound /> },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
]
