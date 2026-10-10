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
import { SignupPage } from '@/pages/signup'
import { UpdatePasswordPage } from '@/pages/update-password'
import { UsagePage } from '@/pages/usage/usage-page'

export const routes: RouteObject[] = [
  {
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
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
              { index: true, element: <OverviewPage /> },
              { path: 'keys', element: <KeysPage /> },
              { path: 'usage', element: <UsagePage /> },
              { path: '*', element: <NotFound /> },
            ],
          },
        ],
      },
    ],
  },
]
