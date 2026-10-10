/**
 * The page to return to after logging in, from a `next` query parameter.
 * Only paths inside the app count, so a crafted link cannot send the user to
 * another site after they log in.
 */
export function safeNext(value: string | null | undefined, fallback = '/'): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) {
    return fallback
  }
  try {
    // Resolve against a dummy origin: anything that leaves it is not ours.
    const url = new URL(value, 'http://app.invalid')
    if (url.origin !== 'http://app.invalid') return fallback
    return url.pathname + url.search + url.hash
  } catch {
    return fallback
  }
}

/** The `next` value that brings the user back to where they are now. */
export function nextFrom(location: { pathname: string; search: string }): string {
  return location.pathname + location.search
}
