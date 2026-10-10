/** Absolute URLs for email links, so each origin (the API, the Vite dev server) gets its own. */
// The app always lives at /ui/ (the API's route and Vite's base).
const base = () => `${window.location.origin}/ui/`

export function callbackUrl(next?: string): string {
  const url = `${base()}auth/callback`
  return next && next !== '/' ? `${url}?next=${encodeURIComponent(next)}` : url
}

export function updatePasswordUrl(): string {
  return `${base()}update-password`
}
