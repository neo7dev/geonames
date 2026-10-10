import type { AuthError } from '@supabase/supabase-js'
import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { useAuthSettings } from '@/auth/auth-settings'
import { authErrorMessage } from '@/auth/errors'
import { callbackUrl } from '@/auth/urls'
import { AuthCard } from '@/components/auth-layout'
import { ErrorAlert, Field, SubmitButton } from '@/components/form'
import { PasswordInput } from '@/components/password-input'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { safeNext } from '@/lib/next'
import { supabase } from '@/lib/supabase'

export function LoginPage() {
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const settings = useAuthSettings()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<AuthError | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setPending(true)
    setError(null)
    // On success the guard sends the user on to `next`.
    const { error } = await supabase().auth.signInWithPassword({ email, password })
    setError(error)
    setPending(false)
  }

  const resend = async () => {
    setPending(true)
    const { error } = await supabase().auth.resend({
      type: 'signup',
      email,
      options: { emailRedirectTo: callbackUrl(next) },
    })
    setPending(false)
    if (error) setError(error)
    else toast.success(`Confirmation email sent to ${email}.`)
  }

  const signupHref = next === '/' ? '/signup' : `/signup?next=${encodeURIComponent(next)}`

  return (
    <AuthCard
      title="Log in"
      description="Manage your API keys and see your usage."
      footer={
        settings.data?.disable_signup ? null : (
          <>
            No account?{' '}
            <Link to={signupHref} className="font-medium text-primary underline-offset-4 hover:underline">
              Sign up
            </Link>
          </>
        )
      }
    >
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        {error && (
          <ErrorAlert>
            {authErrorMessage(error)}
            {error.code === 'email_not_confirmed' && (
              <Button variant="link" className="h-auto p-0" disabled={pending} onClick={resend} type="button">
                Send the confirmation email again
              </Button>
            )}
          </ErrorAlert>
        )}
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
        <Field
          id="password"
          label="Password"
          aside={
            <Link
              to="/reset-password"
              className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              Forgot password?
            </Link>
          }
        >
          <PasswordInput
            id="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <SubmitButton pending={pending} disabled={pending || !email || !password}>
          Log in
        </SubmitButton>
      </form>
    </AuthCard>
  )
}
