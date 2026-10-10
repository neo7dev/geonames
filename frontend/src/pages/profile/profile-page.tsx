import type { AuthError } from '@supabase/supabase-js'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import type { Me } from '@/api/client'
import { displayNameError, MAX_DISPLAY_NAME, useMe, useUpdateMe } from '@/api/queries'
import { canSendEmail, isEmailSendError, reportEmailProblem } from '@/auth/email'
import { authErrorMessage } from '@/auth/errors'
import { callbackUrl } from '@/auth/urls'
import { useAuth } from '@/auth/use-auth'
import { ErrorAlert, Field } from '@/components/form'
import { PageHeader } from '@/components/page-header'
import { PasswordInput } from '@/components/password-input'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { uiConfig } from '@/config'
import { formatDate } from '@/lib/format'
import { supabase } from '@/lib/supabase'

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/** The signed-in user's own account: name, email, password, and their plan. */
export function ProfilePage() {
  const me = useMe()

  return (
    <>
      <PageHeader title="Profile" description="Your name, sign-in details and plan." />
      {me.error && <ErrorAlert>{me.error.message}</ErrorAlert>}
      {me.data && (
        <div className="grid max-w-2xl gap-4">
          <NameSection me={me.data} />
          <EmailSection />
          <PasswordSection />
          <PlanSection me={me.data} />
        </div>
      )}
    </>
  )
}

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border p-4">
      <h2 className="font-medium">{title}</h2>
      {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

function NameSection({ me }: { me: Me }) {
  const update = useUpdateMe()
  const [name, setName] = useState(me.display_name ?? '')
  const [problem, setProblem] = useState<string | null>(null)
  const changed = name.trim() !== (me.display_name ?? '')

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const error = displayNameError(name)
    setProblem(error)
    if (!error) update.mutate(name, { onSuccess: () => toast.success('Name saved.') })
  }

  return (
    <Section title="Name" description="How you appear in the portal. Leave it empty to show your email instead.">
      <form onSubmit={onSubmit} className="grid gap-3" noValidate>
        {update.error && <ErrorAlert>{update.error.message}</ErrorAlert>}
        <Field id="display-name" label="Display name" hint={problem ?? `Up to ${MAX_DISPLAY_NAME} characters.`}>
          <Input
            id="display-name"
            autoComplete="name"
            value={name}
            aria-invalid={problem !== null}
            aria-describedby="display-name-hint"
            onChange={(e) => {
              setName(e.target.value)
              setProblem(null)
            }}
          />
        </Field>
        <div>
          <Button type="submit" disabled={update.isPending || !changed}>
            Save name
          </Button>
        </div>
      </form>
    </Section>
  )
}

function EmailSection() {
  const { session } = useAuth()
  const current = session?.user.email ?? ''
  // Set while a change waits for confirmation: Supabase Auth keeps it on the user.
  const pendingEmail = session?.user.new_email
  const [email, setEmail] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<AuthError | string | null>(null)
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    const next = email.trim()
    if (!EMAIL_PATTERN.test(next)) return setError('Enter an email address.')
    if (next.toLowerCase() === current.toLowerCase()) return setError('That is already your email address.')
    // Supabase Auth confirms the new address by email.
    if (!canSendEmail('email_change')) return
    setPending(true)
    const { error } = await supabase().auth.updateUser({ email: next }, { emailRedirectTo: callbackUrl('/profile') })
    setPending(false)
    if (error && isEmailSendError(error)) return reportEmailProblem('email_change', 'send_failed')
    if (error) return setError(error)
    toast.success(`Confirmation link sent to ${next}`, {
      description: `Your email changes once you open it. You may also need to confirm from ${current}.`,
    })
    setEmail('')
  }

  return (
    <Section
      title="Email"
      description={
        <>
          You sign in with <span className="font-medium text-foreground">{current}</span>.
        </>
      }
    >
      <form onSubmit={onSubmit} className="grid gap-3" noValidate>
        {error && <ErrorAlert>{typeof error === 'string' ? error : authErrorMessage(error)}</ErrorAlert>}
        {pendingEmail && (
          <p className="rounded-lg border px-3 py-2 text-sm text-muted-foreground">
            A change to {pendingEmail} is waiting for confirmation.
          </p>
        )}
        <Field id="new-email" label="New email">
          <Input
            id="new-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <div>
          <Button type="submit" disabled={pending || !email.trim()}>
            Change email
          </Button>
        </div>
      </form>
    </Section>
  )
}

function PasswordSection() {
  const minLength = uiConfig().password_min_length
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<AuthError | string | null>(null)

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (password.length < minLength) return setError(`Use a password of at least ${minLength} characters.`)
    if (password !== confirm) return setError('The passwords do not match.')
    setPending(true)
    const { error } = await supabase().auth.updateUser({ password })
    setPending(false)
    if (error) return setError(error)
    toast.success('Password changed.')
    setPassword('')
    setConfirm('')
  }

  return (
    <Section title="Password">
      <form onSubmit={onSubmit} className="grid gap-3" noValidate>
        {error && <ErrorAlert>{typeof error === 'string' ? error : authErrorMessage(error)}</ErrorAlert>}
        <Field id="new-password" label="New password" hint={`At least ${minLength} characters.`}>
          <PasswordInput
            id="new-password"
            autoComplete="new-password"
            aria-describedby="new-password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field id="confirm-password" label="Confirm new password">
          <PasswordInput
            id="confirm-password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </Field>
        <div>
          <Button type="submit" disabled={pending || !password || !confirm}>
            Change password
          </Button>
        </div>
      </form>
    </Section>
  )
}

function PlanSection({ me }: { me: Me }) {
  return (
    <Section
      title="Plan"
      description={
        <>
          Only an admin can change your plan. See your limits on the{' '}
          <Link to="/" className="text-primary underline-offset-4 hover:underline">
            overview
          </Link>
          .
        </>
      }
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
        <dt className="text-muted-foreground">Tier</dt>
        <dd className="capitalize">{me.tier}</dd>
        <dt className="text-muted-foreground">Role</dt>
        <dd>{me.role}</dd>
        <dt className="text-muted-foreground">Member since</dt>
        <dd>{formatDate(me.created_at)}</dd>
      </dl>
    </Section>
  )
}
