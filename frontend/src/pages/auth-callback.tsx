import { Link, Navigate, useSearchParams } from 'react-router'

import { useAuth } from '@/auth/use-auth'
import { linkError } from '@/auth/initial-url'
import { AuthCard } from '@/components/auth-layout'
import { ErrorAlert } from '@/components/form'
import { safeNext } from '@/lib/next'

/** Where email links land. Supabase reads the tokens from the URL fragment on startup. */
export function AuthCallbackPage() {
  const { session, loading } = useAuth()
  const [params] = useSearchParams()
  const error = linkError()

  if (loading) return null
  if (session && !error) return <Navigate to={safeNext(params.get('next'))} replace />

  return (
    <AuthCard
      title="This link did not work"
      footer={
        <Link to="/login" className="font-medium text-primary underline-offset-4 hover:underline">
          Back to log in
        </Link>
      }
    >
      <ErrorAlert>
        {error ?? 'This link is invalid or has expired.'} Links work once and expire after a while; log
        in to get a new one sent.
      </ErrorAlert>
    </AuthCard>
  )
}
