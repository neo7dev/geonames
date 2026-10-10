import { Navigate, Outlet, useLocation, useSearchParams } from 'react-router'

import { useAuth } from '@/auth/use-auth'
import { nextFrom, safeNext } from '@/lib/next'
import { SuspendedPage } from '@/pages/suspended'

/** Pages for signed-in users: everyone else goes to the login page and comes back after. */
export function RequireAuth() {
  const { session, loading, suspended, signedOutByUser } = useAuth()
  const location = useLocation()

  if (loading) return null
  if (!session) {
    const next = nextFrom(location)
    const search = next === '/' || signedOutByUser ? '' : `?next=${encodeURIComponent(next)}`
    return <Navigate to={`/login${search}`} replace />
  }
  if (suspended) return <SuspendedPage />
  return <Outlet />
}

/** Login and signup: a signed-in user goes straight on to where they were headed. */
export function GuestOnly() {
  const { session, loading } = useAuth()
  const [params] = useSearchParams()

  if (loading) return null
  if (session) return <Navigate to={safeNext(params.get('next'))} replace />
  return <Outlet />
}
