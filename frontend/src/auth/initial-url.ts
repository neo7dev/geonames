/**
 * The URL fragment as the page loaded. Supabase clears the fragment once it
 * has read the tokens from an email link, but the callback page still needs
 * any error GoTrue put there (an expired link, for one).
 */
export const initialHash = typeof window === 'undefined' ? '' : window.location.hash

/** GoTrue's error from the fragment, e.g. "Email link is invalid or has expired". */
export function linkError(hash: string = initialHash): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const description = params.get('error_description')
  if (description) return description.replace(/\+/g, ' ')
  return params.get('error') ? 'This link could not be used.' : null
}
