import type { AuthError } from '@supabase/supabase-js'
import { MailCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'

import { useAuthSettings } from '@/auth/auth-settings'
import { authErrorMessage } from '@/auth/errors'
import { callbackUrl } from '@/auth/urls'
import { AuthCard } from '@/components/auth-layout'
import { ErrorAlert, Field, SubmitButton } from '@/components/form'
import { PasswordInput } from '@/components/password-input'
import { Input } from '@/components/ui/input'
import { uiConfig } from '@/config'
import { safeNext } from '@/lib/next'
import { supabase } from '@/lib/supabase'

const loginLink = (
  <Link to="/login" className="font-medium text-primary underline-offset-4 hover:underline">
    Log in
  </Link>
)

export function SignupPage() {
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const settings = useAuthSettings()
  const minLength = uiConfig().password_min_length
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<AuthError | string | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)

  if (settings.data?.disable_signup) {
    return (
      <AuthCard title="Signups are closed" description="Ask an admin for an account." footer={loginLink}>
        {null}
      </AuthCard>
    )
  }

  if (sentTo) {
    return (
      <AuthCard title="Check your email" footer={<>Confirmed already? {loginLink}</>}>
        <div className="flex gap-3 text-sm">
          <MailCheck className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
          <p>
            We sent a confirmation link to <span className="font-medium">{sentTo}</span>. Open it to finish
            signing up. It can take a minute to arrive.
          </p>
        </div>
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
    setPending(true)
    const { data, error } = await supabase().auth.signUp({
      email,
      password,
      options: { emailRedirectTo: callbackUrl(next) },
    })
    setPending(false)
    if (error) setError(error)
    // With a session (email confirmation off) the guard moves on to `next`.
    else if (!data.session) setSentTo(email)
  }

  return (
    <AuthCard title="Create an account" description="Get an API key for the GeoNames API." footer={<>Have an account? {loginLink}</>}>
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        {error && <ErrorAlert>{typeof error === 'string' ? error : authErrorMessage(error)}</ErrorAlert>}
        <Field id="email" label="Email">
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field id="password" label="Password" hint={`At least ${minLength} characters.`}>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            required
            minLength={minLength}
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <SubmitButton pending={pending} disabled={pending || !email || !password}>
          Create account
        </SubmitButton>
      </form>
    </AuthCard>
  )
}
