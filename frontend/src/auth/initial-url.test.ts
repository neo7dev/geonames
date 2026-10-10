import { expect, it } from 'vitest'

import { linkError } from '@/auth/initial-url'

it("reads GoTrue's error from the fragment", () => {
  expect(linkError('#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid')).toBe(
    'Email link is invalid',
  )
})

it('falls back to a general message for an error without a description', () => {
  expect(linkError('#error=server_error')).toBe('This link could not be used.')
})

it('finds nothing in a fragment with tokens', () => {
  expect(linkError('#access_token=abc&type=signup')).toBeNull()
})
