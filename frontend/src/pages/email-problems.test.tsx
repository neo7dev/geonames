import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setUiConfig, type UiConfig } from '@/config'
import { authError, fakeAuth, fakeSession } from '@/test/fake-supabase'
import { AUTH_SETTINGS, baseRoutes, mockFetch } from '@/test/fetch'
import { prefersColorScheme } from '@/test/media'
import { renderApp } from '@/test/render'

/** Features that need email, when the server's SMTP is off or broken. */

let reports: unknown[]
let warn: ReturnType<typeof vi.spyOn>

function withEmail(email: UiConfig['email'], settings = AUTH_SETTINGS) {
  setUiConfig({
    supabase_url: 'http://supabase.test',
    anon_key: 'anon-key',
    password_min_length: 8,
    email,
  })
  reports = []
  mockFetch({
    ...baseRoutes,
    '/auth/v1/settings': { body: settings },
    '/ui/email-problem': (init) => {
      reports.push(JSON.parse(String(init?.body)))
      return { status: 204 }
    },
  })
}

beforeEach(() => {
  prefersColorScheme('light')
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
})

async function requestReset() {
  renderApp('/reset-password')
  await userEvent.type(await screen.findByLabelText('Email'), 'who@example.com')
  await userEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
}

async function signUp() {
  renderApp('/signup')
  await userEvent.type(await screen.findByLabelText('Email'), 'new@example.com')
  await userEvent.type(screen.getByLabelText('Password'), 'long-enough')
  await userEvent.click(screen.getByRole('button', { name: 'Create account' }))
}

const NOT_SET_UP = /Email is not set up on this server \(SMTP is not configured\)/
const NOT_WORKING = /This server's email \(SMTP\) is not working/

describe('password reset', () => {
  it('says SMTP is not configured, without asking Supabase Auth', async () => {
    withEmail('not_configured')

    await requestReset()

    expect(await screen.findByText(NOT_SET_UP)).toBeInTheDocument()
    expect(fakeAuth.resetPasswordForEmail).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalledWith(expect.stringMatching(NOT_SET_UP))
    await waitFor(() => expect(reports).toEqual([{ feature: 'password_reset', reason: 'unavailable' }]))
  })

  it('says SMTP is not working, in other words', async () => {
    withEmail('failed')

    await requestReset()

    expect(await screen.findByText(NOT_WORKING)).toBeInTheDocument()
    expect(screen.queryByText(NOT_SET_UP)).not.toBeInTheDocument()
    expect(fakeAuth.resetPasswordForEmail).not.toHaveBeenCalled()
  })

  it('goes ahead while the status is unknown', async () => {
    withEmail('unknown')

    await requestReset()

    await waitFor(() => expect(fakeAuth.resetPasswordForEmail).toHaveBeenCalled())
    expect(reports).toEqual([])
  })

  it('reports a send that failed although SMTP looked fine at startup', async () => {
    withEmail('ok')
    fakeAuth.resetPasswordForEmail.mockResolvedValueOnce({
      data: null,
      error: authError('unexpected_failure', 'Error sending recovery email', 500),
    } as never)

    await requestReset()

    expect(
      await screen.findByText(/The email could not be sent, so resetting a password did not work/),
    ).toBeInTheDocument()
    await waitFor(() => expect(reports).toEqual([{ feature: 'password_reset', reason: 'send_failed' }]))
  })
})

describe('signup', () => {
  it('stops when the confirmation email cannot be sent', async () => {
    withEmail('not_configured')

    await signUp()

    expect(await screen.findByText(NOT_SET_UP)).toBeInTheDocument()
    expect(fakeAuth.signUp).not.toHaveBeenCalled()
    await waitFor(() => expect(reports).toEqual([{ feature: 'signup', reason: 'unavailable' }]))
  })

  it('goes ahead when Supabase Auth confirms signups itself', async () => {
    withEmail('not_configured', { ...AUTH_SETTINGS, mailer_autoconfirm: true })
    renderApp('/signup')
    // Wait for the settings, which say no confirmation email is needed.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create account' })).toBeInTheDocument())
    await new Promise((resolve) => setTimeout(resolve, 0))

    await userEvent.type(await screen.findByLabelText('Email'), 'new@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'long-enough')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() => expect(fakeAuth.signUp).toHaveBeenCalled())
    expect(reports).toEqual([])
  })

  it('reports a failed confirmation email', async () => {
    withEmail('ok')
    fakeAuth.signUp.mockResolvedValueOnce({
      data: { session: null, user: null },
      error: authError('unexpected_failure', 'Error sending confirmation email', 500),
    } as never)

    await signUp()

    expect(await screen.findByText(/The email could not be sent, so signing up did not work/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Check your email' })).not.toBeInTheDocument()
  })
})

it('resending the confirmation email stops too', async () => {
  withEmail('failed')
  fakeAuth.signInWithPassword.mockResolvedValueOnce({
    data: { session: null, user: null },
    error: authError('email_not_confirmed'),
  } as never)
  renderApp('/login')

  await userEvent.type(await screen.findByLabelText('Email'), 'ada@example.com')
  await userEvent.type(screen.getByLabelText('Password'), 'correct-horse')
  await userEvent.click(screen.getByRole('button', { name: 'Log in' }))
  await userEvent.click(
    await screen.findByRole('button', {
      name: 'Send the confirmation email again',
    }),
  )

  expect(await screen.findByText(NOT_WORKING)).toBeInTheDocument()
  expect(fakeAuth.resend).not.toHaveBeenCalled()
  await waitFor(() => expect(reports).toEqual([{ feature: 'resend_confirmation', reason: 'unavailable' }]))
})

it('changing the email address stops too', async () => {
  withEmail('not_configured')
  fakeAuth.setSession(fakeSession())
  renderApp('/profile')

  await userEvent.type(await screen.findByLabelText('New email'), 'new@example.com')
  await userEvent.click(screen.getByRole('button', { name: 'Change email' }))

  expect(await screen.findByText(NOT_SET_UP)).toBeInTheDocument()
  expect(fakeAuth.updateUser).not.toHaveBeenCalled()
  await waitFor(() => expect(reports).toEqual([{ feature: 'email_change', reason: 'unavailable' }]))
})
