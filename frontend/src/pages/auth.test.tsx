import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { authError, fakeAuth, fakeSession } from '@/test/fake-supabase'
import { AUTH_SETTINGS, baseRoutes, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { currentPath, renderApp } from '@/test/render'

const hash = vi.hoisted(() => ({ value: '' }))
vi.mock('@/auth/initial-url', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/auth/initial-url')>()
  return { ...real, linkError: () => real.linkError(hash.value) }
})

const ORIGIN = window.location.origin

beforeEach(() => {
  prefersColorScheme('light')
  hash.value = ''
  mockFetch(baseRoutes)
})

async function logIn(email = 'ada@example.com', password = 'correct-horse') {
  await userEvent.type(await screen.findByLabelText('Email'), email)
  await userEvent.type(screen.getByLabelText('Password'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
}

describe('route guard', () => {
  it('sends a signed-out user to log in, then back to the page', async () => {
    const router = renderApp('/keys')

    await waitFor(() => expect(currentPath(router)).toBe('/login?next=%2Fkeys'))
    await logIn()

    await waitFor(() => expect(currentPath(router)).toBe('/keys'))
    expect(fakeAuth.signInWithPassword).toHaveBeenCalledWith({
      email: 'ada@example.com',
      password: 'correct-horse',
    })
  })

  it('sends a signed-in user on from the login page', async () => {
    fakeAuth.setSession(fakeSession())
    const router = renderApp('/login?next=%2Fusage')

    await waitFor(() => expect(currentPath(router)).toBe('/usage'))
  })

  it('ignores a next that leaves the app', async () => {
    fakeAuth.setSession(fakeSession())
    const router = renderApp('/login?next=%2F%2Fevil.example')

    await waitFor(() => expect(currentPath(router)).toBe('/'))
  })

  it('shows the suspended page when the API refuses the account', async () => {
    fakeAuth.setSession(fakeSession())
    mockFetch({ ...baseRoutes, '/portal/me': { status: 403, body: { detail: 'suspended' } } })
    renderApp('/')

    expect(await screen.findByRole('heading', { name: 'Account suspended' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(fakeAuth.signOut).toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument()
  })
})

describe('login', () => {
  it('says when the email or password is wrong', async () => {
    fakeAuth.signInWithPassword.mockResolvedValueOnce({
      data: { session: null, user: null },
      error: authError('invalid_credentials'),
    } as never)
    renderApp('/login')

    await logIn()

    expect(await screen.findByText('Wrong email or password.')).toBeInTheDocument()
  })

  it('offers to resend the confirmation email', async () => {
    fakeAuth.signInWithPassword.mockResolvedValueOnce({
      data: { session: null, user: null },
      error: authError('email_not_confirmed'),
    } as never)
    renderApp('/login?next=%2Fkeys')

    await logIn()
    await userEvent.click(await screen.findByRole('button', { name: 'Send the confirmation email again' }))

    expect(fakeAuth.resend).toHaveBeenCalledWith({
      type: 'signup',
      email: 'ada@example.com',
      options: { emailRedirectTo: `${ORIGIN}/ui/auth/callback?next=%2Fkeys` },
    })
    expect(await screen.findByText('Confirmation email sent to ada@example.com.')).toBeInTheDocument()
  })

  it('links to signup unless signups are closed', async () => {
    renderApp('/login')
    expect(await screen.findByRole('link', { name: 'Sign up' })).toBeInTheDocument()
  })

  it('hides the signup link when signups are closed', async () => {
    mockFetch({ ...baseRoutes, '/auth/v1/settings': { body: { ...AUTH_SETTINGS, disable_signup: true } } })
    renderApp('/login')

    await screen.findByRole('heading', { name: 'Log in' })
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Sign up' })).not.toBeInTheDocument())
  })

  it('shows the password on request', async () => {
    renderApp('/login')
    const password = await screen.findByLabelText('Password')
    expect(password).toHaveAttribute('type', 'password')

    await userEvent.click(screen.getByRole('button', { name: 'Show password' }))

    expect(password).toHaveAttribute('type', 'text')
  })
})

describe('signup', () => {
  async function signUp(password: string) {
    await userEvent.type(await screen.findByLabelText('Email'), 'new@example.com')
    await userEvent.type(screen.getByLabelText('Password'), password)
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
  }

  it('refuses a short password before sending it', async () => {
    renderApp('/signup')
    expect(await screen.findByText('At least 8 characters.')).toBeInTheDocument()

    await signUp('short77')

    expect(screen.getByText('Use a password of at least 8 characters.')).toBeInTheDocument()
    expect(fakeAuth.signUp).not.toHaveBeenCalled()
  })

  it('asks the user to confirm their email', async () => {
    renderApp('/signup?next=%2Fkeys')

    await signUp('long-enough')

    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText('new@example.com')).toBeInTheDocument()
    expect(fakeAuth.signUp).toHaveBeenCalledWith({
      email: 'new@example.com',
      password: 'long-enough',
      options: { emailRedirectTo: `${ORIGIN}/ui/auth/callback?next=%2Fkeys` },
    })
  })

  it('goes straight in when email confirmation is off', async () => {
    fakeAuth.signUp.mockImplementationOnce(async () => {
      fakeAuth.setSession(fakeSession('new@example.com'))
      fakeAuth.emit('SIGNED_IN')
      return { data: { session: fakeSession('new@example.com'), user: null }, error: null } as never
    })
    const router = renderApp('/signup')

    await signUp('long-enough')

    await waitFor(() => expect(currentPath(router)).toBe('/'))
  })

  it("shows GoTrue's refusal", async () => {
    fakeAuth.signUp.mockResolvedValueOnce({
      data: { session: null, user: null },
      error: authError('over_email_send_rate_limit', 'rate limit', 429),
    } as never)
    renderApp('/signup')

    await signUp('long-enough')

    expect(await screen.findByText('Too many attempts. Wait a minute and try again.')).toBeInTheDocument()
  })

  it('says signups are closed', async () => {
    mockFetch({ ...baseRoutes, '/auth/v1/settings': { body: { ...AUTH_SETTINGS, disable_signup: true } } })
    renderApp('/signup')

    expect(await screen.findByRole('heading', { name: 'Signups are closed' })).toBeInTheDocument()
  })
})

describe('email link callback', () => {
  it('moves on to next once the link has logged the user in', async () => {
    fakeAuth.setSession(fakeSession())
    const router = renderApp('/auth/callback?next=%2Fkeys')

    await waitFor(() => expect(currentPath(router)).toBe('/keys'))
  })

  it("shows GoTrue's error for an expired link", async () => {
    hash.value = '#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired'
    renderApp('/auth/callback')

    expect(await screen.findByRole('heading', { name: 'This link did not work' })).toBeInTheDocument()
    expect(screen.getByText(/Email link is invalid or has expired/)).toBeInTheDocument()
  })

  it('explains a link that brought no session', async () => {
    renderApp('/auth/callback')

    expect(await screen.findByText(/This link is invalid or has expired/)).toBeInTheDocument()
  })
})

describe('password reset', () => {
  it('sends the link without saying whether the account exists', async () => {
    renderApp('/reset-password')

    await userEvent.type(await screen.findByLabelText('Email'), 'who@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(fakeAuth.resetPasswordForEmail).toHaveBeenCalledWith('who@example.com', {
      redirectTo: `${ORIGIN}/ui/update-password`,
    })
    expect(
      await screen.findByText('If an account exists for who@example.com, an email with a reset link is on its way.'),
    ).toBeInTheDocument()
  })

  it('opens the update page when a recovery link lands elsewhere', async () => {
    fakeAuth.setSession(fakeSession())
    const router = renderApp('/')
    await screen.findAllByRole('navigation', { name: 'Main' })

    fakeAuth.emit('PASSWORD_RECOVERY')

    await waitFor(() => expect(currentPath(router)).toBe('/update-password'))
  })
})

describe('update password', () => {
  async function setPassword(password: string, confirm = password) {
    await userEvent.type(await screen.findByLabelText('New password'), password)
    await userEvent.type(screen.getByLabelText('Confirm new password'), confirm)
    await userEvent.click(screen.getByRole('button', { name: 'Update password' }))
  }

  it('needs the session from the recovery link', async () => {
    renderApp('/update-password')

    expect(await screen.findByText('This reset link is invalid or has expired.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Send a new reset link' })).toBeInTheDocument()
  })

  it('refuses passwords that do not match', async () => {
    fakeAuth.setSession(fakeSession())
    renderApp('/update-password')

    await setPassword('long-enough', 'long-enougj')

    expect(screen.getByText('The passwords do not match.')).toBeInTheDocument()
    expect(fakeAuth.updateUser).not.toHaveBeenCalled()
  })

  it('sets the password and goes to the overview', async () => {
    fakeAuth.setSession(fakeSession())
    const router = renderApp('/update-password')

    await setPassword('long-enough')

    expect(fakeAuth.updateUser).toHaveBeenCalledWith({ password: 'long-enough' })
    await waitFor(() => expect(currentPath(router)).toBe('/'))
  })

  it('asks for a different password when it is unchanged', async () => {
    fakeAuth.setSession(fakeSession())
    fakeAuth.updateUser.mockResolvedValueOnce({ data: { user: null }, error: authError('same_password') } as never)
    renderApp('/update-password')

    await setPassword('long-enough')

    expect(await screen.findByText('Choose a password different from your current one.')).toBeInTheDocument()
  })
})
