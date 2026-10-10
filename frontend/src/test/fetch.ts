import { vi } from 'vitest'

type Reply = { status?: number; body?: unknown }
type Route = Reply | ((init: RequestInit | undefined, url: string) => Reply)

/**
 * Stub fetch with replies keyed by path (the API) or by the end of the URL
 * (Supabase's /auth/v1/settings). Unknown URLs fail the test loudly.
 */
export function mockFetch(routes: Record<string, Route>) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const path = url.startsWith('/') ? url : new URL(url).pathname + new URL(url).search
    const key = Object.keys(routes).find((k) => path === k || path.split('?')[0] === k || url.endsWith(k))
    if (key === undefined) throw new Error(`Unexpected fetch: ${init?.method ?? 'GET'} ${url}`)
    const route = routes[key]
    const reply = typeof route === 'function' ? route(init, url) : route
    const status = reply.status ?? 200
    return reply.body === undefined
      ? new Response(null, { status })
      : new Response(JSON.stringify(reply.body), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

export const ME = {
  id: '00000000-0000-0000-0000-000000000001',
  email: 'ada@example.com',
  role: 'free',
  tier: 'basic',
  limits: { per_minute: 10, per_hour: 100, per_month: 5000, max_keys: 1 },
  active_keys: 0,
}

export const AUTH_SETTINGS = { disable_signup: false, mailer_autoconfirm: false }

/** What every signed-in page needs: the profile and GoTrue's settings. */
export const baseRoutes = {
  '/portal/me': { body: ME },
  '/portal/keys': { body: [] },
  '/auth/v1/settings': { body: AUTH_SETTINGS },
}
