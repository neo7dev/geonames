import type { Session } from '@supabase/supabase-js'
import { createContext, useContext } from 'react'

export type AuthState = {
  session: Session | null
  /** True until Supabase has read any stored session or email link. */
  loading: boolean
  /** The API refused this account (banned, deleted or without a profile). */
  suspended: boolean
  /** The user signed out themselves, so the login page needs no way back. */
  signedOutByUser: boolean
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (auth === null) throw new Error('useAuth needs an AuthProvider')
  return auth
}
