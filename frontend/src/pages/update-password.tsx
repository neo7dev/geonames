import type { AuthError } from '@supabase/supabase-js'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { useAuth } from '@/auth/use-auth'
import { authErrorMessage } from '@/auth/errors'
import { linkError } from '@/auth/initial-url'
import { AuthCard } from '@/components/auth-layout'
import { ErrorAlert, Field, SubmitButton } from '@/components/form'
import { PasswordInput } from '@/components/password-input'
import { uiConfig } from '@/config'
import { supabase } from '@/lib/supabase'

/** Where the recovery link lands, with a session from its fragment. */
export function UpdatePasswordPage() {
  const { session, loading } = useAuth()
  const navigate = useNavigate()
  const minLength = uiConfig().password_min_length
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<AuthError | string | null>(null)

  if (loading) return null
  if (!session) {
    return (
      <AuthCard
        title="This link did not work"
        footer={
          <Link to="/reset-password" className="font-medium text-primary underline-offset-4 hover:underline">
            Send a new reset link
          </Link>
        }
      >
        <ErrorAlert>{linkError() ?? 'This reset link is invalid or has expired.'}</ErrorAlert>
      </AuthCard>
    )
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (password.length < minLength) {
      setError(`Use a password of at least ${minLength} characters.`)
      return
    }
    if (password !== confirm) {
      setError('The passwords do not match.')
      return
    }
    setPending(true)
    const { error } = await supabase().auth.updateUser({ password })
    setPending(false)
    if (error) setError(error)
    else navigate('/', { replace: true })
  }

  return (
    <AuthCard title="Set a new password" description={session.user.email}>
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        {error && <ErrorAlert>{typeof error === 'string' ? error : authErrorMessage(error)}</ErrorAlert>}
        <Field id="password" label="New password" hint={`At least ${minLength} characters.`}>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            required
            autoFocus
            minLength={minLength}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field id="confirm" label="Confirm new password">
          <PasswordInput
            id="confirm"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </Field>
        <SubmitButton pending={pending} disabled={pending || !password || !confirm}>
          Update password
        </SubmitButton>
      </form>
    </AuthCard>
  )
}
