import type { Session } from '@supabase/supabase-js'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'

import { SUSPENDED_EVENT } from '@/api/client'
import { isRecoveryLink } from '@/auth/initial-url'
import { AuthContext } from '@/auth/use-auth'
import { supabase } from '@/lib/supabase'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [suspended, setSuspended] = useState(false)
  const [signedOutByUser, setSignedOutByUser] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  useEffect(() => {
    const {
      data: { subscription },
    } = supabase().auth.onAuthStateChange((event, next) => {
      setSession(next)
      setLoading(false)
      if (event === 'SIGNED_IN') setSignedOutByUser(false)
      if (event === 'SIGNED_OUT') {
        // Nothing from the last user may show for the next one.
        queryClient.clear()
        setSuspended(false)
      }
      // A recovery link that landed elsewhere (GoTrue's fallback to SITE_URL).
      // Other tabs get the event too, and must stay where they are.
      if (
        event === 'PASSWORD_RECOVERY' &&
        isRecoveryLink() &&
        !window.location.pathname.endsWith('/update-password')
      ) {
        navigate('/update-password', { replace: true })
      }
    })
    return () => subscription.unsubscribe()
  }, [queryClient, navigate])

  useEffect(() => {
    const onSuspended = () => setSuspended(true)
    window.addEventListener(SUSPENDED_EVENT, onSuspended)
    return () => window.removeEventListener(SUSPENDED_EVENT, onSuspended)
  }, [])

  const signOut = async () => {
    setSignedOutByUser(true)
    await supabase().auth.signOut({ scope: 'local' })
  }

  return (
    <AuthContext.Provider value={{ session, loading, suspended, signedOutByUser, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}
