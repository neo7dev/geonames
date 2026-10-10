import type { AuthError } from '@supabase/supabase-js'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { canSendEmail, isEmailSendError, reportEmailProblem } from '@/auth/email'
import { authErrorMessage } from '@/auth/errors'
import { updatePasswordUrl } from '@/auth/urls'
import { AuthCard } from '@/components/auth-layout'
import { ErrorAlert, Field, SubmitButton } from '@/components/form'
import { Input } from '@/components/ui/input'
import { supabase } from '@/lib/supabase'

export function ResetPasswordPage() {
  const [email, setEmail] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<AuthError | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (!canSendEmail('password_reset')) return
    setPending(true)
    const { error } = await supabase().auth.resetPasswordForEmail(email, { redirectTo: updatePasswordUrl() })
    setPending(false)
    // GoTrue answers the same for unknown addresses, so this says nothing about who has an account.
    if (error && isEmailSendError(error)) reportEmailProblem('password_reset', 'send_failed')
    else if (error) setError(error)
    else toast.success(`If an account exists for ${email}, an email with a reset link is on its way.`)
  }

  return (
    <AuthCard
      title="Reset your password"
      description="We will email you a link to set a new one."
      footer={
        <Link to="/login" className="font-medium text-primary underline-offset-4 hover:underline">
          Back to log in
        </Link>
      }
    >
      <form onSubmit={onSubmit} className="grid gap-4" noValidate>
        {error && <ErrorAlert>{authErrorMessage(error)}</ErrorAlert>}
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
        <SubmitButton pending={pending} disabled={pending || !email}>
          Send reset link
        </SubmitButton>
      </form>
    </AuthCard>
  )
}
