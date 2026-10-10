import { describe, expect, it, onTestFinished, vi } from 'vitest'

import { api, ApiError, SUSPENDED_EVENT } from '@/api/client'
import { fakeAuth, fakeSession } from '@/test/fake-supabase'
import { mockFetch } from '@/test/fetch'

async function failure(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    return error as ApiError
  }
  throw new Error('expected the call to fail')
}

const headersOf = (fetch: ReturnType<typeof mockFetch>) =>
  new Headers((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].headers)

describe('api', () => {
  it('sends the login token and returns the JSON body', async () => {
    fakeAuth.setSession(fakeSession())
    const fetch = mockFetch({ '/portal/me': { body: { tier: 'basic' } } })

    expect(await api('/portal/me')).toEqual({ tier: 'basic' })
    expect(headersOf(fetch).get('Authorization')).toBe('Bearer login-token')
  })

  it('sends JSON bodies as JSON', async () => {
    const fetch = mockFetch({ '/portal/keys': { status: 201, body: { id: 'k' } } })

    await api('/portal/keys', { method: 'POST', body: JSON.stringify({ name: 'ci' }) })

    expect(headersOf(fetch).get('Content-Type')).toBe('application/json')
  })

  it('sends no Authorization header when signed out', async () => {
    const fetch = mockFetch({ '/portal/me': { body: {} } })

    await api('/portal/me')

    expect(headersOf(fetch).has('Authorization')).toBe(false)
  })

  it('returns nothing for 204', async () => {
    mockFetch({ '/portal/keys/1': { status: 204 } })

    expect(await api('/portal/keys/1', { method: 'DELETE' })).toBeUndefined()
  })

  it("raises ApiError with the API's detail", async () => {
    mockFetch({ '/portal/keys': { status: 409, body: { detail: 'The basic tier allows 1 active API key(s)' } } })

    const error = await failure(api('/portal/keys', { method: 'POST' }))

    expect(error.status).toBe(409)
    expect(error.message).toBe('The basic tier allows 1 active API key(s)')
  })

  it('raises ApiError when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Bad gateway', { status: 502 })))

    const error = await failure(api('/portal/me'))

    expect(error.message).toBe('Request failed (502)')
  })

  it('signs out when the login token is refused', async () => {
    fakeAuth.setSession(fakeSession())
    mockFetch({ '/portal/me': { status: 401, body: { detail: 'Login token expired' } } })

    expect((await failure(api('/portal/me'))).status).toBe(401)
    expect(fakeAuth.signOut).toHaveBeenCalledWith({ scope: 'local' })
  })

  it('sends nothing when the login token cannot be refreshed', async () => {
    fakeAuth.getSession.mockResolvedValueOnce({ data: { session: null }, error: new Error('Failed to fetch') } as never)
    const fetch = mockFetch({ '/portal/me': { body: {} } })

    await expect(api('/portal/me')).rejects.toThrow('Could not reach the login service')
    expect(fetch).not.toHaveBeenCalled()
    expect(fakeAuth.signOut).not.toHaveBeenCalled()
  })

  it('reports a suspended account', async () => {
    const onSuspended = vi.fn()
    window.addEventListener(SUSPENDED_EVENT, onSuspended)
    onTestFinished(() => window.removeEventListener(SUSPENDED_EVENT, onSuspended))
    mockFetch({ '/portal/me': { status: 403, body: { detail: 'suspended' } } })

    await failure(api('/portal/me'))

    expect(onSuspended).toHaveBeenCalledOnce()
    expect(fakeAuth.signOut).not.toHaveBeenCalled()
  })
})
