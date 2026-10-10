import type { AuthError } from '@supabase/supabase-js'
import { toast } from 'sonner'

import { uiConfig } from '@/config'

/** UI features that only work when Supabase Auth can send email. */
export type EmailFeature = 'signup' | 'password_reset' | 'email_change' | 'resend_confirmation'

const FEATURES: Record<EmailFeature, string> = {
  signup: 'signing up',
  password_reset: 'resetting a password',
  email_change: 'changing your email address',
  resend_confirmation: 'resending the confirmation email',
}

/** Long enough to read; other toasts close after 2 seconds. */
export const EMAIL_TOAST_DURATION_MS = 10_000

function message(feature: EmailFeature, reason: 'unavailable' | 'send_failed'): string {
  const what = FEATURES[feature]
  if (reason === 'send_failed') {
    return `The email could not be sent, so ${what} did not work: this server's email (SMTP) is not working. Ask an admin to fix the SMTP settings.`
  }
  return uiConfig().email === 'not_configured'
    ? `Email is not set up on this server (SMTP is not configured), so ${what} is not available. Ask an admin to set up SMTP.`
    : `This server's email (SMTP) is not working, so ${what} is not available right now. Ask an admin to fix the SMTP settings.`
}

/**
 * Tell the user, the browser console and the server's log that an email
 * feature cannot work. The server logs each kind at most once a minute.
 */
export function reportEmailProblem(feature: EmailFeature, reason: 'unavailable' | 'send_failed') {
  const text = message(feature, reason)
  toast.error(text, { duration: EMAIL_TOAST_DURATION_MS })
  console.warn(`[geonames] ${text}`)
  void fetch('/ui/email-problem', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feature, reason }),
  }).catch(() => {})
}

/**
 * Whether the server can send email, as its startup check found. When it
 * cannot, reports the problem and returns false, so the caller stops before
 * asking Supabase Auth for an email that would never arrive. While the
 * status is unknown it lets the request through.
 */
export function canSendEmail(feature: EmailFeature): boolean {
  const { email } = uiConfig()
  if (email === 'not_configured' || email === 'failed') {
    reportEmailProblem(feature, 'unavailable')
    return false
  }
  return true
}

/** Supabase Auth's answer when it tried to send an email and the SMTP server refused. */
export function isEmailSendError(error: AuthError): boolean {
  return /error sending .*email/i.test(error.message)
}
