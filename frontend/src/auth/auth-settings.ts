import { useQuery } from '@tanstack/react-query'

import { uiConfig } from '@/config'

/** The parts of GoTrue's public /settings the UI uses. */
export type AuthSettings = {
  disable_signup: boolean
  mailer_autoconfirm: boolean
}

export function useAuthSettings() {
  return useQuery({
    queryKey: ['auth-settings'],
    staleTime: Infinity,
    queryFn: async (): Promise<AuthSettings> => {
      const { supabase_url, anon_key } = uiConfig()
      const response = await fetch(`${supabase_url}/auth/v1/settings`, {
        headers: { apikey: anon_key },
      })
      if (!response.ok) throw new Error(`Auth settings: ${response.status}`)
      return response.json()
    },
  })
}
