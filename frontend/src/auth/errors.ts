import type { AuthError } from '@supabase/supabase-js'

import { uiConfig } from '@/config'

/** A message for the user from a Supabase Auth error. */
export function authErrorMessage(error: AuthError): string {
  switch (error.code) {
    case 'invalid_credentials':
      return 'Wrong email or password.'
    case 'email_not_confirmed':
      return 'Confirm your email address first. Check your inbox for the link.'
    case 'user_banned':
      return 'This account is suspended. Ask an admin.'
    case 'weak_password':
      return `Use a password of at least ${uiConfig().password_min_length} characters.`
    case 'same_password':
      return 'Choose a password different from your current one.'
    case 'signup_disabled':
      return 'Signups are closed.'
    case 'over_email_send_rate_limit':
    case 'over_request_rate_limit':
      return 'Too many attempts. Wait a minute and try again.'
    case 'email_address_invalid':
      return 'Enter a valid email address.'
  }
  if (error.status === 0 || error.name === 'AuthRetryableFetchError') {
    return 'Could not reach the sign-in service. Check your connection and try again.'
  }
  return error.message || 'Something went wrong. Try again.'
}
