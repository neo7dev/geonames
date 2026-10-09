import { supabase } from '@/lib/supabase'

import type { components } from './schema'

type Schemas = components['schemas']
export type Me = Schemas['MeResponse']
export type ApiKey = Schemas['ApiKeyResponse']
export type CreatedApiKey = Schemas['CreatedApiKeyResponse']
export type Usage = Schemas['UsageResponse']

/** A non-2xx answer from the API, with FastAPI's `detail` when it sent one. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `Request failed (${status})`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Call the API with the signed-in user's login token. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase().auth.getSession()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  if (data.session) headers.set('Authorization', `Bearer ${data.session.access_token}`)

  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body?.detail ?? null)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}
