import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '@/api/client'

const getSession = vi.fn()

vi.mock('@/lib/supabase', () => ({
  supabase: () => ({ auth: { getSession } }),
}))

function respond(status: number, body?: unknown) {
  const fetch = vi.fn(async () =>
    body === undefined
      ? new Response(null, { status })
      : new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

async function failure(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('expected the call to fail')
}

beforeEach(() => {
  vi.unstubAllGlobals()
  getSession.mockResolvedValue({ data: { session: { access_token: 'login-token' } } })
})

describe('api', () => {
  it('sends the login token and returns the JSON body', async () => {
    const fetch = respond(200, { tier: 'basic' })

    expect(await api('/portal/me')).toEqual({ tier: 'basic' })

    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/portal/me')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer login-token')
  })

  it('sends JSON bodies as JSON', async () => {
    const fetch = respond(201, { id: 'k' })

    await api('/portal/keys', { method: 'POST', body: JSON.stringify({ name: 'ci' }) })

    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it('sends no Authorization header when signed out', async () => {
    getSession.mockResolvedValue({ data: { session: null } })
    const fetch = respond(200, {})

    await api('/portal/me')

    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(new Headers(init.headers).has('Authorization')).toBe(false)
  })

  it('returns nothing for 204', async () => {
    respond(204)

    expect(await api('/portal/keys/1', { method: 'DELETE' })).toBeUndefined()
  })

  it("raises ApiError with the API's detail", async () => {
    respond(409, { detail: 'The basic tier allows 1 active API key(s)' })

    const error = await failure(api('/portal/keys', { method: 'POST' }))

    expect(error.status).toBe(409)
    expect(error.message).toBe('The basic tier allows 1 active API key(s)')
  })

  it('raises ApiError when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Bad gateway', { status: 502 })))

    const error = await failure(api('/portal/me'))

    expect(error.message).toBe('Request failed (502)')
  })
})
