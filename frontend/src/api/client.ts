import { supabase } from '@/lib/supabase'

import type { components } from './schema'

type Schemas = components['schemas']
export type Me = Schemas['MeResponse']
export type ApiKey = Schemas['ApiKeyResponse']
export type CreatedApiKey = Schemas['CreatedApiKeyResponse']
export type Usage = Schemas['UsageResponse']
export type AdminUser = Schemas['AdminUser']
export type AdminUserList = Schemas['AdminUserList']
export type RoleOption = Schemas['RoleOption']
export type UserStatus = Schemas['UserStatus']

/** Fired when the API refuses the account (403): banned, deleted or without a profile. */
export const SUSPENDED_EVENT = 'geonames:suspended'

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
  const { data, error } = await supabase().auth.getSession()
  // An expired token that could not be refreshed (Supabase Auth unreachable):
  // without a token the API would answer 401, and signing out would fail too.
  if (error && !data.session) throw new Error('Could not reach the login service. Check your connection and try again.')
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  if (data.session) headers.set('Authorization', `Bearer ${data.session.access_token}`)

  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    if (response.status === 401) {
      // The login token is no longer accepted: sign out, and the route guard
      // sends the user to the login page and back here afterwards.
      await supabase().auth.signOut({ scope: 'local' })
    } else if (response.status === 403) {
      window.dispatchEvent(new Event(SUSPENDED_EVENT))
    }
    throw new ApiError(response.status, body?.detail ?? null)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}
