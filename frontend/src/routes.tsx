import { Outlet, type RouteObject } from 'react-router'

import { AuthProvider } from '@/auth/auth-provider'
import { GuestOnly, RequireAuth } from '@/auth/guards'
import { AppShell } from '@/components/app-shell'
import { AttributionFooter } from '@/components/attribution-footer'
import { AuthLayout } from '@/components/auth-layout'
import { Toaster } from '@/components/ui/sonner'
import { ActivityPage } from '@/pages/admin/activity-page'
import { AdminPage } from '@/pages/admin/admin-page'
import { AuthCallbackPage } from '@/pages/auth-callback'
import { KeysPage } from '@/pages/keys/keys-page'
import { LoginPage } from '@/pages/login'
import { NotFound } from '@/pages/not-found'
import { OverviewPage } from '@/pages/overview/overview-page'
import { ProfilePage } from '@/pages/profile/profile-page'
import { ResetPasswordPage } from '@/pages/reset-password'
import { RouteError } from '@/pages/route-error'
import { SignupPage } from '@/pages/signup'
import { UpdatePasswordPage } from '@/pages/update-password'

export const routes: RouteObject[] = [
  {
    element: (
      <AuthProvider>
        <Outlet />
        <Toaster />
      </AuthProvider>
    ),
    errorElement: (
      <div className="flex min-h-svh flex-col">
        <main className="mx-auto w-full max-w-xl flex-1 p-6">
          <RouteError />
        </main>
        <AttributionFooter />
      </div>
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
                  { path: 'profile', element: <ProfilePage /> },
                  { path: 'admin', element: <AdminPage /> },
                  { path: 'admin/activity', element: <ActivityPage /> },
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
