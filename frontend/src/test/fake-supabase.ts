import type { AuthChangeEvent, Session } from '@supabase/supabase-js'
import { vi } from 'vitest'

type Listener = (event: AuthChangeEvent, session: Session | null) => void

export function fakeSession(email = 'ada@example.com'): Session {
  return {
    access_token: 'login-token',
    refresh_token: 'refresh-token',
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: {
      id: '00000000-0000-0000-0000-000000000001',
      email,
      aud: 'authenticated',
      app_metadata: {},
      user_metadata: {},
      created_at: '2026-10-01T00:00:00Z',
    },
  }
}

/** Supabase's auth client as the UI uses it, with every call recorded. */
function createFakeAuth() {
  let session: Session | null = null
  const listeners = new Set<Listener>()
  const emit = (event: AuthChangeEvent) => listeners.forEach((l) => l(event, session))

  const auth = {
    /** Test helper: start signed in (or out) before rendering. */
    setSession(next: Session | null) {
      session = next
    },
    /** Test helper: fire an auth event, e.g. PASSWORD_RECOVERY. */
    emit,
    getSession: vi.fn(async () => ({ data: { session }, error: null })),
    onAuthStateChange: vi.fn((listener: Listener) => {
      listeners.add(listener)
      queueMicrotask(() => listener('INITIAL_SESSION', session))
      return { data: { subscription: { unsubscribe: () => listeners.delete(listener) } } }
    }),
    signInWithPassword: vi.fn(async ({ email }: { email: string; password: string }) => {
      session = fakeSession(email)
      emit('SIGNED_IN')
      return { data: { session, user: session.user }, error: null }
    }),
    signUp: vi.fn(async () => ({ data: { session: null, user: null }, error: null })),
    signOut: vi.fn(async () => {
      session = null
      emit('SIGNED_OUT')
      return { error: null }
    }),
    resend: vi.fn(async () => ({ data: {}, error: null })),
    resetPasswordForEmail: vi.fn(async () => ({ data: {}, error: null })),
    updateUser: vi.fn(async () => ({ data: { user: session?.user ?? null }, error: null })),
  }
  return auth
}

export let fakeAuth = createFakeAuth()

export function resetFakeSupabase() {
  fakeAuth = createFakeAuth()
}

/** Stands in for '@/lib/supabase' in every test (see setup.ts). */
export const supabaseModule = {
  supabase: () => ({ auth: fakeAuth }),
  initSupabase: () => ({ auth: fakeAuth }),
}

/** A Supabase AuthError as supabase-js returns it. */
export function authError(code: string, message = code, status = 400) {
  return Object.assign(new Error(message), { name: 'AuthApiError', code, status, __isAuthError: true })
}
